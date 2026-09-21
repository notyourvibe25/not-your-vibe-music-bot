from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import math
import os
from pathlib import Path
import random
import subprocess
import shutil
import tempfile
import time
import wave
from contextlib import contextmanager
from typing import Optional

import numpy as np
import psycopg
from mutagen import File as MutagenFile
from psycopg.rows import dict_row
from telethon import TelegramClient
from telethon.errors import (
    RPCError,
    FloodWaitError,
    MessageIdInvalidError,
    PeerIdInvalidError,
    ChannelPrivateError,
    FileReferenceExpiredError,
)
from telethon.sessions import StringSession


# ============================================================
# NOT YOUR VIBE - AUDIO ANALYZER
# No AI. Audio analysis only.
#
# Features:
# - Shows TOTAL / ANALYZED / REMAINING while scanning
# - Continues until all analyzable tracks are finished
# - Temporary Telegram/DB/network errors are retried
# - Permanently missing/deleted Telegram messages are skipped
# - After finishing, keeps WATCHING PostgreSQL for new tracks
# - New tracks are automatically analyzed without restarting
# ============================================================

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

TELETHON_API_ID = (
    os.getenv("TELETHON_API_ID")
    or os.getenv("API_ID")
    or ""
).strip()

TELETHON_API_HASH = (
    os.getenv("TELETHON_API_HASH")
    or os.getenv("API_HASH")
    or ""
).strip()

TELETHON_SESSION = os.getenv("TELETHON_SESSION", "").strip()

ANALYZER_LIMIT = int(os.getenv("ANALYZER_LIMIT", "10"))
WATCH_INTERVAL = int(os.getenv("ANALYZER_WATCH_INTERVAL", "60"))

SAMPLE_RATE = 22050
CHANNELS = 1
MAX_SECONDS = 90

TEMP_DIR = os.getenv("ANALYZER_TEMP_DIR", tempfile.gettempdir())
MAX_FILE_MB = int(os.getenv("ANALYZER_MAX_FILE_MB", "250"))

TELEGRAM_RETRIES_PER_TRACK = int(
    os.getenv("TELEGRAM_RETRIES_PER_TRACK", "4")
)
TELEGRAM_RETRY_DELAY = float(
    os.getenv("TELEGRAM_RETRY_DELAY", "3")
)
TELEGRAM_MAX_RETRY_DELAY = float(
    os.getenv("TELEGRAM_MAX_RETRY_DELAY", "30")
)
TELEGRAM_TIMEOUT = int(os.getenv("TELEGRAM_TIMEOUT", "60"))

DB_RETRY_DELAY = float(os.getenv("DB_RETRY_DELAY", "2"))
DB_MAX_RETRY_DELAY = float(os.getenv("DB_MAX_RETRY_DELAY", "20"))
DB_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "15"))

TRACK_DELAY = float(os.getenv("ANALYZER_TRACK_DELAY", "1.5"))
FFMPEG_TIMEOUT = int(os.getenv("ANALYZER_FFMPEG_TIMEOUT", "180"))
RETRY_BASE_DELAY = float(os.getenv("ANALYZER_RETRY_BASE_DELAY", "300"))
RETRY_MAX_DELAY = float(os.getenv("ANALYZER_RETRY_MAX_DELAY", "21600"))
MAX_TRACK_RETRIES = int(os.getenv("ANALYZER_MAX_TRACK_RETRIES", "8"))
TEMP_FILE_MAX_AGE = int(os.getenv("ANALYZER_TEMP_MAX_AGE", str(24 * 60 * 60)))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("not-your-vibe-audio-analyzer")


# ============================================================
# ERRORS
# ============================================================

class PermanentTelegramMessageError(Exception):
    pass


class TemporaryTelegramError(Exception):
    pass


# ============================================================
# HELPERS
# ============================================================

def cleanup_old_temp_files():
    """Delete only analyzer-owned temp folders/files older than 24 hours."""
    if not os.path.isdir(TEMP_DIR):
        return
    cutoff = time.time() - TEMP_FILE_MAX_AGE
    removed = 0
    audio_extensions = {
        ".aac", ".flac", ".m4a", ".mkv", ".mp3", ".mp4",
        ".ogg", ".opus", ".wav", ".webm", ".media", ".part",
    }
    for name in os.listdir(TEMP_DIR):
        if not (
            name.startswith("nyv_analyzer_")
            or name.startswith("nyv_analyzer")
            or Path(name).suffix.lower() in audio_extensions
        ):
            continue
        path = os.path.join(TEMP_DIR, name)
        try:
            if os.path.getmtime(path) >= cutoff:
                continue
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
            removed += 1
        except FileNotFoundError:
            pass
        except Exception:
            log.warning("Could not remove old analyzer temp path: %s", path, exc_info=True)

    if removed:
        log.info("Removed %s analyzer temp path(s) older than %ss", removed, TEMP_FILE_MAX_AGE)



