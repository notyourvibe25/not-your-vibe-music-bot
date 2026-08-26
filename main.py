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

from telethon import TelegramClient
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
        "%(asctime)s | %(levelname)s | "
        "%(threadName)s | %(message)s"
    ),
)

logger = logging.getLogger(
    "not-your-vibe-music-bot"
)


# ============================================================
# ENV HELPERS
# ============================================================

def env_text(
    name: str,
    default: str = ""
) -> str:

    return (
        os.getenv(
            name,
            default
        )
        or ""
    ).strip()


def env_int(
    name: str,
    default: int,
    minimum: int,
    maximum: int
) -> int:

    raw = env_text(name)

    if not raw:
        return default

    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid %s. Using default.",
            name
        )
        return default

    if minimum <= value <= maximum:
        return value

    logger.warning(
        "%s is outside allowed range. "
        "Using default.",
        name
    )

    return default


def env_bool(
    name: str,
    default: bool = False
) -> bool:

    raw = env_text(name).lower()

    if not raw:
        return default

    return raw in {
        "1",
        "true",
        "yes",
        "on"
    }


# ============================================================
# ENVIRONMENT VARIABLES
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
    or
    env_text("API_ID")
)


TELETHON_API_HASH = (
    env_text("TELETHON_API_HASH")
    or
    env_text("API_HASH")
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
    120
)


DB_POOL_MAX_CONNECTIONS = env_int(
    "DB_POOL_MAX_CONNECTIONS",
    8,
    2,
    20
)


WORKER_COUNT = env_int(
    "MUSIC_WORKER_COUNT",
    4,
    1,
    8
)


RECENT_HISTORY_LIMIT = env_int(
    "RECENT_HISTORY_LIMIT",
    30,
    1,
    500
)


TRACK_CANDIDATE_LIMIT = env_int(
    "TRACK_CANDIDATE_LIMIT",
    100,
    1,
    1000
)


WEBHOOK_MAX_CONNECTIONS = env_int(
    "WEBHOOK_MAX_CONNECTIONS",
    40,
    1,
    100
)


DROP_PENDING_UPDATES = env_bool(
    "DROP_PENDING_UPDATES",
    False
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
# MOOD DESCRIPTIONS
# ============================================================

MOOD_DESCRIPTIONS = {

    "sad":
        "Emotional • Deep • Lonely",

    "love":
        "Romantic • Soft • Feelings",

    "chill":
        "Relax • Smooth • Vibes",

    "hype":
        "Bass • Energy • Festival",

    "dark":
        "Dark • Heavy • Intense",

    "energetic":
        "High Energy • Dance • Power",

    "night":
        "Late Night • Drive • Neon",

    "melodic":
        "Emotional • Atmospheric • Beautiful",

}


# ============================================================
# CHANNELS
# ============================================================
#
# HYPE + MELODIC are fixed.
#
# Other channels can be supplied through Render ENV.
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
        "-1004427220481",

    "dark":
        env_text("DARK_CHANNEL"),

    "energetic":
        env_text("ENERGETIC_CHANNEL"),

    "night":
        env_text("NIGHT_CHANNEL"),

    "melodic":
        "-1004446996297",

}


# ============================================================
# AUDIO EXTENSIONS
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


music_executor = ThreadPoolExecutor(
    max_workers=WORKER_COUNT,
    thread_name_prefix="music-request"
)


pending_music_users: set[int] = set()


pending_music_lock = threading.Lock()


http_local = threading.local()


# ============================================================
# DATABASE
# ============================================================

def normalize_database_url(
    url: str
) -> str:

    if url.startswith(
        "postgres://"
    ):

        return (
            "postgresql://"
            +
            url[len("postgres://"):]
        )

    return url


# ============================================================
# CREATE DB POOL
# ============================================================

def initialize_db_pool() -> None:

    global db_pool

    if not DATABASE_URL:

        raise RuntimeError(
            "DATABASE_URL is missing. "
            "Add Render PostgreSQL "
            "Internal Database URL."
        )


    with db_pool_lock:

        if db_pool is not None:
            return


        logger.info(
            "🔐 Creating PostgreSQL connection pool..."
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
            "✅ PostgreSQL connection pool created"
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
            InterfaceError
        ):

            logger.warning(
                "⚠️ PostgreSQL connection "
                "was stale. Reconnecting..."
            )


            try:

                db_pool.putconn(
                    connection,
                    close=True
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
                InterfaceError
            ):

                logger.exception(
                    "Could not return "
                    "PostgreSQL connection"
                )


# ============================================================
# DB CURSOR
# ============================================================

@contextmanager
def db_cursor(
    connection: Any
) -> Iterator[Any]:

    cursor = connection.cursor(
        cursor_factory=RealDictCursor
    )

    try:

        yield cursor

    finally:

        cursor.close()


