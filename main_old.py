import os
import time
import random
import sqlite3
import threading
import asyncio
from typing import Optional

import requests
from flask import Flask

from telethon import TelegramClient
from telethon.sessions import StringSession


# ============================================================
# APP
# ============================================================

app = Flask(__name__)


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "").strip()

RENDER_EXTERNAL_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    ""
).strip()


# ============================================================
# TELETHON
# ============================================================

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


# ============================================================
# DATABASE
# ============================================================
#
# IMPORTANT:
# Render Free မှာ /data မသုံးပါ။
#
# /tmp ကိုသုံးမယ်။
#
# Render restart/deploy ဖြစ်ရင် SQLite ပျောက်နိုင်ပါတယ်။
# ဒါပေမယ့် Telethon က channel history ကို ပြန် scan
# လုပ်ပြီး tracks table ကို ပြန်တည်ဆောက်ပေးမယ်။
#
# ============================================================

DB_PATH = "/tmp/nyv_music_bot.db"


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
# CHANNEL CONFIG
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
    "User-Agent": "NOT-YOUR-VIBE-MUSIC-BOT/5.0"
})


# ============================================================
# LOCKS
# ============================================================

db_init_lock = threading.Lock()

user_locks = {}

user_locks_lock = threading.Lock()


# ============================================================
# TELETHON GLOBALS
# ============================================================

telethon_client = None

telethon_loop = None


# ============================================================
# DATABASE
# ============================================================

def get_db():
    """
    Thread တစ်ခုစီအတွက် SQLite connection အသစ်။
    """

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

    return conn