def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def backoff(attempt: int, base: float, maximum: float) -> float:
    value = min(maximum, base * (2 ** max(0, attempt - 1)))
    return value + random.uniform(0, min(1.0, value * 0.15))


def is_retryable_db_error(exc: Exception) -> bool:
    if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
        return True

    text = str(exc).lower()
    words = (
        "timeout", "timed out", "connection", "network", "dns",
        "resolve", "server closed", "connection reset", "broken pipe",
        "could not connect", "temporarily unavailable",
        "no address associated with hostname",
        "network is unreachable", "name or service not known",
    )
    return any(x in text for x in words)


def is_retryable_telegram_error(exc: Exception) -> bool:
    if isinstance(
        exc,
        (
            asyncio.TimeoutError,
            TimeoutError,
            ConnectionError,
            FileReferenceExpiredError,
        ),
    ):
        return True

    text = str(exc).lower()
    words = (
        "timeout", "timed out", "connection", "network", "internal",
        "getfilerequest", "file reference", "server error",
        "temporarily unavailable", "network is unreachable",
        "connection reset", "connection refused", "cannot connect",
        "request was unsuccessful",
    )
    return any(x in text for x in words)


# ============================================================
# DATABASE
# ============================================================

def _db_connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing")

    return psycopg.connect(
        normalize_database_url(DATABASE_URL),
        connect_timeout=DB_CONNECT_TIMEOUT,
        row_factory=dict_row,
        application_name="not-your-vibe-audio-analyzer",
    )


@contextmanager
def db():
    attempt = 0

    while True:
        conn = None
        attempt += 1

        try:
            conn = _db_connect()
            log.info("PostgreSQL: CONNECTED")
            yield conn
            conn.commit()
            return

        except Exception as exc:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.rollback()

            if not is_retryable_db_error(exc):
                raise

            delay = backoff(attempt, DB_RETRY_DELAY, DB_MAX_RETRY_DELAY)
            log.warning(
                "PostgreSQL unavailable. Retrying in %.1fs: %s",
                delay,
                exc,
            )
            time.sleep(delay)

        finally:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()


