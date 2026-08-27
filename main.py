from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
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

from openai import OpenAI


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
# IMPORTANT:
#
# Render Environment Variables:
#
# TELETHON_API_ID
# TELETHON_API_HASH
# TELETHON_SESSION
#
# ============================================================

TELETHON_API_ID = env_text(
    "TELETHON_API_ID"
)

TELETHON_API_HASH = env_text(
    "TELETHON_API_HASH"
)

TELETHON_SESSION = env_text(
    "TELETHON_SESSION"
)


# ============================================================
# OPENAI
#
# Existing API key ကို
#
# OPENAI_API_KEY
#
# မှာထည့်ပါ။
# ============================================================

OPENAI_API_KEY = env_text(
    "OPENAI_API_KEY"
)

OPENAI_MODEL = env_text(
    "OPENAI_MODEL",
    "gpt-5-mini",
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
    16,
)

DB_POOL_MAX_CONNECTIONS = env_int(
    "DB_POOL_MAX_CONNECTIONS",
    8,
    2,
    30,
)

RECENT_HISTORY_LIMIT = env_int(
    "RECENT_HISTORY_LIMIT",
    30,
    1,
    500,
)

TRACK_CANDIDATE_LIMIT = env_int(
    "TRACK_CANDIDATE_LIMIT",
    80,
    10,
    500,
)

RADIO_CANDIDATE_LIMIT = env_int(
    "RADIO_CANDIDATE_LIMIT",
    60,
    10,
    200,
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
# 8 CHANNELS
#
# USERNAME နဲ့လည်းရပါတယ်။
#
# ဥပမာ:
#
# SAD_CHANNEL=@sadmooddatabase
#
# ============================================================

MOOD_CHANNELS = {

    "sad":
        env_text("SAD_CHANNEL"),

    "love":
        env_text("LOVE_CHANNEL"),

    "chill":
        env_text("CHILL_CHANNEL"),

    "hype":
        env_text("HYPE_CHANNEL"),

    "dark":
        env_text("DARK_CHANNEL"),

    "energetic":
        env_text("ENERGETIC_CHANNEL"),

    "night":
        env_text("NIGHT_CHANNEL"),

    "melodic":
        env_text("MELODIC_CHANNEL"),
}


# ============================================================
# AUDIO
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


http_local = threading.local()


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
    thread_name_prefix="music-worker",
)


pending_users: set[int] = set()

pending_users_lock = threading.Lock()


CHANNEL_MOOD_MAP: dict[
    str,
    str
] = {}


# ============================================================
# OPENAI CLIENT
# ============================================================

openai_client: Optional[
    OpenAI
] = None


def initialize_openai() -> None:

    global openai_client

    if not OPENAI_API_KEY:

        logger.warning(
            "⚠️ OPENAI_API_KEY missing"
        )

        return

    try:

        openai_client = OpenAI(
            api_key=OPENAI_API_KEY
        )

        logger.info(
            "🟢 OpenAI AI initialized | model=%s",
            OPENAI_MODEL,
        )

    except Exception:

        logger.exception(
            "OpenAI initialization failed"
        )

        openai_client = None


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
            + url[len("postgres://"):]
        )

    return url


# ============================================================
# DB POOL
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
# DB CONNECTION
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

        if connection:

            try:
                connection.rollback()
            except Exception:
                pass

        raise

    finally:

        if connection:

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

        artist TEXT,

        title TEXT,

        created_at BIGINT NOT NULL,

        UNIQUE(channel_id, message_id)
    );


    CREATE TABLE IF NOT EXISTS user_state (

        user_id BIGINT PRIMARY KEY,

        mood TEXT,

        updated_at BIGINT NOT NULL
    );


    CREATE TABLE IF NOT EXISTS user_history (

        id BIGSERIAL PRIMARY KEY,

        user_id BIGINT NOT NULL,

        mood TEXT NOT NULL,

        channel_id TEXT NOT NULL,

        message_id BIGINT NOT NULL,

        sent_at BIGINT NOT NULL
    );


    CREATE TABLE IF NOT EXISTS user_likes (

        user_id BIGINT NOT NULL,

        track_id BIGINT NOT NULL,

        liked BOOLEAN NOT NULL,

        updated_at BIGINT NOT NULL,

        PRIMARY KEY(user_id, track_id)
    );


    CREATE TABLE IF NOT EXISTS last_delivered (

        user_id BIGINT PRIMARY KEY,

        track_id BIGINT NOT NULL,

        updated_at BIGINT NOT NULL
    );


    CREATE TABLE IF NOT EXISTS user_radio_state (

        user_id BIGINT PRIMARY KEY,

        active BOOLEAN NOT NULL DEFAULT FALSE,

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
        idx_tracks_artist
        ON tracks(artist);


    CREATE INDEX IF NOT EXISTS
        idx_history_user
        ON user_history(
            user_id,
            sent_at DESC
        );


    CREATE INDEX IF NOT EXISTS
        idx_likes_user
        ON user_likes(
            user_id,
            liked
        );


    """

    with (
        db_connection() as connection,
        db_cursor(connection) as cursor
    ):

        cursor.execute(schema)

        # ----------------------------------------------------
        # Existing DB upgrade
        # ----------------------------------------------------

        cursor.execute(
            """
            ALTER TABLE tracks
            ADD COLUMN IF NOT EXISTS artist TEXT
            """
        )

        cursor.execute(
            """
            ALTER TABLE tracks
            ADD COLUMN IF NOT EXISTS title TEXT
            """
        )

        cursor.execute(
            """
            DELETE FROM processed_updates
            WHERE processed_at < %s
            """,
            (
                int(time.time()) - 604800,
            ),
        )

    logger.info(
        "🟢 PostgreSQL schema ready"
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

                    username=EXCLUDED.username,

                    first_name=EXCLUDED.first_name,

                    last_name=EXCLUDED.last_name,

                    last_seen=EXCLUDED.last_seen,

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
            "register_user failed"
        )


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

                VALUES(%s,%s,%s)

                ON CONFLICT(user_id)

                DO UPDATE SET

                    mood=EXCLUDED.mood,

                    updated_at=EXCLUDED.updated_at
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
) -> None:

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

                VALUES(%s,%s,%s)

                ON CONFLICT(user_id)

                DO UPDATE SET

                    active=EXCLUDED.active,

                    updated_at=EXCLUDED.updated_at
                """,
                (
                    user_id,
                    active,
                    int(time.time()),
                ),
            )

    except Exception:

        logger.exception(
            "set_radio_state failed"
        )


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
# TRACK TITLE PARSER
# ============================================================

