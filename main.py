from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import threading
import math
import time

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Mapping

import requests
from flask import Flask, request

from psycopg2 import InterfaceError, OperationalError
from psycopg2.extras import RealDictCursor
from psycopg2.pool import PoolError, ThreadedConnectionPool

from telethon import TelegramClient, events
from telethon.sessions import StringSession


# =========================================================
# APP / LOGGING
# =========================================================

app = Flask(__name__)

logging.basicConfig(
    level=(os.getenv("LOG_LEVEL") or "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
)

log = logging.getLogger("nyv")


# =========================================================
# ENV HELPERS
# =========================================================

def env(n, d=""):
    return (os.getenv(n, d) or "").strip()


def geti(n, d, lo, hi):
    try:
        v = int(env(n, str(d)))
    except (TypeError, ValueError):
        return d

    return v if lo <= v <= hi else d


def norm_db(u):
    if not u:
        return ""

    return (
        "postgresql://" + u[11:]
        if u.startswith("postgres://")
        else u
    )


# =========================================================
# ENVIRONMENT
# =========================================================

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

HTTP_TIMEOUT = geti(
    "TELEGRAM_HTTP_TIMEOUT",
    20,
    5,
    120,
)

WORKERS = geti(
    "MUSIC_WORKER_COUNT",
    4,
    1,
    12,
)

POOL_MAX = geti(
    "DB_POOL_MAX_CONNECTIONS",
    8,
    2,
    30,
)

SCAN_INTERVAL = geti(
    "AUTO_SCAN_INTERVAL",
    300,
    60,
    3600,
)

RECONNECT = geti(
    "TELETHON_RECONNECT_DELAY",
    10,
    3,
    120,
)

HISTORY_LIMIT = geti(
    "RADIO_HISTORY_LIMIT",
    100,
    10,
    1000,
)

TRENDING_DAYS = geti(
    "TRENDING_DAYS",
    7,
    1,
    30,
)


# =========================================================
# MOODS
# =========================================================

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


# =========================================================
# CHANNELS
# =========================================================

CHANNELS = {
    m: env(m.upper() + "_CHANNEL")
    for m in MOODS
}

CHANNELS["hype"] = env(
    "HYPE_CHANNEL",
    "-1004427220481",
)

CHANNELS["melodic"] = env(
    "MELODIC_CHANNEL",
    "-1004446996297",
)


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


# =========================================================
# GLOBALS
# =========================================================

db_pool = None
db_lock = threading.Lock()

client = None
tele_loop = None

ready = threading.Event()

tele_thread = None
tele_lock = threading.Lock()

executor = ThreadPoolExecutor(
    max_workers=WORKERS,
    thread_name_prefix="music",
)

# Per-user music scheduling state.
# Generation tokens invalidate stale queued jobs when mode changes.
pending = {}
pending_lock = threading.Lock()
user_music_locks = {}
user_music_locks_guard = threading.Lock()
music_generation = {}

channel_map = {}
last_scan = 0

http_local = threading.local()


# =========================================================
# DATABASE
# =========================================================

@contextmanager
def db():
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

    c = None

    try:

        c = db_pool.getconn()
        c.autocommit = False

        try:

            with c.cursor() as x:
                x.execute("SELECT 1")

        except (
            OperationalError,
            InterfaceError,
        ):

            db_pool.putconn(
                c,
                close=True,
            )

            c = db_pool.getconn()
            c.autocommit = False

        yield c

        c.commit()

    except Exception:

        if c:

            try:
                c.rollback()
            except Exception:
                pass

        raise

    finally:

        if c:

            try:

                db_pool.putconn(c)

            except (
                PoolError,
                OperationalError,
                InterfaceError,
            ):

                pass


@contextmanager
def cur(c):

    x = c.cursor(
        cursor_factory=RealDictCursor
    )

    try:
        yield x
    finally:
        x.close()


# =========================================================
# DATABASE INIT
# =========================================================

def init_db():

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

    CREATE INDEX IF NOT EXISTS idx_tracks_mood ON tracks(mood);
    CREATE INDEX IF NOT EXISTS idx_tracks_created ON tracks(created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_hist_user ON user_history(user_id,sent_at DESC);
    CREATE INDEX IF NOT EXISTS idx_hist_track ON user_history(user_id,channel_id,message_id,sent_at DESC);
    CREATE INDEX IF NOT EXISTS idx_hist_recent ON user_history(sent_at DESC,channel_id,message_id);
    CREATE INDEX IF NOT EXISTS idx_fb_user ON track_feedback(user_id);
    CREATE INDEX IF NOT EXISTS idx_fb_track_feedback ON track_feedback(channel_id,message_id,feedback);
    CREATE INDEX IF NOT EXISTS idx_fb_recent ON track_feedback(created_at DESC,feedback);
    CREATE INDEX IF NOT EXISTS idx_daily_day ON daily_activity(day);
    CREATE INDEX IF NOT EXISTS idx_bc_broadcast ON broadcast_comments(broadcast_id);

    ALTER TABLE tracks ADD COLUMN IF NOT EXISTS title TEXT;

    -- Additive Audio Analyzer schema migration. Existing tracks/data are preserved.
    ALTER TABLE tracks ADD COLUMN IF NOT EXISTS analyzed BOOLEAN NOT NULL DEFAULT FALSE;
    ALTER TABLE tracks ADD COLUMN IF NOT EXISTS ai_error TEXT;
    ALTER TABLE tracks ADD COLUMN IF NOT EXISTS bpm DOUBLE PRECISION;
    ALTER TABLE tracks ADD COLUMN IF NOT EXISTS musical_key TEXT;
    ALTER TABLE tracks ADD COLUMN IF NOT EXISTS energy DOUBLE PRECISION;
    ALTER TABLE tracks ADD COLUMN IF NOT EXISTS danceability DOUBLE PRECISION;
    ALTER TABLE tracks ADD COLUMN IF NOT EXISTS loudness DOUBLE PRECISION;
    ALTER TABLE tracks ADD COLUMN IF NOT EXISTS genre TEXT;
    ALTER TABLE tracks ADD COLUMN IF NOT EXISTS subgenre TEXT;
    ALTER TABLE tracks ADD COLUMN IF NOT EXISTS analyzer_mood TEXT;
    ALTER TABLE tracks ADD COLUMN IF NOT EXISTS analyzed_at TIMESTAMPTZ;

    CREATE INDEX IF NOT EXISTS idx_tracks_analyzed ON tracks(analyzed);
    CREATE INDEX IF NOT EXISTS idx_tracks_analyzer_mood ON tracks(analyzer_mood);

    ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS source_chat_id BIGINT;
    ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS source_message_id BIGINT;
    ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS content_type TEXT;
    ALTER TABLE broadcasts ALTER COLUMN text DROP NOT NULL;
    """

    with db() as c:
        with cur(c) as x:
            x.execute(schema)



# =========================================================
# USER
# =========================================================

def register(u):

    uid = u.get("id")

    if not isinstance(uid, int):
        return

    now = int(time.time())

    with db() as c:

        with cur(c) as x:

            x.execute(
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
                    u.get("username"),
                    u.get("first_name"),
                    u.get("last_name"),
                    now,
                    now,
                ),
            )

            day = datetime.now(
                ZoneInfo("Asia/Yangon")
            ).date()

            x.execute(
                """
                INSERT INTO daily_activity(
                    user_id,
                    day
                )
                VALUES(%s,%s)
                ON CONFLICT DO NOTHING
                """,
                (
                    uid,
                    day,
                ),
            )


# =========================================================
# PER-USER MUSIC MODE / GENERATION
# =========================================================

def _music_lock(uid):
    uid = int(uid)
    with user_music_locks_guard:
        lock = user_music_locks.get(uid)
        if lock is None:
            lock = threading.RLock()
            user_music_locks[uid] = lock
        return lock


def _music_generation(uid):
    return int(music_generation.get(int(uid), 0))


def _bump_music_generation(uid):
    uid = int(uid)
    music_generation[uid] = _music_generation(uid) + 1
    return music_generation[uid]


# =========================================================
# SPECIAL MODE MEMORY
# =========================================================

SPECIAL_MODE = {}

def set_special_mode(uid, mode):
    SPECIAL_MODE[int(uid)] = mode

def get_special_mode(uid):
    return SPECIAL_MODE.get(int(uid))

# =========================================================
# USER STATE
# =========================================================

def set_mood(uid, mood):

    if mood not in MOODS:
        return False

    with _music_lock(uid):
        with db() as c:
            with cur(c) as x:
                x.execute(
                    """
                    INSERT INTO user_state(user_id,mood,radio_enabled,updated_at)
                    VALUES(%s,%s,FALSE,%s)
                    ON CONFLICT(user_id) DO UPDATE SET
                        mood=EXCLUDED.mood,
                        radio_enabled=FALSE,
                        updated_at=EXCLUDED.updated_at
                    """,
                    (uid, mood, int(time.time())),
                )
        _bump_music_generation(uid)

    return True



def get_state(uid):

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                SELECT
                    mood,
                    radio_enabled
                FROM user_state
                WHERE user_id=%s
                """,
                (uid,),
            )

            r = x.fetchone()

    if not r:

        return {
            "mood": None,
            "radio": False,
        }

    mood = r["mood"]

    if mood not in MOODS:
        mood = None

    return {
        "mood": mood,
        "radio": bool(
            r["radio_enabled"]
        ),
    }


def get_mood(uid):

    return get_state(uid)["mood"]


def is_radio(uid):

    return get_state(uid)["radio"]


def set_radio(uid, on=True):

    with _music_lock(uid):
        with db() as c:
            with cur(c) as x:
                x.execute(
                    """
                    INSERT INTO user_state(user_id,mood,radio_enabled,updated_at)
                    VALUES(%s,NULL,%s,%s)
                    ON CONFLICT(user_id) DO UPDATE SET
                        radio_enabled=EXCLUDED.radio_enabled,
                        updated_at=EXCLUDED.updated_at
                    """,
                    (uid, bool(on), int(time.time())),
                )
        _bump_music_generation(uid)



# =========================================================
# TRACKS
# =========================================================

def save_track(
    mood,
    ch,
    msg,
    title=None,
):

    if (
        mood not in MOODS
        or not ch
        or not msg
    ):
        return False

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                INSERT INTO tracks(
                    mood,
                    channel_id,
                    message_id,
                    created_at,
                    title
                )
                VALUES(%s,%s,%s,%s,%s)

                ON CONFLICT(
                    channel_id,
                    message_id
                )
                DO UPDATE SET
                    mood=EXCLUDED.mood,
                    title=COALESCE(
                        EXCLUDED.title,
                        tracks.title
                    )
                RETURNING id
                """,
                (
                    mood,
                    str(ch),
                    int(msg),
                    int(time.time()),
                    title,
                ),
            )

            return x.fetchone() is not None


def counts():

    r = {
        m: 0
        for m in MOODS
    }

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                SELECT
                    mood,
                    COUNT(*) AS count
                FROM tracks
                GROUP BY mood
                """
            )

            for a in x.fetchall():

                if a["mood"] in r:
                    r[a["mood"]] = int(
                        a["count"]
                    )

    return r


def get_track(track_id):

    try:
        track_id = int(track_id)
    except Exception:
        return None

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                SELECT
                    id,
                    mood,
                    channel_id,
                    message_id,
                    title,
                    bpm,
                    musical_key,
                    energy,
                    danceability,
                    loudness,
                    genre,
                    subgenre,
                    analyzer_mood,
                    analyzed
                FROM tracks
                WHERE id=%s
                """,
                (track_id,),
            )

            return x.fetchone()


# =========================================================
# FEEDBACK
# =========================================================

def feedback_map(uid):

    r = {}

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                SELECT
                    channel_id,
                    message_id,
                    feedback
                FROM track_feedback
                WHERE user_id=%s
                """,
                (uid,),
            )

            for a in x.fetchall():

                r[
                    (
                        str(a["channel_id"]),
                        int(a["message_id"]),
                    )
                ] = a["feedback"]

    return r


