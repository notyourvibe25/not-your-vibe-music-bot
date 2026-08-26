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
# ENVIRONMENT VARIABLES
# ============================================================

BOT_TOKEN = env_text("BOT_TOKEN")

ADMIN_USER_ID = env_text("ADMIN_USER_ID")

RENDER_EXTERNAL_URL = env_text("RENDER_EXTERNAL_URL")

if not RENDER_EXTERNAL_URL:
    hostname = env_text("RENDER_EXTERNAL_HOSTNAME")

    if hostname:
        RENDER_EXTERNAL_URL = f"https://{hostname}"


WEBHOOK_SECRET = env_text(
    "TELEGRAM_WEBHOOK_SECRET"
)

DATABASE_URL = env_text(
    "DATABASE_URL"
)


# Telethon
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


# ============================================================
# AUTO SCAN SETTINGS
# ============================================================

# Channel အသစ်တွေကို event နဲ့ချက်ချင်းဖမ်းမယ်။
# အပြင် 5 မိနစ်တစ်ခါလည်း ပြန်စစ်မယ်။
AUTO_SCAN_INTERVAL = env_int(
    "AUTO_SCAN_INTERVAL",
    300,
    60,
    3600,
)


# Telethon connection ပြတ်သွားရင်
# ဒီအချိန်ပြီးတာနဲ့ reconnect လုပ်မယ်။
TELETHON_RECONNECT_DELAY = env_int(
    "TELETHON_RECONNECT_DELAY",
    10,
    3,
    120,
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
# SUPPORTED AUDIO
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


# channel ID -> mood
CHANNEL_MOOD_MAP: dict[str, str] = {}


# ============================================================
# DATABASE
# ============================================================

def normalize_database_url(url: str) -> str:

    if url.startswith("postgres://"):
        return "postgresql://" + url[11:]

    return url


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
                "PostgreSQL connection pool created"
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
                "Dead PostgreSQL connection detected. Reconnecting..."
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
                    "Could not return PostgreSQL connection"
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
# DATABASE TABLES
# ============================================================

def init_db() -> None:

    if not DATABASE_URL:

        raise RuntimeError(
            "DATABASE_URL missing"
        )

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

    with (
        db_connection() as connection,
        db_cursor(connection) as cursor
    ):

        cursor.execute(schema)

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
        "PostgreSQL database is ready"
    )


# ============================================================
# UPDATE DEDUPLICATION
# ============================================================

def claim_update(
    update_id: Any,
) -> bool:

    if not isinstance(update_id, int):
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

            return cursor.fetchone() is not None

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
    user: Mapping[str, Any]
) -> None:

    user_id = user.get("id") if user else None

    if not isinstance(user_id, int):
        return

    try:

        now = int(time.time())

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

                VALUES (
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

        channel_id = str(channel_id)

        message_id = int(message_id)

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

                VALUES (
                    %s,
                    %s,
                    %s,
                    %s
                )

                ON CONFLICT (
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

            inserted = (
                cursor.fetchone()
                is not None
            )

            if inserted:

                logger.info(
                    "🎵 NEW TRACK ADDED | %s | %s | message=%s",
                    mood.upper(),
                    channel_id,
                    message_id,
                )

            return inserted

    except Exception:

        logger.exception(
            "Could not save track %r/%r",
            channel_id,
            message_id,
        )

        return False


def get_track_count(
    mood: str
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

                VALUES (
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

    if not isinstance(user_id, int):
        return None

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


# ============================================================
# TRACK RESERVATION
# ============================================================

def reserve_track(
    user_id: int,
    mood: str,
) -> Optional[tuple[int, str]]:

    if (
        not isinstance(user_id, int)
        or mood not in MOODS
    ):
        return None

    try:

        with db_connection() as connection:

            with db_cursor(connection) as cursor:

                # Prevent double clicks.
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

                    VALUES (
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
            "Could not remove failed history"
        )


# ============================================================
# STATISTICS
# ============================================================

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

            return (
                int(row["count"])
                if row
                else 0
            )

    except Exception:

        logger.exception(
            "Could not count users"
        )

        return 0


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
            "Could not collect track statistics"
        )

    return counts


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
                    "NOT-YOUR-VIBE-MUSIC-BOT/2.0"
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
                        "Non-JSON response: "
                        f"HTTP {response.status_code}"
                    ),
            }

        if not isinstance(result, dict):

            result = {
                "ok": False,
                "description":
                    "Unexpected Telegram response",
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
            "Telegram request %s failed: %s",
            method,
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

        "chat_id":
            chat_id,

        "text":
            text,

        "disable_web_page_preview":
            True,
    }

    if keyboard is not None:

        data["reply_markup"] = keyboard

    return telegram(
        "sendMessage",
        data,
        timeout=15,
    )


def answer_callback(
    callback_id: Any,
    text: str = "",
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
            "chat_id":
                chat_id,

            "from_chat_id":
                channel_id,

            "message_id":
                message_id,
        },

        timeout=30,
    )


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

        ]
    }