def clean_text(
    text: str,
) -> str:

    text = text or ""

    text = re.sub(
        r"https?://\S+",
        "",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def parse_artist_title(
    message: Any,
) -> tuple[str, str]:

    caption = clean_text(
        getattr(
            message,
            "message",
            "",
        )
        or ""
    )

    file_obj = getattr(
        message,
        "file",
        None,
    )

    filename = clean_text(
        getattr(
            file_obj,
            "name",
            "",
        )
        or ""
    )

    source = caption or filename

    source = re.sub(
        r"\.(mp3|m4a|flac|wav|aac|ogg|opus|mp4|mkv|webm)$",
        "",
        source,
        flags=re.I,
    )

    # --------------------------------------------------------
    # Common:
    #
    # Artist - Song Title
    # --------------------------------------------------------

    if " - " in source:

        artist, title = source.split(
            " - ",
            1,
        )

        artist = artist.strip()
        title = title.strip()

        if artist and title:

            return (
                artist[:300],
                title[:500],
            )

    # --------------------------------------------------------
    # Common:
    #
    # Artist – Song
    # --------------------------------------------------------

    if " – " in source:

        artist, title = source.split(
            " – ",
            1,
        )

        artist = artist.strip()
        title = title.strip()

        if artist and title:

            return (
                artist[:300],
                title[:500],
            )

    # --------------------------------------------------------
    # Fallback
    # --------------------------------------------------------

    return (
        "",
        source[:500],
    )


# ============================================================
# TRACK SAVE
# ============================================================

def save_track(
    mood: str,
    channel_id: str,
    message_id: int,
    artist: str = "",
    title: str = "",
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
                INSERT INTO tracks(
                    mood,
                    channel_id,
                    message_id,
                    artist,
                    title,
                    created_at
                )

                VALUES(
                    %s,%s,%s,%s,%s,%s
                )

                ON CONFLICT(
                    channel_id,
                    message_id
                )

                DO UPDATE SET

                    artist =
                        COALESCE(
                            NULLIF(
                                EXCLUDED.artist,
                                ''
                            ),
                            tracks.artist
                        ),

                    title =
                        COALESCE(
                            NULLIF(
                                EXCLUDED.title,
                                ''
                            ),
                            tracks.title
                        )

                RETURNING id
                """,
                (
                    mood,
                    str(channel_id),
                    int(message_id),
                    artist,
                    title,
                    int(time.time()),
                ),
            )

            row = cursor.fetchone()

            if row:

                logger.info(
                    (
                        "🎵 TRACK | %s | "
                        "%s - %s"
                    ),
                    mood.upper(),
                    artist or "Unknown Artist",
                    title or "Unknown Title",
                )

                return True

    except Exception:

        logger.exception(
            "save_track failed"
        )

    return False


# ============================================================
# TRACK COUNT
# ============================================================

def get_track_count(
    mood: str,
) -> int:

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

            return (
                int(row["count"])
                if row
                else 0
            )

    except Exception:

        return 0


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
                SELECT mood, COUNT(*) AS count
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
            "track counts failed"
        )

    return result


# ============================================================
# TRACK INFO
# ============================================================

def get_track(
    track_id: int,
) -> Optional[dict[str, Any]]:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT *
                FROM tracks
                WHERE id=%s
                """,
                (track_id,),
            )

            row = cursor.fetchone()

            if row:

                return dict(row)

    except Exception:

        logger.exception(
            "get_track failed"
        )

    return None


# ============================================================
# LAST DELIVERED
# ============================================================

def set_last_delivered(
    user_id: int,
    track_id: int,
) -> None:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                INSERT INTO last_delivered(
                    user_id,
                    track_id,
                    updated_at
                )

                VALUES(%s,%s,%s)

                ON CONFLICT(user_id)

                DO UPDATE SET

                    track_id=EXCLUDED.track_id,

                    updated_at=EXCLUDED.updated_at
                """,
                (
                    user_id,
                    track_id,
                    int(time.time()),
                ),
            )

    except Exception:

        logger.exception(
            "set_last_delivered failed"
        )


def get_last_delivered(
    user_id: int,
) -> Optional[int]:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT track_id
                FROM last_delivered
                WHERE user_id=%s
                """,
                (user_id,),
            )

            row = cursor.fetchone()

            if row:
                return int(
                    row["track_id"]
                )

    except Exception:

        logger.exception(
            "get_last_delivered failed"
        )

    return None


# ============================================================
# LIKE / UNLIKE
# ============================================================

