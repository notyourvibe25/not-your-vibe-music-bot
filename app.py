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

BOT_TOKEN = env_text("BOT_TOKEN")

ADMIN_USER_ID = env_text("ADMIN_USER_ID")

DATABASE_URL = env_text("DATABASE_URL")

RENDER_EXTERNAL_URL = env_text("RENDER_EXTERNAL_URL")

if not RENDER_EXTERNAL_URL:
    hostname = env_text("RENDER_EXTERNAL_HOSTNAME")

    if hostname:
        RENDER_EXTERNAL_URL = f"https://{hostname}"


WEBHOOK_SECRET = env_text(
    "TELEGRAM_WEBHOOK_SECRET"
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
    12,
)

DB_POOL_MAX_CONNECTIONS = env_int(
    "DB_POOL_MAX_CONNECTIONS",
    8,
    2,
    30,
)

TRACK_CANDIDATE_LIMIT = env_int(
    "TRACK_CANDIDATE_LIMIT",
    250,
    20,
    2000,
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
# RADIO SETTINGS
# ============================================================

# Like mood weight
LIKE_WEIGHT = 10.0

# Unlike mood weight
DISLIKE_WEIGHT = -20.0

# Selected mood bonus
CURRENT_MOOD_WEIGHT = 4.0

# Exploration bonus
EXPLORATION_WEIGHT = 1.5

# Liked track chance inside a preferred mood
LIKED_TRACK_CHANCE = 0.78

# Radio history window
RADIO_HISTORY_LIMIT = env_int(
    "RADIO_HISTORY_LIMIT",
    100,
    10,
    1000,
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


MOOD_INFO = {

    "sad": {
        "name": "😢 SAD",
        "description": (
            "Stay with the feeling.\n"
            "Let the music say what words can't."
        ),
    },

    "love": {
        "name": "❤️ LOVE",
        "description": (
            "For the moments that make your "
            "heart beat a little faster."
        ),
    },

    "chill": {
        "name": "🌙 CHILL",
        "description": (
            "Slow down, breathe in,\n"
            "and let the world fade away."
        ),
    },

    "hype": {
        "name": "🔥 HYPE",
        "description": (
            "Turn it up.\n"
            "Your energy starts here."
        ),
    },

    "dark": {
        "name": "🖤 DARK",
        "description": (
            "Enter the darker side.\n"
            "Heavy bass. Brutal drops. No mercy."
        ),
    },

    "energetic": {
        "name": "⚡ ENERGETIC",
        "description": (
            "No limits. No brakes.\n"
            "Just pure energy."
        ),
    },

    "night": {
        "name": "🚗 NIGHT DRIVE",
        "description": (
            "Lights outside.\n"
            "Music inside. Keep moving."
        ),
    },

    "melodic": {
        "name": "🌌 MELODIC",
        "description": (
            "Close your eyes and let the melody "
            "take you somewhere else."
        ),
    },
}


# ============================================================
# 8 MOOD CHANNELS
# ============================================================

MOOD_CHANNELS = {

    "sad":
        env_text("SAD_CHANNEL"),

    "love":
        env_text("LOVE_CHANNEL"),

    "chill":
        env_text("CHILL_CHANNEL"),

    "hype":
        env_text(
            "HYPE_CHANNEL",
            "-1004427220481",
        ),

    "dark":
        env_text("DARK_CHANNEL"),

    "energetic":
        env_text("ENERGETIC_CHANNEL"),

    "night":
        env_text("NIGHT_CHANNEL"),

    "melodic":
        env_text(
            "MELODIC_CHANNEL",
            "-1004446996297",
        ),
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

CHANNEL_MOOD_MAP: dict[str, str] = {}

last_scan_time = 0

last_scan_counts: dict[str, int] = {
    mood: 0 for mood in MOODS
}


# ============================================================
# DATABASE URL
# ============================================================

def normalize_database_url(
    url: str,
) -> str:

    if url.startswith("postgres://"):
        return "postgresql://" + url[11:]

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

        if db_pool is None:

            logger.info(
                "Connecting to PostgreSQL..."
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
                "🟢 PostgreSQL pool created"
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

            logger.warning(
                "Dead PostgreSQL connection detected"
            )

            db_pool.putconn(
                connection,
                close=True,
            )

            connection = db_pool.getconn()

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
        created_at BIGINT NOT NULL,

        UNIQUE(channel_id, message_id)
    );


    CREATE TABLE IF NOT EXISTS user_history (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        mood TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        message_id BIGINT NOT NULL,
        action TEXT NOT NULL DEFAULT 'served',
        sent_at BIGINT NOT NULL
    );


    CREATE TABLE IF NOT EXISTS user_state (
        user_id BIGINT PRIMARY KEY,
        mood TEXT,
        radio_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        updated_at BIGINT NOT NULL
    );


    CREATE TABLE IF NOT EXISTS track_feedback (
        id BIGSERIAL PRIMARY KEY,

        user_id BIGINT NOT NULL,
        channel_id TEXT NOT NULL,
        message_id BIGINT NOT NULL,
        mood TEXT NOT NULL,

        feedback TEXT NOT NULL,

        created_at BIGINT NOT NULL,

        UNIQUE(
            user_id,
            channel_id,
            message_id
        )
    );


    CREATE TABLE IF NOT EXISTS processed_updates (
        update_id BIGINT PRIMARY KEY,
        processed_at BIGINT NOT NULL
    );


    CREATE INDEX IF NOT EXISTS
    idx_tracks_mood
    ON tracks(mood);


    CREATE INDEX IF NOT EXISTS
    idx_history_user
    ON user_history(
        user_id,
        sent_at DESC
    );


    CREATE INDEX IF NOT EXISTS
    idx_feedback_user
    ON track_feedback(
        user_id,
        created_at DESC
    );


    CREATE INDEX IF NOT EXISTS
    idx_feedback_track
    ON track_feedback(
        channel_id,
        message_id
    );

    """

    with (
        db_connection() as connection,
        db_cursor(connection) as cursor
    ):

        cursor.execute(schema)

        cursor.execute(
            """
            ALTER TABLE user_history
            ADD COLUMN IF NOT EXISTS action
            TEXT NOT NULL DEFAULT 'served'
            """
        )

        cursor.execute(
            """
            ALTER TABLE user_state
            ADD COLUMN IF NOT EXISTS radio_enabled
            BOOLEAN NOT NULL DEFAULT FALSE
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
        "🟢 PostgreSQL database ready"
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
                VALUES (%s, %s)

                ON CONFLICT (update_id)
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
            "Could not claim update"
        )

        return True


# ============================================================
# USERS
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
            "Could not register user %s",
            user_id,
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

        logger.exception(
            "Could not count users"
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

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                INSERT INTO user_state(
                    user_id,
                    mood,
                    radio_enabled,
                    updated_at
                )

                VALUES(
                    %s,
                    %s,
                    FALSE,
                    %s
                )

                ON CONFLICT(user_id)
                DO UPDATE SET

                    mood =
                        EXCLUDED.mood,

                    radio_enabled =
                        FALSE,

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
            "Could not read user mood"
        )

    return None


def set_radio_mode(
    user_id: int,
    enabled: bool,
) -> None:

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
                    radio_enabled,
                    updated_at
                )

                VALUES(
                    %s,
                    NULL,
                    %s,
                    %s
                )

                ON CONFLICT(user_id)
                DO UPDATE SET

                    radio_enabled =
                        EXCLUDED.radio_enabled,

                    updated_at =
                        EXCLUDED.updated_at
                """,
                (
                    user_id,
                    enabled,
                    int(time.time()),
                ),
            )

    except Exception:

        logger.exception(
            "Could not change radio mode"
        )


# ============================================================
# TRACKS
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
                DO UPDATE SET
                    mood = EXCLUDED.mood

                RETURNING id
                """,
                (
                    mood,
                    str(channel_id),
                    int(message_id),
                    int(time.time()),
                ),
            )

            inserted = (
                cursor.fetchone()
                is not None
            )

            if inserted:

                logger.info(
                    "🎵 TRACK | %s | %s | %s",
                    mood.upper(),
                    channel_id,
                    message_id,
                )

            return inserted

    except Exception:

        logger.exception(
            "Could not save track"
        )

        return False