def music_buttons() -> dict[str, Any]:

    return {

        "inline_keyboard": [

            [
                {
                    "text":
                        "⏭  NEXT TRACK",
                    "callback_data":
                        "next_music",
                }
            ],

            [
                {
                    "text":
                        "🎛  CHANGE MOOD",
                    "callback_data":
                        "change_mood",
                }
            ],

        ]
    }


# ============================================================
# MUSIC SENDING
# ============================================================

def send_music(
    chat_id: int,
    user_id: int,
    mood: str,
) -> None:

    if mood not in MOODS:

        send_message(
            chat_id,
            "⚠️ Mood မမှန်ပါ။\n\n/mood နဲ့ ပြန်ရွေးပါ။",
            mood_menu(),
        )

        return

    try:

        count = get_track_count(
            mood
        )

        if count <= 0:

            send_message(

                chat_id,

                (
                    f"{MOOD_NAMES[mood]}\n\n"
                    "⚠️ ဒီ mood ထဲမှာ "
                    "music မတွေ့သေးပါ။\n\n"
                    "Channel ထဲ music "
                    "ထည့်ထားရင် ခဏအကြာ "
                    "အလိုအလျောက် ထည့်ပေးပါမယ်။"
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
                        f"{MOOD_NAMES[mood]}\n\n"
                        "🎧  TRACK DELIVERED\n"
                        "━━━━━━━━━━━━━━\n"
                        "Enjoy your music. 🔥"
                    ),

                    music_buttons(),
                )

                return

            remove_failed_history(
                user_id,
                channel_id,
                message_id,
            )

        send_message(

            chat_id,

            (
                f"{MOOD_NAMES[mood]}\n\n"
                "⚠️ ဒီ track ကို "
                "အခု copy လုပ်လို့မရသေးပါ။"
            ),

            music_buttons(),
        )

    except Exception:

        logger.exception(
            "Unexpected send_music error"
        )

        send_message(
            chat_id,
            "⚠️ ခဏအကြာမှာ ပြန်စမ်းကြည့်ပါ။",
        )


