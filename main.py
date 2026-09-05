from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterator, Mapping, Optional
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request
from psycopg2 import InterfaceError, OperationalError
from psycopg2.extras import RealDictCursor
from psycopg2.pool import PoolError, ThreadedConnectionPool
from telethon import TelegramClient, events
from telethon.sessions import StringSession


# ============================================================
# APP / LOGGING
# ============================================================

app = Flask(__name__)

logging.basicConfig(
    level=(os.getenv("LOG_LEVEL") or "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
)

log = logging.getLogger("nyv")


# ============================================================
# HELPERS
# ============================================================

def env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def geti(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(env(name, str(default)))
    except (TypeError, ValueError):
        return default

    return value if lo <= value <= hi else default


def norm_db(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql://" + url[11:]
    return url


def normch(value: Any) -> Optional[str]:
    """
    Normalize Telegram channel IDs.

    Examples:
        -100123456789 -> -100123456789
        123456789    -> -100123456789
    """
    value = str(value or "").strip()

    if not value:
        return None

    if value.startswith("-100") and value[1:].isdigit():
        return value

    if value.lstrip("-").isdigit():
        return "-100" + value.lstrip("-")

    return None


# ============================================================
# ENVIRONMENT
# ============================================================

BOT_TOKEN = env("BOT_TOKEN")
ADMIN_USER_ID = env("ADMIN_USER_ID")
DATABASE_URL = env("DATABASE_URL")

RENDER_EXTERNAL_URL = env("RENDER_EXTERNAL_URL") or (
    ("https://" + env("RENDER_EXTERNAL_HOSTNAME"))
    if env("RENDER_EXTERNAL_HOSTNAME")
    else ""
)

WEBHOOK_SECRET = env("TELEGRAM_WEBHOOK_SECRET")

API_ID = env("TELETHON_API_ID") or env("API_ID")
API_HASH = env("TELETHON_API_HASH") or env("API_HASH")
SESSION = env("TELETHON_SESSION")

HTTP_TIMEOUT = geti("TELEGRAM_HTTP_TIMEOUT", 20, 5, 120)
WORKERS = geti("MUSIC_WORKER_COUNT", 4, 1, 12)
POOL_MAX = geti("DB_POOL_MAX_CONNECTIONS", 8, 2, 30)

SCAN_INTERVAL = geti("AUTO_SCAN_INTERVAL", 300, 60, 3600)
RECONNECT = geti("TELETHON_RECONNECT_DELAY", 10, 3, 120)

HISTORY_LIMIT = geti("RADIO_HISTORY_LIMIT", 100, 10, 1000)


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

INFO = {
    "sad": (
        "😢 SAD",
        "Stay with the feeling.\nLet the music say what words can't.",
    ),
    "love": (
        "❤️ LOVE",
        "For the moments that make your heart beats a little faster.",
    ),
    "chill": (
        "🌙 CHILL",
        "Slow down, breathe in,\nand let the world fade away.",
    ),
    "hype": (
        "🔥 HYPE",
        "Turn it up.\nYour energy starts here.",
    ),
    "dark": (
        "🖤 DARK",
        "Enter the darker side.\nHeavy bass. Brutal drops. No mercy.",
    ),
    "energetic": (
        "⚡ ENERGETIC",
        "No limits. No brakes.\nJust pure energy.",
    ),
    "night": (
        "🚗 NIGHT DRIVE",
        "Lights outside.\nMusic inside. Keep moving.",
    ),
    "melodic": (
        "🌌 MELODIC",
        "Close your eyes and let the melody take you somewhere else.",
    ),
}


# ============================================================
# CHANNELS
# ============================================================

CHANNELS = {
    mood: env(mood.upper() + "_CHANNEL")
    for mood in MOODS
}

# Existing defaults
CHANNELS["hype"] = env("HYPE_CHANNEL", "-1004427220481")
CHANNELS["melodic"] = env("MELODIC_CHANNEL", "-1004446996297")


AUDIO = (
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
# GLOBAL STATE
# ============================================================

db_pool = None
db_lock = threading.Lock()

client: Optional[TelegramClient] = None
tele_loop = None

ready = threading.Event()

tele_thread = None
tele_lock = threading.Lock()

executor = ThreadPoolExecutor(
    max_workers=WORKERS,
    thread_name_prefix="music",
)

pending = set()
pending_lock = threading.Lock()

channel_map = {}
last_scan = 0

http_local = threading.local()


# ============================================================
# CACHE
# ============================================================

register_cache = {}
register_cache_lock = threading.Lock()

COUNTS_TTL = 15

_counts_cache = None
_counts_cache_at = 0.0
_counts_cache_lock = threading.Lock()


# ============================================================
# DATABASE
# ============================================================

@contextmanager
def db() -> Iterator[Any]:
    global db_pool

    if db_pool is None:
        with db_lock:
            if db_pool is None:
                db_pool = ThreadedConnectionPool(
                    1,
                    POOL_MAX,
                    dsn=norm_db(DATABASE_URL),
                    connect_timeout=10,
                    application_name="not-your-vibe",
                )

    conn = None

    try:
        conn = db_pool.getconn()
        conn.autocommit = False

        yield conn

        conn.commit()

    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass

        raise

    finally:
        if conn:
            try:
                db_pool.putconn(conn)
            except (PoolError, OperationalError, InterfaceError):
                pass


@contextmanager
def cur(conn: Any) -> Iterator[Any]:
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        yield cursor
    finally:
        cursor.close()


# ============================================================
# DATABASE INIT
# ============================================================

def init_db() -> None:
    schema = """
    CREATE TABLE IF NOT EXISTS users(
        user_id BIGINT PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        first_seen BIGINT NOT NULL,
        last_seen BIGINT NOT NULL,
        total_requests BIGINT NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS tracks(
        id BIGSERIAL PRIMARY KEY,
        mood TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        message_id BIGINT NOT NULL,
        created_at BIGINT NOT NULL,
        title TEXT,
        UNIQUE(channel_id,message_id)
    );

    CREATE TABLE IF NOT EXISTS user_history(
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        mood TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        message_id BIGINT NOT NULL,
        action TEXT NOT NULL DEFAULT 'served',
        source TEXT NOT NULL DEFAULT 'served',
        sent_at BIGINT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS user_state(
        user_id BIGINT PRIMARY KEY,
        mood TEXT,
        radio_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        updated_at BIGINT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS track_feedback(
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        channel_id TEXT NOT NULL,
        message_id BIGINT NOT NULL,
        mood TEXT NOT NULL,
        feedback TEXT NOT NULL,
        created_at BIGINT NOT NULL,
        UNIQUE(user_id,channel_id,message_id)
    );

    CREATE TABLE IF NOT EXISTS daily_activity(
        user_id BIGINT NOT NULL,
        day DATE NOT NULL,
        PRIMARY KEY(user_id,day)
    );

    CREATE TABLE IF NOT EXISTS broadcasts(
        id BIGSERIAL PRIMARY KEY,
        admin_id BIGINT NOT NULL,
        text TEXT,
        created_at BIGINT NOT NULL,
        sent_count BIGINT NOT NULL DEFAULT 0,
        failed_count BIGINT NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS broadcast_reactions(
        id BIGSERIAL PRIMARY KEY,
        broadcast_id BIGINT NOT NULL,
        user_id BIGINT NOT NULL,
        reaction TEXT NOT NULL,
        created_at BIGINT NOT NULL,
        UNIQUE(broadcast_id,user_id)
    );

    CREATE TABLE IF NOT EXISTS broadcast_comments(
        id BIGSERIAL PRIMARY KEY,
        broadcast_id BIGINT NOT NULL,
        user_id BIGINT NOT NULL,
        comment TEXT NOT NULL,
        created_at BIGINT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS pending_comments(
        user_id BIGINT PRIMARY KEY,
        broadcast_id BIGINT NOT NULL,
        created_at BIGINT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS pending_broadcasts(
        user_id BIGINT PRIMARY KEY,
        chat_id BIGINT NOT NULL,
        created_at BIGINT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_tracks_mood
        ON tracks(mood);

    CREATE INDEX IF NOT EXISTS idx_tracks_created
        ON tracks(created_at DESC);

    CREATE INDEX IF NOT EXISTS idx_hist_user
        ON user_history(user_id,sent_at DESC);

    CREATE INDEX IF NOT EXISTS idx_hist_track
        ON user_history(user_id,channel_id,message_id,sent_at DESC);

    CREATE INDEX IF NOT EXISTS idx_hist_source_day
        ON user_history(user_id,source,sent_at DESC);

    CREATE INDEX IF NOT EXISTS idx_fb_user
        ON track_feedback(user_id);

    CREATE INDEX IF NOT EXISTS idx_fb_track_feedback
        ON track_feedback(channel_id,message_id,feedback);

    CREATE INDEX IF NOT EXISTS idx_daily_day
        ON daily_activity(day);

    CREATE INDEX IF NOT EXISTS idx_bc_broadcast
        ON broadcast_comments(broadcast_id);

    ALTER TABLE tracks
        ADD COLUMN IF NOT EXISTS title TEXT;

    ALTER TABLE user_history
        ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'served';

    ALTER TABLE broadcasts
        ADD COLUMN IF NOT EXISTS source_chat_id BIGINT;

    ALTER TABLE broadcasts
        ADD COLUMN IF NOT EXISTS source_message_id BIGINT;

    ALTER TABLE broadcasts
        ADD COLUMN IF NOT EXISTS content_type TEXT;

    ALTER TABLE broadcasts
        ALTER COLUMN text DROP NOT NULL;

    ALTER TABLE broadcast_comments
        ADD COLUMN IF NOT EXISTS username TEXT;

    ALTER TABLE broadcast_comments
        ADD COLUMN IF NOT EXISTS first_name TEXT;

    ALTER TABLE broadcast_comments
        ADD COLUMN IF NOT EXISTS last_name TEXT;
    """

    with db() as conn:
        with cur(conn) as cursor:
            cursor.execute(schema)


# ============================================================
# USER
# ============================================================

def register(user: Mapping[str, Any]) -> None:
    uid = user.get("id")

    if not isinstance(uid, int):
        return

    now = time.time()

    with register_cache_lock:
        last = register_cache.get(uid, 0.0)

        if now - last < 60:
            return

        register_cache[uid] = now

    ts = int(now)

    try:
        with db() as conn:
            with cur(conn) as cursor:
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
                    VALUES(%s,%s,%s,%s,%s,%s,1)

                    ON CONFLICT(user_id)
                    DO UPDATE SET
                        username=EXCLUDED.username,
                        first_name=EXCLUDED.first_name,
                        last_name=EXCLUDED.last_name,
                        last_seen=EXCLUDED.last_seen,
                        total_requests=users.total_requests+1
                    """,
                    (
                        uid,
                        user.get("username"),
                        user.get("first_name"),
                        user.get("last_name"),
                        ts,
                        ts,
                    ),
                )

                day = datetime.now(
                    ZoneInfo("Asia/Yangon")
                ).date()

                cursor.execute(
                    """
                    INSERT INTO daily_activity(user_id,day)
                    VALUES(%s,%s)
                    ON CONFLICT DO NOTHING
                    """,
                    (uid, day),
                )

    except Exception:
        with register_cache_lock:
            register_cache.pop(uid, None)

        raise


# ============================================================
# USER MOOD
# ============================================================

def set_mood(uid: int, mood: str) -> bool:
    if mood not in MOODS:
        return False

    with db() as conn:
        with cur(conn) as cursor:
            cursor.execute(
                """
                INSERT INTO user_state(
                    user_id,
                    mood,
                    radio_enabled,
                    updated_at
                )
                VALUES(%s,%s,FALSE,%s)

                ON CONFLICT(user_id)
                DO UPDATE SET
                    mood=EXCLUDED.mood,
                    radio_enabled=FALSE,
                    updated_at=EXCLUDED.updated_at
                """,
                (
                    uid,
                    mood,
                    int(time.time()),
                ),
            )

    return True


def get_mood(uid: int) -> Optional[str]:
    with db() as conn:
        with cur(conn) as cursor:
            cursor.execute(
                """
                SELECT mood
                FROM user_state
                WHERE user_id=%s
                """,
                (uid,),
            )

            row = cursor.fetchone()

    if row and row["mood"] in MOODS:
        return row["mood"]

    return None


def set_radio(uid: int, on: bool = True) -> None:
    """
    IMPORTANT:
    Do NOT overwrite the user's current mood with NULL.

    Previous version inserted mood=NULL on conflict.
    This version preserves the existing mood.
    """

    with db() as conn:
        with cur(conn) as cursor:
            cursor.execute(
                """
                INSERT INTO user_state(
                    user_id,
                    mood,
                    radio_enabled,
                    updated_at
                )
                VALUES(%s,NULL,%s,%s)

                ON CONFLICT(user_id)
                DO UPDATE SET
                    mood=user_state.mood,
                    radio_enabled=EXCLUDED.radio_enabled,
                    updated_at=EXCLUDED.updated_at
                """,
                (
                    uid,
                    on,
                    int(time.time()),
                ),
            )


# ============================================================
# TRACKS
# ============================================================

def save_track(
    mood: str,
    channel_id: Any,
    message_id: Any,
    title: Optional[str] = None,
) -> bool:

    if mood not in MOODS:
        return False

    if not channel_id or not message_id:
        return False

    with db() as conn:
        with cur(conn) as cursor:
            cursor.execute(
                """
                INSERT INTO tracks(
                    mood,
                    channel_id,
                    message_id,
                    created_at,
                    title
                )
                VALUES(%s,%s,%s,%s,%s)

                ON CONFLICT(channel_id,message_id)
                DO UPDATE SET
                    mood=EXCLUDED.mood,
                    title=COALESCE(EXCLUDED.title,tracks.title)

                RETURNING id
                """,
                (
                    mood,
                    str(channel_id),
                    int(message_id),
                    int(time.time()),
                    title,
                ),
            )

            return cursor.fetchone() is not None


def counts(force: bool = False) -> dict:
    global _counts_cache
    global _counts_cache_at

    now = time.time()

    with _counts_cache_lock:
        if (
            not force
            and _counts_cache is not None
            and now - _counts_cache_at < COUNTS_TTL
        ):
            return dict(_counts_cache)

    result = {mood: 0 for mood in MOODS}

    with db() as conn:
        with cur(conn) as cursor:
            cursor.execute(
                """
                SELECT mood,COUNT(*) AS count
                FROM tracks
                GROUP BY mood
                """
            )

            for row in cursor.fetchall():
                mood = row["mood"]

                if mood in result:
                    result[mood] = int(row["count"])

    with _counts_cache_lock:
        _counts_cache = dict(result)
        _counts_cache_at = now

    return result


# ============================================================
# FEEDBACK
# ============================================================

def feedback(
    uid: int,
    channel_id: Any,
    message_id: Any,
) -> Optional[str]:

    with db() as conn:
        with cur(conn) as cursor:
            cursor.execute(
                """
                SELECT feedback
                FROM track_feedback
                WHERE user_id=%s
                  AND channel_id=%s
                  AND message_id=%s
                """,
                (
                    uid,
                    str(channel_id),
                    int(message_id),
                ),
            )

            row = cursor.fetchone()

    return row["feedback"] if row else None


def feedback_map(uid: int) -> dict:
    result = {}

    with db() as conn:
        with cur(conn) as cursor:
            cursor.execute(
                """
                SELECT channel_id,message_id,feedback
                FROM track_feedback
                WHERE user_id=%s
                """,
                (uid,),
            )

            for row in cursor.fetchall():
                result[
                    (
                        str(row["channel_id"]),
                        int(row["message_id"]),
                    )
                ] = row["feedback"]

    return result


def save_feedback(
    uid: int,
    channel_id: Any,
    message_id: Any,
    mood: str,
    fb: str,
) -> bool:

    if fb not in ("like", "not_for_me"):
        return False

    if mood not in MOODS:
        return False

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                SELECT 1
                FROM tracks
                WHERE channel_id=%s
                  AND message_id=%s
                """,
                (
                    str(channel_id),
                    int(message_id),
                ),
            )

            if not cursor.fetchone():
                return False

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
                VALUES(%s,%s,%s,%s,%s,%s)

                ON CONFLICT(user_id,channel_id,message_id)
                DO UPDATE SET
                    mood=EXCLUDED.mood,
                    feedback=EXCLUDED.feedback,
                    created_at=EXCLUDED.created_at
                """,
                (
                    uid,
                    str(channel_id),
                    int(message_id),
                    mood,
                    fb,
                    int(time.time()),
                ),
            )

    return True


def clear_feedback(
    uid: int,
    channel_id: Any,
    message_id: Any,
) -> None:

    with db() as conn:
        with cur(conn) as cursor:
            cursor.execute(
                """
                DELETE FROM track_feedback
                WHERE user_id=%s
                  AND channel_id=%s
                  AND message_id=%s
                """,
                (
                    uid,
                    str(channel_id),
                    int(message_id),
                ),
            )


# ============================================================
# HISTORY
# ============================================================

def history(uid: int) -> set:
    result = set()

    with db() as conn:
        with cur(conn) as cursor:
            cursor.execute(
                """
                SELECT channel_id,message_id
                FROM user_history
                WHERE user_id=%s
                  AND action='served'
                ORDER BY sent_at DESC,id DESC
                LIMIT %s
                """,
                (
                    uid,
                    HISTORY_LIMIT,
                ),
            )

            for row in cursor.fetchall():
                result.add(
                    (
                        str(row["channel_id"]),
                        int(row["message_id"]),
                    )
                )

    return result


def record(
    uid: int,
    mood: str,
    channel_id: Any,
    message_id: Any,
    source: str = "served",
) -> None:

    with db() as conn:
        with cur(conn) as cursor:
            cursor.execute(
                """
                INSERT INTO user_history(
                    user_id,
                    mood,
                    channel_id,
                    message_id,
                    action,
                    source,
                    sent_at
                )
                VALUES(%s,%s,%s,%s,'served',%s,%s)
                """,
                (
                    uid,
                    mood,
                    str(channel_id),
                    int(message_id),
                    source,
                    int(time.time()),
                ),
            )


def reserve(
    uid: int,
    track: Optional[tuple],
    source: str = "served",
) -> Optional[tuple]:

    if not track:
        return None

    mood, message_id, channel_id = track[:3]

    if mood not in MOODS:
        return None

    with db() as conn:
        with cur(conn) as cursor:

            # One user's track reservation at a time.
            cursor.execute(
                """
                SELECT pg_advisory_xact_lock(%s)
                """,
                (int(uid),),
            )

            cursor.execute(
                """
                INSERT INTO user_history(
                    user_id,
                    mood,
                    channel_id,
                    message_id,
                    action,
                    source,
                    sent_at
                )
                VALUES(%s,%s,%s,%s,'served',%s,%s)
                """,
                (
                    uid,
                    mood,
                    str(channel_id),
                    int(message_id),
                    source,
                    int(time.time()),
                ),
            )

    return track


# ============================================================
# TRACK CANDIDATES
# ============================================================

def candidates(
    mood: str,
    limit: int = 120,
) -> list:

    if mood not in MOODS:
        return []

    with db() as conn:
        with cur(conn) as cursor:
            cursor.execute(
                """
                SELECT message_id,channel_id,title
                FROM tracks
                WHERE mood=%s
                ORDER BY RANDOM()
                LIMIT %s
                """,
                (
                    mood,
                    limit,
                ),
            )

            return [
                (
                    int(row["message_id"]),
                    str(row["channel_id"]),
                    row.get("title"),
                )
                for row in cursor.fetchall()
            ]


def all_unplayed_tracks(
    uid: int,
    mood: Optional[str] = None,
    limit: int = 600,
) -> list:

    params = [uid]
    where = ""

    if mood in MOODS:
        where = "AND t.mood=%s"
        params.append(mood)

    params.append(limit)

    with db() as conn:
        with cur(conn) as cursor:
            cursor.execute(
                f"""
                SELECT
                    t.mood,
                    t.message_id,
                    t.channel_id,
                    t.title
                FROM tracks t
                WHERE 1=1
                  {where}

                  AND NOT EXISTS(
                      SELECT 1
                      FROM track_feedback f
                      WHERE f.user_id=%s
                        AND f.channel_id=t.channel_id
                        AND f.message_id=t.message_id
                        AND f.feedback='not_for_me'
                  )

                  AND NOT EXISTS(
                      SELECT 1
                      FROM user_history h
                      WHERE h.user_id=%s
                        AND h.channel_id=t.channel_id
                        AND h.message_id=t.message_id
                        AND h.action='served'
                  )

                ORDER BY RANDOM()
                LIMIT %s
                """,
                (
                    *params[:-1],
                    uid,
                    params[-1],
                ),
            )

            return cursor.fetchall()


# ============================================================
# NORMAL MOOD TRACK
# ============================================================

def normal_track(
    uid: int,
    mood: str,
) -> Optional[tuple]:

    if mood not in MOODS:
        return None

    # FIRST: real DB-level unplayed selection.
    rows = all_unplayed_tracks(
        uid,
        mood=mood,
        limit=600,
    )

    if rows:
        row = random.choice(rows)

        return (
            str(row["mood"]),
            int(row["message_id"]),
            str(row["channel_id"]),
        )

    # SECOND: all allowed tracks from this mood.
    fm = feedback_map(uid)

    rows = candidates(mood, 300)

    allowed = [
        row
        for row in rows
        if fm.get(
            (
                str(row[1]),
                int(row[0]),
            )
        ) != "not_for_me"
    ]

    if not allowed:
        return None

    row = random.choice(allowed)

    return (
        mood,
        int(row[0]),
        str(row[1]),
    )


# ============================================================
# FEEDBACK RATIOS
# ============================================================

def ratios(uid: int) -> dict:
    result = {
        mood: {
            "like": 0,
            "not": 0,
        }
        for mood in MOODS
    }

    with db() as conn:
        with cur(conn) as cursor:
            cursor.execute(
                """
                SELECT mood,feedback,COUNT(*) AS count
                FROM track_feedback
                WHERE user_id=%s
                GROUP BY mood,feedback
                """,
                (uid,),
            )

            for row in cursor.fetchall():

                mood = row["mood"]

                if mood not in result:
                    continue

                if row["feedback"] == "like":
                    result[mood]["like"] = int(row["count"])

                elif row["feedback"] == "not_for_me":
                    result[mood]["not"] = int(row["count"])

    return result


def radio_weights(uid: int) -> dict:
    feedback_data = ratios(uid)

    weights = {}

    for mood in MOODS:
        likes = feedback_data[mood]["like"]
        nots = feedback_data[mood]["not"]

        total = likes + nots

        ratio = (
            (likes + 1) / (total + 2)
        )

        volume = 1 + min(likes, 20) * 0.35

        weights[mood] = max(
            0.05,
            ratio * volume,
        )

    return weights


# ============================================================
# RADIO
# ============================================================

def radio_track(uid: int) -> Optional[tuple]:

    weights = radio_weights(uid)
    count_map = counts()

    available = [
        mood
        for mood, count in count_map.items()
        if count > 0
    ]

    if not available:
        return None

    feedback_data = ratios(uid)

    ranked = sorted(
        available,
        key=lambda mood: weights[mood],
        reverse=True,
    )

    total_likes = sum(
        feedback_data[mood]["like"]
        for mood in MOODS
    )

    # If user has likes, strongest preference starts first.
    if total_likes > 0:
        mood_order = ranked
    else:
        mood_order = available[:]
        random.shuffle(mood_order)

    # Try every available mood.
    for mood in mood_order:

        rows = all_unplayed_tracks(
            uid,
            mood=mood,
            limit=600,
        )

        if rows:
            row = random.choice(rows)

            return (
                str(row["mood"]),
                int(row["message_id"]),
                str(row["channel_id"]),
            )

    # Everything already played.
    # Reuse allowed tracks, still avoiding NOT FOR ME.
    fm = feedback_map(uid)

    for mood in mood_order:

        rows = candidates(mood, 300)

        allowed = [
            row
            for row in rows
            if fm.get(
                (
                    str(row[1]),
                    int(row[0]),
                )
            ) != "not_for_me"
        ]

        if allowed:
            row = random.choice(allowed)

            return (
                mood,
                int(row[0]),
                str(row[1]),
            )

    return None


# ============================================================
# LIKED TRACKS
# ============================================================

def liked_tracks(uid: int) -> list:
    """
    IMPORTANT:
    FOR YOU ONLY USES TRACKS THAT THIS USER LIKED.

    It NEVER selects:
        - unliked tracks
        - random tracks
        - other users' likes
        - mood-only tracks
    """

    with db() as conn:
        with cur(conn) as cursor:
            cursor.execute(
                """
                SELECT
                    t.id,
                    t.mood,
                    t.message_id,
                    t.channel_id,
                    COALESCE(
                        NULLIF(t.title,''),
                        'Track #' || t.message_id::text
                    ) AS title,
                    f.created_at AS liked_at

                FROM track_feedback f

                INNER JOIN tracks t
                    ON t.channel_id=f.channel_id
                   AND t.message_id=f.message_id

                WHERE f.user_id=%s
                  AND f.feedback='like'

                ORDER BY f.created_at DESC,t.id DESC
                """,
                (uid,),
            )

            return cursor.fetchall()


# ============================================================
# FOR YOU
# ============================================================

def for_you_track(uid: int) -> Optional[tuple]:
    """
    FOR YOU RULE:

    1. Only user's LIKE tracks.
    2. NOT_FOR_ME can never be selected.
    3. Unplayed liked tracks are preferred.
    4. If every liked track was already served,
       reuse one of the user's liked tracks.
    """

    rows = liked_tracks(uid)

    if not rows:
        return None

    fm = feedback_map(uid)

    # Safety check:
    # only actual LIKE records are allowed.
    rows = [
        row
        for row in rows
        if fm.get(
            (
                str(row["channel_id"]),
                int(row["message_id"]),
            )
        ) == "like"
    ]

    if not rows:
        return None

    hist = history(uid)

    unseen = [
        row
        for row in rows
        if (
            str(row["channel_id"]),
            int(row["message_id"]),
        ) not in hist
    ]

    # IMPORTANT:
    # Unplayed liked tracks first.
    pool = unseen if unseen else rows

    row = random.choice(pool)

    return (
        str(row["mood"]),
        int(row["message_id"]),
        str(row["channel_id"]),
    )


# ============================================================
# ELIGIBLE TRACKS
# ============================================================

def eligible_tracks(
    uid: int,
    extra_where: str = "",
    params: tuple = (),
) -> list:

    hist = history(uid)

    with db() as conn:
        with cur(conn) as cursor:

            query = """
                SELECT
                    mood,
                    message_id,
                    channel_id,
                    title
                FROM tracks

                WHERE NOT EXISTS(
                    SELECT 1
                    FROM track_feedback f
                    WHERE f.user_id=%s
                      AND f.channel_id=tracks.channel_id
                      AND f.message_id=tracks.message_id
                      AND f.feedback='not_for_me'
                )
            """

            args = [uid]

            if extra_where:
                query += " AND " + extra_where
                args.extend(params)

            query += """
                ORDER BY RANDOM()
                LIMIT 600
            """

            cursor.execute(
                query,
                args,
            )

            rows = cursor.fetchall()

    unseen = [
        row
        for row in rows
        if (
            str(row["channel_id"]),
            int(row["message_id"]),
        ) not in hist
    ]

    return unseen or rows


# ============================================================
# STABLE PICK
# ============================================================

def stable_pick(
    rows: list,
    key: str,
) -> Optional[tuple]:

    if not rows:
        return None

    index = int(
        hashlib.md5(
            key.encode()
        ).hexdigest()[:8],
        16,
    ) % len(rows)

    row = rows[index]

    return (
        str(row["mood"]),
        int(row["message_id"]),
        str(row["channel_id"]),
    )


# ============================================================
# DAILY VIBE
# ============================================================

def _day_bounds(day):
    tz = ZoneInfo("Asia/Yangon")

    start = datetime.combine(
        day,
        datetime.min.time(),
        tzinfo=tz,
    )

    end = start + timedelta(days=1)

    return (
        int(start.timestamp()),
        int(end.timestamp()),
    )


def daily_vibe_mood(
    uid: int,
    day=None,
) -> Optional[str]:

    day = (
        day
        or datetime.now(
            ZoneInfo("Asia/Yangon")
        ).date()
    )

    start, end = _day_bounds(day)

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                SELECT
                    mood,
                    COUNT(*) AS plays

                FROM user_history

                WHERE user_id=%s
                  AND source='mood'
                  AND sent_at >= %s
                  AND sent_at < %s

                GROUP BY mood

                ORDER BY plays DESC,mood ASC

                LIMIT 1
                """,
                (
                    uid,
                    start,
                    end,
                ),
            )

            row = cursor.fetchone()

    return row["mood"] if row else None


def daily_vibe_track(uid: int) -> Optional[tuple]:
    mood = daily_vibe_mood(uid)

    if not mood:
        return None

    rows = eligible_tracks(
        uid,
        "mood=%s",
        (mood,),
    )

    if not rows:
        return None

    today = (
        datetime.now(
            ZoneInfo("Asia/Yangon")
        )
        .date()
        .isoformat()
    )

    return stable_pick(
        rows,
        f"daily-vibe:{uid}:{today}:{mood}",
    )


# ============================================================
# SURPRISE
# ============================================================

def surprise_track(uid: int) -> Optional[tuple]:

    feedback_data = ratios(uid)

    mood_scores = sorted(
        [
            (
                (
                    feedback_data[mood]["not"] + 1
                )
                /
                (
                    feedback_data[mood]["like"]
                    + feedback_data[mood]["not"]
                    + 2
                ),
                mood,
            )
            for mood in MOODS
        ],
        reverse=True,
    )

    for _, mood in mood_scores:

        rows = eligible_tracks(
            uid,
            "mood=%s",
            (mood,),
        )

        if rows:
            row = random.choice(rows)

            return (
                str(row["mood"]),
                int(row["message_id"]),
                str(row["channel_id"]),
            )

    return None


# ============================================================
# TRENDING
# ============================================================

def trending_rows(limit: int = 10) -> list:

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                SELECT
                    t.id,
                    t.mood,
                    t.channel_id,
                    t.message_id,

                    COALESCE(
                        NULLIF(t.title,''),
                        'Track #' || t.message_id::text
                    ) AS title,

                    COUNT(f.id) AS likes

                FROM tracks t

                INNER JOIN track_feedback f
                    ON f.channel_id=t.channel_id
                   AND f.message_id=t.message_id
                   AND f.feedback='like'

                GROUP BY
                    t.id,
                    t.mood,
                    t.channel_id,
                    t.message_id,
                    t.title

                ORDER BY
                    likes DESC,
                    t.created_at DESC

                LIMIT %s
                """,
                (limit,),
            )

            return cursor.fetchall()


def trending_text(limit: int = 10) -> str:

    rows = trending_rows(limit)

    if not rows:
        return (
            "📈 TRENDING NOW\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "No liked tracks yet. Start liking tracks ❤️"
        )

    lines = [
        "📈 TRENDING NOW",
        "━━━━━━━━━━━━━━━━━━",
        "",
    ]

    for index, row in enumerate(rows, 1):

        title = (
            str(row["title"])
            .replace("\n", " ")
            [:90]
        )

        lines.append(
            f"{index}. 🎵 {title}"
        )

        lines.append(
            f"   {INFO[row['mood']][0]} • ❤️ {int(row['likes'])}"
        )

    return "\n".join(lines)


# ============================================================
# TASTE ANALYTICS
# ============================================================

def taste_analytics(uid: int) -> str:

    data = ratios(uid)

    total_like = sum(
        value["like"]
        for value in data.values()
    )

    total_not = sum(
        value["not"]
        for value in data.values()
    )

    ranked = sorted(
        MOODS,
        key=lambda mood: (
            data[mood]["like"],
            data[mood]["like"]
            - data[mood]["not"],
        ),
        reverse=True,
    )

    lines = [
        "📊 TASTE ANALYTICS",
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"❤️ Likes: {total_like}    😴 Not for me: {total_not}",
    ]

    total = total_like + total_not

    if total:
        lines.append(
            f"🎯 Positive ratio: "
            f"{total_like / total * 100:.0f}%"
        )

    lines.append("")

    for mood in ranked:

        likes = data[mood]["like"]
        nots = data[mood]["not"]

        if likes + nots:
            lines.append(
                f"{INFO[mood][0]} → ❤️ {likes} / 😴 {nots}"
            )

    if not total:
        lines.append(
            "Like or skip tracks to build your taste profile."
        )

    return "\n".join(lines)


# ============================================================
# TRACK OF THE DAY
# ============================================================

def track_of_day_playlist(
    uid: int,
) -> tuple:

    rows = liked_tracks(uid)

    if len(rows) < 10:
        return None, len(rows)

    today = datetime.now(
        ZoneInfo("Asia/Yangon")
    ).date()

    def ordered_for(day):

        seed = day.isoformat()

        return sorted(
            rows,
            key=lambda row: hashlib.md5(
                (
                    f"{seed}:{uid}:"
                    f"{row['channel_id']}:"
                    f"{row['message_id']}"
                ).encode()
            ).hexdigest(),
        )

    current = ordered_for(today)

    if len(rows) > 10:

        previous = ordered_for(
            today - timedelta(days=1)
        )

        previous_keys = {
            (
                str(row["channel_id"]),
                int(row["message_id"]),
            )
            for row in previous[:10]
        }

        current_keys = {
            (
                str(row["channel_id"]),
                int(row["message_id"]),
            )
            for row in current[:10]
        }

        if current_keys == previous_keys:

            for offset in range(1, len(rows)):

                candidate = (
                    current[offset:offset + 10]
                    if offset + 10 <= len(rows)
                    else
                    current[offset:]
                    + current[:(
                        offset + 10 - len(rows)
                    )]
                )

                candidate_keys = {
                    (
                        str(row["channel_id"]),
                        int(row["message_id"]),
                    )
                    for row in candidate
                }

                if candidate_keys != previous_keys:
                    current = candidate
                    break

    return current[:10], len(rows)


# ============================================================
# TELEGRAM HTTP
# ============================================================

def session() -> requests.Session:

    current = getattr(
        http_local,
        "session",
        None,
    )

    if current is None:
        current = requests.Session()
        http_local.session = current

    return current


def tg(
    method: str,
    data: Optional[dict] = None,
    timeout: int = HTTP_TIMEOUT,
) -> dict:

    if not BOT_TOKEN:
        return {
            "ok": False,
            "description": "BOT_TOKEN is missing",
        }

    try:

        response = session().post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=data or {},
            timeout=timeout,
        )

        try:
            payload = response.json()
        except ValueError:
            payload = {
                "ok": False,
                "description": (
                    f"HTTP {response.status_code}: "
                    "non-JSON response"
                ),
            }

        if response.status_code >= 400:
            if payload.get("ok", True):
                payload = {
                    "ok": False,
                    "description": (
                        f"HTTP {response.status_code}"
                    ),
                }

        return payload

    except requests.RequestException as exc:

        log.warning(
            "Telegram %s: %s",
            method,
            exc,
        )

        return {
            "ok": False,
            "description": str(exc),
        }

    except Exception as exc:

        log.exception(
            "Telegram %s unexpected error",
            method,
        )

        return {
            "ok": False,
            "description": str(exc),
        }


def send(
    chat_id: int,
    text: str,
    keyboard: Optional[dict] = None,
) -> dict:

    data = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }

    if keyboard:
        data["reply_markup"] = keyboard

    return tg(
        "sendMessage",
        data,
        15,
    )


def answer(
    callback_id: str,
    text: str = "",
) -> None:

    tg(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text,
        },
        8,
    )


def edit_keyboard(
    chat_id: int,
    message_id: int,
    keyboard: dict,
) -> None:

    tg(
        "editMessageReplyMarkup",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": keyboard,
        },
        10,
    )


def copy_music(
    chat_id: int,
    channel_id: str,
    message_id: int,
) -> dict:

    return tg(
        "copyMessage",
        {
            "chat_id": chat_id,
            "from_chat_id": channel_id,
            "message_id": message_id,
        },
        30,
    )


# ============================================================
# BOT MENUS
# ============================================================

def mood_menu() -> dict:

    return {
        "inline_keyboard": [
            [
                {
                    "text": INFO["sad"][0],
                    "callback_data": "mood_sad",
                },
                {
                    "text": INFO["love"][0],
                    "callback_data": "mood_love",
                },
            ],
            [
                {
                    "text": INFO["chill"][0],
                    "callback_data": "mood_chill",
                },
                {
                    "text": INFO["hype"][0],
                    "callback_data": "mood_hype",
                },
            ],
            [
                {
                    "text": INFO["dark"][0],
                    "callback_data": "mood_dark",
                },
                {
                    "text": INFO["energetic"][0],
                    "callback_data": "mood_energetic",
                },
            ],
            [
                {
                    "text": INFO["night"][0],
                    "callback_data": "mood_night",
                },
                {
                    "text": INFO["melodic"][0],
                    "callback_data": "mood_melodic",
                },
            ],
            [
                {
                    "text": "🔥 DAILY VIBE",
                    "callback_data": "daily_vibe",
                },
                {
                    "text": "🧠 FOR YOU",
                    "callback_data": "for_you",
                },
            ],
            [
                {
                    "text": "🎲 SURPRISE ME",
                    "callback_data": "surprise_me",
                },
                {
                    "text": "📈 TRENDING",
                    "callback_data": "trending",
                },
            ],
            [
                {
                    "text": "🎵 TRACK OF THE DAY",
                    "callback_data": "track_of_day",
                },
            ],
            [
                {
                    "text": "🏆 TOP 10 LIKED",
                    "callback_data": "top_liked",
                },
            ],
        ]
    }


def buttons(
    uid: int,
    channel_id: str,
    message_id: int,
    mood: str,
) -> dict:

    current_feedback = feedback(
        uid,
        channel_id,
        message_id,
    )

    return {
        "inline_keyboard": [
            [
                {
                    "text": (
                        "❤️✓"
                        if current_feedback == "like"
                        else "❤️"
                    ),
                    "callback_data": (
                        f"like:{mood}:"
                        f"{channel_id}:{message_id}"
                    ),
                },
                {
                    "text": (
                        "😴✓"
                        if current_feedback == "not_for_me"
                        else "😴"
                    ),
                    "callback_data": (
                        f"notme:{mood}:"
                        f"{channel_id}:{message_id}"
                    ),
                },
            ],
            [
                {
                    "text": "⏭ NEXT",
                    "callback_data": "next_music",
                },
                {
                    "text": "📻 RADIO",
                    "callback_data": "radio",
                },
            ],
            [
                {
                    "text": "👤 PROFILE",
                    "callback_data": "profile",
                },
                {
                    "text": "🆕 NEW TRACKS",
                    "callback_data": "new_tracks",
                },
            ],
            [
                {
                    "text": "🎛 CHANGE MOOD",
                    "callback_data": "change_mood",
                },
            ],
        ]
    }


def special_buttons(
    uid: int,
    channel_id: str,
    message_id: int,
    mood: str,
) -> dict:

    current_feedback = feedback(
        uid,
        channel_id,
        message_id,
    )

    return {
        "inline_keyboard": [
            [
                {
                    "text": (
                        "❤️✓"
                        if current_feedback == "like"
                        else "❤️"
                    ),
                    "callback_data": (
                        f"like:{mood}:"
                        f"{channel_id}:{message_id}"
                    ),
                },
                {
                    "text": (
                        "😴✓"
                        if current_feedback == "not_for_me"
                        else "😴"
                    ),
                    "callback_data": (
                        f"notme:{mood}:"
                        f"{channel_id}:{message_id}"
                    ),
                },
            ],
            [
                {
                    "text": "⏭ NEXT",
                    "callback_data": "next_music",
                },
                {
                    "text": "📻 RADIO",
                    "callback_data": "radio",
                },
            ],
            [
                {
                    "text": "🧠 FOR YOU",
                    "callback_data": "for_you",
                },
                {
                    "text": "🎛 CHANGE MOOD",
                    "callback_data": "change_mood",
                },
            ],
            [
                {
                    "text": "👤 PROFILE",
                    "callback_data": "profile",
                },
            ],
        ]
    }


# ============================================================
# MUSIC SENDING
# ============================================================

def send_music(
    chat_id: int,
    uid: int,
    mood: str,
    radio: bool = False,
) -> None:

    track = (
        radio_track(uid)
        if radio
        else normal_track(uid, mood)
    )

    if not track:
        send(
            chat_id,
            "⚠️ No suitable track found.",
            mood_menu(),
        )
        return

    selected_mood, message_id, channel_id = track

    result = copy_music(
        chat_id,
        channel_id,
        message_id,
    )

    if not result.get("ok"):
        send(
            chat_id,
            "⚠️ This track could not be delivered.",
            mood_menu(),
        )
        return

    source = (
        "radio"
        if radio
        else "mood"
    )

    try:
        reserve(
            uid,
            (
                selected_mood,
                message_id,
                channel_id,
            ),
            source,
        )
    except Exception:
        log.exception(
            "History reservation failed "
            "uid=%s channel=%s message=%s",
            uid,
            channel_id,
            message_id,
        )

    title = (
        "📻 YOUR RADIO"
        if radio
        else "🎧 NOW PLAYING"
    )

    description = (
        "Personalized by your Like ratio across all moods."
        if radio
        else INFO[selected_mood][1]
    )

    send(
        chat_id,
        (
            f"{title}\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"{INFO[selected_mood][0]}\n\n"
            f"{description}\n\n"
            "Enjoy the vibe. ✨"
        ),
        buttons(
            uid,
            channel_id,
            message_id,
            selected_mood,
        ),
    )


def send_special_music(
    chat_id: int,
    uid: int,
    track: Optional[tuple],
    header: str,
    source: str = "special",
) -> None:

    if not track:
        send(
            chat_id,
            "⚠️ No suitable track found.",
            mood_menu(),
        )
        return

    mood, message_id, channel_id = track[:3]

    result = copy_music(
        chat_id,
        channel_id,
        message_id,
    )

    if not result.get("ok"):
        send(
            chat_id,
            "⚠️ This track could not be delivered.",
            mood_menu(),
        )
        return

    try:
        reserve(
            uid,
            (
                mood,
                message_id,
                channel_id,
            ),
            source,
        )
    except Exception:
        log.exception(
            "Special history reservation failed"
        )

    label = (
        f"Track #{message_id}"
    )

    send(
        chat_id,
        (
            f"{header}\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"🎵 {label}\n"
            f"{INFO[mood][0]}\n\n"
            "Enjoy the vibe. ✨"
        ),
        special_buttons(
            uid,
            channel_id,
            message_id,
            mood,
        ),
    )


# ============================================================
# TRACK OF DAY SEND
# ============================================================

def send_track_of_day_playlist(
    chat_id: int,
    uid: int,
) -> None:

    playlist, total = track_of_day_playlist(uid)

    if not playlist:

        send(
            chat_id,
            (
                "🎵 TRACK OF THE DAY\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Raver အနေနဲ့ Like 10 ခု မပေးရသေးသဖြင့် "
                "BOT မှ Track of the Day Playlist ကို "
                "မပို့ပေးနိုင်သေးပါ။\n\n"
                f"❤️ လက်ရှိ Like: {total}/10\n\n"
                "သီချင်းတွေကို Like 10 ခု ပြည့်အောင်ပေးပြီး "
                "ပြန်လာခဲ့ပါ။ ✨"
            ),
            mood_menu(),
        )

        return

    send(
        chat_id,
        (
            "🎵 TRACK OF THE DAY — YOUR 10 LIKED TRACKS\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "ဒီနေ့အတွက် မင်း Like လုပ်ထားတဲ့ "
            "Track 10 ပုဒ်ကို ရွေးထားပါတယ်။ ✨"
        ),
    )

    for index, row in enumerate(playlist, 1):

        mood = str(row["mood"])
        channel_id = str(row["channel_id"])
        message_id = int(row["message_id"])

        result = copy_music(
            chat_id,
            channel_id,
            message_id,
        )

        if result.get("ok"):

            title = (
                row.get("title")
                or f"Track #{message_id}"
            )

            title = (
                str(title)
                .replace("\n", " ")
                [:120]
            )

            send(
                chat_id,
                (
                    f"#{index} 🎵 {title}\n"
                    f"{INFO[mood][0]}"
                ),
            )

            try:
                reserve(
                    uid,
                    (
                        mood,
                        message_id,
                        channel_id,
                    ),
                    "track_of_day",
                )
            except Exception:
                log.exception(
                    "Track of Day history failed"
                )

        else:
            log.warning(
                "Track of Day delivery failed "
                "uid=%s channel=%s message=%s",
                uid,
                channel_id,
                message_id,
            )

    send(
        chat_id,
        (
            "━━━━━━━━━━━━━━━━━━\n"
            "❤️ All 10 tracks are from your existing Likes.\n"
            "🔄 Tomorrow’s playlist will be reshuffled."
        ),
        mood_menu(),
    )


# ============================================================
# PROFILE
# ============================================================

def profile_text(uid: int) -> str:

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                SELECT
                    username,
                    first_name,
                    last_name,
                    total_requests
                FROM users
                WHERE user_id=%s
                """,
                (uid,),
            )

            user = cursor.fetchone() or {}

            cursor.execute(
                """
                SELECT COUNT(*) AS n
                FROM track_feedback
                WHERE user_id=%s
                  AND feedback='like'
                """,
                (uid,),
            )

            likes = int(
                cursor.fetchone()["n"]
            )

            cursor.execute(
                """
                SELECT COUNT(*) AS n
                FROM track_feedback
                WHERE user_id=%s
                  AND feedback='not_for_me'
                """,
                (uid,),
            )

            nots = int(
                cursor.fetchone()["n"]
            )

            cursor.execute(
                """
                SELECT COUNT(*) AS n
                FROM user_history
                WHERE user_id=%s
                  AND action='served'
                """,
                (uid,),
            )

            served = int(
                cursor.fetchone()["n"]
            )

    data = ratios(uid)

    ranked = sorted(
        MOODS,
        key=lambda mood: (
            data[mood]["like"],
            data[mood]["like"]
            - data[mood]["not"],
        ),
        reverse=True,
    )

    fav = ranked[0]
    second = ranked[1]

    name = " ".join(
        part
        for part in (
            user.get("first_name") or "",
            user.get("last_name") or "",
        )
        if part
    ).strip()

    if not name:
        name = "Vibe Listener"

    username = (
        f"@{user.get('username')}"
        if user.get("username")
        else "Not set"
    )

    mood = get_mood(uid)

    current_mood = (
        INFO[mood][0]
        if mood
        else "Not selected"
    )

    return (
        "👤 VIBE PROFILE 2.0\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"Name: {name}\n"
        f"Username: {username}\n"
        f"Current mood: {current_mood}\n\n"
        f"🎵 Tracks played: {served}\n"
        f"❤️ Likes: {likes}\n"
        f"😴 Not for me: {nots}\n\n"
        f"🏆 Top mood: {INFO[fav][0]}\n"
        f"🥈 Second: {INFO[second][0]}\n\n"
        "Your Radio learns from your feedback across all moods."
    )


# ============================================================
# NEW TRACKS
# ============================================================

def new_tracks(chat_id: int) -> None:

    lines = [
        "🆕 NEW TRACKS",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "Latest 5 tracks from each mood channel:",
        "",
    ]

    for mood in MOODS:

        channel = CHANNELS.get(mood)

        if not channel:
            continue

        with db() as conn:
            with cur(conn) as cursor:

                cursor.execute(
                    """
                    SELECT message_id,title
                    FROM tracks
                    WHERE mood=%s
                    ORDER BY created_at DESC,id DESC
                    LIMIT 5
                    """,
                    (mood,),
                )

                rows = cursor.fetchall()

        lines.append(
            INFO[mood][0]
        )

        if not rows:
            lines.append(
                "  — No tracks"
            )

        else:

            for row in rows:

                title = (
                    row.get("title")
                    or f"Track #{row['message_id']}"
                )

                title = (
                    str(title)
                    .replace("\n", " ")
                    [:80]
                )

                lines.append(
                    f"  • {title}"
                )

        lines.append("")

    send(
        chat_id,
        "\n".join(lines),
        mood_menu(),
    )


# ============================================================
# TOP LIKED
# ============================================================

def top_liked_tracks(limit: int = 10) -> list:

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                SELECT
                    t.mood,
                    t.channel_id,
                    t.message_id,

                    COALESCE(
                        NULLIF(t.title,''),
                        'Track #' || t.message_id::text
                    ) AS title,

                    COUNT(f.id) AS likes

                FROM tracks t

                INNER JOIN track_feedback f
                    ON f.channel_id=t.channel_id
                   AND f.message_id=t.message_id
                   AND f.feedback='like'

                GROUP BY
                    t.id,
                    t.mood,
                    t.channel_id,
                    t.message_id,
                    t.title

                ORDER BY
                    likes DESC,
                    t.created_at DESC

                LIMIT %s
                """,
                (limit,),
            )

            return cursor.fetchall()


def top_liked_text() -> str:

    rows = top_liked_tracks(10)

    lines = [
        "🏆 TOP 10 MOST LIKED TRACKS",
        "━━━━━━━━━━━━━━━━━━",
        "",
    ]

    if not rows:
        lines.append(
            "No likes yet. Start liking tracks ❤️"
        )

        return "\n".join(lines)

    for index, row in enumerate(rows, 1):

        title = (
            str(row["title"])
            .replace("\n", " ")
            [:90]
        )

        lines.append(
            f"{index}. 🎵 {title}"
        )

        lines.append(
            f"   {INFO[row['mood']][0]} • "
            f"❤️ {int(row['likes'])} likes"
        )

    return "\n".join(lines)


# ============================================================
# DAILY STATS
# ============================================================

def daily_stats():

    today = datetime.now(
        ZoneInfo("Asia/Yangon")
    ).date()

    days = [
        today - timedelta(days=i)
        for i in range(7)
    ]

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                SELECT COUNT(*) AS n
                FROM users
                """
            )

            total = int(
                cursor.fetchone()["n"]
            )

            cursor.execute(
                """
                SELECT COUNT(*) AS n
                FROM daily_activity
                WHERE day=%s
                """,
                (today,),
            )

            today_n = int(
                cursor.fetchone()["n"]
            )

            cursor.execute(
                """
                SELECT COUNT(DISTINCT user_id) AS n
                FROM daily_activity
                WHERE day >= %s
                """,
                (
                    today - timedelta(days=6),
                ),
            )

            week_n = int(
                cursor.fetchone()["n"]
            )

            cursor.execute(
                """
                SELECT day,COUNT(*) AS n
                FROM daily_activity
                WHERE day >= %s
                GROUP BY day
                ORDER BY day DESC
                """,
                (
                    today - timedelta(days=6),
                ),
            )

            rows = {
                row["day"]: int(row["n"])
                for row in cursor.fetchall()
            }

    return (
        total,
        today_n,
        week_n,
        [
            (
                day,
                rows.get(day, 0),
            )
            for day in days
        ],
    )


