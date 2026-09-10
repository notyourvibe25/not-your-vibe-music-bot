from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import math
import os
import random
import subprocess
import tempfile
import time
import wave
from contextlib import contextmanager
from typing import Optional

import numpy as np
import psycopg
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
# CONFIG
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

TELETHON_SESSION = os.getenv(
    "TELETHON_SESSION",
    "",
).strip()

ANALYZER_LIMIT = int(
    os.getenv("ANALYZER_LIMIT", "10")
)

SAMPLE_RATE = 22050
CHANNELS = 1
MAX_SECONDS = 90

TEMP_DIR = os.getenv(
    "ANALYZER_TEMP_DIR",
    tempfile.gettempdir(),
)

MAX_FILE_MB = int(
    os.getenv("ANALYZER_MAX_FILE_MB", "250")
)

# ------------------------------------------------------------
# Telegram retry settings
# ------------------------------------------------------------

TELEGRAM_MAX_RETRIES = int(
    os.getenv("TELEGRAM_MAX_RETRIES", "6")
)

TELEGRAM_RETRY_DELAY = float(
    os.getenv("TELEGRAM_RETRY_DELAY", "3")
)

TELEGRAM_MAX_RETRY_DELAY = float(
    os.getenv("TELEGRAM_MAX_RETRY_DELAY", "30")
)

TELEGRAM_TIMEOUT = int(
    os.getenv("TELEGRAM_TIMEOUT", "60")
)

TELEGRAM_RECONNECT_RETRIES = int(
    os.getenv("TELEGRAM_RECONNECT_RETRIES", "5")
)

TELEGRAM_RECONNECT_DELAY = float(
    os.getenv("TELEGRAM_RECONNECT_DELAY", "5")
)

# Delay between tracks
TRACK_DELAY = float(
    os.getenv("ANALYZER_TRACK_DELAY", "1.5")
)

# ------------------------------------------------------------
# Database retry settings
# ------------------------------------------------------------

DB_CONNECT_RETRIES = int(
    os.getenv("DB_CONNECT_RETRIES", "5")
)

DB_RETRY_DELAY = float(
    os.getenv("DB_RETRY_DELAY", "2")
)

DB_MAX_RETRY_DELAY = float(
    os.getenv("DB_MAX_RETRY_DELAY", "20")
)

DB_CONNECT_TIMEOUT = int(
    os.getenv("DB_CONNECT_TIMEOUT", "15")
)

# ------------------------------------------------------------
# FFmpeg
# ------------------------------------------------------------

FFMPEG_TIMEOUT = int(
    os.getenv("ANALYZER_FFMPEG_TIMEOUT", "180")
)

# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------

LOG_LEVEL = os.getenv(
    "LOG_LEVEL",
    "INFO",
).upper()

logging.basicConfig(
    level=getattr(
        logging,
        LOG_LEVEL,
        logging.INFO,
    ),
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger(
    "not-your-vibe-audio-analyzer"
)


# ============================================================
# CUSTOM ERRORS
# ============================================================

class PermanentTelegramMessageError(Exception):
    """
    The Telegram message is permanently unavailable.

    Examples:
    - message deleted
    - invalid message ID
    - inaccessible channel
    """

    pass


class TemporaryTelegramError(Exception):
    """
    Telegram/network error that should be retried.
    """

    pass


# ============================================================
# HELPERS
# ============================================================

def normalize_database_url(url: str) -> str:
    if not url:
        return ""

    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]

    return url


def retry_delay(
    attempt: int,
    base: float,
    maximum: float,
) -> float:
    """
    Exponential backoff + small jitter.
    """

    value = min(
        maximum,
        base * (2 ** max(0, attempt - 1)),
    )

    jitter = random.uniform(
        0,
        min(1.0, value * 0.15),
    )

    return value + jitter


async def sleep_backoff(
    attempt: int,
    base: float,
    maximum: float,
):
    delay = retry_delay(
        attempt,
        base,
        maximum,
    )

    await asyncio.sleep(delay)


def is_retryable_db_error(exc: Exception) -> bool:
    text = str(exc).lower()

    if isinstance(
        exc,
        (
            psycopg.OperationalError,
            psycopg.InterfaceError,
        ),
    ):
        return True

    retry_words = (
        "timeout",
        "timed out",
        "connection",
        "server closed",
        "connection reset",
        "broken pipe",
        "could not connect",
        "temporarily unavailable",
    )

    return any(
        word in text
        for word in retry_words
    )