def get_track_counts() -> dict[str, int]:

    counts = {
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

                if mood in counts:

                    counts[mood] = int(
                        row["count"]
                    )

    except Exception:

        logger.exception(
            "Could not get track counts"
        )

    return counts


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

        logger.exception(
            "Could not count tracks"
        )

        return 0


# ============================================================
# FEEDBACK
# ============================================================

def save_feedback(
    user_id: int,
    channel_id: str,
    message_id: int,
    mood: str,
    feedback: str,
) -> bool:

    if feedback not in {
        "like",
        "not_for_me",
    }:
        return False

    if mood not in MOODS:
        return False

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                INSERT INTO track_feedback(
                    user_id,
                    channel_id,
                    message_id,
                    mood,
                    feedback,
                    created_at
                )

                VALUES(
                    %s,%s,%s,%s,%s,%s
                )

                ON CONFLICT(
                    user_id,
                    channel_id,
                    message_id
                )

                DO UPDATE SET

                    mood =
                        EXCLUDED.mood,

                    feedback =
                        EXCLUDED.feedback,

                    created_at =
                        EXCLUDED.created_at
                """,
                (
                    user_id,
                    str(channel_id),
                    int(message_id),
                    mood,
                    feedback,
                    int(time.time()),
                ),
            )

        return True

    except Exception:

        logger.exception(
            "Could not save feedback"
        )

        return False


def get_feedback(
    user_id: int,
    channel_id: str,
    message_id: int,
) -> Optional[str]:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT feedback
                FROM track_feedback

                WHERE user_id=%s
                AND channel_id=%s
                AND message_id=%s
                """,
                (
                    user_id,
                    str(channel_id),
                    int(message_id),
                ),
            )

            row = cursor.fetchone()

            if row:
                return row["feedback"]

    except Exception:

        logger.exception(
            "Could not get feedback"
        )

    return None