def ensure_analyzer_schema():
    """Add analyzer and independent genre-scan metadata columns."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE tracks ADD COLUMN IF NOT EXISTS analyzer_retry_count INTEGER NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE tracks ADD COLUMN IF NOT EXISTS analyzer_retry_after TIMESTAMPTZ")
            cur.execute("ALTER TABLE tracks ADD COLUMN IF NOT EXISTS genre_scanned_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE tracks ADD COLUMN IF NOT EXISTS genre_source TEXT")
            cur.execute("ALTER TABLE tracks ADD COLUMN IF NOT EXISTS genre_tags TEXT")
            cur.execute("ALTER TABLE tracks ADD COLUMN IF NOT EXISTS artist_name TEXT")
            cur.execute("ALTER TABLE tracks ADD COLUMN IF NOT EXISTS genre_scan_error TEXT")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tracks_analyzer_retry_after ON tracks(analyzer_retry_after)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tracks_genre_scanned_at ON tracks(genre_scanned_at)")



def get_progress() -> dict:
    query = """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (
                WHERE COALESCE(analyzed,FALSE)=TRUE
            ) AS analyzed,
            COUNT(*) FILTER (
                WHERE COALESCE(analyzed,FALSE)=FALSE
                AND (
                    ai_error IS NULL
                    OR ai_error NOT LIKE 'PERMANENT:%'
                )
            ) AS remaining,
            COUNT(*) FILTER (
                WHERE COALESCE(analyzed,FALSE)=FALSE
                AND ai_error LIKE 'PERMANENT:%'
            ) AS permanent_failed
        FROM tracks
    """

    while True:
        try:
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute(query)
                    row = cur.fetchone()
                    return dict(row)
        except Exception as exc:
            if not is_retryable_db_error(exc):
                raise
            time.sleep(DB_RETRY_DELAY)


def print_progress(prefix="SCAN PROGRESS"):
    p = get_progress()
    total = int(p["total"] or 0)
    analyzed = int(p["analyzed"] or 0)
    remaining = int(p["remaining"] or 0)
    permanent = int(p["permanent_failed"] or 0)

    percent = (analyzed / total * 100) if total else 100.0

    print()
    print("=" * 60)
    print(prefix)
    print(
        f"TOTAL TRACKS : {total}\n"
        f"ANALYZED     : {analyzed}\n"
        f"REMAINING    : {remaining}\n"
        f"PERMANENT    : {permanent}\n"
        f"PROGRESS     : {percent:.2f}%"
    )
    print("=" * 60)


def get_pending_tracks(limit: int):
    query = """
        SELECT
            id,
            title,
            channel_id,
            message_id,
            mood,
            analyzed,
            ai_error
        FROM tracks
        WHERE
            COALESCE(analyzed,FALSE)=FALSE
            AND (
                ai_error IS NULL
                OR ai_error NOT LIKE 'PERMANENT:%'
            )
            AND (analyzer_retry_after IS NULL OR analyzer_retry_after <= NOW())
        ORDER BY id ASC
        LIMIT %s
    """

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (limit,))
            return cur.fetchall()


def get_pending_genre_tracks(limit: int):
    """Return tracks needing only embedded metadata scanning.

    This queue intentionally does not require analyzed=FALSE: already analyzed
    tracks must still be eligible for a later genre-only pass.
    """
    query = """
        SELECT id, title, channel_id, message_id, mood
        FROM tracks
        WHERE genre_scanned_at IS NULL
          AND (genre_scan_error IS NULL OR genre_scan_error NOT LIKE 'PERMANENT:%')
        ORDER BY id ASC
        LIMIT %s
    """
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (limit,))
            return cur.fetchall()


def mark_error(track_id: int, error: Exception | str, permanent=False):
    message = str(error).strip()

    with db() as conn:
        with conn.cursor() as cur:
            if permanent:
                cur.execute(
                    "UPDATE tracks SET analyzed=FALSE, ai_error=%s, analyzer_retry_after=NULL WHERE id=%s",
                    ("PERMANENT: " + message[:1980], track_id),
                )
                return

            cur.execute(
                """
                UPDATE tracks
                SET analyzed=FALSE,
                    ai_error=%s,
                    analyzer_retry_count=COALESCE(analyzer_retry_count, 0) + 1,
                    analyzer_retry_after=CASE
                        WHEN COALESCE(analyzer_retry_count, 0) + 1 >= %s THEN NULL
                        ELSE NOW() + (LEAST(%s, %s * POWER(2, LEAST(COALESCE(analyzer_retry_count, 0), 6))) * INTERVAL '1 second')
                    END
                WHERE id=%s
                RETURNING analyzer_retry_count
                """,
                (message[:2000], MAX_TRACK_RETRIES, RETRY_MAX_DELAY, RETRY_BASE_DELAY, track_id),
            )
            row = cur.fetchone()
            retry_count = int(row["analyzer_retry_count"]) if row else 0
            if retry_count >= MAX_TRACK_RETRIES:
                cur.execute(
                    "UPDATE tracks SET ai_error=%s, analyzer_retry_after=NULL WHERE id=%s",
                    (f"PERMANENT: retry limit reached ({MAX_TRACK_RETRIES}): {message[:1800]}", track_id),
                )
                log.error("Track %s disabled after %s analyzer retries", track_id, retry_count)
            else:
                delay = min(RETRY_MAX_DELAY, RETRY_BASE_DELAY * (2 ** min(retry_count - 1, 6)))
                log.warning("Track %s retry %s/%s scheduled in %.0fs", track_id, retry_count, MAX_TRACK_RETRIES, delay)


def save_analysis(track_id: int, result: dict):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE tracks
                SET
                    bpm=%s,
                    musical_key=%s,
                    energy=%s,
                    danceability=%s,
                    loudness=%s,
                    analyzer_mood=%s,
                    analyzed=TRUE,
                    analyzed_at=NOW(),
                    ai_error=NULL
                WHERE id=%s
                """,
                (
                    result.get("bpm"),
                    result.get("key"),
                    result.get("energy"),
                    result.get("danceability"),
                    result.get("loudness"),
                    result.get("mood"),
                    track_id,
                ),
            )


def save_genre_metadata(track_id: int, metadata: dict, error=None):
    """Persist only embedded metadata; never changes audio analysis status."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE tracks
                SET genre=%s,
                    subgenre=%s,
                    genre_tags=%s,
                    artist_name=%s,
                    genre_source=%s,
                    genre_scan_error=%s,
                    genre_scanned_at=NOW()
                WHERE id=%s
                """,
                (
                    metadata.get("genre"),
                    metadata.get("subgenre"),
                    metadata.get("genre_tags"),
                    metadata.get("artist_name"),
                    "embedded" if metadata.get("found") else None,
                    str(error)[:2000] if error else None,
                    track_id,
                ),
            )


# ============================================================
# TELEGRAM
# ============================================================

def build_telegram_client():
    if not TELETHON_API_ID:
        raise RuntimeError("TELETHON_API_ID/API_ID is missing")
    if not TELETHON_API_HASH:
        raise RuntimeError("TELETHON_API_HASH/API_HASH is missing")
    if not TELETHON_SESSION:
        raise RuntimeError("TELETHON_SESSION is missing")

    try:
        api_id = int(TELETHON_API_ID)
    except ValueError:
        raise RuntimeError("TELETHON_API_ID/API_ID must be an integer")

    return TelegramClient(
        StringSession(TELETHON_SESSION),
        api_id,
        TELETHON_API_HASH,
        connection_retries=10,
        retry_delay=5,
        timeout=TELEGRAM_TIMEOUT,
        auto_reconnect=True,
    )