# ============================================================
# INIT DATABASE
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


        CREATE INDEX IF NOT EXISTS idx_tracks_mood

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


    with db_connection() as connection:

        with db_cursor(connection) as cursor:

            cursor.execute(
                schema
            )


            # Keep only 7 days of webhook
            # deduplication records.

            cursor.execute(
                """
                DELETE FROM processed_updates
                WHERE processed_at < %s
                """,
                (
                    int(time.time()) - 604800,
                )
            )


    logger.info(
        "✅ PostgreSQL database is ready"
    )


# ============================================================
# WEBHOOK UPDATE DEDUPLICATION
# ============================================================

def claim_update(
    update_id: Any
) -> bool:

    if not isinstance(
        update_id,
        int
    ):

        return True


    try:

        with db_connection() as connection:

            with db_cursor(connection) as cursor:

                cursor.execute(
                    """
                    INSERT INTO processed_updates
                    (
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
                        int(time.time())
                    )
                )


                return (
                    cursor.fetchone()
                    is not None
                )


    except Exception:

        logger.exception(
            "Could not deduplicate update %s",
            update_id
        )

        return True


# ============================================================
# HTTP SESSION
# ============================================================

def get_http_session() -> requests.Session:

    session = getattr(
        http_local,
        "session",
        None
    )


    if session is None:

        session = requests.Session()


        session.headers.update({

            "User-Agent":
                "NOT-YOUR-VIBE-MUSIC-BOT/POSTGRES-2.0"

        })


        http_local.session = session


    return session


# ============================================================
# TELEGRAM API
# ============================================================

def telegram(
    method: str,
    data: Optional[
        dict[str, Any]
    ] = None,
    timeout: int = HTTP_TIMEOUT
) -> dict[str, Any]:

    if not BOT_TOKEN:

        return {

            "ok": False,

            "description":
                "BOT_TOKEN missing"

        }


    url = (

        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"

    )


    try:

        response = get_http_session().post(

            url,

            json=data or {},

            timeout=timeout

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
                    )

            }


        if not isinstance(
            result,
            dict
        ):

            result = {

                "ok": False,

                "description":
                    "Unexpected Telegram response"

            }


        if (
            response.status_code >= 400
            or
            not result.get("ok")
        ):

            logger.warning(

                "Telegram %s failed: %s",

                method,

                result.get(
                    "description",
                    result
                )

            )


        return result


    except requests.RequestException as exc:

        logger.warning(

            "Telegram request %s failed: %s",

            method,

            exc

        )


        return {

            "ok": False,

            "description":
                str(exc)

        }


# ============================================================
# SEND MESSAGE
# ============================================================

def send_message(
    chat_id: int,
    text: str,
    keyboard: Optional[
        dict[str, Any]
    ] = None
) -> dict[str, Any]:

    data: dict[str, Any] = {

        "chat_id":
            chat_id,

        "text":
            text,

        "disable_web_page_preview":
            True,

    }


    if keyboard is not None:

        data[
            "reply_markup"
        ] = keyboard


    return telegram(

        "sendMessage",

        data,

        timeout=15

    )


# ============================================================
# CALLBACK
# ============================================================

def answer_callback(
    callback_id: Any,
    text: str = ""
) -> dict[str, Any]:

    if not callback_id:

        return {
            "ok": False
        }


    return telegram(

        "answerCallbackQuery",

        {

            "callback_query_id":
                callback_id,

            "text":
                text,

        },

        timeout=8

    )


# ============================================================
# COPY MUSIC
# ============================================================

def copy_music(
    chat_id: int,
    channel_id: str,
    message_id: int
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

        timeout=30

    )


# ============================================================
# USERS
# ============================================================

def register_user(
    user: Mapping[str, Any]
) -> None:

    user_id = (
        user.get("id")
        if user
        else None
    )


    if not isinstance(
        user_id,
        int
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
                    INSERT INTO users
                    (
                        user_id,
                        username,
                        first_name,
                        last_name,
                        first_seen,
                        last_seen,
                        total_requests
                    )

                    VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        1
                    )

                    ON CONFLICT (user_id)

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

                        user.get(
                            "username"
                        ),

                        user.get(
                            "first_name"
                        ),

                        user.get(
                            "last_name"
                        ),

                        now,

                        now,

                    )

                )


    except Exception:

        logger.exception(
            "Could not register user %s",
            user_id
        )


# ============================================================
# SAVE TRACK
# ============================================================

def save_track(
    mood: str,
    channel_id: Any,
    message_id: Any
) -> None:

    if mood not in MOODS:
        return


    if not channel_id:
        return


    if not message_id:
        return


    try:

        with db_connection() as connection:

            with db_cursor(connection) as cursor:

                cursor.execute(

                    """
                    INSERT INTO tracks
                    (
                        mood,
                        channel_id,
                        message_id,
                        created_at
                    )

                    VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s
                    )

                    ON CONFLICT
                    (
                        channel_id,
                        message_id
                    )

                    DO NOTHING
                    """,

                    (

                        mood,

                        str(
                            channel_id
                        ),

                        int(
                            message_id
                        ),

                        int(
                            time.time()
                        ),

                    )

                )


    except Exception:

        logger.exception(
            "Could not save track"
        )


# ============================================================
# TRACK COUNT
# ============================================================

def get_track_count(
    mood: str
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

                    (
                        mood,
                    )

                )


                row = cursor.fetchone()


                if not row:
                    return 0


                return int(
                    row["count"]
                )


    except Exception:

        logger.exception(
            "Could not count tracks"
        )

        return 0


# ============================================================
# TRACK COUNTS
# ============================================================

def get_track_counts() -> dict[str, int]:

    counts = {
        mood: 0
        for mood in MOODS
    }


    try:

        with db_connection() as connection:

            with db_cursor(connection) as cursor:

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

                    mood = row[
                        "mood"
                    ]


                    if mood in counts:

                        counts[mood] = int(
                            row["count"]
                        )


    except Exception:

        logger.exception(
            "Could not collect track statistics"
        )


    return counts


# ============================================================
# USER MOOD
# ============================================================

def set_user_mood(
    user_id: int,
    mood: str
) -> bool:

    if not isinstance(
        user_id,
        int
    ):

        return False


    if mood not in MOODS:
        return False


    try:

        with db_connection() as connection:

            with db_cursor(connection) as cursor:

                cursor.execute(

                    """
                    INSERT INTO user_state
                    (
                        user_id,
                        mood,
                        updated_at
                    )

                    VALUES
                    (
                        %s,
                        %s,
                        %s
                    )

                    ON CONFLICT (user_id)

                    DO UPDATE SET

                        mood =
                            EXCLUDED.mood,

                        updated_at =
                            EXCLUDED.updated_at
                    """,

                    (

                        user_id,

                        mood,

                        int(
                            time.time()
                        ),

                    )

                )


        return True


    except Exception:

        logger.exception(
            "Could not save user mood"
        )

        return False


# ============================================================
# GET USER MOOD
# ============================================================

def get_user_mood(
    user_id: int
) -> Optional[str]:

    if not isinstance(
        user_id,
        int
    ):

        return None


    try:

        with db_connection() as connection:

            with db_cursor(connection) as cursor:

                cursor.execute(

                    """
                    SELECT mood
                    FROM user_state
                    WHERE user_id = %s
                    """,

                    (
                        user_id,
                    )

                )


                row = cursor.fetchone()


                if (
                    row
                    and
                    row["mood"] in MOODS
                ):

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
    mood: str
) -> Optional[
    tuple[int, str]
]:

    if not isinstance(
        user_id,
        int
    ):

        return None


    if mood not in MOODS:
        return None


    try:

        with db_connection() as connection:

            with db_cursor(connection) as cursor:

                # Prevent two simultaneous
                # requests from the same user.

                cursor.execute(

                    """
                    SELECT pg_advisory_xact_lock(%s)
                    """,

                    (
                        user_id,
                    )

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

                    )

                )


                recent = {

                    (
                        str(
                            row[
                                "channel_id"
                            ]
                        ),

                        int(
                            row[
                                "message_id"
                            ]
                        )

                    )

                    for row
                    in cursor.fetchall()

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

                    )

                )


                rows = cursor.fetchall()


                if not rows:

                    return None


                candidates = [

                    (

                        int(
                            row[
                                "message_id"
                            ]
                        ),

                        str(
                            row[
                                "channel_id"
                            ]
                        )

                    )

                    for row
                    in rows

                    if (

                        str(
                            row[
                                "channel_id"
                            ]
                        ),

                        int(
                            row[
                                "message_id"
                            ]
                        )

                    ) not in recent

                ]


                if not candidates:

                    candidates = [

                        (

                            int(
                                row[
                                    "message_id"
                                ]
                            ),

                            str(
                                row[
                                    "channel_id"
                                ]
                            )

                        )

                        for row
                        in rows

                    ]


                message_id, channel_id = (
                    random.choice(
                        candidates
                    )
                )


                cursor.execute(

                    """
                    INSERT INTO user_history
                    (
                        user_id,
                        mood,
                        channel_id,
                        message_id,
                        sent_at
                    )

                    VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    """,

                    (

                        user_id,

                        mood,

                        channel_id,

                        message_id,

                        int(
                            time.time()
                        ),

                    )

                )


                return (
                    message_id,
                    channel_id
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
    message_id: int
) -> None:

    try:

        with db_connection() as connection:

            with db_cursor(connection) as cursor:

                cursor.execute(

                    """
                    DELETE FROM user_history

                    WHERE id = (

                        SELECT id

                        FROM user_history

                        WHERE user_id = %s

                        AND channel_id = %s

                        AND message_id = %s

                        ORDER BY id DESC

                        LIMIT 1

                    )
                    """,

                    (

                        user_id,

                        channel_id,

                        message_id,

                    )

                )


    except Exception:

        logger.exception(
            "Could not remove failed history"
        )


# ============================================================
# USER COUNT
# ============================================================

def get_users_count() -> int:

    try:

        with db_connection() as connection:

            with db_cursor(connection) as cursor:

                cursor.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM users
                    """
                )


                row = cursor.fetchone()


                if row:

                    return int(
                        row["count"]
                    )


    except Exception:

        logger.exception(
            "Could not count users"
        )


    return 0


