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

import psycopg2
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
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(threadName)s | "
        "%(message)s"
    ),
)

logger = logging.getLogger(
    "not-your-vibe-bot"
)


# ============================================================
# ENV HELPERS
# ============================================================

def env_text(
    name: str,
    default: str = "",
) -> str:

    return (
        os.getenv(name, default)
        or ""
    ).strip()


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
            "Invalid %s. Using %s",
            name,
            default,
        )

        return default

    if minimum <= value <= maximum:
        return value

    logger.warning(
        "%s out of range. Using %s",
        name,
        default,
    )

    return default


def env_bool(
    name: str,
    default: bool = False,
) -> bool:

    value = env_text(
        name
    ).lower()

    if not value:
        return default

    return value in {
        "1",
        "true",
        "yes",
        "on",
    }


# ============================================================
# TELEGRAM BOT
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


# ============================================================
# DATABASE
# ============================================================

DATABASE_URL = env_text(
    "DATABASE_URL"
)


# ============================================================
# TELETHON
#
# ဒီနေရာက အရေးကြီးပါတယ်။
#
# TELEGRAM_API_ID / TELEGRAM_API_HASH
# ရှိရင် သုံးမယ်။
#
# မရှိရင် အရင် code တွေမှာသုံးခဲ့တဲ့
#
# API_ID / API_HASH
#
# ကိုလည်း လက်ခံမယ်။
# ============================================================

TELETHON_API_ID = (
    env_text("TELEGRAM_API_ID")
    or env_text("TELETHON_API_ID")
    or env_text("API_ID")
)


TELETHON_API_HASH = (
    env_text("TELEGRAM_API_HASH")
    or env_text("TELETHON_API_HASH")
    or env_text("API_HASH")
)


TELETHON_SESSION = env_text(
    "TELETHON_SESSION"
)


# ============================================================
# OPTIONAL OPENAI
#
# AI key မရှိလည်း bot မရပ်ဘူး။
# ============================================================

OPENAI_API_KEY = env_text(
    "OPENAI_API_KEY"
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


DB_POOL_MAX_CONNECTIONS = env_int(
    "DB_POOL_MAX_CONNECTIONS",
    8,
    2,
    30,
)


WORKER_COUNT = env_int(
    "MUSIC_WORKER_COUNT",
    4,
    1,
    16,
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
    10,
    1000,
)


AUTO_SCAN_INTERVAL = env_int(
    "AUTO_SCAN_INTERVAL",
    300,
    60,
    3600,
)


TELETHON_RECONNECT_DELAY = env_int(
    "TELETHON_RECONNECT_DELAY",
    10,
    3,
    120,
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
# 8 MOOD CHANNELS
#
# @username နဲ့ ထည့်လို့ရတယ်။
#
# ဥပမာ:
#
# SAD_CHANNEL=@sadmooddatabase
#
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
        env_text(
            "HYPE_CHANNEL"
        ),

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
        env_text(
            "MELODIC_CHANNEL"
        ),
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


scan_running = False


scan_running_lock = threading.Lock()


scan_started_at = 0


scan_finished_at = 0


scan_channels_done = 0


scan_tracks_found = 0


scan_status_lock = threading.Lock()


pending_music_users: set[int] = set()


pending_music_lock = threading.Lock()


music_executor = ThreadPoolExecutor(
    max_workers=WORKER_COUNT,
    thread_name_prefix="music-worker",
)


http_local = threading.local()


CHANNEL_MOOD_MAP: dict[
    str,
    str
] = {}


# ============================================================
# DATABASE URL
# ============================================================

def normalize_database_url(
    url: str,
) -> str:

    if url.startswith(
        "postgres://"
    ):

        return (
            "postgresql://"
            + url[
                len("postgres://"):
            ]
        )

    return url


# ============================================================
# DATABASE POOL
# ============================================================

def initialize_db_pool() -> None:

    global db_pool

    if not DATABASE_URL:

        raise RuntimeError(
            "DATABASE_URL is missing"
        )

    with db_pool_lock:

        if db_pool is not None:
            return

        logger.info(
            "Connecting PostgreSQL..."
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
            "🟢 PostgreSQL pool ready"
        )


# ============================================================
# DATABASE CONNECTION
# ============================================================

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

            try:

                db_pool.putconn(
                    connection,
                    close=True,
                )

            except Exception:
                pass

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

            except Exception:
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
                    "DB connection return failed"
                )


# ============================================================
# DB CURSOR
# ============================================================

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
# DATABASE INITIALIZATION
# ============================================================

def init_db() -> None:

    initialize_db_pool()

    schema = """

    CREATE TABLE IF NOT EXISTS users (

        user_id BIGINT PRIMARY KEY,

        username TEXT,

        first_name TEXT,

        last_name TEXT,

        first_seen BIGINT NOT NULL,

        last_seen BIGINT NOT NULL,

        total_requests BIGINT NOT NULL DEFAULT 0
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


    CREATE TABLE IF NOT EXISTS user_radio_state (

        user_id BIGINT PRIMARY KEY,

        active BOOLEAN NOT NULL DEFAULT FALSE,

        updated_at BIGINT NOT NULL
    );


    CREATE TABLE IF NOT EXISTS liked_tracks (

        id BIGSERIAL PRIMARY KEY,

        user_id BIGINT NOT NULL,

        channel_id TEXT NOT NULL,

        message_id BIGINT NOT NULL,

        mood TEXT NOT NULL,

        liked_at BIGINT NOT NULL,

        UNIQUE(
            user_id,
            channel_id,
            message_id
        )
    );


    CREATE TABLE IF NOT EXISTS disliked_tracks (

        id BIGSERIAL PRIMARY KEY,

        user_id BIGINT NOT NULL,

        channel_id TEXT NOT NULL,

        message_id BIGINT NOT NULL,

        mood TEXT NOT NULL,

        disliked_at BIGINT NOT NULL,

        UNIQUE(
            user_id,
            channel_id,
            message_id
        )
    );


    CREATE TABLE IF NOT EXISTS channel_scan_state (

        channel_id TEXT PRIMARY KEY,

        mood TEXT NOT NULL,

        last_message_id BIGINT NOT NULL DEFAULT 0,

        last_scan_at BIGINT NOT NULL DEFAULT 0
    );


    CREATE TABLE IF NOT EXISTS processed_updates (

        update_id BIGINT PRIMARY KEY,

        processed_at BIGINT NOT NULL
    );


    CREATE INDEX IF NOT EXISTS
        idx_tracks_mood
        ON tracks(mood);


    CREATE INDEX IF NOT EXISTS
        idx_history_user_mood
        ON user_history(
            user_id,
            mood,
            sent_at DESC
        );


    CREATE INDEX IF NOT EXISTS
        idx_history_user
        ON user_history(
            user_id,
            sent_at DESC
        );


    CREATE INDEX IF NOT EXISTS
        idx_liked_user
        ON liked_tracks(
            user_id,
            liked_at DESC
        );


    CREATE INDEX IF NOT EXISTS
        idx_disliked_user
        ON disliked_tracks(
            user_id,
            disliked_at DESC
        );


    CREATE INDEX IF NOT EXISTS
        idx_processed_updates
        ON processed_updates(
            processed_at
        );

    """

    with (
        db_connection() as connection,
        db_cursor(connection) as cursor
    ):

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
        "🟢 Database initialized"
    )


