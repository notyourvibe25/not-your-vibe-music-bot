from __future__ import annotations

import asyncio
import json
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
from psycopg2.pool import ThreadedConnectionPool, PoolError

from openai import OpenAI

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

logger = logging.getLogger(
    "not_your_vibe_music_bot"
)


# ============================================================
# ENV HELPERS
# ============================================================

def env_text(
    name: str,
    default: str = "",
) -> str:

    return (
        os.getenv(
            name,
            default,
        )
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

DATABASE_URL = env_text(
    "DATABASE_URL"
)

OPENAI_API_KEY = env_text(
    "OPENAI_API_KEY"
)

OPENAI_MODEL = env_text(
    "OPENAI_MODEL",
    "gpt-5-mini",
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
# TELETHON ENV
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
    10,
    1000,
)

RADIO_HISTORY_LIMIT = env_int(
    "RADIO_HISTORY_LIMIT",
    100,
    10,
    1000,
)

RADIO_CANDIDATE_LIMIT = env_int(
    "RADIO_CANDIDATE_LIMIT",
    150,
    20,
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

AI_BATCH_SIZE = env_int(
    "AI_BATCH_SIZE",
    20,
    1,
    100,
)

AI_REQUEST_DELAY = float(
    env_text(
        "AI_REQUEST_DELAY",
        "0.2",
    )
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
# MOOD CHANNELS
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
            "HYPE_CHANNEL",
            "-1004427220481",
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
            "MELODIC_CHANNEL",
            "-1004446996297",
        ),
}


# ============================================================
# GLOBALS
# ============================================================

http_local = threading.local()

db_pool: Optional[
    ThreadedConnectionPool
] = None

db_pool_lock = threading.Lock()

openai_client: Optional[
    OpenAI
] = None

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
                "PostgreSQL pool created"
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
                "Dead PostgreSQL connection. Reconnecting..."
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

        title TEXT,

        artist TEXT,

        caption TEXT,

        ai_classified_at BIGINT,

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

        radio_active BOOLEAN NOT NULL DEFAULT FALSE,

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
        idx_history_user_time
        ON user_history(
            user_id,
            sent_at DESC,
            id DESC
        );


    CREATE INDEX IF NOT EXISTS
        idx_feedback_user
        ON track_feedback(
            user_id,
            feedback
        );


    CREATE INDEX IF NOT EXISTS
        idx_feedback_mood
        ON track_feedback(
            user_id,
            mood,
            feedback
        );

    """

    with (
        db_connection() as connection,
        db_cursor(connection) as cursor
    ):

        cursor.execute(schema)

        cursor.execute(
            """
            ALTER TABLE tracks
            ADD COLUMN IF NOT EXISTS
            title TEXT
            """
        )

        cursor.execute(
            """
            ALTER TABLE tracks
            ADD COLUMN IF NOT EXISTS
            artist TEXT
            """
        )

        cursor.execute(
            """
            ALTER TABLE tracks
            ADD COLUMN IF NOT EXISTS
            caption TEXT
            """
        )

        cursor.execute(
            """
            ALTER TABLE tracks
            ADD COLUMN IF NOT EXISTS
            ai_classified_at BIGINT
            """
        )

        cursor.execute(
            """
            ALTER TABLE user_state
            ADD COLUMN IF NOT EXISTS
            radio_active BOOLEAN
            NOT NULL
            DEFAULT FALSE
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
# OPENAI
# ============================================================

def initialize_openai() -> bool:

    global openai_client

    if not OPENAI_API_KEY:

        logger.warning(
            "OPENAI_API_KEY missing. AI disabled."
        )

        return False

    try:

        openai_client = OpenAI(
            api_key=OPENAI_API_KEY
        )

        logger.info(
            "🟢 OpenAI AI classifier ready | model=%s",
            OPENAI_MODEL,
        )

        return True

    except Exception:

        logger.exception(
            "Could not initialize OpenAI"
        )

        openai_client = None

        return False


def ai_classify_track(
    title: Optional[str],
    artist: Optional[str],
    caption: Optional[str],
) -> Optional[str]:

    if openai_client is None:
        return None

    text = "\n".join(
        [
            f"Title: {title or ''}",
            f"Artist: {artist or ''}",
            f"Caption: {caption or ''}",
        ]
    ).strip()

    if not text:
        return None

    prompt = f"""
You are a music mood classifier for an EDM music bot.

Classify this track into EXACTLY ONE of these moods:

sad
love
chill
hype
dark
energetic
night
melodic

Use only the available metadata.

Do not invent song information.

Guidelines:

sad:
emotional, sad, melancholic, heartbreak

love:
romantic, relationship, intimate, affectionate

chill:
relaxed, laid-back, atmospheric, calm

hype:
festival, party, big drop, crowd energy

dark:
dark bass, aggressive, heavy, sinister

energetic:
high-energy EDM, workout, powerful, driving

night:
late-night, night-drive, neon, city-at-night feeling

melodic:
beautiful melodies, emotional EDM,
melodic bass, uplifting melodic feeling

Return ONLY this JSON:

{{"mood":"one_allowed_mood"}}

Track metadata:

{text}
"""

    try:

        response = openai_client.responses.create(

            model=OPENAI_MODEL,

            input=prompt,

        )

        raw = (
            getattr(
                response,
                "output_text",
                ""
            )
            or ""
        ).strip()

        if not raw:
            return None

        data = json.loads(
            raw
        )

        mood = (
            str(
                data.get(
                    "mood",
                    ""
                )
            )
            .lower()
            .strip()
        )

        if mood in MOODS:

            return mood

        logger.warning(
            "AI returned invalid mood: %s",
            mood,
        )

    except Exception:

        logger.exception(
            "AI classification failed"
        )

    return None


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

                VALUES(%s,%s)

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

    user_id = user.get(
        "id"
    )

    if not isinstance(
        user_id,
        int,
    ):
        return

    try:

        now = int(
            time.time()
        )

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
                ),
            )

    except Exception:

        logger.exception(
            "Could not register user"
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

            return (
                int(
                    row["count"]
                )
                if row
                else 0
            )

    except Exception:

        logger.exception(
            "Could not count users"
        )

        return 0


# ============================================================
# TRACK METADATA
# ============================================================

def extract_track_metadata(
    message: Any,
) -> tuple[
    Optional[str],
    Optional[str],
    Optional[str],
]:

    caption = (
        getattr(
            message,
            "message",
            None,
        )
        or ""
    ).strip()

    title = None
    artist = None

    file_obj = getattr(
        message,
        "file",
        None,
    )

    if file_obj:

        title = (
            getattr(
                file_obj,
                "title",
                None,
            )
            or getattr(
                file_obj,
                "name",
                None,
            )
        )

        artist = getattr(
            file_obj,
            "performer",
            None,
        )

    return (
        title,
        artist,
        caption,
    )


# ============================================================
# SAVE TRACK
# ============================================================

def save_track(
    mood: str,
    channel_id: Any,
    message_id: Any,
    title: Optional[str] = None,
    artist: Optional[str] = None,
    caption: Optional[str] = None,
    ai_classified: bool = False,
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

        ai_time = (
            int(time.time())
            if ai_classified
            else None
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
                    created_at,
                    title,
                    artist,
                    caption,
                    ai_classified_at
                )

                VALUES(
                    %s,%s,%s,%s,%s,%s,%s,%s
                )

                ON CONFLICT(
                    channel_id,
                    message_id
                )

                DO UPDATE SET

                    mood =
                        EXCLUDED.mood,

                    title =
                        COALESCE(
                            EXCLUDED.title,
                            tracks.title
                        ),

                    artist =
                        COALESCE(
                            EXCLUDED.artist,
                            tracks.artist
                        ),

                    caption =
                        COALESCE(
                            EXCLUDED.caption,
                            tracks.caption
                        ),

                    ai_classified_at =
                        COALESCE(
                            EXCLUDED.ai_classified_at,
                            tracks.ai_classified_at
                        )
                """,
                (
                    mood,
                    channel_id,
                    message_id,
                    int(time.time()),
                    title,
                    artist,
                    caption,
                    ai_time,
                ),
            )

        return True

    except Exception:

        logger.exception(
            "Could not save track"
        )

        return False


# ============================================================
# TRACK COUNTS
# ============================================================

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
                int(
                    row["count"]
                )
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
            "Could not collect track counts"
        )

    return result


# ============================================================
# AI FULL RESCAN
# ============================================================

def get_all_tracks_for_ai_rescan() -> list[
    dict[str, Any]
]:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT
                    id,
                    mood,
                    channel_id,
                    message_id,
                    title,
                    artist,
                    caption

                FROM tracks

                ORDER BY id ASC
                """
            )

            return [
                dict(row)
                for row in cursor.fetchall()
            ]

    except Exception:

        logger.exception(
            "Could not load tracks for AI rescan"
        )

        return []


def update_track_ai_result(
    track_id: int,
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
                UPDATE tracks

                SET
                    mood=%s,
                    ai_classified_at=%s

                WHERE id=%s
                """,
                (
                    mood,
                    int(time.time()),
                    track_id,
                ),
            )

        return True

    except Exception:

        logger.exception(
            "Could not update AI result"
        )

        return False


def ai_full_rescan() -> dict[str, int]:

    result = {
        "scanned": 0,
        "classified": 0,
        "failed": 0,
    }

    if openai_client is None:

        logger.warning(
            "AI Full Rescan skipped. OpenAI unavailable."
        )

        return result

    logger.info(
        "🤖 AI FULL RESCAN STARTED"
    )

    tracks = (
        get_all_tracks_for_ai_rescan()
    )

    total = len(
        tracks
    )

    logger.info(
        "🤖 AI Full Rescan found %s tracks",
        total,
    )

    for index, track in enumerate(
        tracks,
        start=1,
    ):

        result["scanned"] += 1

        mood = ai_classify_track(

            track.get(
                "title"
            ),

            track.get(
                "artist"
            ),

            track.get(
                "caption"
            ),

        )

        if not mood:

            result["failed"] += 1

            logger.warning(
                "AI failed | %s/%s | track=%s",
                index,
                total,
                track.get("id"),
            )

            continue

        if update_track_ai_result(

            int(
                track["id"]
            ),

            mood,

        ):

            result["classified"] += 1

            logger.info(
                (
                    "🤖 AI RESCAN | "
                    "%s/%s | track=%s | %s"
                ),
                index,
                total,
                track.get("id"),
                mood.upper(),
            )

        else:

            result["failed"] += 1

        if AI_REQUEST_DELAY > 0:

            time.sleep(
                AI_REQUEST_DELAY
            )

    logger.info(
        (
            "🤖 AI FULL RESCAN COMPLETE | "
            "scanned=%s classified=%s failed=%s"
        ),
        result["scanned"],
        result["classified"],
        result["failed"],
    )

    return result


# ============================================================
# USER STATE
# ============================================================

def set_user_mood(
    user_id: int,
    mood: str,
) -> bool:

    if (
        not isinstance(
            user_id,
            int,
        )
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
                    radio_active,
                    updated_at
                )

                VALUES(
                    %s,%s,FALSE,%s
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
                INSERT INTO user_state(
                    user_id,
                    mood,
                    radio_active,
                    updated_at
                )

                VALUES(
                    %s,NULL,%s,%s
                )

                ON CONFLICT(user_id)

                DO UPDATE SET

                    radio_active =
                        EXCLUDED.radio_active,

                    updated_at =
                        EXCLUDED.updated_at
                """,
                (
                    user_id,
                    active,
                    int(time.time()),
                ),
            )

    except Exception:

        logger.exception(
            "Could not update radio state"
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
                SELECT radio_active
                FROM user_state
                WHERE user_id=%s
                """,
                (user_id,),
            )

            row = cursor.fetchone()

            return bool(
                row
                and row["radio_active"]
            )

    except Exception:

        return False


# ============================================================
# HISTORY
# ============================================================

def add_history(
    user_id: int,
    mood: str,
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

        return True

    except Exception:

        logger.exception(
            "Could not save history"
        )

        return False


def get_recent_history(
    user_id: int,
    limit: int = RECENT_HISTORY_LIMIT,
) -> set[tuple[str, int]]:

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

                ORDER BY
                    sent_at DESC,
                    id DESC

                LIMIT %s
                """,
                (
                    user_id,
                    limit,
                ),
            )

            return {

                (
                    str(
                        row["channel_id"]
                    ),
                    int(
                        row["message_id"]
                    ),
                )

                for row in cursor.fetchall()
            }

    except Exception:

        logger.exception(
            "Could not read history"
        )

        return set()


def get_last_track_for_user(
    user_id: int,
) -> Optional[
    dict[str, Any]
]:

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT
                    mood,
                    channel_id,
                    message_id,
                    sent_at

                FROM user_history

                WHERE user_id=%s

                ORDER BY
                    sent_at DESC,
                    id DESC

                LIMIT 1
                """,
                (user_id,),
            )

            row = cursor.fetchone()

            return (
                dict(row)
                if row
                else None
            )

    except Exception:

        return None


def remove_latest_history(
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
# FEEDBACK
# ============================================================

def save_feedback(
    user_id: int,
    channel_id: str,
    message_id: int,
    feedback: str,
) -> bool:

    if feedback not in {
        "like",
        "skip",
    }:

        return False

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT mood

                FROM tracks

                WHERE channel_id=%s
                AND message_id=%s

                LIMIT 1
                """,
                (
                    channel_id,
                    message_id,
                ),
            )

            track = cursor.fetchone()

            if not track:

                return False

            mood = str(
                track["mood"]
            )

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
                    channel_id,
                    message_id,
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