# ============================================================
# PREMIUM MOOD MENU
# ============================================================

def mood_menu() -> dict[str, Any]:

    return {

        "inline_keyboard": [

            [

                {
                    "text":
                        "😢  SAD",
                    "callback_data":
                        "mood_sad"
                },

                {
                    "text":
                        "❤️  LOVE",
                    "callback_data":
                        "mood_love"
                },

            ],

            [

                {
                    "text":
                        "🌙  CHILL",
                    "callback_data":
                        "mood_chill"
                },

                {
                    "text":
                        "🔥  HYPE",
                    "callback_data":
                        "mood_hype"
                },

            ],

            [

                {
                    "text":
                        "🖤  DARK",
                    "callback_data":
                        "mood_dark"
                },

                {
                    "text":
                        "⚡  ENERGY",
                    "callback_data":
                        "mood_energetic"
                },

            ],

            [

                {
                    "text":
                        "🚗  NIGHT DRIVE",
                    "callback_data":
                        "mood_night"
                },

                {
                    "text":
                        "🌌  MELODIC",
                    "callback_data":
                        "mood_melodic"
                },

            ],

        ]

    }


# ============================================================
# MUSIC BUTTONS
# ============================================================

def music_buttons() -> dict[str, Any]:

    return {

        "inline_keyboard": [

            [

                {
                    "text":
                        "🔀  NEXT TRACK",
                    "callback_data":
                        "next_music"
                }

            ],

            [

                {
                    "text":
                        "🎧  CHANGE MOOD",
                    "callback_data":
                        "change_mood"
                }

            ],

        ]

    }