def clear_feedback(
    user_id: int,
    channel_id: str,
    message_id: int,
) -> bool:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                DELETE FROM track_feedback

                WHERE user_id=%s
                AND channel_id=%s
                AND message_id=%s
                """,
                (
                    user_id,
                    str(channel_id),
                    int(message_id),
                ),
            )

        return True

    except Exception:

        logger.exception(
            "Could not clear feedback"
        )

        return False


def get_feedback_map(
    user_id: int,
) -> dict[tuple[str, int], str]:

    result: dict[
        tuple[str, int],
        str
    ] = {}

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT
                    channel_id,
                    message_id,
                    feedback
                FROM track_feedback
                WHERE user_id=%s
                """,
                (user_id,),
            )

            for row in cursor.fetchall():

                result[
                    (
                        str(row["channel_id"]),
                        int(row["message_id"]),
                    )
                ] = row["feedback"]

    except Exception:

        logger.exception(
            "Could not get feedback map"
        )

    return result


# ============================================================
# USER HISTORY
# ============================================================

def save_history(
    user_id: int,
    mood: str,
    channel_id: str,
    message_id: int,
    action: str = "served",
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
                    action,
                    sent_at
                )

                VALUES(
                    %s,%s,%s,%s,%s,%s
                )
                """,
                (
                    user_id,
                    mood,
                    str(channel_id),
                    int(message_id),
                    action,
                    int(time.time()),
                ),
            )

    except Exception:

        logger.exception(
            "Could not save history"
        )


def get_served_history(
    user_id: int,
) -> set[tuple[str, int]]:

    result: set[
        tuple[str, int]
    ] = set()

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT
                    channel_id,
                    message_id

                FROM user_history

                WHERE user_id=%s
                AND action='served'

                ORDER BY sent_at DESC, id DESC

                LIMIT %s
                """,
                (
                    user_id,
                    RADIO_HISTORY_LIMIT,
                ),
            )

            for row in cursor.fetchall():

                result.add(
                    (
                        str(row["channel_id"]),
                        int(row["message_id"]),
                    )
                )

    except Exception:

        logger.exception(
            "Could not get history"
        )

    return result


# ============================================================
# TRACK CANDIDATES
# ============================================================

def get_tracks_for_mood(
    mood: str,
) -> list[tuple[int, str]]:

    if mood not in MOODS:
        return []

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT
                    message_id,
                    channel_id

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

            return [
                (
                    int(row["message_id"]),
                    str(row["channel_id"]),
                )
                for row in cursor.fetchall()
            ]

    except Exception:

        logger.exception(
            "Could not get candidate tracks"
        )

        return []


# ============================================================
# MOOD PREFERENCE
# ============================================================