def init_db():

    with db_init_lock:

        conn = get_db()

        try:

            # ------------------------------------------------
            # USERS
            # ------------------------------------------------

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

            # ------------------------------------------------
            # TRACKS
            # ------------------------------------------------

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

            # ------------------------------------------------
            # USER HISTORY
            # ------------------------------------------------

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

            # ------------------------------------------------
            # USER STATE
            # ------------------------------------------------

            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_state (
                    user_id INTEGER PRIMARY KEY,
                    mood TEXT,
                    updated_at INTEGER NOT NULL
                )
            """)

            # ------------------------------------------------
            # IMPORT STATUS
            # ------------------------------------------------

            conn.execute("""
                CREATE TABLE IF NOT EXISTS import_status (
                    mood TEXT PRIMARY KEY,
                    last_import INTEGER NOT NULL DEFAULT 0,
                    track_count INTEGER NOT NULL DEFAULT 0
                )
            """)

            # ------------------------------------------------
            # INDEXES
            # ------------------------------------------------

            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_tracks_mood
                ON tracks(mood)
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
# TELEGRAM BOT API
# ============================================================

def telegram(
    method,
    data=None,
    timeout=30,
):

    if not BOT_TOKEN:

        print("❌ BOT_TOKEN is missing")

        return {
            "ok": False,
            "description": "BOT_TOKEN missing",
        }

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

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
                "description": response.text,
            }

        if not result.get("ok"):

            print(
                "TELEGRAM ERROR:",
                method,
                result,
            )

        return result

    except Exception as exc:

        print(
            "TELEGRAM REQUEST ERROR:",
            method,
            repr(exc),
        )

        return {
            "ok": False,
            "description": str(exc),
        }


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
        timeout=20,
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
        timeout=10,
    )


# ============================================================
# COPY MESSAGE
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
        timeout=40,
    )


# ============================================================
# USER LOCK
# ============================================================

def get_user_lock(user_id):

    with user_locks_lock:

        if user_id not in user_locks:

            user_locks[user_id] = threading.Lock()

        return user_locks[user_id]


# ============================================================
# REGISTER USER
# ============================================================

def register_user(user):

    if not user:
        return

    user_id = user.get("id")

    if not user_id:
        return

    username = user.get("username")
    first_name = user.get("first_name")
    last_name = user.get("last_name")

    now = int(time.time())

    conn = get_db()

    try:

        conn.execute("""
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
        """, (
            int(user_id),
            username,
            first_name,
            last_name,
            now,
            now,
        ))

        conn.commit()

    except Exception as exc:

        print(
            "REGISTER USER ERROR:",
            repr(exc),
        )

    finally:

        conn.close()


# ============================================================
# SET USER MOOD
# ============================================================

def set_user_mood(
    user_id,
    mood,
):

    if mood not in MOODS:
        return

    conn = get_db()

    try:

        conn.execute("""
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
        """, (
            int(user_id),
            mood,
            int(time.time()),
        ))

        conn.commit()

    finally:

        conn.close()


# ============================================================
# GET USER MOOD
# ============================================================

def get_user_mood(user_id):

    conn = get_db()

    try:

        row = conn.execute("""
            SELECT mood
            FROM user_state
            WHERE user_id = ?
        """, (
            int(user_id),
        )).fetchone()

        if row and row["mood"] in MOODS:
            return row["mood"]

        return None

    finally:

        conn.close()


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

    if not channel_id:
        return

    if not message_id:
        return

    conn = get_db()

    try:

        conn.execute("""
            INSERT OR IGNORE INTO tracks (
                mood,
                channel_id,
                message_id,
                created_at
            )
            VALUES (?, ?, ?, ?)
        """, (
            mood,
            str(channel_id),
            int(message_id),
            int(time.time()),
        ))

        conn.commit()

    except Exception as exc:

        print(
            "SAVE TRACK ERROR:",
            repr(exc),
        )

    finally:

        conn.close()


# ============================================================
# TELETHON CHANNEL RESOLVE
# ============================================================

async def resolve_channel(channel_value):

    if not channel_value:
        return None

    value = channel_value.strip()

    try:

        entity = await telethon_client.get_entity(
            value
        )

        return entity

    except Exception as exc:

        print(
            "❌ CHANNEL RESOLVE FAILED:",
            value,
            repr(exc),
        )

        return None


# ============================================================
# CHECK MUSIC MESSAGE
# ============================================================

def is_music_message(message):

    if not message:
        return False

    # Audio
    if getattr(message, "audio", None):
        return True

    # Voice
    if getattr(message, "voice", None):
        return True

    # Document with media
    document = getattr(
        message,
        "document",
        None,
    )

    if document:

        mime = getattr(
            document,
            "mime_type",
            ""
        ) or ""

        if mime.startswith("audio/"):
            return True

        # Some Telegram music files may have
        # application/octet-stream
        attributes = getattr(
            document,
            "attributes",
            []
        ) or []

        for attr in attributes:

            if (
                hasattr(attr, "title")
                or hasattr(attr, "performer")
            ):
                return True

    return False


# ============================================================
# IMPORT ONE MOOD
# ============================================================

async def import_mood_async(
    mood,
):

    if mood not in MOODS:
        return 0

    channel_value = MOOD_CHANNELS.get(
        mood,
        ""
    ).strip()

    if not channel_value:

        print(
            f"⚠️ {mood.upper()} CHANNEL is missing"
        )

        return 0

    print(
        f"🔎 IMPORTING {MOOD_NAMES[mood]} ..."
    )

    entity = await resolve_channel(
        channel_value
    )

    if not entity:

        print(
            f"❌ Cannot access {mood} channel"
        )

        return 0

    # Use configured value for Bot API.
    bot_channel = channel_value

    count = 0

    try:

        async for message in telethon_client.iter_messages(
            entity,
            limit=None,
        ):

            try:

                if not is_music_message(message):
                    continue

                message_id = getattr(
                    message,
                    "id",
                    None
                )

                if not message_id:
                    continue

                save_track(
                    mood,
                    bot_channel,
                    message_id,
                )

                count += 1

            except Exception as exc:

                print(
                    "IMPORT MESSAGE ERROR:",
                    mood,
                    repr(exc),
                )

        conn = get_db()

        try:

            total = conn.execute("""
                SELECT COUNT(*) AS count
                FROM tracks
                WHERE mood = ?
            """, (
                mood,
            )).fetchone()["count"]

            conn.execute("""
                INSERT INTO import_status (
                    mood,
                    last_import,
                    track_count
                )
                VALUES (?, ?, ?)

                ON CONFLICT(mood)

                DO UPDATE SET
                    last_import = excluded.last_import,
                    track_count = excluded.track_count
            """, (
                mood,
                int(time.time()),
                int(total),
            ))

            conn.commit()

        finally:

            conn.close()

        print(
            f"✅ {MOOD_NAMES[mood]} IMPORT COMPLETE: "
            f"{count} scanned / {total} tracks"
        )

        return count

    except Exception as exc:

        print(
            f"❌ IMPORT FAILED {mood}:",
            repr(exc),
        )

        return 0


# ============================================================
# IMPORT ALL MOODS
# ============================================================

async def import_all_async():

    print("")
    print("==========================================")
    print("📥 TELETHON MUSIC IMPORT STARTED")
    print("==========================================")

    for mood in MOODS:

        try:

            await import_mood_async(
                mood
            )

        except Exception as exc:

            print(
                "MOOD IMPORT ERROR:",
                mood,
                repr(exc),
            )

    print("==========================================")
    print("📥 TELETHON MUSIC IMPORT FINISHED")
    print("==========================================")
    print("")


# ============================================================
# PERIODIC IMPORT
# ============================================================

async def periodic_import_async():

    while True:

        try:

            await import_all_async()

        except Exception as exc:

            print(
                "PERIODIC IMPORT ERROR:",
                repr(exc),
            )

        # Every 30 minutes
        await asyncio.sleep(
            1800
        )


# ============================================================
# TELETHON THREAD
# ============================================================

def telethon_thread():

    global telethon_client
    global telethon_loop

    if not TELETHON_API_ID:
        print(
            "❌ TELETHON_API_ID is missing"
        )
        return

    if not TELETHON_API_HASH:
        print(
            "❌ TELETHON_API_HASH is missing"
        )
        return

    if not TELETHON_SESSION:
        print(
            "❌ TELETHON_SESSION is missing"
        )
        return

    try:

        api_id = int(
            TELETHON_API_ID
        )

    except Exception:

        print(
            "❌ TELETHON_API_ID must be a number"
        )

        return

    try:

        loop = asyncio.new_event_loop()

        asyncio.set_event_loop(
            loop
        )

        telethon_loop = loop

        telethon_client = TelegramClient(
            StringSession(
                TELETHON_SESSION
            ),
            api_id,
            TELETHON_API_HASH,
        )

        async def runner():

            print(
                "🔐 Connecting Telegram account..."
            )

            await telethon_client.start()

            me = await telethon_client.get_me()

            if me:

                username = getattr(
                    me,
                    "username",
                    None
                )

                print(
                    "✅ TELETHON LOGIN SUCCESS"
                )

                print(
                    "👤 Account:",
                    username
                    or getattr(me, "id", "unknown")
                )

            else:

                print(
                    "⚠️ Telegram account not found"
                )

            await import_all_async()

            print(
                "📡 TELETHON BACKGROUND IMPORT ACTIVE"
            )

            await periodic_import_async()

        loop.run_until_complete(
            runner()
        )

    except Exception as exc:

        print(
            "❌ TELETHON FATAL ERROR:",
            repr(exc),
        )


# ============================================================
# MOOD MENU
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


# ============================================================
# MUSIC BUTTONS
# ============================================================

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
# TRACK COUNT
# ============================================================

def get_track_count(mood):

    conn = get_db()

    try:

        row = conn.execute("""
            SELECT COUNT(*) AS count
            FROM tracks
            WHERE mood = ?
        """, (
            mood,
        )).fetchone()

        return int(
            row["count"]
        )

    finally:

        conn.close()


# ============================================================
# RECENT USER TRACKS
# ============================================================

def get_recent_tracks(
    user_id,
    mood,
    limit=50,
):

    conn = get_db()

    try:

        rows = conn.execute("""
            SELECT message_id
            FROM user_history
            WHERE user_id = ?
              AND mood = ?
            ORDER BY sent_at DESC, id DESC
            LIMIT ?
        """, (
            int(user_id),
            mood,
            int(limit),
        )).fetchall()

        return {
            int(row["message_id"])
            for row in rows
        }

    finally:

        conn.close()


# ============================================================
# RESERVE TRACK
# ============================================================

def reserve_random_track(
    user_id,
    mood,
):

    lock = get_user_lock(
        int(user_id)
    )

    with lock:

        conn = get_db()

        try:

            # ----------------------------------------------
            # Recent tracks
            # ----------------------------------------------

            recent_rows = conn.execute("""
                SELECT message_id
                FROM user_history
                WHERE user_id = ?
                  AND mood = ?
                ORDER BY sent_at DESC, id DESC
                LIMIT 50
            """, (
                int(user_id),
                mood,
            )).fetchall()

            recent = {
                int(row["message_id"])
                for row in recent_rows
            }

            # ----------------------------------------------
            # Get tracks
            # ----------------------------------------------

            rows = conn.execute("""
                SELECT
                    id,
                    channel_id,
                    message_id
                FROM tracks
                WHERE mood = ?
                ORDER BY RANDOM()
            """, (
                mood,
            )).fetchall()

            if not rows:
                return None

            candidates = []

            for row in rows:

                message_id = int(
                    row["message_id"]
                )

                if message_id not in recent:

                    candidates.append(
                        row
                    )

            # ----------------------------------------------
            # If everything was recently used
            # ----------------------------------------------

            if not candidates:

                candidates = list(
                    rows
                )

            if not candidates:
                return None

            row = random.choice(
                candidates
            )

            channel_id = str(
                row["channel_id"]
            )

            message_id = int(
                row["message_id"]
            )

            # ----------------------------------------------
            # Reserve BEFORE copy
            # ----------------------------------------------

            conn.execute("""
                INSERT INTO user_history (
                    user_id,
                    mood,
                    channel_id,
                    message_id,
                    sent_at
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                int(user_id),
                mood,
                channel_id,
                message_id,
                int(time.time()),
            ))

            # Keep only latest 100
            conn.execute("""
                DELETE FROM user_history
                WHERE user_id = ?
                AND id NOT IN (
                    SELECT id
                    FROM user_history
                    WHERE user_id = ?
                    ORDER BY sent_at DESC, id DESC
                    LIMIT 100
                )
            """, (
                int(user_id),
                int(user_id),
            ))

            conn.commit()

            return (
                channel_id,
                message_id,
            )

        except Exception as exc:

            conn.rollback()

            print(
                "RESERVE TRACK ERROR:",
                repr(exc),
            )

            return None

        finally:

            conn.close()