async def ensure_telegram_connected(client):
    if client.is_connected():
        return

    attempt = 0
    while True:
        attempt += 1
        try:
            log.info("Telegram reconnect attempt #%s...", attempt)
            await client.connect()

            if not await client.is_user_authorized():
                raise RuntimeError("Telethon session is not authorized")

            log.info("Telegram: CONNECTED")
            return

        except Exception as exc:
            log.warning("Telegram connection unavailable: %s", exc)
            with contextlib.suppress(Exception):
                await client.disconnect()

            await asyncio.sleep(
                backoff(attempt, 5, 60)
            )


async def get_telegram_message(client, channel_id, message_id):
    try:
        entity = await client.get_entity(
            int(channel_id)
            if str(channel_id).lstrip("-").isdigit()
            else channel_id
        )
    except (PeerIdInvalidError, ChannelPrivateError) as exc:
        raise PermanentTelegramMessageError(
            f"Telegram channel unavailable: {channel_id}"
        ) from exc
    except Exception as exc:
        if is_retryable_telegram_error(exc):
            raise TemporaryTelegramError(str(exc)) from exc
        raise

    try:
        message = await client.get_messages(entity, ids=int(message_id))
    except MessageIdInvalidError as exc:
        raise PermanentTelegramMessageError(
            f"Telegram message not found: {channel_id}/{message_id}"
        ) from exc
    except Exception as exc:
        if is_retryable_telegram_error(exc):
            raise TemporaryTelegramError(str(exc)) from exc
        raise

    if message is None:
        raise PermanentTelegramMessageError(
            f"Telegram message not found: {channel_id}/{message_id}"
        )

    return message


async def download_track(client, channel_id, message_id, output_path):
    last_error = None

    for attempt in range(1, TELEGRAM_RETRIES_PER_TRACK + 1):
        try:
            await ensure_telegram_connected(client)

            message = await get_telegram_message(
                client, channel_id, message_id
            )

            if not getattr(message, "media", None):
                raise PermanentTelegramMessageError(
                    "Telegram message has no media"
                )

            log.info(
                "Telegram download attempt #%s: %s/%s",
                attempt, channel_id, message_id
            )

            # Remove a partial file before every retry.
            with contextlib.suppress(FileNotFoundError):
                os.remove(output_path)
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

            downloaded = await asyncio.wait_for(
                client.download_media(message, file=output_path),
                timeout=TELEGRAM_TIMEOUT,
            )

            if not downloaded or not os.path.exists(downloaded):
                raise TemporaryTelegramError(
                    "Telegram returned no downloaded file"
                )

            size = os.path.getsize(downloaded)
            if size <= 0:
                raise TemporaryTelegramError("Downloaded file is empty")

            if size > MAX_FILE_MB * 1024 * 1024:
                raise PermanentTelegramMessageError(
                    f"Downloaded file exceeds {MAX_FILE_MB} MB limit"
                )

            return downloaded

        except PermanentTelegramMessageError:
            raise

        except FloodWaitError as exc:
            wait_seconds = int(getattr(exc, "seconds", 5))
            log.warning("Telegram FloodWait: sleeping %ss", wait_seconds)
            await asyncio.sleep(wait_seconds + 1)
            last_error = exc

        except (
            TemporaryTelegramError,
            asyncio.TimeoutError,
            TimeoutError,
            ConnectionError,
            FileReferenceExpiredError,
        ) as exc:
            last_error = exc
            log.warning(
                "Temporary Telegram error attempt %s/%s: %s",
                attempt, TELEGRAM_RETRIES_PER_TRACK, exc
            )
            with contextlib.suppress(Exception):
                await client.disconnect()
            await asyncio.sleep(
                backoff(
                    attempt,
                    TELEGRAM_RETRY_DELAY,
                    TELEGRAM_MAX_RETRY_DELAY,
                )
            )

        except RPCError as exc:
            if not is_retryable_telegram_error(exc):
                raise
            last_error = exc
            log.warning(
                "Telegram RPC error attempt %s/%s: %s",
                attempt, TELEGRAM_RETRIES_PER_TRACK, exc
            )
            with contextlib.suppress(Exception):
                await client.disconnect()
            await asyncio.sleep(
                backoff(
                    attempt,
                    TELEGRAM_RETRY_DELAY,
                    TELEGRAM_MAX_RETRY_DELAY,
                )
            )

        except Exception as exc:
            if not is_retryable_telegram_error(exc):
                raise
            last_error = exc
            log.warning(
                "Telegram error attempt %s/%s: %s",
                attempt, TELEGRAM_RETRIES_PER_TRACK, exc
            )
            await asyncio.sleep(
                backoff(
                    attempt,
                    TELEGRAM_RETRY_DELAY,
                    TELEGRAM_MAX_RETRY_DELAY,
                )
            )

    raise TemporaryTelegramError(
        f"Telegram download failed after "
        f"{TELEGRAM_RETRIES_PER_TRACK} attempts: {last_error}"
    )