def feedback(uid, ch, msg):

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                SELECT feedback
                FROM track_feedback
                WHERE user_id=%s
                  AND channel_id=%s
                  AND message_id=%s
                """,
                (
                    uid,
                    str(ch),
                    int(msg),
                ),
            )

            r = x.fetchone()

    return (
        r["feedback"]
        if r
        else None
    )


def save_feedback(
    uid,
    ch,
    msg,
    mood,
    fb,
):

    if (
        fb not in (
            "like",
            "not_for_me",
        )
        or mood not in MOODS
    ):
        return False

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                SELECT 1
                FROM tracks
                WHERE channel_id=%s
                  AND message_id=%s
                """,
                (
                    str(ch),
                    int(msg),
                ),
            )

            if not x.fetchone():
                return False

            x.execute(
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

                ON CONFLICT(
                    user_id,
                    channel_id,
                    message_id
                )
                DO UPDATE SET
                    mood=EXCLUDED.mood,
                    feedback=EXCLUDED.feedback,
                    created_at=EXCLUDED.created_at
                """,
                (
                    uid,
                    str(ch),
                    int(msg),
                    mood,
                    fb,
                    int(time.time()),
                ),
            )

    return True


def clear_feedback(uid, ch, msg):

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                DELETE FROM track_feedback
                WHERE user_id=%s
                  AND channel_id=%s
                  AND message_id=%s
                """,
                (
                    uid,
                    str(ch),
                    int(msg),
                ),
            )


# =========================================================
# HISTORY
# =========================================================

def history(uid):

    r = set()

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                SELECT
                    channel_id,
                    message_id
                FROM user_history
                WHERE user_id=%s
                  AND action='served'
                ORDER BY
                    sent_at DESC,
                    id DESC
                LIMIT %s
                """,
                (
                    uid,
                    HISTORY_LIMIT,
                ),
            )

            for a in x.fetchall():

                r.add(
                    (
                        str(a["channel_id"]),
                        int(a["message_id"]),
                    )
                )

    return r


def record(
    uid,
    mood,
    ch,
    msg,
):

    with db() as c:

        with cur(c) as x:

            x.execute(
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
                    %s,%s,%s,%s,
                    'served',%s
                )
                """,
                (
                    uid,
                    mood,
                    str(ch),
                    int(msg),
                    int(time.time()),
                ),
            )


# =========================================================
# TRACK CANDIDATES
# =========================================================

def candidates(
    mood,
    limit=250,
):

    with db() as c:

        with cur(c) as x:

            x.execute(
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
                    limit,
                ),
            )

            return [
                (
                    int(a["message_id"]),
                    str(a["channel_id"]),
                )
                for a in x.fetchall()
            ]


# =========================================================
# BEHAVIOR ENGINE
# =========================================================