def music_request_worker(
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


def schedule_music_request(
    chat_id: int,
    user_id: int,
    mood: str,
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
            mood,
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


def rebuild_channel_mood_map() -> None:

    CHANNEL_MOOD_MAP.clear()

    for mood in MOODS:

        channel = MOOD_CHANNELS.get(
            mood,
            "",
        )

        normalized = normalize_config_channel(
            channel
        )

        if normalized:

            CHANNEL_MOOD_MAP[
                normalized
            ] = mood

    logger.info(
        "Configured channel map: %s",
        CHANNEL_MOOD_MAP,
    )


def is_music_message(
    message: Any,
) -> bool:

    if (
        not message
        or not getattr(
            message,
            "media",
            None,
        )
    ):
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

    name = (
        getattr(
            getattr(
                message,
                "file",
                None,
            ),
            "name",
            "",
        )
        or ""
    ).lower()

    return name.endswith(
        AUDIO_EXTENSIONS
    )


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
            "%s channel is not configured",
            mood.upper(),
        )

        return 0

    try:

        lookup: Any

        if channel_value.lstrip("-").isdigit():

            lookup = int(
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

            try:

                if save_telethon_message(
                    mood,
                    entity,
                    message,
                ):

                    found += 1

            except Exception:

                logger.exception(
                    "Message processing failed in %s",
                    mood.upper(),
                )

        logger.info(
            "🔎 %s scan completed | detected=%s",
            mood.upper(),
            found,
        )

        return found

    except Exception:

        logger.exception(
            "%s channel scan failed",
            mood.upper(),
        )

        return 0


# ============================================================
# SCAN ALL CHANNELS
# ============================================================

async def scan_all_channels() -> None:

    logger.info(
        "🔎 Starting full channel scan..."
    )

    rebuild_channel_mood_map()

    for mood in MOODS:

        channel = MOOD_CHANNELS.get(
            mood,
            "",
        )

        await scan_one_channel(
            mood,
            channel,
        )

        await asyncio.sleep(
            1
        )

    counts = get_track_counts()

    logger.info(
        "📊 Channel scan result: %s",
        counts,
    )


# ============================================================
# REAL-TIME NEW MUSIC EVENT
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

            # IMPORTANT:
            # New song is immediately saved.
            inserted = save_track(

                mood,

                normalized,

                message_id,
            )

            if inserted:

                logger.info(

                    (
                        "🚀 REAL-TIME NEW MUSIC | "
                        "%s | channel=%s | message=%s"
                    ),

                    mood.upper(),

                    normalized,

                    message_id,
                )

        except Exception:

            logger.exception(
                "Real-time music event error"
            )


# ============================================================
# PERIODIC AUTO SCANNER
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

            logger.info(
                "⏰ Periodic channel rescan started..."
            )

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

        logger.warning(
            (
                "Telethon worker not started: "
                "TELETHON_API_ID / TELETHON_API_HASH / "
                "TELETHON_SESSION missing"
            )
        )

        return

    rebuild_channel_mood_map()

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

    async def runner() -> None:

        assert telethon_client is not None

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
                        "❌ Telethon session is unauthorized"
                    )

                    return

                telethon_ready.set()

                logger.info(
                    "🟢 Telethon CONNECTED"
                )

                # Startup full scan.
                await scan_all_channels()

                # Background 5-minute scanner.
                scanner_task = asyncio.create_task(
                    periodic_scanner()
                )

                logger.info(
                    "👀 Real-time channel watcher is ACTIVE"
                )

                # Stay connected.
                await telethon_client.run_until_disconnected()

            except asyncio.CancelledError:

                raise

            except Exception:

                logger.exception(
                    "❌ Telethon connection error"
                )

            finally:

                telethon_ready.clear()

                if scanner_task:

                    scanner_task.cancel()

                    try:

                        await scanner_task

                    except asyncio.CancelledError:

                        pass

                    except Exception:

                        pass

                try:

                    if telethon_client.is_connected():

                        await telethon_client.disconnect()

                except Exception:

                    logger.exception(
                        "Telethon disconnect error"
                    )

            logger.warning(
                (
                    "🔄 Telethon disconnected. "
                    "Reconnecting in %s seconds..."
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

    lines = [

        "📊 NOT YOUR VIBE",
        "━━━━━━━━━━━━━━━━",
        "",
        f"👥 Users: {get_users_count()}",
        f"🎵 Total Tracks: {total_tracks}",
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
            "🟢 PostgreSQL: ONLINE"
            if db_pool is not None
            else "🔴 PostgreSQL: OFFLINE",

            (
                "🟢 Telethon: CONNECTED"
                if telethon_ready.is_set()
                else
                "🔴 Telethon: DISCONNECTED"
            ),
        ]
    )

    send_message(
        chat_id,
        "\n".join(lines),
    )


# ============================================================
# COMMAND PARSER
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

    chat_id = (
        message.get("chat")
        or {}
    ).get("id")

    user_id = user.get("id")

    if (
        not isinstance(chat_id, int)
        or not isinstance(user_id, int)
    ):
        return

    register_user(
        user
    )

    if data.startswith(
        "mood_"
    ):

        mood = data[5:]

        if mood not in MOODS:

            answer_callback(
                callback_id,
                "Invalid mood",
            )

        elif not set_user_mood(
            user_id,
            mood,
        ):

            answer_callback(
                callback_id,
                "Try again shortly",
            )

        elif schedule_music_request(
            chat_id,
            user_id,
            mood,
        ):

            answer_callback(

                callback_id,

                f"{MOOD_NAMES[mood]} ✓",
            )

        else:

            answer_callback(
                callback_id,
                "⏳ Track is already being prepared",
            )

    elif data == "next_music":

        mood = get_user_mood(
            user_id
        )

        if not mood:

            answer_callback(
                callback_id,
                "Choose mood first",
            )

            send_message(
                chat_id,
                "🎧 Choose your mood 👇",
                mood_menu(),
            )

        elif schedule_music_request(
            chat_id,
            user_id,
            mood,
        ):

            answer_callback(
                callback_id,
                "🔀 Finding next track...",
            )

        else:

            answer_callback(
                callback_id,
                "⏳ A track is already being prepared",
            )

    elif data == "change_mood":

        answer_callback(
            callback_id,
            "🎧 Choose mood",
        )

        send_message(
            chat_id,
            "🎧 Choose your mood 👇",
            mood_menu(),
        )

    else:

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

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    if command == "/start":

        send_message(

            chat_id,

            (
                "🎧 NOT YOUR VIBE MUSIC\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Welcome to your personal "
                "mood music selector. 🔥\n\n"
                "Choose your mood below 👇"
            ),

            mood_menu(),
        )

    # --------------------------------------------------------
    # MOOD
    # --------------------------------------------------------

    elif command == "/mood":

        send_message(

            chat_id,

            (
                "🎛 MOOD SELECTOR\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "What are you feeling right now?"
            ),

            mood_menu(),
        )

    # --------------------------------------------------------
    # NEXT
    # --------------------------------------------------------

    elif command == "/next":

        mood = (
            get_user_mood(
                user_id
            )
            if isinstance(
                user_id,
                int,
            )
            else None
        )

        if not mood:

            send_message(

                chat_id,

                "🎧 အရင်ဆုံး Mood ရွေးပါ 👇",

                mood_menu(),
            )

        elif not schedule_music_request(
            chat_id,
            user_id,
            mood,
        ):

            send_message(

                chat_id,

                "⏳ Track ရှာနေပြီးသားပါ။ ခဏစောင့်ပါ။",
            )

    # --------------------------------------------------------
    # USERS
    # --------------------------------------------------------

    elif command == "/users":

        if is_admin(
            user_id
        ):

            send_message(

                chat_id,

                (
                    "👥 USER STATISTICS\n"
                    "━━━━━━━━━━━━━━━━\n\n"
                    f"Total users: "
                    f"{get_users_count()}"
                ),
            )

        else:

            send_message(
                chat_id,
                "❌ Admin only.",
            )

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    elif command == "/stats":

        if isinstance(
            user_id,
            int,
        ):

            send_stats(
                chat_id,
                user_id,
            )

    # --------------------------------------------------------
    # TELEGRAM STATUS
    # --------------------------------------------------------

    elif command == "/telegram":

        if not is_admin(
            user_id
        ):

            send_message(
                chat_id,
                "❌ Admin only.",
            )

        elif telethon_ready.is_set():

            send_message(

                chat_id,

                (
                    "🟢 TELETHON CONNECTED\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    "📡 Channel watcher: ACTIVE\n"
                    "🔄 Auto reconnect: ON\n"
                    f"⏰ Rescan: every "
                    f"{AUTO_SCAN_INTERVAL // 60} minutes"
                ),
            )

        else:

            send_message(

                chat_id,

                (
                    "🔴 TELETHON DISCONNECTED\n\n"
                    "Render Logs ကို စစ်ပါ။"
                ),
            )

    # --------------------------------------------------------
    # HELP
    # --------------------------------------------------------

    elif command == "/help":

        send_message(

            chat_id,

            (
                "🎧 NOT YOUR VIBE MUSIC BOT\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "/start → Start\n"
                "/mood → Mood menu\n"
                "/next → Next track\n"
                "/users → User count (Admin)\n"
                "/stats → Bot statistics (Admin)\n"
                "/telegram → Telegram status (Admin)\n"
                "/help → Help"
            ),
        )


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

    if isinstance(
        update.get(
            "callback_query"
        ),
        Mapping,
    ):

        handle_callback(
            update[
                "callback_query"
            ]
        )

    elif isinstance(
        update.get(
            "message"
        ),
        Mapping,
    ):

        handle_message(
            update[
                "message"
            ]
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

        logger.warning(
            "Rejected invalid webhook secret"
        )

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
                "Webhook was not configured: "
                "BOT_TOKEN or RENDER_EXTERNAL_URL missing"
            )
        )

        return

    payload: dict[str, Any] = {

        "url":
            (
                f"{RENDER_EXTERNAL_URL.rstrip('/')}"
                "/webhook"
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
            "🔴 Telegram webhook setup failed: %s",
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
        "🎧 NOT YOUR VIBE MUSIC BOT STARTING"
    )

    logger.info(
        "========================================"
    )

    try:

        init_db()

    except Exception:

        logger.exception(
            "❌ PostgreSQL initialization failed"
        )

        return False

    setup_webhook()

    start_telethon_worker()

    logger.info(
        "🟢 Bot server is ready"
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