# ============================================================
# WELCOME TEXT
# ============================================================

def welcome_text() -> str:

    return (

        "🎧  NOT YOUR VIBE\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        "Welcome to your personal "
        "music mood selector. ✦\n\n"

        "Choose a vibe below and "
        "let the music find you.\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "✦ Curated by NOT YOUR VIBE\n"
        "✦ Mood-based music discovery\n"
        "━━━━━━━━━━━━━━━━━━"

    )


# ============================================================
# MOOD SELECTED TEXT
# ============================================================

def mood_selected_text(
    mood: str
) -> str:

    return (

        f"{MOOD_NAMES[mood]}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"✦ {MOOD_DESCRIPTIONS[mood]}\n\n"

        "🎧 Finding something for you...\n\n"

        "━━━━━━━━━━━━━━━━━━"

    )


# ============================================================
# SEND MUSIC
# ============================================================

def send_music(
    chat_id: int,
    user_id: int,
    mood: str
) -> None:

    if mood not in MOODS:

        send_message(

            chat_id,

            "⚠️ Mood မမှန်ပါ။",

            mood_menu()

        )

        return


    try:

        count = get_track_count(
            mood
        )


        logger.info(

            "🎧 Music request | "
            "user=%s | mood=%s | tracks=%s",

            user_id,

            mood,

            count

        )


        if count <= 0:

            send_message(

                chat_id,

                (
                    f"{MOOD_NAMES[mood]}\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    "⚠️ ဒီ mood ထဲမှာ "
                    "music မတွေ့သေးပါ။\n\n"
                    "Admin က channel ထဲမှာ "
                    "music ထည့်ပေးပြီး "
                    "scanner ပြန် run ပါလိမ့်မယ်။"
                ),

                mood_menu()

            )

            return


        attempts = min(
            count,
            10
        )


        for _ in range(
            attempts
        ):

            reserved = reserve_track(

                user_id,

                mood

            )


            if not reserved:

                break


            message_id, channel_id = (
                reserved
            )


            logger.info(

                "🎵 Trying track | "
                "mood=%s | channel=%s | message=%s",

                mood,

                channel_id,

                message_id

            )


            result = copy_music(

                chat_id,

                channel_id,

                message_id

            )


            if result.get(
                "ok"
            ):

                send_message(

                    chat_id,

                    (
                        f"{MOOD_NAMES[mood]}\n"
                        "━━━━━━━━━━━━━━━━━━\n\n"
                        "🎧  NOW PLAYING\n\n"
                        f"✦ {MOOD_DESCRIPTIONS[mood]}\n\n"
                        "Enjoy the vibe. ✦\n\n"
                        "━━━━━━━━━━━━━━━━━━"
                    ),

                    music_buttons()

                )


                logger.info(

                    "✅ MUSIC SENT | "
                    "user=%s | mood=%s | message=%s",

                    user_id,

                    mood,

                    message_id

                )


                return


            logger.warning(

                "⚠️ Copy failed | "
                "channel=%s | message=%s",

                channel_id,

                message_id

            )


            remove_failed_history(

                user_id,

                channel_id,

                message_id

            )


        send_message(

            chat_id,

            (
                f"{MOOD_NAMES[mood]}\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "⚠️ ဒီ mood က track တွေကို "
                "အခု copy လုပ်လို့မရသေးပါ။\n\n"
                "ခဏနေရင် ပြန်စမ်းကြည့်ပါ။"
            ),

            music_buttons()

        )


    except Exception:

        logger.exception(
            "Unexpected send_music error"
        )


        send_message(

            chat_id,

            "⚠️ ခဏအကြာမှာ "
            "ပြန်စမ်းကြည့်ပါ။"

        )