# ============================================================
# REMOVE HISTORY
# ============================================================

def remove_last_history(
    user_id,
    message_id,
):

    conn = get_db()

    try:

        row = conn.execute("""
            SELECT id
            FROM user_history
            WHERE user_id = ?
              AND message_id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (
            int(user_id),
            int(message_id),
        )).fetchone()

        if row:

            conn.execute("""
                DELETE FROM user_history
                WHERE id = ?
            """, (
                int(row["id"]),
            ))

            conn.commit()

    except Exception as exc:

        print(
            "REMOVE HISTORY ERROR:",
            repr(exc),
        )

    finally:

        conn.close()


# ============================================================
# SEND TRACK
# ============================================================

def send_mood_track(
    chat_id,
    user_id,
    mood,
):

    count = get_track_count(
        mood
    )

    if count <= 0:

        send_message(
            chat_id,

            f"{MOOD_NAMES[mood]}\n\n"
            "⚠️ ဒီ mood ထဲမှာ music မတွေ့သေးပါ။\n\n"
            "Telethon က channel history ကို scan "
            "လုပ်ပြီး database ထဲ ထည့်ပေးရပါမယ်။\n\n"
            "Render Logs မှာ "
            "IMPORT COMPLETE ကို စစ်ကြည့်ပါ။",

            mood_menu(),
        )

        return

    # --------------------------------------------------------
    # Try several tracks
    # --------------------------------------------------------

    attempts = min(
        count,
        10,
    )

    attempted = set()

    for _ in range(attempts):

        reserved = reserve_random_track(
            user_id,
            mood,
        )

        if not reserved:
            break

        channel_id, message_id = reserved

        if message_id in attempted:

            remove_last_history(
                user_id,
                message_id,
            )

            continue

        attempted.add(
            message_id
        )

        result = copy_music(
            chat_id,
            channel_id,
            message_id,
        )

        if result.get("ok"):

            print(
                "🎵 TRACK SENT:",
                "user=",
                user_id,
                "mood=",
                mood,
                "message=",
                message_id,
            )

            send_message(
                chat_id,

                f"{MOOD_NAMES[mood]}\n\n"
                "🎧 Enjoy your music! 🔥",

                music_buttons(),
            )

            return

        print(
            "⚠️ COPY FAILED:",
            channel_id,
            message_id,
            result.get("description"),
        )

        remove_last_history(
            user_id,
            message_id,
        )

    send_message(
        chat_id,

        f"{MOOD_NAMES[mood]}\n\n"
        "❌ Music ပို့လို့မရပါ။\n\n"
        "Bot ကို ဒီ mood channel ထဲမှာ "
        "admin/member permission ပေးထားတာ "
        "သေချာစစ်ပါ။",

        mood_menu(),
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
            repr(exc),
        )

        try:

            send_message(
                chat_id,
                "❌ Music ပို့နေစဉ် error ဖြစ်သွားပါတယ်။"
            )

        except Exception:
            pass


# ============================================================
# USER STATS
# ============================================================

def get_users_count():

    conn = get_db()

    try:

        row = conn.execute("""
            SELECT COUNT(*) AS count
            FROM users
        """).fetchone()

        return int(
            row["count"]
        )

    finally:

        conn.close()


# ============================================================
# TRACK STATS
# ============================================================

def get_all_track_counts():

    result = {
        mood: 0
        for mood in MOODS
    }

    conn = get_db()

    try:

        rows = conn.execute("""
            SELECT mood, COUNT(*) AS count
            FROM tracks
            GROUP BY mood
        """).fetchall()

        for row in rows:

            mood = row["mood"]

            if mood in result:

                result[mood] = int(
                    row["count"]
                )

        return result

    finally:

        conn.close()


# ============================================================
# ADMIN
# ============================================================

def is_admin(user_id):

    if not ADMIN_USER_ID:
        return False

    return str(user_id) == str(
        ADMIN_USER_ID
    )


# ============================================================
# STATS
# ============================================================

def send_stats(
    chat_id,
):

    if not is_admin(chat_id):

        send_message(
            chat_id,
            "❌ Admin only."
        )

        return

    users = get_users_count()

    counts = get_all_track_counts()

    total = sum(
        counts.values()
    )

    text = (
        "📊 NOT YOUR VIBE MUSIC BOT\n\n"
        f"👥 Users: {users}\n"
        f"🎵 Tracks: {total}\n\n"
    )

    for mood in MOODS:

        text += (
            f"{MOOD_NAMES[mood]} → "
            f"{counts[mood]}\n"
        )

    send_message(
        chat_id,
        text,
    )


# ============================================================
# IMPORT STATUS
# ============================================================

def send_import_status(
    chat_id,
):

    if not is_admin(chat_id):

        send_message(
            chat_id,
            "❌ Admin only."
        )

        return

    counts = get_all_track_counts()

    text = (
        "📥 IMPORT STATUS\n\n"
    )

    for mood in MOODS:

        text += (
            f"{MOOD_NAMES[mood]} → "
            f"{counts[mood]} tracks\n"
        )

    send_message(
        chat_id,
        text,
    )


# ============================================================
# PROCESS CHANNEL POST
# ============================================================

def process_channel_post(
    post,
):

    if not post:
        return

    chat = post.get(
        "chat",
        {}
    )

    channel_id = chat.get(
        "id"
    )

    username = (
        chat.get("username")
        or ""
    ).lower()

    mood = None

    for m in MOODS:

        configured = (
            MOOD_CHANNELS.get(m, "")
            .strip()
            .lower()
        )

        if not configured:
            continue

        configured_clean = configured.lstrip("@")

        if (
            str(configured) == str(channel_id)
            or configured_clean == username
        ):

            mood = m
            break

    if not mood:
        return

    message_id = post.get(
        "message_id"
    )

    if not message_id:
        return

    # Audio/document/video/voice
    if not any([
        post.get("audio"),
        post.get("document"),
        post.get("video"),
        post.get("voice"),
    ]):

        return

    # Keep configured Bot API source
    source = MOOD_CHANNELS[mood]

    save_track(
        mood,
        source,
        message_id,
    )

    print(
        "📥 NEW CHANNEL TRACK:",
        mood,
        message_id,
    )


# ============================================================
# HANDLE UPDATE
# ============================================================

def handle_update(
    update,
):

    # --------------------------------------------------------
    # Channel post
    # --------------------------------------------------------

    channel_post = update.get(
        "channel_post"
    )

    if channel_post:

        try:

            process_channel_post(
                channel_post
            )

        except Exception as exc:

            print(
                "CHANNEL POST ERROR:",
                repr(exc),
            )

        return


    # --------------------------------------------------------
    # Callback
    # --------------------------------------------------------

    callback = update.get(
        "callback_query"
    )

    if callback:

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

            if callback_id:

                answer_callback(
                    callback_id,
                    "Chat error",
                )

            return

        register_user(
            user
        )

        # ----------------------------------------------------
        # MOOD
        # ----------------------------------------------------

        if data.startswith("mood_"):

            mood = data[
                len("mood_"):
            ]

            if mood not in MOODS:

                answer_callback(
                    callback_id,
                    "Invalid mood",
                )

                return

            set_user_mood(
                user_id,
                mood,
            )

            answer_callback(
                callback_id,
                f"{MOOD_NAMES[mood]} ✓",
            )

            threading.Thread(
                target=background_send,
                args=(
                    chat_id,
                    user_id,
                    mood,
                ),
                daemon=True,
            ).start()

            return

        # ----------------------------------------------------
        # NEXT
        # ----------------------------------------------------

        if data == "next_music":

            answer_callback(
                callback_id,
                "🔀 Finding next...",
            )

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

            threading.Thread(
                target=background_send,
                args=(
                    chat_id,
                    user_id,
                    mood,
                ),
                daemon=True,
            ).start()

            return

        # ----------------------------------------------------
        # CHANGE MOOD
        # ----------------------------------------------------

        if data == "change_mood":

            answer_callback(
                callback_id,
                "🎧 Choose mood",
            )

            send_message(
                chat_id,
                "🎧 Choose your mood 👇",
                mood_menu(),
            )

            return

        return

    # --------------------------------------------------------
    # Normal message
    # --------------------------------------------------------

    message = update.get(
        "message"
    )

    if not message:
        return

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
        message.get("text", "")
        or ""
    ).strip()

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    if text.startswith("/start"):

        send_message(
            chat_id,

            "🎧 NOT YOUR VIBE MUSIC\n\n"
            "Welcome! 🔥\n\n"
            "Mood တစ်ခုရွေးပြီး "
            "အဲ့ဒီ mood channel ထဲက "
            "random music ကို နားထောင်ပါ 👇",

            mood_menu(),
        )

        return

    # --------------------------------------------------------
    # MOOD
    # --------------------------------------------------------

    if text == "/mood":

        send_message(
            chat_id,
            "🎧 Choose your mood 👇",
            mood_menu(),
        )

        return

    # --------------------------------------------------------
    # NEXT
    # --------------------------------------------------------

    if text == "/next":

        mood = get_user_mood(
            chat_id
        )

        if not mood:

            send_message(
                chat_id,
                "🎧 အရင်ဆုံး Mood ရွေးပါ 👇",
                mood_menu(),
            )

            return

        threading.Thread(
            target=background_send,
            args=(
                chat_id,
                chat_id,
                mood,
            ),
            daemon=True,
        ).start()

        return

    # --------------------------------------------------------
    # USERS
    # --------------------------------------------------------

    if text == "/users":

        send_stats(
            chat_id
        )

        return

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    if text == "/stats":

        send_stats(
            chat_id
        )

        return

    # --------------------------------------------------------
    # IMPORT STATUS
    # --------------------------------------------------------

    if text == "/import":

        send_import_status(
            chat_id
        )

        return

    # --------------------------------------------------------
    # HELP
    # --------------------------------------------------------

    if text == "/help":

        send_message(
            chat_id,

            "🎧 NOT YOUR VIBE MUSIC BOT\n\n"
            "/start → Start\n"
            "/mood → Mood menu\n"
            "/next → Next track\n"
            "/stats → Admin stats\n"
            "/import → Import status\n"
            "/help → Help",
        )

        return


# ============================================================
# BOT POLLING
# ============================================================

def bot_polling():

    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN missing"
        )

        return

    print(
        "🤖 BOT POLLING STARTING..."
    )

    # --------------------------------------------------------
    # Remove webhook first
    # --------------------------------------------------------

    result = telegram(
        "deleteWebhook",
        {
            "drop_pending_updates": True
        },
        timeout=20,
    )

    print(
        "🧹 DELETE WEBHOOK:",
        result.get("ok"),
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
                        "channel_post",
                    ],
                },
                timeout=40,
            )

            if not result.get("ok"):

                print(
                    "⚠️ GET UPDATES FAILED:",
                    result.get("description"),
                )

                time.sleep(5)

                continue

            updates = result.get(
                "result",
                []
            )

            for update in updates:

                try:

                    update_id = update.get(
                        "update_id"
                    )

                    if update_id is not None:

                        offset = (
                            int(update_id) + 1
                        )

                    handle_update(
                        update
                    )

                except Exception as exc:

                    print(
                        "UPDATE ERROR:",
                        repr(exc),
                    )

        except Exception as exc:

            print(
                "❌ POLLING ERROR:",
                repr(exc),
            )

            time.sleep(5)


# ============================================================
# FLASK
# ============================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return (
        "🎧 NOT YOUR VIBE MUSIC BOT ONLINE"
    )


@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return "OK"


# ============================================================
# STARTUP VALIDATION
# ============================================================

def validate_environment():

    print("")
    print("==========================================")
    print("🔧 ENVIRONMENT CHECK")
    print("==========================================")

    required = {
        "BOT_TOKEN": BOT_TOKEN,
        "ADMIN_USER_ID": ADMIN_USER_ID,
        "TELETHON_API_ID": TELETHON_API_ID,
        "TELETHON_API_HASH": TELETHON_API_HASH,
        "TELETHON_SESSION": TELETHON_SESSION,
    }

    for name, value in required.items():

        if value:

            print(
                f"✅ {name}"
            )

        else:

            print(
                f"❌ MISSING {name}"
            )

    print("")

    for mood in MOODS:

        channel = MOOD_CHANNELS.get(
            mood,
            ""
        )

        if channel:

            print(
                f"✅ {mood.upper()} CHANNEL"
            )

        else:

            print(
                f"⚠️ {mood.upper()} CHANNEL MISSING"
            )

    print("==========================================")
    print("")


# ============================================================
# MAIN
# ============================================================

def main():

    print("")
    print("==========================================")
    print("🎧 NOT YOUR VIBE MUSIC BOT")
    print("==========================================")
    print(
        "Database:",
        DB_PATH,
    )
    print(
        "Mode: Bot Polling + Telethon",
    )
    print("==========================================")

    # --------------------------------------------------------
    # DB
    # --------------------------------------------------------

    try:

        init_db()

        print(
            "✅ SQLITE DATABASE READY"
        )

    except Exception as exc:

        print(
            "❌ DATABASE STARTUP ERROR:",
            repr(exc),
        )

        # Don't silently exit.
        # Keep process alive so Render logs show problem.
        raise

    # --------------------------------------------------------
    # ENV
    # --------------------------------------------------------

    validate_environment()

    # --------------------------------------------------------
    # Telethon
    # --------------------------------------------------------

    threading.Thread(
        target=telethon_thread,
        daemon=True,
        name="TelethonThread",
    ).start()

    # --------------------------------------------------------
    # Bot polling
    # --------------------------------------------------------

    threading.Thread(
        target=bot_polling,
        daemon=True,
        name="BotPollingThread",
    ).start()

    # --------------------------------------------------------
    # Flask
    # --------------------------------------------------------

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
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as exc:

        print("")
        print("==========================================")
        print("❌ FATAL STARTUP ERROR")
        print("==========================================")
        print(
            repr(exc)
        )
        print("==========================================")
        print("")

        # Keep the error visible in Render logs.
        raise