# ============================================================
# BROADCAST
# ============================================================

def broadcast_buttons(
    broadcast_id: int,
) -> dict:

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                SELECT
                    reaction,
                    COUNT(*) AS count
                FROM broadcast_reactions
                WHERE broadcast_id=%s
                GROUP BY reaction
                """,
                (broadcast_id,),
            )

            reactions = {
                row["reaction"]: int(row["count"])
                for row in cursor.fetchall()
            }

    return {
        "inline_keyboard": [
            [
                {
                    "text": (
                        f"❤️ "
                        f"{reactions.get('love',0)}"
                    ),
                    "callback_data": (
                        f"br:{broadcast_id}:love"
                    ),
                },
                {
                    "text": (
                        f"🔥 "
                        f"{reactions.get('fire',0)}"
                    ),
                    "callback_data": (
                        f"br:{broadcast_id}:fire"
                    ),
                },
                {
                    "text": (
                        f"👍 "
                        f"{reactions.get('like',0)}"
                    ),
                    "callback_data": (
                        f"br:{broadcast_id}:like"
                    ),
                },
            ],
            [
                {
                    "text": "💬 COMMENT",
                    "callback_data": (
                        f"bc:{broadcast_id}"
                    ),
                }
            ],
        ]
    }


def create_broadcast(
    admin_id: int,
    source_chat_id: int,
    source_message_id: int,
    content_type: str,
    text: Optional[str] = None,
) -> int:

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                INSERT INTO broadcasts(
                    admin_id,
                    text,
                    source_chat_id,
                    source_message_id,
                    content_type,
                    created_at
                )
                VALUES(%s,%s,%s,%s,%s,%s)

                RETURNING id
                """,
                (
                    admin_id,
                    text,
                    source_chat_id,
                    source_message_id,
                    content_type,
                    int(time.time()),
                ),
            )

            return int(
                cursor.fetchone()["id"]
            )