def get_mood_preferences(
    user_id: int,
) -> dict[str, dict[str, float]]:

    result = {
        mood: {
            "like": 0.0,
            "not_for_me": 0.0,
        }
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
                    feedback,
                    COUNT(*) AS count

                FROM track_feedback

                WHERE user_id=%s

                GROUP BY
                    mood,
                    feedback
                """,
                (user_id,),
            )

            for row in cursor.fetchall():

                mood = row["mood"]
                feedback = row["feedback"]

                if (
                    mood in result
                    and feedback in result[mood]
                ):

                    result[mood][feedback] = float(
                        row["count"]
                    )

    except Exception:

        logger.exception(
            "Could not calculate preferences"
        )

    return result


# ============================================================
# RADIO MOOD SCORE
# ============================================================

def radio_mood_score(
    mood: str,
    current_mood: str,
    preferences: dict[str, dict[str, float]],
) -> float:

    p = preferences[mood]

    likes = p["like"]

    dislikes = p["not_for_me"]

    score = 1.0

    # User's selected mood gets a small bonus.
    if mood == current_mood:
        score += CURRENT_MOOD_WEIGHT

    # Likes are the main signal.
    score += likes * LIKE_WEIGHT

    # Unlike is a strong negative signal.
    score += dislikes * DISLIKE_WEIGHT

    # Every available mood gets some exploration.
    if likes == 0:
        score += EXPLORATION_WEIGHT

    return max(
        0.2,
        score,
    )


# ============================================================
# RADIO MOOD
# ============================================================

def choose_radio_mood(
    user_id: int,
    current_mood: str,
) -> Optional[str]:

    counts = get_track_counts()

    available = [
        mood
        for mood in MOODS
        if counts.get(mood, 0) > 0
    ]

    if not available:
        return None

    preferences = get_mood_preferences(
        user_id
    )

    weights = [
        radio_mood_score(
            mood,
            current_mood,
            preferences,
        )
        for mood in available
    ]

    selected = random.choices(
        available,
        weights=weights,
        k=1,
    )[0]

    logger.info(
        "📻 RADIO MOOD | user=%s | current=%s | selected=%s | weights=%s",
        user_id,
        current_mood,
        selected,
        dict(
            zip(
                available,
                weights,
            )
        ),
    )

    return selected


# ============================================================
# RADIO TRACK
# ============================================================

def choose_radio_track(
    user_id: int,
    current_mood: str,
) -> Optional[
    tuple[str, int, str]
]:

    radio_mood = choose_radio_mood(
        user_id,
        current_mood,
    )

    if not radio_mood:
        return None

    candidates = get_tracks_for_mood(
        radio_mood
    )

    if not candidates:
        return None

    feedback = get_feedback_map(
        user_id
    )

    history = get_served_history(
        user_id
    )

    # Never recommend tracks marked Not For Me.
    allowed = [
        track
        for track in candidates
        if feedback.get(
            (
                track[1],
                track[0],
            )
        ) != "not_for_me"
    ]

    if not allowed:
        return None

    liked = [
        track
        for track in allowed
        if feedback.get(
            (
                track[1],
                track[0],
            )
        ) == "like"
    ]

    unseen = [
        track
        for track in allowed
        if (
            track[1],
            track[0],
        ) not in history
    ]

    # --------------------------------------------------------
    # PRIORITY 1
    # User liked a track in this mood.
    # Radio can replay liked tracks.
    # --------------------------------------------------------

    if liked and random.random() < LIKED_TRACK_CHANCE:

        message_id, channel_id = random.choice(
            liked
        )

        return (
            radio_mood,
            message_id,
            channel_id,
        )

    # --------------------------------------------------------
    # PRIORITY 2
    # New / unseen track.
    # --------------------------------------------------------

    if unseen:

        message_id, channel_id = random.choice(
            unseen
        )

        return (
            radio_mood,
            message_id,
            channel_id,
        )

    # --------------------------------------------------------
    # PRIORITY 3
    # Everything allowed.
    # --------------------------------------------------------

    message_id, channel_id = random.choice(
        allowed
    )

    return (
        radio_mood,
        message_id,
        channel_id,
    )


# ============================================================
# NORMAL NEXT
# ============================================================

def choose_next_track(
    user_id: int,
    mood: str,
) -> Optional[
    tuple[str, int, str]
]:

    candidates = get_tracks_for_mood(
        mood
    )

    if not candidates:
        return None

    feedback = get_feedback_map(
        user_id
    )

    history = get_served_history(
        user_id
    )

    allowed = [
        track
        for track in candidates

        if feedback.get(
            (
                track[1],
                track[0],
            )
        ) != "not_for_me"
    ]

    if not allowed:
        return None

    # NEXT strongly prefers unseen tracks.
    unseen = [
        track
        for track in allowed

        if (
            track[1],
            track[0],
        ) not in history
    ]

    pool = unseen or allowed

    message_id, channel_id = random.choice(
        pool
    )

    return (
        mood,
        message_id,
        channel_id,
    )


# ============================================================
# RESERVATION
# ============================================================

def reserve_track(
    user_id: int,
    choice: Optional[
        tuple[str, int, str]
    ],
) -> Optional[
    tuple[str, int, str]
]:

    if not choice:
        return None

    mood, message_id, channel_id = choice

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            # Prevent two simultaneous requests
            # from selecting the same user state.
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (user_id,),
            )

            cursor.execute(
                """
                INSERT INTO user_history(
                    user_id,
                    mood,
                    channel_id,
                    message_id,
                    action,
                    sent_at
                )

                VALUES(
                    %s,%s,%s,%s,'served',%s
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

        return choice

    except Exception:

        logger.exception(
            "Could not reserve track"
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

            result = {
                "ok": False,
                "description":
                    (
                        "Non-JSON response "
                        f"HTTP {response.status_code}"
                    ),
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

    data: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }

    if keyboard is not None:
        data["reply_markup"] = keyboard

    return telegram(
        "sendMessage",
        data,
        timeout=15,
    )


def edit_message_keyboard(
    chat_id: int,
    message_id: int,
    keyboard: dict[str, Any],
) -> None:

    telegram(
        "editMessageReplyMarkup",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": keyboard,
        },
        timeout=15,
    )


def answer_callback(
    callback_id: Any,
    text: str = "",
) -> None:

    telegram(
        "answerCallbackQuery",
        {
            "callback_query_id":
                callback_id,
            "text":
                text,
        },
        timeout=8,
    )


def copy_music(
    chat_id: int,
    channel_id: str,
    message_id: int,
) -> dict[str, Any]:

    return telegram(
        "copyMessage",
        {
            "chat_id": chat_id,
            "from_chat_id": channel_id,
            "message_id": message_id,
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
                    "text": "😢 SAD",
                    "callback_data":
                        "mood_sad",
                },
                {
                    "text": "❤️ LOVE",
                    "callback_data":
                        "mood_love",
                },
            ],

            [
                {
                    "text": "🌙 CHILL",
                    "callback_data":
                        "mood_chill",
                },
                {
                    "text": "🔥 HYPE",
                    "callback_data":
                        "mood_hype",
                },
            ],

            [
                {
                    "text": "🖤 DARK",
                    "callback_data":
                        "mood_dark",
                },
                {
                    "text": "⚡ ENERGETIC",
                    "callback_data":
                        "mood_energetic",
                },
            ],

            [
                {
                    "text": "🚗 NIGHT DRIVE",
                    "callback_data":
                        "mood_night",
                },
                {
                    "text": "🌌 MELODIC",
                    "callback_data":
                        "mood_melodic",
                },
            ],

        ]
    }


# ============================================================
# MUSIC BUTTONS
# ============================================================