def ratios(uid):

    out = {
        m: {
            "like": 0,
            "not": 0,
        }
        for m in MOODS
    }

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                SELECT
                    mood,
                    feedback,
                    COUNT(*) AS count
                FROM track_feedback
                WHERE user_id=%s
                GROUP BY mood,feedback
                """,
                (uid,),
            )

            for a in x.fetchall():

                if a["mood"] not in out:
                    continue

                if a["feedback"] == "like":

                    out[a["mood"]]["like"] = int(
                        a["count"]
                    )

                elif a["feedback"] == "not_for_me":

                    out[a["mood"]]["not"] = int(
                        a["count"]
                    )

    return out


def _radio_key_parts(value):
    if not value:
        return None, None

    parts = str(value).strip().lower().split()
    if not parts:
        return None, None

    root = parts[0].replace("♯", "#").replace("♭", "b")
    mode = parts[1] if len(parts) > 1 else None
    return root, mode


def _numeric_similarity(value, target, scale):
    if value is None or target is None:
        return None

    try:
        value = float(value)
        target = float(target)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(value) or not math.isfinite(target):
        return None

    return max(0.0, 1.0 - abs(value - target) / scale)


def _radio_profile(uid):
    """
    Build a rule-based taste profile from the user's recent positive and
    negative feedback.

    This deliberately uses no AI/ML model.  It is a weighted nearest-neighbor
    style profile: recent Likes matter more than old Likes, and Not For Me
    tracks create a negative taste boundary.
    """
    profile = {
        "likes": [],
        "dislikes": [],
        "bpm": None,
        "energy": None,
        "danceability": None,
        "loudness": None,
        "keys": {},
        "analyzer_moods": {},
        "genres": {},
        "subgenres": {},
    }

    with db() as c:
        with cur(c) as x:
            x.execute(
                """
                SELECT
                    f.feedback,
                    f.created_at,
                    t.bpm,
                    t.musical_key,
                    t.energy,
                    t.danceability,
                    t.loudness,
                    t.genre,
                    t.subgenre,
                    t.analyzer_mood,
                    t.mood,
                    t.channel_id,
                    t.message_id
                FROM track_feedback f
                JOIN tracks t
                  ON t.channel_id=f.channel_id
                 AND t.message_id=f.message_id
                WHERE f.user_id=%s
                ORDER BY f.created_at DESC
                LIMIT 160
                """,
                (uid,),
            )
            rows = x.fetchall()

    likes = [r for r in rows if r.get("feedback") == "like"]
    dislikes = [r for r in rows if r.get("feedback") == "not_for_me"]

    # Keep the most recent Likes as individual "seeds".  Spotify-like radio
    # behaviour is much closer to seed similarity than a single global average.
    profile["likes"] = likes[:40]
    profile["dislikes"] = dislikes[:40]

    # Weighted centroid: recent feedback gets more influence.
    for field in ("bpm", "energy", "danceability", "loudness"):
        weighted_sum = 0.0
        weight_sum = 0.0

        for index, row in enumerate(likes[:40]):
            try:
                value = float(row.get(field))
            except (TypeError, ValueError):
                continue

            if not math.isfinite(value):
                continue

            # Smooth recency decay.  The newest Like has weight 1.0.
            weight = math.exp(-index / 12.0)
            weighted_sum += value * weight
            weight_sum += weight

        if weight_sum:
            profile[field] = weighted_sum / weight_sum

    for index, row in enumerate(likes[:40]):
        weight = math.exp(-index / 12.0)

        root, mode = _radio_key_parts(row.get("musical_key"))
        if root:
            key = (root, mode)
            profile["keys"][key] = profile["keys"].get(key, 0.0) + weight

        for field, target in (
            ("analyzer_mood", "analyzer_moods"),
            ("genre", "genres"),
            ("subgenre", "subgenres"),
        ):
            value = row.get(field)
            if value and str(value).strip():
                value = str(value).strip().lower()
                profile[target][value] = (
                    profile[target].get(value, 0.0) + weight
                )

    return profile


def _radio_history(uid):
    recent = {}

    with db() as c:
        with cur(c) as x:
            x.execute(
                """
                SELECT channel_id,message_id,sent_at
                FROM user_history
                WHERE user_id=%s
                  AND action='served'
                ORDER BY sent_at DESC,id DESC
                LIMIT %s
                """,
                (uid, HISTORY_LIMIT),
            )

            for index, row in enumerate(x.fetchall()):
                recent[
                    (
                        str(row["channel_id"]),
                        int(row["message_id"]),
                    )
                ] = {
                    "rank": index,
                    "sent_at": int(row["sent_at"]),
                }

    return recent


def _radio_today_served(uid):
    """Return every track already served to this user today.

    Radio treats this as a hard daily freshness boundary: a track served once
    today must not be sent again by Radio until the date changes.
    """
    served = set()

    with db() as c:
        with cur(c) as x:
            x.execute(
                """
                SELECT channel_id,message_id
                FROM user_history
                WHERE user_id=%s
                  AND action='served'
                  AND to_timestamp(sent_at)::date = CURRENT_DATE
                """,
                (uid,),
            )

            for row in x.fetchall():
                served.add((str(row["channel_id"]), int(row["message_id"])))

    return served


def _radio_last_seed(uid):
    """
    Return the latest served track with analyzer metadata.

    This gives Radio a local "continuation" signal instead of restarting from
    the whole user profile on every request.
    """
    with db() as c:
        with cur(c) as x:
            x.execute(
                """
                SELECT
                    t.mood,
                    t.message_id,
                    t.channel_id,
                    t.bpm,
                    t.musical_key,
                    t.energy,
                    t.danceability,
                    t.loudness,
                    t.genre,
                    t.subgenre,
                    t.analyzer_mood
                FROM user_history h
                JOIN tracks t
                  ON t.channel_id=h.channel_id
                 AND t.message_id=h.message_id
                WHERE h.user_id=%s
                  AND h.action='served'
                ORDER BY h.sent_at DESC,h.id DESC
                LIMIT 1
                """,
                (uid,),
            )
            return x.fetchone()


def _radio_candidates(uid):
    with db() as c:
        with cur(c) as x:
            x.execute(
                """
                SELECT
                    t.id,
                    t.mood,
                    t.message_id,
                    t.channel_id,
                    t.title,
                    t.created_at,
                    t.bpm,
                    t.musical_key,
                    t.energy,
                    t.danceability,
                    t.loudness,
                    t.genre,
                    t.subgenre,
                    t.analyzer_mood,
                    COALESCE(gl.global_likes,0) AS global_likes,
                    COALESCE(gs.global_served,0) AS global_served
                FROM tracks t
                LEFT JOIN (
                    SELECT
                        channel_id,
                        message_id,
                        COUNT(*) AS global_likes
                    FROM track_feedback
                    WHERE feedback='like'
                    GROUP BY channel_id,message_id
                ) gl
                  ON gl.channel_id=t.channel_id
                 AND gl.message_id=t.message_id
                LEFT JOIN (
                    SELECT
                        channel_id,
                        message_id,
                        COUNT(*) AS global_served
                    FROM user_history
                    WHERE action='served'
                    GROUP BY channel_id,message_id
                ) gs
                  ON gs.channel_id=t.channel_id
                 AND gs.message_id=t.message_id
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM track_feedback f
                    WHERE f.user_id=%s
                      AND f.channel_id=t.channel_id
                      AND f.message_id=t.message_id
                      AND f.feedback='not_for_me'
                )
                ORDER BY t.id ASC
                """,
                (uid,),
            )
            return x.fetchall()


def _radio_feature_similarity(a, b):
    """
    Audio/content similarity in [0,1].

    The weighting intentionally favours genre/subgenre and audio feel over
    exact musical key, which is closer to how a practical music radio should
    behave.  Missing analyzer fields are simply ignored.
    """
    parts = []

    for field, scale, weight in (
        ("bpm", 35.0, 0.08),
        ("energy", 0.30, 0.22),
        ("danceability", 0.30, 0.14),
        ("loudness", 10.0, 0.10),
    ):
        sim = _numeric_similarity(a.get(field), b.get(field), scale)
        if sim is not None:
            parts.append((sim, weight))

    # Categorical similarity.
    genre_a = str(a.get("genre") or "").strip().lower()
    genre_b = str(b.get("genre") or "").strip().lower()
    if genre_a and genre_b:
        parts.append((1.0 if genre_a == genre_b else 0.0, 0.15))

    sub_a = str(a.get("subgenre") or "").strip().lower()
    sub_b = str(b.get("subgenre") or "").strip().lower()
    if sub_a and sub_b:
        parts.append((1.0 if sub_a == sub_b else 0.0, 0.13))

    mood_a = str(a.get("analyzer_mood") or "").strip().lower()
    mood_b = str(b.get("analyzer_mood") or "").strip().lower()
    if mood_a and mood_b:
        parts.append((1.0 if mood_a == mood_b else 0.0, 0.08))

    root_a, mode_a = _radio_key_parts(a.get("musical_key"))
    root_b, mode_b = _radio_key_parts(b.get("musical_key"))
    if root_a and root_b:
        if root_a == root_b and mode_a == mode_b:
            parts.append((1.0, 0.04))
        elif root_a == root_b:
            parts.append((0.65, 0.04))
        else:
            parts.append((0.0, 0.04))

    if not parts:
        return None

    total_weight = sum(weight for _, weight in parts)
    return sum(sim * weight for sim, weight in parts) / total_weight


def _radio_seed_similarity(row, seeds):
    """
    Compare a candidate against multiple liked seeds.

    Recent seeds are weighted more heavily, while the strongest similarity is
    retained so one very strong match is not drowned out by unrelated Likes.
    """
    if not seeds:
        return None

    scored = []

    for index, seed in enumerate(seeds[:20]):
        sim = _radio_feature_similarity(row, seed)
        if sim is None:
            continue

        recency = math.exp(-index / 8.0)
        scored.append((sim, recency))

    if not scored:
        return None

    best = max(sim for sim, _ in scored)
    weighted_avg = (
        sum(sim * weight for sim, weight in scored)
        / sum(weight for _, weight in scored)
    )

    # Best seed match dominates; the weighted average keeps the profile stable.
    return (best * 0.62) + (weighted_avg * 0.38)


def _radio_bpm_smooth_transition_score(candidate_bpm, last_bpm):
    """
    Prefer gradual BPM movement between consecutive Radio tracks.

    BPM is NOT treated as an exact target and is NOT forced to stay around
    the user's Like centroid.  Instead, the previous Radio track is used as
    the transition anchor:
      * 0-1 BPM change: slight penalty (avoid getting stuck)
      * 2-4 BPM change: strongest preference
      * 5-7 BPM change: good preference
      * 8-10 BPM change: mild preference
      * >10 BPM change: progressively penalized

    This makes sequences such as 134 -> 137 -> 141 -> 145 -> 148 possible,
    while discouraging abrupt jumps such as 134 -> 150 or 134 -> 82.
    """
    if last_bpm is None:
        return 0.0

    try:
        bpm = float(candidate_bpm)
        previous = float(last_bpm)
    except (TypeError, ValueError):
        return 0.0

    if not (math.isfinite(bpm) and math.isfinite(previous)):
        return 0.0

    delta = abs(bpm - previous)

    if delta < 1.0:
        return -3.0
    if delta <= 4.0:
        return 9.0
    if delta <= 7.0:
        return 6.0
    if delta <= 10.0:
        return 2.0
    if delta <= 14.0:
        return -5.0 - ((delta - 10.0) * 1.0)
    return -9.0 - min(10.0, (delta - 14.0) * 0.7)


def _radio_negative_similarity(row, dislikes):
    """
    Penalize candidates that resemble tracks the user explicitly rejected.
    """
    if not dislikes:
        return 0.0

    scores = []

    for index, seed in enumerate(dislikes[:20]):
        sim = _radio_feature_similarity(row, seed)
        if sim is None:
            continue

        weight = math.exp(-index / 10.0)
        scores.append((sim, weight))

    if not scores:
        return 0.0

    return (
        sum(sim * weight for sim, weight in scores)
        / sum(weight for _, weight in scores)
    )


def radio_weights(uid, baseline_mood=None):
    """
    Rule-based mood prior used only as a light fallback/tie-breaker.

    Radio is global and never locks to the currently selected mood.
    """
    r = ratios(uid)
    weights = {}

    for mood in MOODS:
        likes = float(r[mood]["like"])
        nots = float(r[mood]["not"])

        positive = (likes + 1.0) / (likes + nots + 2.0)
        volume = math.log1p(likes)
        rejection = nots / (likes + nots + 2.0)

        weights[mood] = (
            0.35
            + 0.75 * positive
            + 0.20 * volume
            - 0.45 * rejection
        )

    # The selected mood is intentionally only a tiny prior.  It must not
    # override feedback/audio similarity.
    if baseline_mood in MOODS:
        weights[baseline_mood] += 0.08

    return {
        mood: max(0.05, value)
        for mood, value in weights.items()
    }


def radio_track(uid, baseline_mood=None):
    """
    Spotify-like rule-based Radio.

    No AI/ML/API is used here.  Selection is based on:
      1) recent liked tracks as similarity seeds,
      2) audio-feature similarity,
      3) genre/subgenre/mood/key similarity,
      4) similarity to the last served track for smooth continuation,
      5) negative similarity from Not For Me tracks,
      6) recency/anti-repeat logic,
      7) a small popularity + exploration component.

    The result is intentionally not locked to the selected mood.
    """
    rows = _radio_candidates(uid)
    if not rows:
        return None

    feedback = feedback_map(uid)
    history_map = _radio_history(uid)
    today_served = _radio_today_served(uid)
    profile = _radio_profile(uid)
    last_seed = _radio_last_seed(uid)
    mood_weights = radio_weights(uid, baseline_mood)

    # BPM is a smooth transition signal between consecutive Radio tracks.
    # The Like profile remains a taste signal, but is NOT used as a hard BPM
    # target, so Radio does not get stuck around one value.
    last_bpm = last_seed.get("bpm") if last_seed else None

    liked_seeds = profile["likes"]
    disliked_seeds = profile["dislikes"]
    has_profile = bool(liked_seeds)

    scored = []

    for row in rows:
        mood = row.get("mood")
        if mood not in MOODS:
            continue

        key = (
            str(row["channel_id"]),
            int(row["message_id"]),
        )

        # HARD RULE: anything already sent today cannot be sent again by Radio
        # until the calendar date changes. This is intentionally independent
        # of the normal recent-history penalty.
        if key in today_served:
            continue

        fb = feedback.get(key)
        if fb == "not_for_me":
            continue

        # Base is deliberately small.  Similarity should be the main driver.
        score = 10.0 * mood_weights[mood]

        # -------------------------------------------------------------
        # 1. Seed-track similarity — strongest Radio signal.
        # -------------------------------------------------------------
        seed_sim = _radio_seed_similarity(row, liked_seeds)
        if seed_sim is not None:
            score += 48.0 * seed_sim

        # -------------------------------------------------------------
        # 1b. Smooth BPM transition. Avoid both exact-BPM lock and large
        #     jumps between consecutive Radio tracks.
        # -------------------------------------------------------------
        score += _radio_bpm_smooth_transition_score(
            row.get("bpm"),
            last_bpm,
        )

        # -------------------------------------------------------------
        # 2. Last-played continuity — keeps consecutive Radio tracks
        #    musically coherent without locking to one genre.
        # -------------------------------------------------------------
        if last_seed:
            transition_sim = _radio_feature_similarity(row, last_seed)
            if transition_sim is not None:
                score += 14.0 * transition_sim

        # -------------------------------------------------------------
        # 3. Negative taste boundary.
        # -------------------------------------------------------------
        negative_sim = _radio_negative_similarity(row, disliked_seeds)
        if negative_sim:
            score -= 24.0 * negative_sim

        # -------------------------------------------------------------
        # 4. Explicit feedback.
        # -------------------------------------------------------------
        if fb == "like":
            # A liked track is a strong seed, but Radio should still discover
            # similar tracks instead of replaying the exact same song.
            score += 5.0

        # -------------------------------------------------------------
        # 5. Centroid similarity as a stabilizer when there are many Likes.
        # -------------------------------------------------------------
        centroid_sims = []

        for field, scale, weight in (
            ("bpm", 40.0, 0.06),
            ("energy", 0.32, 0.22),
            ("danceability", 0.32, 0.14),
            ("loudness", 10.0, 0.10),
        ):
            sim = _numeric_similarity(
                row.get(field),
                profile.get(field),
                scale,
            )
            if sim is not None:
                centroid_sims.append((sim, weight))

        if centroid_sims:
            total = sum(weight for _, weight in centroid_sims)
            centroid = (
                sum(sim * weight for sim, weight in centroid_sims)
                / total
            )
            score += 13.0 * centroid

        # Genre / subgenre / analyzer mood / key profile.
        for field, table, max_bonus, base_bonus in (
            ("genre", profile["genres"], 7.0, 1.5),
            ("subgenre", profile["subgenres"], 8.0, 1.8),
            ("analyzer_mood", profile["analyzer_moods"], 5.0, 1.2),
        ):
            value = row.get(field)
            if value:
                value = str(value).strip().lower()
                count = float(table.get(value, 0.0))
                if count:
                    score += min(max_bonus, base_bonus + count * 1.4)

        root, key_mode = _radio_key_parts(row.get("musical_key"))
        if root:
            exact = profile["keys"].get((root, key_mode), 0.0)
            root_matches = sum(
                count
                for (liked_root, _), count in profile["keys"].items()
                if liked_root == root
            )

            if exact:
                score += min(3.5, 1.0 + exact * 0.8)
            elif root_matches:
                score += min(1.5, 0.4 + root_matches * 0.25)

        # -------------------------------------------------------------
        # 6. Anti-repeat / freshness.
        # -------------------------------------------------------------
        history = history_map.get(key)

        if history is None:
            score += 13.0
        else:
            rank = int(history["rank"])

            # Strong penalty for the newest repeats, then a smooth recovery.
            if rank < 8:
                score -= 22.0 - (rank * 1.5)
            elif rank < 25:
                score -= 10.0 - ((rank - 8) * 0.35)
            else:
                score -= 1.0

        # -------------------------------------------------------------
        # 7. Mild global popularity signal.
        #    Popularity should break ties, not dominate personalization.
        # -------------------------------------------------------------
        global_likes = float(row.get("global_likes") or 0.0)
        global_served = float(row.get("global_served") or 0.0)

        if global_likes:
            score += min(5.0, math.log1p(global_likes) * 0.9)

        if global_served:
            # Small "known-good" signal, capped very tightly.
            score += min(2.0, math.log1p(global_served) * 0.25)

        # -------------------------------------------------------------
        # 8. Controlled exploration.
        # -------------------------------------------------------------
        # Exploration is smaller when the user has enough feedback, larger
        # during cold-start so Radio does not get stuck on one mood.
        exploration = 1.0 if has_profile else 2.5
        score += random.uniform(0.0, exploration)

        scored.append((score, row))

    if not scored:
        return None

    scored.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    # Do not simply choose the #1 every time.  Spotify-like radio benefits
    # from a small high-quality candidate pool with weighted choice.
    pool_size = 10 if len(scored) >= 10 else len(scored)
    pool = scored[:pool_size]

    top_score = pool[0][0]
    temperature = 4.5 if has_profile else 6.0

    choice_weights = [
        math.exp(max(-20.0, (item[0] - top_score) / temperature))
        for item in pool
    ]

    row = random.choices(
        pool,
        weights=choice_weights,
        k=1,
    )[0][1]

    return (
        row["mood"],
        int(row["message_id"]),
        str(row["channel_id"]),
        row.get("title"),
    )


# =========================================================
# NORMAL MOOD TRACK
# =========================================================

def normal_track(
    uid,
    mood,
):

    fm = feedback_map(uid)
    h = history(uid)

    a = [
        t
        for t in candidates(mood)
        if fm.get(
            (
                t[1],
                t[0],
            )
        ) != "not_for_me"
    ]

    u = [
        t
        for t in a
        if (
            t[1],
            t[0],
        ) not in h
    ]

    if not (u or a):
        return None

    t = random.choice(
        u or a
    )

    return (
        mood,
        t[0],
        t[1],
    )


# =========================================================
# RESERVE
# =========================================================

def reserve(
    uid,
    track,
):

    if not track:
        return None

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                SELECT pg_advisory_xact_lock(%s)
                """,
                (uid,),
            )

            x.execute(
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
                    %s,%s,%s,%s,
                    'served',%s
                )
                """,
                (
                    uid,
                    track[0],
                    str(track[2]),
                    int(track[1]),
                    int(time.time()),
                ),
            )

    return track


# =========================================================
# TELEGRAM HTTP
# =========================================================

def session():

    s = getattr(
        http_local,
        "s",
        None,
    )

    if not s:

        s = requests.Session()
        http_local.s = s

    return s


def tg(
    method,
    data=None,
    timeout=20,
):

    try:

        r = session().post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=data or {},
            timeout=timeout,
        )

        try:
            payload = r.json()

        except ValueError:

            payload = {
                "ok": False,
                "description":
                    f"HTTP {r.status_code}: "
                    "non-JSON response",
            }

        if r.status_code >= 400:

            payload = {
                "ok": False,
                "description":
                    f"HTTP {r.status_code}",
            }

        return payload

    except requests.RequestException as e:

        log.warning(
            "Telegram %s: %s",
            method,
            e,
        )

        return {
            "ok": False,
            "description": str(e),
        }

    except Exception as e:

        log.exception(
            "Telegram %s unexpected error",
            method,
        )

        return {
            "ok": False,
            "description": str(e),
        }


def send(
    chat,
    text,
    k=None,
):

    d = {
        "chat_id": chat,
        "text": text,
        "disable_web_page_preview": True,
    }

    if k:
        d["reply_markup"] = k

    return tg(
        "sendMessage",
        d,
        15,
    )


def answer(
    cid,
    text="",
):

    return tg(
        "answerCallbackQuery",
        {
            "callback_query_id": cid,
            "text": text,
        },
        8,
    )


def edit_k(
    chat,
    msg,
    k,
):

    return tg(
        "editMessageReplyMarkup",
        {
            "chat_id": chat,
            "message_id": msg,
            "reply_markup": k,
        },
        10,
    )


def copy_music(
    chat,
    ch,
    msg,
):

    return tg(
        "copyMessage",
        {
            "chat_id": chat,
            "from_chat_id": ch,
            "message_id": msg,
        },
        30,
    )


def track_details(ch, msg):

    try:
        with db() as c:
            with cur(c) as x:
                x.execute(
                    """
                    SELECT bpm, musical_key, energy, danceability,
                           loudness, genre, subgenre, analyzer_mood
                    FROM tracks
                    WHERE channel_id=%s AND message_id=%s
                    LIMIT 1
                    """,
                    (str(ch), int(msg)),
                )
                row = x.fetchone()
        if not row:
            return ""

        def num(value):
            try:
                value = float(value)
                return value if math.isfinite(value) else None
            except (TypeError, ValueError):
                return None

        lines = []
        v = num(row.get("bpm"))
        if v is not None: lines.append(f"💿 BPM: {v:.1f}")
        if row.get("musical_key"): lines.append(f"🎹 Key: {str(row['musical_key'])[:40]}")
        v = num(row.get("energy"))
        if v is not None: lines.append(f"⚡ Energy: {v:.1f}")
        v = num(row.get("danceability"))
        if v is not None: lines.append(f"💃 Danceability: {v:.1f}")
        v = num(row.get("loudness"))
        if v is not None: lines.append(f"🔊 Loudness: {v:.1f} dB")
        if row.get("genre"): lines.append(f"🎧 Genre: {str(row['genre'])[:40]}")
        if row.get("subgenre"): lines.append(f"🎵 Subgenre: {str(row['subgenre'])[:40]}")
        return "🎚 AUDIO ANALYSIS\n━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines) if lines else ""
    except Exception:
        log.exception("track details lookup failed channel=%s message=%s", ch, msg)
        return ""


# =========================================================
# BROADCAST
# =========================================================

def broadcast_buttons(bid):

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                SELECT
                    reaction,
                    COUNT(*) AS count
                FROM broadcast_reactions
                WHERE broadcast_id=%s
                GROUP BY reaction
                """,
                (bid,),
            )

            r = {
                a["reaction"]:
                    int(a["count"])
                for a in x.fetchall()
            }

    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        f"❤️ {r.get('love',0)}",
                    "callback_data":
                        f"br:{bid}:love",
                },
                {
                    "text":
                        f"🔥 {r.get('fire',0)}",
                    "callback_data":
                        f"br:{bid}:fire",
                },
                {
                    "text":
                        f"👍 {r.get('like',0)}",
                    "callback_data":
                        f"br:{bid}:like",
                },
            ],
            [
                {
                    "text":
                        "💬 COMMENT",
                    "callback_data":
                        f"bc:{bid}",
                }
            ],
        ]
    }