def set_like(
    user_id: int,
    track_id: int,
    liked: bool,
) -> bool:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                INSERT INTO user_likes(
                    user_id,
                    track_id,
                    liked,
                    updated_at
                )

                VALUES(%s,%s,%s,%s)

                ON CONFLICT(
                    user_id,
                    track_id
                )

                DO UPDATE SET

                    liked=EXCLUDED.liked,

                    updated_at=EXCLUDED.updated_at
                """,
                (
                    user_id,
                    track_id,
                    liked,
                    int(time.time()),
                ),
            )

        logger.info(
            (
                "❤️/😴 FEEDBACK | "
                "user=%s | track=%s | liked=%s"
            ),
            user_id,
            track_id,
            liked,
        )

        return True

    except Exception:

        logger.exception(
            "set_like failed"
        )

        return False


def get_liked_tracks(
    user_id: int,
    limit: int = 30,
) -> list[dict[str, Any]]:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT
                    t.id,
                    t.mood,
                    t.artist,
                    t.title

                FROM user_likes ul

                JOIN tracks t
                    ON t.id=ul.track_id

                WHERE ul.user_id=%s
                AND ul.liked=TRUE

                ORDER BY
                    ul.updated_at DESC

                LIMIT %s
                """,
                (
                    user_id,
                    limit,
                ),
            )

            return [
                dict(row)
                for row in cursor.fetchall()
            ]

    except Exception:

        logger.exception(
            "get_liked_tracks failed"
        )

        return []


# ============================================================
# HISTORY
# ============================================================

def save_history(
    user_id: int,
    track: Mapping[str, Any],
) -> None:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                INSERT INTO user_history(
                    user_id,
                    mood,
                    channel_id,
                    message_id,
                    sent_at
                )

                VALUES(%s,%s,%s,%s,%s)
                """,
                (
                    user_id,
                    track["mood"],
                    track["channel_id"],
                    track["message_id"],
                    int(time.time()),
                ),
            )

    except Exception:

        logger.exception(
            "save_history failed"
        )


def get_recent_track_ids(
    user_id: int,
    limit: int = 30,
) -> set[int]:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT t.id

                FROM user_history h

                JOIN tracks t
                    ON
                    t.channel_id=h.channel_id
                    AND
                    t.message_id=h.message_id

                WHERE h.user_id=%s

                ORDER BY
                    h.sent_at DESC,
                    h.id DESC

                LIMIT %s
                """,
                (
                    user_id,
                    limit,
                ),
            )

            return {
                int(row["id"])
                for row in cursor.fetchall()
            }

    except Exception:

        return set()


# ============================================================
# NORMAL RESERVATION
# ============================================================

def reserve_track(
    user_id: int,
    mood: str,
) -> Optional[dict[str, Any]]:

    recent = get_recent_track_ids(
        user_id
    )

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT *
                FROM tracks
                WHERE mood=%s
                ORDER BY RANDOM()
                LIMIT %s
                """,
                (
                    mood,
                    TRACK_CANDIDATE_LIMIT,
                ),
            )

            rows = [
                dict(row)
                for row in cursor.fetchall()
            ]

            if not rows:
                return None

            fresh = [
                row
                for row in rows
                if int(row["id"])
                not in recent
            ]

            candidates = (
                fresh
                or rows
            )

            return random.choice(
                candidates
            )

    except Exception:

        logger.exception(
            "reserve_track failed"
        )

        return None


# ============================================================
# RADIO CANDIDATES
# ============================================================

def get_radio_candidates(
    user_id: int,
) -> list[dict[str, Any]]:

    mood = get_user_mood(
        user_id
    )

    recent = get_recent_track_ids(
        user_id
    )

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            if mood in MOODS:

                cursor.execute(
                    """
                    SELECT *
                    FROM tracks
                    WHERE mood=%s
                    ORDER BY RANDOM()
                    LIMIT %s
                    """,
                    (
                        mood,
                        RADIO_CANDIDATE_LIMIT,
                    ),
                )

            else:

                cursor.execute(
                    """
                    SELECT *
                    FROM tracks
                    ORDER BY RANDOM()
                    LIMIT %s
                    """,
                    (
                        RADIO_CANDIDATE_LIMIT,
                    ),
                )

            rows = [
                dict(row)
                for row in cursor.fetchall()
            ]

            fresh = [
                row
                for row in rows
                if int(row["id"])
                not in recent
            ]

            return (
                fresh
                or rows
            )

    except Exception:

        logger.exception(
            "get_radio_candidates failed"
        )

        return []


# ============================================================
# AI RADIO
#
# IMPORTANT:
#
# AI က song အသစ်တီထွင်မပေးဘူး။
#
# Database ထဲက candidate ID တွေကိုပဲ
# rank လုပ်ပေးတယ်။
# ============================================================

def ai_choose_radio_track(
    liked_tracks: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> Optional[int]:

    if not candidates:
        return None

    # AI မရှိရင် fallback.

    if openai_client is None:

        return int(
            random.choice(
                candidates
            )["id"]
        )

    liked_text = "\n".join(

        (
            f"- {x.get('artist') or 'Unknown Artist'}"
            f" — "
            f"{x.get('title') or 'Unknown Title'}"
            f" [{x.get('mood')}]"
        )

        for x in liked_tracks[:30]
    )

    candidate_text = "\n".join(

        (
            f"ID={x['id']} | "
            f"{x.get('artist') or 'Unknown Artist'}"
            f" — "
            f"{x.get('title') or 'Unknown Title'}"
            f" | mood={x.get('mood')}"
        )

        for x in candidates
    )

    if not liked_text:

        liked_text = (
            "No liked tracks yet."
        )

    prompt = f"""
You are the music recommendation engine
for NOT YOUR VIBE MUSIC.

The user has liked these tracks:

{liked_text}

Available candidate tracks are:

{candidate_text}

Choose ONE candidate ID that is most
similar to the user's taste.

Consider:

- artist similarity
- song title/context
- mood
- genre/style clues
- artist patterns
- emotional atmosphere
- EDM/bass/music style