def music_buttons(
    user_id: int,
    channel_id: str,
    message_id: int,
    mood: str,
) -> dict[str, Any]:

    feedback = get_feedback(
        user_id,
        channel_id,
        message_id,
    )

    like_text = (
        "❤️✓"
        if feedback == "like"
        else "❤️"
    )

    unlike_text = (
        "😴✓"
        if feedback == "not_for_me"
        else "😴"
    )

    return {
        "inline_keyboard": [

            [
                {
                    "text":
                        like_text,

                    "callback_data":
                        (
                            "like:"
                            f"{mood}:"
                            f"{channel_id}:"
                            f"{message_id}"
                        ),
                },

                {
                    "text":
                        unlike_text,

                    "callback_data":
                        (
                            "notme:"
                            f"{mood}:"
                            f"{channel_id}:"
                            f"{message_id}"
                        ),
                },
            ],

            [
                {
                    "text":
                        "⏭ NEXT",

                    "callback_data":
                        "next_music",
                },

                {
                    "text":
                        "📻 RADIO",

                    "callback_data":
                        "radio",
                },
            ],

            [
                {
                    "text":
                        "🎛 CHANGE MOOD",

                    "callback_data":
                        "change_mood",
                },
            ],

        ]
    }


# ============================================================
# MOOD DESCRIPTION
# ============================================================

def mood_description(
    mood: str,
) -> str:

    info = MOOD_INFO[mood]

    return (
        f"{info['name']}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"{info['description']}"
    )


# ============================================================
# SEND MUSIC
# ============================================================

def send_music(
    chat_id: int,
    user_id: int,
    mood: str,
    radio: bool = False,
) -> None:

    if mood not in MOODS:

        send_message(
            chat_id,
            "⚠️ Please choose a valid mood.",
            mood_menu(),
        )

        return


    if get_track_count(mood) <= 0:

        send_message(
            chat_id,

            (
                f"{MOOD_INFO[mood]['name']}\n\n"
                "⚠️ No tracks available yet."
            ),

            mood_menu(),
        )

        return


    if radio:

        choice = choose_radio_track(
            user_id,
            mood,
        )

    else:

        choice = choose_next_track(
            user_id,
            mood,
        )


    reserved = reserve_track(
        user_id,
        choice,
    )


    if not reserved:

        send_message(
            chat_id,

            (
                "⚠️ Couldn't find a track "
                "right now.\n\n"
                "Try again."
            ),

            music_buttons(
                user_id,
                "",
                0,
                mood,
            ),
        )

        return


    selected_mood, message_id, channel_id = reserved


    result = copy_music(
        chat_id,
        channel_id,
        message_id,
    )


    if not result.get("ok"):

        send_message(
            chat_id,

            (
                "⚠️ This track could not "
                "be delivered."
            ),

            music_buttons(
                user_id,
                channel_id,
                message_id,
                selected_mood,
            ),
        )

        return


    if radio:

        header = "📻 YOUR RADIO"

        description = (
            "Personalized from your "
            "❤️ Likes and 😴 Not-for-me choices."
        )

    else:

        header = "🎧 NOW PLAYING"

        description = (
            MOOD_INFO[
                selected_mood
            ]["description"]
        )


    text = (
        f"{header}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"{MOOD_INFO[selected_mood]['name']}\n\n"
        f"{description}\n\n"
        "Enjoy the vibe. ✨"
    )


    send_message(
        chat_id,
        text,

        music_buttons(
            user_id,
            channel_id,
            message_id,
            selected_mood,
        ),
    )


# ============================================================
# MUSIC WORKER
# ============================================================

def music_worker(
    chat_id: int,
    user_id: int,
    mood: str,
    radio: bool,
) -> None:

    try:

        send_music(
            chat_id,
            user_id,
            mood,
            radio,
        )

    except Exception:

        logger.exception(
            "Music worker error"
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
    radio: bool,
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
            radio,
        )

        return True

    except Exception:

        with pending_music_lock:

            pending_music_users.discard(
                user_id
            )

        logger.exception(
            "Could not queue music"
        )

        return False


# ============================================================
# TELETHON CHANNEL HELPERS
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

    value = str(entity_id)

    if value.startswith("-100"):
        return value

    return f"-100{value}"


def normalize_config_channel(
    value: str,
) -> Optional[str]:

    if not value:
        return None

    value = value.strip()

    if value.startswith("-100"):
        return value

    if value.lstrip("-").isdigit():

        number = value.lstrip("-")

        return f"-100{number}"

    return None


def rebuild_channel_map() -> None:

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
# MUSIC MESSAGE DETECTION
# ============================================================