def broadcast_job(
    broadcast_id: int,
    source_chat_id: int,
    source_message_id: int,
) -> None:

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                SELECT user_id
                FROM users
                ORDER BY user_id
                """
            )

            users = [
                int(row["user_id"])
                for row in cursor.fetchall()
            ]

    keyboard = broadcast_buttons(
        broadcast_id
    )

    sent = 0
    failed = 0

    for uid in users:

        result = tg(
            "copyMessage",
            {
                "chat_id": uid,
                "from_chat_id": source_chat_id,
                "message_id": source_message_id,
                "reply_markup": keyboard,
            },
            30,
        )

        if result.get("ok"):
            sent += 1
        else:
            failed += 1

        time.sleep(0.04)

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                UPDATE broadcasts
                SET
                    sent_count=%s,
                    failed_count=%s
                WHERE id=%s
                """,
                (
                    sent,
                    failed,
                    broadcast_id,
                ),
            )

    send(
        int(ADMIN_USER_ID),
        (
            f"📣 BROADCAST #{broadcast_id} COMPLETE\n\n"
            f"✅ Sent: {sent}\n"
            f"❌ Failed: {failed}"
        ),
    )


def start_broadcast(
    admin_id: int,
    source_chat_id: int,
    source_message_id: int,
    content_type: str,
    text: Optional[str] = None,
) -> int:

    broadcast_id = create_broadcast(
        admin_id,
        source_chat_id,
        source_message_id,
        content_type,
        text,
    )

    executor.submit(
        broadcast_job,
        broadcast_id,
        source_chat_id,
        source_message_id,
    )

    return broadcast_id