def is_retryable_telegram_error(
    exc: Exception,
) -> bool:

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

    retry_words = (
        "timeout",
        "timed out",
        "connection",
        "network",
        "internal",
        "getfilerequest",
        "file reference",
        "server error",
        "temporarily unavailable",
    )

    return any(
        word in text
        for word in retry_words
    )


# ============================================================
# DATABASE
# ============================================================

def _db_connect():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is missing"
        )

    return psycopg.connect(
        normalize_database_url(
            DATABASE_URL
        ),
        connect_timeout=DB_CONNECT_TIMEOUT,
        row_factory=dict_row,
        application_name=(
            "not-your-vibe-audio-analyzer"
        ),
    )


@contextmanager
def db():
    """
    PostgreSQL connection with automatic reconnect.

    A connection failure does not kill the analyzer.
    """

    last_error = None

    for attempt in range(
        1,
        DB_CONNECT_RETRIES + 1,
    ):

        conn = None

        try:
            conn = _db_connect()

            yield conn

            conn.commit()
            return

        except Exception as exc:

            last_error = exc

            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.rollback()

            if not is_retryable_db_error(exc):
                raise

            if attempt >= DB_CONNECT_RETRIES:
                raise

            delay = retry_delay(
                attempt,
                DB_RETRY_DELAY,
                DB_MAX_RETRY_DELAY,
            )

            log.warning(
                "PostgreSQL error. "
                "Retry %s/%s in %.1fs: %s",
                attempt,
                DB_CONNECT_RETRIES,
                delay,
                exc,
            )

            time.sleep(delay)

        finally:

            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()

    if last_error:
        raise last_error


def get_pending_tracks(
    limit: int,
):
    """
    IMPORTANT:

    Temporary failures:
        analyzed = false
        ai_error = normal error

    Permanent failures:
        analyzed = false
        ai_error starts with PERMANENT:

    Permanent failures are excluded so they don't retry forever.
    """

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
            COALESCE(analyzed, FALSE) = FALSE
            AND (
                ai_error IS NULL
                OR ai_error NOT LIKE 'PERMANENT:%'
            )
        ORDER BY id ASC
        LIMIT %s
    """

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                (limit,),
            )

            return cur.fetchall()


def mark_error(
    track_id: int,
    error: Exception | str,
    permanent: bool = False,
):
    """
    Temporary:
        analyzed = false
        retry on next run

    Permanent:
        analyzed = false
        ai_error = PERMANENT:...
        excluded from future runs
    """

    message = str(error).strip()

    if permanent:
        message = (
            "PERMANENT: "
            + message
        )

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                UPDATE tracks
                SET
                    analyzed = FALSE,
                    ai_error = %s
                WHERE id = %s
                """,
                (
                    message[:2000],
                    track_id,
                ),
            )