def save_feedback_from_callback(
    user_id: int,
    callback_data: str,
) -> bool:

    parts = callback_data.split(
        ":"
    )

    if len(parts) != 3:
        return False

    try:

        channel_id = parts[1]

        message_id = int(
            parts[2]
        )

    except ValueError:

        return False

    feedback = parts[0]

    return save_feedback(
        user_id,
        channel_id,
        message_id,
        feedback,
    )


def get_feedback_stats(
    user_id: int,
) -> dict[str, int]:

    result = {
        "like": 0,
        "skip": 0,
    }

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT
                    feedback,
                    COUNT(*) AS count

                FROM track_feedback

                WHERE user_id=%s

                GROUP BY feedback
                """,
                (user_id,),
            )

            for row in cursor.fetchall():

                feedback = row[
                    "feedback"
                ]

                if feedback in result:

                    result[feedback] = int(
                        row["count"]
                    )

    except Exception:

        logger.exception(
            "Could not read feedback"
        )

    return result


def get_liked_tracks(
    user_id: int,
) -> set[tuple[str, int]]:

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

                FROM track_feedback

                WHERE user_id=%s
                AND feedback='like'

                ORDER BY created_at DESC

                LIMIT %s
                """,
                (
                    user_id,
                    RADIO_HISTORY_LIMIT,
                ),
            )

            return {

                (
                    str(
                        row["channel_id"]
                    ),
                    int(
                        row["message_id"]
                    ),
                )

                for row in cursor.fetchall()
            }

    except Exception:

        logger.exception(
            "Could not read liked tracks"
        )

        return set()