# ============================================================
# COMMENTS
# ============================================================

def cleanup_pending() -> None:

    cutoff = int(
        time.time()
    ) - 900

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                DELETE FROM pending_comments
                WHERE created_at < %s
                """,
                (cutoff,),
            )

            cursor.execute(
                """
                DELETE FROM pending_broadcasts
                WHERE created_at < %s
                """,
                (cutoff,),
            )


def set_pending_broadcast(
    uid: int,
    chat_id: int,
) -> None:

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                INSERT INTO pending_broadcasts(
                    user_id,
                    chat_id,
                    created_at
                )
                VALUES(%s,%s,%s)

                ON CONFLICT(user_id)
                DO UPDATE SET
                    chat_id=EXCLUDED.chat_id,
                    created_at=EXCLUDED.created_at
                """,
                (
                    uid,
                    chat_id,
                    int(time.time()),
                ),
            )


def get_pending_broadcast(
    uid: int,
) -> Optional[int]:

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                SELECT chat_id
                FROM pending_broadcasts
                WHERE user_id=%s
                """,
                (uid,),
            )

            row = cursor.fetchone()

    return (
        int(row["chat_id"])
        if row
        else None
    )


def clear_pending_broadcast(uid: int) -> None:

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                DELETE FROM pending_broadcasts
                WHERE user_id=%s
                """,
                (uid,),
            )


