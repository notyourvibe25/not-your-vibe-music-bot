 import os
import time
import random
import sqlite3
import threading
import asyncio
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask

from telethon import TelegramClient
from telethon.sessions import StringSession


# ============================================================
# APP
# ============================================================

app = Flask(__name__)


# ============================================================
# ENV
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "").strip()

TELETHON_API_ID = (
    os.getenv("TELETHON_API_ID")
    or os.getenv("API_ID")
    or ""
).strip()

TELETHON_API_HASH = (
    os.getenv("TELETHON_API_HASH")
    or os.getenv("API_HASH")
    or ""
).strip()

TELETHON_SESSION = os.getenv(
    "TELETHON_SESSION",
    ""
).strip()

RENDER_EXTERNAL_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    ""
).strip()


# ============================================================
# RENDER FREE DATABASE
# ============================================================
#
# /data မသုံးပါ။
# Render ရဲ့ writable application directory ကိုသုံးမယ်။
#
# Restart ဖြစ်ရင် SQLite ပျောက်နိုင်ပေမယ့်
# Telethon က channel history ကို ပြန် scan လုပ်နိုင်တယ်။
#
# ============================================================

DB_PATH = os.getenv(
    "DB_PATH",
    "music_bot.db"
).strip()

if not DB_PATH:
    DB_PATH = "music_bot.db"


# ============================================================
# MOODS
# ============================================================

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

MOODS = [
    "sad",
    "love",
    "chill",
    "hype",
    "dark",
    "energetic",
    "night",
    "melodic",
]


# ============================================================
# CHANNELS
# ============================================================

MOOD_CHANNELS = {
    "sad": os.getenv("SAD_CHANNEL", "").strip(),
    "love": os.getenv("LOVE_CHANNEL", "").strip(),
    "chill": os.getenv("CHILL_CHANNEL", "").strip(),
    "hype": os.getenv("HYPE_CHANNEL", "").strip(),
    "dark": os.getenv("DARK_CHANNEL", "").strip(),
    "energetic": os.getenv("ENERGETIC_CHANNEL", "").strip(),
    "night": os.getenv("NIGHT_CHANNEL", "").strip(),
    "melodic": os.getenv("MELODIC_CHANNEL", "").strip(),
}


# ============================================================
# HTTP
# ============================================================

http = requests.Session()

http.headers.update({
    "User-Agent": "NOT-YOUR-VIBE-MUSIC-BOT/4.0"
})


# ============================================================
# LOCKS
# ============================================================

db_init_lock = threading.Lock()

user_locks = {}
user_locks_lock = threading.Lock()

telegram_lock = threading.Lock()


# ============================================================
# EXECUTOR
# ============================================================

executor = ThreadPoolExecutor(
    max_workers=12,
    thread_name_prefix="music-worker"
)


# ============================================================
# TELETHON
# ============================================================

telethon_client = None

telethon_ready = threading.Event()

telethon_loop = None


# ============================================================
# DATABASE
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False,
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA busy_timeout=30000"
    )

    conn.execute(
        "PRAGMA journal_mode=WAL"
    )

    conn.execute(
        "PRAGMA synchronous=NORMAL"
    )

    return conn


def db_execute(
    query,
    params=(),
    fetchone=False,
    fetchall=False,
    commit=False,
    retries=5,
):

    last_error = None

    for attempt in range(retries):

        conn = None

        try:

            conn = get_db()

            cursor = conn.execute(
                query,
                params
            )

            if commit:
                conn.commit()

            if fetchone:
                return cursor.fetchone()

            if fetchall:
                return cursor.fetchall()

            return None

        except sqlite3.OperationalError as exc:

            last_error = exc

            if "locked" in str(exc).lower():
                time.sleep(
                    0.15 * (attempt + 1)
                )
                continue

            raise

        finally:

            if conn:
                conn.close()

    raise last_error