# ============================================================
# USER
# ============================================================

def register_user(
    user: Mapping[str, Any],
) -> None:

    user_id = user.get("id")

    if not isinstance(
        user_id,
        int,
    ):
        return

    now = int(time.time())

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

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

                VALUES(
                    %s,%s,%s,%s,%s,%s,1
                )

                ON CONFLICT(user_id)

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
            "User registration failed"
        )


# ============================================================
# TRACK
# ============================================================

def save_track(
    mood: str,
    channel_id: Any,
    message_id: Any,
) -> bool:

    if (
        mood not in MOODS
        or not channel_id
        or not message_id
    ):
        return False

    try:

        channel_id = str(
            channel_id
        )

        message_id = int(
            message_id
        )

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                INSERT INTO tracks(
                    mood,
                    channel_id,
                    message_id,
                    created_at
                )

                VALUES(
                    %s,%s,%s,%s
                )

                ON CONFLICT(
                    channel_id,
                    message_id
                )

                DO NOTHING

                RETURNING id
                """,
                (
                    mood,
                    channel_id,
                    message_id,
                    int(time.time()),
                ),
            )

            return (
                cursor.fetchone()
                is not None
            )

    except Exception:

        logger.exception(
            "save_track failed"
        )

        return False


# ============================================================
# TRACK COUNTS
# ============================================================

def get_track_counts() -> dict[str, int]:

    result = {
        mood: 0
        for mood in MOODS
    }

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT
                    mood,
                    COUNT(*) AS count

                FROM tracks

                GROUP BY mood
                """
            )

            for row in cursor.fetchall():

                mood = row["mood"]

                if mood in result:

                    result[mood] = int(
                        row["count"]
                    )

    except Exception:

        logger.exception(
            "Track count failed"
        )

    return result


def get_track_count(
    mood: str,
) -> int:

    if mood not in MOODS:
        return 0

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT COUNT(*) AS count

                FROM tracks

                WHERE mood=%s
                """,
                (mood,),
            )

            row = cursor.fetchone()

            return int(
                row["count"]
            ) if row else 0

    except Exception:

        return 0


# ============================================================
# USER MOOD
# ============================================================

def set_user_mood(
    user_id: int,
    mood: str,
) -> bool:

    if (
        not isinstance(user_id, int)
        or mood not in MOODS
    ):
        return False

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                INSERT INTO user_state(
                    user_id,
                    mood,
                    updated_at
                )

                VALUES(
                    %s,%s,%s
                )

                ON CONFLICT(user_id)

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
            "set_user_mood failed"
        )

        return False


def get_user_mood(
    user_id: int,
) -> Optional[str]:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT mood

                FROM user_state

                WHERE user_id=%s
                """,
                (user_id,),
            )

            row = cursor.fetchone()

            if (
                row
                and row["mood"] in MOODS
            ):

                return row["mood"]

    except Exception:

        logger.exception(
            "get_user_mood failed"
        )

    return None


# ============================================================
# RADIO STATE
# ============================================================

def set_radio_state(
    user_id: int,
    active: bool,
) -> bool:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                INSERT INTO user_radio_state(
                    user_id,
                    active,
                    updated_at
                )

                VALUES(
                    %s,%s,%s
                )

                ON CONFLICT(user_id)

                DO UPDATE SET

                    active =
                        EXCLUDED.active,

                    updated_at =
                        EXCLUDED.updated_at
                """,
                (
                    user_id,
                    active,
                    int(time.time()),
                ),
            )

        return True

    except Exception:

        logger.exception(
            "set_radio_state failed"
        )

        return False


def is_radio_active(
    user_id: int,
) -> bool:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT active

                FROM user_radio_state

                WHERE user_id=%s
                """,
                (user_id,),
            )

            row = cursor.fetchone()

            return bool(
                row
                and row["active"]
            )

    except Exception:

        return False


# ============================================================
# LIKE
# ============================================================

def like_track(
    user_id: int,
    channel_id: str,
    message_id: int,
    mood: str,
) -> bool:

    if mood not in MOODS:
        return False

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                INSERT INTO liked_tracks(
                    user_id,
                    channel_id,
                    message_id,
                    mood,
                    liked_at
                )

                VALUES(
                    %s,%s,%s,%s,%s
                )

                ON CONFLICT(
                    user_id,
                    channel_id,
                    message_id
                )

                DO UPDATE SET

                    mood =
                        EXCLUDED.mood,

                    liked_at =
                        EXCLUDED.liked_at
                """,
                (
                    user_id,
                    channel_id,
                    message_id,
                    mood,
                    int(time.time()),
                ),
            )

            cursor.execute(
                """
                DELETE FROM disliked_tracks

                WHERE user_id=%s

                AND channel_id=%s

                AND message_id=%s
                """,
                (
                    user_id,
                    channel_id,
                    message_id,
                ),
            )

        logger.info(
            "❤️ LIKE user=%s mood=%s",
            user_id,
            mood,
        )

        return True

    except Exception:

        logger.exception(
            "like_track failed"
        )

        return False


# ============================================================
# DISLIKE
# ============================================================

def dislike_track(
    user_id: int,
    channel_id: str,
    message_id: int,
    mood: str,
) -> bool:

    if mood not in MOODS:
        return False

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                INSERT INTO disliked_tracks(
                    user_id,
                    channel_id,
                    message_id,
                    mood,
                    disliked_at
                )

                VALUES(
                    %s,%s,%s,%s,%s
                )

                ON CONFLICT(
                    user_id,
                    channel_id,
                    message_id
                )

                DO UPDATE SET

                    mood =
                        EXCLUDED.mood,

                    disliked_at =
                        EXCLUDED.disliked_at
                """,
                (
                    user_id,
                    channel_id,
                    message_id,
                    mood,
                    int(time.time()),
                ),
            )

            cursor.execute(
                """
                DELETE FROM liked_tracks

                WHERE user_id=%s

                AND channel_id=%s

                AND message_id=%s
                """,
                (
                    user_id,
                    channel_id,
                    message_id,
                ),
            )

        logger.info(
            "😴 DISLIKE user=%s mood=%s",
            user_id,
            mood,
        )

        return True

    except Exception:

        logger.exception(
            "dislike_track failed"
        )

        return False


# ============================================================
# HISTORY
# ============================================================