def cleanup_pending():

    cutoff = int(
        time.time()
    ) - 900

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                DELETE FROM pending_comments
                WHERE created_at < %s
                """,
                (cutoff,),
            )

            x.execute(
                """
                DELETE FROM pending_broadcasts
                WHERE created_at < %s
                """,
                (cutoff,),
            )


def set_pending_broadcast(
    uid,
    chat_id,
):

    with db() as c:

        with cur(c) as x:

            x.execute(
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


def get_pending_broadcast(uid):

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                SELECT chat_id
                FROM pending_broadcasts
                WHERE user_id=%s
                """,
                (uid,),
            )

            r = x.fetchone()

    return (
        int(r["chat_id"])
        if r
        else None
    )


def clear_pending_broadcast(uid):

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                DELETE FROM pending_broadcasts
                WHERE user_id=%s
                """,
                (uid,),
            )


def create_broadcast(
    admin_id,
    source_chat_id,
    source_message_id,
    content_type,
    text=None,
):

    with db() as c:

        with cur(c) as x:

            x.execute(
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
                x.fetchone()["id"]
            )


def broadcast_job(
    bid,
    source_chat_id,
    source_message_id,
):

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                SELECT user_id
                FROM users
                ORDER BY user_id
                """
            )

            users = [
                int(a["user_id"])
                for a in x.fetchall()
            ]

    k = broadcast_buttons(bid)

    okn = 0
    bad = 0

    for uid in users:

        r = tg(
            "copyMessage",
            {
                "chat_id": uid,
                "from_chat_id":
                    source_chat_id,
                "message_id":
                    source_message_id,
                "reply_markup": k,
            },
            30,
        )

        if r.get("ok"):
            okn += 1
        else:
            bad += 1

        time.sleep(0.04)

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                UPDATE broadcasts
                SET
                    sent_count=%s,
                    failed_count=%s
                WHERE id=%s
                """,
                (
                    okn,
                    bad,
                    bid,
                ),
            )

    send(
        ADMIN_USER_ID,
        f"📣 BROADCAST #{bid} COMPLETE\n\n"
        f"✅ Sent: {okn}\n"
        f"❌ Failed: {bad}",
    )


def start_broadcast(
    admin_id,
    source_chat_id,
    source_message_id,
    content_type,
    text=None,
):

    bid = create_broadcast(
        admin_id,
        source_chat_id,
        source_message_id,
        content_type,
        text,
    )

    executor.submit(
        broadcast_job,
        bid,
        source_chat_id,
        source_message_id,
    )

    return bid


# =========================================================
# COMMENTS
# =========================================================

def set_pending_comment(
    uid,
    bid,
):

    with db() as c:

        with cur(c) as x:

            x.execute(
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
                    bid,
                    int(time.time()),
                ),
            )


def get_pending_comment(uid):

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                SELECT broadcast_id
                FROM pending_comments
                WHERE user_id=%s
                """,
                (uid,),
            )

            r = x.fetchone()

    return (
        int(r["broadcast_id"])
        if r
        else None
    )


def save_comment(
    uid,
    bid,
    text,
):

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                INSERT INTO broadcast_comments(
                    broadcast_id,
                    user_id,
                    comment,
                    created_at
                )
                VALUES(%s,%s,%s,%s)
                """,
                (
                    bid,
                    uid,
                    text,
                    int(time.time()),
                ),
            )

            x.execute(
                """
                DELETE FROM pending_comments
                WHERE user_id=%s
                """,
                (uid,),
            )


# =========================================================
# TOP 10 LIKED
# =========================================================

def top_liked_tracks(
    limit=10,
):

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                SELECT
                    t.id,
                    t.mood,
                    t.channel_id,
                    t.message_id,
                    NULLIF(t.title,'') AS title,
                    COUNT(f.id) AS likes
                FROM tracks t
                JOIN track_feedback f
                    ON f.channel_id=t.channel_id
                   AND f.message_id=t.message_id
                   AND f.feedback='like'
                GROUP BY
                    t.id,
                    t.mood,
                    t.channel_id,
                    t.message_id,
                    t.title,
                    t.created_at
                ORDER BY
                    likes DESC,
                    t.created_at DESC
                LIMIT %s
                """,
                (limit,),
            )

            return x.fetchall()


# =========================================================
# TRENDING
# =========================================================

def trending_rows(
    limit=10,
):

    cutoff = int(
        time.time()
    ) - (
        TRENDING_DAYS * 86400
    )

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                WITH recent_served AS (
                    SELECT
                        channel_id,
                        message_id,
                        COUNT(*) AS served_count,
                        COUNT(
                            DISTINCT user_id
                        ) AS unique_users,
                        MAX(sent_at)
                            AS last_served
                    FROM user_history
                    WHERE action='served'
                      AND sent_at >= %s
                    GROUP BY
                        channel_id,
                        message_id
                ),

                recent_likes AS (
                    SELECT
                        channel_id,
                        message_id,
                        COUNT(*) AS recent_likes,
                        MAX(created_at)
                            AS last_like
                    FROM track_feedback
                    WHERE feedback='like'
                      AND created_at >= %s
                    GROUP BY
                        channel_id,
                        message_id
                )

                SELECT
                    t.id,
                    t.mood,
                    t.channel_id,
                    t.message_id,

                    NULLIF(t.title,'') AS title,

                    COALESCE(
                        rs.served_count,
                        0
                    ) AS served_count,

                    COALESCE(
                        rs.unique_users,
                        0
                    ) AS unique_users,

                    COALESCE(
                        rl.recent_likes,
                        0
                    ) AS recent_likes,

                    GREATEST(
                        COALESCE(
                            rs.last_served,
                            0
                        ),
                        COALESCE(
                            rl.last_like,
                            0
                        )
                    ) AS last_activity,

                    (
                        COALESCE(
                            rs.unique_users,
                            0
                        ) * 2.0
                        +
                        COALESCE(
                            rl.recent_likes,
                            0
                        ) * 5.0
                        +
                        LEAST(
                            COALESCE(
                                rs.served_count,
                                0
                            ),
                            20
                        ) * 0.25
                    ) AS trend_score

                FROM tracks t

                LEFT JOIN recent_served rs
                    ON rs.channel_id=
                        t.channel_id
                   AND rs.message_id=
                        t.message_id

                LEFT JOIN recent_likes rl
                    ON rl.channel_id=
                        t.channel_id
                   AND rl.message_id=
                        t.message_id

                WHERE
                    rs.message_id IS NOT NULL
                    OR
                    rl.message_id IS NOT NULL

                ORDER BY
                    trend_score DESC,
                    last_activity DESC,
                    t.created_at DESC

                LIMIT %s
                """,
                (
                    cutoff,
                    cutoff,
                    limit,
                ),
            )

            return x.fetchall()


# =========================================================
# TITLE BACKFILL
# =========================================================

async def backfill_track_titles_async(
    rows,
):

    for row in rows:

        if row.get("title"):
            continue

        try:

            ent = await client.get_entity(
                int(row["channel_id"])
            )

            msg = await client.get_messages(
                ent,
                ids=int(
                    row["message_id"]
                ),
            )

            if not msg:
                continue

            title = message_title(
                msg
            )

            if not title:
                continue

            with db() as c:

                with cur(c) as x:

                    x.execute(
                        """
                        UPDATE tracks
                        SET title=%s
                        WHERE channel_id=%s
                          AND message_id=%s
                        """,
                        (
                            title,
                            str(
                                row["channel_id"]
                            ),
                            int(
                                row["message_id"]
                            ),
                        ),
                    )

            row["title"] = title

        except Exception:

            log.exception(
                "backfill title channel=%s message=%s",
                row.get("channel_id"),
                row.get("message_id"),
            )


def backfill_track_titles(
    rows,
):

    if (
        not rows
        or client is None
        or tele_loop is None
        or not ready.is_set()
    ):
        return rows

    try:

        fut = asyncio.run_coroutine_threadsafe(
            backfill_track_titles_async(rows),
            tele_loop,
        )

        fut.result(
            timeout=45
        )

    except Exception:

        log.exception(
            "track title backfill"
        )

    return rows


# =========================================================
# LIST BUTTONS
# =========================================================

def track_list_buttons(
    rows,
):

    keyboard = []

    for i, row in enumerate(
        rows,
        1,
    ):

        track_id = int(
            row["id"]
        )

        title = (
            row.get("title")
            or
            "Unknown Track"
        )

        title = str(
            title
        ).replace(
            "\n",
            " ",
        )

        if len(title) > 38:
            title = title[:35] + "..."

        keyboard.append(
            [
                {
                    "text":
                        f"▶️ {i}. {title}",
                    "callback_data":
                        f"play:{track_id}",
                }
            ]
        )

    keyboard.append(
        [
            {
                "text":
                    "🎛 CHANGE MOOD",
                "callback_data":
                    "change_mood",
            },
            {
                "text":
                    "👤 PROFILE",
                "callback_data":
                    "profile",
            },
        ]
    )

    return {
        "inline_keyboard": keyboard
    }


def top_liked_text(
    limit=10,
):

    rows = backfill_track_titles(
        top_liked_tracks(limit)
    )

    lines = [
        "🏆 TOP 10 MOST LIKED TRACKS",
        "━━━━━━━━━━━━━━━━━━",
        "",
    ]

    if not rows:

        lines.append(
            "No likes yet."
        )

        lines.append(
            "Start liking tracks ❤️"
        )

        return (
            "\n".join(lines),
            None,
        )

    for i, row in enumerate(
        rows,
        1,
    ):

        title = str(
            row["title"]
        ).replace(
            "\n",
            " ",
        )[:90]

        lines.append(
            f"{i}. 🎵 {title}"
        )

        lines.append(
            f'   {INFO[row["mood"]][0]} • '
            f'❤️ {int(row["likes"])} likes'
        )

        lines.append("")

    return (
        "\n".join(lines),
        track_list_buttons(rows),
    )


def trending_text(
    limit=10,
):

    rows = backfill_track_titles(
        trending_rows(limit)
    )

    lines = [
        "📈 TRENDING NOW",
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"🔥 Based on the last {TRENDING_DAYS} days",
        "",
    ]

    if not rows:

        lines.append(
            "Not enough recent activity yet."
        )

        lines.append(
            "Keep discovering and liking tracks ❤️"
        )

        return (
            "\n".join(lines),
            None,
        )

    for i, row in enumerate(
        rows,
        1,
    ):

        title = str(
            row["title"]
        ).replace(
            "\n",
            " ",
        )[:90]

        lines.append(
            f"{i}. 🎵 {title}"
        )

        lines.append(
            f'   {INFO[row["mood"]][0]} • '
            f'🔥 {float(row["trend_score"]):.1f}'
        )

        lines.append("")

    return (
        "\n".join(lines),
        track_list_buttons(rows),
    )


# =========================================================
# DAILY STATS
# =========================================================

def daily_stats():

    today = datetime.now(
        ZoneInfo("Asia/Yangon")
    ).date()

    days = [
        today - timedelta(days=i)
        for i in range(7)
    ]

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                SELECT COUNT(*) n
                FROM users
                """
            )

            total = int(
                x.fetchone()["n"]
            )

            x.execute(
                """
                SELECT COUNT(*) n
                FROM daily_activity
                WHERE day=%s
                """,
                (today,),
            )

            today_n = int(
                x.fetchone()["n"]
            )

            x.execute(
                """
                SELECT COUNT(
                    DISTINCT user_id
                ) n
                FROM daily_activity
                WHERE day >= %s
                """,
                (
                    today
                    - timedelta(days=6),
                ),
            )

            week_n = int(
                x.fetchone()["n"]
            )

            x.execute(
                """
                SELECT
                    day,
                    COUNT(*) n
                FROM daily_activity
                WHERE day >= %s
                GROUP BY day
                ORDER BY day DESC
                """,
                (
                    today
                    - timedelta(days=6),
                ),
            )

            rows = {
                a["day"]:
                    int(a["n"])
                for a in x.fetchall()
            }

    return (
        total,
        today_n,
        week_n,
        [
            (
                d,
                rows.get(d, 0)
            )
            for d in days
        ],
    )