def is_music_message(
    message: Any,
) -> bool:

    if not message:
        return False

    media = getattr(
        message,
        "media",
        None,
    )

    if not media:
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

    mime_type = (
        getattr(
            document,
            "mime_type",
            "",
        )
        or ""
    ).lower()

    if mime_type.startswith(
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

    name = (
        getattr(
            file_obj,
            "name",
            "",
        )
        or ""
    ).lower()

    return name.endswith(
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
# SCAN ONE CHANNEL
# ============================================================

async def scan_one_channel(
    mood: str,
    channel_value: str,
) -> int:

    if (
        not channel_value
        or telethon_client is None
    ):

        logger.warning(
            "⚠️ %s channel not configured",
            mood.upper(),
        )

        return 0

    try:

        if channel_value.lstrip(
            "-"
        ).isdigit():

            lookup: Any = int(
                channel_value
            )

        else:

            lookup = channel_value


        entity = await telethon_client.get_entity(
            lookup
        )


        found = 0


        async for message in (
            telethon_client.iter_messages(
                entity
            )
        ):

            if save_telethon_message(
                mood,
                entity,
                message,
            ):
                found += 1


        logger.info(
            "🔎 %s scan complete | detected=%s",
            mood.upper(),
            found,
        )

        return found

    except Exception:

        logger.exception(
            "%s scan failed",
            mood.upper(),
        )

        return 0


# ============================================================
# SCAN ALL CHANNELS
# ============================================================

async def scan_all_channels() -> None:

    global last_scan_time
    global last_scan_counts

    rebuild_channel_map()

    logger.info(
        "🔎 Starting full channel scan..."
    )

    result = {
        mood: 0
        for mood in MOODS
    }

    for mood in MOODS:

        channel = MOOD_CHANNELS.get(
            mood,
            "",
        )

        if channel:

            result[mood] = await scan_one_channel(
                mood,
                channel,
            )

        await asyncio.sleep(
            0.5
        )

    last_scan_counts = result

    last_scan_time = int(
        time.time()
    )

    logger.info(
        "📊 TRACK COUNTS: %s",
        get_track_counts(),
    )


# ============================================================
# REAL-TIME WATCHER
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

            normalized = normalize_config_channel(
                str(chat_id)
            )

            if not normalized:
                return

            mood = CHANNEL_MOOD_MAP.get(
                normalized
            )

            if not mood:
                return

            message = event.message

            if not is_music_message(
                message
            ):
                return

            message_id = getattr(
                message,
                "id",
                None,
            )

            if not message_id:
                return

            if save_track(
                mood,
                normalized,
                message_id,
            ):

                logger.info(
                    (
                        "🚀 NEW TRACK | "
                        "%s | message=%s"
                    ),
                    mood.upper(),
                    message_id,
                )

        except Exception:

            logger.exception(
                "Real-time watcher error"
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

            if (
                telethon_client is None
                or not telethon_ready.is_set()
            ):
                continue

            await scan_all_channels()

        except asyncio.CancelledError:

            return

        except Exception:

            logger.exception(
                "Periodic scanner error"
            )

            await asyncio.sleep(
                10
            )


# ============================================================
# TELETHON WORKER
# ============================================================

def telethon_worker() -> None:

    global telethon_client

    if not all(
        (
            TELETHON_API_ID,
            TELETHON_API_HASH,
            TELETHON_SESSION,
        )
    ):

        logger.error(
            (
                "❌ Telethon cannot start. "
                "Missing API_ID/API_HASH/SESSION."
            )
        )

        return


    rebuild_channel_map()


    try:

        telethon_client = TelegramClient(

            StringSession(
                TELETHON_SESSION
            ),

            int(
                TELETHON_API_ID
            ),

            TELETHON_API_HASH,

            connection_retries=10,

            retry_delay=5,

            timeout=30,

            auto_reconnect=True,
        )

    except Exception:

        logger.exception(
            "Telethon creation failed"
        )

        return


    register_telethon_events(
        telethon_client
    )


    async def runner() -> None:

        scanner_task = None

        while True:

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
                        "❌ Telethon unauthorized"
                    )

                    telethon_ready.clear()

                    return


                telethon_ready.set()


                logger.info(
                    "🟢 TELETHON CONNECTED"
                )


                await scan_all_channels()


                scanner_task = asyncio.create_task(
                    periodic_scanner()
                )


                logger.info(
                    "👀 Channel watcher ACTIVE"
                )


                await (
                    telethon_client
                    .run_until_disconnected()
                )


            except asyncio.CancelledError:

                raise


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

                    except (
                        asyncio.CancelledError
                    ):

                        pass

                    except Exception:

                        pass

                    scanner_task = None


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

                    logger.exception(
                        "Telethon disconnect error"
                    )


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

    finally:

        telethon_ready.clear()


# ============================================================
# START TELETHON
# ============================================================

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


# ============================================================
# FORMAT UPTIME
# ============================================================

def format_time(
    timestamp: int,
) -> str:

    if not timestamp:
        return "Never"

    try:

        return time.strftime(
            "%Y-%m-%d %H:%M:%S UTC",
            time.gmtime(timestamp),
        )

    except Exception:

        return "Unknown"


# ============================================================
# ADMIN STATS
# ============================================================

def send_stats(
    chat_id: int,
    requester_id: int,
) -> None:

    if not is_admin(
        requester_id
    ):

        send_message(
            chat_id,
            "❌ Admin only.",
        )

        return


    counts = get_track_counts()

    total_tracks = sum(
        counts.values()
    )


    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM track_feedback
                WHERE feedback='like'
                """
            )

            like_row = cursor.fetchone()

            total_likes = int(
                like_row["count"]
            ) if like_row else 0


            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM track_feedback
                WHERE feedback='not_for_me'
                """
            )

            dislike_row = cursor.fetchone()

            total_dislikes = int(
                dislike_row["count"]
            ) if dislike_row else 0


            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM user_history
                WHERE action='served'
                """
            )

            served_row = cursor.fetchone()

            total_served = int(
                served_row["count"]
            ) if served_row else 0

    except Exception:

        logger.exception(
            "Could not collect statistics"
        )

        total_likes = 0
        total_dislikes = 0
        total_served = 0


    lines = [

        "📊 NOT YOUR VIBE",
        "━━━━━━━━━━━━━━━━━━",

        "",

        f"👥 Users: {get_users_count()}",

        f"🎵 Tracks: {total_tracks}",

        f"▶️ Served: {total_served}",

        f"❤️ Likes: {total_likes}",

        f"😴 Not for me: {total_dislikes}",

        "",
    ]


    for mood in MOODS:

        lines.append(
            (
                f"{MOOD_INFO[mood]['name']} "
                f"→ {counts[mood]}"
            )
        )


    lines.extend(
        [

            "",

            (
                "🟢 PostgreSQL: ONLINE"
                if db_pool is not None
                else
                "🔴 PostgreSQL: OFFLINE"
            ),

            (
                "🟢 Telethon: CONNECTED"
                if telethon_ready.is_set()
                else
                "🔴 Telethon: DISCONNECTED"
            ),

            (
                "🟢 Channel Watcher: ACTIVE"
                if telethon_ready.is_set()
                else
                "🔴 Channel Watcher: STOPPED"
            ),

            "",

            f"🔎 Last scan: "
            f"{format_time(last_scan_time)}",

            "",

            "🤖 AI: DISABLED",

            "📻 Radio: LIKE-BASED",

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
        .split(
            maxsplit=1
        )[0]
        .lower()
        .split(
            "@",
            1
        )[0]
    )


# ============================================================
# FEEDBACK CALLBACK PARSER
# ============================================================

def parse_feedback_callback(
    data: str,
) -> Optional[
    tuple[str, str, str, int]
]:

    parts = data.split(
        ":",
        3,
    )

    if len(parts) != 4:
        return None

    action = parts[0]

    mood = parts[1]

    channel_id = parts[2]

    try:

        message_id = int(
            parts[3]
        )

    except ValueError:

        return None


    if action not in {
        "like",
        "notme",
    }:

        return None


    if mood not in MOODS:
        return None


    return (
        action,
        mood,
        channel_id,
        message_id,
    )


# ============================================================
# CALLBACK HANDLER
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

    chat_id = chat.get(
        "id"
    )

    user_id = user.get(
        "id"
    )

    telegram_message_id = message.get(
        "message_id"
    )


    if not isinstance(
        chat_id,
        int,
    ):
        return

    if not isinstance(
        user_id,
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

        mood = data[5:]


        if mood not in MOODS:

            answer_callback(
                callback_id,
                "Invalid mood",
            )

            return


        if not set_user_mood(
            user_id,
            mood,
        ):

            answer_callback(
                callback_id,
                "Could not save mood",
            )

            return


        answer_callback(
            callback_id,
            f"{MOOD_INFO[mood]['name']} ✓",
        )


        schedule_music(
            chat_id,
            user_id,
            mood,
            False,
        )

        return


    # ========================================================
    # LIKE / NOT FOR ME
    # ========================================================

    feedback_data = parse_feedback_callback(
        data
    )


    if feedback_data:

        (
            action,
            mood,
            channel_id,
            message_id,
        ) = feedback_data


        new_feedback = (
            "like"
            if action == "like"
            else "not_for_me"
        )


        current = get_feedback(
            user_id,
            channel_id,
            message_id,
        )


        # Same button = clear feedback.
        if current == new_feedback:

            clear_feedback(
                user_id,
                channel_id,
                message_id,
            )

            answer_callback(
                callback_id,
                "Feedback cleared",
            )

        else:

            if save_feedback(
                user_id,
                channel_id,
                message_id,
                mood,
                new_feedback,
            ):

                if new_feedback == "like":

                    answer_callback(
                        callback_id,
                        "❤️ Added to your taste",
                    )

                else:

                    answer_callback(
                        callback_id,
                        "😴 Radio will avoid this",
                    )

            else:

                answer_callback(
                    callback_id,
                    "Could not save feedback",
                )


        # Refresh the buttons so
        # ❤️✓ / 😴✓ changes immediately.
        if (
            isinstance(
                telegram_message_id,
                int,
            )
        ):

            edit_message_keyboard(
                chat_id,
                telegram_message_id,

                music_buttons(
                    user_id,
                    channel_id,
                    message_id,
                    mood,
                ),
            )

        return


    # ========================================================
    # NEXT
    # ========================================================

    if data == "next_music":

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
                "🎧 Choose your mood 👇",
                mood_menu(),
            )

            return


        if schedule_music(
            chat_id,
            user_id,
            mood,
            False,
        ):

            answer_callback(
                callback_id,
                "⏭ Finding next track...",
            )

        else:

            answer_callback(
                callback_id,
                "⏳ Preparing...",
            )

        return


    # ========================================================
    # RADIO
    # ========================================================

    if data == "radio":

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
                "📻 Choose your starting mood 👇",
                mood_menu(),
            )

            return


        set_radio_mode(
            user_id,
            True,
        )


        if schedule_music(
            chat_id,
            user_id,
            mood,
            True,
        ):

            answer_callback(
                callback_id,
                "📻 Personalized Radio...",
            )

        else:

            answer_callback(
                callback_id,
                "⏳ Radio is preparing...",
            )

        return


    # ========================================================
    # CHANGE MOOD
    # ========================================================

    if data == "change_mood":

        answer_callback(
            callback_id,
            "🎛 Choose your mood",
        )

        send_message(
            chat_id,

            (
                "🎛 MOOD SELECTOR\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "What are you feeling right now?"
            ),

            mood_menu(),
        )

        return


    answer_callback(
        callback_id
    )


# ============================================================
# MESSAGE HANDLER
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
                "🎧 NOT YOUR VIBE\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Your music.\n"
                "Your mood.\n"
                "Your radio.\n\n"
                "❤️ Like what you love.\n"
                "😴 Mark what you don't want.\n\n"
                "📻 Radio will learn from "
                "your choices.\n\n"
                "Choose a mood 👇"
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
                "What are you feeling right now?"
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


        mood = get_user_mood(
            user_id
        )


        if not mood:

            send_message(
                chat_id,
                "🎧 Choose your mood first 👇",
                mood_menu(),
            )

            return


        schedule_music(
            chat_id,
            user_id,
            mood,
            False,
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


        mood = get_user_mood(
            user_id
        )


        if not mood:

            send_message(
                chat_id,
                "📻 Choose your starting mood 👇",
                mood_menu(),
            )

            return


        set_radio_mode(
            user_id,
            True,
        )


        schedule_music(
            chat_id,
            user_id,
            mood,
            True,
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
                    "👥 USER STATISTICS\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    f"Total users: "
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


        if telethon_ready.is_set():

            send_message(

                chat_id,

                (
                    "🟢 TELETHON CONNECTED\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    "📡 Channel watcher: ACTIVE\n"
                    "🔄 Auto reconnect: ON\n"
                    f"⏰ Backup scan: every "
                    f"{AUTO_SCAN_INTERVAL // 60} minutes\n\n"
                    f"🔎 Last scan:\n"
                    f"{format_time(last_scan_time)}"
                ),
            )

        else:

            send_message(

                chat_id,

                (
                    "🔴 TELETHON DISCONNECTED\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    "🔄 Reconnect loop: ON\n"
                    "📡 Channel watcher: STOPPED\n\n"
                    "Check Render logs."
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
                "🎧 NOT YOUR VIBE MUSIC\n"
                "━━━━━━━━━━━━━━━━━━\n\n"

                "/start → Start\n"
                "/mood → Choose mood\n"
                "/next → Next track\n"
                "/radio → Personalized Radio\n"
                "/telegram → Telethon status\n"
                "/stats → Statistics (Admin)\n"
                "/users → Users (Admin)\n"
                "/help → Help\n\n"

                "❤️ Like → Tell Radio what you love\n"
                "😴 → Tell Radio what to avoid\n\n"

                "📻 Radio uses your Likes.\n"
                "No AI required."
            ),
        )

        return


# ============================================================
# UPDATE
# ============================================================

def handle_update(
    update: Mapping[str, Any],
) -> None:

    if not claim_update(
        update.get(
            "update_id"
        )
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
# FLASK
# ============================================================

@app.route("/")
def home() -> str:

    return (
        "🎧 NOT YOUR VIBE MUSIC BOT ONLINE"
    )


@app.route("/health")
def health():

    db_ok = False

    try:

        if db_pool is not None:

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

        return (
            "OK",
            200,
        )


    return (
        "Database not ready",
        503,
    )


# ============================================================
# STATUS JSON
# ============================================================

@app.route("/status")
def status():

    db_ok = False

    try:

        if db_pool is not None:

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


    return {

        "bot": "online",

        "ai": False,

        "database":
            "online"
            if db_ok
            else
            "offline",

        "telethon":
            "connected"
            if telethon_ready.is_set()
            else
            "disconnected",

        "channel_watcher":
            "active"
            if telethon_ready.is_set()
            else
            "stopped",

        "tracks":
            get_track_counts(),

        "last_scan":
            last_scan_time,

    }


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
            "Webhook processing error"
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


    payload: dict[str, Any] = {

        "url":
            (
                RENDER_EXTERNAL_URL.rstrip("/")
                + "/webhook"
            ),

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
        timeout=20,
    )


    if result.get("ok"):

        logger.info(
            "🟢 Telegram webhook configured"
        )

    else:

        logger.error(
            "🔴 Webhook setup failed: %s",
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
        "🎧 NOT YOUR VIBE MUSIC BOT v5"
    )

    logger.info(
        "📻 LIKE-BASED RADIO"
    )

    logger.info(
        "🤖 AI DISABLED"
    )

    logger.info(
        "========================================"
    )


    if not BOT_TOKEN:

        logger.error(
            "❌ BOT_TOKEN missing"
        )

        return False


    if not DATABASE_URL:

        logger.error(
            "❌ DATABASE_URL missing"
        )

        return False


    try:

        init_db()

    except Exception:

        logger.exception(
            "❌ PostgreSQL initialization failed"
        )

        return False


    # --------------------------------------------------------
    # AI deliberately removed.
    # --------------------------------------------------------

    logger.info(
        "🟡 AI: DISABLED"
    )

    logger.info(
        "📻 Radio: LIKE-BASED"
    )


    # --------------------------------------------------------
    # Webhook
    # --------------------------------------------------------

    setup_webhook()


    # --------------------------------------------------------
    # Telethon
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

    if not startup():

        raise SystemExit(
            1
        )


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