# ============================================================
# MUSIC WORKER
# ============================================================

def music_request_worker(
    chat_id: int,
    user_id: int,
    mood: str
) -> None:

    try:

        send_music(

            chat_id,

            user_id,

            mood

        )

    finally:

        with pending_music_lock:

            pending_music_users.discard(
                user_id
            )


# ============================================================
# SCHEDULE MUSIC
# ============================================================

def schedule_music_request(
    chat_id: int,
    user_id: int,
    mood: str
) -> bool:

    if mood not in MOODS:

        return False


    with pending_music_lock:

        if user_id in pending_music_users:

            return False


        pending_music_users.add(
            user_id
        )


    try:

        music_executor.submit(

            music_request_worker,

            chat_id,

            user_id,

            mood

        )


        return True


    except Exception:

        with pending_music_lock:

            pending_music_users.discard(
                user_id
            )


        logger.exception(
            "Could not queue music request"
        )


        return False


# ============================================================
# TELETHON CHANNEL ID
# ============================================================

def normalize_channel_id(
    entity: Any
) -> Optional[str]:

    entity_id = getattr(
        entity,
        "id",
        None
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
        +
        value
    )


# ============================================================
# CHECK MUSIC MESSAGE
# ============================================================

def is_music_message(
    message: Any
) -> bool:

    if not message:
        return False


    if not getattr(
        message,
        "media",
        None
    ):

        return False


    if getattr(
        message,
        "audio",
        None
    ):

        return True


    document = getattr(
        message,
        "document",
        None
    )


    if not document:

        return False


    mime_type = (

        getattr(
            document,
            "mime_type",
            ""
        )
        or
        ""

    ).lower()


    if mime_type.startswith(
        (
            "audio/",
            "video/"
        )
    ):

        return True


    file_obj = getattr(
        message,
        "file",
        None
    )


    name = (

        getattr(
            file_obj,
            "name",
            ""
        )
        or
        ""

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
    message: Any
) -> bool:

    if not is_music_message(
        message
    ):

        return False


    channel_id = (
        normalize_channel_id(
            entity
        )
    )


    message_id = getattr(
        message,
        "id",
        None
    )


    if not channel_id:
        return False


    if not message_id:
        return False


    save_track(

        mood,

        channel_id,

        message_id

    )


    return True


# ============================================================
# SCAN ONE CHANNEL
# ============================================================