# =========================================================
# COMMENTS
# =========================================================

def comments_text(
    limit=20,
):

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                SELECT
                    c.id,
                    c.broadcast_id,
                    c.user_id,
                    c.comment,
                    c.created_at
                FROM broadcast_comments c
                ORDER BY c.id DESC
                LIMIT %s
                """,
                (limit,),
            )

            rows = x.fetchall()

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

    for a in rows:

        txt = (
            a["comment"] or ""
        ).replace(
            "\n",
            " ",
        )[:180]

        lines.append(
            f"Comment #{a['id']} • "
            f"Broadcast #{a['broadcast_id']} • "
            f"User {a['user_id']}\n"
            f"{txt}\n"
        )

    return "\n".join(lines)


# =========================================================
# ADMIN
# =========================================================

def admin_panel():

    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "📊 DAILY USERS",
                    "callback_data":
                        "admin:daily",
                },
                {
                    "text":
                        "📈 STATS",
                    "callback_data":
                        "admin:stats",
                },
            ],
            [
                {
                    "text":
                        "💬 COMMENTS",
                    "callback_data":
                        "admin:comments",
                },
                {
                    "text":
                        "📣 BROADCAST",
                    "callback_data":
                        "admin:broadcast",
                },
            ],
            [
                {
                    "text":
                        "🏆 TOP 10 LIKED",
                    "callback_data":
                        "admin:top",
                }
            ],
            [
                {
                    "text":
                        "📡 TELETHON",
                    "callback_data":
                        "admin:telegram",
                }
            ],
        ]
    }


def admin_dashboard():

    total, today_n, week_n, rows = (
        daily_stats()
    )

    track_counts = counts()

    track_total = sum(
        track_counts.values()
    )

    with db() as c:

        with cur(c) as x:

            x.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE analyzed=TRUE) AS analyzed
                FROM tracks
                """
            )

            analyzer_row = x.fetchone() or {}

            x.execute(
                """
                SELECT COUNT(*) n
                FROM broadcasts
                WHERE created_at >= %s
                """,
                (
                    int(time.time())
                    - 86400,
                ),
            )

            recent_24h = int(
                x.fetchone()["n"]
            )

    return (
        "🛠 NOT YOUR VIBE — ADMIN\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Total Users: {total}\n"
        f"🟢 Today Active: {today_n}\n"
        f"📅 7-Day Active: {week_n}\n"
        f"🎵 Tracks: {track_total}\n"
        f"🎚 Analyzer: {int(analyzer_row.get('analyzed') or 0)} / {int(analyzer_row.get('total') or 0)} scanned\n"
        f"📣 Broadcasts (24h): {recent_24h}\n\n"
        f"📡 Telethon: "
        f"{'CONNECTED' if ready.is_set() else 'DISCONNECTED'}\n"
        f"🗄 PostgreSQL: "
        f"{'ONLINE' if db_pool else 'OFFLINE'}"
    )


# =========================================================
# MOOD MENU
# =========================================================

def mood_menu():

    return {
        "inline_keyboard": [
            [
                {
                    "text": INFO["sad"][0],
                    "callback_data":
                        "mood_sad",
                },
                {
                    "text": INFO["love"][0],
                    "callback_data":
                        "mood_love",
                },
            ],
            [
                {
                    "text": INFO["chill"][0],
                    "callback_data":
                        "mood_chill",
                },
                {
                    "text": INFO["hype"][0],
                    "callback_data":
                        "mood_hype",
                },
            ],
            [
                {
                    "text": INFO["dark"][0],
                    "callback_data":
                        "mood_dark",
                },
                {
                    "text": INFO["energetic"][0],
                    "callback_data":
                        "mood_energetic",
                },
            ],
            [
                {
                    "text": INFO["night"][0],
                    "callback_data":
                        "mood_night",
                },
                {
                    "text": INFO["melodic"][0],
                    "callback_data":
                        "mood_melodic",
                },
            ],
            [
                {
                    "text":
                        "🔥 DAILY VIBE",
                    "callback_data":
                        "daily_vibe",
                },
                {
                    "text":
                        "🧠 FOR YOU",
                    "callback_data":
                        "for_you",
                },
            ],
            [
                {
                    "text":
                        "🎲 SURPRISE ME",
                    "callback_data":
                        "surprise_me",
                },
                {
                    "text":
                        "📈 TRENDING",
                    "callback_data":
                        "trending",
                },
            ],
            [
                {
                    "text":
                        "🎵 TRACK OF THE DAY",
                    "callback_data":
                        "track_of_day",
                }
            ],
            [
                {
                    "text":
                        "🏆 TOP 10 LIKED",
                    "callback_data":
                        "top_liked",
                }
            ],
        ]
    }


# =========================================================
# ELIGIBLE TRACKS
# =========================================================

def eligible_tracks(
    uid,
    extra_where="",
    params=(),
):

    hist = history(uid)

    with db() as c:

        with cur(c) as x:

            q = """
                SELECT
                    id,
                    mood,
                    message_id,
                    channel_id,
                    title
                FROM tracks
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM track_feedback f
                    WHERE f.user_id=%s
                      AND f.channel_id=
                          tracks.channel_id
                      AND f.message_id=
                          tracks.message_id
                      AND f.feedback=
                          'not_for_me'
                )
            """

            args = [uid]

            if extra_where:

                q += (
                    " AND "
                    + extra_where
                )

                args.extend(
                    params
                )

            x.execute(
                q,
                args,
            )

            rows = x.fetchall()

    unseen = [
        r
        for r in rows
        if (
            str(r["channel_id"]),
            int(r["message_id"]),
        ) not in hist
    ]

    return (
        unseen
        or rows
    )


def stable_pick(
    rows,
    key,
):

    if not rows:
        return None

    idx = int(
        hashlib.md5(
            key.encode()
        ).hexdigest()[:8],
        16,
    ) % len(rows)

    r = rows[idx]

    return (
        r["mood"],
        int(r["message_id"]),
        str(r["channel_id"]),
        r.get("title"),
    )


# =========================================================
# SPECIAL TRACKS
# =========================================================

def daily_vibe_track(uid):

    rows = eligible_tracks(uid)

    today = datetime.now(
        ZoneInfo("Asia/Yangon")
    ).date().isoformat()

    return stable_pick(
        rows,
        f"daily:{uid}:{today}",
    )


def track_of_day(uid):

    rows = eligible_tracks(uid)

    today = datetime.now(
        ZoneInfo("Asia/Yangon")
    ).date().isoformat()

    return stable_pick(
        rows,
        f"today:{uid}:{today}",
    )


def for_you_track(uid):
    # FOR YOU = ONLY tracks explicitly liked by this user.
    with db() as c:
        with cur(c) as x:
            x.execute("""
                SELECT t.mood,t.message_id,t.channel_id,t.title
                FROM tracks t
                JOIN track_feedback f
                  ON f.channel_id=t.channel_id
                 AND f.message_id=t.message_id
                WHERE f.user_id=%s AND f.feedback='like'
                ORDER BY f.created_at DESC,t.id DESC
            """, (uid,))
            rows=x.fetchall()
    if not rows: return None
    h=history(uid)
    unseen=[r for r in rows if (str(r['channel_id']),int(r['message_id'])) not in h]
    r=random.choice(unseen or rows)
    return (r['mood'],int(r['message_id']),str(r['channel_id']),r.get('title'))

def surprise_track(uid):

    r = ratios(uid)

    mood_scores = sorted(
        [
            (
                (
                    r[m]["not"] + 1
                )
                / (
                    r[m]["like"]
                    + r[m]["not"]
                    + 2
                ),
                m,
            )
            for m in MOODS
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

            z = random.choice(
                rows
            )

            return (
                z["mood"],
                int(
                    z["message_id"]
                ),
                str(
                    z["channel_id"]
                ),
                z.get("title"),
            )

    return None


# =========================================================
# TASTE
# =========================================================

def taste_analytics(uid):

    r = ratios(uid)

    total_like = sum(
        v["like"]
        for v in r.values()
    )

    total_not = sum(
        v["not"]
        for v in r.values()
    )

    ranked = sorted(
        MOODS,
        key=lambda m: (
            r[m]["like"],
            r[m]["like"]
            - r[m]["not"],
        ),
        reverse=True,
    )

    lines = [
        "📊 TASTE ANALYTICS",
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"❤️ Likes: {total_like}    "
        f"😴 Not for me: {total_not}",
    ]

    if total_like + total_not:

        lines.append(
            f"🎯 Positive ratio: "
            f"{total_like / (total_like + total_not) * 100:.0f}%"
        )

    lines.append("")

    for mood in ranked:

        likes = r[mood]["like"]
        nots = r[mood]["not"]

        if likes + nots:

            lines.append(
                f"{INFO[mood][0]} → "
                f"❤️ {likes} / 😴 {nots}"
            )

    if not total_like + total_not:

        lines.append(
            "Like or skip tracks to "
            "build your taste profile."
        )

    return "\n".join(lines)


# =========================================================
# MUSIC BUTTONS
# =========================================================

def buttons(
    uid,
    ch,
    msg,
    mood,
    mode=None,
):

    f = feedback(
        uid,
        ch,
        msg,
    )

    radio_active = is_radio(uid)

    radio_text = (
        "📻 RADIO ✓"
        if radio_active
        else "📻 RADIO"
    )

    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "❤️✓"
                        if f == "like"
                        else "❤️",
                    "callback_data":
                        f"like:{mood}:{ch}:{msg}",
                },
                {
                    "text":
                        "😴✓"
                        if f == "not_for_me"
                        else "😴",
                    "callback_data":
                        f"notme:{mood}:{ch}:{msg}",
                },
            ],
            [
                {
                    "text":
                        "⏭ NEXT",
                    "callback_data":
                        f"next_special:{mode}" if mode else "next_music",
                },
                {
                    "text":
                        radio_text,
                    "callback_data":
                        "radio",
                },
            ],
            [
                {
                    "text":
                        "👤 PROFILE",
                    "callback_data":
                        "profile",
                },
                {
                    "text":
                        "🆕 NEW TRACKS",
                    "callback_data":
                        "new_tracks",
                },
            ],
            [
                {
                    "text":
                        "🎛 CHANGE MOOD",
                    "callback_data":
                        "change_mood",
                }
            ],
        ]
    }


# =========================================================
# PLAY SELECTED TRACK
# =========================================================

