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