# ============================================================
# USER PREFERENCE
# ============================================================

def get_user_preference(
    user_id: int,
) -> dict[str, float]:

    scores = {
        mood: 0.0
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

                FROM user_history

                WHERE user_id=%s

                GROUP BY mood
                """,
                (user_id,),
            )

            for row in cursor.fetchall():

                mood = row["mood"]

                if mood in scores:

                    scores[mood] += (
                        int(
                            row["count"]
                        )
                        * 1.0
                    )

            cursor.execute(
                """
                SELECT
                    mood,
                    COUNT(*) AS count

                FROM track_feedback

                WHERE user_id=%s
                AND feedback='like'

                GROUP BY mood
                """,
                (user_id,),
            )

            for row in cursor.fetchall():

                mood = row["mood"]

                if mood in scores:

                    scores[mood] += (
                        int(
                            row["count"]
                        )
                        * 8.0
                    )

            cursor.execute(
                """
                SELECT
                    mood,
                    COUNT(*) AS count

                FROM track_feedback

                WHERE user_id=%s
                AND feedback='skip'

                GROUP BY mood
                """,
                (user_id,),
            )

            for row in cursor.fetchall():

                mood = row["mood"]

                if mood in scores:

                    scores[mood] -= (
                        int(
                            row["count"]
                        )
                        * 6.0
                    )

    except Exception:

        logger.exception(
            "Could not calculate preferences"
        )

    return scores


# ============================================================
# NORMAL RADIO TRACK
# ============================================================

def reserve_next_track(
    user_id: int,
    mood: str,
) -> Optional[
    tuple[int, str]
]:

    if mood not in MOODS:
        return None

    recent = get_recent_history(
        user_id
    )

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

            rows = cursor.fetchall()

            if not rows:
                return None

            candidates = [

                (
                    int(
                        row["message_id"]
                    ),
                    str(
                        row["channel_id"]
                    ),
                )

                for row in rows

                if (
                    str(
                        row["channel_id"]
                    ),
                    int(
                        row["message_id"]
                    ),
                ) not in recent
            ]

            if not candidates:

                candidates = [

                    (
                        int(
                            row["message_id"]
                        ),
                        str(
                            row["channel_id"]
                        ),
                    )

                    for row in rows
                ]

            return random.choice(
                candidates
            )

    except Exception:

        logger.exception(
            "Could not reserve normal track"
        )

        return None


# ============================================================
# PERSONAL RADIO
# ============================================================

def reserve_radio_track(
    user_id: int,
    preferred_mood: Optional[str],
) -> Optional[
    tuple[int, str, str]
]:

    recent = get_recent_history(
        user_id
    )

    preferences = get_user_preference(
        user_id
    )

    liked_tracks = get_liked_tracks(
        user_id
    )

    mood_scores = dict(
        preferences
    )

    if preferred_mood in MOODS:

        mood_scores[
            preferred_mood
        ] += 10.0

    has_learning_data = any(
        score != 0
        for score in mood_scores.values()
    )

    if not has_learning_data:

        if preferred_mood in MOODS:

            selected_mood = (
                preferred_mood
            )

        else:

            selected_mood = random.choice(
                MOODS
            )

    else:

        positive_scores = {

            mood:
                max(
                    1.0,
                    score + 8.0
                )

            for mood, score
            in mood_scores.items()
        }

        selected_mood = random.choices(

            list(
                positive_scores.keys()
            ),

            weights=list(
                positive_scores.values()
            ),

            k=1,
        )[0]

    try:

        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):

            cursor.execute(
                """
                SELECT
                    message_id,
                    channel_id,
                    mood

                FROM tracks

                WHERE mood=%s

                ORDER BY RANDOM()

                LIMIT %s
                """,
                (
                    selected_mood,
                    RADIO_CANDIDATE_LIMIT,
                ),
            )

            rows = cursor.fetchall()

            if not rows:

                cursor.execute(
                    """
                    SELECT
                        message_id,
                        channel_id,
                        mood

                    FROM tracks

                    ORDER BY RANDOM()

                    LIMIT %s
                    """,
                    (
                        RADIO_CANDIDATE_LIMIT,
                    ),
                )

                rows = cursor.fetchall()

            if not rows:
                return None

            scored = []

            for row in rows:

                channel_id = str(
                    row[
                        "channel_id"
                    ]
                )

                message_id = int(
                    row[
                        "message_id"
                    ]
                )

                mood = str(
                    row[
                        "mood"
                    ]
                )

                key = (
                    channel_id,
                    message_id,
                )

                score = random.uniform(
                    0,
                    5,
                )

                if key in recent:

                    score -= 1000

                if key in liked_tracks:

                    score -= 1000

                if mood == selected_mood:

                    score += 20

                score += (
                    mood_scores.get(
                        mood,
                        0,
                    )
                    * 2
                )

                scored.append(
                    (
                        score,
                        message_id,
                        channel_id,
                        mood,
                    )
                )

            scored.sort(
                key=lambda item: item[0],
                reverse=True,
            )

            top_count = min(
                10,
                len(scored),
            )

            chosen = random.choice(
                scored[:top_count]
            )

            return (
                chosen[1],
                chosen[2],
                chosen[3],
            )

    except Exception:

        logger.exception(
            "Could not reserve radio track"
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

        response = (
            get_http_session().post(

                (
                    "https://api.telegram.org/"
                    f"bot{BOT_TOKEN}/{method}"
                ),

                json=data or {},

                timeout=timeout,
            )
        )

        try:

            result = response.json()

        except ValueError:

            result = {
                "ok": False,
                "description":
                    (
                        "HTTP "
                        f"{response.status_code}"
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
            "Telegram %s failed: %s",
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

    if keyboard:

        data[
            "reply_markup"
        ] = keyboard

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

    payload = {
        "callback_query_id":
            callback_id,
    }

    if text:

        payload[
            "text"
        ] = text

    return telegram(
        "answerCallbackQuery",
        payload,
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
# MENUS
# ============================================================

def start_menu() -> dict[str, Any]:

    return {

        "inline_keyboard": [

            [
                {
                    "text":
                        "😢 SAD",
                    "callback_data":
                        "mood_sad",
                },

                {
                    "text":
                        "❤️ LOVE",
                    "callback_data":
                        "mood_love",
                },
            ],

            [
                {
                    "text":
                        "🌙 CHILL",
                    "callback_data":
                        "mood_chill",
                },

                {
                    "text":
                        "🔥 HYPE",
                    "callback_data":
                        "mood_hype",
                },
            ],

            [
                {
                    "text":
                        "🖤 DARK",
                    "callback_data":
                        "mood_dark",
                },

                {
                    "text":
                        "⚡ ENERGETIC",
                    "callback_data":
                        "mood_energetic",
                },
            ],

            [
                {
                    "text":
                        "🚗 NIGHT DRIVE",
                    "callback_data":
                        "mood_night",
                },

                {
                    "text":
                        "🌌 MELODIC",
                    "callback_data":
                        "mood_melodic",
                },
            ],

            [
                {
                    "text":
                        "📻 PERSONAL RADIO",
                    "callback_data":
                        "start_radio",
                },
            ],
        ]
    }


def mood_menu() -> dict[str, Any]:

    return start_menu()


# ============================================================
# TRACK BUTTONS
# ============================================================

def music_buttons(
    channel_id: str,
    message_id: int,
    radio: bool = False,
) -> dict[str, Any]:

    return {

        "inline_keyboard": [

            [
                {
                    "text":
                        "❤️",
                    "callback_data":
                        (
                            f"like:"
                            f"{channel_id}:"
                            f"{message_id}"
                        ),
                },

                {
                    "text":
                        "😴",
                    "callback_data":
                        (
                            f"skip:"
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
                        (
                            "radio_next"
                            if radio
                            else
                            "next_music"
                        ),
                },

                {
                    "text":
                        (
                            "⏹ STOP"
                            if radio
                            else
                            "📻 RADIO"
                        ),
                    "callback_data":
                        (
                            "stop_radio"
                            if radio
                            else
                            "start_radio"
                        ),
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
# DELIVER TRACK
# ============================================================

def deliver_track(
    chat_id: int,
    user_id: int,
    message_id: int,
    channel_id: str,
    mood: str,
    radio: bool,
) -> bool:

    result = copy_music(

        chat_id,

        channel_id,

        message_id,
    )

    if not result.get("ok"):

        logger.warning(
            "Could not copy track %s/%s",
            channel_id,
            message_id,
        )

        return False

    if not add_history(

        user_id,

        mood,

        channel_id,

        message_id,

    ):

        return False

    if radio:

        text = (
            "📻 PERSONAL RADIO\n"
            "━━━━━━━━━━━━━━━━━━"
        )

    else:

        text = (
            f"{MOOD_NAMES[mood]}\n"
            "━━━━━━━━━━━━━━━━━━"
        )

    send_message(

        chat_id,

        text,

        music_buttons(
            channel_id,
            message_id,
            radio=radio,
        ),
    )

    return True


# ============================================================
# NORMAL NEXT
# ============================================================

def send_next(
    chat_id: int,
    user_id: int,
) -> None:

    mood = get_user_mood(
        user_id
    )

    if not mood:

        send_message(
            chat_id,
            (
                "🎧 Choose a mood "
                "or start Personal Radio."
            ),
            start_menu(),
        )

        return

    if get_track_count(
        mood
    ) <= 0:

        send_message(

            chat_id,

            (
                f"{MOOD_NAMES[mood]}\n\n"
                "No tracks are available "
                "for this mood yet."
            ),

            mood_menu(),
        )

        return

    reserved = reserve_next_track(

        user_id,

        mood,
    )

    if not reserved:

        send_message(

            chat_id,

            "⚠️ I couldn't find another track right now.",

            music_buttons(
                "",
                0,
            ),
        )

        return

    message_id, channel_id = (
        reserved
    )

    delivered = deliver_track(

        chat_id,

        user_id,

        message_id,

        channel_id,

        mood,

        False,
    )

    if not delivered:

        remove_latest_history(

            user_id,

            channel_id,

            message_id,
        )


# ============================================================
# START RADIO
# ============================================================

def start_radio(
    chat_id: int,
    user_id: int,
) -> None:

    mood = get_user_mood(
        user_id
    )

    set_radio_state(
        user_id,
        True,
    )

    reserved = reserve_radio_track(

        user_id,

        mood,
    )

    if not reserved:

        set_radio_state(
            user_id,
            False,
        )

        send_message(

            chat_id,

            (
                "📻 PERSONAL RADIO\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "There are no tracks available yet."
            ),

            start_menu(),
        )

        return

    (
        message_id,
        channel_id,
        selected_mood,
    ) = reserved

    delivered = deliver_track(

        chat_id,

        user_id,

        message_id,

        channel_id,

        selected_mood,

        True,
    )

    if not delivered:

        remove_latest_history(

            user_id,

            channel_id,

            message_id,
        )


# ============================================================
# RADIO NEXT
# ============================================================

def radio_next(
    chat_id: int,
    user_id: int,
) -> None:

    if not is_radio_active(
        user_id
    ):

        start_radio(
            chat_id,
            user_id,
        )

        return

    mood = get_user_mood(
        user_id
    )

    reserved = reserve_radio_track(

        user_id,

        mood,
    )

    if not reserved:

        send_message(

            chat_id,

            "📻 Radio needs more tracks.",

        )

        return

    (
        message_id,
        channel_id,
        selected_mood,
    ) = reserved

    delivered = deliver_track(

        chat_id,

        user_id,

        message_id,

        channel_id,

        selected_mood,

        True,
    )

    if not delivered:

        remove_latest_history(

            user_id,

            channel_id,

            message_id,
        )


# ============================================================
# STOP RADIO
# ============================================================

def stop_radio(
    chat_id: int,
    user_id: int,
) -> None:

    set_radio_state(
        user_id,
        False,
    )

    send_message(

        chat_id,

        (
            "⏹ RADIO STOPPED\n"
            "━━━━━━━━━━━━━━━━━━"
        ),

        start_menu(),
    )


# ============================================================
# MUSIC WORKER
# ============================================================

def music_worker(
    chat_id: int,
    user_id: int,
    action: str,
) -> None:

    try:

        if action == "next":

            send_next(
                chat_id,
                user_id,
            )

        elif action == "radio":

            start_radio(
                chat_id,
                user_id,
            )

        elif action == "radio_next":

            radio_next(
                chat_id,
                user_id,
            )

    except Exception:

        logger.exception(
            "Music worker error"
        )

        send_message(
            chat_id,
            "⚠️ Something went wrong.",
        )

    finally:

        with pending_music_lock:

            pending_music_users.discard(
                user_id
            )


def schedule_music(
    chat_id: int,
    user_id: int,
    action: str,
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

            action,
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


# ============================================================
# TELETHON
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


def normalize_config_channel(
    value: str,
) -> Optional[str]:

    if not value:
        return None

    value = value.strip()

    if value.startswith(
        "-100"
    ):

        return value

    if value.lstrip("-").isdigit():

        number = value.lstrip("-")

        return (
            "-100"
            + number
        )

    return None


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
        + value
    )


def rebuild_channel_mood_map() -> None:

    CHANNEL_MOOD_MAP.clear()

    for mood in MOODS:

        channel = MOOD_CHANNELS.get(
            mood,
            "",
        )

        normalized = (
            normalize_config_channel(
                channel
            )
        )

        if normalized:

            CHANNEL_MOOD_MAP[
                normalized
            ] = mood

    logger.info(
        "Channel map: %s",
        CHANNEL_MOOD_MAP,
    )


def is_music_message(
    message: Any,
) -> bool:

    if not message:
        return False

    if not getattr(
        message,
        "media",
        None,
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
# PROCESS CHANNEL TRACK
# ============================================================

def process_channel_message(
    source_mood: str,
    entity: Any,
    message: Any,
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
        None,
    )

    if (
        not channel_id
        or not message_id
    ):

        return False

    (
        title,
        artist,
        caption,
    ) = extract_track_metadata(
        message
    )

    # --------------------------------------------------------
    # AI CLASSIFICATION
    # --------------------------------------------------------

    ai_mood = ai_classify_track(

        title,

        artist,

        caption,
    )

    if ai_mood in MOODS:

        final_mood = ai_mood

        ai_classified = True

    else:

        final_mood = source_mood

        ai_classified = False

    return save_track(

        final_mood,

        channel_id,

        message_id,

        title,

        artist,

        caption,

        ai_classified=ai_classified,
    )


# ============================================================
# SCAN ONE CHANNEL
# ============================================================

async def scan_one_channel(
    source_mood: str,
    channel_value: str,
) -> int:

    if (
        not channel_value
        or telethon_client is None
    ):

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

        entity = await (
            telethon_client.get_entity(
                lookup
            )
        )

        found = 0

        async for message in (
            telethon_client.iter_messages(
                entity
            )
        ):

            try:

                if process_channel_message(

                    source_mood,

                    entity,

                    message,

                ):

                    found += 1

            except Exception:

                logger.exception(
                    "Channel message processing failed"
                )

        logger.info(
            (
                "🔎 %s scan completed | "
                "processed=%s"
            ),
            source_mood.upper(),
            found,
        )

        return found

    except Exception:

        logger.exception(
            "%s channel scan failed",
            source_mood.upper(),
        )

        return 0


# ============================================================
# FULL CHANNEL SCAN
# ============================================================

async def scan_all_channels() -> None:

    logger.info(
        "🔎 Starting full Telegram channel scan..."
    )

    rebuild_channel_mood_map()

    for mood in MOODS:

        channel = MOOD_CHANNELS.get(
            mood,
            "",
        )

        if channel:

            await scan_one_channel(

                mood,

                channel,
            )

        await asyncio.sleep(
            1
        )

    logger.info(
        "📊 Track counts: %s",
        get_track_counts(),
    )


# ============================================================
# REAL-TIME NEW TRACKS
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

            normalized = (
                normalize_config_channel(
                    str(chat_id)
                )
            )

            if not normalized:
                return

            source_mood = (
                CHANNEL_MOOD_MAP.get(
                    normalized
                )
            )

            if not source_mood:
                return

            message = event.message

            if not is_music_message(
                message
            ):

                return

            (
                title,
                artist,
                caption,
            ) = extract_track_metadata(
                message
            )

            message_id = getattr(
                message,
                "id",
                None,
            )

            if not message_id:
                return

            ai_mood = ai_classify_track(

                title,

                artist,

                caption,
            )

            if ai_mood in MOODS:

                final_mood = ai_mood

                ai_classified = True

            else:

                final_mood = source_mood

                ai_classified = False

            if save_track(

                final_mood,

                normalized,

                message_id,

                title,

                artist,

                caption,

                ai_classified=ai_classified,

            ):

                logger.info(
                    (
                        "🚀 NEW TRACK | "
                        "source=%s | "
                        "AI=%s | "
                        "message=%s"
                    ),
                    source_mood.upper(),
                    final_mood.upper(),
                    message_id,
                )

        except Exception:

            logger.exception(
                "Real-time new track error"
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
                "Periodic scanner error"
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
                "Telethon disabled. "
                "Missing API_ID/API_HASH/SESSION."
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
            "Could not create Telethon client"
        )

        return

    register_telethon_events(
        telethon_client
    )

    async def runner() -> None:

        while True:

            scanner_task = None

            try:

                logger.info(
                    "🔌 Connecting Telethon..."
                )

                await (
                    telethon_client
                    .connect()
                )

                if not await (
                    telethon_client
                    .is_user_authorized()
                ):

                    logger.error(
                        "❌ Telethon session unauthorized"
                    )

                    return

                telethon_ready.set()

                logger.info(
                    "🟢 Telethon connected"
                )

                await scan_all_channels()

                scanner_task = (
                    asyncio.create_task(
                        periodic_scanner()
                    )
                )

                logger.info(
                    "👀 Channel watcher ACTIVE"
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

                    except BaseException:

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

        telethon_thread = (
            threading.Thread(

                target=telethon_worker,

                name="telethon-worker",

                daemon=True,
            )
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

    total = sum(
        counts.values()
    )

    ai_status = (
        "🟢 AI ONLINE"
        if openai_client
        else
        "🔴 AI OFFLINE"
    )

    lines = [

        "📊 NOT YOUR VIBE",

        "━━━━━━━━━━━━━━━━━━",

        "",

        f"👥 Users: {get_users_count()}",

        f"🎵 Tracks: {total}",

        f"🤖 AI: {ai_status}",

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
                "🟢 PostgreSQL: ONLINE"
                if db_pool
                else
                "🔴 PostgreSQL: OFFLINE"
            ),

            (
                "🟢 Telethon: CONNECTED"
                if telethon_ready.is_set()
                else
                "🔴 Telethon: OFFLINE"
            ),
        ]
    )

    send_message(
        chat_id,
        "\n".join(lines),
    )


def send_user_feedback_stats(
    chat_id: int,
    user_id: int,
) -> None:

    stats = get_feedback_stats(
        user_id
    )

    send_message(

        chat_id,

        (
            "🎧 YOUR TASTE\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"❤️ {stats['like']}\n"
            f"😴 {stats['skip']}\n\n"
            "Radio learns from your choices."
        ),
    )


# ============================================================
# COMMAND
# ============================================================

def extract_command(
    text: str,
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
# AI RESCAN THREAD
# ============================================================

def run_admin_rescan(
    chat_id: int,
) -> None:

    try:

        result = ai_full_rescan()

        send_message(

            chat_id,

            (
                "🤖 AI FULL RESCAN COMPLETE\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"🔎 Scanned: {result['scanned']}\n"
                f"✅ Classified: {result['classified']}\n"
                f"⚠️ Failed: {result['failed']}"
            ),
        )

    except Exception:

        logger.exception(
            "Admin AI rescan failed"
        )

        send_message(

            chat_id,

            "❌ AI Full Rescan failed. Check Render logs.",
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
        callback.get(
            "data"
        )
        or ""
    )

    user = (
        callback.get(
            "from"
        )
        or {}
    )

    message = (
        callback.get(
            "message"
        )
        or {}
    )

    chat = (
        message.get(
            "chat"
        )
        or {}
    )

    chat_id = chat.get(
        "id"
    )

    user_id = user.get(
        "id"
    )

    if (
        not isinstance(
            chat_id,
            int,
        )
        or not isinstance(
            user_id,
            int,
        )
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
                "Please try again",
            )

            return

        answer_callback(
            callback_id,
            MOOD_NAMES[mood],
        )

        # IMPORTANT:
        # Mood selection immediately sends a track.

        schedule_music(

            chat_id,

            user_id,

            "next",
        )

        return

    # ========================================================
    # LIKE
    # ========================================================

    if data.startswith(
        "like:"
    ):

        saved = save_feedback_from_callback(

            user_id,

            data,
        )

        answer_callback(

            callback_id,

            "❤️"
            if saved
            else
            "⚠️",
        )

        return

    # ========================================================
    # SKIP
    # ========================================================

    if data.startswith(
        "skip:"
    ):

        saved = save_feedback_from_callback(

            user_id,

            data,
        )

        answer_callback(

            callback_id,

            "😴"
            if saved
            else
            "⚠️",
        )

        return

    # ========================================================
    # NORMAL NEXT
    # ========================================================

    if data == "next_music":

        answer_callback(
            callback_id,
            "⏭",
        )

        if not schedule_music(

            chat_id,

            user_id,

            "next",
        ):

            answer_callback(
                callback_id,
                "⏳",
            )

        return

    # ========================================================
    # RADIO
    # ========================================================

    if data == "start_radio":

        answer_callback(
            callback_id,
            "📻",
        )

        if not schedule_music(

            chat_id,

            user_id,

            "radio",
        ):

            answer_callback(
                callback_id,
                "⏳",
            )

        return

    # ========================================================
    # RADIO NEXT
    # ========================================================

    if data == "radio_next":

        answer_callback(
            callback_id,
            "⏭",
        )

        if not schedule_music(

            chat_id,

            user_id,

            "radio_next",
        ):

            answer_callback(
                callback_id,
                "⏳",
            )

        return

    # ========================================================
    # STOP RADIO
    # ========================================================

    if data == "stop_radio":

        answer_callback(
            callback_id,
            "⏹",
        )

        stop_radio(

            chat_id,

            user_id,
        )

        return

    # ========================================================
    # CHANGE MOOD
    # ========================================================

    if data == "change_mood":

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
# MESSAGE HANDLER
# ============================================================

def handle_message(
    message: Mapping[str, Any],
) -> None:

    chat = (
        message.get(
            "chat"
        )
        or {}
    )

    user = (
        message.get(
            "from"
        )
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
        message.get(
            "text"
        )
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
                "Your personal music space.\n\n"
                "Choose a mood or start "
                "Personal Radio."
            ),

            start_menu(),
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

        if not schedule_music(

            chat_id,

            user_id,

            "next",
        ):

            send_message(
                chat_id,
                "⏳ Already preparing your next track.",
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

        if not schedule_music(

            chat_id,

            user_id,

            "radio",
        ):

            send_message(
                chat_id,
                "⏳ Radio is already loading.",
            )

        return

    # ========================================================
    # STOP
    # ========================================================

    if command == "/stop":

        if isinstance(
            user_id,
            int,
        ):

            stop_radio(

                chat_id,

                user_id,
            )

        return

    # ========================================================
    # TASTE
    # ========================================================

    if command in {
        "/taste",
        "/mytaste",
    }:

        if isinstance(
            user_id,
            int,
        ):

            send_user_feedback_stats(

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
    # AI FULL RESCAN
    # ========================================================

    if command == "/airescan":

        if not is_admin(
            user_id
        ):

            send_message(
                chat_id,
                "❌ Admin only.",
            )

            return

        if openai_client is None:

            send_message(

                chat_id,

                (
                    "❌ AI is offline.\n\n"
                    "Check OPENAI_API_KEY "
                    "in Render Environment."
                ),
            )

            return

        send_message(

            chat_id,

            (
                "🤖 AI FULL RESCAN STARTED\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "I am reading the existing tracks "
                "and classifying their moods.\n\n"
                "You can wait for the completion message."
            ),
        )

        threading.Thread(

            target=run_admin_rescan,

            args=(chat_id,),

            name="ai-full-rescan",

            daemon=True,

        ).start()

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
                    "📡 Real-time watcher: ACTIVE\n"
                    "🔄 Auto reconnect: ON\n"
                    f"⏰ Backup scan: every "
                    f"{AUTO_SCAN_INTERVAL // 60} minutes"
                ),
            )

        else:

            send_message(

                chat_id,

                (
                    "🔴 TELETHON OFFLINE\n\n"
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
                "🎧 NOT YOUR VIBE\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "/start → Start\n"
                "/mood → Choose mood\n"
                "/next → Next track\n"
                "/radio → Personal Radio\n"
                "/stop → Stop Radio\n"
                "/taste → Your taste\n"
                "/airescan → AI Full Rescan (Admin)\n"
                "/users → User count (Admin)\n"
                "/stats → Bot statistics (Admin)\n"
                "/telegram → Telegram status (Admin)"
            ),
        )

        return


# ============================================================
# UPDATE HANDLER
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


@app.route(
    "/health"
)
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
                "Webhook not configured: "
                "BOT_TOKEN or Render URL missing"
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

    if result.get(
        "ok"
    ):

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
        "=========================================="
    )

    logger.info(
        "🎧 NOT YOUR VIBE MUSIC BOT STARTING"
    )

    logger.info(
        "=========================================="
    )

    if not BOT_TOKEN:

        logger.error(
            "❌ BOT_TOKEN is missing"
        )

        return False

    if not DATABASE_URL:

        logger.error(
            "❌ DATABASE_URL is missing"
        )

        return False

    try:

        init_db()

    except Exception:

        logger.exception(
            "❌ PostgreSQL initialization failed"
        )

        return False

    initialize_openai()

    setup_webhook()

    start_telethon_worker()

    logger.info(
        "🟢 Bot server ready"
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