def play_selected_track(
    chat,
    uid,
    track_id,
    header="▶️ SELECTED TRACK",
):

    row = get_track(
        track_id
    )

    if not row:

        return send(
            chat,
            "⚠️ Track not found.",
            mood_menu(),
        )

    mood = row["mood"]
    ch = str(
        row["channel_id"]
    )
    msg = int(
        row["message_id"]
    )

    title = (
        row.get("title")
        or f"Track #{msg}"
    )

    result = copy_music(
        chat,
        ch,
        msg,
    )

    if not result.get("ok"):

        log.warning(
            "copy selected track failed "
            "uid=%s track=%s result=%s",
            uid,
            track_id,
            result,
        )

        return send(
            chat,
            "⚠️ This track could not be delivered.",
            mood_menu(),
        )

    reserve(
        uid,
        (
            mood,
            msg,
            ch,
        ),
    )

    title = str(
        title
    ).replace(
        "\n",
        " ",
    )[:120]

    details = track_details(ch, msg)
    body = (
        f"{header}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🎵 {title}\n"
        f"{INFO[mood][0]}\n\n"
        + (details + "\n\n" if details else "")
        + "Enjoy the vibe. ✨"
    )

    send(
        chat,
        body,
        buttons(
            uid,
            ch,
            msg,
            mood,
        ),
    )


# =========================================================
# SEND SPECIAL MUSIC
# =========================================================

def send_special_music(
    chat,
    uid,
    track,
    header,
    mode=None,
):

    if not track:

        return send(
            chat,
            "⚠️ No suitable track found.",
            mood_menu(),
        )

    mood, msg, ch, title = track

    result = copy_music(
        chat,
        ch,
        msg,
    )

    if not result.get("ok"):

        return send(
            chat,
            "⚠️ This track could not be delivered.",
            mood_menu(),
        )

    reserve(
        uid,
        (
            mood,
            msg,
            ch,
        ),
    )

    label = (
        title
        or "Unknown Track"
    )

    label = str(
        label
    ).replace(
        "\n",
        " ",
    )[:120]

    details = track_details(ch, msg)
    body = (
        f"{header}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🎵 {label}\n"
        f"{INFO[mood][0]}\n\n"
        + (details + "\n\n" if details else "")
        + "Enjoy the vibe. ✨"
    )

    send(
        chat,
        body,
        buttons(
            uid,
            ch,
            msg,
            mood,
            mode,
        ),
    )


# =========================================================
# SEND NORMAL / RADIO
# =========================================================

def send_music(
    chat,
    uid,
    mood,
    radio=False,
):

    if radio:
        track = radio_track(uid, baseline_mood=mood)
    else:
        track = normal_track(uid, mood)

    if not track:
        return send(chat, "⚠️ No suitable track found.", mood_menu())

    if len(track) == 4:
        selected_mood, msg, channel, track_title = track
    else:
        selected_mood, msg, channel = track
        track_title = None

    result = copy_music(chat, channel, msg)

    if not result.get("ok"):
        log.warning(
            "copy music failed uid=%s channel=%s msg=%s result=%s",
            uid, channel, msg, result,
        )
        return send(chat, "⚠️ This track could not be delivered.", mood_menu())

    reserve(uid, (selected_mood, msg, channel))

    if radio:
        title = "📻 YOUR RADIO"
        desc = "Personalized from your feedback across all moods."
    else:
        title = "🎧 NOW PLAYING"
        desc = INFO[selected_mood][1]

    details = track_details(channel, msg)
    body = (
        f"{title}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"{INFO[selected_mood][0]}\n\n"
        f"{desc}\n\n"
        + (details + "\n\n" if details else "")
        + "Enjoy the vibe. ✨"
    )

    send(
        chat,
        body,
        buttons(uid, channel, msg, selected_mood),
    )



# =========================================================
# MUSIC SCHEDULER
# =========================================================

def schedule(
    chat,
    uid,
    mood,
    radio=False,
):

    uid = int(uid)
    lock = _music_lock(uid)

    with lock:
        state = get_state(uid)
        generation = _music_generation(uid)

        if radio:
            if not state["radio"]:
                return False
        else:
            if state["radio"] or state["mood"] != mood:
                return False

        with pending_lock:
            if pending.get(uid) == generation:
                return False
            pending[uid] = generation

    def work():
        try:
            with lock:
                with pending_lock:
                    if pending.get(uid) != generation:
                        return

                state_now = get_state(uid)
                if _music_generation(uid) != generation:
                    return

                if radio:
                    if not state_now["radio"]:
                        return
                else:
                    if state_now["radio"] or state_now["mood"] != mood:
                        return

                send_music(chat, uid, mood, radio)

        except Exception:
            log.exception("music worker")
        finally:
            with pending_lock:
                if pending.get(uid) == generation:
                    pending.pop(uid, None)

    executor.submit(work)
    return True



def schedule_next(
    chat,
    uid,
):

    state = get_state(
        uid
    )

    mood = state["mood"]
    radio = state["radio"]

    if radio:

        return schedule(
            chat,
            uid,
            mood or "melodic",
            True,
        )

    if mood:

        return schedule(
            chat,
            uid,
            mood,
            False,
        )

    return False


# =========================================================
# PROFILE
# =========================================================