def set_pending_comment(
    uid: int,
    broadcast_id: int,
) -> None:

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                INSERT INTO pending_comments(
                    user_id,
                    broadcast_id,
                    created_at
                )
                VALUES(%s,%s,%s)

                ON CONFLICT(user_id)
                DO UPDATE SET
                    broadcast_id=EXCLUDED.broadcast_id,
                    created_at=EXCLUDED.created_at
                """,
                (
                    uid,
                    broadcast_id,
                    int(time.time()),
                ),
            )


def get_pending_comment(
    uid: int,
) -> Optional[int]:

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                SELECT broadcast_id
                FROM pending_comments
                WHERE user_id=%s
                """,
                (uid,),
            )

            row = cursor.fetchone()

    return (
        int(row["broadcast_id"])
        if row
        else None
    )


def save_comment(
    uid: int,
    broadcast_id: int,
    text: str,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
) -> None:

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                INSERT INTO broadcast_comments(
                    broadcast_id,
                    user_id,
                    username,
                    first_name,
                    last_name,
                    comment,
                    created_at
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    broadcast_id,
                    uid,
                    username,
                    first_name,
                    last_name,
                    text,
                    int(time.time()),
                ),
            )

            cursor.execute(
                """
                DELETE FROM pending_comments
                WHERE user_id=%s
                """,
                (uid,),
            )