# ============================================================
# FFMPEG
# ============================================================

async def convert_to_wav(input_path, output_path):
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", input_path,
        "-ac", str(CHANNELS),
        "-ar", str(SAMPLE_RATE),
        "-t", str(MAX_SECONDS),
        "-f", "wav",
        output_path,
    ]

    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=FFMPEG_TIMEOUT,
        )
    except asyncio.TimeoutError as exc:
        if process:
            with contextlib.suppress(Exception):
                process.kill()
        raise RuntimeError("FFmpeg conversion timed out") from exc

    if process.returncode != 0:
        error = stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError("FFmpeg failed: " + error[:1000])

    if not os.path.exists(output_path):
        raise RuntimeError("FFmpeg did not create WAV file")

    return output_path


def read_wav(path):
    with wave.open(path, "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    if sample_width == 1:
        audio = (
            np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128
        ) / 128.0
    elif sample_width == 2:
        audio = (
            np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            / 32768.0
        )
    elif sample_width == 4:
        audio = (
            np.frombuffer(raw, dtype=np.int32).astype(np.float32)
            / 2147483648.0
        )
    else:
        raise RuntimeError(
            f"Unsupported WAV sample width: {sample_width}"
        )

    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)

    return audio, sample_rate


# ============================================================
# AUDIO ANALYSIS - NO AI
# ============================================================

def rms_energy(audio):
    if len(audio) == 0:
        return 0.0

    rms = float(np.sqrt(np.mean(np.square(audio))))
    value = 20.0 * math.log10(max(rms, 1e-8))
    normalized = ((value + 40.0) / 40.0) * 100.0

    return float(np.clip(normalized, 0, 100))


def loudness_db(audio):
    if len(audio) == 0:
        return -100.0

    rms = float(np.sqrt(np.mean(np.square(audio))))
    return float(20.0 * math.log10(max(rms, 1e-8)))


def spectral_features(audio, sr):
    if len(audio) < 2:
        return {
            "centroid": 0.0,
            "bandwidth": 0.0,
            "rolloff": 0.0,
            "flatness": 0.0,
        }

    n = min(len(audio), sr * 30)
    signal = audio[:n]
    window = np.hanning(len(signal))

    spectrum = np.abs(np.fft.rfft(signal * window))
    freqs = np.fft.rfftfreq(len(signal), 1.0 / sr)

    total = float(np.sum(spectrum))
    if total <= 1e-12:
        return {
            "centroid": 0.0,
            "bandwidth": 0.0,
            "rolloff": 0.0,
            "flatness": 0.0,
        }

    centroid = float(np.sum(freqs * spectrum) / total)

    bandwidth = float(
        np.sqrt(
            np.sum(((freqs - centroid) ** 2) * spectrum) / total
        )
    )

    cumulative = np.cumsum(spectrum)
    target = cumulative[-1] * 0.85
    idx = int(np.searchsorted(cumulative, target))
    rolloff = float(freqs[min(idx, len(freqs) - 1)])

    geometric = np.exp(np.mean(np.log(spectrum + 1e-12)))
    arithmetic = np.mean(spectrum + 1e-12)
    flatness = float(geometric / max(arithmetic, 1e-12))

    return {
        "centroid": centroid,
        "bandwidth": bandwidth,
        "rolloff": rolloff,
        "flatness": flatness,
    }


def estimate_bpm(audio, sr):
    if len(audio) < sr * 2:
        return None

    audio = audio.astype(np.float32)
    audio = audio[:min(len(audio), sr * 60)]

    diff = np.abs(np.diff(audio))
    if len(diff) < sr:
        return None

    hop = max(1, int(sr * 0.01))

    envelope = np.array(
        [
            np.mean(diff[i:i + hop])
            for i in range(0, len(diff), hop)
        ],
        dtype=np.float32,
    )

    if len(envelope) < 20:
        return None

    envelope -= np.mean(envelope)
    std = np.std(envelope)
    if std <= 1e-9:
        return None

    envelope /= std
    envelope_rate = sr / hop

    min_bpm, max_bpm = 60.0, 200.0
    min_lag = int(envelope_rate * 60.0 / max_bpm)
    max_lag = int(envelope_rate * 60.0 / min_bpm)
    max_lag = min(max_lag, len(envelope) - 1)

    if min_lag >= max_lag:
        return None

    best_corr = None
    best_lag = None

    for lag in range(min_lag, max_lag + 1):
        a = envelope[:-lag]
        b = envelope[lag:]
        if len(a) == 0:
            continue

        corr = float(np.mean(a * b))
        if best_corr is None or corr > best_corr:
            best_corr = corr
            best_lag = lag

    if best_lag is None:
        return None

    bpm = 60.0 * envelope_rate / best_lag

    while bpm < 80:
        bpm *= 2
    while bpm > 180:
        bpm /= 2

    return round(float(bpm), 2)