def profile_text(uid):

    with db() as c:

        with cur(c) as x:

            x.execute(
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

            u = x.fetchone() or {}

            x.execute(
                """
                SELECT COUNT(*) n
                FROM track_feedback
                WHERE user_id=%s
                  AND feedback='like'
                """,
                (uid,),
            )

            likes = int(
                x.fetchone()["n"]
            )

            x.execute(
                """
                SELECT COUNT(*) n
                FROM track_feedback
                WHERE user_id=%s
                  AND feedback='not_for_me'
                """,
                (uid,),
            )

            nots = int(
                x.fetchone()["n"]
            )

            x.execute(
                """
                SELECT COUNT(*) n
                FROM user_history
                WHERE user_id=%s
                  AND action='served'
                """,
                (uid,),
            )

            served = int(
                x.fetchone()["n"]
            )

    r = ratios(uid)

    ranked = sorted(
        MOODS,
        key=lambda m: (
            r[m]["like"],
            r[m]["like"]
            - r[m]["not"],
        ),
        reverse=True,
    )

    fav = ranked[0]
    second = ranked[1]

    name = " ".join(
        x
        for x in [
            u.get("first_name")
            or "",
            u.get("last_name")
            or "",
        ]
        if x
    ).strip() or "Vibe Listener"

    username = (
        f"@{u.get('username')}"
        if u.get("username")
        else "Not set"
    )

    state = get_state(
        uid
    )

    mood = state["mood"]
    radio = state["radio"]

    radio_status = (
        "📻 ON"
        if radio
        else "🎧 MOOD MODE"
    )

    return (
        "👤 VIBE PROFILE 2.0\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"Name: {name}\n"
        f"Username: {username}\n"
        f"Current mood: "
        f"{INFO[mood][0] if mood else 'Not selected'}\n"
        f"Mode: {radio_status}\n\n"
        f"🎵 Tracks served: {served}\n"
        f"❤️ Likes: {likes}\n"
        f"😴 Not for me: {nots}\n\n"
        f"🏆 Top mood: {INFO[fav][0]}\n"
        f"🥈 Second: {INFO[second][0]}\n\n"
        "Your Radio learns from your "
        "feedback across all moods."
    )


# =========================================================
# NEW TRACKS
# =========================================================

def latest_tracks(
    limit_per_mood=5,
):

    rows = []

    with db() as c:

        with cur(c) as x:

            for mood in MOODS:

                x.execute(
                    """
                    SELECT
                        id,
                        mood,
                        message_id,
                        channel_id,
                        title
                    FROM tracks
                    WHERE mood=%s
                    ORDER BY
                        created_at DESC,
                        id DESC
                    LIMIT %s
                    """,
                    (
                        mood,
                        limit_per_mood,
                    ),
                )

                rows.extend(
                    x.fetchall()
                )

    return rows


def new_tracks(chat):

    rows = latest_tracks(
        5
    )

    rows = backfill_track_titles(
        rows
    )

    lines = [
        "🆕 NEW TRACKS",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "Latest tracks from the mood channels.",
        "Tap a track to listen.",
        "",
    ]

    keyboard = []

    for mood in MOODS:

        mood_rows = [
            r
            for r in rows
            if r["mood"] == mood
        ]

        if not mood_rows:
            continue

        lines.append(
            INFO[mood][0]
        )

        for row in mood_rows:

            title = (
                row.get("title")
                or
                "Unknown Track"
            )

            title = str(
                title
            ).replace(
                "\n",
                " ",
            )

            lines.append(
                f"• {title[:80]}"
            )

            keyboard.append(
                [
                    {
                        "text":
                            f"▶️ {title[:38]}",
                        "callback_data":
                            f"play:{int(row['id'])}",
                    }
                ]
            )

        lines.append("")

    keyboard.append(
        [
            {
                "text":
                    "🎛 CHANGE MOOD",
                "callback_data":
                    "change_mood",
            },
            {
                "text":
                    "📈 TRENDING",
                "callback_data":
                    "trending",
            },
        ]
    )

    send(
        chat,
        "\n".join(lines),
        {
            "inline_keyboard":
                keyboard
        },
    )


# =========================================================
# FEEDBACK PARSER
# =========================================================

def parse_fb(d):

    p = d.split(
        ":",
        3,
    )

    if (
        len(p) != 4
        or p[0] not in (
            "like",
            "notme",
        )
        or p[1] not in MOODS
    ):
        return None

    try:

        return (
            p[0],
            p[1],
            p[2],
            int(p[3]),
        )

    except Exception:

        return None


# =========================================================
# CALLBACK HANDLER
# =========================================================

def callback(c):

    uid = c.get(
        "from",
        {},
    ).get("id")

    msg = c.get(
        "message",
        {},
    )

    chat = msg.get(
        "chat",
        {},
    ).get("id")

    data = c.get(
        "data",
        "",
    )

    if (
        not isinstance(uid, int)
        or not isinstance(chat, int)
    ):
        return

    register(
        c.get(
            "from",
            {}
        )
    )

    callback_id = c.get(
        "id"
    )

    # =====================================================
    # PLAY SELECTED TRACK
    # =====================================================

    if data.startswith(
        "play:"
    ):

        try:

            track_id = int(
                data.split(
                    ":",
                    1,
                )[1]
            )

        except Exception:

            answer(
                callback_id,
                "Invalid track",
            )

            return

        answer(
            callback_id,
            "▶️ Loading track...",
        )

        play_selected_track(
            chat,
            uid,
            track_id,
        )

        return

    # =====================================================
    # ADMIN
    # =====================================================

    if data.startswith(
        "admin:"
    ):

        if str(uid) != ADMIN_USER_ID:

            answer(
                callback_id,
                "Admin only",
            )

            return

        action = data.split(
            ":",
            1,
        )[1]

        answer(
            callback_id
        )

        if action == "daily":

            total, today_n, week_n, rows = (
                daily_stats()
            )

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
                f"{d.isoformat()} → {n}"
                for d, n in rows
            )

            send(
                chat,
                "\n".join(lines),
                admin_panel(),
            )

            return

        if action == "stats":

            cc = counts()

            send(
                chat,
                "📈 BOT STATS\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                + "\n".join(
                    f"{INFO[m][0]} → {cc[m]}"
                    for m in MOODS
                )
                + "\n\n"
                + (
                    "📡 Telethon: "
                    + (
                        "CONNECTED"
                        if ready.is_set()
                        else "DISCONNECTED"
                    )
                ),
                admin_panel(),
            )

            return

        if action == "comments":

            send(
                chat,
                comments_text(),
                admin_panel(),
            )

            return

        if action == "top":

            text, keyboard = (
                top_liked_text()
            )

            send(
                chat,
                text,
                keyboard or admin_panel(),
            )

            return

        if action == "broadcast":

            set_pending_broadcast(
                uid,
                chat,
            )

            send(
                chat,
                "📣 BROADCAST\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Now send the post you want to broadcast.\n\n"
                "✅ Text, photo, video, audio, "
                "document and other Telegram posts "
                "are supported.\n"
                "❌ Send /cancel to stop.",
                admin_panel(),
            )

            return

        if action == "telegram":

            send(
                chat,
                "📡 TELETHON\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Status: "
                + (
                    "🟢 CONNECTED"
                    if ready.is_set()
                    else "🔴 DISCONNECTED"
                ),
                admin_panel(),
            )

            return

        return

    # =====================================================
    # BROADCAST REACTION
    # =====================================================

    if data.startswith(
        "br:"
    ):

        p = data.split(
            ":",
            2,
        )

        if len(p) != 3:
            return

        try:

            bid = int(
                p[1]
            )

        except Exception:

            return

        reaction = p[2]

        if reaction not in (
            "love",
            "fire",
            "like",
        ):
            return

        with db() as dbc:

            with cur(dbc) as x:

                x.execute(
                    """
                    SELECT 1
                    FROM broadcasts
                    WHERE id=%s
                    """,
                    (bid,),
                )

                if not x.fetchone():

                    answer(
                        callback_id,
                        "Broadcast not found",
                    )

                    return

                x.execute(
                    """
                    SELECT reaction
                    FROM broadcast_reactions
                    WHERE broadcast_id=%s
                      AND user_id=%s
                    """,
                    (
                        bid,
                        uid,
                    ),
                )

                old = x.fetchone()

                if (
                    old
                    and old["reaction"]
                    == reaction
                ):

                    x.execute(
                        """
                        DELETE FROM broadcast_reactions
                        WHERE broadcast_id=%s
                          AND user_id=%s
                        """,
                        (
                            bid,
                            uid,
                        ),
                    )

                else:

                    x.execute(
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
                            bid,
                            uid,
                            reaction,
                            int(time.time()),
                        ),
                    )

        answer(
            callback_id,
            "Reaction saved",
        )

        edit_k(
            chat,
            msg.get(
                "message_id"
            ),
            broadcast_buttons(
                bid
            ),
        )

        return

    # =====================================================
    # BROADCAST COMMENT
    # =====================================================

    if data.startswith(
        "bc:"
    ):

        try:

            bid = int(
                data.split(
                    ":",
                    1,
                )[1]
            )

        except Exception:

            return

        with db() as dbc:

            with cur(dbc) as x:

                x.execute(
                    """
                    SELECT 1
                    FROM broadcasts
                    WHERE id=%s
                    """,
                    (bid,),
                )

                if not x.fetchone():

                    answer(
                        callback_id,
                        "Broadcast not found",
                    )

                    return

        set_pending_comment(
            uid,
            bid,
        )

        answer(
            callback_id,
            "Send your comment",
        )

        send(
            chat,
            "💬 Send your comment below 👇",
            {
                "force_reply": True,
                "input_field_placeholder":
                    "Write a comment...",
            },
        )

        return

    # =====================================================
    # MOOD
    # =====================================================

    if data.startswith(
        "mood_"
    ):

        mood = data[5:]

        if mood not in MOODS:
            return

        if set_mood(
            uid,
            mood,
        ):

            answer(
                callback_id,
                f"{INFO[mood][0]} ✓",
            )

            schedule(
                chat,
                uid,
                mood,
                False,
            )

        return

    # =====================================================
    # SPECIAL MODE NEXT (NEVER LEAVE CURRENT MODE)
    # =====================================================
    if data.startswith("next_special:"):
        mode=data.split(":",1)[1]
        set_special_mode(uid, mode)
        mapping={
            "daily_vibe": (daily_vibe_track, "🔥 YOUR DAILY VIBE"),
            "for_you": (for_you_track, "🧠 PICKED FOR YOU"),
            "surprise_me": (surprise_track, "🎲 SURPRISE ME"),
            "track_of_day": (track_of_day, "🎵 TRACK OF THE DAY"),
        }
        if mode in mapping:
            fn,header=mapping[mode]
            answer(callback_id,"⏭ Next track...")
            send_special_music(chat,uid,fn(uid),header,mode=mode)
            return

    # =====================================================
    # NEXT
    # =====================================================

    if data == "next_music":

        state = get_state(
            uid
        )

        mood = state["mood"]
        radio = state["radio"]

        if radio:

            answer(
                callback_id,
                "📻 Finding your next Radio track...",
            )

            schedule(
                chat,
                uid,
                mood or "melodic",
                True,
            )

            return

        if mood:

            answer(
                callback_id,
                "⏭ Finding next track...",
            )

            schedule(
                chat,
                uid,
                mood,
                False,
            )

            return

        answer(
            callback_id,
            "Choose a mood first",
        )

        send(
            chat,
            "🎧 Choose your mood 👇",
            mood_menu(),
        )

        return

    # =====================================================
    # RADIO
    # =====================================================

    if data == "radio":

        state = get_state(
            uid
        )

        mood = (
            state["mood"]
            or "melodic"
        )

        set_radio(
            uid,
            True,
        )

        answer(
            callback_id,
            "📻 Personalized Radio...",
        )

        schedule(
            chat,
            uid,
            mood,
            True,
        )

        return

    # =====================================================
    # CHANGE MOOD
    # =====================================================

    if data == "change_mood":

        answer(
            callback_id,
            "Choose your mood",
        )

        send(
            chat,
            "🎛 MOOD SELECTOR\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "What are you feeling right now?",
            mood_menu(),
        )

        return

    # =====================================================
    # PROFILE
    # =====================================================

    if data == "profile":

        answer(
            callback_id
        )

        send(
            chat,
            profile_text(uid),
            {
                "inline_keyboard": [
                    [
                        {
                            "text":
                                "📊 TASTE ANALYTICS",
                            "callback_data":
                                "taste_analytics",
                        }
                    ],
                    [
                        {
                            "text":
                                "🧠 FOR YOU",
                            "callback_data":
                                "for_you",
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
                        }
                    ],
                ]
            },
        )

        return

    # =====================================================
    # SPECIAL FEATURES
    # =====================================================

    if data == "daily_vibe":

        answer(
            callback_id,
            "🔥 Daily Vibe",
        )

        set_special_mode(uid, "daily_vibe")

        send_special_music(
            chat,
            uid,
            daily_vibe_track(uid),
            "🔥 YOUR DAILY VIBE",
            mode="daily_vibe",
        )

        return

    if data == "for_you":

        answer(
            callback_id,
            "🧠 Personal pick",
        )

        set_special_mode(uid, "for_you")

        send_special_music(
            chat,
            uid,
            for_you_track(uid),
            "🧠 PICKED FOR YOU",
            mode="for_you",
        )

        return

    if data == "surprise_me":

        answer(
            callback_id,
            "🎲 Surprise!",
        )

        set_special_mode(uid, "surprise_me")

        send_special_music(
            chat,
            uid,
            surprise_track(uid),
            "🎲 SURPRISE ME",
            mode="surprise_me",
        )

        return

    if data == "trending":

        answer(
            callback_id,
            "📈 Loading Trending...",
        )

        text, keyboard = (
            trending_text()
        )

        send(
            chat,
            text,
            keyboard or mood_menu(),
        )

        return

    if data == "track_of_day":

        answer(
            callback_id,
            "🎵 Track of the Day",
        )

        set_special_mode(uid, "track_of_day")

        send_special_music(
            chat,
            uid,
            track_of_day(uid),
            "🎵 TRACK OF THE DAY",
            mode="track_of_day",
        )

        return

    if data == "taste_analytics":

        answer(
            callback_id
        )

        send(
            chat,
            taste_analytics(uid),
            {
                "inline_keyboard": [
                    [
                        {
                            "text":
                                "🧠 FOR YOU",
                            "callback_data":
                                "for_you",
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
                                "👤 PROFILE",
                            "callback_data":
                                "profile",
                        }
                    ],
                ]
            },
        )

        return

    if data == "top_liked":

        answer(
            callback_id,
            "🏆 Loading Top 10...",
        )

        text, keyboard = (
            top_liked_text()
        )

        send(
            chat,
            text,
            keyboard or mood_menu(),
        )

        return

    if data == "new_tracks":

        answer(
            callback_id,
            "🆕 Loading new tracks...",
        )

        new_tracks(
            chat
        )

        return

    # =====================================================
    # LIKE / NOT FOR ME
    # =====================================================

    f = parse_fb(
        data
    )

    if f:

        action, mood, ch, mid = f

        new_feedback = (
            "like"
            if action == "like"
            else "not_for_me"
        )

        old = feedback(
            uid,
            ch,
            mid,
        )

        if old == new_feedback:

            clear_feedback(
                uid,
                ch,
                mid,
            )

            answer(
                callback_id,
                "Feedback cleared",
            )

        else:

            if save_feedback(
                uid,
                ch,
                mid,
                mood,
                new_feedback,
            ):

                answer(
                    callback_id,
                    (
                        "❤️ Added to your taste"
                        if new_feedback == "like"
                        else
                        "😴 Radio will avoid this"
                    ),
                )

            else:

                answer(
                    callback_id,
                    "⚠️ Track not found",
                )

        edit_k(
            chat,
            msg.get(
                "message_id"
            ),
            buttons(
                uid,
                ch,
                mid,
                mood,
            ),
        )


# =========================================================
# COMMAND PARSER
# =========================================================

def command(t):

    return (
        t.split(
            maxsplit=1
        )[0]
        .lower()
        .split(
            "@",
            1,
        )[0]
        if t.startswith("/")
        else ""
    )


# =========================================================
# MESSAGE HANDLER
# =========================================================