def comments_text(limit: int = 20) -> str:

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                SELECT
                    c.id,
                    c.broadcast_id,
                    c.user_id,
                    c.username,
                    c.first_name,
                    c.last_name,
                    c.comment,
                    c.created_at,

                    u.username AS live_username,
                    u.first_name AS live_first_name,
                    u.last_name AS live_last_name

                FROM broadcast_comments c

                LEFT JOIN users u
                    ON u.user_id=c.user_id

                ORDER BY c.id DESC

                LIMIT %s
                """,
                (limit,),
            )

            rows = cursor.fetchall()

    if not rows:
        return (
            "💬 COMMENTS\n\n"
            "No comments yet."
        )

    lines = [
        "💬 LATEST COMMENTS",
        "━━━━━━━━━━━━━━━━━━",
        "",
    ]

    for row in rows:

        username = (
            row.get("username")
            or row.get("live_username")
        )

        first = (
            row.get("first_name")
            or row.get("live_first_name")
            or ""
        )

        last = (
            row.get("last_name")
            or row.get("live_last_name")
            or ""
        )

        if username:
            display = "@" + username

        else:
            display = (
                " ".join(
                    part
                    for part in (first, last)
                    if part
                )
                or f"User {row['user_id']}"
            )

        text = (
            row["comment"]
            or ""
        ).replace("\n", " ")[:180]

        lines.append(
            f"Comment #{row['id']} • "
            f"Broadcast #{row['broadcast_id']}"
        )

        lines.append(
            f"👤 {display} "
            f"(ID: {row['user_id']})"
        )

        lines.append(
            f"💬 {text}\n"
        )

    return "\n".join(lines)


# ============================================================
# ADMIN
# ============================================================

def admin_panel() -> dict:

    return {
        "inline_keyboard": [
            [
                {
                    "text": "📊 DAILY USERS",
                    "callback_data": "admin:daily",
                },
                {
                    "text": "📈 STATS",
                    "callback_data": "admin:stats",
                },
            ],
            [
                {
                    "text": "💬 COMMENTS",
                    "callback_data": "admin:comments",
                },
                {
                    "text": "📣 BROADCAST",
                    "callback_data": "admin:broadcast",
                },
            ],
            [
                {
                    "text": "🏆 TOP 10 LIKED",
                    "callback_data": "admin:top",
                },
            ],
            [
                {
                    "text": "📡 TELETHON",
                    "callback_data": "admin:telegram",
                },
            ],
        ]
    }


def admin_dashboard() -> str:

    total, today_n, week_n, _ = daily_stats()

    track_counts = counts()
    track_total = sum(
        track_counts.values()
    )

    recent_24h = 0

    with db() as conn:
        with cur(conn) as cursor:

            cursor.execute(
                """
                SELECT COUNT(*) AS n
                FROM broadcasts
                WHERE created_at >= %s
                """,
                (
                    int(time.time()) - 86400,
                ),
            )

            recent_24h = int(
                cursor.fetchone()["n"]
            )

    return (
        "🛠 NOT YOUR VIBE — ADMIN\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Total Users: {total}\n"
        f"🟢 Today Active: {today_n}\n"
        f"📅 7-Day Active: {week_n}\n"
        f"🎵 Tracks: {track_total}\n"
        f"📣 Broadcasts (24h): {recent_24h}\n\n"
        f"📡 Telethon: "
        f"{'CONNECTED' if ready.is_set() else 'DISCONNECTED'}\n"
        f"🗄 PostgreSQL: "
        f"{'ONLINE' if db_pool else 'OFFLINE'}"
    )


# ============================================================
# CALLBACK PARSER
# ============================================================

def parse_feedback(
    data: str,
) -> Optional[tuple]:

    parts = data.split(":", 3)

    if len(parts) != 4:
        return None

    if parts[0] not in (
        "like",
        "notme",
    ):
        return None

    if parts[1] not in MOODS:
        return None

    try:
        message_id = int(parts[3])
    except (TypeError, ValueError):
        return None

    return (
        parts[0],
        parts[1],
        parts[2],
        message_id,
    )


# ============================================================
# CALLBACK HANDLER
# ============================================================

def callback(callback_query: Mapping[str, Any]) -> None:

    uid = callback_query.get(
        "from",
        {},
    ).get("id")

    message = callback_query.get(
        "message",
        {},
    )

    chat_id = (
        message.get("chat", {})
        .get("id")
    )

    data = callback_query.get(
        "data",
        "",
    )

    if not isinstance(uid, int):
        return

    if not isinstance(chat_id, int):
        return

    register(
        callback_query.get(
            "from",
            {},
        )
    )

    # --------------------------------------------------------
    # ADMIN
    # --------------------------------------------------------

    if data.startswith("admin:"):

        if str(uid) != ADMIN_USER_ID:
            answer(
                callback_query.get("id"),
                "Admin only",
            )
            return

        action = data.split(
            ":",
            1,
        )[1]

        if action == "daily":

            total, today_n, week_n, rows = daily_stats()

            lines = [
                "📊 DAILY USERS",
                "━━━━━━━━━━━━━━━━━━",
                "",
                f"🟢 Today: {today_n}",
                f"📅 Last 7 days unique: {week_n}",
                f"👥 Total users: {total}",
                "",
                "Daily activity:",
            ]

            lines.extend(
                f"{day.isoformat()} → {count}"
                for day, count in rows
            )

            send(
                chat_id,
                "\n".join(lines),
                admin_panel(),
            )

            return

        if action == "stats":

            track_counts = counts()

            send(
                chat_id,
                (
                    "📈 BOT STATS\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    +
                    "\n".join(
                        f"{INFO[mood][0]} → "
                        f"{track_counts[mood]}"
                        for mood in MOODS
                    )
                    +
                    "\n\n"
                    f"📡 Telethon: "
                    f"{'CONNECTED' if ready.is_set() else 'DISCONNECTED'}"
                ),
                admin_panel(),
            )

            return

        if action == "comments":

            send(
                chat_id,
                comments_text(),
                admin_panel(),
            )

            return

        if action == "top":

            send(
                chat_id,
                top_liked_text(),
                admin_panel(),
            )

            return

        if action == "broadcast":

            set_pending_broadcast(
                uid,
                chat_id,
            )

            send(
                chat_id,
                (
                    "📣 BROADCAST\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    "Now send the post you want to broadcast.\n\n"
                    "✅ Text, photo, video, audio, "
                    "document and other Telegram posts are supported.\n"
                    "❌ Send /cancel to stop."
                ),
                admin_panel(),
            )

            return

        if action == "telegram":

            send(
                chat_id,
                (
                    "📡 TELETHON\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    "Status: "
                    +
                    (
                        "🟢 CONNECTED"
                        if ready.is_set()
                        else "🔴 DISCONNECTED"
                    )
                ),
                admin_panel(),
            )

            return

        return

    # --------------------------------------------------------
    # BROADCAST REACTION
    # --------------------------------------------------------

    if data.startswith("br:"):

        parts = data.split(":", 2)

        if len(parts) != 3:
            return

        try:
            broadcast_id = int(parts[1])
        except (TypeError, ValueError):
            return

        reaction = parts[2]

        if reaction not in (
            "love",
            "fire",
            "like",
        ):
            return

        with db() as conn:
            with cur(conn) as cursor:

                cursor.execute(
                    """
                    SELECT 1
                    FROM broadcasts
                    WHERE id=%s
                    """,
                    (broadcast_id,),
                )

                if not cursor.fetchone():

                    answer(
                        callback_query.get("id"),
                        "Broadcast not found",
                    )

                    return

                cursor.execute(
                    """
                    SELECT reaction
                    FROM broadcast_reactions
                    WHERE broadcast_id=%s
                      AND user_id=%s
                    """,
                    (
                        broadcast_id,
                        uid,
                    ),
                )

                old = cursor.fetchone()

                if old and old["reaction"] == reaction:

                    cursor.execute(
                        """
                        DELETE FROM broadcast_reactions
                        WHERE broadcast_id=%s
                          AND user_id=%s
                        """,
                        (
                            broadcast_id,
                            uid,
                        ),
                    )

                else:

                    cursor.execute(
                        """
                        INSERT INTO broadcast_reactions(
                            broadcast_id,
                            user_id,
                            reaction,
                            created_at
                        )
                        VALUES(%s,%s,%s,%s)

                        ON CONFLICT(
                            broadcast_id,
                            user_id
                        )
                        DO UPDATE SET
                            reaction=EXCLUDED.reaction,
                            created_at=EXCLUDED.created_at
                        """,
                        (
                            broadcast_id,
                            uid,
                            reaction,
                            int(time.time()),
                        ),
                    )

        answer(
            callback_query.get("id"),
            "Reaction saved",
        )

        edit_keyboard(
            chat_id,
            int(message.get("message_id")),
            broadcast_buttons(broadcast_id),
        )

        return

    # --------------------------------------------------------
    # BROADCAST COMMENT
    # --------------------------------------------------------

    if data.startswith("bc:"):

        try:
            broadcast_id = int(
                data.split(":", 1)[1]
            )
        except (TypeError, ValueError):
            return

        with db() as conn:
            with cur(conn) as cursor:

                cursor.execute(
                    """
                    SELECT 1
                    FROM broadcasts
                    WHERE id=%s
                    """,
                    (broadcast_id,),
                )

                if not cursor.fetchone():

                    answer(
                        callback_query.get("id"),
                        "Broadcast not found",
                    )

                    return

        set_pending_comment(
            uid,
            broadcast_id,
        )

        answer(
            callback_query.get("id"),
            "Send your comment",
        )

        send(
            chat_id,
            "💬 Send your comment below 👇",
            {
                "force_reply": True,
                "input_field_placeholder": (
                    "Write a comment..."
                ),
            },
        )

        return

    # --------------------------------------------------------
    # MOOD
    # --------------------------------------------------------

    if data.startswith("mood_"):

        mood = data[5:]

        if mood in MOODS:

            if set_mood(uid, mood):

                answer(
                    callback_query.get("id"),
                    f"{INFO[mood][0]} ✓",
                )

                schedule(
                    chat_id,
                    uid,
                    mood,
                    False,
                )

        return

    # --------------------------------------------------------
    # NEXT
    # --------------------------------------------------------

    if data == "next_music":

        mood = get_mood(uid)

        if not mood:

            answer(
                callback_query.get("id"),
                "Choose a mood first",
            )

            send(
                chat_id,
                "🎧 Choose your mood 👇",
                mood_menu(),
            )

        else:

            answer(
                callback_query.get("id"),
                "⏭ Finding next track...",
            )

            schedule(
                chat_id,
                uid,
                mood,
                False,
            )

        return

    # --------------------------------------------------------
    # RADIO
    # --------------------------------------------------------

    if data == "radio":

        mood = get_mood(uid) or "melodic"

        set_radio(
            uid,
            True,
        )

        answer(
            callback_query.get("id"),
            "📻 Personalized Radio...",
        )

        schedule(
            chat_id,
            uid,
            mood,
            True,
        )

        return

    # --------------------------------------------------------
    # CHANGE MOOD
    # --------------------------------------------------------

    if data == "change_mood":

        answer(
            callback_query.get("id"),
            "Choose your mood",
        )

        send(
            chat_id,
            (
                "🎛 MOOD SELECTOR\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "What are you feeling right now?"
            ),
            mood_menu(),
        )

        return

    # --------------------------------------------------------
    # PROFILE
    # --------------------------------------------------------

    if data == "profile":

        send(
            chat_id,
            profile_text(uid),
            {
                "inline_keyboard": [
                    [
                        {
                            "text": "📊 TASTE ANALYTICS",
                            "callback_data": "taste_analytics",
                        }
                    ],
                    [
                        {
                            "text": "🧠 FOR YOU",
                            "callback_data": "for_you",
                        },
                        {
                            "text": "📻 RADIO",
                            "callback_data": "radio",
                        },
                    ],
                    [
                        {
                            "text": "🎛 CHANGE MOOD",
                            "callback_data": "change_mood",
                        }
                    ],
                ]
            },
        )

        return

    # --------------------------------------------------------
    # DAILY VIBE
    # --------------------------------------------------------

    if data == "daily_vibe":

        answer(
            callback_query.get("id"),
            "🔥 Daily Vibe",
        )

        if not daily_vibe_mood(uid):

            send(
                chat_id,
                (
                    "🔥 DAILY VIBE\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    "ဒီနေ့ Mood Menu ကနေ Mood ရွေးပြီး "
                    "နားထောင်ထားတဲ့ Track မရှိသေးလို့ "
                    "Daily Vibe ကို သတ်မှတ်နိုင်သေးပါ။\n\n"
                    "🎛 Mood တစ်ခုရွေးပြီး နားထောင်ကြည့်ပါ။"
                ),
                mood_menu(),
            )

        else:

            send_special_music(
                chat_id,
                uid,
                daily_vibe_track(uid),
                "🔥 YOUR DAILY VIBE",
                "daily_vibe",
            )

        return

    # --------------------------------------------------------
    # FOR YOU
    # --------------------------------------------------------

    if data == "for_you":

        answer(
            callback_query.get("id"),
            "🧠 Personal pick",
        )

        # IMPORTANT:
        # for_you_track() ONLY returns user's liked tracks.
        send_special_music(
            chat_id,
            uid,
            for_you_track(uid),
            "🧠 PICKED FOR YOU",
            "for_you",
        )

        return

    # --------------------------------------------------------
    # SURPRISE
    # --------------------------------------------------------

    if data == "surprise_me":

        answer(
            callback_query.get("id"),
            "🎲 Surprise!",
        )

        send_special_music(
            chat_id,
            uid,
            surprise_track(uid),
            "🎲 SURPRISE ME",
            "surprise_me",
        )

        return

    # --------------------------------------------------------
    # TRENDING
    # --------------------------------------------------------

    if data == "trending":

        answer(
            callback_query.get("id")
        )

        send(
            chat_id,
            trending_text(),
            mood_menu(),
        )

        return

    # --------------------------------------------------------
    # TRACK OF DAY
    # --------------------------------------------------------

    if data == "track_of_day":

        answer(
            callback_query.get("id"),
            "🎵 Track of the Day",
        )

        send_track_of_day_playlist(
            chat_id,
            uid,
        )

        return

    # --------------------------------------------------------
    # TASTE
    # --------------------------------------------------------

    if data == "taste_analytics":

        answer(
            callback_query.get("id")
        )

        send(
            chat_id,
            taste_analytics(uid),
            {
                "inline_keyboard": [
                    [
                        {
                            "text": "🧠 FOR YOU",
                            "callback_data": "for_you",
                        },
                        {
                            "text": "📻 RADIO",
                            "callback_data": "radio",
                        },
                    ],
                    [
                        {
                            "text": "👤 PROFILE",
                            "callback_data": "profile",
                        }
                    ],
                ]
            },
        )

        return

    # --------------------------------------------------------
    # TOP LIKED
    # --------------------------------------------------------

    if data == "top_liked":

        answer(
            callback_query.get("id")
        )

        send(
            chat_id,
            top_liked_text(),
            mood_menu(),
        )

        return

    # --------------------------------------------------------
    # NEW TRACKS
    # --------------------------------------------------------

    if data == "new_tracks":

        answer(
            callback_query.get("id")
        )

        new_tracks(chat_id)

        return

    # --------------------------------------------------------
    # FEEDBACK
    # --------------------------------------------------------

    parsed = parse_feedback(data)

    if parsed:

        action, mood, channel_id, message_id = parsed

        new_feedback = (
            "like"
            if action == "like"
            else "not_for_me"
        )

        old_feedback = feedback(
            uid,
            channel_id,
            message_id,
        )

        if old_feedback == new_feedback:

            clear_feedback(
                uid,
                channel_id,
                message_id,
            )

            answer(
                callback_query.get("id"),
                "Feedback cleared",
            )

        else:

            saved = save_feedback(
                uid,
                channel_id,
                message_id,
                mood,
                new_feedback,
            )

            if saved:

                answer(
                    callback_query.get("id"),
                    (
                        "❤️ Added to your taste"
                        if new_feedback == "like"
                        else
                        "😴 Radio will avoid this"
                    ),
                )

            else:

                answer(
                    callback_query.get("id"),
                    "⚠️ Track not found",
                )

        edit_keyboard(
            chat_id,
            int(message.get("message_id")),
            buttons(
                uid,
                channel_id,
                message_id,
                mood,
            ),
        )


# ============================================================
# COMMANDS
# ============================================================

def command(text: str) -> str:

    if not text.startswith("/"):
        return ""

    return (
        text
        .split(maxsplit=1)[0]
        .lower()
        .split("@", 1)[0]
    )


# ============================================================
# MESSAGE HANDLER
# ============================================================

def message(update_message: Mapping[str, Any]) -> None:

    chat_id = (
        update_message
        .get("chat", {})
        .get("id")
    )

    user = update_message.get(
        "from",
        {},
    )

    uid = user.get("id")

    if not isinstance(chat_id, int):
        return

    if not isinstance(uid, int):
        return

    register(user)

    text = (
        update_message
        .get("text")
        or ""
    ).strip()

    cmd = command(text)

    # --------------------------------------------------------
    # ADMIN BROADCAST INPUT
    # --------------------------------------------------------

    pending_broadcast = (
        get_pending_broadcast(uid)
        if str(uid) == ADMIN_USER_ID
        else None
    )

    if pending_broadcast:

        raw_text = (
            update_message.get("text")
            or update_message.get("caption")
            or ""
        ).strip()

        if raw_text.startswith("/cancel"):

            clear_pending_broadcast(uid)

            send(
                chat_id,
                "❌ Broadcast cancelled.",
                admin_panel(),
            )

            return

        if update_message.get("message_id"):

            clear_pending_broadcast(uid)

            if update_message.get("text"):
                content_type = "text"

            elif update_message.get("photo"):
                content_type = "photo"

            elif update_message.get("video"):
                content_type = "video"

            elif update_message.get("audio"):
                content_type = "audio"

            elif update_message.get("document"):
                content_type = "document"

            elif update_message.get("animation"):
                content_type = "animation"

            else:
                content_type = "media"

            broadcast_id = start_broadcast(
                uid,
                chat_id,
                int(update_message["message_id"]),
                content_type,
                raw_text or None,
            )

            send(
                chat_id,
                (
                    f"📣 Broadcast #{broadcast_id} started.\n\n"
                    f"📦 Type: {content_type}\n"
                    "❤️ 🔥 👍 reactions + 💬 comments are enabled."
                ),
            )

            return

    # --------------------------------------------------------
    # COMMENT INPUT
    # --------------------------------------------------------

    pending_comment = get_pending_comment(uid)

    if (
        pending_comment
        and text
        and not text.startswith("/")
    ):

        comment_text = text[:2000]

        save_comment(
            uid,
            pending_comment,
            comment_text,
            user.get("username"),
            user.get("first_name"),
            user.get("last_name"),
        )

        username = user.get("username")

        first = user.get("first_name") or ""
        last = user.get("last_name") or ""

        display = (
            "@" + username
            if username
            else
            (
                " ".join(
                    part
                    for part in (first, last)
                    if part
                )
                or f"User {uid}"
            )
        )

        send(
            int(ADMIN_USER_ID),
            (
                "💬 NEW COMMENT\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"Broadcast: #{pending_comment}\n"
                f"👤 {display} (ID: {uid})\n\n"
                f"{comment_text}"
            ),
        )

        send(
            chat_id,
            (
                "✅ Thanks! Your comment was sent "
                "to the Not Your Vibe team."
            ),
            mood_menu(),
        )

        return

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    if cmd in (
        "/start",
        "/mood",
    ):

        send(
            chat_id,
            (
                "🎧 NOT YOUR VIBE\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Your music. Your mood. Your radio.\n\n"
                "Choose a mood 👇"
            ),
            mood_menu(),
        )

        return

    # --------------------------------------------------------
    # NEXT
    # --------------------------------------------------------

    if cmd == "/next":

        mood = get_mood(uid)

        if not mood:

            send(
                chat_id,
                "🎧 Choose your mood first 👇",
                mood_menu(),
            )

        else:

            schedule(
                chat_id,
                uid,
                mood,
                False,
            )

        return

    # --------------------------------------------------------
    # RADIO
    # --------------------------------------------------------

    if cmd == "/radio":

        mood = get_mood(uid) or "melodic"

        set_radio(
            uid,
            True,
        )

        schedule(
            chat_id,
            uid,
            mood,
            True,
        )

        return

    # --------------------------------------------------------
    # PROFILE
    # --------------------------------------------------------

    if cmd == "/profile":

        send(
            chat_id,
            profile_text(uid),
            mood_menu(),
        )

        return

    # --------------------------------------------------------
    # NEW
    # --------------------------------------------------------

    if cmd == "/new":

        new_tracks(chat_id)

        return

    # --------------------------------------------------------
    # TOP
    # --------------------------------------------------------

    if cmd == "/top":

        send(
            chat_id,
            top_liked_text(),
            mood_menu(),
        )

        return

    # --------------------------------------------------------
    # DAILY VIBE
    # --------------------------------------------------------

    if cmd == "/dailyvibe":

        if not daily_vibe_mood(uid):

            send(
                chat_id,
                (
                    "🔥 DAILY VIBE\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    "ဒီနေ့ Mood Menu ကနေ Mood ရွေးပြီး "
                    "နားထောင်ထားတဲ့ Track မရှိသေးလို့ "
                    "Daily Vibe ကို သတ်မှတ်နိုင်သေးပါ။\n\n"
                    "🎛 Mood တစ်ခုရွေးပြီး နားထောင်ကြည့်ပါ။"
                ),
                mood_menu(),
            )

        else:

            send_special_music(
                chat_id,
                uid,
                daily_vibe_track(uid),
                "🔥 YOUR DAILY VIBE",
                "daily_vibe",
            )

        return

    # --------------------------------------------------------
    # FOR YOU
    # --------------------------------------------------------

    if cmd == "/foryou":

        send_special_music(
            chat_id,
            uid,
            for_you_track(uid),
            "🧠 PICKED FOR YOU",
            "for_you",
        )

        return

    # --------------------------------------------------------
    # SURPRISE
    # --------------------------------------------------------

    if cmd == "/surprise":

        send_special_music(
            chat_id,
            uid,
            surprise_track(uid),
            "🎲 SURPRISE ME",
            "surprise_me",
        )

        return

    # --------------------------------------------------------
    # TRENDING
    # --------------------------------------------------------

    if cmd == "/trending":

        send(
            chat_id,
            trending_text(),
            mood_menu(),
        )

        return

    # --------------------------------------------------------
    # TODAY
    # --------------------------------------------------------

    if cmd == "/today":

        send_track_of_day_playlist(
            chat_id,
            uid,
        )

        return

    # --------------------------------------------------------
    # TASTE
    # --------------------------------------------------------

    if cmd == "/taste":

        send(
            chat_id,
            taste_analytics(uid),
            mood_menu(),
        )

        return

    # --------------------------------------------------------
    # HELP
    # --------------------------------------------------------

    if cmd == "/help":

        send(
            chat_id,
            (
                "🎧 NOT YOUR VIBE\n\n"
                "/start /mood /next /radio /profile "
                "/new /top /help\n\n"
                "🔥 Daily Vibe • 🧠 For You • "
                "🎲 Surprise Me\n"
                "📈 Trending • 🎵 Track of the Day • "
                "📊 Taste Analytics\n\n"
                "❤️ Like = improves Radio\n"
                "😴 = avoid this track/mood signal\n"
                "🧠 For You = YOUR LIKED TRACKS ONLY\n"
                "🎯 Daily Vibe counts only your Mood-selected listening."
            ),
        )

        return

    # --------------------------------------------------------
    # ADMIN
    # --------------------------------------------------------

    if cmd == "/admin":

        if str(uid) != ADMIN_USER_ID:
            send(
                chat_id,
                "❌ Admin only.",
            )
            return

        send(
            chat_id,
            admin_dashboard(),
            admin_panel(),
        )

        return

    # --------------------------------------------------------
    # BROADCAST
    # --------------------------------------------------------

    if cmd == "/broadcast":

        if str(uid) != ADMIN_USER_ID:
            send(
                chat_id,
                "❌ Admin only.",
            )
            return

        set_pending_broadcast(
            uid,
            chat_id,
        )

        send(
            chat_id,
            (
                "📣 BROADCAST\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Now send the post you want to broadcast.\n\n"
                "✅ Text, photo, video, audio, "
                "document and other Telegram posts are supported.\n"
                "❌ Send /cancel to stop."
            ),
            admin_panel(),
        )

        return

    # --------------------------------------------------------
    # DAILY ADMIN
    # --------------------------------------------------------

    if cmd == "/daily":

        if str(uid) != ADMIN_USER_ID:
            send(
                chat_id,
                "❌ Admin only.",
            )
            return

        total, today_n, week_n, rows = daily_stats()

        lines = [
            "📊 DAILY USERS",
            "━━━━━━━━━━━━━━━━━━",
            f"Today: {today_n}",
            f"Last 7 days unique: {week_n}",
            f"Total users: {total}",
            "",
            "Last 7 days:",
        ]

        lines.extend(
            f"{day.isoformat()} → {count}"
            for day, count in rows
        )

        send(
            chat_id,
            "\n".join(lines),
        )

        return

    # --------------------------------------------------------
    # COMMENTS ADMIN
    # --------------------------------------------------------

    if cmd == "/comments":

        if str(uid) != ADMIN_USER_ID:
            send(
                chat_id,
                "❌ Admin only.",
            )
            return

        send(
            chat_id,
            comments_text(),
        )

        return

    # --------------------------------------------------------
    # STATS ADMIN
    # --------------------------------------------------------

    if cmd == "/stats":

        if str(uid) != ADMIN_USER_ID:
            send(
                chat_id,
                "❌ Admin only.",
            )
            return

        track_counts = counts()

        send(
            chat_id,
            (
                "📊 TRACKS\n\n"
                +
                "\n".join(
                    f"{INFO[mood][0]} → "
                    f"{track_counts[mood]}"
                    for mood in MOODS
                )
                +
                "\n\n"
                f"Telethon: "
                f"{'CONNECTED' if ready.is_set() else 'DISCONNECTED'}"
            ),
        )

        return

    # --------------------------------------------------------
    # TELETHON
    # --------------------------------------------------------

    if cmd == "/telegram":

        send(
            chat_id,
            (
                "🟢 TELETHON CONNECTED"
                if ready.is_set()
                else
                "🔴 TELETHON DISCONNECTED"
            ),
        )

        return


# ============================================================
# UPDATE ROUTER
# ============================================================

def update(data: Mapping[str, Any]) -> None:

    try:

        callback_query = data.get(
            "callback_query"
        )

        if isinstance(
            callback_query,
            Mapping,
        ):

            callback(
                callback_query
            )

            return

        telegram_message = data.get(
            "message"
        )

        if isinstance(
            telegram_message,
            Mapping,
        ):

            message(
                telegram_message
            )

    except Exception:

        log.exception(
            "Update processing error"
        )


# ============================================================
# MUSIC / TELETHON
# ============================================================

def is_music(msg: Any) -> bool:

    if getattr(msg, "audio", None):
        return True

    document = getattr(
        msg,
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

    try:
        filename = (
            getattr(
                getattr(
                    msg,
                    "file",
                    None,
                ),
                "name",
                "",
            )
            or ""
        ).lower()
    except Exception:
        filename = ""

    return filename.endswith(AUDIO)


def message_title(msg: Any) -> Optional[str]:

    try:

        file_object = getattr(
            msg,
            "file",
            None,
        )

        filename = (
            getattr(
                file_object,
                "name",
                "",
            )
            or ""
        ).strip()

        if filename:
            return (
                filename
                .rsplit("/", 1)[-1]
                [:200]
            )

    except Exception:
        pass

    try:

        text = (
            getattr(
                msg,
                "message",
                "",
            )
            or ""
        ).strip()

        if text:
            return text[:200]

    except Exception:
        pass

    return None


# ============================================================
# TELETHON SCAN
# ============================================================

async def scan(
    mood: str,
    channel_value: str,
) -> int:

    if not channel_value:
        return 0

    if client is None:
        return 0

    try:

        entity = await client.get_entity(
            int(channel_value)
            if str(channel_value).lstrip("-").isdigit()
            else channel_value
        )

        channel_id = normch(
            getattr(
                entity,
                "id",
                0,
            )
        )

        if not channel_id:
            return 0

        count = 0

        async for msg in client.iter_messages(entity):

            if not is_music(msg):
                continue

            try:

                saved = save_track(
                    mood,
                    channel_id,
                    msg.id,
                    message_title(msg),
                )

                if saved:
                    count += 1

            except Exception:

                log.exception(
                    "save_track failed "
                    "mood=%s message=%s",
                    mood,
                    getattr(msg, "id", None),
                )

        return count

    except Exception:

        log.exception(
            "scan failed mood=%s channel=%s",
            mood,
            channel_value,
        )

        return 0


async def scan_all() -> None:

    global last_scan

    if client is None:
        return

    channel_map.clear()

    for mood, value in CHANNELS.items():

        if not value:
            continue

        normalized = normch(value)

        if normalized:
            channel_map[normalized] = mood

    total_saved = 0

    for mood, value in CHANNELS.items():

        if not value:
            continue

        saved = await scan(
            mood,
            value,
        )

        total_saved += saved

        await asyncio.sleep(0.3)

    last_scan = int(
        time.time()
    )

    log.info(
        "Telethon scan completed | "
        "saved=%s | tracks=%s",
        total_saved,
        counts(force=True),
    )


# ============================================================
# PERIODIC SCAN
# ============================================================

async def periodic_scan() -> None:

    while True:

        await asyncio.sleep(
            SCAN_INTERVAL
        )

        try:

            if (
                client is not None
                and ready.is_set()
            ):
                log.info(
                    "Running periodic channel scan..."
                )

                await scan_all()

        except Exception:

            log.exception(
                "periodic scan failed"
            )


# ============================================================
# TELETHON WORKER
# ============================================================

def tele_worker() -> None:

    global client
    global tele_loop

    if not (
        API_ID
        and API_HASH
        and SESSION
    ):

        log.error(
            "Missing Telethon "
            "API_ID/API_HASH/SESSION"
        )

        return

    try:

        api_id = int(API_ID)

    except (TypeError, ValueError):

        log.error(
            "TELETHON_API_ID/API_ID "
            "must be an integer"
        )

        return

    try:

        client = TelegramClient(
            StringSession(SESSION),
            api_id,
            API_HASH,
            connection_retries=10,
            retry_delay=5,
            timeout=30,
            auto_reconnect=True,
        )

        @client.on(
            events.NewMessage(
                incoming=True
            )
        )
        async def new_message(event):

            try:

                channel_id = normch(
                    event.chat_id
                )

                mood = channel_map.get(
                    channel_id
                )

                if (
                    mood
                    and is_music(
                        event.message
                    )
                ):

                    save_track(
                        mood,
                        channel_id,
                        event.message.id,
                        message_title(
                            event.message
                        ),
                    )

            except Exception:

                log.exception(
                    "Telethon watcher error"
                )

        async def run():

            global tele_loop

            tele_loop = (
                asyncio.get_running_loop()
            )

            while True:

                try:

                    await client.connect()

                    if not await client.is_user_authorized():

                        log.error(
                            "Telethon unauthorized"
                        )

                        return

                    ready.set()

                    log.info(
                        "🟢 Telethon connected"
                    )

                    await scan_all()

                    # Periodic scan runs inside
                    # the same Telethon event loop.
                    periodic_task = asyncio.create_task(
                        periodic_scan()
                    )

                    try:
                        await client.run_until_disconnected()

                    finally:

                        periodic_task.cancel()

                        try:
                            await periodic_task
                        except asyncio.CancelledError:
                            pass

                except Exception:

                    log.exception(
                        "Telethon error"
                    )

                finally:

                    ready.clear()

                    try:

                        if (
                            client
                            and client.is_connected()
                        ):
                            await client.disconnect()

                    except Exception:

                        pass

                log.warning(
                    "Telethon reconnecting "
                    "in %s seconds...",
                    RECONNECT,
                )

                await asyncio.sleep(
                    RECONNECT
                )

        asyncio.run(run())

    except Exception:

        log.exception(
            "Telethon worker crashed"
        )


def start_telethon() -> None:

    global tele_thread

    with tele_lock:

        if (
            tele_thread
            and tele_thread.is_alive()
        ):
            return

        tele_thread = threading.Thread(
            target=tele_worker,
            name="telethon-worker",
            daemon=True,
        )

        tele_thread.start()


# ============================================================
# WEBHOOK
# ============================================================

def webhook_setup() -> None:

    if not BOT_TOKEN:
        log.warning(
            "BOT_TOKEN missing; webhook skipped"
        )
        return

    if not RENDER_EXTERNAL_URL:
        log.warning(
            "RENDER_EXTERNAL_URL missing; "
            "webhook skipped"
        )
        return

    data = {
        "url": (
            RENDER_EXTERNAL_URL.rstrip("/")
            + "/webhook"
        ),
        "allowed_updates": [
            "message",
            "callback_query",
        ],
        "max_connections": 40,
    }

    if WEBHOOK_SECRET:
        data["secret_token"] = (
            WEBHOOK_SECRET
        )

    result = tg(
        "setWebhook",
        data,
        20,
    )

    if result.get("ok"):
        log.info(
            "Telegram webhook configured"
        )
    else:
        log.error(
            "Webhook setup failed: %s",
            result.get("description"),
        )


# ============================================================
# CLEANUP WORKER
# ============================================================

def cleanup_worker() -> None:

    while True:

        try:

            cleanup_pending()

        except Exception:

            log.exception(
                "background cleanup"
            )

        time.sleep(300)


# ============================================================
# STARTUP
# ============================================================

def startup() -> bool:

    if not BOT_TOKEN:
        log.error(
            "BOT_TOKEN is required"
        )
        return False

    if not DATABASE_URL:
        log.error(
            "DATABASE_URL is required"
        )
        return False

    try:

        init_db()

    except Exception:

        log.exception(
            "Database initialization failed"
        )

        return False

    webhook_setup()

    start_telethon()

    threading.Thread(
        target=cleanup_worker,
        name="cleanup-worker",
        daemon=True,
    ).start()

    log.info(
        "🟢 NOT YOUR VIBE BOT READY"
    )

    return True


# ============================================================
# FLASK
# ============================================================

@app.route("/")
def home():

    return (
        "🎧 NOT YOUR VIBE MUSIC BOT ONLINE"
    )


@app.route("/health")
def health():

    try:

        with db() as conn:
            with cur(conn) as cursor:

                cursor.execute(
                    "SELECT 1"
                )

        return "OK", 200

    except Exception:

        return (
            "Database not ready",
            503,
        )


@app.route("/status")
def status():

    try:
        track_counts = counts()
    except Exception:
        track_counts = {
            mood: 0
            for mood in MOODS
        }

    return {
        "bot": "online",
        "ai": False,
        "database": (
            "online"
            if db_pool
            else "offline"
        ),
        "telethon": (
            "connected"
            if ready.is_set()
            else "disconnected"
        ),
        "tracks": track_counts,
        "last_scan": last_scan,
    }


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

        return "Forbidden", 403

    try:

        data = request.get_json(
            silent=True
        )

        if isinstance(
            data,
            Mapping,
        ):
            update(data)

    except Exception:

        log.exception(
            "Webhook processing error"
        )

    return "OK", 200


# ============================================================
# MUSIC WORKER
# ============================================================

def schedule(
    chat_id: int,
    uid: int,
    mood: str,
    radio: bool = False,
) -> bool:

    with pending_lock:

        if uid in pending:
            return False

        pending.add(uid)

    def worker():

        try:

            send_music(
                chat_id,
                uid,
                mood,
                radio,
            )

        except Exception:

            log.exception(
                "Music worker error"
            )

            try:
                send(
                    chat_id,
                    "⚠️ Something went wrong. Please try again.",
                    mood_menu(),
                )
            except Exception:
                pass

        finally:

            with pending_lock:
                pending.discard(uid)

    executor.submit(
        worker
    )

    return True


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    if not startup():
        raise SystemExit(1)

    port = geti(
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