def remove_failed_history(
    user_id: int,
    channel_id: str,
    message_id: int,
) -> None:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                DELETE FROM user_history

                WHERE id = (

                    SELECT id

                    FROM user_history

                    WHERE user_id=%s

                    AND channel_id=%s

                    AND message_id=%s

                    ORDER BY id DESC

                    LIMIT 1
                )
                """,
                (
                    user_id,
                    channel_id,
                    message_id,
                ),
            )

    except Exception:

        logger.exception(
            "remove_failed_history failed"
        )


# ============================================================
# NORMAL TRACK RESERVATION
# ============================================================

def reserve_track(
    user_id: int,
    mood: str,
) -> Optional[
    tuple[int, str]
]:

    if mood not in MOODS:
        return None

    try:

        with db_connection() as connection:

            with db_cursor(connection) as cursor:

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

                    WHERE user_id=%s

                    AND mood=%s

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
                        t.channel_id,
                        t.message_id

                    FROM tracks t

                    WHERE t.mood=%s

                    AND NOT EXISTS (

                        SELECT 1

                        FROM disliked_tracks d

                        WHERE d.user_id=%s

                        AND d.channel_id =
                            t.channel_id

                        AND d.message_id =
                            t.message_id
                    )

                    ORDER BY RANDOM()

                    LIMIT %s
                    """,
                    (
                        mood,
                        user_id,
                        TRACK_CANDIDATE_LIMIT,
                    ),
                )

                rows = cursor.fetchall()

                candidates = [

                    (
                        int(row["message_id"]),
                        str(row["channel_id"]),
                    )

                    for row in rows

                    if (
                        str(row["channel_id"]),
                        int(row["message_id"]),
                    ) not in recent
                ]

                if not candidates:

                    candidates = [

                        (
                            int(row["message_id"]),
                            str(row["channel_id"]),
                        )

                        for row in rows
                    ]

                if not candidates:
                    return None

                message_id, channel_id = random.choice(
                    candidates
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

                    VALUES(
                        %s,%s,%s,%s,%s
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
            "reserve_track failed"
        )

        return None


# ============================================================
# PERSONAL RADIO
# ============================================================

def reserve_radio_track(
    user_id: int,
) -> Optional[
    tuple[int, str, str]
]:

    try:

        with db_connection() as connection:

            with db_cursor(connection) as cursor:

                cursor.execute(
                    """
                    SELECT pg_advisory_xact_lock(%s)
                    """,
                    (user_id,),
                )

                # ----------------------------------------------
                # LIKE MOODS
                # ----------------------------------------------

                cursor.execute(
                    """
                    SELECT
                        mood,
                        COUNT(*) AS likes,
                        MAX(liked_at) AS last_like

                    FROM liked_tracks

                    WHERE user_id=%s

                    GROUP BY mood

                    ORDER BY
                        likes DESC,
                        last_like DESC

                    LIMIT 8
                    """,
                    (user_id,),
                )

                like_moods = [

                    row["mood"]

                    for row in cursor.fetchall()

                    if row["mood"] in MOODS
                ]

                # ----------------------------------------------
                # USER HISTORY
                # ----------------------------------------------

                cursor.execute(
                    """
                    SELECT
                        mood,
                        COUNT(*) AS listens

                    FROM user_history

                    WHERE user_id=%s

                    GROUP BY mood

                    ORDER BY
                        listens DESC,
                        MAX(sent_at) DESC

                    LIMIT 8
                    """,
                    (user_id,),
                )

                history_moods = [

                    row["mood"]

                    for row in cursor.fetchall()

                    if row["mood"] in MOODS
                ]

                # ----------------------------------------------
                # SELECTED MOOD
                # ----------------------------------------------

                selected_mood = get_user_mood(
                    user_id
                )

                # ----------------------------------------------
                # BUILD PRIORITY
                #
                # LIKE > SELECTED MOOD > HISTORY
                # ----------------------------------------------

                favorite_moods: list[str] = []

                for mood in like_moods:

                    if mood not in favorite_moods:

                        favorite_moods.append(
                            mood
                        )

                if selected_mood:

                    if selected_mood not in favorite_moods:

                        favorite_moods.append(
                            selected_mood
                        )

                for mood in history_moods:

                    if mood not in favorite_moods:

                        favorite_moods.append(
                            mood
                        )

                if not favorite_moods:

                    favorite_moods = list(
                        MOODS
                    )

                # ----------------------------------------------
                # RECENT TRACKS
                # ----------------------------------------------

                cursor.execute(
                    """
                    SELECT
                        channel_id,
                        message_id

                    FROM user_history

                    WHERE user_id=%s

                    ORDER BY
                        sent_at DESC,
                        id DESC

                    LIMIT %s
                    """,
                    (
                        user_id,
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

                # ----------------------------------------------
                # SEARCH LIKE MOODS FIRST
                # ----------------------------------------------

                for mood in favorite_moods:

                    cursor.execute(
                        """
                        SELECT
                            t.channel_id,
                            t.message_id

                        FROM tracks t

                        WHERE t.mood=%s

                        AND NOT EXISTS (

                            SELECT 1

                            FROM disliked_tracks d

                            WHERE d.user_id=%s

                            AND d.channel_id =
                                t.channel_id

                            AND d.message_id =
                                t.message_id
                        )

                        ORDER BY RANDOM()

                        LIMIT %s
                        """,
                        (
                            mood,
                            user_id,
                            TRACK_CANDIDATE_LIMIT,
                        ),
                    )

                    rows = cursor.fetchall()

                    candidates = [

                        (
                            int(row["message_id"]),
                            str(row["channel_id"]),
                        )

                        for row in rows

                        if (
                            str(row["channel_id"]),
                            int(row["message_id"]),
                        ) not in recent
                    ]

                    if not candidates:
                        continue

                    message_id, channel_id = random.choice(
                        candidates
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

                        VALUES(
                            %s,%s,%s,%s,%s
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
                        mood,
                    )

                # ----------------------------------------------
                # ABSOLUTE FALLBACK
                # ----------------------------------------------

                cursor.execute(
                    """
                    SELECT
                        t.channel_id,
                        t.message_id,
                        t.mood

                    FROM tracks t

                    WHERE NOT EXISTS (

                        SELECT 1

                        FROM disliked_tracks d

                        WHERE d.user_id=%s

                        AND d.channel_id =
                            t.channel_id

                        AND d.message_id =
                            t.message_id
                    )

                    ORDER BY RANDOM()

                    LIMIT %s
                    """,
                    (
                        user_id,
                        TRACK_CANDIDATE_LIMIT,
                    ),
                )

                rows = cursor.fetchall()

                candidates = [

                    (
                        int(row["message_id"]),
                        str(row["channel_id"]),
                        str(row["mood"]),
                    )

                    for row in rows

                    if (
                        str(row["channel_id"]),
                        int(row["message_id"]),
                    ) not in recent
                ]

                if not candidates:
                    return None

                message_id, channel_id, mood = random.choice(
                    candidates
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

                    VALUES(
                        %s,%s,%s,%s,%s
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
                    mood,
                )

    except Exception:

        logger.exception(
            "reserve_radio_track failed"
        )

        return None


# ============================================================
# TELEGRAM HTTP
# ============================================================

def get_http_session() -> requests.Session:

    session = getattr(
        http_local,
        "session",
        None,
    )

    if session is None:

        session = requests.Session()

        session.headers.update(
            {
                "User-Agent":
                    "NOT-YOUR-VIBE-BOT"
            }
        )

        http_local.session = session

    return session


def telegram(
    method: str,
    data: Optional[
        dict[str, Any]
    ] = None,
    timeout: int = HTTP_TIMEOUT,
) -> dict[str, Any]:

    if not BOT_TOKEN:

        return {
            "ok": False,
            "description":
                "BOT_TOKEN missing",
        }

    try:

        response = get_http_session().post(

            (
                "https://api.telegram.org/"
                f"bot{BOT_TOKEN}/{method}"
            ),

            json=data or {},

            timeout=timeout,
        )

        try:

            result = response.json()

        except ValueError:

            result = {
                "ok": False,
                "description":
                    f"HTTP {response.status_code}",
            }

        if (
            response.status_code >= 400
            or not result.get("ok")
        ):

            logger.warning(
                "Telegram %s failed: %s",
                method,
                result.get(
                    "description",
                    result,
                ),
            )

        return result

    except requests.RequestException as exc:

        logger.warning(
            "Telegram request failed: %s",
            exc,
        )

        return {
            "ok": False,
            "description": str(exc),
        }


# ============================================================
# TELEGRAM UI
# ============================================================

def send_message(
    chat_id: int,
    text: str,
    keyboard: Optional[
        dict[str, Any]
    ] = None,
) -> dict[str, Any]:

    data = {

        "chat_id":
            chat_id,

        "text":
            text,

        "disable_web_page_preview":
            True,
    }

    if keyboard:

        data["reply_markup"] = keyboard

    return telegram(
        "sendMessage",
        data,
        15,
    )


def answer_callback(
    callback_id: Any,
    text: str = "",
) -> dict[str, Any]:

    return telegram(
        "answerCallbackQuery",
        {
            "callback_query_id":
                callback_id,

            "text":
                text,

            "show_alert":
                False,
        },
        8,
    )


def copy_music(
    chat_id: int,
    channel_id: str,
    message_id: int,
) -> dict[str, Any]:

    return telegram(
        "copyMessage",
        {
            "chat_id":
                chat_id,

            "from_chat_id":
                channel_id,

            "message_id":
                message_id,
        },
        30,
    )


# ============================================================
# MOOD MENU
# ============================================================

def mood_menu() -> dict[str, Any]:

    return {
        "inline_keyboard": [

            [
                {
                    "text": "😢 SAD",
                    "callback_data": "mood_sad",
                },
                {
                    "text": "❤️ LOVE",
                    "callback_data": "mood_love",
                },
            ],

            [
                {
                    "text": "🌙 CHILL",
                    "callback_data": "mood_chill",
                },
                {
                    "text": "🔥 HYPE",
                    "callback_data": "mood_hype",
                },
            ],

            [
                {
                    "text": "🖤 DARK",
                    "callback_data": "mood_dark",
                },
                {
                    "text": "⚡ ENERGETIC",
                    "callback_data": "mood_energetic",
                },
            ],

            [
                {
                    "text": "🚗 NIGHT DRIVE",
                    "callback_data": "mood_night",
                },
                {
                    "text": "🌌 MELODIC",
                    "callback_data": "mood_melodic",
                },
            ],

            [
                {
                    "text": "📻 START MY RADIO",
                    "callback_data":
                        "radio_start",
                },
            ],
        ]
    }


# ============================================================
# MUSIC BUTTONS
# ============================================================

def music_buttons(
    channel_id: str,
    message_id: int,
    mood: str,
    radio: bool = False,
) -> dict[str, Any]:

    like_callback = (
        f"like|"
        f"{channel_id}|"
        f"{message_id}|"
        f"{mood}"
    )

    dislike_callback = (
        f"dislike|"
        f"{channel_id}|"
        f"{message_id}|"
        f"{mood}"
    )

    buttons = [

        [
            {
                "text":
                    "❤️",

                "callback_data":
                    like_callback,
            },

            {
                "text":
                    "😴",

                "callback_data":
                    dislike_callback,
            },
        ],

        [
            {
                "text":
                    "⏭",

                "callback_data":
                    "next_music",
            },
        ],
    ]

    if radio:

        buttons.extend(
            [

                [
                    {
                        "text":
                            "🎛",

                        "callback_data":
                            "change_mood",
                    },
                ],

                [
                    {
                        "text":
                            "⏹",

                        "callback_data":
                            "radio_stop",
                    },
                ],

            ]
        )

    else:

        buttons.extend(
            [

                [
                    {
                        "text":
                            "📻",

                        "callback_data":
                            "radio_start",
                    },
                ],

                [
                    {
                        "text":
                            "🎛",

                        "callback_data":
                            "change_mood",
                    },
                ],

            ]
        )

    return {
        "inline_keyboard":
            buttons
    }


# ============================================================
# SEND NORMAL MUSIC
# ============================================================

def send_music(
    chat_id: int,
    user_id: int,
    mood: str,
) -> None:

    count = get_track_count(
        mood
    )

    if count <= 0:

        send_message(
            chat_id,
            (
                f"{MOOD_NAMES[mood]}\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "⚠️ ဒီ mood ထဲမှာ "
                "music မရှိသေးပါ။\n\n"
                "Channel scan ဆက်လုပ်နေပါတယ်။"
            ),
            mood_menu(),
        )

        return

    for _ in range(
        min(count, 10)
    ):

        reserved = reserve_track(
            user_id,
            mood,
        )

        if not reserved:
            break

        message_id, channel_id = reserved

        result = copy_music(
            chat_id,
            channel_id,
            message_id,
        )

        if result.get("ok"):

            send_message(
                chat_id,
                (
                    f"{MOOD_NAMES[mood]}\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    "🎧"
                ),
                music_buttons(
                    channel_id,
                    message_id,
                    mood,
                    False,
                ),
            )

            return

        remove_failed_history(
            user_id,
            channel_id,
            message_id,
        )

    send_message(
        chat_id,
        "⚠️ Track ကို အခုမပို့နိုင်သေးပါ။",
        mood_menu(),
    )


# ============================================================
# SEND RADIO
# ============================================================

def send_radio_track(
    chat_id: int,
    user_id: int,
) -> None:

    reserved = reserve_radio_track(
        user_id
    )

    if not reserved:

        send_message(
            chat_id,
            (
                "📻 MY RADIO\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "⚠️ Track မတွေ့သေးပါ။\n\n"
                "Mood တစ်ခုရွေးပြီး "
                "သီချင်းအနည်းငယ်နားထောင်ပါ။"
            ),
            mood_menu(),
        )

        return

    message_id, channel_id, mood = reserved

    result = copy_music(
        chat_id,
        channel_id,
        message_id,
    )

    if result.get("ok"):

        send_message(
            chat_id,
            (
                "📻 MY RADIO\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"{MOOD_NAMES[mood]}\n\n"
                "🎧 Recommended for you"
            ),
            music_buttons(
                channel_id,
                message_id,
                mood,
                True,
            ),
        )

        return

    remove_failed_history(
        user_id,
        channel_id,
        message_id,
    )

    send_message(
        chat_id,
        "⚠️ Track copy မအောင်မြင်ပါ။",
        music_buttons(
            channel_id,
            message_id,
            mood,
            True,
        ),
    )


# ============================================================
# MUSIC WORKERS
# ============================================================

def music_worker(
    chat_id: int,
    user_id: int,
    mood: str,
) -> None:

    try:

        send_music(
            chat_id,
            user_id,
            mood,
        )

    finally:

        with pending_music_lock:

            pending_music_users.discard(
                user_id
            )


def schedule_music(
    chat_id: int,
    user_id: int,
    mood: str,
) -> bool:

    with pending_music_lock:

        if user_id in pending_music_users:
            return False

        pending_music_users.add(
            user_id
        )

    try:

        music_executor.submit(
            music_worker,
            chat_id,
            user_id,
            mood,
        )

        return True

    except Exception:

        with pending_music_lock:

            pending_music_users.discard(
                user_id
            )

        return False


def radio_worker(
    chat_id: int,
    user_id: int,
) -> None:

    try:

        send_radio_track(
            chat_id,
            user_id,
        )

    finally:

        with pending_music_lock:

            pending_music_users.discard(
                user_id
            )


def schedule_radio(
    chat_id: int,
    user_id: int,
) -> bool:

    with pending_music_lock:

        if user_id in pending_music_users:
            return False

        pending_music_users.add(
            user_id
        )

    try:

        music_executor.submit(
            radio_worker,
            chat_id,
            user_id,
        )

        return True

    except Exception:

        with pending_music_lock:

            pending_music_users.discard(
                user_id
            )

        return False


# ============================================================
# CHANNEL NORMALIZATION
# ============================================================

def normalize_channel_id(
    entity: Any,
) -> Optional[str]:

    entity_id = getattr(
        entity,
        "id",
        None,
    )

    if not entity_id:
        return None

    value = str(
        entity_id
    )

    if value.startswith("-100"):
        return value

    return f"-100{value}"


def normalize_config_channel(
    value: str,
) -> Optional[str]:

    value = (
        value or ""
    ).strip()

    if not value:
        return None

    if value.startswith("@"):
        return value.lower()

    if value.startswith(
        "https://t.me/"
    ):

        value = value.rstrip(
            "/"
        )

        username = value.split(
            "/"
        )[-1]

        if username:

            return (
                "@"
                + username.lower()
            )

    if value.lstrip("-").isdigit():

        number = value.lstrip("-")

        return (
            "-100"
            + number
        )

    return value.lower()


# ============================================================
# CHANNEL MAP
# ============================================================

def rebuild_channel_mood_map() -> None:

    CHANNEL_MOOD_MAP.clear()

    for mood in MOODS:

        value = MOOD_CHANNELS.get(
            mood,
            "",
        )

        normalized = normalize_config_channel(
            value
        )

        if normalized:

            CHANNEL_MOOD_MAP[
                normalized
            ] = mood

    logger.info(
        "📡 Channel map: %s",
        CHANNEL_MOOD_MAP,
    )


# ============================================================
# MUSIC CHECK
# ============================================================

def is_music_message(
    message: Any,
) -> bool:

    if not message:
        return False

    if getattr(
        message,
        "audio",
        None,
    ):

        return True

    document = getattr(
        message,
        "document",
        None,
    )

    if not document:
        return False

    mime = (
        getattr(
            document,
            "mime_type",
            "",
        )
        or ""
    ).lower()

    if mime.startswith(
        "audio/"
    ):

        return True

    if mime.startswith(
        "video/"
    ):

        return True

    file_obj = getattr(
        message,
        "file",
        None,
    )

    filename = (
        getattr(
            file_obj,
            "name",
            "",
        )
        or ""
    ).lower()

    return filename.endswith(
        AUDIO_EXTENSIONS
    )


# ============================================================
# SAVE TELETHON MESSAGE
# ============================================================

def save_telethon_message(
    mood: str,
    entity: Any,
    message: Any,
) -> bool:

    if not is_music_message(
        message
    ):
        return False

    channel_id = normalize_channel_id(
        entity
    )

    message_id = getattr(
        message,
        "id",
        None,
    )

    if not channel_id or not message_id:
        return False

    return save_track(
        mood,
        channel_id,
        message_id,
    )


# ============================================================
# SCAN STATUS
# ============================================================

def set_scan_started() -> None:

    global scan_running
    global scan_started_at
    global scan_channels_done
    global scan_tracks_found

    with scan_status_lock:

        scan_running = True

        scan_started_at = int(
            time.time()
        )

        scan_channels_done = 0

        scan_tracks_found = 0


def set_scan_channel_done(
    new_tracks: int,
) -> None:

    global scan_channels_done
    global scan_tracks_found

    with scan_status_lock:

        scan_channels_done += 1

        scan_tracks_found += new_tracks


def set_scan_finished() -> None:

    global scan_running
    global scan_finished_at

    with scan_status_lock:

        scan_running = False

        scan_finished_at = int(
            time.time()
        )


# ============================================================
# SCAN ONE CHANNEL
# ============================================================

async def scan_one_channel(
    mood: str,
    channel_value: str,
) -> int:

    if telethon_client is None:
        return 0

    if not channel_value:

        logger.warning(
            "%s channel not configured",
            mood.upper(),
        )

        return 0

    try:

        # Username / ID နှစ်မျိုးလုံး support

        if channel_value.startswith("@"):

            lookup: Any = (
                channel_value
            )

        elif channel_value.lstrip(
            "-"
        ).isdigit():

            lookup = int(
                channel_value
            )

        else:

            lookup = channel_value

        entity = await (
            telethon_client.get_entity(
                lookup
            )
        )

        channel_id = normalize_channel_id(
            entity
        )

        if not channel_id:
            return 0

        new_tracks = 0

        # ----------------------------------------------------
        # Full scan
        #
        # Startup မှာ existing songs အားလုံးကို
        # DB ထဲထည့်မယ်။
        # ----------------------------------------------------

        async for message in (
            telethon_client.iter_messages(
                entity,
                limit=None,
            )
        ):

            try:

                if save_telethon_message(
                    mood,
                    entity,
                    message,
                ):

                    new_tracks += 1

            except Exception:

                logger.exception(
                    "Message scan error"
                )

        # ----------------------------------------------------
        # Scan state
        # ----------------------------------------------------

        try:

            latest_message = await (
                telethon_client.get_messages(
                    entity,
                    limit=1,
                )
            )

            latest_id = 0

            if latest_message:

                latest_id = int(
                    latest_message[0].id
                )

            with (
                db_connection() as connection,
                db_cursor(connection) as cursor
            ):

                cursor.execute(
                    """
                    INSERT INTO channel_scan_state(
                        channel_id,
                        mood,
                        last_message_id,
                        last_scan_at
                    )

                    VALUES(
                        %s,%s,%s,%s
                    )

                    ON CONFLICT(channel_id)

                    DO UPDATE SET

                        mood =
                            EXCLUDED.mood,

                        last_message_id =
                            EXCLUDED.last_message_id,

                        last_scan_at =
                            EXCLUDED.last_scan_at
                    """,
                    (
                        channel_id,
                        mood,
                        latest_id,
                        int(time.time()),
                    ),
                )

        except Exception:

            logger.exception(
                "Could not save scan state"
            )

        logger.info(
            (
                "🔎 SCAN %s complete | "
                "new=%s"
            ),
            mood.upper(),
            new_tracks,
        )

        return new_tracks

    except Exception:

        logger.exception(
            "%s channel scan failed",
            mood.upper(),
        )

        return 0


# ============================================================
# FULL SCAN
# ============================================================

async def scan_all_channels() -> None:

    if telethon_client is None:
        return

    with scan_status_lock:

        if scan_running:

            logger.info(
                "Scan already running"
            )

            return

    set_scan_started()

    logger.info(
        "========================================"
    )

    logger.info(
        "🔎 BACKGROUND MUSIC SCAN STARTED"
    )

    logger.info(
        "========================================"
    )

    rebuild_channel_mood_map()

    try:

        for mood in MOODS:

            channel = MOOD_CHANNELS.get(
                mood,
                "",
            )

            if not channel:

                logger.warning(
                    "⚠️ %s channel missing",
                    mood.upper(),
                )

                set_scan_channel_done(
                    0
                )

                continue

            found = await scan_one_channel(
                mood,
                channel,
            )

            set_scan_channel_done(
                found
            )

            # Telegram rate limit ကိုရှောင်ဖို့
            await asyncio.sleep(
                1
            )

    finally:

        set_scan_finished()

        counts = get_track_counts()

        logger.info(
            "📊 TRACK COUNTS = %s",
            counts,
        )

        logger.info(
            "🟢 BACKGROUND SCAN FINISHED"
        )


# ============================================================
# REAL-TIME NEW SONG
# ============================================================

def register_telethon_events(
    client: TelegramClient,
) -> None:

    @client.on(
        events.NewMessage(
            incoming=True
        )
    )
    async def new_music_handler(
        event: Any,
    ) -> None:

        try:

            chat_id = getattr(
                event,
                "chat_id",
                None,
            )

            if chat_id is None:
                return

            normalized_id = (
                normalize_config_channel(
                    str(chat_id)
                )
            )

            mood = CHANNEL_MOOD_MAP.get(
                normalized_id or ""
            )

            # Numeric channel map
            # မတွေ့ရင် entity username နဲ့ရှာ

            if not mood:

                try:

                    entity = await event.get_chat()

                    username = getattr(
                        entity,
                        "username",
                        None,
                    )

                    if username:

                        mood = CHANNEL_MOOD_MAP.get(
                            "@"
                            + username.lower()
                        )

                except Exception:
                    pass

            if not mood:
                return

            message = event.message

            if not is_music_message(
                message
            ):
                return

            channel_id = normalize_channel_id(
                await event.get_chat()
            )

            message_id = getattr(
                message,
                "id",
                None,
            )

            if not channel_id or not message_id:
                return

            inserted = save_track(
                mood,
                channel_id,
                message_id,
            )

            if inserted:

                logger.info(
                    (
                        "🚀 NEW SONG | "
                        "%s | "
                        "message=%s"
                    ),
                    mood.upper(),
                    message_id,
                )

        except Exception:

            logger.exception(
                "Real-time song watcher failed"
            )


# ============================================================
# PERIODIC SCANNER
# ============================================================

async def periodic_scanner() -> None:

    while True:

        try:

            await asyncio.sleep(
                AUTO_SCAN_INTERVAL
            )

            if not telethon_ready.is_set():
                continue

            await scan_all_channels()

        except asyncio.CancelledError:

            return

        except Exception:

            logger.exception(
                "Periodic scan failed"
            )


# ============================================================
# TELETHON WORKER
# ============================================================

def telethon_worker() -> None:

    global telethon_client

    if not TELETHON_API_ID:

        logger.warning(
            "⚠️ TELEGRAM_API_ID missing"
        )

    if not TELETHON_API_HASH:

        logger.warning(
            "⚠️ TELEGRAM_API_HASH missing"
        )

    if not TELETHON_SESSION:

        logger.warning(
            "⚠️ TELETHON_SESSION missing"
        )

    if not (
        TELETHON_API_ID
        and TELETHON_API_HASH
        and TELETHON_SESSION
    ):

        logger.error(
            (
                "❌ Telethon cannot start. "
                "Need API ID + API HASH + SESSION."
            )
        )

        return

    try:

        telethon_client = TelegramClient(

            StringSession(
                TELETHON_SESSION
            ),

            int(
                TELETHON_API_ID
            ),

            TELETHON_API_HASH,

            connection_retries=5,

            retry_delay=5,

            timeout=30,

            auto_reconnect=True,
        )

        register_telethon_events(
            telethon_client
        )

    except Exception:

        logger.exception(
            "Telethon creation failed"
        )

        return

    async def runner() -> None:

        assert telethon_client is not None

        while True:

            scanner_task = None

            try:

                logger.info(
                    "🔌 Connecting Telethon..."
                )

                await telethon_client.connect()

                authorized = await (
                    telethon_client
                    .is_user_authorized()
                )

                if not authorized:

                    logger.error(
                        "❌ Telethon session unauthorized"
                    )

                    return

                telethon_ready.set()

                logger.info(
                    "🟢 TELETHON CONNECTED"
                )

                # ------------------------------------------------
                # IMPORTANT
                #
                # Full scan ကို watcher ကို block မလုပ်အောင်
                # background task အနေနဲ့ run
                # ------------------------------------------------

                scanner_task = asyncio.create_task(
                    scan_all_channels()
                )

                periodic_task = asyncio.create_task(
                    periodic_scanner()
                )

                logger.info(
                    "👀 REAL-TIME WATCHER ACTIVE"
                )

                try:

                    await (
                        telethon_client
                        .run_until_disconnected()
                    )

                finally:

                    periodic_task.cancel()

                    try:

                        await periodic_task

                    except asyncio.CancelledError:
                        pass

            except asyncio.CancelledError:

                raise

            except Exception:

                logger.exception(
                    "Telethon connection error"
                )

            finally:

                telethon_ready.clear()

                if scanner_task:

                    if not scanner_task.done():

                        scanner_task.cancel()

                        try:

                            await scanner_task

                        except asyncio.CancelledError:
                            pass

                        except Exception:
                            pass

                try:

                    if (
                        telethon_client
                        and telethon_client.is_connected()
                    ):

                        await (
                            telethon_client
                            .disconnect()
                        )

                except Exception:

                    pass

            logger.warning(
                (
                    "🔄 Reconnecting Telethon "
                    "in %s seconds..."
                ),
                TELETHON_RECONNECT_DELAY,
            )

            await asyncio.sleep(
                TELETHON_RECONNECT_DELAY
            )

    try:

        asyncio.run(
            runner()
        )

    except Exception:

        logger.exception(
            "Telethon worker stopped"
        )

    finally:

        telethon_ready.clear()


def start_telethon_worker() -> None:

    global telethon_thread

    with telethon_start_lock:

        if (
            telethon_thread
            and telethon_thread.is_alive()
        ):
            return

        telethon_thread = threading.Thread(

            target=telethon_worker,

            name="telethon-worker",

            daemon=True,
        )

        telethon_thread.start()


# ============================================================
# ADMIN
# ============================================================

def is_admin(
    user_id: Any,
) -> bool:

    return bool(
        ADMIN_USER_ID
        and user_id is not None
        and str(user_id)
        == ADMIN_USER_ID
    )


def get_users_count() -> int:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM users
                """
            )

            row = cursor.fetchone()

            return int(
                row["count"]
            ) if row else 0

    except Exception:

        return 0


# ============================================================
# SCAN STATUS TEXT
# ============================================================

def get_scan_status_text() -> str:

    with scan_status_lock:

        running = scan_running

        channels_done = (
            scan_channels_done
        )

        tracks_found = (
            scan_tracks_found
        )

        started = scan_started_at

        finished = scan_finished_at

    if running:

        elapsed = (
            int(time.time())
            - started
            if started
            else 0
        )

        return (
            "🔎 Scan: RUNNING\n"
            f"📡 Channels: "
            f"{channels_done}/8\n"
            f"🎵 New tracks: "
            f"{tracks_found}\n"
            f"⏱ Running: "
            f"{elapsed}s"
        )

    if finished:

        return (
            "🟢 Scan: COMPLETE\n"
            f"📡 Channels: "
            f"{channels_done}/8\n"
            f"🎵 New tracks: "
            f"{tracks_found}"
        )

    return (
        "⚪ Scan: NOT STARTED"
    )


# ============================================================
# STATS
# ============================================================

def send_stats(
    chat_id: int,
    user_id: int,
) -> None:

    if not is_admin(
        user_id
    ):

        send_message(
            chat_id,
            "❌ Admin only.",
        )

        return

    counts = get_track_counts()

    total = sum(
        counts.values()
    )

    lines = [

        "💎 NOT YOUR VIBE",
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"👥 Users: {get_users_count()}",
        f"🎵 Tracks: {total}",
        "",
        get_scan_status_text(),
        "",
    ]

    for mood in MOODS:

        lines.append(
            (
                f"{MOOD_NAMES[mood]} "
                f"→ {counts[mood]}"
            )
        )

    lines.extend(
        [

            "",

            (
                "🟢 Telethon ONLINE"
                if telethon_ready.is_set()
                else
                "🔴 Telethon OFFLINE"
            ),

        ]
    )

    send_message(
        chat_id,
        "\n".join(lines),
    )


# ============================================================
# COMMAND
# ============================================================

def extract_command(
    text: str,
) -> str:

    if not text.startswith("/"):
        return ""

    return (
        text
        .split(maxsplit=1)[0]
        .lower()
        .split("@", 1)[0]
    )


# ============================================================
# CALLBACK
# ============================================================

def handle_callback(
    callback: Mapping[str, Any],
) -> None:

    callback_id = callback.get(
        "id"
    )

    data = (
        callback.get("data")
        or ""
    )

    user = (
        callback.get("from")
        or {}
    )

    message = (
        callback.get("message")
        or {}
    )

    chat = (
        message.get("chat")
        or {}
    )

    user_id = user.get(
        "id"
    )

    chat_id = chat.get(
        "id"
    )

    if not isinstance(
        user_id,
        int,
    ):

        return

    if not isinstance(
        chat_id,
        int,
    ):

        return

    register_user(
        user
    )

    # ========================================================
    # LIKE
    # ========================================================

    if data.startswith(
        "like|"
    ):

        try:

            _, channel_id, message_id, mood = (
                data.split(
                    "|",
                    3,
                )
            )

            message_id = int(
                message_id
            )

        except Exception:

            answer_callback(
                callback_id,
                "Invalid track",
            )

            return

        if like_track(
            user_id,
            channel_id,
            message_id,
            mood,
        ):

            answer_callback(
                callback_id,
                "❤️",
            )

        else:

            answer_callback(
                callback_id,
                "Error",
            )

        return

    # ========================================================
    # DISLIKE
    # ========================================================

    if data.startswith(
        "dislike|"
    ):

        try:

            _, channel_id, message_id, mood = (
                data.split(
                    "|",
                    3,
                )
            )

            message_id = int(
                message_id
            )

        except Exception:

            answer_callback(
                callback_id,
                "Invalid track",
            )

            return

        if dislike_track(
            user_id,
            channel_id,
            message_id,
            mood,
        ):

            answer_callback(
                callback_id,
                "😴",
            )

        else:

            answer_callback(
                callback_id,
                "Error",
            )

        return

    # ========================================================
    # MOOD
    # ========================================================

    if data.startswith(
        "mood_"
    ):

        mood = data[5:]

        if mood not in MOODS:

            answer_callback(
                callback_id,
                "Invalid mood",
            )

            return

        set_radio_state(
            user_id,
            False,
        )

        set_user_mood(
            user_id,
            mood,
        )

        answer_callback(
            callback_id,
            MOOD_NAMES[mood],
        )

        send_message(
            chat_id,
            (
                f"{MOOD_NAMES[mood]}\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "🎧 Finding your track..."
            ),
        )

        if not schedule_music(
            chat_id,
            user_id,
            mood,
        ):

            send_message(
                chat_id,
                "⏳ Track ရှာနေပြီးသားပါ။",
            )

        return

    # ========================================================
    # RADIO START
    # ========================================================

    if data == "radio_start":

        set_radio_state(
            user_id,
            True,
        )

        answer_callback(
            callback_id,
            "📻",
        )

        send_message(
            chat_id,
            (
                "📻 MY RADIO\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Your Personal Radio is ON.\n\n"
                "❤️ Like လုပ်ထားတဲ့ "
                "tracks တွေကို အဓိကယူပြီး "
                "နောက်ထပ် recommendation "
                "လုပ်ပေးမယ်။"
            ),
        )

        if not schedule_radio(
            chat_id,
            user_id,
        ):

            send_message(
                chat_id,
                "⏳ Radio ရှာနေပြီးသားပါ။",
            )

        return

    # ========================================================
    # RADIO STOP
    # ========================================================

    if data == "radio_stop":

        set_radio_state(
            user_id,
            False,
        )

        answer_callback(
            callback_id,
            "⏹",
        )

        send_message(
            chat_id,
            (
                "⏹ RADIO OFF\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Personal Radio stopped."
            ),
            mood_menu(),
        )

        return

    # ========================================================
    # NEXT
    # ========================================================

    if data == "next_music":

        answer_callback(
            callback_id,
            "⏭",
        )

        if is_radio_active(
            user_id
        ):

            if not schedule_radio(
                chat_id,
                user_id,
            ):

                answer_callback(
                    callback_id,
                    "⏳",
                )

            return

        mood = get_user_mood(
            user_id
        )

        if not mood:

            send_message(
                chat_id,
                "🎧 အရင်ဆုံး Mood ရွေးပါ 👇",
                mood_menu(),
            )

            return

        if not schedule_music(
            chat_id,
            user_id,
            mood,
        ):

            send_message(
                chat_id,
                "⏳ Track ရှာနေပြီးသားပါ။",
            )

        return

    # ========================================================
    # CHANGE MOOD
    # ========================================================

    if data == "change_mood":

        set_radio_state(
            user_id,
            False,
        )

        answer_callback(
            callback_id,
            "🎛",
        )

        send_message(
            chat_id,
            (
                "🎛 MOOD SELECTOR\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "What are you feeling?"
            ),
            mood_menu(),
        )

        return

    answer_callback(
        callback_id
    )


# ============================================================
# MESSAGE
# ============================================================

def handle_message(
    message: Mapping[str, Any],
) -> None:

    chat = (
        message.get("chat")
        or {}
    )

    user = (
        message.get("from")
        or {}
    )

    chat_id = chat.get(
        "id"
    )

    user_id = user.get(
        "id"
    )

    if not isinstance(
        chat_id,
        int,
    ):
        return

    register_user(
        user
    )

    text = (
        message.get("text")
        or ""
    ).strip()

    command = extract_command(
        text
    )

    # ========================================================
    # START
    # ========================================================

    if command == "/start":

        send_message(
            chat_id,
            (
                "💎 NOT YOUR VIBE MUSIC\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Welcome 🎧\n\n"
                "Mood ရွေးပြီး music ရယူပါ။\n\n"
                "❤️ Like လုပ်ထားတဲ့ songs တွေက "
                "Personal Radio ကို "
                "ပိုတိကျစေမယ်။\n\n"
                "👇 SELECT MOOD"
            ),
            mood_menu(),
        )

        return

    # ========================================================
    # MOOD
    # ========================================================

    if command == "/mood":

        send_message(
            chat_id,
            (
                "🎛 MOOD SELECTOR\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "What are you feeling?"
            ),
            mood_menu(),
        )

        return

    # ========================================================
    # NEXT
    # ========================================================

    if command == "/next":

        if not isinstance(
            user_id,
            int,
        ):
            return

        if is_radio_active(
            user_id
        ):

            schedule_radio(
                chat_id,
                user_id,
            )

            return

        mood = get_user_mood(
            user_id
        )

        if not mood:

            send_message(
                chat_id,
                "🎧 အရင်ဆုံး Mood ရွေးပါ 👇",
                mood_menu(),
            )

            return

        schedule_music(
            chat_id,
            user_id,
            mood,
        )

        return

    # ========================================================
    # RADIO
    # ========================================================

    if command == "/radio":

        if not isinstance(
            user_id,
            int,
        ):
            return

        set_radio_state(
            user_id,
            True,
        )

        send_message(
            chat_id,
            (
                "📻 MY RADIO\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "❤️ Like history ကို "
                "အဓိကသုံးပြီး "
                "Personal recommendation "
                "လုပ်နေပါတယ်..."
            ),
        )

        schedule_radio(
            chat_id,
            user_id,
        )

        return

    # ========================================================
    # STOP RADIO
    # ========================================================

    if command == "/stopradio":

        if isinstance(
            user_id,
            int,
        ):

            set_radio_state(
                user_id,
                False,
            )

        send_message(
            chat_id,
            (
                "⏹ RADIO OFF\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Personal Radio stopped."
            ),
            mood_menu(),
        )

        return

    # ========================================================
    # USERS
    # ========================================================

    if command == "/users":

        if is_admin(
            user_id
        ):

            send_message(
                chat_id,
                (
                    "👥 USERS\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    f"Total: "
                    f"{get_users_count()}"
                ),
            )

        else:

            send_message(
                chat_id,
                "❌ Admin only.",
            )

        return

    # ========================================================
    # STATS
    # ========================================================

    if command == "/stats":

        send_stats(
            chat_id,
            user_id,
        )

        return

    # ========================================================
    # TELEGRAM STATUS
    # ========================================================

    if command == "/telegram":

        if not is_admin(
            user_id
        ):

            send_message(
                chat_id,
                "❌ Admin only.",
            )

            return

        send_message(
            chat_id,
            (
                (
                    "🟢 TELETHON CONNECTED\n"
                    if telethon_ready.is_set()
                    else
                    "🔴 TELETHON DISCONNECTED\n"
                )
                + "\n"
                + get_scan_status_text()
            ),
        )

        return

    # ========================================================
    # HELP
    # ========================================================

    if command == "/help":

        send_message(
            chat_id,
            (
                "💎 NOT YOUR VIBE MUSIC\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "/start\n"
                "/mood\n"
                "/next\n"
                "/radio\n"
                "/stopradio\n\n"
                "👑 ADMIN\n"
                "/users\n"
                "/stats\n"
                "/telegram"
            ),
        )

        return


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

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                INSERT INTO processed_updates(
                    update_id,
                    processed_at
                )

                VALUES(
                    %s,%s
                )

                ON CONFLICT(update_id)
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

        return True


# ============================================================
# UPDATE
# ============================================================

def handle_update(
    update: Mapping[str, Any],
) -> None:

    if not claim_update(
        update.get("update_id")
    ):

        return

    callback = update.get(
        "callback_query"
    )

    if isinstance(
        callback,
        Mapping,
    ):

        handle_callback(
            callback
        )

        return

    message = update.get(
        "message"
    )

    if isinstance(
        message,
        Mapping,
    ):

        handle_message(
            message
        )


# ============================================================
# WEB
# ============================================================

@app.route("/")
def home():

    return (
        "💎 NOT YOUR VIBE MUSIC BOT ONLINE",
        200,
    )


@app.route("/health")
def health():

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                "SELECT 1"
            )

        return (
            "OK",
            200,
        )

    except Exception:

        return (
            "Database not ready",
            503,
        )


# ============================================================
# WEBHOOK
# ============================================================

@app.route(
    "/webhook",
    methods=["POST"],
)
def webhook():

    if (
        WEBHOOK_SECRET
        and request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token",
            "",
        )
        != WEBHOOK_SECRET
    ):

        return (
            "Forbidden",
            403,
        )

    try:

        update = request.get_json(
            silent=True
        )

        if isinstance(
            update,
            Mapping,
        ):

            handle_update(
                update
            )

    except Exception:

        logger.exception(
            "Webhook error"
        )

    return (
        "OK",
        200,
    )


# ============================================================
# SET WEBHOOK
# ============================================================

def setup_webhook() -> None:

    if not BOT_TOKEN:
        return

    if not RENDER_EXTERNAL_URL:

        logger.warning(
            "RENDER_EXTERNAL_URL missing"
        )

        return

    url = (
        RENDER_EXTERNAL_URL.rstrip("/")
        + "/webhook"
    )

    payload = {

        "url":
            url,

        "allowed_updates":
            [
                "message",
                "callback_query",
            ],

        "drop_pending_updates":
            DROP_PENDING_UPDATES,

        "max_connections":
            WEBHOOK_MAX_CONNECTIONS,
    }

    if WEBHOOK_SECRET:

        payload[
            "secret_token"
        ] = WEBHOOK_SECRET

    result = telegram(
        "setWebhook",
        payload,
        20,
    )

    if result.get("ok"):

        logger.info(
            "🟢 Webhook configured: %s",
            url,
        )

    else:

        logger.error(
            "Webhook failed: %s",
            result,
        )


# ============================================================
# STARTUP
# ============================================================

def startup() -> bool:

    logger.info(
        "========================================"
    )

    logger.info(
        "💎 NOT YOUR VIBE MUSIC BOT"
    )

    logger.info(
        "========================================"
    )

    # --------------------------------------------------------
    # ENV CHECK
    # --------------------------------------------------------

    logger.info(
        "BOT_TOKEN: %s",
        "OK" if BOT_TOKEN else "MISSING",
    )

    logger.info(
        "DATABASE_URL: %s",
        "OK" if DATABASE_URL else "MISSING",
    )

    logger.info(
        "TELEGRAM_API_ID: %s",
        "OK"
        if TELETHON_API_ID
        else "MISSING",
    )

    logger.info(
        "TELEGRAM_API_HASH: %s",
        "OK"
        if TELETHON_API_HASH
        else "MISSING",
    )

    logger.info(
        "TELETHON_SESSION: %s",
        "OK"
        if TELETHON_SESSION
        else "MISSING",
    )

    # --------------------------------------------------------
    # CHANNELS
    # --------------------------------------------------------

    rebuild_channel_mood_map()

    for mood in MOODS:

        channel = MOOD_CHANNELS.get(
            mood,
            "",
        )

        if channel:

            logger.info(
                "📡 %s -> %s",
                mood.upper(),
                channel,
            )

        else:

            logger.warning(
                "⚠️ %s channel missing",
                mood.upper(),
            )

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    try:

        init_db()

    except Exception:

        logger.exception(
            "❌ Database initialization failed"
        )

        return False

    # --------------------------------------------------------
    # WEBHOOK
    # --------------------------------------------------------

    setup_webhook()

    # --------------------------------------------------------
    # TELETHON
    # --------------------------------------------------------

    start_telethon_worker()

    logger.info(
        "🟢 BOT SERVER READY"
    )

    return True


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    startup()

    port = env_int(
        "PORT",
        10000,
        1,
        65535,
    )

    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True,
        use_reloader=False,
    )