def message(m):

    chat = m.get(
        "chat",
        {},
    ).get("id")

    u = m.get(
        "from",
        {},
    )

    uid = u.get(
        "id"
    )

    if not isinstance(
        chat,
        int,
    ):
        return

    if not isinstance(
        uid,
        int,
    ):
        return

    register(
        u
    )

    cleanup_pending()

    cmd = command(
        (
            m.get("text")
            or ""
        ).strip()
    )

    # =====================================================
    # ADMIN BROADCAST PENDING
    # =====================================================

    pending_b = (
        get_pending_broadcast(uid)
        if str(uid) == ADMIN_USER_ID
        else None
    )

    if pending_b:

        raw_text = (
            m.get("text")
            or m.get("caption")
            or ""
        ).strip()

        if raw_text.startswith(
            "/cancel"
        ):

            clear_pending_broadcast(
                uid
            )

            send(
                chat,
                "❌ Broadcast cancelled.",
                admin_panel(),
            )

            return

        if m.get("message_id"):

            clear_pending_broadcast(
                uid
            )

            if m.get("text"):
                ctype = "text"

            elif m.get("photo"):
                ctype = "photo"

            elif m.get("video"):
                ctype = "video"

            elif m.get("audio"):
                ctype = "audio"

            elif m.get("document"):
                ctype = "document"

            elif m.get("animation"):
                ctype = "animation"

            else:
                ctype = "media"

            bid = start_broadcast(
                uid,
                chat,
                int(
                    m["message_id"]
                ),
                ctype,
                raw_text or None,
            )

            send(
                chat,
                f"📣 Broadcast #{bid} started.\n\n"
                f"📦 Type: {ctype}\n"
                "❤️ 🔥 👍 reactions + 💬 comments are enabled.",
            )

            return

    # =====================================================
    # COMMENT
    # =====================================================

    pending_comment = (
        get_pending_comment(uid)
    )

    if (
        pending_comment
        and (
            m.get("text")
            or ""
        ).strip()
        and not (
            m.get("text")
            or ""
        ).strip().startswith("/")
    ):

        text = (
            m.get("text")
            or ""
        ).strip()[:2000]

        save_comment(
            uid,
            pending_comment,
            text,
        )

        send(
            ADMIN_USER_ID,
            "💬 NEW COMMENT\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Broadcast: #{pending_comment}\n"
            f"User: {uid}\n\n"
            f"{text}",
        )

        send(
            chat,
            "✅ Thanks! Your comment was sent "
            "to the Not Your Vibe team.",
            mood_menu(),
        )

        return

    # =====================================================
    # START / MOOD
    # =====================================================

    if cmd in (
        "/start",
        "/mood",
    ):

        send(
            chat,
            "🎧 NOT YOUR VIBE\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Your music. Your mood. Your radio.\n\n"
            "Choose a mood or discover something new 👇",
            mood_menu(),
        )

        return

    # =====================================================
    # NEXT
    # =====================================================

    if cmd == "/next":

        state = get_state(
            uid
        )

        mood = state["mood"]
        radio = state["radio"]

        if radio:

            answer_text = (
                "📻 Finding your next Radio track..."
            )

            send(
                chat,
                answer_text,
            )

            schedule(
                chat,
                uid,
                mood or "melodic",
                True,
            )

            return

        if mood:

            send(
                chat,
                "⏭ Finding next track...",
            )

            schedule(
                chat,
                uid,
                mood,
                False,
            )

            return

        send(
            chat,
            "🎧 Choose your mood first 👇",
            mood_menu(),
        )

        return

    # =====================================================
    # RADIO
    # =====================================================

    if cmd == "/radio":

        state = get_state(
            uid
        )

        mood = (
            state["mood"]
            or "melodic"
        )

        set_radio(
            uid,
            True,
        )

        send(
            chat,
            "📻 RADIO MODE ON\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Your Radio learns from "
            "your Likes and Not For Me feedback "
            "across all moods. ✨",
        )

        schedule(
            chat,
            uid,
            mood,
            True,
        )

        return

    # =====================================================
    # PROFILE
    # =====================================================

    if cmd == "/profile":

        send(
            chat,
            profile_text(uid),
            mood_menu(),
        )

        return

    # =====================================================
    # NEW
    # =====================================================

    if cmd == "/new":

        new_tracks(
            chat
        )

        return

    # =====================================================
    # TOP
    # =====================================================

    if cmd == "/top":

        text, keyboard = (
            top_liked_text()
        )

        send(
            chat,
            text,
            keyboard or mood_menu(),
        )

        return

    # =====================================================
    # DAILY VIBE
    # =====================================================

    if cmd == "/dailyvibe":

        set_special_mode(uid, "daily_vibe")

        send_special_music(
            chat,
            uid,
            daily_vibe_track(uid),
            "🔥 YOUR DAILY VIBE",
            mode="daily_vibe",
        )

        return

    # =====================================================
    # FOR YOU
    # =====================================================

    if cmd == "/foryou":

        set_special_mode(uid, "for_you")

        send_special_music(
            chat,
            uid,
            for_you_track(uid),
            "🧠 PICKED FOR YOU",
            mode="for_you",
        )

        return

    # =====================================================
    # SURPRISE
    # =====================================================

    if cmd == "/surprise":

        set_special_mode(uid, "surprise_me")

        send_special_music(
            chat,
            uid,
            surprise_track(uid),
            "🎲 SURPRISE ME",
            mode="surprise_me",
        )

        return

    # =====================================================
    # TRENDING
    # =====================================================

    if cmd == "/trending":

        text, keyboard = (
            trending_text()
        )

        send(
            chat,
            text,
            keyboard or mood_menu(),
        )

        return

    # =====================================================
    # TODAY
    # =====================================================

    if cmd == "/today":

        set_special_mode(uid, "track_of_day")

        send_special_music(
            chat,
            uid,
            track_of_day(uid),
            "🎵 TRACK OF THE DAY",
            mode="track_of_day",
        )

        return

    # =====================================================
    # TASTE
    # =====================================================

    if cmd == "/taste":

        send(
            chat,
            taste_analytics(uid),
            mood_menu(),
        )

        return

    # =====================================================
    # HELP
    # =====================================================

    if cmd == "/help":

        send(
            chat,
            "🎧 NOT YOUR VIBE\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "/start /mood /next /radio /profile\n"
            "/new /top /trending /help\n\n"
            "🔥 Daily Vibe\n"
            "🧠 For You\n"
            "🎲 Surprise Me\n"
            "📈 Trending\n"
            "🎵 Track of the Day\n"
            "📊 Taste Analytics\n\n"
            "❤️ Like = strong positive signal\n"
            "😴 Not For Me = negative signal\n"
            "⏭ Next = neutral navigation\n\n"
            "📻 Radio learns from your feedback.",
        )

        return

    # =====================================================
    # ADMIN
    # =====================================================

    if cmd == "/admin":

        if str(uid) != ADMIN_USER_ID:

            return send(
                chat,
                "❌ Admin only.",
            )

        send(
            chat,
            admin_dashboard(),
            admin_panel(),
        )

        return

    # =====================================================
    # BROADCAST
    # =====================================================

    if cmd == "/broadcast":

        if str(uid) != ADMIN_USER_ID:

            return send(
                chat,
                "❌ Admin only.",
            )

        set_pending_broadcast(
            uid,
            chat,
        )

        send(
            chat,
            "📣 BROADCAST\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Now send the post you want to broadcast.\n\n"
            "✅ Text, photo, video, audio, "
            "document and other Telegram posts are supported.\n"
            "❌ Send /cancel to stop.",
            admin_panel(),
        )

        return

    # =====================================================
    # DAILY ADMIN
    # =====================================================

    if cmd == "/daily":

        if str(uid) != ADMIN_USER_ID:

            return send(
                chat,
                "❌ Admin only.",
            )

        total, today_n, week_n, rows = (
            daily_stats()
        )

        lines = [
            "📊 DAILY USERS",
            "━━━━━━━━━━━━━━━━━━",
            "",
            f"Today: {today_n}",
            f"Last 7 days unique: {week_n}",
            f"Total users: {total}",
            "",
            "Last 7 days:",
        ]

        lines.extend(
            f"{d.isoformat()} → {n}"
            for d, n in rows
        )

        send(
            chat,
            "\n".join(lines),
        )

        return

    # =====================================================
    # COMMENTS ADMIN
    # =====================================================

    if cmd == "/comments":

        if str(uid) != ADMIN_USER_ID:

            return send(
                chat,
                "❌ Admin only.",
            )

        send(
            chat,
            comments_text(),
        )

        return

    # =====================================================
    # STATS ADMIN
    # =====================================================

    if cmd == "/stats":

        if str(uid) != ADMIN_USER_ID:

            return send(
                chat,
                "❌ Admin only.",
            )

        cc = counts()

        send(
            chat,
            "📊 TRACKS\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            + "\n".join(
                f"{INFO[m][0]} → {cc[m]}"
                for m in MOODS
            )
            + "\n\n"
            + (
                "Telethon: "
                + (
                    "CONNECTED"
                    if ready.is_set()
                    else "DISCONNECTED"
                )
            ),
        )

        return

    # =====================================================
    # TELETHON
    # =====================================================

    if cmd == "/telegram":

        send(
            chat,
            (
                "🟢 TELETHON CONNECTED"
                if ready.is_set()
                else
                "🔴 TELETHON DISCONNECTED"
            ),
        )

        return


# =========================================================
# UPDATE ROUTER
# =========================================================

def update(u):

    if isinstance(
        u.get("callback_query"),
        Mapping,
    ):

        callback(
            u["callback_query"]
        )

    elif isinstance(
        u.get("message"),
        Mapping,
    ):

        message(
            u["message"]
        )


# =========================================================
# FLASK
# =========================================================

@app.route("/")
def home():

    return (
        "🎧 NOT YOUR VIBE MUSIC BOT ONLINE"
    )


@app.route("/health")
def health():

    try:

        with db() as c:

            with cur(c) as x:
                x.execute(
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

    return {
        "bot":
            "online",

        "ai":
            False,

        "database":
            "online"
            if db_pool
            else "offline",

        "telethon":
            "connected"
            if ready.is_set()
            else "disconnected",

        "tracks":
            counts(),

        "trending_days":
            TRENDING_DAYS,
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
        ) != WEBHOOK_SECRET
    ):

        return (
            "Forbidden",
            403,
        )

    try:

        u = request.get_json(
            silent=True
        )

        if isinstance(
            u,
            Mapping,
        ):

            update(
                u
            )

    except Exception:

        log.exception(
            "webhook"
        )

    return "OK", 200


# =========================================================
# TELETHON HELPERS
# =========================================================

def normch(v):

    if v is None:
        return None

    v = str(
        v
    ).strip()

    if v.startswith(
        "-100"
    ):
        return v

    if v.lstrip(
        "-"
    ).isdigit():

        return (
            "-100"
            + v.lstrip("-")
        )

    return None


def is_music(msg):

    if getattr(
        msg,
        "audio",
        None,
    ):
        return True

    d = getattr(
        msg,
        "document",
        None,
    )

    if not d:
        return False

    mime = (
        getattr(
            d,
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

    name = (
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

    return name.endswith(
        AUDIO
    )


def message_title(msg):
    # Real Telegram audio metadata first; never return copyMessage/message_id.
    try:
        for obj in (getattr(msg,"audio",None), getattr(msg,"document",None)):
            for attr in getattr(obj,"attributes",None) or []:
                title=getattr(attr,"title",None); performer=getattr(attr,"performer",None)
                if title and str(title).strip():
                    return (f"{str(performer).strip()} - " if performer and str(performer).strip() else "") + str(title).strip()
    except Exception: pass
    try:
        name=str(getattr(getattr(msg,"file",None),"name","") or "").strip()
        if name:
            for ext in AUDIO:
                if name.lower().endswith(ext): name=name[:-len(ext)]; break
            if name and not name.isdigit(): return name[:200]
    except Exception: pass
    try:
        text=str(getattr(msg,"message","") or "").strip()
        if text and not text.isdigit(): return text[:200]
    except Exception: pass
    return None

# =========================================================
# TELETHON SCAN
# =========================================================

async def scan(
    mood,
    val,
):

    if not val:
        return 0

    try:

        ent = await client.get_entity(
            int(val)
            if val.lstrip("-").isdigit()
            else val
        )

        n = 0

        async for msg in client.iter_messages(
            ent
        ):

            if is_music(msg):

                ch = normch(
                    getattr(
                        ent,
                        "id",
                        0,
                    )
                )

                if ch:

                    n += save_track(
                        mood,
                        ch,
                        msg.id,
                        message_title(
                            msg
                        ),
                    )

        return n

    except Exception:

        log.exception(
            "scan %s",
            mood,
        )

        return 0


async def scan_all():

    global last_scan

    channel_map.clear()

    for mood, value in CHANNELS.items():

        if value:

            normalized = normch(
                value
            )

            if normalized:

                channel_map[
                    normalized
                ] = mood

    for mood, value in CHANNELS.items():

        if value:

            await scan(
                mood,
                value,
            )

            await asyncio.sleep(
                0.3
            )

    last_scan = int(
        time.time()
    )

    log.info(
        "tracks=%s",
        counts(),
    )


# =========================================================
# TELETHON WORKER
# =========================================================

def tele_worker():

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

        api_id = int(
            API_ID
        )

    except (
        TypeError,
        ValueError,
    ):

        log.error(
            "TELETHON_API_ID/API_ID "
            "must be an integer"
        )

        return

    client = TelegramClient(
        StringSession(
            SESSION
        ),
        api_id,
        API_HASH,
        connection_retries=10,
        retry_delay=5,
        timeout=30,
        auto_reconnect=True,
    )

    # =====================================================
    # NEW CHANNEL TRACK WATCHER
    # =====================================================

    @client.on(
        events.NewMessage(
            incoming=True
        )
    )
    async def new(event):

        try:

            ch = normch(
                event.chat_id
            )

            mood = channel_map.get(
                ch
            )

            if (
                mood
                and is_music(
                    event.message
                )
            ):

                save_track(
                    mood,
                    ch,
                    event.message.id,
                    message_title(
                        event.message
                    ),
                )

                log.info(
                    "NEW TRACK mood=%s "
                    "channel=%s message=%s",
                    mood,
                    ch,
                    event.message.id,
                )

        except Exception:

            log.exception(
                "watcher"
            )

    # =====================================================
    # TELETHON LOOP
    # =====================================================

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
                    "Telethon connected"
                )

                await scan_all()

                await client.run_until_disconnected()

            except Exception:

                log.exception(
                    "Telethon error"
                )

            finally:

                ready.clear()

                try:

                    if client.is_connected():

                        await client.disconnect()

                except Exception:

                    pass

            log.warning(
                "Telethon reconnecting in %ss",
                RECONNECT,
            )

            await asyncio.sleep(
                RECONNECT
            )

    asyncio.run(
        run()
    )


# =========================================================
# START TELETHON
# =========================================================

def start_telethon():

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


# =========================================================
# WEBHOOK SETUP
# =========================================================

def webhook_setup():

    if (
        not BOT_TOKEN
        or not RENDER_EXTERNAL_URL
    ):
        return

    p = {
        "url":
            RENDER_EXTERNAL_URL.rstrip("/")
            + "/webhook",

        "allowed_updates": [
            "message",
            "callback_query",
        ],

        "max_connections":
            40,
    }

    if WEBHOOK_SECRET:

        p["secret_token"] = (
            WEBHOOK_SECRET
        )

    result = tg(
        "setWebhook",
        p,
    )

    log.info(
        "webhook=%s description=%s",
        result.get("ok"),
        result.get("description"),
    )


# =========================================================
# STARTUP
# =========================================================

def startup():

    if (
        not BOT_TOKEN
        or not DATABASE_URL
    ):

        log.error(
            "BOT_TOKEN and DATABASE_URL "
            "are required"
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

    log.info(
        "🟢 BOT READY"
    )

    return True


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    if not startup():

        raise SystemExit(1)

    app.run(
        host="0.0.0.0",
        port=geti(
            "PORT",
            10000,
            1,
            65535,
        ),
        threaded=True,
        use_reloader=False,
    )