async def scan_one_channel(
    mood: str,
    channel_value: str
) -> int:

    logger.info(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    logger.info(
        "🔎 SCANNING %s",
        mood.upper()
    )


    if not channel_value:

        logger.warning(

            "⚠️ %s channel missing",

            mood.upper()

        )

        return 0


    if telethon_client is None:

        logger.warning(
            "⚠️ Telethon client unavailable"
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


        logger.info(

            "🔎 Getting %s entity...",

            mood.upper()

        )


        entity = await (
            telethon_client.get_entity(
                lookup
            )
        )


        logger.info(

            "✅ %s entity found | id=%s",

            mood.upper(),

            getattr(
                entity,
                "id",
                "unknown"
            )

        )


        found = 0

        scanned = 0


        async for message in (
            telethon_client.iter_messages(
                entity
            )
        ):

            scanned += 1


            try:

                if save_telethon_message(

                    mood,

                    entity,

                    message

                ):

                    found += 1


            except Exception:

                logger.exception(

                    "Message processing failed "
                    "in %s",

                    mood.upper()

                )


        logger.info(

            "📦 %s messages checked: %s",

            mood.upper(),

            scanned

        )


        logger.info(

            "🎵 %s MUSIC FOUND: %s",

            mood.upper(),

            found

        )


        logger.info(

            "✅ %s SCAN COMPLETE",

            mood.upper()

        )


        return found


    except Exception:

        logger.exception(

            "❌ %s SCAN FAILED",

            mood.upper()

        )

        return 0


# ============================================================
# SCAN ALL CHANNELS
# ============================================================

async def scan_all_channels() -> None:

    logger.info(
        "🚀 STARTING ALL CHANNEL SCANS"
    )


    total = 0


    for mood in MOODS:

        try:

            found = await scan_one_channel(

                mood,

                MOOD_CHANNELS.get(
                    mood,
                    ""
                )

            )


            total += found


        except Exception:

            logger.exception(

                "❌ %s scan system error",

                mood.upper()

            )


        await asyncio.sleep(
            1
        )


    logger.info(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


    logger.info(
        "🎵 TOTAL MUSIC FOUND: %s",
        total
    )


    logger.info(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


# ============================================================
# TELETHON WORKER
# ============================================================

def telethon_worker() -> None:

    global telethon_client


    logger.info(
        "🔐 TELETHON WORKER STARTING"
    )


    if not TELETHON_API_ID:

        logger.error(
            "❌ TELETHON_API_ID missing"
        )

        return


    logger.info(
        "✅ TELETHON_API_ID found"
    )


    if not TELETHON_API_HASH:

        logger.error(
            "❌ TELETHON_API_HASH missing"
        )

        return


    logger.info(
        "✅ TELETHON_API_HASH found"
    )


    if not TELETHON_SESSION:

        logger.error(
            "❌ TELETHON_SESSION missing"
        )

        return


    logger.info(
        "✅ TELETHON_SESSION found"
    )


    try:

        api_id = int(
            TELETHON_API_ID
        )


    except ValueError:

        logger.error(
            "❌ TELETHON_API_ID is not a number"
        )

        return


    try:

        logger.info(
            "🔐 Creating Telegram client..."
        )


        telethon_client = TelegramClient(

            StringSession(
                TELETHON_SESSION
            ),

            api_id,

            TELETHON_API_HASH,

            connection_retries=5,

            retry_delay=5,

            timeout=30,

        )


        logger.info(
            "🔐 Telegram client created"
        )


    except Exception:

        logger.exception(
            "❌ Telegram client creation failed"
        )

        return


    async def runner() -> None:

        assert telethon_client is not None


        while True:

            try:

                logger.info(
                    "🔐 Connecting to Telegram..."
                )


                await telethon_client.connect()


                logger.info(
                    "✅ Telegram connection established"
                )


                authorized = (
                    await telethon_client
                    .is_user_authorized()
                )


                if not authorized:

                    logger.error(
                        "❌ TELETHON SESSION "
                        "NOT AUTHORIZED"
                    )

                    logger.error(
                        "❌ Generate a new "
                        "StringSession."
                    )

                    return


                telethon_ready.set()


                logger.info(
                    "✅ TELETHON LOGIN SUCCESS"
                )


                # ------------------------------------------------
                # ACCOUNT
                # ------------------------------------------------

                try:

                    me = await (
                        telethon_client.get_me()
                    )


                    if me:

                        username = (

                            getattr(
                                me,
                                "username",
                                None
                            )

                            or

                            getattr(
                                me,
                                "first_name",
                                None
                            )

                            or

                            "Unknown"

                        )


                        logger.info(

                            "👤 Telegram account: %s",

                            username

                        )


                except Exception:

                    logger.exception(
                        "⚠️ Could not get "
                        "Telegram account"
                    )


                # ------------------------------------------------
                # SCAN CHANNELS
                # ------------------------------------------------

                try:

                    await scan_all_channels()

                except Exception:

                    logger.exception(
                        "❌ Channel scan error"
                    )


                logger.info(
                    "📡 TELETHON WATCHER READY"
                )


                # ------------------------------------------------
                # KEEP CONNECTION ALIVE
                # ------------------------------------------------

                await (
                    telethon_client
                    .run_until_disconnected()
                )


                logger.warning(
                    "⚠️ Telethon disconnected"
                )


            except Exception:

                logger.exception(
                    "❌ TELETHON CONNECTION ERROR"
                )


            finally:

                telethon_ready.clear()


                try:

                    if (
                        telethon_client
                        and
                        telethon_client.is_connected()
                    ):

                        await (
                            telethon_client
                            .disconnect()
                        )


                except Exception:

                    logger.exception(
                        "⚠️ Telethon disconnect error"
                    )


            # ----------------------------------------------------
            # AUTO RECONNECT
            # ----------------------------------------------------

            logger.warning(
                "🔄 Telethon will reconnect "
                "in 10 seconds..."
            )


            await asyncio.sleep(
                10
            )


    try:

        asyncio.run(
            runner()
        )


    except Exception:

        logger.exception(
            "❌ TELETHON WORKER STOPPED"
        )


    finally:

        telethon_ready.clear()


# ============================================================
# START TELETHON WORKER
# ============================================================

def start_telethon_worker() -> None:

    global telethon_thread


    with telethon_start_lock:

        if (
            telethon_thread
            and
            telethon_thread.is_alive()
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
    user_id: Any
) -> bool:

    if not ADMIN_USER_ID:

        return False


    return (
        user_id is not None
        and
        str(user_id)
        ==
        ADMIN_USER_ID
    )


# ============================================================
# STATS
# ============================================================

def send_stats(
    chat_id: int,
    requester_id: int
) -> None:

    if not is_admin(
        requester_id
    ):

        send_message(
            chat_id,
            "❌ Admin only."
        )

        return


    users = get_users_count()

    counts = get_track_counts()

    total = sum(
        counts.values()
    )


    lines = [

        "📊  NOT YOUR VIBE",
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"👥  Users       : {users}",
        f"🎵  Total Tracks: {total}",
        "",
        "MOOD LIBRARY",
        "──────────────",

    ]


    for mood in MOODS:

        lines.append(

            f"{MOOD_NAMES[mood]}"
            f"  →  "
            f"{counts[mood]}"

        )


    lines.extend([

        "",
        "━━━━━━━━━━━━━━━━━━",
        "✦ Database: PostgreSQL",
        "✦ Scanner: Telethon",

    ])


    send_message(

        chat_id,

        "\n".join(
            lines
        )

    )


# ============================================================
# COMMAND PARSER
# ============================================================

def extract_command(
    text: str
) -> str:

    if not text.startswith(
        "/"
    ):

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
# HANDLE CALLBACK
# ============================================================

def handle_callback(
    callback: Mapping[str, Any]
) -> None:

    callback_id = callback.get(
        "id"
    )


    data = (
        callback.get("data")
        or
        ""
    )


    user = (
        callback.get("from")
        or
        {}
    )


    message = (
        callback.get("message")
        or
        {}
    )


    chat = (
        message.get("chat")
        or
        {}
    )


    chat_id = chat.get(
        "id"
    )


    user_id = user.get(
        "id"
    )


    if not isinstance(
        chat_id,
        int
    ):

        return


    if not isinstance(
        user_id,
        int
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
            5:
        ]


        if mood not in MOODS:

            answer_callback(
                callback_id,
                "Invalid mood"
            )

            return


        if not set_user_mood(

            user_id,

            mood

        ):

            answer_callback(

                callback_id,

                "Please try again"

            )

            return


        if not schedule_music_request(

            chat_id,

            user_id,

            mood

        ):

            answer_callback(

                callback_id,

                "⏳ Already finding a track..."

            )

            return


        answer_callback(

            callback_id,

            f"{MOOD_NAMES[mood]} ✓"

        )


        send_message(

            chat_id,

            mood_selected_text(
                mood
            )

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

                "Choose a mood first"

            )


            send_message(

                chat_id,

                welcome_text(),

                mood_menu()

            )


            return


        if schedule_music_request(

            chat_id,

            user_id,

            mood

        ):

            answer_callback(

                callback_id,

                "🔀 Finding next..."

            )

        else:

            answer_callback(

                callback_id,

                "⏳ Already finding a track..."

            )


        return


    # ========================================================
    # CHANGE MOOD
    # ========================================================

    if data == "change_mood":

        answer_callback(

            callback_id,

            "🎧 Choose your vibe"

        )


        send_message(

            chat_id,

            welcome_text(),

            mood_menu()

        )


        return


    answer_callback(
        callback_id
    )


# ============================================================
# HANDLE NORMAL MESSAGE
# ============================================================

def handle_message(
    message: Mapping[str, Any]
) -> None:

    chat = (
        message.get("chat")
        or
        {}
    )


    user = (
        message.get("from")
        or
        {}
    )


    chat_id = chat.get(
        "id"
    )


    user_id = user.get(
        "id"
    )


    if not isinstance(
        chat_id,
        int
    ):

        return


    register_user(
        user
    )


    text = (

        message
        .get(
            "text",
            ""
        )
        or
        ""

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

            welcome_text(),

            mood_menu()

        )

        return


    # ========================================================
    # MOOD
    # ========================================================

    if command == "/mood":

        send_message(

            chat_id,

            welcome_text(),

            mood_menu()

        )

        return


    # ========================================================
    # NEXT
    # ========================================================

    if command == "/next":

        if not isinstance(
            user_id,
            int
        ):

            return


        mood = get_user_mood(
            user_id
        )


        if not mood:

            send_message(

                chat_id,

                welcome_text(),

                mood_menu()

            )

            return


        if not schedule_music_request(

            chat_id,

            user_id,

            mood

        ):

            send_message(

                chat_id,

                "⏳ Track ရှာနေပြီးသားပါ။ "
                "ခဏစောင့်ပါ။"

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
                    "👥  USER DATABASE\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    f"Total users: "
                    f"{get_users_count()}\n\n"
                    "━━━━━━━━━━━━━━━━━━"
                )

            )

        else:

            send_message(
                chat_id,
                "❌ Admin only."
            )


        return


    # ========================================================
    # STATS
    # ========================================================

    if command == "/stats":

        if isinstance(
            user_id,
            int
        ):

            send_stats(

                chat_id,

                user_id

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
                "❌ Admin only."
            )

            return


        if telethon_ready.is_set():

            send_message(

                chat_id,

                (
                    "🟢  TELEGRAM CONNECTED\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    "📡 Telethon: ONLINE\n"
                    "🎵 Channel Scanner: READY\n"
                    "🔄 Auto Reconnect: ON\n\n"
                    "━━━━━━━━━━━━━━━━━━"
                )

            )

        else:

            send_message(

                chat_id,

                (
                    "🔴  TELEGRAM OFFLINE\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    "Telethon is currently "
                    "not connected.\n\n"
                    "🔄 Auto reconnect is ON.\n"
                    "Check Render Logs if it "
                    "doesn't reconnect.\n\n"
                    "━━━━━━━━━━━━━━━━━━"
                )

            )


        return


    # ========================================================
    # HELP
    # ========================================================

    if command == "/help":

        send_message(

            chat_id,

            (
                "🎧  NOT YOUR VIBE\n"
                "━━━━━━━━━━━━━━━━━━\n\n"

                "/start  →  Start Bot\n"
                "/mood   →  Mood Menu\n"
                "/next   →  Next Track\n"
                "/users  →  User Count\n"
                "/stats  →  Bot Statistics\n"
                "/telegram → Connection Status\n"
                "/help   →  Help\n\n"

                "━━━━━━━━━━━━━━━━━━\n"
                "✦ Choose a mood.\n"
                "✦ Get a random track.\n"
                "✦ Press NEXT for another one."
            )

        )

        return


# ============================================================
# HANDLE UPDATE
# ============================================================

def handle_update(
    update: Mapping[str, Any]
) -> None:

    update_id = update.get(
        "update_id"
    )


    if not claim_update(
        update_id
    ):

        return


    callback = update.get(
        "callback_query"
    )


    if isinstance(
        callback,
        Mapping
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
        Mapping
    ):

        handle_message(
            message
        )


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home() -> str:

    return (
        "🎧 NOT YOUR VIBE MUSIC BOT ONLINE"
    )


# ============================================================
# HEALTH
# ============================================================

@app.route(
    "/health"
)
def health():

    if db_pool is None:

        return (
            "Database not ready",
            503
        )


    return (
        "OK",
        200
    )


# ============================================================
# WEBHOOK
# ============================================================

@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    if (
        WEBHOOK_SECRET
        and
        request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token",
            ""
        )
        != WEBHOOK_SECRET
    ):

        logger.warning(
            "Rejected invalid webhook secret"
        )

        return (
            "Forbidden",
            403
        )


    try:

        update = request.get_json(
            silent=True
        )


        if isinstance(
            update,
            Mapping
        ):

            handle_update(
                update
            )


    except Exception:

        logger.exception(
            "Webhook processing error"
        )


    return "OK"


# ============================================================
# SET WEBHOOK
# ============================================================

def setup_webhook() -> None:

    if not BOT_TOKEN:

        logger.error(
            "❌ BOT_TOKEN missing"
        )

        return


    if not RENDER_EXTERNAL_URL:

        logger.error(
            "❌ RENDER_EXTERNAL_URL missing"
        )

        return


    webhook_url = (

        RENDER_EXTERNAL_URL.rstrip("/")
        +
        "/webhook"

    )


    payload: dict[str, Any] = {

        "url":
            webhook_url,

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


    logger.info(
        "🔗 Webhook URL: %s",
        webhook_url
    )


    result = telegram(

        "setWebhook",

        payload,

        timeout=20

    )


    if result.get(
        "ok"
    ):

        logger.info(
            "✅ Webhook configured"
        )

    else:

        logger.error(
            "❌ Webhook configuration failed: %s",
            result
        )


# ============================================================
# STARTUP
# ============================================================

def startup() -> bool:

    logger.info(
        "=================================================="
    )


    logger.info(
        "🎧 NOT YOUR VIBE MUSIC BOT"
    )


    logger.info(
        "=================================================="
    )


    # ========================================================
    # ENV CHECK
    # ========================================================

    if BOT_TOKEN:

        logger.info(
            "✅ BOT_TOKEN found"
        )

    else:

        logger.error(
            "❌ BOT_TOKEN missing"
        )


    if ADMIN_USER_ID:

        logger.info(
            "✅ ADMIN_USER_ID found"
        )

    else:

        logger.warning(
            "⚠️ ADMIN_USER_ID missing"
        )


    if DATABASE_URL:

        logger.info(
            "✅ DATABASE_URL found"
        )

    else:

        logger.error(
            "❌ DATABASE_URL missing"
        )


    # ========================================================
    # CHANNELS
    # ========================================================

    logger.info(
        "📡 MOOD CHANNELS"
    )


    for mood in MOODS:

        channel = MOOD_CHANNELS.get(
            mood,
            ""
        )


        if channel:

            logger.info(

                "✅ %s → %s",

                mood.upper(),

                channel

            )

        else:

            logger.warning(

                "⚠️ %s channel missing",

                mood.upper()

            )


    # ========================================================
    # DATABASE
    # ========================================================

    try:

        init_db()


    except Exception:

        logger.exception(
            "❌ PostgreSQL initialization failed"
        )

        return False


    # ========================================================
    # WEBHOOK
    # ========================================================

    setup_webhook()


    # ========================================================
    # TELETHON
    # ========================================================

    start_telethon_worker()


    logger.info(
        "=================================================="
    )


    logger.info(
        "🚀 BOT SERVER READY"
    )


    logger.info(
        "=================================================="
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

        65535

    )


    app.run(

        host="0.0.0.0",

        port=port,

        threaded=True,

        use_reloader=False

)