IMPORTANT:

1. You MUST choose only one ID from
   the candidate list.
2. NEVER invent a song.
3. NEVER return an ID that is not listed.
4. Return ONLY valid JSON.

Format:

{{"track_id": 123}}
"""

    try:

        response = openai_client.responses.create(

            model=OPENAI_MODEL,

            input=prompt,

        )

        text = (
            getattr(
                response,
                "output_text",
                "",
            )
            or ""
        ).strip()

        match = re.search(
            r"\{.*?\}",
            text,
            flags=re.S,
        )

        if not match:
            return None

        data = json.loads(
            match.group(0)
        )

        selected_id = int(
            data["track_id"]
        )

        valid_ids = {
            int(x["id"])
            for x in candidates
        }

        if selected_id not in valid_ids:

            logger.warning(
                "AI returned invalid track ID"
            )

            return None

        return selected_id

    except Exception:

        logger.exception(
            "AI radio recommendation failed"
        )

        return None


# ============================================================
# RESERVE RADIO
# ============================================================

def reserve_radio_track(
    user_id: int,
) -> Optional[dict[str, Any]]:

    liked_tracks = get_liked_tracks(
        user_id,
        30,
    )

    candidates = get_radio_candidates(
        user_id
    )

    if not candidates:
        return None

    selected_id = ai_choose_radio_track(
        liked_tracks,
        candidates,
    )

    if selected_id is None:

        selected = random.choice(
            candidates
        )

    else:

        selected = next(
            (
                x
                for x in candidates
                if int(x["id"])
                == selected_id
            ),
            None,
        )

        if selected is None:

            selected = random.choice(
                candidates
            )

    save_history(
        user_id,
        selected,
    )

    return selected


# ============================================================
# REMOVE FAILED HISTORY
# ============================================================

def remove_failed_history(
    user_id: int,
    track: Mapping[str, Any],
) -> None:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                DELETE FROM user_history

                WHERE user_id=%s

                AND channel_id=%s

                AND message_id=%s
                """,
                (
                    user_id,
                    track["channel_id"],
                    track["message_id"],
                ),
            )

    except Exception:

        logger.exception(
            "remove_failed_history failed"
        )


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
                    "NOT-YOUR-VIBE-MUSIC-BOT/5.0"
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

            return {
                "ok": False,
                "description":
                    (
                        "Invalid Telegram response "
                        f"HTTP {response.status_code}"
                    ),
            }

        if not isinstance(
            result,
            dict,
        ):

            return {
                "ok": False,
                "description":
                    "Invalid Telegram result",
            }

        if not result.get("ok"):

            logger.warning(
                (
                    "Telegram %s failed: %s"
                ),
                method,
                result.get(
                    "description"
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
        timeout=15,
    )


def answer_callback(
    callback_id: str,
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
        timeout=8,
    )


def edit_message(
    chat_id: int,
    message_id: int,
    text: str,
    keyboard: Optional[
        dict[str, Any]
    ] = None,
) -> dict[str, Any]:

    data = {

        "chat_id":
            chat_id,

        "message_id":
            message_id,

        "text":
            text,
    }

    if keyboard:

        data["reply_markup"] = keyboard

    return telegram(
        "editMessageText",
        data,
        timeout=15,
    )


def edit_reply_markup(
    chat_id: int,
    message_id: int,
    keyboard: dict[str, Any],
) -> dict[str, Any]:

    return telegram(
        "editMessageReplyMarkup",
        {
            "chat_id":
                chat_id,

            "message_id":
                message_id,

            "reply_markup":
                keyboard,
        },
        timeout=15,
    )


def copy_music(
    chat_id: int,
    track: Mapping[str, Any],
) -> dict[str, Any]:

    return telegram(
        "copyMessage",
        {
            "chat_id":
                chat_id,

            "from_chat_id":
                track["channel_id"],

            "message_id":
                track["message_id"],
        },
        timeout=30,
    )


# ============================================================
# MOOD MENU
# ============================================================

def mood_menu() -> dict[str, Any]:

    return {

        "inline_keyboard": [

            [
                {
                    "text":
                        "😢  SAD",
                    "callback_data":
                        "mood_sad",
                },
                {
                    "text":
                        "❤️  LOVE",
                    "callback_data":
                        "mood_love",
                },
            ],

            [
                {
                    "text":
                        "🌙  CHILL",
                    "callback_data":
                        "mood_chill",
                },
                {
                    "text":
                        "🔥  HYPE",
                    "callback_data":
                        "mood_hype",
                },
            ],

            [
                {
                    "text":
                        "🖤  DARK",
                    "callback_data":
                        "mood_dark",
                },
                {
                    "text":
                        "⚡  ENERGETIC",
                    "callback_data":
                        "mood_energetic",
                },
            ],

            [
                {
                    "text":
                        "🚗  NIGHT DRIVE",
                    "callback_data":
                        "mood_night",
                },
                {
                    "text":
                        "🌌  MELODIC",
                    "callback_data":
                        "mood_melodic",
                },
            ],

            [
                {
                    "text":
                        "📻  MY RADIO",
                    "callback_data":
                        "radio_start",
                },
            ],
        ]
    }


# ============================================================
# TRACK BUTTONS
#
# ❤️ / 😴 only feedback.
# ============================================================

def track_buttons(
    track_id: int,
    radio: bool = False,
) -> dict[str, Any]:

    return {

        "inline_keyboard": [

            [
                {
                    "text":
                        "❤️",
                    "callback_data":
                        f"like_{track_id}",
                },
                {
                    "text":
                        "😴",
                    "callback_data":
                        f"unlike_{track_id}",
                },
            ],

            [
                {
                    "text":
                        "▶️  NEXT TRACK",
                    "callback_data":
                        "next_music",
                },
            ],

            [
                {
                    "text":
                        "🎛  CHANGE MOOD",
                    "callback_data":
                        "change_mood",
                },
                {
                    "text":
                        "📻  MY RADIO",
                    "callback_data":
                        "radio_start",
                },
            ],

        ]
    }


# ============================================================
# RADIO BUTTONS
# ============================================================

def radio_buttons() -> dict[str, Any]:

    return {

        "inline_keyboard": [

            [
                {
                    "text":
                        "❤️",
                    "callback_data":
                        "like_last",
                },
                {
                    "text":
                        "😴",
                    "callback_data":
                        "unlike_last",
                },
            ],

            [
                {
                    "text":
                        "⏭  NEXT RADIO",
                    "callback_data":
                        "next_music",
                },
            ],

            [
                {
                    "text":
                        "🎛  CHANGE MOOD",
                    "callback_data":
                        "change_mood",
                },
                {
                    "text":
                        "⏹  STOP RADIO",
                    "callback_data":
                        "radio_stop",
                },
            ],
        ]
    }


# ============================================================
# TRACK CAPTION
# ============================================================

def track_info_text(
    track: Mapping[str, Any],
    radio: bool = False,
) -> str:

    artist = (
        track.get("artist")
        or "Unknown Artist"
    )

    title = (
        track.get("title")
        or "Unknown Title"
    )

    mood = (
        track.get("mood")
        or "unknown"
    )

    if radio:

        return (
            "📻  MY RADIO\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"🎧 {artist}\n"
            f"🎵 {title}\n\n"
            f"{MOOD_NAMES.get(mood, mood)}\n\n"
            "✨ Recommended for you\n"
            "Based on your likes & listening taste."
        )

    return (
        "💎  NOT YOUR VIBE\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🎧 {artist}\n"
        f"🎵 {title}\n\n"
        f"{MOOD_NAMES.get(mood, mood)}\n\n"
        "How does this track feel?"
    )


# ============================================================
# SEND NORMAL TRACK
# ============================================================

def send_music(
    chat_id: int,
    user_id: int,
    mood: str,
) -> None:

    track = reserve_track(
        user_id,
        mood,
    )

    if not track:

        send_message(
            chat_id,
            (
                f"{MOOD_NAMES[mood]}\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "⚠️ ဒီ mood ထဲမှာ "
                "track မရှိသေးပါ။"
            ),
            mood_menu(),
        )

        return

    result = copy_music(
        chat_id,
        track,
    )

    if not result.get("ok"):

        remove_failed_history(
            user_id,
            track,
        )

        send_message(
            chat_id,
            (
                "⚠️ Track ကို "
                "အခုမပို့နိုင်သေးပါ။\n"
                "NEXT ကို ပြန်နှိပ်ပါ။"
            ),
            mood_menu(),
        )

        return

    # Telegram copyMessage result
    # ထဲက copied message ID
    # ကို မလိုအပ်ပါ။
    #
    # User အတွက် original track ID
    # ကို last_delivered အဖြစ်သိမ်းထားမယ်။

    set_last_delivered(
        user_id,
        int(track["id"]),
    )

    send_message(
        chat_id,
        track_info_text(
            track,
            radio=False,
        ),
        track_buttons(
            int(track["id"]),
            radio=False,
        ),
    )


# ============================================================
# SEND RADIO TRACK
# ============================================================

def send_radio_track(
    chat_id: int,
    user_id: int,
) -> None:

    track = reserve_radio_track(
        user_id
    )

    if not track:

        send_message(
            chat_id,
            (
                "📻  MY RADIO\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "⚠️ Radio အတွက် "
                "track မတွေ့သေးပါ။\n\n"
                "အရင်ဆုံး Mood ရွေးပြီး "
                "သီချင်းတွေကို ❤️ လုပ်ပါ။"
            ),
            mood_menu(),
        )

        return

    result = copy_music(
        chat_id,
        track,
    )

    if not result.get("ok"):

        remove_failed_history(
            user_id,
            track,
        )

        send_message(
            chat_id,
            (
                "📻 Track ပို့လို့မရသေးပါ။\n"
                "⏭ NEXT RADIO ကို ပြန်နှိပ်ပါ။"
            ),
            radio_buttons(),
        )

        return

    set_last_delivered(
        user_id,
        int(track["id"]),
    )

    send_message(
        chat_id,
        track_info_text(
            track,
            radio=True,
        ),
        radio_buttons(),
    )

    logger.info(
        (
            "📻 AI RADIO | "
            "user=%s | track=%s | "
            "%s - %s"
        ),
        user_id,
        track["id"],
        track.get("artist"),
        track.get("title"),
    )


# ============================================================
# WORKERS
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

        with pending_users_lock:

            pending_users.discard(
                user_id
            )


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

        with pending_users_lock:

            pending_users.discard(
                user_id
            )


def schedule_music(
    chat_id: int,
    user_id: int,
    mood: str,
) -> bool:

    with pending_users_lock:

        if user_id in pending_users:

            return False

        pending_users.add(
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

        with pending_users_lock:

            pending_users.discard(
                user_id
            )

        return False


def schedule_radio(
    chat_id: int,
    user_id: int,
) -> bool:

    with pending_users_lock:

        if user_id in pending_users:

            return False

        pending_users.add(
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

        with pending_users_lock:

            pending_users.discard(
                user_id
            )

        return False


# ============================================================
# TELETHON CHANNEL NORMALIZATION
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

    if value.startswith(
        "-100"
    ):

        return value

    return (
        "-100"
        + value.lstrip("-")
    )


def normalize_config_channel(
    value: str,
) -> Optional[str]:

    if not value:
        return None

    value = value.strip()

    # username
    if value.startswith("@"):

        return value.lower()

    # public username without @
    if (
        not value.lstrip("-").isdigit()
        and "/" not in value
        and " " not in value
    ):

        return (
            "@"
            + value.lower()
        )

    # numeric channel ID

    if value.lstrip("-").isdigit():

        number = value.lstrip("-")

        if number.startswith(
            "100"
        ):

            return (
                "-"
                + number
            )

        return (
            "-100"
            + number
        )

    return None


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

        normalized = (
            normalize_config_channel(
                value
            )
        )

        if normalized:

            CHANNEL_MOOD_MAP[
                normalized
            ] = mood

    logger.info(
        "📡 CHANNEL MAP = %s",
        CHANNEL_MOOD_MAP,
    )


# ============================================================
# MUSIC MESSAGE
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
        (
            "audio/",
            "video/",
        )
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

    if not channel_id:

        return False

    message_id = getattr(
        message,
        "id",
        None,
    )

    if not message_id:

        return False

    artist, title = (
        parse_artist_title(
            message
        )
    )

    return save_track(
        mood,
        channel_id,
        message_id,
        artist,
        title,
    )


# ============================================================
# SCAN CHANNEL
# ============================================================

async def scan_one_channel(
    mood: str,
    channel_value: str,
) -> None:

    if (
        not channel_value
        or telethon_client is None
    ):

        return

    try:

        # ----------------------------------------------------
        # Username
        # ----------------------------------------------------

        if (
            channel_value.startswith("@")
            or not channel_value.lstrip("-").isdigit()
        ):

            lookup: Any = (
                channel_value
                if channel_value.startswith("@")
                else
                f"@{channel_value}"
            )

        else:

            lookup = int(
                channel_value
            )

        entity = await (
            telethon_client.get_entity(
                lookup
            )
        )

        count = 0

        logger.info(
            (
                "🔎 Scanning %s | %s"
            ),
            mood.upper(),
            channel_value,
        )

        async for message in (
            telethon_client.iter_messages(
                entity
            )
        ):

            try:

                if save_telethon_message(
                    mood,
                    entity,
                    message,
                ):

                    count += 1

            except Exception:

                logger.exception(
                    "Track scan error"
                )

        logger.info(
            (
                "✅ %s scan complete | "
                "new=%s | total=%s"
            ),
            mood.upper(),
            count,
            get_track_count(mood),
        )

    except Exception:

        logger.exception(
            "%s channel scan failed",
            mood.upper(),
        )


# ============================================================
# SCAN ALL
# ============================================================

async def scan_all_channels() -> None:

    logger.info(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    logger.info(
        "🔎 CHANNEL SCAN START"
    )

    logger.info(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    rebuild_channel_mood_map()

    for mood in MOODS:

        channel = MOOD_CHANNELS.get(
            mood,
            "",
        )

        if not channel:

            logger.warning(
                "%s channel not configured",
                mood.upper(),
            )

            continue

        await scan_one_channel(
            mood,
            channel,
        )

        await asyncio.sleep(
            0.5
        )

    counts = get_track_counts()

    logger.info(
        "📊 DATABASE = %s",
        counts,
    )

    logger.info(
        "✅ CHANNEL SCAN FINISHED"
    )


# ============================================================
# REALTIME NEW SONG
# ============================================================

def register_telethon_events(
    client: TelegramClient,
) -> None:

    @client.on(
        events.NewMessage(
            incoming=True
        )
    )
    async def new_song(event: Any):

        try:

            chat_id = getattr(
                event,
                "chat_id",
                None,
            )

            if chat_id is None:
                return

            normalized = (
                normalize_channel_id(
                    await event.get_chat()
                )
            )

            if not normalized:
                return

            mood = (
                CHANNEL_MOOD_MAP.get(
                    normalized
                )
            )

            # username based map fallback
            if not mood:

                username = getattr(
                    await event.get_chat(),
                    "username",
                    None,
                )

                if username:

                    mood = (
                        CHANNEL_MOOD_MAP.get(
                            "@"
                            + username.lower()
                        )
                    )

            if not mood:
                return

            message = event.message

            if not is_music_message(
                message
            ):
                return

            artist, title = (
                parse_artist_title(
                    message
                )
            )

            inserted = save_track(
                mood,
                normalized,
                int(message.id),
                artist,
                title,
            )

            if inserted:

                logger.info(
                    (
                        "🚀 NEW SONG | "
                        "%s | %s - %s"
                    ),
                    mood.upper(),
                    artist,
                    title,
                )

        except Exception:

            logger.exception(
                "Realtime watcher error"
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

            logger.info(
                "⏰ Periodic rescan..."
            )

            await scan_all_channels()

        except asyncio.CancelledError:

            return

        except Exception:

            logger.exception(
                "Periodic scan error"
            )


# ============================================================
# TELETHON WORKER
# ============================================================

def telethon_worker() -> None:

    global telethon_client

    # --------------------------------------------------------
    # IMPORTANT FIX
    #
    # API ID / HASH / SESSION
    # missing ဖြစ်ရင် warning ပြမယ်။
    # --------------------------------------------------------

    if not TELETHON_API_ID:

        logger.error(
            "❌ TELETHON_API_ID missing"
        )

        return

    if not TELETHON_API_HASH:

        logger.error(
            "❌ TELETHON_API_HASH missing"
        )

        return

    if not TELETHON_SESSION:

        logger.error(
            "❌ TELETHON_SESSION missing"
        )

        return

    logger.info(
        "🔑 Telethon credentials detected"
    )

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

    except Exception:

        logger.exception(
            "Telethon client creation failed"
        )

        return

    register_telethon_events(
        telethon_client
    )

    async def runner():

        while True:

            scanner_task = None

            try:

                logger.info(
                    "🔌 Connecting Telethon..."
                )

                await telethon_client.connect()

                if not await (
                    telethon_client
                    .is_user_authorized()
                ):

                    logger.error(
                        (
                            "❌ TELETHON SESSION "
                            "UNAUTHORIZED"
                        )
                    )

                    return

                telethon_ready.set()

                logger.info(
                    "🟢 TELETHON CONNECTED"
                )

                # ------------------------------------------------
                # Scan existing songs.
                #
                # Bot က ဒီ scan ပြီးမှမှ
                # music ပို့တာ မဟုတ်ပါဘူး။
                #
                # Database ထဲရှိပြီးသား tracks တွေကို
                # scan နဲ့ parallel မဟုတ်ပေမယ့်
                # webhook bot က run နေပြီးသားဖြစ်ပါတယ်။
                # ------------------------------------------------

                await scan_all_channels()

                scanner_task = (
                    asyncio.create_task(
                        periodic_scanner()
                    )
                )

                logger.info(
                    "👀 REALTIME WATCHER ACTIVE"
                )

                await (
                    telethon_client
                    .run_until_disconnected()
                )

            except Exception:

                logger.exception(
                    "Telethon connection error"
                )

            finally:

                telethon_ready.clear()

                if scanner_task:

                    scanner_task.cancel()

                    try:

                        await scanner_task

                    except Exception:
                        pass

                try:

                    if telethon_client.is_connected():

                        await (
                            telethon_client
                            .disconnect()
                        )

                except Exception:
                    pass

            logger.warning(
                (
                    "🔄 Telethon reconnecting "
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


def start_telethon() -> None:

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

    return (
        bool(ADMIN_USER_ID)
        and str(user_id)
        == ADMIN_USER_ID
    )


def get_user_count() -> int:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                "SELECT COUNT(*) AS count FROM users"
            )

            row = cursor.fetchone()

            return int(
                row["count"]
            )

    except Exception:

        return 0


def get_like_count() -> int:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM user_likes
                WHERE liked=TRUE
                """
            )

            row = cursor.fetchone()

            return int(
                row["count"]
            )

    except Exception:

        return 0


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

        "💎  NOT YOUR VIBE",
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"👥 Users       : {get_user_count()}",
        f"🎵 Tracks      : {total}",
        f"❤️ Likes       : {get_like_count()}",
        "",
    ]

    for mood in MOODS:

        lines.append(
            (
                f"{MOOD_NAMES[mood]}"
                f" → {counts[mood]}"
            )
        )

    lines.extend(
        [
            "",
            (
                "🤖 AI: ONLINE"
                if openai_client
                else
                "🤖 AI: OFFLINE"
            ),
            (
                "📡 Telethon: ONLINE"
                if telethon_ready.is_set()
                else
                "📡 Telethon: OFFLINE"
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

    callback_id = (
        callback.get("id")
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
    # MOOD
    # ========================================================

    if data.startswith(
        "mood_"
    ):

        mood = data[
            len("mood_"):
        ]

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
            f"{MOOD_NAMES[mood]} ✓",
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
                "⏳ Track is already being prepared.",
            )

        return

    # ========================================================
    # LIKE SPECIFIC TRACK
    # ========================================================

    if data.startswith(
        "like_"
    ):

        try:

            track_id = int(
                data[
                    len("like_"):
                ]
            )

        except ValueError:

            return

        set_like(
            user_id,
            track_id,
            True,
        )

        answer_callback(
            callback_id,
            "❤️ Added to your taste",
        )

        edit_reply_markup(
            chat_id,
            int(
                message.get("message_id")
            ),
            {
                "inline_keyboard": [
                    [
                        {
                            "text":
                                "❤️ LIKED",
                            "callback_data":
                                "noop",
                        },
                    ],
                    [
                        {
                            "text":
                                "▶️  NEXT TRACK",
                            "callback_data":
                                "next_music",
                        },
                    ],
                    [
                        {
                            "text":
                                "🎛  CHANGE MOOD",
                            "callback_data":
                                "change_mood",
                        },
                        {
                            "text":
                                "📻  MY RADIO",
                            "callback_data":
                                "radio_start",
                        },
                    ],
                ]
            },
        )

        return

    # ========================================================
    # UNLIKE SPECIFIC TRACK
    # ========================================================

    if data.startswith(
        "unlike_"
    ):

        try:

            track_id = int(
                data[
                    len("unlike_"):
                ]
            )

        except ValueError:

            return

        set_like(
            user_id,
            track_id,
            False,
        )

        answer_callback(
            callback_id,
            "😴 Removed from your taste",
        )

        return

    # ========================================================
    # LAST LIKE
    # ========================================================

    if data == "like_last":

        track_id = get_last_delivered(
            user_id
        )

        if track_id:

            set_like(
                user_id,
                track_id,
                True,
            )

            answer_callback(
                callback_id,
                "❤️ Added to your taste",
            )

        return

    # ========================================================
    # LAST UNLIKE
    # ========================================================

    if data == "unlike_last":

        track_id = get_last_delivered(
            user_id
        )

        if track_id:

            set_like(
                user_id,
                track_id,
                False,
            )

            answer_callback(
                callback_id,
                "😴 Removed from your taste",
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
            "📻 MY RADIO is ON",
        )

        send_message(
            chat_id,
            (
                "📻  MY RADIO\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "✨ Personal recommendations\n\n"
                "AI will learn from the tracks "
                "you ❤️ like.\n\n"
                "🎧 Finding your next track..."
            ),
        )

        if not schedule_radio(
            chat_id,
            user_id,
        ):

            send_message(
                chat_id,
                "⏳ Radio is already preparing a track.",
                radio_buttons(),
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
            "📻 Radio stopped",
        )

        send_message(
            chat_id,
            (
                "⏹  RADIO OFF\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Your personal Radio is paused."
            ),
            mood_menu(),
        )

        return

    # ========================================================
    # NEXT
    # ========================================================

    if data == "next_music":

        if is_radio_active(
            user_id
        ):

            answer_callback(
                callback_id,
                "📻 Finding your next track...",
            )

            if not schedule_radio(
                chat_id,
                user_id,
            ):

                answer_callback(
                    callback_id,
                    "⏳ Already preparing...",
                )

            return

        mood = get_user_mood(
            user_id
        )

        if not mood:

            answer_callback(
                callback_id,
                "Choose a mood first",
            )

            send_message(
                chat_id,
                "🎛 Choose your mood 👇",
                mood_menu(),
            )

            return

        answer_callback(
            callback_id,
            "🎧 Finding next track...",
        )

        if not schedule_music(
            chat_id,
            user_id,
            mood,
        ):

            answer_callback(
                callback_id,
                "⏳ Already preparing...",
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
            "🎛 Choose your mood",
        )

        send_message(
            chat_id,
            (
                "🎛  MOOD SELECTOR\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "What are you feeling right now?"
            ),
            mood_menu(),
        )

        return

    # ========================================================
    # NOOP
    # ========================================================

    if data == "noop":

        answer_callback(
            callback_id
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
                "💎  NOT YOUR VIBE MUSIC\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Welcome to your personal "
                "music experience. 🎧\n\n"
                "Choose a mood and discover "
                "your next track.\n\n"
                "❤️ Like tracks you love.\n"
                "😴 Skip tracks you don't like.\n\n"
                "📻 MY RADIO learns from your likes.\n\n"
                "👇 SELECT YOUR MOOD"
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
                "🎛  MOOD SELECTOR\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "What are you feeling right now?"
            ),
            mood_menu(),
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
                "📻  MY RADIO\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "✨ Personal Radio is ON.\n\n"
                "AI will recommend tracks "
                "based on your ❤️ likes."
            ),
            radio_buttons(),
        )

        if not schedule_radio(
            chat_id,
            user_id,
        ):

            send_message(
                chat_id,
                "⏳ Radio is already preparing...",
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
                "🎛 အရင်ဆုံး Mood ရွေးပါ 👇",
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
                "⏹  RADIO OFF\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Personal Radio stopped."
            ),
            mood_menu(),
        )

        return

    # ========================================================
    # STATS
    # ========================================================

    if command == "/stats":

        if isinstance(
            user_id,
            int,
        ):

            send_stats(
                chat_id,
                user_id,
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
                    f"Total users: "
                    f"{get_user_count()}"
                ),
            )

        else:

            send_message(
                chat_id,
                "❌ Admin only.",
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
                "📡 TELETHON STATUS\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"Telethon: "
                f"{'🟢 ONLINE' if telethon_ready.is_set() else '🔴 OFFLINE'}\n"
                f"AI: "
                f"{'🟢 ONLINE' if openai_client else '🔴 OFFLINE'}\n"
                f"DB: "
                f"{'🟢 ONLINE' if db_pool else '🔴 OFFLINE'}\n\n"
                "Realtime watcher: "
                f"{'ON' if telethon_ready.is_set() else 'OFF'}"
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
                "💎  NOT YOUR VIBE MUSIC\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "/start\n"
                "/mood\n"
                "/next\n"
                "/radio\n"
                "/stopradio\n\n"
                "❤️ = Like\n"
                "😴 = Unlike\n\n"
                "👑 ADMIN\n"
                "/stats\n"
                "/users\n"
                "/telegram"
            ),
        )

        return


# ============================================================
# UPDATE
# ============================================================

def handle_update(
    update: Mapping[str, Any],
) -> None:

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
# FLASK
# ============================================================

@app.route("/")
def home():

    return (
        "💎 NOT YOUR VIBE MUSIC BOT ONLINE",
        200,
    )


@app.route("/health")
def health():

    db_ok = False

    try:

        if db_pool:

            with (
                db_connection() as connection,
                db_cursor(connection) as cursor
            ):

                cursor.execute(
                    "SELECT 1"
                )

                db_ok = True

    except Exception:

        db_ok = False

    if db_ok:

        return "OK", 200

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
# WEBHOOK SETUP
# ============================================================

def setup_webhook() -> None:

    if (
        not BOT_TOKEN
        or not RENDER_EXTERNAL_URL
    ):

        logger.warning(
            (
                "Webhook not configured. "
                "BOT_TOKEN or RENDER_EXTERNAL_URL missing."
            )
        )

        return

    url = (
        f"{RENDER_EXTERNAL_URL.rstrip('/')}"
        "/webhook"
    )

    payload = {

        "url":
            url,

        "allowed_updates": [
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
        timeout=20,
    )

    if result.get("ok"):

        logger.info(
            "🟢 WEBHOOK = %s",
            url,
        )

    else:

        logger.error(
            "❌ Webhook setup failed: %s",
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
    # DATABASE
    # --------------------------------------------------------

    try:

        init_db()

    except Exception:

        logger.exception(
            "❌ PostgreSQL initialization failed"
        )

        return False

    # --------------------------------------------------------
    # AI
    # --------------------------------------------------------

    initialize_openai()

    # --------------------------------------------------------
    # WEBHOOK
    # --------------------------------------------------------

    setup_webhook()

    # --------------------------------------------------------
    # TELETHON
    # --------------------------------------------------------

    start_telethon()

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
