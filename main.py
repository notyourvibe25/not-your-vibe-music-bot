from __future__ import annotations

import asyncio
import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Optional

import requests
from flask import Flask, request
from psycopg2 import InterfaceError, OperationalError
from psycopg2.extras import RealDictCursor
from psycopg2.pool import PoolError, ThreadedConnectionPool
from telethon import TelegramClient, events
from telethon.sessions import StringSession


# ============================================================
# APP
# ============================================================

app = Flask(__name__)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=(os.getenv("LOG_LEVEL") or "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
)

logger = logging.getLogger("not_your_vibe_music_bot")


# ============================================================
# ENV HELPERS
# ============================================================

def env_text(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def env_int(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:

    raw = env_text(name)

    if not raw:
        return default

    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid %s. Using default %s",
            name,
            default,
        )
        return default

    if minimum <= value <= maximum:
        return value

    logger.warning(
        "%s out of range. Using default %s",
        name,
        default,
    )

    return default


def env_bool(
    name: str,
    default: bool = False,
) -> bool:

    raw = env_text(name).lower()

    if not raw:
        return default

    return raw in {
        "1",
        "true",
        "yes",
        "on",
    }


# ============================================================
# ENVIRONMENT
# ============================================================

BOT_TOKEN = env_text(
    "BOT_TOKEN"
)

ADMIN_USER_ID = env_text(
    "ADMIN_USER_ID"
)

RENDER_EXTERNAL_URL = env_text(
    "RENDER_EXTERNAL_URL"
)

if not RENDER_EXTERNAL_URL:

    hostname = env_text(
        "RENDER_EXTERNAL_HOSTNAME"
    )

    if hostname:

        RENDER_EXTERNAL_URL = (
            f"https://{hostname}"
        )


WEBHOOK_SECRET = env_text(
    "TELEGRAM_WEBHOOK_SECRET"
)


DATABASE_URL = env_text(
    "DATABASE_URL"
)


# ============================================================
# TELETHON
# ============================================================

TELETHON_API_ID = (
    env_text("TELETHON_API_ID")
    or env_text("API_ID")
)

TELETHON_API_HASH = (
    env_text("TELETHON_API_HASH")
    or env_text("API_HASH")
)

TELETHON_SESSION = env_text(
    "TELETHON_SESSION"
)


# ============================================================
# SETTINGS
# ============================================================

HTTP_TIMEOUT = env_int(
    "TELEGRAM_HTTP_TIMEOUT",
    20,
    5,
    120,
)

WORKER_COUNT = env_int(
    "MUSIC_WORKER_COUNT",
    4,
    1,
    8,
)

DB_POOL_MAX_CONNECTIONS = env_int(
    "DB_POOL_MAX_CONNECTIONS",
    8,
    2,
    20,
)

RECENT_HISTORY_LIMIT = env_int(
    "RECENT_HISTORY_LIMIT",
    30,
    1,
    500,
)

TRACK_CANDIDATE_LIMIT = env_int(
    "TRACK_CANDIDATE_LIMIT",
    100,
    1,
    1000,
)

WEBHOOK_MAX_CONNECTIONS = env_int(
    "WEBHOOK_MAX_CONNECTIONS",
    40,
    1,
    100,
)

DROP_PENDING_UPDATES = env_bool(
    "DROP_PENDING_UPDATES",
    False,
)


# ============================================================
# MOODS
# ============================================================

MOODS = (
    "sad",
    "love",
    "chill",
    "hype",
    "dark",
    "energetic",
    "night",
    "melodic",
)


MOOD_NAMES = {

    "sad":
        "😢 SAD",

    "love":
        "❤️ LOVE",

    "chill":
        "🌙 CHILL",

    "hype":
        "🔥 HYPE",

    "dark":
        "🖤 DARK",

    "energetic":
        "⚡ ENERGETIC",

    "night":
        "🚗 NIGHT DRIVE",

    "melodic":
        "🌌 MELODIC",
}


# ============================================================
# CHANNELS
# ============================================================

MOOD_CHANNELS = {

    "sad":
        env_text(
            "SAD_CHANNEL"
        ),

    "love":
        env_text(
            "LOVE_CHANNEL"
        ),

    "chill":
        env_text(
            "CHILL_CHANNEL"
        ),

    "hype":
        "-1004427220481",

    "dark":
        env_text(
            "DARK_CHANNEL"
        ),

    "energetic":
        env_text(
            "ENERGETIC_CHANNEL"
        ),

    "night":
        env_text(
            "NIGHT_CHANNEL"
        ),

    "melodic":
        "-1004446996297",
}


# ============================================================
# AUDIO TYPES
# ============================================================

AUDIO_EXTENSIONS = (

    ".mp3",
    ".m4a",
    ".flac",
    ".wav",
    ".aac",
    ".ogg",
    ".opus",
    ".mp4",
    ".mkv",
    ".webm",

)


# ============================================================
# GLOBALS
# ============================================================

http_local = threading.local()

db_pool: Optional[
    ThreadedConnectionPool
] = None

db_pool_lock = threading.Lock()


telethon_client: Optional[
    TelegramClient
] = None

telethon_ready = threading.Event()

telethon_thread: Optional[
    threading.Thread
] = None

telethon_start_lock = threading.Lock()


music_executor = ThreadPoolExecutor(
    max_workers=WORKER_COUNT,
    thread_name_prefix="music-request",
)


pending_music_users: set[int] = set()

pending_music_lock = threading.Lock()


# ============================================================
# POSTGRESQL
# ============================================================

def normalize_database_url(
    url: str,
) -> str:

    if url.startswith(
        "postgres://"
    ):

        return (
            "postgresql://"
            + url[11:]
        )

    return url


def initialize_db_pool() -> None:

    global db_pool

    if not DATABASE_URL:

        raise RuntimeError(
            "DATABASE_URL is missing. "
            "Add Render PostgreSQL Internal Database URL."
        )

    with db_pool_lock:

        if db_pool is None:

            logger.info(
                "🐘 Creating PostgreSQL connection pool..."
            )

            db_pool = ThreadedConnectionPool(

                1,

                DB_POOL_MAX_CONNECTIONS,

                dsn=normalize_database_url(
                    DATABASE_URL
                ),

                connect_timeout=10,

                application_name=(
                    "not-your-vibe-music-bot"
                ),

            )

            logger.info(
                "✅ PostgreSQL pool ready"
            )


@contextmanager
def db_connection() -> Iterator[Any]:

    if db_pool is None:

        initialize_db_pool()

    assert db_pool is not None

    connection = None

    try:

        connection = db_pool.getconn()

        connection.autocommit = False

        try:

            with connection.cursor() as cursor:

                cursor.execute(
                    "SELECT 1"
                )

        except (
            OperationalError,
            InterfaceError,
        ):

            logger.warning(
                "♻️ Replacing broken PostgreSQL connection"
            )

            db_pool.putconn(
                connection,
                close=True,
            )

            connection = (
                db_pool.getconn()
            )

            connection.autocommit = False

        yield connection

        connection.commit()

    except Exception:

        if connection is not None:

            try:

                connection.rollback()

            except (
                OperationalError,
                InterfaceError,
            ):

                pass

        raise

    finally:

        if connection is not None:

            try:

                db_pool.putconn(
                    connection
                )

            except (
                PoolError,
                OperationalError,
                InterfaceError,
            ):

                logger.exception(
                    "Could not return DB connection"
                )


@contextmanager
def db_cursor(
    connection: Any,
) -> Iterator[Any]:

    cursor = connection.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        yield cursor

    finally:

        cursor.close()


# ============================================================
# DATABASE INIT
# ============================================================

def init_db() -> None:

    schema = """

        CREATE TABLE IF NOT EXISTS users (

            user_id BIGINT PRIMARY KEY,

            username TEXT,

            first_name TEXT,

            last_name TEXT,

            first_seen BIGINT NOT NULL,

            last_seen BIGINT NOT NULL,

            total_requests BIGINT
                NOT NULL DEFAULT 0

        );


        CREATE TABLE IF NOT EXISTS tracks (

            id BIGSERIAL PRIMARY KEY,

            mood TEXT NOT NULL,

            channel_id TEXT NOT NULL,

            message_id BIGINT NOT NULL,

            created_at BIGINT NOT NULL,

            UNIQUE(
                channel_id,
                message_id
            )

        );


        CREATE TABLE IF NOT EXISTS user_history (

            id BIGSERIAL PRIMARY KEY,

            user_id BIGINT NOT NULL,

            mood TEXT NOT NULL,

            channel_id TEXT NOT NULL,

            message_id BIGINT NOT NULL,

            sent_at BIGINT NOT NULL

        );


        CREATE TABLE IF NOT EXISTS user_state (

            user_id BIGINT PRIMARY KEY,

            mood TEXT,

            updated_at BIGINT NOT NULL

        );


        CREATE TABLE IF NOT EXISTS processed_updates (

            update_id BIGINT PRIMARY KEY,

            processed_at BIGINT NOT NULL

        );


        CREATE INDEX IF NOT EXISTS
        idx_tracks_mood

        ON tracks(mood);


        CREATE INDEX IF NOT EXISTS
        idx_history_user_mood_time

        ON user_history(
            user_id,
            mood,
            sent_at DESC,
            id DESC
        );


        CREATE INDEX IF NOT EXISTS
        idx_processed_updates_time

        ON processed_updates(
            processed_at
        );

    """

    initialize_db_pool()

    with db_connection() as connection:

        with db_cursor(connection) as cursor:

            cursor.execute(
                schema
            )

            cursor.execute(
                """
                DELETE FROM processed_updates
                WHERE processed_at < %s
                """,
                (
                    int(time.time())
                    - 604800,
                ),
            )

    logger.info(
        "✅ PostgreSQL database is ready"
    )


# ============================================================
# UPDATE DEDUPLICATION
# ============================================================

def claim_update(
    update_id: Any,
) -> bool:

    if not isinstance(
        update_id,
        int,
    ):

        return True

    try:

        with db_connection() as connection:

            with db_cursor(connection) as cursor:

                cursor.execute(

                    """
                    INSERT INTO processed_updates(
                        update_id,
                        processed_at
                    )

                    VALUES (%s, %s)

                    ON CONFLICT (
                        update_id
                    )

                    DO NOTHING

                    RETURNING update_id
                    """,

                    (
                        update_id,
                        int(time.time()),
                    ),

                )

                return (
                    cursor.fetchone()
                    is not None
                )

    except Exception:

        logger.exception(
            "Could not deduplicate update %s",
            update_id,
        )

        return True


# ============================================================
# USER
# ============================================================

def register_user(
    user: Mapping[str, Any],
) -> None:

    user_id = (
        user.get("id")
        if user
        else None
    )

    if not isinstance(
        user_id,
        int,
    ):

        return

    now = int(
        time.time()
    )

    try:

        with db_connection() as connection:

            with db_cursor(connection) as cursor:

                cursor.execute(

                    """
                    INSERT INTO users(

                        user_id,
                        username,
                        first_name,
                        last_name,
                        first_seen,
                        last_seen,
                        total_requests

                    )

                    VALUES (
                        %s, %s, %s,
                        %s, %s, %s, 1
                    )

                    ON CONFLICT (
                        user_id
                    )

                    DO UPDATE SET

                        username =
                            EXCLUDED.username,

                        first_name =
                            EXCLUDED.first_name,

                        last_name =
                            EXCLUDED.last_name,

                        last_seen =
                            EXCLUDED.last_seen,

                        total_requests =
                            users.total_requests + 1
                    """,

                    (
                        user_id,
                        user.get("username"),
                        user.get("first_name"),
                        user.get("last_name"),
                        now,
                        now,
                    ),

                )

    except Exception:

        logger.exception(
            "Could not register user %s",
            user_id,
        )


# ============================================================
# SAVE TRACK
# ============================================================

def save_track(
    mood: str,
    channel_id: Any,
    message_id: Any,
) -> None:

    if mood not in MOODS:
        return

    if not channel_id:
        return

    if not message_id:
        return

    try:

        channel_id = str(
            channel_id
        )

        message_id = int(
            message_id
        )

        with db_connection() as connection:

            with db_cursor(connection) as cursor:

                cursor.execute(

                    """
                    INSERT INTO tracks(

                        mood,
                        channel_id,
                        message_id,
                        created_at

                    )

                    VALUES (
                        %s, %s, %s, %s
                    )

                    ON CONFLICT (
                        channel_id,
                        message_id
                    )

                    DO NOTHING
                    """,

                    (
                        mood,
                        channel_id,
                        message_id,
                        int(time.time()),
                    ),

                )

    except Exception:

        logger.exception(
            "Could not save track"
        )


# ============================================================
# TRACK COUNT
# ============================================================

def get_track_count(
    mood: str,
) -> int:

    if mood not in MOODS:
        return 0

    try:

        with db_connection() as connection:

            with db_cursor(connection) as cursor:

                cursor.execute(

                    """
                    SELECT COUNT(*) AS count
                    FROM tracks
                    WHERE mood = %s
                    """,

                    (mood,),

                )

                row = cursor.fetchone()

                return (
                    int(row["count"])
                    if row
                    else 0
                )

    except Exception:

        logger.exception(
            "Could not count tracks"
        )

        return 0


# ============================================================
# USER MOOD
# ============================================================

def set_user_mood(
    user_id: int,
    mood: str,
) -> bool:

    if mood not in MOODS:
        return False

    try:

        with db_connection() as connection:

            with db_cursor(connection) as cursor:

                cursor.execute(

                    """
                    INSERT INTO user_state(

                        user_id,
                        mood,
                        updated_at

                    )

                    VALUES (%s, %s, %s)

                    ON CONFLICT (
                        user_id
                    )

                    DO UPDATE SET

                        mood =
                            EXCLUDED.mood,

                        updated_at =
                            EXCLUDED.updated_at
                    """,

                    (
                        user_id,
                        mood,
                        int(time.time()),
                    ),

                )

        return True

    except Exception:

        logger.exception(
            "Could not save user mood"
        )

        return False


def get_user_mood(
    user_id: int,
) -> Optional[str]:

    try:

        with db_connection() as connection:

            with db_cursor(connection) as cursor:

                cursor.execute(

                    """
                    SELECT mood
                    FROM user_state
                    WHERE user_id = %s
                    """,

                    (user_id,),

                )

                row = cursor.fetchone()

                if row and row["mood"] in MOODS:

                    return row["mood"]

    except Exception:

        logger.exception(
            "Could not read user mood"
        )

    return None


# ============================================================
# RESERVE TRACK
# ============================================================

def reserve_track(
    user_id: int,
    mood: str,
) -> Optional[tuple[int, str]]:

    if mood not in MOODS:
        return None

    try:

        with db_connection() as connection:

            with db_cursor(connection) as cursor:

                # Prevent two simultaneous requests
                cursor.execute(
                    """
                    SELECT pg_advisory_xact_lock(%s)
                    """,
                    (user_id,),
                )

                cursor.execute(

                    """
                    SELECT
                        channel_id,
                        message_id

                    FROM user_history

                    WHERE user_id = %s

                    AND mood = %s

                    ORDER BY
                        sent_at DESC,
                        id DESC

                    LIMIT %s
                    """,

                    (
                        user_id,
                        mood,
                        RECENT_HISTORY_LIMIT,
                    ),

                )

                recent = {

                    (
                        str(row["channel_id"]),
                        int(row["message_id"]),
                    )

                    for row in cursor.fetchall()

                }

                cursor.execute(

                    """
                    SELECT
                        message_id,
                        channel_id

                    FROM tracks

                    WHERE mood = %s

                    ORDER BY RANDOM()

                    LIMIT %s
                    """,

                    (
                        mood,
                        TRACK_CANDIDATE_LIMIT,
                    ),

                )

                rows = cursor.fetchall()

                if not rows:
                    return None

                candidates = [

                    (
                        int(row["message_id"]),
                        str(row["channel_id"]),
                    )

                    for row in rows

                    if (
                        str(row["channel_id"]),
                        int(row["message_id"]),
                    )
                    not in recent

                ]

                if not candidates:

                    candidates = [

                        (
                            int(row["message_id"]),
                            str(row["channel_id"]),
                        )

                        for row in rows

                    ]

                message_id, channel_id = (
                    random.choice(candidates)
                )

                cursor.execute(

                    """
                    INSERT INTO user_history(

                        user_id,
                        mood,
                        channel_id,
                        message_id,
                        sent_at

                    )

                    VALUES (
                        %s, %s, %s, %s, %s
                    )
                    """,

                    (
                        user_id,
                        mood,
                        channel_id,
                        message_id,
                        int(time.time()),
                    ),

                )

                return (
                    message_id,
                    channel_id,
                )

    except Exception:

        logger.exception(
            "Could not reserve track"
        )

        return None


# ============================================================
# REMOVE FAILED HISTORY
# ============================================================

def remove_failed_history(
    user_id: int,
    channel_id: str,
    message_id: int,
)