def estimate_key(audio, sr):
    if len(audio) < sr * 2:
        return None

    signal = audio[:min(len(audio), sr * 60)]
    n_fft = 8192

    if len(signal) < n_fft:
        return None

    window = np.hanning(n_fft)

    positions = np.linspace(
        0,
        len(signal) - n_fft,
        num=min(30, max(1, len(signal) // (sr * 2))),
        dtype=int,
    )

    chroma = np.zeros(12, dtype=np.float64)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)

    valid = (freqs >= 50) & (freqs <= 5000)
    freqs = freqs[valid]
    if len(freqs) == 0:
        return None

    notes = 12.0 * np.log2(freqs / 440.0) + 69.0
    midi = np.round(notes).astype(int)

    for pos in positions:
        frame = signal[pos:pos + n_fft]
        if len(frame) < n_fft:
            continue

        spectrum = np.abs(np.fft.rfft(frame * window))[valid]

        for magnitude, note in zip(spectrum, midi):
            if 0 <= magnitude:
                chroma[note % 12] += magnitude

    total = np.sum(chroma)
    if total <= 1e-9:
        return None

    chroma /= total

    names = [
        "C", "C#", "D", "D#", "E", "F",
        "F#", "G", "G#", "A", "A#", "B",
    ]

    major = np.array([
        6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
        2.52, 5.19, 2.39, 3.66, 2.29, 2.88,
    ])

    minor = np.array([
        6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
        2.54, 4.75, 3.98, 2.69, 3.34, 3.17,
    ])

    major /= np.linalg.norm(major)
    minor /= np.linalg.norm(minor)

    best = None

    for root in range(12):
        rotated = np.roll(chroma, -root)
        rotated /= max(np.linalg.norm(rotated), 1e-12)

        major_score = float(np.dot(rotated, major))
        minor_score = float(np.dot(rotated, minor))

        if best is None or major_score > best[0]:
            best = (major_score, names[root], "Major")

        if minor_score > best[0]:
            best = (minor_score, names[root], "Minor")

    if best is None or best[0] < 0.45:
        return None

    return f"{best[1]} {best[2]}"


def estimate_danceability(audio, sr):
    if len(audio) == 0:
        return 0.0

    features = spectral_features(audio, sr)
    centroid = features["centroid"]
    rolloff = features["rolloff"]
    flatness = features["flatness"]

    bpm = estimate_bpm(audio, sr)
    bpm_score = 50.0

    if bpm is not None:
        bpm_score = 100.0 - abs(bpm - 125.0) * 1.3

    centroid_score = np.clip(
        (centroid - 500) / 3500 * 100,
        0, 100,
    )

    rolloff_score = np.clip(
        (rolloff - 1000) / 5000 * 100,
        0, 100,
    )

    flatness_score = 100.0 - flatness * 100.0

    result = (
        bpm_score * 0.45
        + centroid_score * 0.20
        + rolloff_score * 0.20
        + flatness_score * 0.15
    )

    return round(float(np.clip(result, 0, 100)), 2)


def estimate_mood(audio, sr):
    energy = rms_energy(audio)
    features = spectral_features(audio, sr)
    centroid = features["centroid"]

    bpm = estimate_bpm(audio, sr)
    if bpm is None:
        bpm = 120.0

    if energy >= 85 and bpm >= 128:
        return "energetic"
    if energy >= 80 and bpm >= 120:
        return "hype"
    if energy < 35 and centroid < 1200:
        return "sad"
    if energy < 45 and bpm < 105:
        return "night"
    if centroid < 1400 and energy < 65:
        return "dark"
    if centroid < 1800 and energy < 70:
        return "chill"
    if centroid >= 2200 and energy >= 65:
        return "melodic"

    return "dark"


def _metadata_values(tags, keys):
    values = []
    for key in keys:
        try:
            value = tags.get(key)
        except Exception:
            value = None
        if value is None:
            continue
        raw = getattr(value, "text", value)
        if isinstance(raw, (list, tuple)):
            values.extend(str(item).strip() for item in raw if str(item).strip())
        elif str(raw).strip():
            values.append(str(raw).strip())
    return values


def extract_embedded_metadata(audio_path):
    """Read only metadata physically embedded in the audio file.

    Missing values remain None; this function never infers a genre from audio
    features or filenames.
    """
    result = {
        "artist_name": None,
        "genre": None,
        "subgenre": None,
        "genre_tags": None,
        "found": False,
    }
    media = MutagenFile(audio_path, easy=False)
    if media is None or not getattr(media, "tags", None):
        return result
    tags = media.tags
    artists = _metadata_values(tags, ("TPE1", "TPE2", "©ART", "artist", "ARTIST"))
    genres = _metadata_values(tags, ("TCON", "©gen", "genre", "GENRE"))
    subgenres = _metadata_values(tags, (
        "TXXX:SUBGENRE", "SUBGENRE", "subgenre", "TXXX:STYLE", "STYLE", "style",
    ))
    tags_all = []
    for value in genres + subgenres:
        for item in str(value).replace(";", ",").split(","):
            item = item.strip()
            if item and item.casefold() not in {x.casefold() for x in tags_all}:
                tags_all.append(item)
    result.update({
        "artist_name": artists[0] if artists else None,
        "genre": genres[0] if genres else None,
        "subgenre": subgenres[0] if subgenres else None,
        "genre_tags": json.dumps(tags_all, ensure_ascii=False) if tags_all else None,
        "found": bool(artists or genres or subgenres),
    })
    return result


def analyze_file(wav_path):
    audio, sr = read_wav(wav_path)

    if len(audio) == 0:
        raise RuntimeError("Audio contains no samples")

    return {
        "bpm": estimate_bpm(audio, sr),
        "key": estimate_key(audio, sr),
        "energy": round(rms_energy(audio), 2),
        "danceability": estimate_danceability(audio, sr),
        "loudness": round(loudness_db(audio), 2),
        "mood": estimate_mood(audio, sr),
    }


# ============================================================
# TRACK PROCESSING
# ============================================================

def safe_filename(title, track_id):
    title = str(title or f"track_{track_id}").strip()
    if not title:
        title = f"track_{track_id}"

    return (
        title
        .replace("/", "_")
        .replace("\\", "_")
        .replace("\x00", "")
    )[:180]


async def process_track(client, track):
    track_id = int(track["id"])
    channel_id = str(track["channel_id"])
    message_id = int(track["message_id"])
    title = track.get("title") or "Unknown"

    # Isolate every track so retries cannot collide or leave numbered copies.
    work_dir = tempfile.mkdtemp(prefix=f"nyv_analyzer_{track_id}_", dir=TEMP_DIR)
    temp_audio = os.path.join(
        work_dir,
        f"{safe_filename(title, track_id)}_{track_id}_{message_id}.part",
    )
    temp_wav = os.path.join(work_dir, f"nyv_analyzer_{track_id}.wav")

    print()
    print("-" * 60)
    print(f"TRACK ID : {track_id}")
    print(f"TITLE    : {title}")
    print(f"TELEGRAM : {channel_id}/{message_id}")
    print("-" * 60)

    try:
        print("1/4 Downloading Telegram audio...")

        downloaded = await download_track(
            client,
            channel_id,
            message_id,
            temp_audio,
        )

        print("2/4 Running FFmpeg...")
        await convert_to_wav(downloaded, temp_wav)

        print("3/4 Reading embedded metadata...")
        try:
            save_genre_metadata(track_id, extract_embedded_metadata(downloaded))
        except Exception as metadata_error:
            # Metadata is optional. Audio analysis and playback must continue.
            log.warning("Genre metadata scan failed for track=%s: %s", track_id, metadata_error)

        print("3/4 Analyzing audio...")
        result = analyze_file(temp_wav)

        print("4/4 Saving PostgreSQL...")
        save_analysis(track_id, result)

        print("✅ ANALYZED")
        print(f"   BPM:          {result.get('bpm')}")
        print(f"   Key:          {result.get('key')}")
        print(f"   Energy:       {result.get('energy')}")
        print(f"   Danceability: {result.get('danceability')}")
        print(f"   Loudness:     {result.get('loudness')}")
        print(f"   Mood:         {result.get('mood')}")

        return True

    except PermanentTelegramMessageError as exc:
        print(f"❌ PERMANENT: {exc}")
        mark_error(track_id, exc, permanent=True)
        return False

    except Exception as exc:
        print(f"❌ TEMPORARY FAILED: {exc}")
        mark_error(track_id, exc, permanent=False)
        return False

    finally:
        # Remove partial downloads and converted files even after timeout/error.
        with contextlib.suppress(Exception):
            shutil.rmtree(work_dir, ignore_errors=True)


async def process_genre_track(client, track):
    """Scan embedded metadata for an analyzed or unanalyzed track only."""
    track_id = int(track["id"])
    work_dir = tempfile.mkdtemp(prefix=f"nyv_genre_{track_id}_", dir=TEMP_DIR)
    temp_audio = os.path.join(work_dir, f"genre_{track_id}.part")
    try:
        downloaded = await download_track(
            client, str(track["channel_id"]), int(track["message_id"]), temp_audio
        )
        try:
            metadata = extract_embedded_metadata(downloaded)
            save_genre_metadata(track_id, metadata)
            log.info("Genre scan complete track=%s genre=%s subgenre=%s", track_id, metadata.get("genre"), metadata.get("subgenre"))
            return True
        except Exception as exc:
            # Mark the scan complete even when the container has no readable
            # tags; NULL means unknown, not unavailable, and avoids hot-looping.
            save_genre_metadata(track_id, {}, error=exc)
            log.warning("Genre scan failed track=%s: %s", track_id, exc)
            return False
    except PermanentTelegramMessageError as exc:
        save_genre_metadata(track_id, {}, error=f"PERMANENT: {exc}")
        return False
    except Exception as exc:
        log.warning("Genre track download failed track=%s: %s", track_id, exc)
        return False
    finally:
        with contextlib.suppress(Exception):
            shutil.rmtree(work_dir, ignore_errors=True)


# ============================================================
# MAIN LOOP
# ============================================================

async def run_analyzer(limit):
    print()
    print("NOT YOUR VIBE AUDIO ANALYZER")
    print("AI: OFF")
    print("AUTO WATCH: ON")
    print("=" * 60)

    ensure_analyzer_schema()
    cleanup_old_temp_files()
    client = build_telegram_client()

    success_total = 0
    failed_total = 0

    try:
        await ensure_telegram_connected(client)

        while True:
            print_progress("CURRENT SCAN")

            tracks = get_pending_tracks(limit)
            genre_tracks = get_pending_genre_tracks(limit)
            audio_track_ids = {int(track["id"]) for track in tracks}

            if not tracks and not genre_tracks:
                print()
                print("✅ CURRENT QUEUE FINISHED")
                print("No analyzable pending tracks right now.")
                print(
                    f"New tracks will be checked every "
                    f"{WATCH_INTERVAL} seconds."
                )

                # Keep running forever.
                # If new tracks are inserted into tracks,
                # they will automatically be picked up.
                while True:
                    await asyncio.sleep(WATCH_INTERVAL)

                    print()
                    print("🔎 CHECKING FOR NEW TRACKS...")
                    cleanup_old_temp_files()
                    print_progress("AUTO WATCH")

                    new_tracks = get_pending_tracks(limit)
                    new_genre_tracks = get_pending_genre_tracks(limit)

                    if new_tracks or new_genre_tracks:
                        print(
                            f"🆕 FOUND {len(new_tracks)} AUDIO + "
                            f"{len(new_genre_tracks)} GENRE PENDING TRACK(S)"
                        )
                        tracks = new_tracks
                        genre_tracks = new_genre_tracks
                        audio_track_ids = {int(track["id"]) for track in tracks}
                        break

                    print("No new tracks yet.")

            for index, track in enumerate(tracks, start=1):
                await ensure_telegram_connected(client)

                p = get_progress()

                print()
                print(
                    f"QUEUE {index}/{len(tracks)} | "
                    f"TOTAL={p['total']} | "
                    f"ANALYZED={p['analyzed']} | "
                    f"REMAINING={p['remaining']}"
                )

                ok = await process_track(client, track)

                if ok:
                    success_total += 1
                else:
                    failed_total += 1

                print_progress("AFTER TRACK")

                if index < len(tracks):
                    await asyncio.sleep(TRACK_DELAY)

            for index, track in enumerate(genre_tracks, start=1):
                if int(track["id"]) in audio_track_ids:
                    continue
                await ensure_telegram_connected(client)
                print()
                print(f"GENRE QUEUE {index}/{len(genre_tracks)} | TRACK={track['id']}")
                if await process_genre_track(client, track):
                    print("✅ GENRE SCANNED")
                else:
                    print("⚠️ GENRE SCAN FAILED; AUDIO PLAYBACK UNAFFECTED")

            print()
            print("=" * 60)
            print("BATCH FINISHED")
            print(
                f"RUN SUCCESS={success_total} | "
                f"RUN FAILED={failed_total}"
            )
            print_progress("BATCH RESULT")
            print("=" * 60)

            # IMPORTANT:
            # Immediately ask DB for pending tracks again.
            # Temporary failures remain pending.
            # Permanent missing/deleted Telegram messages are excluded.
            await asyncio.sleep(1)

    finally:
        with contextlib.suppress(Exception):
            if client.is_connected():
                await client.disconnect()

        print("Telethon: DISCONNECTED")


def parse_args():
    parser = argparse.ArgumentParser(
        description="NOT YOUR VIBE Audio Analyzer - no AI"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=ANALYZER_LIMIT,
        help="Tracks per batch",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.limit <= 0:
        raise SystemExit("--limit must be greater than 0")

    try:
        asyncio.run(run_analyzer(args.limit))
    except KeyboardInterrupt:
        print()
        print("Analyzer stopped by user.")
        return 130
    except Exception as exc:
        print()
        print("❌ FATAL ANALYZER ERROR:")
        print(exc)
        log.exception("Fatal analyzer error")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