def init_db():

    with db_init_lock:

        conn = get_db()

        try:

            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    first_seen INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL,
                    total_requests INTEGER NOT NULL DEFAULT 0
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mood TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(channel_id, message_id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    mood TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    sent_at INTEGER NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_state (
                    user_id INTEGER PRIMARY KEY,
                    mood TEXT,
                    updated_at INTEGER NOT NULL
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_tracks_mood
                ON tracks(mood)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_tracks_mood_id
                ON tracks(mood, id)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_history_user
                ON user_history(user_id, sent_at DESC)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_history_user_mood
                ON user_history(user_id, mood, sent_at DESC)
            """)

            conn.commit()

        finally:

            conn.close()


# ============================================================
# TELEGRAM API
# ============================================================

def telegram(
    method,
    data=None,
    timeout=20,
    retries=3,
):

    if not BOT_TOKEN:

        print("❌ BOT_TOKEN missing")

        return {
            "ok": False,
            "description": "BOT_TOKEN missing"
        }

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    last_result = {
        "ok": False,
        "description": "unknown error"
    }

    for attempt in range(retries):

        try:

            response = http.post(
                url,
                json=data or {},
                timeout=timeout,
            )

            try:
                result = response.json()

            except Exception:

                result = {
                    "ok": False,
                    "description": response.text
                }

            last_result = result

            if result.get("ok"):
                return result

            error_code = result.get(
                "error_code",
                0
            )

            # Retry Telegram temporary errors
            if error_code in (
                429,
                500,
                502,
                503,
                504,
            ):

                retry_after = (
                    result.get("parameters", {})
                    .get("retry_after", 1)
                )

                time.sleep(
                    min(
                        int(retry_after),
                        5
                    )
                )

                continue

            print(
                "TELEGRAM API ERROR:",
                method,
                result
            )

            return result

        except requests.RequestException as exc:

            print(
                "TELEGRAM NETWORK ERROR:",
                method,
                repr(exc)
            )

            if attempt < retries - 1:

                time.sleep(
                    0.5 * (attempt + 1)
                )

    return last_result


# ============================================================
# SEND MESSAGE
# ============================================================

def send_message(
    chat_id,
    text,
    keyboard=None,
):

    data = {
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
        retries=3,
    )


# ============================================================
# CALLBACK
# ============================================================

def answer_callback(
    callback_id,
    text="",
):

    return telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text,
        },
        timeout=5,
        retries=2,
    )


# ============================================================
# COPY MUSIC
# ============================================================

def copy_music(
    chat_id,
    channel_id,
    message_id,
):

    return telegram(
        "copyMessage",
        {
            "chat_id": chat_id,
            "from_chat_id": channel_id,
            "message_id": message_id,
        },
        timeout=20,
        retries=3,
    )


# ============================================================
# USER LOCK
# ============================================================

def get_user_lock(user_id):

    with user_locks_lock:

        lock = user_locks.get(user_id)

        if lock is None:

            lock = threading.Lock()

            user_locks[user_id] = lock

        return lock


# ============================================================
# REGISTER USER
# ============================================================

def register_user(user):

    if not user:
        return

    user_id = user.get("id")

    if not user_id:
        return

    now = int(time.time())

    try:

        db_execute(
            """
            INSERT INTO users (
                user_id,
                username,
                first_name,
                last_name,
                first_seen,
                last_seen,
                total_requests
            )
            VALUES (?, ?, ?, ?, ?, ?, 1)

            ON CONFLICT(user_id)
            DO UPDATE SET

                username = excluded.username,

                first_name = excluded.first_name,

                last_name = excluded.last_name,

                last_seen = excluded.last_seen,

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
            commit=True,
        )

    except Exception as exc:

        print(
            "REGISTER USER ERROR:",
            repr(exc)
        )


# ============================================================
# SAVE USER MOOD
# ============================================================

def set_user_mood(
    user_id,
    mood,
):

    db_execute(
        """
        INSERT INTO user_state (
            user_id,
            mood,
            updated_at
        )
        VALUES (?, ?, ?)

        ON CONFLICT(user_id)
        DO UPDATE SET
            mood = excluded.mood,
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            mood,
            int(time.time()),
        ),
        commit=True,
    )


def get_user_mood(user_id):

    row = db_execute(
        """
        SELECT mood
        FROM user_state
        WHERE user_id = ?
        """,
        (user_id,),
        fetchone=True,
    )

    if row:
        return row["mood"]

    return None


# ============================================================
# SAVE TRACK
# ============================================================

def save_track(
    mood,
    channel_id,
    message_id,
):

    if mood not in MOODS:
        return

    try:

        db_execute(
            """
            INSERT OR IGNORE INTO tracks (
                mood,
                channel_id,
                message_id,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                mood,
                str(channel_id),
                int(message_id),
                int(time.time()),
            ),
            commit=True,
        )

    except Exception as exc:

        print(
            "SAVE TRACK ERROR:",
            repr(exc)
        )


# ============================================================
# TRACK COUNT
# ============================================================

track_count_cache = {}

track_count_lock = threading.Lock()


def get_track_count(mood):

    with track_count_lock:

        cached = track_count_cache.get(mood)

        if cached is not None:
            return cached

    try:

        row = db_execute(
            """
            SELECT COUNT(*) AS count
            FROM tracks
            WHERE mood = ?
            """,
            (mood,),
            fetchone=True,
        )

        count = int(row["count"])

        with track_count_lock:
            track_count_cache[mood] = count

        return count

    except Exception:

        return 0


def invalidate_track_count(mood):

    with track_count_lock:

        track_count_cache.pop(
            mood,
            None
        )


# ============================================================
# RANDOM TRACK
# ============================================================
#
# ORDER BY RANDOM() မသုံးဘူး။
# Track ID range ကနေ random ID ရွေးပြီး
# database lookup လုပ်မယ်။
#
# ============================================================

def get_random_track(
    user_id,
    mood,
):

    count = get_track_count(mood)

    if count <= 0:
        return None

    # Recent history
    recent_rows = db_execute(
        """
        SELECT message_id
        FROM user_history
        WHERE user_id = ?
          AND mood = ?
        ORDER BY id DESC
        LIMIT 40
        """,
        (
            user_id,
            mood,
        ),
        fetchall=True,
    )

    recent = {
        int(row["message_id"])
        for row in recent_rows
    }

    # Get ID range
    row = db_execute(
        """
        SELECT
            MIN(id) AS min_id,
            MAX(id) AS max_id
        FROM tracks
        WHERE mood = ?
        """,
        (mood,),
        fetchone=True,
    )

    if not row:
        return None

    min_id = row["min_id"]
    max_id = row["max_id"]

    if min_id is None or max_id is None:
        return None

    # Try random IDs
    for _ in range(12):

        random_id = random.randint(
            int(min_id),
            int(max_id)
        )

        track = db_execute(
            """
            SELECT
                id,
                channel_id,
                message_id
            FROM tracks
            WHERE mood = ?
              AND id >= ?
            ORDER BY id
            LIMIT 1
            """,
            (
                mood,
                random_id,
            ),
            fetchone=True,
        )

        if not track:
            continue

        message_id = int(
            track["message_id"]
        )

        if message_id not in recent:

            return (
                str(track["channel_id"]),
                message_id,
            )

    # Fallback
    track = db_execute(
        """
        SELECT
            channel_id,
            message_id
        FROM tracks
        WHERE mood = ?
        LIMIT 1
        """,
        (mood,),
        fetchone=True,
    )

    if not track:
        return None

    return (
        str(track["channel_id"]),
        int(track["message_id"]),
    )


# ============================================================
# SAVE HISTORY
# ============================================================

def save_history(
    user_id,
    mood,
    channel_id,
    message_id,
):

    db_execute(
        """
        INSERT INTO user_history (
            user_id,
            mood,
            channel_id,
            message_id,
            sent_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            user_id,
            mood,
            str(channel_id),
            int(message_id),
            int(time.time()),
        ),
        commit=True,
    )

    # Keep latest 100 per user
    db_execute(
        """
        DELETE FROM user_history
        WHERE user_id = ?
          AND id NOT IN (
              SELECT id
              FROM user_history
              WHERE user_id = ?
              ORDER BY id DESC
              LIMIT 100
          )
        """,
        (
            user_id,
            user_id,
        ),
        commit=True,
    )


# ============================================================
# REMOVE HISTORY
# ============================================================

def remove_history(
    user_id,
    message_id,
):

    try:

        db_execute(
            """
            DELETE FROM user_history
            WHERE id = (
                SELECT id
                FROM user_history
                WHERE user_id = ?
                  AND message_id = ?
                ORDER BY id DESC
                LIMIT 1
            )
            """,
            (
                user_id,
                int(message_id),
            ),
            commit=True,
        )

    except Exception as exc:

        print(
            "REMOVE HISTORY ERROR:",
            repr(exc)
        )


# ============================================================
# RESERVE TRACK
# ============================================================

def reserve_track(
    user_id,
    mood,
):

    lock = get_user_lock(user_id)

    with lock:

        track = get_random_track(
            user_id,
            mood
        )

        if not track:
            return None

        channel_id, message_id = track

        try:

            save_history(
                user_id,
                mood,
                channel_id,
                message_id,
            )

            return (
                channel_id,
                message_id,
            )

        except Exception as exc:

            print(
                "RESERVE ERROR:",
                repr(exc)
            )

            return None


# ============================================================
# SEND TRACK
# ============================================================

def send_mood_track(
    chat_id,
    user_id,
    mood,
):

    if mood not in MOODS:
        return

    count = get_track_count(mood)

    if count <= 0:

        send_message(
            chat_id,
            (
                f"{MOOD_NAMES[mood]}\n\n"
                "⚠️ ဒီ mood ထဲမှာ music မရှိသေးပါ။\n\n"
                "Channel ထဲက music history ကို "
                "Telethon က scan လုပ်ပြီး "
                "ပြန်ထည့်ပေးနိုင်ပါတယ်။"
            ),
            mood_menu(),
        )

        return

    attempted = set()

    for _ in range(6):

        reserved = reserve_track(
            user_id,
            mood
        )

        if not reserved:
            break

        channel_id, message_id = reserved

        if message_id in attempted:
            continue

        attempted.add(message_id)

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
                    "🎧 Enjoy your music! 🔥"
                ),
                music_buttons(),
            )

            return

        print(
            "COPY FAILED:",
            channel_id,
            message_id,
            result,
        )

        remove_history(
            user_id,
            message_id
        )

    send_message(
        chat_id,
        (
            f"{MOOD_NAMES[mood]}\n\n"
            "❌ ဒီ track ကို ပို့လို့မရသေးပါ။\n"
            "နောက်တစ်ကြိမ် Next နှိပ်ပြီး "
            "ပြန်စမ်းနိုင်ပါတယ်။"
        ),
        music_buttons(),
    )


# ============================================================
# BACKGROUND SEND
# ============================================================

def background_send(
    chat_id,
    user_id,
    mood,
):

    try:

        send_mood_track(
            chat_id,
            user_id,
            mood,
        )

    except Exception as exc:

        print(
            "BACKGROUND SEND ERROR:",
            repr(exc)
        )

        try:

            send_message(
                chat_id,
                "⚠️ ခဏအကြာ ပြန်စမ်းပေးပါ။"
            )

        except Exception:
            pass


# ============================================================
# KEYBOARDS
# ============================================================

def mood_menu():

    return {
        "inline_keyboard": [
            [
                {
                    "text": "😢 Sad",
                    "callback_data": "mood_sad",
                },
                {
                    "text": "❤️ Love",
                    "callback_data": "mood_love",
                },
            ],
            [
                {
                    "text": "🌙 Chill",
                    "callback_data": "mood_chill",
                },
                {
                    "text": "🔥 Hype",
                    "callback_data": "mood_hype",
                },
            ],
            [
                {
                    "text": "🖤 Dark",
                    "callback_data": "mood_dark",
                },
                {
                    "text": "⚡ Energetic",
                    "callback_data": "mood_energetic",
                },
            ],
            [
                {
                    "text": "🚗 Night Drive",
                    "callback_data": "mood_night",
                },
                {
                    "text": "🌌 Melodic",
                    "callback_data": "mood_melodic",
                },
            ],
        ]
    }


def music_buttons():

    return {
        "inline_keyboard": [
            [
                {
                    "text": "🔀 Next",
                    "callback_data": "next_music",
                }
            ],
            [
                {
                    "text": "🎧 Change Mood",
                    "callback_data": "change_mood",
                }
            ],
        ]
    }


# ============================================================
# STATS
# ============================================================

def get_users_count():

    row = db_execute(
        """
        SELECT COUNT(*) AS count
        FROM users
        """,
        fetchone=True,
    )

    return int(row["count"])


def get_track_counts():

    rows = db_execute(
        """
        SELECT mood, COUNT(*) AS count
        FROM tracks
        GROUP BY mood
        """,
        fetchall=True,
    )

    result = {
        mood: 0
        for mood in MOODS
    }

    for row in rows:

        if row["mood"] in result:

            result[row["mood"]] = int(
                row["count"]
            )

    return result


def is_admin(user_id):

    if not ADMIN_USER_ID:
        return False

    return str(user_id) == str(
        ADMIN_USER_ID
    )


def send_stats(chat_id):

    if not is_admin(chat_id):

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
        "📊 NOT YOUR VIBE",
        "",
        f"👥 Users: {users}",
        f"🎵 Tracks: {total}",
        "",
    ]

    for mood in MOODS:

        lines.append(
            f"{MOOD_NAMES[mood]} → "
            f"{counts[mood]}"
        )

    send_message(
        chat_id,
        "\n".join(lines)
    )


# ============================================================
# TELETHON CHANNEL RESOLUTION
# ============================================================

def resolve_channel(value):

    if not value:
        return None

    value = str(value).strip()

    # Numeric Telegram ID
    if value.lstrip("-").isdigit():

        return int(value)

    # @username
    if value.startswith("@"):

        return value

    # t.me/username
    if "t.me/" in value:

        value = value.rstrip("/")

        username = value.split(
            "t.me/",
            1
        )[1]

        if "/" in username:

            username = username.split(
                "/",
                1
            )[0]

        return (
            "@"
            + username.lstrip("@")
        )

    return value


# ============================================================
# TELETHON SCAN
# ============================================================

async def scan_mood(
    mood,
    limit=None,
):

    if not telethon_client:
        return 0

    channel_value = MOOD_CHANNELS.get(
        mood,
        ""
    )

    if not channel_value:
        return 0

    try:

        entity = await telethon_client.get_entity(
            resolve_channel(channel_value)
        )

        # Save actual Telegram ID
        channel_id = str(
            entity.id
        )

        found = 0

        async for message in telethon_client.iter_messages(
            entity,
            limit=limit,
        ):

            # Music / audio / document / video
            is_music = bool(
                message.audio
                or message.document
                or message.video
                or message.voice
            )

            if not is_music:
                continue

            save_track(
                mood,
                channel_id,
                message.id,
            )

            found += 1

        invalidate_track_count(
            mood
        )

        print(
            f"🎵 {MOOD_NAMES[mood]} scanned:",
            found
        )

        return found

    except Exception as exc:

        print(
            f"❌ SCAN ERROR {mood}:",
            repr(exc)
        )

        return 0


async def scan_all_channels_async():

    print(
        "🔎 Starting channel scan..."
    )

    for mood in MOODS:

        await scan_mood(
            mood,
            limit=None,
        )

        await asyncio.sleep(
            0.2
        )

    print(
        "✅ Channel scan completed"
    )


def run_telethon_scan():

    global telethon_loop

    if not telethon_client:
        return

    try:

        loop = asyncio.new_event_loop()

        telethon_loop = loop

        asyncio.set_event_loop(loop)

        loop.run_until_complete(
            scan_all_channels_async()
        )

    except Exception as exc:

        print(
            "❌ TELETHON SCAN ERROR:",
            repr(exc)
        )

    finally:

        try:
            loop.close()
        except Exception:
            pass


# ============================================================
# TELETHON START
# ============================================================

def start_telethon():

    global telethon_client

    if not TELETHON_API_ID:
        print(
            "❌ TELETHON_API_ID missing"
        )
        return

    if not TELETHON_API_HASH:
        print(
            "❌ TELETHON_API_HASH missing"
        )
        return

    if not TELETHON_SESSION:
        print(
            "❌ TELETHON_SESSION missing"
        )
        return

    try:

        api_id = int(
            TELETHON_API_ID
        )

    except ValueError:

        print(
            "❌ TELETHON_API_ID must be number"
        )

        return

    try:

        print(
            "🔐 Connecting Telegram account..."
        )

        loop = asyncio.new_event_loop()

        asyncio.set_event_loop(loop)

        client = TelegramClient(
            StringSession(
                TELETHON_SESSION
            ),
            api_id,
            TELETHON_API_HASH,
            loop=loop,
            connection_retries=10,
            retry_delay=5,
            auto_reconnect=True,
        )

        telethon_client = client

        loop.run_until_complete(
            client.connect()
        )

        if not loop.run_until_complete(
            client.is_user_authorized()
        ):

            print(
                "❌ TELETHON SESSION IS NOT AUTHORIZED"
            )

            return

        print(
            "✅ Telegram account connected"
        )

        telethon_ready.set()

        # Initial scan
        loop.run_until_complete(
            scan_all_channels_async()
        )

        print(
            "✅ Initial channel scan completed"
        )

        # Keep Telethon alive
        print(
            "🔄 Telethon is running..."
        )

        loop.run_until_complete(
            client.run_until_disconnected()
        )

    except Exception as exc:

        telethon_ready.clear()

        print(
            "❌ TELETHON ERROR:",
            repr(exc)
        )

        # Auto retry
        time.sleep(5)

        print(
            "🔄 Retrying Telethon..."
        )

        start_telethon()


# ============================================================
# PERIODIC SCAN
# ============================================================

def periodic_scan():

    while True:

        try:

            # Wait until Telethon is ready
            telethon_ready.wait(
                timeout=60
            )

            if telethon_ready.is_set():

                # Background scan
                run_telethon_scan()

        except Exception as exc:

            print(
                "PERIODIC SCAN ERROR:",
                repr(exc)
            )

        # Scan every 30 minutes
        time.sleep(
            1800
        )


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

    return {
        "status": "ok",
        "telethon": telethon_ready.is_set(),
    }


# ============================================================
# START BACKGROUND MUSIC
# ============================================================

def submit_music(
    chat_id,
    user_id,
    mood,
):

    try:

        executor.submit(
            background_send,
            chat_id,
            user_id,
            mood,
        )

    except Exception as exc:

        print(
            "EXECUTOR ERROR:",
            repr(exc)
        )


# ============================================================
# SIMPLE BOT POLLING
# ============================================================
#
# Webhook မသုံးဘူး။
# Long polling သုံးတယ်။
#
# ဒါကြောင့် Render URL / webhook conflict
# မဖြစ်တော့ဘူး။
#
# ============================================================

def bot_polling():

    print(
        "🤖 BOT POLLING STARTED"
    )

    offset = 0

    while True:

        try:

            result = telegram(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 30,
                    "allowed_updates": [
                        "message",
                        "callback_query",
                    ],
                },
                timeout=40,
                retries=3,
            )

            if not result.get("ok"):

                time.sleep(2)

                continue

            updates = result.get(
                "result",
                []
            )

            for update in updates:

                offset = (
                    update["update_id"] + 1
                )

                try:

                    process_update(
                        update
                    )

                except Exception as exc:

                    print(
                        "UPDATE ERROR:",
                        repr(exc)
                    )

        except Exception as exc:

            print(
                "POLLING ERROR:",
                repr(exc)
            )

            time.sleep(3)


# ============================================================
# PROCESS UPDATE
# ============================================================

def process_update(update):

    callback = update.get(
        "callback_query"
    )

    if callback:

        process_callback(
            callback
        )

        return

    message = update.get(
        "message"
    )

    if message:

        process_message(
            message
        )


# ============================================================
# CALLBACK
# ============================================================

def process_callback(callback):

    callback_id = callback.get(
        "id"
    )

    data = callback.get(
        "data",
        ""
    )

    user = callback.get(
        "from",
        {}
    )

    message = callback.get(
        "message",
        {}
    )

    chat = message.get(
        "chat",
        {}
    )

    chat_id = chat.get(
        "id"
    )

    user_id = user.get(
        "id"
    )

    if not chat_id or not user_id:

        answer_callback(
            callback_id,
            "Chat error"
        )

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
                "Invalid mood"
            )

            return

        # IMPORTANT:
        # Callback ကို ချက်ချင်းဖြေ
        answer_callback(
            callback_id,
            f"{MOOD_NAMES[mood]} ✓"
        )

        # Save selected mood immediately
        try:

            set_user_mood(
                user_id,
                mood
            )

        except Exception as exc:

            print(
                "SET MOOD ERROR:",
                repr(exc)
            )

        # Background music
        submit_music(
            chat_id,
            user_id,
            mood
        )

        return

    # ========================================================
    # NEXT
    # ========================================================

    if data == "next_music":

        # Immediate response
        answer_callback(
            callback_id,
            "🔀 Finding..."
        )

        mood = get_user_mood(
            user_id
        )

        if not mood:

            send_message(
                chat_id,
                "🎧 အရင်ဆုံး Mood ရွေးပါ 👇",
                mood_menu()
            )

            return

        submit_music(
            chat_id,
            user_id,
            mood
        )

        return

    # ========================================================
    # CHANGE MOOD
    # ========================================================

    if data == "change_mood":

        answer_callback(
            callback_id,
            "🎧 Choose mood"
        )

        send_message(
            chat_id,
            "🎧 Choose your mood 👇",
            mood_menu()
        )

        return


# ============================================================
# NORMAL MESSAGE
# ============================================================

def process_message(message):

    chat = message.get(
        "chat",
        {}
    )

    chat_id = chat.get(
        "id"
    )

    user = message.get(
        "from",
        {}
    )

    if not chat_id:
        return

    register_user(
        user
    )

    text = (
        message.get(
            "text",
            ""
        )
        or ""
    ).strip()

    # ========================================================
    # START
    # ========================================================

    if text == "/start":

        send_message(
            chat_id,
            (
                "🎧 NOT YOUR VIBE MUSIC\n\n"
                "Welcome! 🔥\n\n"
                "Mood တစ်ခုရွေးပြီး "
                "အဲ့ဒီ mood channel ထဲက "
                "random track ကို နားထောင်ပါ 👇"
            ),
            mood_menu()
        )

        return

    # ========================================================
    # MOOD
    # ========================================================

    if text == "/mood":

        send_message(
            chat_id,
            "🎧 Choose your mood 👇",
            mood_menu()
        )

        return

    # ========================================================
    # NEXT
    # ========================================================

    if text == "/next":

        mood = get_user_mood(
            chat_id
        )

        if not mood:

            send_message(
                chat_id,
                "🎧 အရင်ဆုံး Mood ရွေးပါ 👇",
                mood_menu()
            )

            return

        send_message(
            chat_id,
            "🔀 Finding your next track..."
        )

        submit_music(
            chat_id,
            chat_id,
            mood
        )

        return

    # ========================================================
    # USERS
    # ========================================================

    if text == "/users":

        send_stats(
            chat_id
        )

        return

    # ========================================================
    # STATS
    # ========================================================

    if text == "/stats":

        send_stats(
            chat_id
        )

        return

    # ========================================================
    # HEALTH
    # ========================================================

    if text == "/health":

        send_message(
            chat_id,
            (
                "🟢 Bot: Online\n"
                f"📡 Telethon: "
                f"{'Connected' if telethon_ready.is_set() else 'Reconnecting'}"
            )
        )

        return

    # ========================================================
    # HELP
    # ========================================================

    if text == "/help":

        send_message(
            chat_id,
            (
                "🎧 NOT YOUR VIBE MUSIC BOT\n\n"
                "/start → Start\n"
                "/mood → Mood menu\n"
                "/next → Next track\n"
                "/stats → Admin statistics\n"
                "/users → Admin statistics\n"
                "/health → Bot status"
            )
        )

        return


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=========================================="
    )

    print(
        "🎧 NOT YOUR VIBE MUSIC BOT"
    )

    print(
        "⚡ FAST + AUTO RECOVERY VERSION"
    )

    print(
        "=========================================="
    )

    print(
        "Database:",
        DB_PATH
    )

    # ========================================================
    # ENV CHECK
    # ========================================================

    missing = []

    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")

    if not ADMIN_USER_ID:
        missing.append("ADMIN_USER_ID")

    if not TELETHON_API_ID:
        missing.append("TELETHON_API_ID")

    if not TELETHON_API_HASH:
        missing.append("TELETHON_API_HASH")

    if not TELETHON_SESSION:
        missing.append("TELETHON_SESSION")

    if missing:

        print(
            "❌ MISSING ENV:",
            ", ".join(missing)
        )

        # Don't silently crash.
        # Keep web server alive so Render knows service is alive.
        print(
            "⚠️ Please fix Environment Variables."
        )

    # ========================================================
    # DATABASE
    # ========================================================

    try:

        init_db()

        print(
            "✅ DATABASE READY"
        )

    except Exception as exc:

        print(
            "❌ DATABASE ERROR:",
            repr(exc)
        )

    # ========================================================
    # TELETHON THREAD
    # ========================================================

    threading.Thread(
        target=start_telethon,
        daemon=True,
        name="telethon-main"
    ).start()

    # ========================================================
    # PERIODIC SCANNER
    # ========================================================

    threading.Thread(
        target=periodic_scan,
        daemon=True,
        name="telethon-scanner"
    ).start()

    # ========================================================
    # BOT POLLING
    # ========================================================

    threading.Thread(
        target=bot_polling,
        daemon=True,
        name="bot-polling"
    ).start()

    # ========================================================
    # WEB SERVER
    # ========================================================

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    print(
        "🚀 WEB SERVER STARTING..."
    )

    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True,
        use_reloader=False,
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as exc:

        print(
            "❌ MAIN ERROR:",
            repr(exc)
        )

        # Keep process alive on unexpected startup error
        while True:

            time.sleep(30)