def save_analysis(
    track_id: int,
    result: dict,
):
    with db() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                UPDATE tracks
                SET
                    bpm = %s,
                    musical_key = %s,
                    energy = %s,
                    danceability = %s,
                    loudness = %s,
                    genre = %s,
                    subgenre = %s,
                    analyzer_mood = %s,
                    analyzed = TRUE,
                    analyzed_at = NOW(),
                    ai_error = NULL
                WHERE id = %s
                """,
                (
                    result.get("bpm"),
                    result.get("key"),
                    result.get("energy"),
                    result.get("danceability"),
                    result.get("loudness"),
                    result.get("genre"),
                    result.get("subgenre"),
                    result.get("mood"),
                    track_id,
                ),
            )


# ============================================================
# TELEGRAM
# ============================================================

def build_telegram_client():

    if not TELETHON_API_ID:
        raise RuntimeError(
            "TELETHON_API_ID/API_ID is missing"
        )

    if not TELETHON_API_HASH:
        raise RuntimeError(
            "TELETHON_API_HASH/API_HASH is missing"
        )

    if not TELETHON_SESSION:
        raise RuntimeError(
            "TELETHON_SESSION is missing"
        )

    try:
        api_id = int(
            TELETHON_API_ID
        )
    except ValueError:
        raise RuntimeError(
            "TELETHON_API_ID/API_ID "
            "must be an integer"
        )

    return TelegramClient(
        StringSession(
            TELETHON_SESSION
        ),
        api_id,
        TELETHON_API_HASH,

        # Telethon internal connection retry
        connection_retries=10,
        retry_delay=5,

        # Network timeout
        timeout=TELEGRAM_TIMEOUT,

        # Let Telethon reconnect itself
        auto_reconnect=True,
    )


async def ensure_telegram_connected(
    client: TelegramClient,
):
    """
    Make absolutely sure Telethon is connected.
    """

    if client.is_connected():
        return

    last_error = None

    for attempt in range(
        1,
        TELEGRAM_RECONNECT_RETRIES + 1,
    ):

        try:

            log.warning(
                "Telegram reconnect "
                "attempt %s/%s...",
                attempt,
                TELEGRAM_RECONNECT_RETRIES,
            )

            await client.connect()

            if not await client.is_user_authorized():
                raise RuntimeError(
                    "Telethon session is not authorized"
                )

            log.info(
                "Telegram: CONNECTED"
            )

            return

        except Exception as exc:

            last_error = exc

            log.warning(
                "Telegram reconnect failed: %s",
                exc,
            )

            with contextlib.suppress(Exception):
                await client.disconnect()

            if attempt < TELEGRAM_RECONNECT_RETRIES:
                await sleep_backoff(
                    attempt,
                    TELEGRAM_RECONNECT_DELAY,
                    TELEGRAM_RECONNECT_DELAY * 4,
                )

    raise RuntimeError(
        "Telegram reconnect failed"
    ) from last_error


async def reconnect_telegram(
    client: TelegramClient,
):
    """
    Force a fresh Telegram connection.

    Used after GetFileRequest timeout.
    """

    log.warning(
        "Refreshing Telegram connection..."
    )

    with contextlib.suppress(Exception):
        await client.disconnect()

    await asyncio.sleep(2)

    await ensure_telegram_connected(
        client
    )


async def get_telegram_message(
    client: TelegramClient,
    channel_id: str,
    message_id: int,
):
    """
    Get one Telegram message.

    If the message does not exist, classify it as
    PERMANENT instead of endlessly retrying.
    """

    try:
        entity = await client.get_entity(
            int(channel_id)
            if str(channel_id).lstrip("-").isdigit()
            else channel_id
        )

    except (
        PeerIdInvalidError,
        ChannelPrivateError,
    ) as exc:

        raise PermanentTelegramMessageError(
            f"Telegram channel unavailable: "
            f"{channel_id}"
        ) from exc

    except Exception as exc:

        if is_retryable_telegram_error(exc):
            raise TemporaryTelegramError(
                str(exc)
            ) from exc

        raise

    try:

        message = await client.get_messages(
            entity,
            ids=int(message_id),
        )

    except (
        MessageIdInvalidError,
    ) as exc:

        raise PermanentTelegramMessageError(
            f"Telegram message not found: "
            f"{channel_id}/{message_id}"
        ) from exc

    except Exception as exc:

        if is_retryable_telegram_error(exc):
            raise TemporaryTelegramError(
                str(exc)
            ) from exc

        raise

    if message is None:

        raise PermanentTelegramMessageError(
            f"Telegram message not found: "
            f"{channel_id}/{message_id}"
        )

    return message


async def download_track(
    client: TelegramClient,
    channel_id: str,
    message_id: int,
    output_path: str,
):
    """
    Robust Telegram downloader.

    Handles:
      - GetFileRequest TimeoutError
      - connection errors
      - expired file references
      - FloodWait
      - Telegram reconnect
    """

    last_error = None

    for attempt in range(
        1,
        TELEGRAM_MAX_RETRIES + 1,
    ):

        try:

            await ensure_telegram_connected(
                client
            )

            message = await get_telegram_message(
                client,
                channel_id,
                message_id,
            )

            # A message exists but contains no downloadable media.
            if not getattr(
                message,
                "media",
                None,
            ):
                raise PermanentTelegramMessageError(
                    "Telegram message has no media"
                )

            log.info(
                "Telegram download attempt "
                "%s/%s: %s/%s",
                attempt,
                TELEGRAM_MAX_RETRIES,
                channel_id,
                message_id,
            )

            downloaded = await asyncio.wait_for(
                client.download_media(
                    message,
                    file=output_path,
                ),
                timeout=TELEGRAM_TIMEOUT,
            )

            if not downloaded:
                raise TemporaryTelegramError(
                    "Telegram returned no downloaded file"
                )

            if not os.path.exists(
                downloaded
            ):
                raise TemporaryTelegramError(
                    "Downloaded file does not exist"
                )

            size = os.path.getsize(
                downloaded
            )

            if size <= 0:
                raise TemporaryTelegramError(
                    "Downloaded file is empty"
                )

            if size > (
                MAX_FILE_MB * 1024 * 1024
            ):
                raise PermanentTelegramMessageError(
                    "Downloaded file exceeds "
                    f"{MAX_FILE_MB} MB limit"
                )

            return downloaded

        except PermanentTelegramMessageError:
            raise

        except FloodWaitError as exc:

            last_error = exc

            wait_seconds = int(
                getattr(
                    exc,
                    "seconds",
                    5,
                )
            )

            # IMPORTANT:
            # Do NOT cap Telegram's actual FloodWait time.
            log.warning(
                "Telegram FloodWait: "
                "sleeping %ss",
                wait_seconds,
            )

            await asyncio.sleep(
                wait_seconds + 1
            )

        except (
            TemporaryTelegramError,
            asyncio.TimeoutError,
            TimeoutError,
            ConnectionError,
            FileReferenceExpiredError,
        ) as exc:

            last_error = exc

            log.warning(
                "Telegram download temporary "
                "error %s/%s: %s",
                attempt,
                TELEGRAM_MAX_RETRIES,
                exc,
            )

            # Fresh connection after GetFileRequest timeout
            if attempt < TELEGRAM_MAX_RETRIES:

                with contextlib.suppress(Exception):
                    await reconnect_telegram(
                        client
                    )

                await sleep_backoff(
                    attempt,
                    TELEGRAM_RETRY_DELAY,
                    TELEGRAM_MAX_RETRY_DELAY,
                )

        except RPCError as exc:

            last_error = exc

            if not is_retryable_telegram_error(
                exc
            ):
                raise

            log.warning(
                "Telegram RPC temporary error "
                "%s/%s: %s",
                attempt,
                TELEGRAM_MAX_RETRIES,
                exc,
            )

            if attempt < TELEGRAM_MAX_RETRIES:

                with contextlib.suppress(Exception):
                    await reconnect_telegram(
                        client
                    )

                await sleep_backoff(
                    attempt,
                    TELEGRAM_RETRY_DELAY,
                    TELEGRAM_MAX_RETRY_DELAY,
                )

        except Exception as exc:

            last_error = exc

            if not is_retryable_telegram_error(
                exc
            ):
                raise

            log.warning(
                "Telegram unexpected temporary "
                "error %s/%s: %s",
                attempt,
                TELEGRAM_MAX_RETRIES,
                exc,
            )

            if attempt < TELEGRAM_MAX_RETRIES:

                with contextlib.suppress(Exception):
                    await reconnect_telegram(
                        client
                    )

                await sleep_backoff(
                    attempt,
                    TELEGRAM_RETRY_DELAY,
                    TELEGRAM_MAX_RETRY_DELAY,
                )

    raise TemporaryTelegramError(
        "Telegram download failed after "
        f"{TELEGRAM_MAX_RETRIES} attempts: "
        f"{last_error}"
    )


# ============================================================
# FFMPEG
# ============================================================

async def convert_to_wav(
    input_path: str,
    output_path: str,
):
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        input_path,
        "-ac",
        str(CHANNELS),
        "-ar",
        str(SAMPLE_RATE),
        "-t",
        str(MAX_SECONDS),
        "-f",
        "wav",
        output_path,
    ]

    try:

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=FFMPEG_TIMEOUT,
        )

    except asyncio.TimeoutError as exc:

        with contextlib.suppress(Exception):
            process.kill()

        raise RuntimeError(
            "FFmpeg conversion timed out"
        ) from exc

    if process.returncode != 0:

        error = (
            stderr.decode(
                "utf-8",
                errors="ignore",
            ).strip()
        )

        raise RuntimeError(
            "FFmpeg failed: "
            + error[:1000]
        )

    if not os.path.exists(
        output_path
    ):
        raise RuntimeError(
            "FFmpeg did not create WAV file"
        )

    return output_path


def read_wav(
    path: str,
):
    with wave.open(
        path,
        "rb",
    ) as wf:

        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        frames = wf.getnframes()

        raw = wf.readframes(
            frames
        )

    if sample_width == 1:
        audio = (
            np.frombuffer(
                raw,
                dtype=np.uint8,
            ).astype(
                np.float32
            )
            - 128
        ) / 128.0

    elif sample_width == 2:
        audio = (
            np.frombuffer(
                raw,
                dtype=np.int16,
            ).astype(
                np.float32
            )
            / 32768.0
        )

    elif sample_width == 4:
        audio = (
            np.frombuffer(
                raw,
                dtype=np.int32,
            ).astype(
                np.float32
            )
            / 2147483648.0
        )

    else:
        raise RuntimeError(
            f"Unsupported WAV sample width: "
            f"{sample_width}"
        )

    if channels > 1:

        audio = audio.reshape(
            -1,
            channels,
        ).mean(
            axis=1
        )

    return (
        audio,
        sample_rate,
    )


# ============================================================
# AUDIO ANALYSIS
# ============================================================

def rms_energy(
    audio: np.ndarray,
) -> float:

    if len(audio) == 0:
        return 0.0

    rms = float(
        np.sqrt(
            np.mean(
                np.square(audio)
            )
        )
    )

    # Convert roughly to 0-100.
    value = (
        20.0
        * math.log10(
            max(rms, 1e-8)
        )
    )

    normalized = (
        (value + 40.0)
        / 40.0
        * 100.0
    )

    return float(
        np.clip(
            normalized,
            0,
            100,
        )
    )


def loudness_db(
    audio: np.ndarray,
) -> float:

    if len(audio) == 0:
        return -100.0

    rms = float(
        np.sqrt(
            np.mean(
                np.square(audio)
            )
        )
    )

    return float(
        20.0
        * math.log10(
            max(rms, 1e-8)
        )
    )


def spectral_features(
    audio: np.ndarray,
    sr: int,
):
    if len(audio) < 2:
        return {
            "centroid": 0.0,
            "bandwidth": 0.0,
            "rolloff": 0.0,
            "flatness": 0.0,
        }

    n = min(
        len(audio),
        sr * 30,
    )

    signal = audio[:n]

    window = np.hanning(
        len(signal)
    )

    spectrum = np.abs(
        np.fft.rfft(
            signal * window
        )
    )

    freqs = np.fft.rfftfreq(
        len(signal),
        1.0 / sr,
    )

    total = float(
        np.sum(spectrum)
    )

    if total <= 1e-12:
        return {
            "centroid": 0.0,
            "bandwidth": 0.0,
            "rolloff": 0.0,
            "flatness": 0.0,
        }

    centroid = float(
        np.sum(
            freqs * spectrum
        )
        / total
    )

    bandwidth = float(
        np.sqrt(
            np.sum(
                ((freqs - centroid) ** 2)
                * spectrum
            )
            / total
        )
    )

    cumulative = np.cumsum(
        spectrum
    )

    target = (
        cumulative[-1] * 0.85
    )

    rolloff_index = int(
        np.searchsorted(
            cumulative,
            target,
        )
    )

    rolloff = float(
        freqs[
            min(
                rolloff_index,
                len(freqs) - 1,
            )
        ]
    )

    geometric = np.exp(
        np.mean(
            np.log(
                spectrum + 1e-12
            )
        )
    )

    arithmetic = (
        np.mean(
            spectrum + 1e-12
        )
    )

    flatness = float(
        geometric
        / max(arithmetic, 1e-12)
    )

    return {
        "centroid": centroid,
        "bandwidth": bandwidth,
        "rolloff": rolloff,
        "flatness": flatness,
    }


def estimate_bpm(
    audio: np.ndarray,
    sr: int,
) -> Optional[float]:

    if len(audio) < sr * 2:
        return None

    # Keep the original style of lightweight
    # onset/autocorrelation BPM estimation.

    audio = audio.astype(
        np.float32
    )

    # Limit analysis size.
    max_samples = sr * 60

    if len(audio) > max_samples:
        audio = audio[:max_samples]

    # Envelope
    diff = np.abs(
        np.diff(audio)
    )

    if len(diff) < sr:
        return None

    hop = max(
        1,
        int(sr * 0.01),
    )

    envelope = np.array(
        [
            np.mean(
                diff[i:i + hop]
            )
            for i in range(
                0,
                len(diff),
                hop,
            )
        ],
        dtype=np.float32,
    )

    if len(envelope) < 20:
        return None

    envelope -= np.mean(
        envelope
    )

    std = np.std(
        envelope
    )

    if std <= 1e-9:
        return None

    envelope /= std

    envelope_rate = (
        sr / hop
    )

    min_bpm = 60.0
    max_bpm = 200.0

    min_lag = int(
        envelope_rate
        * 60.0
        / max_bpm
    )

    max_lag = int(
        envelope_rate
        * 60.0
        / min_bpm
    )

    max_lag = min(
        max_lag,
        len(envelope) - 1,
    )

    if min_lag >= max_lag:
        return None

    correlations = []

    for lag in range(
        min_lag,
        max_lag + 1,
    ):

        a = envelope[:-lag]
        b = envelope[lag:]

        if len(a) == 0:
            continue

        corr = float(
            np.mean(
                a * b
            )
        )

        correlations.append(
            (
                corr,
                lag,
            )
        )

    if not correlations:
        return None

    _, best_lag = max(
        correlations,
        key=lambda x: x[0],
    )

    bpm = (
        60.0
        * envelope_rate
        / best_lag
    )

    # Normalize common half/double-time errors.
    while bpm < 80:
        bpm *= 2

    while bpm > 180:
        bpm /= 2

    return round(
        float(bpm),
        2,
    )


def estimate_key(
    audio: np.ndarray,
    sr: int,
) -> Optional[str]:

    if len(audio) < sr * 2:
        return None

    # Lightweight chroma/key estimation.
    # Returns None when confidence is too low.

    signal = audio[: min(
        len(audio),
        sr * 60,
    )]

    n_fft = 8192

    if len(signal) < n_fft:
        return None

    window = np.hanning(
        n_fft
    )

    # Sample windows.
    positions = np.linspace(
        0,
        len(signal) - n_fft,
        num=min(
            30,
            max(
                1,
                len(signal) // (
                    sr * 2
                ),
            ),
        ),
        dtype=int,
    )

    chroma = np.zeros(
        12,
        dtype=np.float64,
    )

    freqs = np.fft.rfftfreq(
        n_fft,
        1.0 / sr,
    )

    valid = (
        freqs >= 50
    ) & (
        freqs <= 5000
    )

    freqs = freqs[valid]

    if len(freqs) == 0:
        return None

    for pos in positions:

        frame = signal[
            pos:pos + n_fft
        ]

        if len(frame) < n_fft:
            continue

        spectrum = np.abs(
            np.fft.rfft(
                frame * window
            )
        )[valid]

        notes = (
            12.0
            * np.log2(
                freqs / 440.0
            )
            + 69.0
        )

        midi = np.round(
            notes
        ).astype(int)

        for magnitude, note in zip(
            spectrum,
            midi,
        ):

            if 0 <= magnitude:
                chroma[
                    note % 12
                ] += magnitude

    total = np.sum(
        chroma
    )

    if total <= 1e-9:
        return None

    chroma /= total

    names = [
        "C",
        "C#",
        "D",
        "D#",
        "E",
        "F",
        "F#",
        "G",
        "G#",
        "A",
        "A#",
        "B",
    ]

    # Major/minor templates.
    major = np.array([
        6.35,
        2.23,
        3.48,
        2.33,
        4.38,
        4.09,
        2.52,
        5.19,
        2.39,
        3.66,
        2.29,
        2.88,
    ])

    minor = np.array([
        6.33,
        2.68,
        3.52,
        5.38,
        2.60,
        3.53,
        2.54,
        4.75,
        3.98,
        2.69,
        3.34,
        3.17,
    ])

    major /= np.linalg.norm(
        major
    )

    minor /= np.linalg.norm(
        minor
    )

    best = None

    for root in range(12):

        rotated = np.roll(
            chroma,
            -root,
        )

        rotated /= max(
            np.linalg.norm(
                rotated
            ),
            1e-12,
        )

        major_score = float(
            np.dot(
                rotated,
                major,
            )
        )

        minor_score = float(
            np.dot(
                rotated,
                minor,
            )
        )

        if best is None or (
            major_score > best[0]
        ):
            best = (
                major_score,
                names[root],
                "Major",
            )

        if (
            minor_score
            > best[0]
        ):
            best = (
                minor_score,
                names[root],
                "Minor",
            )

    if best is None:
        return None

    confidence = best[0]

    # Avoid returning unreliable keys.
    if confidence < 0.45:
        return None

    return (
        f"{best[1]} "
        f"{best[2]}"
    )


def estimate_danceability(
    audio: np.ndarray,
    sr: int,
) -> float:

    if len(audio) == 0:
        return 0.0

    features = spectral_features(
        audio,
        sr,
    )

    centroid = features[
        "centroid"
    ]

    rolloff = features[
        "rolloff"
    ]

    flatness = features[
        "flatness"
    ]

    bpm = estimate_bpm(
        audio,
        sr,
    )

    bpm_score = 50.0

    if bpm is not None:

        bpm_score = (
            100.0
            - abs(
                bpm - 125.0
            ) * 1.3
        )

    centroid_score = np.clip(
        (
            centroid - 500
        )
        / 3500
        * 100,
        0,
        100,
    )

    rolloff_score = np.clip(
        (
            rolloff - 1000
        )
        / 5000
        * 100,
        0,
        100,
    )

    flatness_score = (
        100.0
        - flatness * 100.0
    )

    result = (
        bpm_score * 0.45
        + centroid_score * 0.20
        + rolloff_score * 0.20
        + flatness_score * 0.15
    )

    return round(
        float(
            np.clip(
                result,
                0,
                100,
            )
        ),
        2,
    )


def estimate_mood(
    audio: np.ndarray,
    sr: int,
) -> str:

    energy = rms_energy(
        audio
    )

    features = spectral_features(
        audio,
        sr,
    )

    centroid = features[
        "centroid"
    ]

    bpm = estimate_bpm(
        audio,
        sr,
    )

    if bpm is None:
        bpm = 120.0

    # Mood classification.
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


def analyze_file(
    wav_path: str,
) -> dict:

    audio, sr = read_wav(
        wav_path
    )

    if len(audio) == 0:
        raise RuntimeError(
            "Audio contains no samples"
        )

    bpm = estimate_bpm(
        audio,
        sr,
    )

    key = estimate_key(
        audio,
        sr,
    )

    energy = rms_energy(
        audio
    )

    danceability = (
        estimate_danceability(
            audio,
            sr,
        )
    )

    loudness = loudness_db(
        audio
    )

    mood = estimate_mood(
        audio,
        sr,
    )

    return {
        "bpm": bpm,
        "key": key,
        "energy": round(
            energy,
            2,
        ),
        "danceability": round(
            danceability,
            2,
        ),
        "loudness": round(
            loudness,
            2,
        ),
        "genre": None,
        "subgenre": None,
        "mood": mood,
    }


# ============================================================
# TRACK PROCESSING
# ============================================================

def safe_filename(
    title: Optional[str],
    track_id: int,
):
    title = (
        title
        or f"track_{track_id}"
    )

    title = str(
        title
    ).strip()

    if not title:
        title = f"track_{track_id}"

    # Remove path separators.
    title = (
        title
        .replace("/", "_")
        .replace("\\", "_")
        .replace("\x00", "")
    )

    return title[:180]


async def process_track(
    client: TelegramClient,
    track: dict,
):

    track_id = int(
        track["id"]
    )

    channel_id = str(
        track["channel_id"]
    )

    message_id = int(
        track["message_id"]
    )

    title = track.get(
        "title"
    )

    print()
    print("=" * 60)
    print(
        f"TRACK #{track_id} | "
        f"{title or 'Unknown'}"
    )
    print(
        f"Telegram: "
        f"{channel_id}/{message_id}"
    )
    print("=" * 60)

    temp_audio = os.path.join(
        TEMP_DIR,
        safe_filename(
            title,
            track_id,
        ),
    )

    # We don't trust extension because Telegram may return
    # mp3/m4a/ogg/etc.
    temp_audio += (
        f"_{track_id}_{message_id}.media"
    )

    temp_wav = os.path.join(
        TEMP_DIR,
        f"nyv_analyzer_{track_id}.wav",
    )

    try:

        print(
            "1/4 Downloading Telegram audio..."
        )

        downloaded = await download_track(
            client,
            channel_id,
            message_id,
            temp_audio,
        )

        print(
            f"Downloaded: {downloaded}"
        )

        print(
            "2/4 Running FFmpeg..."
        )

        await convert_to_wav(
            downloaded,
            temp_wav,
        )

        print(
            "3/4 Analyzing audio..."
        )

        result = analyze_file(
            temp_wav
        )

        print(
            "4/4 Saving to PostgreSQL..."
        )

        save_analysis(
            track_id,
            result,
        )

        print()
        print(
            "✅ ANALYZED"
        )
        print(
            f"   BPM:          "
            f"{result.get('bpm')}"
        )
        print(
            f"   Key:          "
            f"{result.get('key')}"
        )
        print(
            f"   Energy:       "
            f"{result.get('energy')}"
        )
        print(
            f"   Danceability: "
            f"{result.get('danceability')}"
        )
        print(
            f"   Loudness:     "
            f"{result.get('loudness')}"
        )
        print(
            f"   Mood:         "
            f"{result.get('mood')}"
        )

        return True

    except PermanentTelegramMessageError as exc:

        print()
        print(
            "❌ PERMANENT FAILURE: "
            f"{exc}"
        )

        # Do NOT retry this track on the next run.
        mark_error(
            track_id,
            exc,
            permanent=True,
        )

        return False

    except Exception as exc:

        print()
        print(
            f"❌ FAILED: {exc}"
        )

        # IMPORTANT:
        # analyzed remains FALSE.
        #
        # Therefore this track will be selected
        # again on the next analyzer run.
        mark_error(
            track_id,
            exc,
            permanent=False,
        )

        return False

    finally:

        with contextlib.suppress(Exception):
            if os.path.exists(
                temp_audio
            ):
                os.remove(
                    temp_audio
                )

        with contextlib.suppress(Exception):
            if os.path.exists(
                temp_wav
            ):
                os.remove(
                    temp_wav
                )


# ============================================================
# MAIN ANALYZER
# ============================================================

async def run_analyzer(
    limit: int,
):

    print()
    print(
        "NOT YOUR VIBE AUDIO ANALYZER"
    )
    print("=" * 60)

    tracks = get_pending_tracks(
        limit
    )

    print(
        f"Tracks selected: {len(tracks)}"
    )

    if not tracks:
        print()
        print(
            "No pending tracks."
        )
        print(
            "All retryable tracks are already analyzed."
        )
        return 0

    client = build_telegram_client()

    success = 0
    failed = 0

    try:

        await ensure_telegram_connected(
            client
        )

        print()
        print(
            "Telethon: CONNECTED"
        )

        for index, track in enumerate(
            tracks,
            start=1,
        ):

            try:

                result = await process_track(
                    client,
                    track,
                )

                if result:
                    success += 1
                else:
                    failed += 1

            except Exception as exc:

                # Extra safety:
                # one track can NEVER kill the whole run.
                failed += 1

                log.exception(
                    "Unhandled track error "
                    "track_id=%s",
                    track.get("id"),
                )

                with contextlib.suppress(Exception):
                    mark_error(
                        int(track["id"]),
                        exc,
                        permanent=False,
                    )

            # Small delay between tracks.
            if index < len(tracks):

                await asyncio.sleep(
                    TRACK_DELAY
                )

        print()
        print("=" * 60)
        print(
            "ANALYZER FINISHED"
        )
        print(
            f"Success: {success}"
        )
        print(
            f"Failed:  {failed}"
        )

        if failed:
            print()
            print(
                "ℹ️ Temporary failed tracks "
                "remain pending and will be retried "
                "on the next run."
            )

            print(
                "ℹ️ Permanent Telegram-missing tracks "
                "are excluded from future retries."
            )

        return failed

    finally:

        with contextlib.suppress(Exception):
            if client.is_connected():
                await client.disconnect()

        print()
        print(
            "Telethon: DISCONNECTED"
        )


# ============================================================
# CLI
# ============================================================

def parse_args():

    parser = argparse.ArgumentParser(
        description=(
            "NOT YOUR VIBE Audio Analyzer"
        )
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=ANALYZER_LIMIT,
        help=(
            "Number of pending tracks "
            "to process"
        ),
    )

    return parser.parse_args()


def main():

    args = parse_args()

    if args.limit <= 0:
        raise SystemExit(
            "--limit must be greater than 0"
        )

    try:

        failed = asyncio.run(
            run_analyzer(
                args.limit
            )
        )

        # 0 = everything selected succeeded
        # 2 = some tracks failed but analyzer itself completed
        return (
            2
            if failed
            else 0
        )

    except KeyboardInterrupt:

        print()
        print(
            "Analyzer stopped by user."
        )

        return 130

    except Exception as exc:

        print()
        print(
            "❌ FATAL ANALYZER ERROR:"
        )
        print(
            str(exc)
        )

        log.exception(
            "Fatal analyzer error"
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
)
