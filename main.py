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

logger = logging.getLogger("not-your-vibe-bot")


# ============================================================
# ENV HELPERS
# ============================================================

def env_text(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def env_first(*names: str) -> str:
    for name in names:
        value = env_text(name)
        if value:
            return value
    return ""


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
# MAIN ENVIRONMENT
# ============================================================

BOT_TOKEN = env_text("BOT_TOKEN")

ADMIN_USER_ID = env_text("ADMIN_USER_ID")

DATABASE_URL = env_text("DATABASE_URL")

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
# TELETHON CREDENTIALS
#
# IMPORTANT:
#
# ဒီ code က အောက်ပါ naming နှစ်မျိုးလုံးကို
# လက်ခံပါတယ်။
#
# TELETHON_API_ID / TELETHON_API_HASH
#
# သို့မဟုတ်
#
# TELEGRAM_API_ID / TELEGRAM_API_HASH
#
# သို့မဟုတ်
#
# API_ID / API_HASH
# ============================================================

TELETHON_API_ID = env_first(
    "TELETHON_API_ID",
    "TELEGRAM_API_ID",
    "API_ID",
)

TELETHON_API_HASH = env_first(
    "TELETHON_API_HASH",
    "TELEGRAM_API_HASH",
    "API_HASH",
)

TELETHON_SESSION = env_first(
    "TELETHON_SESSION",
    "TELEGRAM_SESSION",
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
    100,
    10,
    1000,
)

WEBHOOK_MAX_CONNECTIONS = env_int(
    "WEBHOOK_MAX_CONNECTIONS",
    40,
    1,
    100,
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
    "sad": "😢 SAD",
    "love": "❤️ LOVE",
    "chill": "🌙 CHILL",
    "hype": "🔥 HYPE",
    "dark": "🖤 DARK",
    "energetic": "⚡ ENERGETIC",
    "night": "🚗 NIGHT DRIVE",
    "melodic": "🌌 MELODIC",
}


# ============================================================
# MOOD CHANNELS
#
# USERNAME ရှိတဲ့ channel တွေကို
# @username နဲ့ တိုက်ရိုက်ထည့်နိုင်ပါတယ်။
#
# HYPE က private invite link ဖြစ်တဲ့အတွက်
# HYPE_CHANNEL ကို Render မှာ numeric ID ထည့်ပါ။
#
# ဥပမာ:
#
# SAD_CHANNEL=@sadmooddatabase
# LOVE_CHANNEL=@lovemooddatabase
# CHILL_CHANNEL=@chillmooddatabase
#
# HYPE_CHANNEL=-1001234567890
#
# ============================================================

MOOD_CHANNELS = {

    "sad": env_text(
        "SAD_CHANNEL",
        "@sadmooddatabase",
    ),

    "love": env_text(
        "LOVE_CHANNEL",
        "@lovemooddatabase",
    ),

    "chill": env_text(
        "CHILL_CHANNEL",
        "@chillmooddatabase",
    ),

    "hype": env_text(
        "HYPE_CHANNEL",
    ),

    "dark": env_text(
        "DARK_CHANNEL",
        "@darkmooddatabase",
    ),

    "energetic": env_text(
        "ENERGETIC_CHANNEL",
        "@energeticmooddatabase",
    ),

    "night": env_text(
        "NIGHT_CHANNEL",
    ),

    "melodic": env_text(
        "MELODIC_CHANNEL",
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
    thread_name_prefix="music-worker",
)

pending_music_users: set[int] = set()

pending_music_lock = threading.Lock()

CHANNEL_MOOD_MAP: dict[str, str] = {}


# ============================================================
# DATABASE
# ============================================================

def normalize_database_url(
    url: str,
) -> str:

    if url.startswith("postgres://"):
        return (
            "postgresql://"
            + url[len("postgres://"):]
        )

    return url


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
            "🟢 PostgreSQL pool ready"
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
                cursor.execute("SELECT 1")

        except (
            OperationalError,
            InterfaceError,
        ):

            logger.warning(
                "Dead PostgreSQL connection detected"
            )

            try:
                db_pool.putconn(
                    connection,
                    close=True,
                )
            except Exception:
                pass

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

    CREATE TABLE IF NOT EXISTS user_radio_state (
        user_id BIGINT PRIMARY KEY,
        active BOOLEAN NOT NULL DEFAULT FALSE,
        updated_at BIGINT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS processed_updates (
        update_id BIGINT PRIMARY KEY,
        processed_at BIGINT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_tracks_mood
    ON tracks(mood);

    CREATE INDEX IF NOT EXISTS idx_history_user_mood_time
    ON user_history(
        user_id,
        mood,
        sent_at DESC,
        id DESC
    );

    CREATE INDEX IF NOT EXISTS idx_history_user_time
    ON user_history(
        user_id,
        sent_at DESC,
        id DESC
    );

    CREATE INDEX IF NOT EXISTS idx_processed_updates_time
    ON processed_updates(
        processed_at
    );
    """

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
        "🟢 PostgreSQL database ready"
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
                VALUES(%s, %s)
                ON CONFLICT(update_id)
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
            "Update deduplication failed"
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

    if not isinstance(user_id, int):
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
                    total_requests=
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
                "SELECT COUNT(*) AS count FROM users"
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
                VALUES(%s,%s,%s,%s)
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

            inserted = (
                cursor.fetchone()
                is not None
            )

            if inserted:

                logger.info(
                    "🎵 NEW TRACK | %s | %s | message=%s",
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
                SELECT mood, COUNT(*) AS count
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
            "Could not save mood"
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


# ============================================================
# RADIO
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

        return True

    except Exception:

        logger.exception(
            "Could not update radio"
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
# TRACK RESERVATION
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

                cursor.execute(
                    """
                    SELECT pg_advisory_xact_lock(%s)
                    """,
                    (user_id,),
                )

                cursor.execute(
                    """
                    SELECT channel_id, message_id
                    FROM user_history
                    WHERE user_id=%s
                    AND mood=%s
                    ORDER BY sent_at DESC, id DESC
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
                    SELECT message_id, channel_id
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
                    VALUES(%s,%s,%s,%s,%s)
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

                cursor.execute(
                    """
                    SELECT mood, COUNT(*) AS listens
                    FROM user_history
                    WHERE user_id=%s
                    GROUP BY mood
                    ORDER BY listens DESC,
                             MAX(sent_at) DESC
                    LIMIT 4
                    """,
                    (user_id,),
                )

                favorite_moods = [
                    row["mood"]
                    for row in cursor.fetchall()
                    if row["mood"] in MOODS
                ]

                selected_mood = get_user_mood(
                    user_id
                )

                if (
                    selected_mood
                    and selected_mood
                    not in favorite_moods
                ):
                    favorite_moods.insert(
                        0,
                        selected_mood,
                    )

                if not favorite_moods:

                    favorite_moods = list(
                        MOODS
                    )

                cursor.execute(
                    """
                    SELECT channel_id, message_id
                    FROM user_history
                    WHERE user_id=%s
                    ORDER BY sent_at DESC, id DESC
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

                for mood in favorite_moods:

                    cursor.execute(
                        """
                        SELECT message_id, channel_id
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
                        VALUES(%s,%s,%s,%s,%s)
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

                return None

    except Exception:

        logger.exception(
            "Could not reserve radio track"
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
                WHERE id=(
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
                    "NOT-YOUR-VIBE-MUSIC-BOT/4.0"
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


def answer_callback(
    callback_id: Any,
    text: str = "",
) -> dict[str, Any]:

    if not callback_id:
        return {"ok": False}

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
# UI
# ============================================================

def mood_menu() -> dict[str, Any]:

    return {
        "inline_keyboard": [

            [
                {
                    "text": "😢  SAD",
                    "callback_data": "mood_sad",
                },
                {
                    "text": "❤️  LOVE",
                    "callback_data": "mood_love",
                },
            ],

            [
                {
                    "text": "🌙  CHILL",
                    "callback_data": "mood_chill",
                },
                {
                    "text": "🔥  HYPE",
                    "callback_data": "mood_hype",
                },
            ],

            [
                {
                    "text": "🖤  DARK",
                    "callback_data": "mood_dark",
                },
                {
                    "text": "⚡  ENERGETIC",
                    "callback_data": "mood_energetic",
                },
            ],

            [
                {
                    "text": "🚗  NIGHT DRIVE",
                    "callback_data": "mood_night",
                },
                {
                    "text": "🌌  MELODIC",
                    "callback_data": "mood_melodic",
                },
            ],

            [
                {
                    "text": "📻  START MY RADIO",
                    "callback_data": "radio_start",
                },
            ],
        ]
    }


def music_buttons(
    radio: bool = False,
) -> dict[str, Any]:

    if radio:

        return {
            "inline_keyboard": [

                [
                    {
                        "text": "⏭  NEXT RADIO",
                        "callback_data": "next_music",
                    },
                ],

                [
                    {
                        "text": "🎛  CHANGE MOOD",
                        "callback_data": "change_mood",
                    },
                ],

                [
                    {
                        "text": "⏹  STOP RADIO",
                        "callback_data": "radio_stop",
                    },
                ],
            ]
        }

    return {
        "inline_keyboard": [

            [
                {
                    "text": "⏭  NEXT TRACK",
                    "callback_data": "next_music",
                },
                {
                    "text": "📻  RADIO",
                    "callback_data": "radio_start",
                },
            ],

            [
                {
                    "text": "🎛  CHANGE MOOD",
                    "callback_data": "change_mood",
                },
            ],
        ]
    }


# ============================================================
# SEND MUSIC
# ============================================================

def send_music(
    chat_id: int,
    user_id: int,
    mood: str,
) -> None:

    if mood not in MOODS:
        send_message(
            chat_id,
            "⚠️ Invalid mood.",
            mood_menu(),
        )
        return

    count = get_track_count(mood)

    if count <= 0:

        send_message(
            chat_id,
            (
                f"{MOOD_NAMES[mood]}\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "⚠️ ဒီ mood ထဲမှာ music မရှိသေးပါ။\n\n"
                "Channel ထဲ song ထည့်လိုက်ရင် "
                "Bot က auto scan လုပ်ပြီး ထည့်ပေးပါမယ်။"
            ),
            mood_menu(),
        )

        return

    for _ in range(min(count, 10)):

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
                    "🎧 TRACK DELIVERED\n\n"
                    "Enjoy your music. ✨"
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
            "⚠️ Track ကို အခုအချိန်မှာ "
            "copy လုပ်လို့မရသေးပါ။"
        ),
        music_buttons(),
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
                "⚠️ Radio အတွက် track မတွေ့သေးပါ။\n\n"
                "Mood တစ်ခုရွေးပြီး သီချင်းအနည်းငယ် "
                "နားထောင်ပြီးရင် Radio က ပိုကောင်းလာပါမယ်။"
            ),
            music_buttons(radio=True),
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
                f"{MOOD_NAMES.get(mood, mood)}\n\n"
                "🎧 Recommended for you\n\n"
                "Based on your mood & listening history. ✨"
            ),
            music_buttons(radio=True),
        )

        return

    remove_failed_history(
        user_id,
        channel_id,
        message_id,
    )

    send_message(
        chat_id,
        "⚠️ Track copy failed. NEXT ကိုနှိပ်ပြီး ပြန်စမ်းပါ။",
        music_buttons(radio=True),
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

        with pending_music_lock:
            pending_music_users.discard(
                user_id
            )


def schedule_music(
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

        logger.exception(
            "Could not schedule music"
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

    if value.startswith("@"):
        return value.lower()

    if value.startswith("-100"):
        return value

    if value.lstrip("-").isdigit():

        number = value.lstrip("-")

        return f"-100{number}"

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
        "📡 Mood channel map: %s",
        CHANNEL_MOOD_MAP,
    )


# ============================================================
# MUSIC DETECTION
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
# TELETHON MESSAGE SAVE
# ============================================================

def save_telethon_message(
    mood: str,
    entity: Any,
    message: Any,
) -> bool:

    if not is_music_message(message):
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
# SCAN CHANNEL
# ============================================================

async def scan_one_channel(
    mood: str,
    channel_value: str,
) -> int:

    if not channel_value:
        logger.warning(
            "%s channel not configured",
            mood.upper(),
        )
        return 0

    if telethon_client is None:
        return 0

    try:

        value = channel_value.strip()

        if value.lstrip("-").isdigit():

            lookup: Any = int(value)

        else:

            lookup = value

        logger.info(
            "🔎 Scanning %s channel: %s",
            mood.upper(),
            value,
        )

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
                    "Message scan error"
                )

        logger.info(
            "✅ %s scan complete | new=%s",
            mood.upper(),
            found,
        )

        return found

    except Exception:

        logger.exception(
            "❌ %s channel scan failed",
            mood.upper(),
        )

        return 0


# ============================================================
# SCAN ALL 8 CHANNELS
# ============================================================

async def scan_all_channels() -> None:

    logger.info(
        "========================================"
    )

    logger.info(
        "🔎 FULL 8-MOOD CHANNEL SCAN STARTED"
    )

    logger.info(
        "========================================"
    )

    rebuild_channel_mood_map()

    for mood in MOODS:

        channel = MOOD_CHANNELS.get(
            mood,
            "",
        )

        if not channel:
            logger.warning(
                "⚠️ %s has no channel configured",
                mood.upper(),
            )
            continue

        await scan_one_channel(
            mood,
            channel,
        )

        await asyncio.sleep(1)

    counts = get_track_counts()

    logger.info(
        "📊 TRACK DATABASE: %s",
        counts,
    )

    logger.info(
        "🟢 FULL CHANNEL SCAN FINISHED"
    )


# ============================================================
# REAL-TIME NEW SONG WATCHER
# ============================================================

def register_telethon_events(
    client: TelegramClient,
) -> None:

    @client.on(
        events.NewMessage()
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

            entity = await event.get_chat()

            normalized_id = normalize_channel_id(
                entity
            )

            username = (
                getattr(
                    entity,
                    "username",
                    None,
                )
                or ""
            )

            username_key = (
                f"@{username}".lower()
                if username
                else ""
            )

            mood = (
                CHANNEL_MOOD_MAP.get(
                    normalized_id or ""
                )
                or CHANNEL_MOOD_MAP.get(
                    username_key
                )
            )

            if not mood:
                return

            message = event.message

            if not is_music_message(message):
                return

            message_id = getattr(
                message,
                "id",
                None,
            )

            if not message_id:
                return

            channel_key = (
                normalized_id
                or username_key
            )

            if save_track(
                mood,
                channel_key,
                message_id,
            ):

                logger.info(
                    (
                        "🚀 NEW SONG AUTO ADDED "
                        "| %s | %s"
                    ),
                    mood.upper(),
                    message_id,
                )

        except Exception:

            logger.exception(
                "Real-time channel watcher error"
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
                "⏰ Backup channel rescan..."
            )

            await scan_all_channels()

        except asyncio.CancelledError:
            return

        except Exception:

            logger.exception(
                "Periodic scanner error"
            )

            await asyncio.sleep(10)


# ============================================================
# TELETHON WORKER
# ============================================================

def telethon_worker() -> None:

    global telethon_client

    if not TELETHON_API_ID:

        logger.warning(
            "⚠️ TELETHON_API_ID / TELEGRAM_API_ID / API_ID missing"
        )

        return

    if not TELETHON_API_HASH:

        logger.warning(
            "⚠️ TELETHON_API_HASH / TELEGRAM_API_HASH / API_HASH missing"
        )

        return

    if not TELETHON_SESSION:

        logger.warning(
            "⚠️ TELETHON_SESSION missing"
        )

        return

    rebuild_channel_mood_map()

    try:

        telethon_client = TelegramClient(
            StringSession(
                TELETHON_SESSION
            ),
            int(TELETHON_API_ID),
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

                authorized = await (
                    telethon_client
                    .is_user_authorized()
                )

                if not authorized:

                    logger.error(
                        (
                            "❌ Telethon session "
                            "is NOT authorized"
                        )
                    )

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
                    "👀 REAL-TIME WATCHER ACTIVE"
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
                "🔄 Telethon reconnecting in %s seconds",
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

    if not is_admin(requester_id):

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
        f"🎵 Total Tracks: {total}",
        "",
    ]

    for mood in MOODS:

        lines.append(
            f"{MOOD_NAMES[mood]} → {counts[mood]}"
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

    callback_id = callback.get("id")

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

    chat_id = chat.get("id")
    user_id = user.get("id")

    if (
        not isinstance(chat_id, int)
        or not isinstance(user_id, int)
    ):
        return

    register_user(user)

    # --------------------------------------------------------
    # MOOD
    # --------------------------------------------------------

    if data.startswith("mood_"):

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

        if not set_user_mood(
            user_id,
            mood,
        ):

            answer_callback(
                callback_id,
                "Try again",
            )

            return

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

    # --------------------------------------------------------
    # RADIO START
    # --------------------------------------------------------

    if data == "radio_start":

        set_radio_state(
            user_id,
            True,
        )

        answer_callback(
            callback_id,
            "📻 Radio ON",
        )

        send_message(
            chat_id,
            (
                "📻 MY RADIO\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Personal Radio is ON. ✨\n\n"
                "▶️ Finding your first track..."
            ),
        )

        if not schedule_radio(
            chat_id,
            user_id,
        ):

            send_message(
                chat_id,
                "⏳ Radio is already preparing a track.",
                music_buttons(radio=True),
            )

        return

    # --------------------------------------------------------
    # RADIO STOP
    # --------------------------------------------------------

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
                "⏹ RADIO OFF\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Personal Radio stopped."
            ),
            mood_menu(),
        )

        return

    # --------------------------------------------------------
    # NEXT
    # --------------------------------------------------------

    if data == "next_music":

        if is_radio_active(user_id):

            answer_callback(
                callback_id,
                "📻 Finding next track...",
            )

            if not schedule_radio(
                chat_id,
                user_id,
            ):

                answer_callback(
                    callback_id,
                    "⏳ Already preparing.",
                )

            return

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

            return

        answer_callback(
            callback_id,
            "🔀 Finding next track...",
        )

        if not schedule_music(
            chat_id,
            user_id,
            mood,
        ):

            answer_callback(
                callback_id,
                "⏳ Already preparing.",
            )

        return

    # --------------------------------------------------------
    # CHANGE MOOD
    # --------------------------------------------------------

    if data == "change_mood":

        set_radio_state(
            user_id,
            False,
        )

        answer_callback(
            callback_id,
            "🎧 Choose your mood",
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

    chat_id = chat.get("id")
    user_id = user.get("id")

    if not isinstance(chat_id, int):
        return

    register_user(user)

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
                "💎 NOT YOUR VIBE MUSIC\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Welcome to your personal "
                "mood music experience. 🎧\n\n"
                "Choose a mood and discover "
                "your next track.\n\n"
                "📻 You can also start your "
                "Personal Radio.\n\n"
                "👇 SELECT YOUR MOOD"
            ),
            mood_menu(),
        )

        return

    # --------------------------------------------------------
    # MOOD
    # --------------------------------------------------------

    if command == "/mood":

        send_message(
            chat_id,
            (
                "🎛 MOOD SELECTOR\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "What are you feeling right now?\n\n"
                "👇 Choose your mood"
            ),
            mood_menu(),
        )

        return

    # --------------------------------------------------------
    # NEXT
    # --------------------------------------------------------

    if command == "/next":

        if not isinstance(user_id, int):
            return

        if is_radio_active(user_id):

            if not schedule_radio(
                chat_id,
                user_id,
            ):

                send_message(
                    chat_id,
                    "⏳ Radio track is already being prepared.",
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

    # --------------------------------------------------------
    # RADIO
    # --------------------------------------------------------

    if command == "/radio":

        if not isinstance(user_id, int):
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
                "Personal Radio is ON. ✨\n\n"
                "Your listening history will "
                "guide the next tracks."
            ),
            music_buttons(radio=True),
        )

        if not schedule_radio(
            chat_id,
            user_id,
        ):

            send_message(
                chat_id,
                "⏳ Radio is already preparing.",
            )

        return

    # --------------------------------------------------------
    # STOP RADIO
    # --------------------------------------------------------

    if command == "/stopradio":

        if isinstance(user_id, int):

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

    # --------------------------------------------------------
    # USERS
    # --------------------------------------------------------

    if command == "/users":

        if is_admin(user_id):

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

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    if command == "/stats":

        if isinstance(user_id, int):

            send_stats(
                chat_id,
                user_id,
            )

        return

    # --------------------------------------------------------
    # TELEGRAM STATUS
    # --------------------------------------------------------

    if command == "/telegram":

        if not is_admin(user_id):

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
                    "🚀 New song auto-add: ON\n"
                    "🔄 Auto reconnect: ON\n"
                    f"⏰ Backup scan: "
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

        return

    # --------------------------------------------------------
    # HELP
    # --------------------------------------------------------

    if command == "/help":

        send_message(
            chat_id,
            (
                "💎 NOT YOUR VIBE MUSIC\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "/start → Start\n"
                "/mood → Mood selector\n"
                "/next → Next track\n"
                "/radio → Personal Radio\n"
                "/stopradio → Stop Radio\n\n"
                "👑 ADMIN\n"
                "/users → User count\n"
                "/stats → Statistics\n"
                "/telegram → Telethon status"
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
# FLASK
# ============================================================

@app.route("/")
def home() -> str:

    return (
        "💎 NOT YOUR VIBE MUSIC BOT ONLINE"
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

                cursor.execute("SELECT 1")
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
            "Webhook processing error"
        )

    return "OK", 200


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
                "Webhook not configured: "
                "BOT_TOKEN or RENDER_EXTERNAL_URL missing"
            )
        )

        return

    payload: dict[str, Any] = {

        "url": (
            f"{RENDER_EXTERNAL_URL.rstrip('/')}"
            "/webhook"
        ),

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
        "TELETHON_API_ID: %s",
        "OK" if TELETHON_API_ID else "MISSING",
    )

    logger.info(
        "TELETHON_API_HASH: %s",
        "OK" if TELETHON_API_HASH else "MISSING",
    )

    logger.info(
        "TELETHON_SESSION: %s",
        "OK" if TELETHON_SESSION else "MISSING",
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
                "📡 %s → %s",
                mood.upper(),
                channel,
            )

        else:

            logger.warning(
                "⚠️ %s → NOT CONFIGURED",
                mood.upper(),
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
