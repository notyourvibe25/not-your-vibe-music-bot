from __future__ import annotations

import asyncio
import io
import hashlib
import hmac
import json
import logging
import os
import random
import threading
import math
import time
import contextlib
from html import escape
from urllib.parse import quote, parse_qsl

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Mapping

import requests
from flask import Flask, request, Response, render_template, jsonify, send_file, make_response

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


def getf(n, d, lo, hi):
    try:
        v = float(env(n, str(d)))
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
BOT_USERNAME = (env("BOT_USERNAME", "NotYourVibeMusicBot") or "NotYourVibeMusicBot").lstrip("@")
ADMIN_USER_ID = env("ADMIN_USER_ID")
DATABASE_URL = env("DATABASE_URL")

RENDER_EXTERNAL_URL = env("RENDER_EXTERNAL_URL") or (
    ("https://" + env("RENDER_EXTERNAL_HOSTNAME"))
    if env("RENDER_EXTERNAL_HOSTNAME")
    else ""
)

# Public HTTPS URL used by Telegram to open the Mini App.
# Set MINI_APP_URL only if you want a custom URL; otherwise /mini-app is used.
MINI_APP_URL = (
    env("MINI_APP_URL")
    or (RENDER_EXTERNAL_URL.rstrip("/") + "/mini-app" if RENDER_EXTERNAL_URL else "")
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

# Radio mood balancing.  These are environment-configurable so the live
# recommendation mix can be tuned without changing application code.
RADIO_MOOD_TEMPERATURE = getf("RADIO_MOOD_TEMPERATURE", 1.5, 0.1, 10.0)
RADIO_MOOD_MAX_RATIO = getf("RADIO_MOOD_MAX_RATIO", 0.40, 0.05, 1.0)
RADIO_BASELINE_MOOD_MULTIPLIER = getf(
    "RADIO_BASELINE_MOOD_MULTIPLIER", 0.22, 0.0, 10.0
)
RADIO_FEEDBACK_DECAY_SCALE = getf(
    "RADIO_FEEDBACK_DECAY_SCALE", 12.0, 1.0, 1000.0
)
RADIO_TIME_MOOD_BONUS_SCALE = getf(
    "RADIO_TIME_MOOD_BONUS_SCALE", 4.0, 0.0, 20.0
)
RADIO_TIME_MOOD_WEIGHT = getf(
    "RADIO_TIME_MOOD_WEIGHT", 0.35, 0.0, 1.0
)
RADIO_TIME_BPM_BONUS = getf(
    "RADIO_TIME_BPM_BONUS", 2.0, 0.0, 20.0
)
RADIO_TIME_EVENING_MAX_BPM = getf(
    "RADIO_TIME_EVENING_MAX_BPM", 180.0, 150.0, 240.0
)
RADIO_TIME_NIGHT_FAST_MIN_BPM = getf(
    "RADIO_TIME_NIGHT_FAST_MIN_BPM", 170.0, 140.0, 240.0
)
RADIO_TIME_NIGHT_FAST_MAX_BPM = getf(
    "RADIO_TIME_NIGHT_FAST_MAX_BPM", 200.0, 160.0, 260.0
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

ANALYZER_ENABLED = env("ANALYZER_ENABLED", "true").lower() not in {"0", "false", "no", "off"}
ANALYZER_LIMIT = geti("ANALYZER_LIMIT", 10, 1, 100)
ANALYZER_WATCH_INTERVAL = geti("ANALYZER_WATCH_INTERVAL", 60, 10, 3600)
analyzer_thread = None
analyzer_stop_event = threading.Event()
analyzer_lock = threading.Lock()

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

    CREATE TABLE IF NOT EXISTS pending_comment_replies(
        admin_id BIGINT PRIMARY KEY,
        comment_id BIGINT NOT NULL,
        created_at BIGINT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS broadcast_comment_replies(
        id BIGSERIAL PRIMARY KEY,
        comment_id BIGINT NOT NULL,
        admin_id BIGINT NOT NULL,
        reply TEXT NOT NULL,
        created_at BIGINT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS pending_broadcasts(
        user_id BIGINT PRIMARY KEY,
        chat_id BIGINT NOT NULL,
        created_at BIGINT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS processed_updates(
        update_id BIGINT PRIMARY KEY,
        processed_at BIGINT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS radio_daily_deliveries(
        day DATE NOT NULL,
        user_id BIGINT NOT NULL,
        channel_id TEXT NOT NULL,
        message_id BIGINT NOT NULL,
        reserved_at BIGINT NOT NULL,
        PRIMARY KEY(day,user_id,channel_id,message_id)
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

    -- Older scanner/database versions created analyzed_at as BIGINT epoch
    -- seconds. Convert that existing column before the scanner writes NOW().
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'tracks'
              AND column_name = 'analyzed_at'
              AND data_type = 'bigint'
        ) THEN
            ALTER TABLE tracks
            ALTER COLUMN analyzed_at TYPE TIMESTAMPTZ
            USING CASE
                WHEN analyzed_at IS NULL THEN NULL
                ELSE to_timestamp(analyzed_at)
            END;
        END IF;
    END $$;

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
            weight = math.exp(-index / RADIO_FEEDBACK_DECAY_SCALE)
            weighted_sum += value * weight
            weight_sum += weight

        if weight_sum:
            profile[field] = weighted_sum / weight_sum

    for index, row in enumerate(likes[:40]):
        weight = math.exp(-index / RADIO_FEEDBACK_DECAY_SCALE)

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


def _radio_song_key(title):
    """Return a conservative normalized song identity for Radio daily dedupe.

    Telegram message/channel identity is still used for feedback/history, but
    Radio also needs a song-level identity because the same song can exist as
    multiple Telegram messages in different channels.
    """
    import re
    text = str(title or "").strip().casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _radio_today_served(uid):
    """Return every track already served to this user today.

    Radio treats this as a hard daily freshness boundary: a track served once
    today must not be sent again by Radio until the date changes.
    """
    served = set()

    today = datetime.now(ZoneInfo("Asia/Yangon")).date()
    start = int(datetime.combine(
        today,
        datetime.min.time(),
        tzinfo=ZoneInfo("Asia/Yangon"),
    ).timestamp())
    end = int(datetime.combine(
        today + timedelta(days=1),
        datetime.min.time(),
        tzinfo=ZoneInfo("Asia/Yangon"),
    ).timestamp())

    with db() as c:
        with cur(c) as x:
            x.execute(
                """
                SELECT channel_id,message_id
                FROM user_history
                WHERE user_id=%s
                  AND action='served'
                  AND sent_at >= %s
                  AND sent_at < %s
                """,
                (uid, start, end),
            )

            for row in x.fetchall():
                served.add((str(row["channel_id"]), int(row["message_id"])))

    return served


def _radio_today_song_keys(uid):
    """Return normalized song titles already served by Radio today.

    This closes the duplicate-song gap where identical audio/song records have
    different Telegram channel/message identities.
    """
    keys = set()
    today = datetime.now(ZoneInfo("Asia/Yangon")).date()
    start = int(datetime.combine(today, datetime.min.time(), tzinfo=ZoneInfo("Asia/Yangon")).timestamp())
    end = int(datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=ZoneInfo("Asia/Yangon")).timestamp())
    with db() as c:
        with cur(c) as x:
            x.execute(
                """
                SELECT t.title
                FROM user_history h
                JOIN tracks t
                  ON t.channel_id=h.channel_id
                 AND t.message_id=h.message_id
                WHERE h.user_id=%s
                  AND h.action='served'
                  AND h.sent_at >= %s
                  AND h.sent_at < %s
                  AND COALESCE(TRIM(t.title),'') <> ''
                """,
                (uid, start, end),
            )
            for row in x.fetchall():
                key = _radio_song_key(row.get("title"))
                if key:
                    keys.add(key)
    return keys


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
                AND NOT EXISTS (
                    SELECT 1
                    FROM user_history failed
                    WHERE failed.user_id=%s
                      AND failed.channel_id=t.channel_id
                      AND failed.message_id=t.message_id
                      AND failed.action='delivery_failed'
                )
                ORDER BY t.id ASC
                """,
                (uid, uid),
            )
            return x.fetchall()


def _radio_harmonic_transition_score(candidate_key, last_key):
    """Prefer Camelot-compatible keys and avoid large harmonic jumps."""
    if not candidate_key or not last_key:
        return 0.0

    def camelot(value):
        text = str(value).strip().upper().replace("♯", "#").replace("♭", "B")
        import re
        match = re.search(r"(?:^|\s)(1[0-2]|[1-9])\s*([AB])(?:\s|$)", text)
        if not match:
            return None
        return int(match.group(1)), match.group(2)

    left = camelot(candidate_key)
    right = camelot(last_key)
    if left and right:
        number, mode = left
        previous_number, previous_mode = right
        distance = abs(number - previous_number)
        distance = min(distance, 12 - distance)
        if distance == 0 and mode == previous_mode:
            return 8.0
        if distance == 1 and mode == previous_mode:
            return 6.0
        if distance == 0 and mode != previous_mode:
            return 5.5
        if distance == 1 and mode != previous_mode:
            return 2.0
        return -min(8.0, 2.0 + distance * 1.5)

    # Also support analyzed keys such as "F minor" / "Ab major".
    note_order = {"C": 0, "B#": 0, "C#": 1, "DB": 1, "D": 2,
                  "D#": 3, "EB": 3, "E": 4, "FB": 4, "E#": 5,
                  "F": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8,
                  "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11, "CB": 11}

    def note_key(value):
        parts = str(value).strip().upper().replace("♯", "#").replace("♭", "B").split()
        if len(parts) < 2:
            return None
        root = note_order.get(parts[0])
        mode = "MINOR" if parts[1].startswith("MIN") else "MAJOR" if parts[1].startswith("MAJ") else None
        return (root, mode) if root is not None and mode else None

    left = note_key(candidate_key)
    right = note_key(last_key)
    if not left or not right:
        return 0.0
    distance = abs(left[0] - right[0])
    distance = min(distance, 12 - distance)
    if distance == 0 and left[1] == right[1]:
        return 8.0
    if distance == 1 and left[1] == right[1]:
        return 6.0
    # Relative major/minor (for example F minor <-> Ab major) is harmonic-
    # mixing compatible even though the tonic names are different.
    if ((right[1] == "MINOR" and left[1] == "MAJOR" and left[0] == (right[0] + 3) % 12)
            or (right[1] == "MAJOR" and left[1] == "MINOR" and left[0] == (right[0] + 9) % 12)):
        return 5.5
    if distance == 0 and left[1] != right[1]:
        return 3.0
    return -min(8.0, 2.0 + distance * 1.5)


def _radio_bpm_is_smooth(candidate_bpm, last_bpm, max_delta=12.0):
    """Trending/top-list tracks are eligible only when BPM transition is smooth."""
    if last_bpm is None:
        return candidate_bpm is not None
    try:
        candidate = float(candidate_bpm)
        previous = float(last_bpm)
    except (TypeError, ValueError):
        return False
    return math.isfinite(candidate) and math.isfinite(previous) and abs(candidate - previous) <= max_delta


def _radio_feature_similarity(a, b):
    """
    Audio/content similarity in [0,1].

    The weighting intentionally favours genre/subgenre and audio feel over
    exact musical key, which is closer to how a practical music radio should
    behave.  Missing analyzer fields are simply ignored.
    """
    parts = []

    for field, scale, weight in (
        ("bpm", 60.0, 0.01),
        ("energy", 0.30, 0.25),
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

        recency = math.exp(-index / RADIO_FEEDBACK_DECAY_SCALE)
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


def _radio_bpm_smooth_transition_score(candidate_bpm, last_bpm, recent_bpms=None):
    """Score BPM continuity without turning BPM into a fixed target.

    Radio should *travel* through BPM space instead of repeatedly selecting
    the two values nearest the user's Like centroid.  The previous BPM is
    therefore only a transition anchor. Small-to-medium moves are preferred,
    while exact repeats and large jumps are discouraged.

    A short recent-BPM window also discourages immediate back-and-forth
    patterns such as 134 -> 126 -> 134.
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

    # Avoid exact/near repeats: they are a common cause of BPM lock.
    if delta < 1.0:
        score = -6.0
    elif delta < 2.0:
        score = -1.0
    elif delta <= 5.0:
        score = 8.0
    elif delta <= 8.0:
        score = 7.0
    elif delta <= 11.0:
        score = 4.0
    elif delta <= 14.0:
        score = 0.0
    elif delta <= 18.0:
        score = -5.0
    else:
        score = -10.0 - min(10.0, (delta - 18.0) * 0.5)

    # If the last two moves went in one direction, gently continue that
    # direction. This prevents a 134 <-> 126 ping-pong while still allowing
    # the radio to turn around after a few tracks.
    if recent_bpms and len(recent_bpms) >= 2:
        try:
            prev2 = float(recent_bpms[-2])
            prev1 = float(recent_bpms[-1])
        except (TypeError, ValueError):
            prev2 = prev1 = None

        if prev2 is not None and prev1 is not None:
            direction = prev1 - prev2
            move = bpm - previous

            if abs(direction) >= 1.0 and abs(move) >= 1.0:
                same_direction = (direction > 0 and move > 0) or (direction < 0 and move < 0)
                opposite_direction = (direction > 0 and move < 0) or (direction < 0 and move > 0)

                if same_direction and abs(move) <= 8.0:
                    score += 2.5
                elif opposite_direction and abs(move) <= 5.0:
                    score -= 2.0

    return score


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

        weight = math.exp(-index / RADIO_FEEDBACK_DECAY_SCALE)
        scores.append((sim, weight))

    if not scores:
        return 0.0

    return (
        sum(sim * weight for sim, weight in scores)
        / sum(weight for _, weight in scores)
    )


def get_time_based_mood_bias(now=None):
    """Return Yangon-local time context as mood bias and ideal BPM range(s).

    The source specification mentions mood labels not present in this bot
    (happy, acoustic, groove, deep, dance, bass, ambient).  They are mapped to
    the closest existing channels so time context remains effective without
    creating unsupported moods or database values.
    """
    current = now or datetime.now(ZoneInfo("Asia/Yangon"))
    hour = current.hour
    if 6 <= hour < 12:
        return {
            "energetic": 0.40,
            "melodic": 0.30,
            "chill": 0.20,
            "hype": 0.10,
        }, ((100, 135),)
    if 12 <= hour < 17:
        return {
            "chill": 0.35,
            "hype": 0.30,
            "dark": 0.20,
            "melodic": 0.15,
        }, ((115, 140),)
    if 17 <= hour < 22:
        return {
            "energetic": 0.45,
            "hype": 0.25,
            "dark": 0.15,
            "night": 0.10,
            "melodic": 0.05,
        }, ((128, RADIO_TIME_EVENING_MAX_BPM),)
    return (
        {
            "sad": 0.35,
            "chill": 0.25,
            "night": 0.25,
            "melodic": 0.10,
            "energetic": 0.05,
        },
        ((60, 110), (RADIO_TIME_NIGHT_FAST_MIN_BPM, RADIO_TIME_NIGHT_FAST_MAX_BPM)),
    )


def _radio_time_context_score(row, time_mood_bias, bpm_range):
    """Score how well a track fits the current time context."""
    score = (
        time_mood_bias.get(row.get("mood"), 0.0)
        * RADIO_TIME_MOOD_BONUS_SCALE
    )
    try:
        bpm = float(row.get("bpm"))
    except (TypeError, ValueError):
        bpm = None
    if bpm is not None and math.isfinite(bpm):
        if any(low <= bpm <= high for low, high in bpm_range):
            score += RADIO_TIME_BPM_BONUS
    return score


def adjust_mood_ratios(
    raw_mood_scores,
    temperature=RADIO_MOOD_TEMPERATURE,
    max_cap=RADIO_MOOD_MAX_RATIO,
):
    """Convert raw mood scores into stable, capped dynamic ratios.

    Temperature scaling reduces the effect of a single dominant mood.  The
    cap is applied with iterative water-filling, so redistribution can never
    push a secondary mood above the same ceiling and the result still sums to
    one (within floating-point precision).
    """
    if not raw_mood_scores:
        return {}

    temperature = max(0.1, float(temperature))
    max_cap = min(1.0, max(0.0, float(max_cap)))
    if max_cap * len(raw_mood_scores) < 1.0:
        max_cap = 1.0 / len(raw_mood_scores)

    finite_scores = {}
    for mood, score in raw_mood_scores.items():
        try:
            score = float(score)
        except (TypeError, ValueError):
            continue
        if math.isfinite(score):
            finite_scores[mood] = max(0.0, score)
    if not finite_scores:
        return {}

    # Numerically stable softmax variant.
    scaled = {m: score / temperature for m, score in finite_scores.items()}
    peak = max(scaled.values())
    exp_scores = {m: math.exp(value - peak) for m, value in scaled.items()}
    total = sum(exp_scores.values())
    ratios = {m: value / total for m, value in exp_scores.items()}

    remaining = set(ratios)
    result = {}
    residual = 1.0
    while remaining:
        available = sum(ratios[m] for m in remaining)
        proposed = {
            m: residual * ratios[m] / available
            for m in remaining
        }
        over = [m for m, value in proposed.items() if value > max_cap]
        if not over:
            result.update(proposed)
            break
        for mood in over:
            result[mood] = max_cap
            residual -= max_cap
            remaining.remove(mood)

    # A final bounded normalization removes tiny floating-point drift.
    total_result = sum(result.values())
    return {m: value / total_result for m, value in result.items()}


def radio_weights(uid, baseline_mood=None):
    """Return temperature-scaled, capped mood ratios for Radio."""
    stats = ratios(uid)
    raw_scores = {}
    for mood in MOODS:
        liked = float(stats[mood]["like"])
        disliked = float(stats[mood]["not"])
        # Bayesian-smoothed preference: neutral users still hear every mood.
        raw_scores[mood] = (liked + 1.0) / (liked + disliked + 2.0)

    if baseline_mood in raw_scores:
        raw_scores[baseline_mood] += RADIO_BASELINE_MOOD_MULTIPLIER

    return adjust_mood_ratios(raw_scores)


def radio_track(uid, baseline_mood=None):
    """Choose Radio tracks from user taste ratios without AI.

    It uses a strict 70/20/10 source mix: fresh tracks, tracks the user liked,
    and the global trending/top-10-liked set. Within each pool, candidates are
    ranked by mood ratio, recency-weighted nearest-neighbor similarity, BPM and
    harmonic continuity, and a negative similarity boundary.
    """
    rows = _radio_candidates(uid)
    if not rows:
        return None

    feedback = feedback_map(uid)
    recent = _radio_history(uid)
    today_served = _radio_today_served(uid)
    today_song_keys = _radio_today_song_keys(uid)
    mood_weights = radio_weights(uid, baseline_mood=baseline_mood)
    profile = _radio_profile(uid)
    liked_seeds = profile.get("likes", [])
    disliked_seeds = profile.get("dislikes", [])
    time_mood_bias, time_bpm_range = get_time_based_mood_bias()
    # Context-aware prior: user taste remains dominant, while the timeline
    # meaningfully steers the mood mix. Unsupported/absent moods contribute 0.
    context_weight = RADIO_TIME_MOOD_WEIGHT
    blended_mood_weights = {
        mood: ((1.0 - context_weight) * float(mood_weights.get(mood, 0.0)))
        + (context_weight * float(time_mood_bias.get(mood, 0.0)))
        for mood in MOODS
    }
    blended_total = sum(blended_mood_weights.values())
    if blended_total > 0:
        mood_weights = {
            mood: value / blended_total
            for mood, value in blended_mood_weights.items()
        }
    last_seed = _radio_last_seed(uid)
    last_bpm = last_seed.get("bpm") if last_seed else None
    last_key = last_seed.get("musical_key") if last_seed else None

    top_ids = {
        (str(row["channel_id"]), int(row["message_id"]))
        for row in top_liked_tracks(10)
    }
    trending_ids = {
        (str(row["channel_id"]), int(row["message_id"]))
        for row in trending_rows(10)
    }
    special_ids = top_ids | trending_ids

    usable = []
    for row in rows:
        mood = row.get("mood")
        if mood not in MOODS:
            continue
        key = (str(row["channel_id"]), int(row["message_id"]))
        song_key = _radio_song_key(row.get("title"))
        if key in today_served or song_key in today_song_keys or feedback.get(key) == "not_for_me":
            continue
        usable.append((row, key))

    if not usable:
        return None

    special_pool = [
        item for item in usable
        if item[1] in special_ids
    ]
    liked_pool = [
        item for item in usable
        if feedback.get(item[1]) == "like" and item[1] not in special_ids
    ]
    fresh_pool = [
        item for item in usable
        if feedback.get(item[1]) != "like"
        and item[1] not in recent
        and item[1] not in special_ids
    ]
    unliked_pool = [item for item in usable if feedback.get(item[1]) != "like"]

    # Progressive fallback: daily-served and disliked tracks remain hard
    # boundaries, while BPM continuity becomes softer only when necessary.
    # Time-of-day mood/BPM context remains a scoring signal at every stage.
    pools = (fresh_pool, liked_pool, special_pool)
    pool_weights = (0.70, 0.20, 0.10)
    selected_stage = None
    for max_delta in (12.0, 20.0, 30.0, None):
        stage_pools = []
        for pool in pools:
            if max_delta is None or last_bpm is None:
                stage_pools.append(pool)
            else:
                stage_pools.append([
                    item for item in pool
                    if _radio_bpm_is_smooth(
                        item[0].get("bpm"),
                        last_bpm,
                        max_delta=max_delta,
                    )
                ])

        available = [
            (pool, weight)
            for pool, weight in zip(stage_pools, pool_weights)
            if pool
        ]
        if available:
            selected_stage = max_delta
            pick = random.random() * sum(weight for _, weight in available)
            pool = available[-1][0]
            for candidate_pool, weight in available:
                pick -= weight
                if pick <= 0:
                    pool = candidate_pool
                    break
            break

    if selected_stage is None and last_bpm is not None:
        log.info("radio exhausted uid=%s after progressive BPM fallback", uid)
        return None
    if selected_stage is None:
        pool = unliked_pool or usable

    scored = []
    for row, key in pool:
        mood = row["mood"]
        score = 48.0 * float(mood_weights.get(mood, 0.5))

        # Weighted nearest-neighbor taste matching.  This is deliberately
        # rule-based: recent Likes receive larger decay weights and the best
        # matching seed is retained so a strong local match is not diluted.
        similarity = _radio_seed_similarity(row, liked_seeds)
        if similarity is not None:
            score += 16.0 * similarity

        negative_similarity = _radio_negative_similarity(row, disliked_seeds)
        if negative_similarity:
            score -= 12.0 * negative_similarity

        # Time-of-day is a soft context signal: it steers recommendations
        # toward the appropriate mood/BPM without overriding user feedback,
        # dislikes, daily freshness, or hard BPM transition gates.
        score += _radio_time_context_score(
            row,
            time_mood_bias,
            time_bpm_range,
        )

        # Fresh tracks are preferred inside the 70% discovery bucket.
        if key not in recent:
            score += 18.0
        else:
            rank = int(recent[key]["rank"])
            score -= max(0.0, 14.0 - min(rank, 16) * 0.8)

        # BPM continuity: small/medium changes are preferred; large jumps lose.
        score += 2.2 * _radio_bpm_smooth_transition_score(
            row.get("bpm"),
            last_bpm,
        )
        # Harmonic compatibility has a smaller, deliberate priority than
        # taste/content similarity, while discouraging large key jumps.
        score += 1.25 * _radio_harmonic_transition_score(
            row.get("musical_key"),
            last_key,
        )

        try:
            score += min(5.0, math.log1p(float(row.get("global_likes") or 0)) * 1.0)
            score += min(1.5, math.log1p(float(row.get("global_served") or 0)) * 0.18)
        except (TypeError, ValueError):
            pass

        score += random.uniform(0.0, 2.5)
        scored.append((score, row))

    scored.sort(key=lambda item: item[0], reverse=True)
    shortlist = scored[:min(8, len(scored))]
    top = shortlist[0][0]
    weights = [
        math.exp(max(-20.0, (score - top) / 5.0))
        for score, _ in shortlist
    ]
    row = random.choices(shortlist, weights=weights, k=1)[0][1]
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
    no_repeat_today=False,
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

            if no_repeat_today:
                today = datetime.now(ZoneInfo("Asia/Yangon")).date()
                start = int(datetime.combine(
                    today,
                    datetime.min.time(),
                    tzinfo=ZoneInfo("Asia/Yangon"),
                ).timestamp())
                end = int(datetime.combine(
                    today + timedelta(days=1),
                    datetime.min.time(),
                    tzinfo=ZoneInfo("Asia/Yangon"),
                ).timestamp())
                x.execute(
                    """
                    SELECT 1
                    FROM user_history
                    WHERE user_id=%s
                      AND channel_id=%s
                      AND message_id=%s
                      AND action='served'
                      AND sent_at >= %s
                      AND sent_at < %s
                    LIMIT 1
                    """,
                    (uid, str(track[2]), int(track[1]), start, end),
                )
                if x.fetchone():
                    return None

                # Song-level same-day guard. The same song may exist under
                # different Telegram messages/channels, so source-message
                # identity alone is not enough for Radio daily freshness.
                x.execute(
                    """
                    SELECT 1
                    FROM tracks candidate
                    JOIN user_history h
                      ON h.user_id=%s
                     AND h.action='served'
                     AND h.channel_id=candidate.channel_id
                     AND h.message_id=candidate.message_id
                     AND h.sent_at >= %s
                     AND h.sent_at < %s
                    JOIN tracks served_track
                      ON served_track.channel_id=h.channel_id
                     AND served_track.message_id=h.message_id
                    WHERE candidate.channel_id=%s
                      AND candidate.message_id=%s
                      AND COALESCE(TRIM(candidate.title),'') <> ''
                      AND COALESCE(TRIM(served_track.title),'') <> ''
                      AND regexp_replace(lower(trim(candidate.title)), '[^[:alnum:]]+', ' ', 'g')
                          = regexp_replace(lower(trim(served_track.title)), '[^[:alnum:]]+', ' ', 'g')
                    LIMIT 1
                    """,
                    (uid, start, end, str(track[2]), int(track[1])),
                )
                if x.fetchone():
                    return None

                # This is the final same-day Radio guard.  It is independent
                # of user_history.action, so a delivery error or another
                # worker cannot make the same source message eligible again.
                x.execute(
                    """
                    INSERT INTO radio_daily_deliveries(
                        day,user_id,channel_id,message_id,reserved_at
                    )
                    VALUES(%s,%s,%s,%s,%s)
                    ON CONFLICT(day,user_id,channel_id,message_id)
                    DO NOTHING
                    """,
                    (
                        today,
                        uid,
                        str(track[2]),
                        int(track[1]),
                        int(time.time()),
                    ),
                )
                if x.rowcount != 1:
                    return None

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


def mark_delivery_failed(uid, track):
    """Remember an unavailable Telegram message without marking it served."""
    if not track:
        return

    try:
        with db() as c:
            with cur(c) as x:
                x.execute(
                    """
                    UPDATE user_history
                    SET action='delivery_failed'
                    WHERE id=(
                        SELECT id
                        FROM user_history
                        WHERE user_id=%s
                          AND channel_id=%s
                          AND message_id=%s
                          AND action='served'
                        ORDER BY sent_at DESC,id DESC
                        LIMIT 1
                    )
                    """,
                    (uid, str(track[2]), int(track[1])),
                )
                if x.rowcount:
                    return
                x.execute(
                    """
                    INSERT INTO user_history(
                        user_id, mood, channel_id, message_id, action, sent_at
                    )
                    VALUES(%s,%s,%s,%s,'delivery_failed',%s)
                    """,
                    (
                        uid,
                        track[0],
                        str(track[2]),
                        int(track[1]),
                        int(time.time()),
                    ),
                )
    except Exception:
        # Keep the original delivery error as the user-facing result.
        log.exception("could not record failed delivery uid=%s", uid)


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

            x.execute(
                """
                DELETE FROM pending_comment_replies
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


def set_pending_comment_reply(admin_id, comment_id):

    with db() as c:
        with cur(c) as x:
            x.execute(
                """
                INSERT INTO pending_comment_replies(admin_id, comment_id, created_at)
                VALUES(%s,%s,%s)
                ON CONFLICT(admin_id) DO UPDATE SET
                    comment_id=EXCLUDED.comment_id,
                    created_at=EXCLUDED.created_at
                """,
                (admin_id, comment_id, int(time.time())),
            )


def get_pending_comment_reply(admin_id):
    with db() as c:
        with cur(c) as x:
            x.execute("SELECT comment_id FROM pending_comment_replies WHERE admin_id=%s", (admin_id,))
            r=x.fetchone()
    return int(r["comment_id"]) if r else None


def send_comment_reply(admin_id, comment_id, reply):
    with db() as c:
        with cur(c) as x:
            x.execute("SELECT user_id, broadcast_id, comment FROM broadcast_comments WHERE id=%s", (comment_id,))
            row=x.fetchone()
            if not row: return None
            x.execute(
                "INSERT INTO broadcast_comment_replies(comment_id, admin_id, reply, created_at) VALUES(%s,%s,%s,%s)",
                (comment_id, admin_id, reply, int(time.time())),
            )
            x.execute("DELETE FROM pending_comment_replies WHERE admin_id=%s", (admin_id,))
    return row


def listener_top5_text():
    with db() as c:
        with cur(c) as x:
            x.execute("""
                SELECT h.user_id, COUNT(*) AS listens, u.username, u.first_name, u.last_name
                FROM user_history h
                LEFT JOIN users u ON u.user_id=h.user_id
                WHERE h.action='served'
                GROUP BY h.user_id, u.username, u.first_name, u.last_name
                ORDER BY listens DESC, h.user_id ASC
                LIMIT 5
            """)
            rows=x.fetchall()
    lines=["🎧 TOP 5 LISTENERS", "━━━━━━━━━━━━━━━━━━", ""]
    if not rows:
        lines.append("No listening activity yet.")
        return "\n".join(lines)
    for i,row in enumerate(rows,1):
        name=" ".join(v for v in (row.get("first_name") or "", row.get("last_name") or "") if v).strip()
        display=f"@{row['username']}" if row.get("username") else (name or f"User {row['user_id']}")
        lines.append(f"{i}. {display} — 🎵 {int(row['listens'])} tracks")
    return "\n".join(lines)


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

def comments_text(limit=20):

    with db() as c:
        with cur(c) as x:
            x.execute("""
                SELECT c.id, c.broadcast_id, c.user_id, c.comment, c.created_at,
                       u.username, u.first_name, u.last_name
                FROM broadcast_comments c
                LEFT JOIN users u ON u.user_id=c.user_id
                ORDER BY c.id DESC LIMIT %s
            """, (limit,))
            rows=x.fetchall()

    if not rows:
        return "💬 COMMENTS\n\nNo comments yet."

    lines=["💬 LATEST COMMENTS", "━━━━━━━━━━━━━━━━━━", ""]
    for a in rows:
        txt=(a["comment"] or "").replace("\n"," ")[:180]
        name=" ".join(v for v in (a.get("first_name") or "", a.get("last_name") or "") if v).strip()
        username=f"@{a['username']}" if a.get("username") else (name or f"User {a['user_id']}")
        lines.append(f"💬 #{a['id']} • {username}\nBroadcast #{a['broadcast_id']}\n{txt}\n")
    return "\n".join(lines)


def comments_keyboard(limit=20):
    with db() as c:
        with cur(c) as x:
            x.execute("SELECT id FROM broadcast_comments ORDER BY id DESC LIMIT %s", (limit,))
            rows=x.fetchall()
    return {"inline_keyboard":[[{"text":f"↩️ Reply #{r['id']}","callback_data":f"cr:{r['id']}"}] for r in rows]} if rows else None


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
                },
                {
                    "text":
                        "🎧 TOP 5 LISTENERS",
                    "callback_data":
                        "admin:listeners",
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

    keyboard = [
        [
            {
                "text": "🎧 OPEN NOT YOUR VIBE",
                "web_app": {"url": MINI_APP_URL},
            },
        ],
        [
            {"text": INFO["sad"][0], "callback_data": "mood_sad"},
            {"text": INFO["love"][0], "callback_data": "mood_love"},
        ],
        [
            {"text": INFO["chill"][0], "callback_data": "mood_chill"},
            {"text": INFO["hype"][0], "callback_data": "mood_hype"},
        ],
        [
            {"text": INFO["dark"][0], "callback_data": "mood_dark"},
            {"text": INFO["energetic"][0], "callback_data": "mood_energetic"},
        ],
        [
            {"text": INFO["night"][0], "callback_data": "mood_night"},
            {"text": INFO["melodic"][0], "callback_data": "mood_melodic"},
        ],
        [
            {"text": "🔥 DAILY VIBE", "callback_data": "daily_vibe"},
            {"text": "🧠 FOR YOU", "callback_data": "for_you"},
        ],
        [
            {"text": "🎲 SURPRISE ME", "callback_data": "surprise_me"},
            {"text": "📈 TRENDING", "callback_data": "trending"},
        ],
        [
            {"text": "🎵 TRACK OF THE DAY", "callback_data": "track_of_day"},
        ],
        [
            {"text": "🏆 TOP 10 LIKED", "callback_data": "top_liked"},
        ],
    ]
    return {"inline_keyboard": keyboard}


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

def share_track_id(channel_id, message_id):
    with db() as c:
        with cur(c) as x:
            x.execute(
                "SELECT id FROM tracks WHERE channel_id=%s AND message_id=%s ORDER BY id DESC LIMIT 1",
                (str(channel_id), int(message_id)),
            )
            row = x.fetchone()
    return int(row["id"]) if row else None


def track_share_url(channel_id, message_id):
    track_id = share_track_id(channel_id, message_id)
    if not track_id:
        return f"https://t.me/{BOT_USERNAME}"
    return f"https://t.me/{BOT_USERNAME}?start=track_{track_id}"


def track_share_title(channel_id, message_id):
    with db() as c:
        with cur(c) as x:
            x.execute(
                "SELECT title FROM tracks WHERE channel_id=%s AND message_id=%s ORDER BY id DESC LIMIT 1",
                (str(channel_id), int(message_id)),
            )
            row = x.fetchone()
    return (row["title"] or "NOT YOUR VIBE") if row else "NOT YOUR VIBE"


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
                        "↗️ SHARE",
                    "url":
                        f"https://t.me/share/url?url={quote(track_share_url(ch, msg), safe='')}&text={quote('🎵 ' + track_share_title(ch, msg) + ' • NOT YOUR VIBE', safe='')}",
                },
                {
                    "text":
                        "👤 PROFILE",
                    "callback_data":
                        "profile",
                },
            ],
            [
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

    # Reserve before sending in Radio. The per-user scheduler lock normally
    # prevents overlap, but reserving first also protects against duplicate
    # Radio jobs running in different workers/processes.
    if radio:
        reserved = reserve(
            uid,
            (selected_mood, msg, channel),
            no_repeat_today=True,
        )
        for _ in range(4):
            if reserved:
                break
            retry_track = radio_track(uid, baseline_mood=mood)
            if not retry_track:
                break
            selected_mood, msg, channel, track_title = retry_track
            reserved = reserve(
                uid,
                (selected_mood, msg, channel),
                no_repeat_today=True,
            )
        if not reserved:
            return send(chat, "⚠️ No suitable track found.", mood_menu())
    result = copy_music(chat, channel, msg)
    if radio and not result.get("ok"):
        # A DB row can outlive its Telegram message. Mark it unavailable and
        # immediately choose another track instead of stopping Radio.
        mark_delivery_failed(uid, track)
        for _ in range(4):
            retry_track = radio_track(uid, baseline_mood=mood)
            if not retry_track:
                break
            retry_mood, retry_msg, retry_channel, retry_title = retry_track
            retry_reserved = reserve(
                uid,
                (retry_mood, retry_msg, retry_channel),
                no_repeat_today=True,
            )
            if not retry_reserved:
                continue
            retry_result = copy_music(chat, retry_channel, retry_msg)
            if retry_result.get("ok"):
                track = retry_track
                selected_mood, msg, channel, track_title = (
                    retry_mood, retry_msg, retry_channel, retry_title
                )
                result = retry_result
                break
            mark_delivery_failed(uid, retry_track)

    if not result.get("ok"):
        log.warning(
            "copy music failed uid=%s channel=%s msg=%s result=%s",
            uid, channel, msg, result,
        )
        return send(chat, "⚠️ This track could not be delivered.", mood_menu())

    if not radio:
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
        "👤 YOUR VIBE\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"{name}\n"
        f"{username}\n\n"
        f"{INFO[mood][0] if mood else '🎧'}  "
        f"{INFO[mood][1] if mood else 'Your current vibe'}\n"
        f"📻 Radio: {radio_status}\n\n"
        f"❤️ {likes} liked\n"
        f"😴 {nots} skipped\n"
        f"🎵 {served} tracks discovered\n\n"
        f"🏆 Top vibe  {INFO[fav][0]}\n"
        f"🥈 Next vibe  {INFO[second][0]}"
    )


def public_profile_text(profile_uid):

    with db() as c:
        with cur(c) as x:
            x.execute(
                """
                SELECT username, first_name, last_name
                FROM users
                WHERE user_id=%s
                """,
                (profile_uid,),
            )
            u = x.fetchone()

            if not u:
                return "👤 PROFILE\n\nProfile not found."

            x.execute(
                """
                SELECT COUNT(*) n
                FROM track_feedback
                WHERE user_id=%s AND feedback='like'
                """,
                (profile_uid,),
            )
            likes = int(x.fetchone()["n"])

            x.execute(
                """
                SELECT COUNT(*) n
                FROM user_history
                WHERE user_id=%s AND action='served'
                """,
                (profile_uid,),
            )
            served = int(x.fetchone()["n"])

    r = ratios(profile_uid)
    ranked = sorted(MOODS, key=lambda m: (r[m]["like"], r[m]["like"] - r[m]["not"]), reverse=True)
    name = " ".join(v for v in (u.get("first_name") or "", u.get("last_name") or "") if v).strip() or "Vibe Listener"
    username = f"@{u['username']}" if u.get("username") else "Vibe Listener"

    return (
        "👤 NOT YOUR VIBE\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"{name}\n"
        f"{username}\n\n"
        f"🏆 {INFO[ranked[0]][0]}\n"
        f"🥈 {INFO[ranked[1]][0]}\n\n"
        f"❤️ {likes} liked  •  🎵 {served} discovered\n\n"
        "A personal taste profile from NOT YOUR VIBE."
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
                comments_keyboard() or admin_panel(),
            )

            return

        if action == "listeners":

            send(
                chat,
                listener_top5_text(),
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
    # COMMENT REPLY (ADMIN)
    # =====================================================

    if data.startswith("cr:"):
        if str(uid) != ADMIN_USER_ID:
            answer(callback_id, "Admin only")
            return
        try:
            comment_id=int(data.split(":",1)[1])
        except Exception:
            return
        with db() as dbc:
            with cur(dbc) as x:
                x.execute("SELECT 1 FROM broadcast_comments WHERE id=%s", (comment_id,))
                if not x.fetchone():
                    answer(callback_id, "Comment not found")
                    return
        set_pending_comment_reply(uid, comment_id)
        answer(callback_id, "Send your reply")
        send(chat, f"↩️ Reply to comment #{comment_id} below 👇", {"force_reply":True,"input_field_placeholder":"Write a reply..."})
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
                                "↗️ SHARE PROFILE",
                            "url":
                                f"https://t.me/share/url?url={quote(f'https://t.me/{BOT_USERNAME}?start=profile_{uid}', safe='')}&text={quote('👤 My NOT YOUR VIBE profile', safe='')}",
                        }
                    ],
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

    raw_text = (m.get("text") or "").strip()
    cmd = command(raw_text)

    # =====================================================
    # TELEGRAM SHARE DEEP LINKS
    # =====================================================

    if cmd == "/start" and " " in raw_text:

        payload = raw_text.split(" ", 1)[1].strip()

        if payload.startswith("track_"):
            try:
                track_id = int(payload.split("_", 1)[1])
            except Exception:
                track_id = 0

            if track_id:
                play_selected_track(
                    chat,
                    uid,
                    track_id,
                    header="▶️ SHARED TRACK",
                )
                return

        if payload.startswith("profile_"):
            try:
                profile_uid = int(payload.split("_", 1)[1])
            except Exception:
                profile_uid = 0

            if profile_uid:
                send(
                    chat,
                    public_profile_text(profile_uid),
                    {
                        "inline_keyboard": [
                            [{
                                "text": "↗️ SHARE PROFILE",
                                "url": f"https://t.me/share/url?url={quote(f'https://t.me/{BOT_USERNAME}?start=profile_{profile_uid}', safe='')}&text={quote('👤 My NOT YOUR VIBE profile', safe='')}",
                            }],
                            [{
                                "text": "🎛 CHANGE MOOD",
                                "callback_data": "change_mood",
                            }, {
                                "text": "📻 RADIO",
                                "callback_data": "radio",
                            }],
                        ]
                    },
                )
                return

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
    # COMMENT REPLY
    # =====================================================

    pending_reply = get_pending_comment_reply(uid) if str(uid) == ADMIN_USER_ID else None
    if pending_reply and (m.get("text") or "").strip() and not (m.get("text") or "").strip().startswith("/"):
        reply_text=(m.get("text") or "").strip()[:2000]
        row=send_comment_reply(uid, pending_reply, reply_text)
        if not row:
            send(chat, "❌ Comment not found.", admin_panel())
            return
        target_user=int(row["user_id"])
        send(target_user, "↩️ REPLY FROM NOT YOUR VIBE\n━━━━━━━━━━━━━━━━━━\n\n" f"Your comment: {str(row['comment'])[:500]}\n\n" f"{reply_text}")
        send(chat, f"✅ Reply sent to {target_user}.", comments_keyboard() or admin_panel())
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
            comments_keyboard() or admin_panel(),
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

def claim_update(update_id):
    """Process each Telegram webhook update at most once."""
    try:
        with db() as c:
            with cur(c) as x:
                x.execute(
                    """
                    INSERT INTO processed_updates(update_id, processed_at)
                    VALUES(%s,%s)
                    ON CONFLICT(update_id) DO NOTHING
                    """,
                    (int(update_id), int(time.time())),
                )
                return x.rowcount == 1
    except Exception:
        # Do not drop a real update when an old database has not migrated yet;
        # init_db normally creates this table during startup.
        log.exception("could not claim Telegram update_id=%s", update_id)
        return True

def update(u):

    update_id = u.get("update_id") if isinstance(u, Mapping) else None
    if update_id is not None and not claim_update(update_id):
        log.info("ignored duplicate Telegram update_id=%s", update_id)
        return

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


def share_page(title, description, bot_link, image_url=None):
    image_meta = (
        f'<meta property="og:image" content="{escape(image_url)}">'
        f'<meta name="twitter:image" content="{escape(image_url)}">'
        if image_url else ""
    )
    return f"""<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{escape(title)}</title><meta property=\"og:title\" content=\"{escape(title)}\"><meta property=\"og:description\" content=\"{escape(description)}\">{image_meta}<meta name=\"twitter:card\" content=\"summary_large_image\"><meta name=\"twitter:title\" content=\"{escape(title)}\"><meta name=\"twitter:description\" content=\"{escape(description)}\"><style>body{{margin:0;background:#0b0b0f;color:#fff;font-family:system-ui,-apple-system,sans-serif}}main{{max-width:560px;margin:8vh auto;padding:24px}}.card{{border:1px solid #292933;border-radius:24px;padding:30px;text-align:center;background:#121218}}h1{{font-size:28px;margin:8px 0 12px}}p{{color:#b7b7c2;line-height:1.5}}a{{display:inline-block;margin-top:16px;padding:12px 20px;border-radius:999px;background:#fff;color:#111;text-decoration:none;font-weight:700}}</style></head><body><main><div class=\"card\"><div>🎧</div><h1>{escape(title)}</h1><p>{escape(description)}</p><a href=\"{escape(bot_link)}\">Open in Telegram</a></div></main></body></html>"""


_SHARE_COVER_CACHE = {}
_SHARE_COVER_LOCK = threading.Lock()
SHARE_COVER_FAILURE_BACKOFF = 300

@app.route("/share/track/<int:track_id>/cover")
def share_track_cover(track_id):
    row = get_track(track_id)
    if not row or client is None or tele_loop is None or not ready.is_set():
        return ("", 404)
    try:
        now = time.time()
        with _SHARE_COVER_LOCK:
            cached = _SHARE_COVER_CACHE.get(int(track_id))
        if cached:
            data, content_type, cached_at = cached
            if data:
                return Response(
                    data,
                    mimetype=content_type,
                    headers={"Cache-Control": "public, max-age=86400"},
                )
            if now - cached_at < SHARE_COVER_FAILURE_BACKOFF:
                return ("", 404)

        async def fetch_thumb():
            message = await client.get_messages(int(row["channel_id"]), ids=int(row["message_id"]))
            if not message or not message.media:
                return None
            buf = io.BytesIO()
            result = await message.download_media(file=buf, thumb=-1)
            if not result:
                return None
            return buf.getvalue()

        future = asyncio.run_coroutine_threadsafe(fetch_thumb(), tele_loop)
        data = future.result(timeout=8)
        if not data:
            with _SHARE_COVER_LOCK:
                _SHARE_COVER_CACHE[int(track_id)] = (None, "image/jpeg", now)
            return ("", 404)
        if data.startswith(b"\x89PNG"):
            content_type = "image/png"
        elif data.startswith(b"RIFF") and b"WEBP" in data[:16]:
            content_type = "image/webp"
        else:
            content_type = "image/jpeg"
        with _SHARE_COVER_LOCK:
            _SHARE_COVER_CACHE[int(track_id)] = (data, content_type, now)
        return Response(data, mimetype=content_type, headers={"Cache-Control": "public, max-age=86400"})
    except Exception:
        with _SHARE_COVER_LOCK:
            _SHARE_COVER_CACHE[int(track_id)] = (None, "image/jpeg", time.time())
        log.warning("share cover unavailable track=%s; using cover backoff", track_id)
        return ("", 404)

@app.route("/share/track/<int:track_id>")
def share_track_page(track_id):
    row = get_track(track_id)
    if not row:
        return share_page("NOT YOUR VIBE", "This track is no longer available.", f"https://t.me/{BOT_USERNAME}")
    title = str(row.get("title") or f"Track #{row['message_id']}")[:160]
    base = RENDER_EXTERNAL_URL.rstrip("/")
    image_url = f"{base}/share/track/{track_id}/cover" if base else None
    return share_page(f"{title} • NOT YOUR VIBE", "🎧 Listen on NOT YOUR VIBE • Discover your vibe.", f"https://t.me/{BOT_USERNAME}?start=track_{track_id}", image_url=image_url)

@app.route("/share/profile/<int:profile_uid>")
def share_profile_page(profile_uid):
    text = public_profile_text(profile_uid)
    if "Profile not found" in text:
        return share_page("NOT YOUR VIBE", "Profile not found.", f"https://t.me/{BOT_USERNAME}")
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    name = lines[2] if len(lines) > 2 else "Vibe Listener"
    return share_page(f"{name} • NOT YOUR VIBE", "A personal EDM taste profile.", f"https://t.me/{BOT_USERNAME}?start=profile_{profile_uid}")

# =========================================================
# TELEGRAM MINI APP
# =========================================================

MINI_APP_MAX_AGE = 86400
MINI_AUDIO_CACHE_ITEMS = geti("MINI_AUDIO_CACHE_ITEMS", 4, 1, 12)
_MINI_AUDIO_CACHE = {}
_MINI_AUDIO_CACHE_LOCK = threading.Lock()
_MINI_AUDIO_FILE_LOCKS = {}
_MINI_AUDIO_FILE_LOCKS_GUARD = threading.Lock()


def _mini_app_user():
    """Validate Telegram WebApp initData and return the Telegram user."""
    init_data = request.headers.get("X-Telegram-Init-Data", "") or request.args.get("initData", "")
    if not init_data or not BOT_TOKEN:
        return None, ("Telegram authentication required", 401)

    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", "")
        auth_date = int(pairs.get("auth_date", "0"))
        if not received_hash or not auth_date:
            return None, ("Invalid Telegram initData", 401)
        if abs(int(time.time()) - auth_date) > MINI_APP_MAX_AGE:
            return None, ("Telegram initData expired", 401)

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated, received_hash):
            return None, ("Invalid Telegram initData", 401)

        user = json.loads(pairs.get("user", "{}"))
        uid = int(user.get("id"))
        return user, None
    except Exception:
        return None, ("Invalid Telegram initData", 401)


def _mini_track(row):
    if not row:
        return None
    d = dict(row)
    d["id"] = int(d["id"])
    d["channel_id"] = str(d["channel_id"])
    d["message_id"] = int(d["message_id"])
    if "musical_key" in d:
        d["key"] = d.pop("musical_key")
    d["cover_url"] = f"/share/track/{d['id']}/cover"
    return d


def _mini_pick_row(uid, mood=None, liked_only=False):
    with db() as c:
        with cur(c) as x:
            if liked_only:
                x.execute("""
                    SELECT t.id,t.mood,t.channel_id,t.message_id,t.title,
                           t.bpm,t.musical_key,t.energy,t.danceability,t.loudness,
                           t.genre,t.subgenre,t.analyzer_mood,t.analyzed
                    FROM tracks t
                    JOIN track_feedback f ON f.channel_id=t.channel_id AND f.message_id=t.message_id
                    WHERE f.user_id=%s AND f.feedback='like'
                    ORDER BY f.created_at DESC, t.id DESC
                    LIMIT 1
                """, (uid,))
            elif mood:
                x.execute("""
                    SELECT id,mood,channel_id,message_id,title,bpm,musical_key,
                           energy,danceability,loudness,genre,subgenre,analyzer_mood,analyzed
                    FROM tracks WHERE mood=%s ORDER BY RANDOM() LIMIT 1
                """, (mood,))
            else:
                x.execute("""
                    SELECT id,mood,channel_id,message_id,title,bpm,musical_key,
                           energy,danceability,loudness,genre,subgenre,analyzer_mood,analyzed
                    FROM tracks ORDER BY RANDOM() LIMIT 1
                """)
            return x.fetchone()


def _mini_radio_row(uid, baseline_mood=None):
    """Return the exact track selected by the bot Radio engine."""
    picked = radio_track(uid, baseline_mood=baseline_mood)
    if not picked:
        return None
    _, message_id, channel_id, _ = picked
    with db() as c:
        with cur(c) as x:
            x.execute("""
                SELECT id,mood,channel_id,message_id,title,bpm,musical_key,
                       energy,danceability,loudness,genre,subgenre,
                       analyzer_mood,analyzed
                FROM tracks
                WHERE channel_id=%s AND message_id=%s
                LIMIT 1
            """, (str(channel_id), int(message_id)))
            return x.fetchone()


@app.route("/mini-app")
def mini_app():
    response = make_response(render_template("index.html"))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/api/me")
def mini_me():
    user, err = _mini_app_user()
    if err:
        return jsonify({"error": err[0]}), err[1]
    uid = int(user["id"])
    return jsonify({
        "user": {
            "id": uid,
            "first_name": user.get("first_name", ""),
            "last_name": user.get("last_name", ""),
            "username": user.get("username", ""),
        }
    })


@app.route("/api/home")
def mini_home():
    user, err = _mini_app_user()
    if err:
        return jsonify({"error": err[0]}), err[1]
    uid = int(user["id"])
    try:
        state = get_state(uid)
        requested_mood = (request.args.get("mood") or "").strip().lower()
        if requested_mood in MOODS:
            set_mood(uid, requested_mood)
            state = get_state(uid)
        mood = state.get("mood")
        liked_rows = []
        with db() as c:
            with cur(c) as x:
                x.execute("""
                    SELECT t.id,t.mood,t.channel_id,t.message_id,t.title,
                           t.bpm,t.musical_key,t.energy,t.danceability,t.loudness,
                           t.genre,t.subgenre,t.analyzer_mood,t.analyzed
                    FROM tracks t
                    JOIN track_feedback f ON f.channel_id=t.channel_id AND f.message_id=t.message_id
                    WHERE f.user_id=%s AND f.feedback='like'
                    ORDER BY f.created_at DESC,t.id DESC LIMIT 30
                """, (uid,))
                liked_rows = x.fetchall()

        daily = _mini_pick_row(uid, mood=mood) if mood else _mini_pick_row(uid)
        for_you = _mini_pick_row(uid, liked_only=True)
        # Keep initial Home fast: exact bot Radio selection is done by /api/action
        # when the user opens/plays Radio. A lightweight preview avoids blocking
        # the first screen on the full continuity/scoring query.
        radio = _mini_pick_row(uid, mood=mood) if mood else _mini_pick_row(uid)
        # Keep Track of the Day deterministic per user/day.
        day = datetime.now(ZoneInfo("Asia/Yangon")).date().isoformat()
        with db() as c:
            with cur(c) as x:
                x.execute("""
                    SELECT id,mood,channel_id,message_id,title,bpm,musical_key,
                           energy,danceability,loudness,genre,subgenre,analyzer_mood,analyzed
                    FROM tracks ORDER BY md5(id::text || %s) LIMIT 1
                """, (f"{uid}:{day}",))
                totd = x.fetchone()

        return jsonify({
            "user": {"id": uid, "first_name": user.get("first_name", "")},
            "state": state,
            "liked": [_mini_track(r) for r in liked_rows],
            "recommendations": {
                "daily_vibe": _mini_track(daily),
                "for_you": _mini_track(for_you),
                "radio": _mini_track(radio),
                "track_of_day": _mini_track(totd),
            }
        })
    except Exception as e:
        log.exception("Mini App home failed")
        return jsonify({"error": "Mini App data unavailable"}), 500


def _mini_row_from_pick(picked):
    if not picked:
        return None
    _, message_id, channel_id = picked[:3]
    with db() as c:
        with cur(c) as x:
            x.execute("""
                SELECT id,mood,channel_id,message_id,title,bpm,musical_key,
                       energy,danceability,loudness,genre,subgenre,
                       analyzer_mood,analyzed
                FROM tracks
                WHERE channel_id=%s AND message_id=%s
                LIMIT 1
            """, (str(channel_id), int(message_id)))
            return x.fetchone()


@app.route("/api/action", methods=["POST"])
def mini_action():
    user, err = _mini_app_user()
    if err:
        return jsonify({"error": err[0]}), err[1]
    uid = int(user["id"])
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip().lower()
    requested_mood = str(payload.get("mood") or "").strip().lower()
    if requested_mood in MOODS:
        set_mood(uid, requested_mood)
    state = get_state(uid)
    mood = state.get("mood") or "melodic"
    if action == "mood":
        if requested_mood not in MOODS:
            return jsonify({"error": "Invalid mood"}), 400
        return jsonify({"ok": True, "state": get_state(uid)})
    if action == "radio":
        set_radio(uid, True)
        state = get_state(uid)
        picked = radio_track(uid, baseline_mood=mood)
    elif action == "next":
        picked = radio_track(uid, baseline_mood=mood) if state.get("radio") else normal_track(uid, mood)
    elif action == "daily_vibe":
        set_special_mode(uid, "daily_vibe")
        picked = daily_vibe_track(uid)
    elif action == "for_you":
        set_special_mode(uid, "for_you")
        picked = for_you_track(uid)
    elif action == "surprise_me":
        set_special_mode(uid, "surprise_me")
        picked = surprise_track(uid)
    elif action == "track_of_day":
        set_special_mode(uid, "track_of_day")
        picked = track_of_day(uid)
    else:
        return jsonify({"error": "Unknown bot action"}), 400
    if not picked:
        return jsonify({"error": "No suitable track found", "state": get_state(uid)}), 404
    selected = (picked[0], picked[1], picked[2])
    radio_mode = action == "radio" or state.get("radio")
    reserved = reserve(uid, selected, no_repeat_today=(action in ("radio", "next") and radio_mode))
    if not reserved and action in ("radio", "next"):
        for _ in range(3):
            picked = radio_track(uid, baseline_mood=mood) if radio_mode else normal_track(uid, mood)
            if not picked:
                break
            selected = (picked[0], picked[1], picked[2])
            reserved = reserve(uid, selected, no_repeat_today=(action in ("radio", "next") and radio_mode))
            if reserved:
                break
    row = _mini_row_from_pick(picked)
    if not row:
        return jsonify({"error": "Selected track is unavailable"}), 404
    return jsonify({"ok": True, "action": action, "state": get_state(uid), "track": _mini_track(row)})


@app.route("/api/discover")
def mini_discover():
    user, err = _mini_app_user()
    if err:
        return jsonify({"error": err[0]}), err[1]
    try:
        trending = trending_rows(10)
        top_liked = top_liked_tracks(10)
        surprise = _mini_pick_row(int(user["id"]))
        return jsonify({
            "trending": [_mini_track(row) for row in trending],
            "top_liked": [_mini_track(row) for row in top_liked],
            "surprise": _mini_track(surprise),
        })
    except Exception:
        log.exception("Mini App discover failed")
        return jsonify({"error": "Discover data unavailable"}), 500


@app.route("/api/track/<int:track_id>")
def mini_track(track_id):
    user, err = _mini_app_user()
    if err:
        return jsonify({"error": err[0]}), err[1]
    row = get_track(track_id)
    if not row:
        return jsonify({"error": "Track not found"}), 404
    return jsonify(_mini_track(row))


@app.route("/api/track/<int:track_id>/audio")
def mini_track_audio(track_id):
    """Serve Telegram audio as a normal seekable file.

    Some Telegram in-app WebViews are unreliable with chunked/live responses
    that have no Content-Length or real Range handling. We cache the downloaded
    Telegram file on Render's ephemeral /tmp disk and let Flask send_file handle
    Content-Length, Range/206 and the correct MIME type.
    """
    user, err = _mini_app_user()
    if err:
        return jsonify({"error": err[0]}), err[1]

    row = get_track(track_id)
    if not row:
        return jsonify({"error": "Track not found"}), 404
    if client is None or tele_loop is None or not ready.is_set():
        return jsonify({"error": "Telegram audio service is not ready"}), 503

    import os
    from pathlib import Path

    cache_dir = Path(os.getenv("MINI_AUDIO_CACHE_DIR", "/tmp/nyv_mini_audio"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    final_path = cache_dir / f"{int(track_id)}.audio"
    mime_path = cache_dir / f"{int(track_id)}.mime"

    # Reuse a completed cached file.
    if final_path.is_file() and final_path.stat().st_size > 0:
        mime = "audio/mpeg"
        try:
            saved = mime_path.read_text().strip()
            if saved:
                mime = saved
        except Exception:
            pass
        log.info("Mini App audio cache HIT track=%s size=%s mime=%s", track_id, final_path.stat().st_size, mime)
        return send_file(
            final_path,
            mimetype=mime,
            as_attachment=False,
            conditional=True,
            etag=True,
            max_age=1800,
            download_name=f"track-{int(track_id)}.audio",
        )

    # Only one request should populate a given track cache at a time.
    with _MINI_AUDIO_FILE_LOCKS_GUARD:
        lock = _MINI_AUDIO_FILE_LOCKS.get(int(track_id))
        if lock is None:
            lock = threading.Lock()
            _MINI_AUDIO_FILE_LOCKS[int(track_id)] = lock

    with lock:
        if final_path.is_file() and final_path.stat().st_size > 0:
            mime = "audio/mpeg"
            try:
                saved = mime_path.read_text().strip()
                if saved:
                    mime = saved
            except Exception:
                pass
            return send_file(
                final_path,
                mimetype=mime,
                as_attachment=False,
                conditional=True,
                etag=True,
                max_age=1800,
                download_name=f"track-{int(track_id)}.audio",
            )

        partial = cache_dir / f"{int(track_id)}.part"
        try:
            if partial.exists():
                partial.unlink()
        except Exception:
            pass

        async def download_full():
            message = await client.get_messages(int(row["channel_id"]), ids=int(row["message_id"]))
            if not message or not message.media:
                raise RuntimeError("Telegram media not found")

            obj = getattr(message, "audio", None) or getattr(message, "document", None)
            mime_value = getattr(obj, "mime_type", None) if obj else None
            mime = str(mime_value).lower().strip() if mime_value and str(mime_value).lower().strip().startswith("audio/") else "audio/mpeg"
            # Normalize common Android/WebView aliases to standards-based MIME types.
            if mime in ("audio/m4a", "audio/x-m4a"):
                mime = "audio/mp4"

            with open(partial, "wb") as fh:
                async for chunk in client.iter_download(message.media, request_size=1024 * 1024):
                    if chunk:
                        fh.write(chunk)

            if not partial.is_file() or partial.stat().st_size <= 0:
                raise RuntimeError("Telegram audio download returned no data")
            os.replace(partial, final_path)
            mime_path.write_text(mime)
            return mime

        try:
            future = asyncio.run_coroutine_threadsafe(download_full(), tele_loop)
            mime = future.result(timeout=180)
        except Exception as exc:
            log.exception("Mini App audio download failed track=%s", track_id)
            try:
                if partial.exists():
                    partial.unlink()
            except Exception:
                pass
            return jsonify({"error": "Audio file unavailable", "detail": str(exc)[:180]}), 502

        # Keep the disk cache bounded. Remove the oldest completed files first.
        try:
            files = sorted(
                [p for p in cache_dir.glob("*.audio") if p.is_file()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            limit = MINI_AUDIO_CACHE_ITEMS
            for old in files[limit:]:
                try:
                    old.unlink()
                    old_mime = old.with_suffix(".mime")
                    if old_mime.exists():
                        old_mime.unlink()
                except Exception:
                    pass
        except Exception:
            log.debug("Mini App audio cache cleanup failed", exc_info=True)

        log.info("Mini App audio cache MISS->SAVED track=%s size=%s mime=%s", track_id, final_path.stat().st_size, mime)
        return send_file(
            final_path,
            mimetype=mime,
            as_attachment=False,
            conditional=True,
            etag=True,
            max_age=1800,
            download_name=f"track-{int(track_id)}.audio",
        )

@app.route("/api/track/<int:track_id>/feedback", methods=["POST"])
def mini_feedback(track_id):
    user, err = _mini_app_user()
    if err:
        return jsonify({"error": err[0]}), err[1]
    row = get_track(track_id)
    if not row:
        return jsonify({"error": "Track not found"}), 404
    payload = request.get_json(silent=True) or {}
    fb = payload.get("feedback")
    ok = save_feedback(int(user["id"]), row["channel_id"], row["message_id"], row["mood"], fb)
    if not ok:
        return jsonify({"error": "Invalid feedback"}), 400
    return jsonify({"ok": True, "feedback": fb})


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

        "mini_app":
            MINI_APP_URL or None,

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


async def periodic_scan():

    while True:

        await asyncio.sleep(SCAN_INTERVAL)

        if not ready.is_set() or client is None:
            continue

        try:

            log.info(
                "periodic channel rescan starting"
            )

            await scan_all()

            log.info(
                "periodic channel rescan complete"
            )

        except asyncio.CancelledError:

            raise

        except Exception:

            log.exception(
                "periodic channel rescan"
            )


# =========================================================
# INTEGRATED AUDIO ANALYZER
# =========================================================

def _run_analyzer_worker(shared_client, shared_loop):
    from audio_analyzer import run_integrated_worker
    while not analyzer_stop_event.is_set():
        try:
            run_integrated_worker(
                shared_client,
                shared_loop,
                ANALYZER_LIMIT,
                ANALYZER_WATCH_INTERVAL,
                analyzer_stop_event,
            )
            return
        except Exception:
            log.exception("integrated analyzer stopped unexpectedly; retrying")
            analyzer_stop_event.wait(min(30, ANALYZER_WATCH_INTERVAL))


def start_analyzer():
    global analyzer_thread
    if not ANALYZER_ENABLED or client is None or tele_loop is None:
        return
    with analyzer_lock:
        if analyzer_thread and analyzer_thread.is_alive():
            return
        analyzer_stop_event.clear()
        analyzer_thread = threading.Thread(target=_run_analyzer_worker, args=(client, tele_loop), name="audio-analyzer-worker", daemon=True)
        analyzer_thread.start()
        log.info("integrated audio analyzer started")


def stop_analyzer():
    """Stop the single analyzer worker and wait for it to exit cleanly."""
    global analyzer_thread
    analyzer_stop_event.set()
    with analyzer_lock:
        thread = analyzer_thread
    if thread and thread is not threading.current_thread():
        thread.join()
    with analyzer_lock:
        if analyzer_thread is thread and (not thread or not thread.is_alive()):
            analyzer_thread = None


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

        rescan_task = asyncio.create_task(
            periodic_scan()
        )

        try:

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
                    start_analyzer()

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

        finally:

            rescan_task.cancel()

            with contextlib.suppress(asyncio.CancelledError):
                await rescan_task

            stop_analyzer()

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
# MINI APP TELEGRAM MENU
# =========================================================

def configure_mini_app():
    """
    Configure the bot's private-chat menu button to launch the Mini App.
    The same setting can also be configured manually through @BotFather.
    """
    if not BOT_TOKEN or not MINI_APP_URL:
        log.warning("Mini App menu not configured: BOT_TOKEN or MINI_APP_URL missing")
        return False

    if not MINI_APP_URL.startswith("https://"):
        log.error("Mini App URL must be HTTPS for Telegram: %s", MINI_APP_URL)
        return False

    result = tg(
        "setChatMenuButton",
        {
            "menu_button": {
                "type": "web_app",
                "text": "🎧 NOT YOUR VIBE",
                "web_app": {"url": MINI_APP_URL},
            }
        },
        15,
    )

    if result.get("ok"):
        log.info("Mini App menu button configured: %s", MINI_APP_URL)
    else:
        log.warning(
            "Mini App menu button configuration failed: %s",
            result.get("description"),
        )

    return bool(result.get("ok"))


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
    configure_mini_app()

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
