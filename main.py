import os
import time
import random
import sqlite3
import threading
import asyncio

import requests
from flask import Flask, request

from telethon import TelegramClient
from telethon.sessions import StringSession


# ============================================================
# APP
# ============================================================

app = Flask(__name__)


# ============================================================
# ENVIRONMENT
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

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

DB_PATH = os.path.join(
    BASE_DIR,
    "music_bot.db"
)


# ============================================================
# MOODS
# ============================================================

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
# CHANNELS
# ============================================================
#
# HYPE / MELODIC ကို user ပေးထားတဲ့ Telegram ID
# အတိုင်း fixed ထားပါတယ်။
#
# HYPE:
# -1004427220481
#
# MELODIC:
# -1004446996297
#
# ============================================================

MOOD_CHANNELS = {

    "sad":
        os.getenv("SAD_CHANNEL", "").strip(),

    "love":
        os.getenv("LOVE_CHANNEL", "").strip(),

    "chill":
        os.getenv("CHILL_CHANNEL", "").strip(),

    "hype":
        "-1004427220481",

    "dark":
        os.getenv("DARK_CHANNEL", "").strip(),

    "energetic":
        os.getenv("ENERGETIC_CHANNEL", "").strip(),

    "night":
        os.getenv("NIGHT_CHANNEL", "").strip(),

    "melodic":
        "-1004446996297",
}


# ============================================================
# HTTP
# ============================================================

http = requests.Session()

http.headers.update({
    "User-Agent": "NOT-YOUR-VIBE-MUSIC-BOT/7.0"
})


# ============================================================
# LOCKS
# ============================================================

db_lock = threading.Lock()

user_locks = {}

user_locks_lock = threading.Lock()


# ============================================================
# TELETHON CLIENT
# ============================================================

telethon_client = None

telethon_ready = False


# ============================================================
# DATABASE
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA busy_timeout=30000"
    )

    return conn


def init_db():

    with db_lock:

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

                    total_requests INTEGER DEFAULT 0

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
                idx_history_user_mood
                ON user_history(
                    user_id,
                    mood,
                    sent_at DESC
                )
            """)

            conn.commit()

            print("✅ Database ready")

        finally:

            conn.close()


# ============================================================
# TELEGRAM BOT API
# ============================================================

def telegram(
    method,
    data=None,
    timeout=20
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

    try:

        response = http.post(
            url,
            json=data or {},
            timeout=timeout
        )

        try:

            result = response.json()

        except Exception:

            result = {
                "ok": False,
                "description": response.text
            }

        if not result.get("ok"):

            print(
                "⚠️ Telegram API ERROR:",
                method,
                result
            )

        return result

    except Exception as exc:

        print(
            "⚠️ Telegram REQUEST ERROR:",
            method,
            repr(exc)
        )

        return {
            "ok": False,
            "description": str(exc)
        }


# ============================================================
# SEND MESSAGE
# ============================================================

def send_message(
    chat_id,
    text,
    keyboard=None
):

    data = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True
    }

    if keyboard is not None:

        data["reply_markup"] = keyboard

    return telegram(
        "sendMessage",
        data,
        timeout=15
    )


# ============================================================
# CALLBACK
# ============================================================

def answer_callback(
    callback_id,
    text=""
):

    return telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text
        },
        timeout=8
    )


# ============================================================
# COPY MUSIC
# ============================================================

def copy_music(
    chat_id,
    channel_id,
    message_id
):

    return telegram(
        "copyMessage",
        {
            "chat_id": chat_id,
            "from_chat_id": channel_id,
            "message_id": message_id
        },
        timeout=30
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
            user_id,
            user.get("username"),
            user.get("first_name"),
            user.get("last_name"),
            now,
            now
        ))

        conn.commit()

    except Exception as exc:

        print(
            "REGISTER USER ERROR:",
            repr(exc)
        )

    finally:

        conn.close()


# ============================================================
# SAVE TRACK
# ============================================================

def save_track(
    mood,
    channel_id,
    message_id
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
            int(time.time())
        ))

        conn.commit()

    except Exception as exc:

        print(
            "SAVE TRACK ERROR:",
            repr(exc)
        )

    finally:

        conn.close()


# ============================================================
# NORMALIZE CHANNEL ID
# ============================================================

def normalize_channel_id(entity):

    entity_id = getattr(
        entity,
        "id",
        None
    )

    if not entity_id:
        return None

    entity_id = str(entity_id)

    if entity_id.startswith("-100"):
        return entity_id

    return "-100" + entity_id


# ============================================================
# CHECK MUSIC
# ============================================================

def is_music_message(message):

    if not message:
        return False

    if not getattr(
        message,
        "media",
        None
    ):
        return False

    # Telegram Audio
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

    if document:

        mime_type = (
            getattr(
                document,
                "mime_type",
                ""
            )
            or ""
        ).lower()

        if mime_type.startswith("audio/"):
            return True

        if mime_type.startswith("video/"):
            return True

        file_obj = getattr(
            message,
            "file",
            None
        )

        if file_obj:

            name = (
                getattr(
                    file_obj,
                    "name",
                    ""
                )
                or ""
            ).lower()

            if name.endswith((
                ".mp3",
                ".m4a",
                ".flac",
                ".wav",
                ".aac",
                ".ogg",
                ".opus",
                ".mp4",
                ".mkv",
                ".webm"
            )):

                return True

    return False


# ============================================================
# SAVE TELETHON MESSAGE
# ============================================================

def save_telethon_message(
    mood,
    entity,
    message
):

    if not is_music_message(message):
        return False

    message_id = getattr(
        message,
        "id",
        None
    )

    if not message_id:
        return False

    channel_id = normalize_channel_id(
        entity
    )

    if not channel_id:
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
    mood,
    channel_value
):

    print("")
    print("=" * 55)
    print(f"🔎 SCANNING {mood.upper()}")
    print(f"📡 Channel: {channel_value}")

    if not channel_value:

        print(
            f"⚠️ {mood.upper()} CHANNEL MISSING"
        )

        return 0

    if telethon_client is None:

        print(
            "❌ Telethon client unavailable"
        )

        return 0

    try:

        lookup_value = channel_value

        # Telegram numeric ID
        if (
            isinstance(
                channel_value,
                str
            )
            and
            channel_value.lstrip("-").isdigit()
        ):

            lookup_value = int(
                channel_value
            )

        print(
            f"🔎 Finding {mood.upper()} channel..."
        )

        entity = await telethon_client.get_entity(
            lookup_value
        )

        print(
            f"✅ {mood.upper()} CHANNEL FOUND"
        )

        print(
            "   Telegram ID:",
            getattr(
                entity,
                "id",
                "unknown"
            )
        )

        count = 0
        checked = 0

        async for message in telethon_client.iter_messages(
            entity
        ):

            checked += 1

            try:

                if save_telethon_message(
                    mood,
                    entity,
                    message
                ):

                    count += 1

            except Exception as exc:

                print(
                    "⚠️ Message error:",
                    repr(exc)
                )

        print(
            f"📦 {mood.upper()} messages checked:",
            checked
        )

        print(
            f"🎵 {mood.upper()} MUSIC FOUND:",
            count
        )

        print(
            f"✅ {mood.upper()} SCAN COMPLETE"
        )

        return count

    except Exception as exc:

        print(
            f"❌ {mood.upper()} SCAN ERROR:",
            repr(exc)
        )

        return 0


# ============================================================
# SCAN ALL
# ============================================================

async def scan_all_channels():

    print("")
    print("=" * 60)
    print("🚀 STARTING MOOD CHANNEL SCAN")
    print("=" * 60)

    total = 0

    for mood in MOODS:

        try:

            count = await scan_one_channel(
                mood,
                MOOD_CHANNELS.get(
                    mood,
                    ""
                )
            )

            total += count

        except Exception as exc:

            print(
                f"❌ {mood.upper()} SYSTEM ERROR:",
                repr(exc)
            )

        await asyncio.sleep(1)

    print("")
    print("=" * 60)
    print(
        "🎵 TOTAL MUSIC FOUND:",
        total
    )
    print("=" * 60)


# ============================================================
# TELETHON WORKER
# ============================================================

def telethon_worker():

    global telethon_client
    global telethon_ready

    print("")
    print("=" * 60)
    print("🔐 TELETHON WORKER STARTING")
    print("=" * 60)

    # --------------------------------------------------------
    # API ID
    # --------------------------------------------------------

    if not TELETHON_API_ID:

        print(
            "❌ TELETHON_API_ID missing"
        )

        return

    print(
        "✅ TELETHON_API_ID found"
    )

    # --------------------------------------------------------
    # API HASH
    # --------------------------------------------------------

    if not TELETHON_API_HASH:

        print(
            "❌ TELETHON_API_HASH missing"
        )

        return

    print(
        "✅ TELETHON_API_HASH found"
    )

    # --------------------------------------------------------
    # SESSION
    # --------------------------------------------------------

    if not TELETHON_SESSION:

        print(
            "❌ TELETHON_SESSION missing"
        )

        return

    print(
        "✅ TELETHON_SESSION found"
    )

    # --------------------------------------------------------
    # API ID convert
    # --------------------------------------------------------

    try:

        api_id = int(
            TELETHON_API_ID
        )

    except Exception:

        print(
            "❌ TELETHON_API_ID must be a number"
        )

        return

    # --------------------------------------------------------
    # Create client
    # --------------------------------------------------------

    try:

        print(
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

            timeout=30

        )

        print(
            "✅ Telegram client created"
        )

    except Exception as exc:

        print(
            "❌ CLIENT CREATE ERROR:",
            repr(exc)
        )

        return

    # --------------------------------------------------------
    # Async runner
    # --------------------------------------------------------

    async def runner():

        global telethon_ready

        while True:

            try:

                # ------------------------------------------------
                # CONNECT
                # ------------------------------------------------

                print(
                    "🔐 Connecting to Telegram..."
                )

                await telethon_client.connect()

                print(
                    "✅ Telegram connection established"
                )

                # ------------------------------------------------
                # AUTH CHECK
                # ------------------------------------------------

                authorized = (
                    await telethon_client.is_user_authorized()
                )

                if not authorized:

                    telethon_ready = False

                    print("")
                    print(
                        "❌ TELETHON SESSION NOT AUTHORIZED"
                    )

                    print(
                        "❌ Please generate a new StringSession"
                    )

                    return

                # ------------------------------------------------
                # LOGIN OK
                # ------------------------------------------------

                telethon_ready = True

                print(
                    "✅ TELETHON LOGIN SUCCESS"
                )

                # ------------------------------------------------
                # Account
                # ------------------------------------------------

                try:

                    me = await telethon_client.get_me()

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

                        print(
                            "👤 Telegram account:",
                            username
                        )

                except Exception as exc:

                    print(
                        "⚠️ Account info error:",
                        repr(exc)
                    )

                # ------------------------------------------------
                # SCAN
                # ------------------------------------------------

                try:

                    await scan_all_channels()

                except Exception as exc:

                    print(
                        "❌ CHANNEL SCAN ERROR:",
                        repr(exc)
                    )

                # ------------------------------------------------
                # READY
                # ------------------------------------------------

                print("")
                print("=" * 60)
                print(
                    "📡 TELETHON READY"
                )
                print(
                    "🎵 CHANNEL SCANNING READY"
                )
                print("=" * 60)

                # ------------------------------------------------
                # Keep connection
                # ------------------------------------------------

                await telethon_client.run_until_disconnected()

                telethon_ready = False

                print(
                    "⚠️ Telegram connection disconnected"
                )

            except Exception as exc:

                telethon_ready = False

                print(
                    "❌ TELETHON CONNECTION ERROR:",
                    repr(exc)
                )

            # ----------------------------------------------------
            # Reconnect
            # ----------------------------------------------------

            print(
                "🔄 Reconnecting Telegram in 10 seconds..."
            )

            try:

                if telethon_client:

                    await telethon_client.disconnect()

            except Exception:
                pass

            await asyncio.sleep(10)

    try:

        asyncio.run(
            runner()
        )

    except Exception as exc:

        telethon_ready = False

        print(
            "❌ TELETHON WORKER ERROR:",
            repr(exc)
        )

    finally:

        telethon_ready = False

        print(
            "⚠️ TELETHON WORKER STOPPED"
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
                    "callback_data": "mood_sad"
                },
                {
                    "text": "❤️ Love",
                    "callback_data": "mood_love"
                }
            ],

            [
                {
                    "text": "🌙 Chill",
                    "callback_data": "mood_chill"
                },
                {
                    "text": "🔥 Hype",
                    "callback_data": "mood_hype"
                }
            ],

            [
                {
                    "text": "🖤 Dark",
                    "callback_data": "mood_dark"
                },
                {
                    "text": "⚡ Energetic",
                    "callback_data": "mood_energetic"
                }
            ],

            [
                {
                    "text": "🚗 Night Drive",
                    "callback_data": "mood_night"
                },
                {
                    "text": "🌌 Melodic",
                    "callback_data": "mood_melodic"
                }
            ]

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
                    "callback_data": "next_music"
                }
            ],

            [
                {
                    "text": "🎧 Change Mood",
                    "callback_data": "change_mood"
                }
            ]

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
# RECENT TRACKS
# ============================================================

def get_recent_tracks(
    user_id,
    mood,
    limit=30
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
            user_id,
            mood,
            limit
        )).fetchall()

        return {
            int(row["message_id"])
            for row in rows
        }

    finally:

        conn.close()


# ============================================================
# USER MOOD
# ============================================================

def set_user_mood(
    user_id,
    mood
):

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
            user_id,
            mood,
            int(time.time())
        ))

        conn.commit()

    finally:

        conn.close()


def get_user_mood(user_id):

    conn = get_db()

    try:

        row = conn.execute("""
            SELECT mood

            FROM user_state

            WHERE user_id = ?

        """, (
            user_id,
        )).fetchone()

        if not row:
            return None

        return row["mood"]

    finally:

        conn.close()


# ============================================================
# RESERVE TRACK
# ============================================================

def reserve_track(
    user_id,
    mood
):

    lock = get_user_lock(
        user_id
    )

    with lock:

        conn = get_db()

        try:

            recent = get_recent_tracks(
                user_id,
                mood,
                30
            )

            rows = conn.execute("""
                SELECT
                    message_id,
                    channel_id

                FROM tracks

                WHERE mood = ?

                ORDER BY RANDOM()

                LIMIT 100

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
                        (
                            message_id,
                            str(row["channel_id"])
                        )
                    )

            if not candidates:

                candidates = [
                    (
                        int(row["message_id"]),
                        str(row["channel_id"])
                    )
                    for row in rows
                ]

            message_id, channel_id = random.choice(
                candidates
            )

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
                user_id,
                mood,
                channel_id,
                message_id,
                int(time.time())
            ))

            conn.commit()

            return (
                message_id,
                channel_id
            )

        except Exception as exc:

            conn.rollback()

            print(
                "RESERVE TRACK ERROR:",
                repr(exc)
            )

            return None

        finally:

            conn.close()


# ============================================================
# REMOVE FAILED HISTORY
# ============================================================

def remove_last_history(
    user_id,
    message_id
):

    conn = get_db()

    try:

        conn.execute("""
            DELETE FROM user_history

            WHERE id = (

                SELECT id

                FROM user_history

                WHERE user_id = ?

                AND message_id = ?

                ORDER BY id DESC

                LIMIT 1

            )

        """, (
            user_id,
            message_id
        ))

        conn.commit()

    except Exception as exc:

        print(
            "REMOVE HISTORY ERROR:",
            repr(exc)
        )

    finally:

        conn.close()


# ============================================================
# SEND MUSIC
# ============================================================

def send_music(
    chat_id,
    user_id,
    mood
):

    try:

        count = get_track_count(
            mood
        )

        print(
            "🎧 MUSIC REQUEST:",
            user_id,
            mood,
            "tracks:",
            count
        )

        if count <= 0:

            send_message(
                chat_id,

                f"{MOOD_NAMES[mood]}\n\n"
                "⚠️ ဒီ mood ထဲမှာ music မတွေ့သေးပါ။\n\n"
                "ခဏနေရင် ထပ်စမ်းကြည့်ပါ။",

                mood_menu()
            )

            return

        attempts = min(
            count,
            10
        )

        for _ in range(attempts):

            reserved = reserve_track(
                user_id,
                mood
            )

            if not reserved:
                break

            message_id = reserved[0]

            channel_id = reserved[1]

            print(
                "🎵 Trying:",
                mood,
                channel_id,
                message_id
            )

            result = copy_music(
                chat_id,
                channel_id,
                message_id
            )

            if result.get("ok"):

                print(
                    "✅ MUSIC SENT:",
                    user_id,
                    mood,
                    message_id
                )

                send_message(
                    chat_id,

                    f"{MOOD_NAMES[mood]}\n\n"
                    "🎧 Enjoy your music! 🔥",

                    music_buttons()
                )

                return

            print(
                "⚠️ COPY FAILED:",
                result
            )

            remove_last_history(
                user_id,
                message_id
            )

        send_message(
            chat_id,

            f"{MOOD_NAMES[mood]}\n\n"
            "⚠️ ဒီ mood က track ကို "
            "အခု copy လုပ်လို့မရသေးပါ။",

            music_buttons()
        )

    except Exception as exc:

        print(
            "❌ SEND MUSIC ERROR:",
            repr(exc)
        )

        try:

            send_message(
                chat_id,
                "⚠️ ခဏအကြာမှာ ပြန်စမ်းကြည့်ပါ။"
            )

        except Exception:
            pass


# ============================================================
# BACKGROUND MUSIC
# ============================================================

def background_music(
    chat_id,
    user_id,
    mood
):

    try:

        send_music(
            chat_id,
            user_id,
            mood
        )

    except Exception as exc:

        print(
            "❌ BACKGROUND MUSIC ERROR:",
            repr(exc)
        )


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
# USER COUNT
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
# TRACK COUNTS
# ============================================================

def get_track_counts():

    result = {
        mood: 0
        for mood in MOODS
    }

    conn = get_db()

    try:

        rows = conn.execute("""
            SELECT
                mood,
                COUNT(*) AS count

            FROM tracks

            GROUP BY mood

        """).fetchall()

        for row in rows:

            mood = row["mood"]

            if mood in result:

                result[mood] = int(
                    row["count"]
                )

    finally:

        conn.close()

    return result


# ============================================================
# STATS
# ============================================================

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
        "📊 NOT YOUR VIBE MUSIC BOT",
        "",
        f"👥 Users: {users}",
        f"🎵 Total tracks: {total}",
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
# HOME
# ============================================================

@app.route("/")
def home():

    return "🎧 NOT YOUR VIBE MUSIC BOT ONLINE"


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    return "OK"


# ============================================================
# WEBHOOK
# ============================================================

@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    try:

        update = request.get_json(
            silent=True
        )

    except Exception as exc:

        print(
            "WEBHOOK JSON ERROR:",
            repr(exc)
        )

        return "OK"

    if not update:
        return "OK"

    # ========================================================
    # CALLBACK
    # ========================================================

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
            "from"
        ) or {}

        message = callback.get(
            "message"
        ) or {}

        chat = message.get(
            "chat"
        ) or {}

        chat_id = chat.get(
            "id"
        )

        user_id = user.get(
            "id"
        )

        if not chat_id or not user_id:
            return "OK"

        register_user(
            user
        )

        # ----------------------------------------------------
        # MOOD
        # ----------------------------------------------------

        if data.startswith(
            "mood_"
        ):

            mood = data[5:]

            if mood not in MOODS:

                answer_callback(
                    callback_id,
                    "Invalid mood"
                )

                return "OK"

            set_user_mood(
                user_id,
                mood
            )

            answer_callback(
                callback_id,
                f"{MOOD_NAMES[mood]} ✓"
            )

            threading.Thread(
                target=background_music,
                args=(
                    chat_id,
                    user_id,
                    mood
                ),
                daemon=True
            ).start()

            return "OK"

        # ----------------------------------------------------
        # NEXT
        # ----------------------------------------------------

        if data == "next_music":

            mood = get_user_mood(
                user_id
            )

            if not mood:

                answer_callback(
                    callback_id,
                    "Choose mood first"
                )

                send_message(
                    chat_id,
                    "🎧 Choose your mood 👇",
                    mood_menu()
                )

                return "OK"

            answer_callback(
                callback_id,
                "🔀 Finding next..."
            )

            threading.Thread(
                target=background_music,
                args=(
                    chat_id,
                    user_id,
                    mood
                ),
                daemon=True
            ).start()

            return "OK"

        # ----------------------------------------------------
        # CHANGE MOOD
        # ----------------------------------------------------

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

            return "OK"

        return "OK"

    # ========================================================
    # NORMAL MESSAGE
    # ========================================================

    message = update.get(
        "message"
    )

    if not message:
        return "OK"

    chat = message.get(
        "chat"
    ) or {}

    user = message.get(
        "from"
    ) or {}

    chat_id = chat.get(
        "id"
    )

    if not chat_id:
        return "OK"

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

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    if text == "/start":

        send_message(

            chat_id,

            "🎧 NOT YOUR VIBE MUSIC\n\n"
            "Welcome! 🔥\n\n"
            "Mood တစ်ခုရွေးပါ 👇",

            mood_menu()

        )

        return "OK"

    # --------------------------------------------------------
    # MOOD
    # --------------------------------------------------------

    if text == "/mood":

        send_message(
            chat_id,
            "🎧 Choose your mood 👇",
            mood_menu()
        )

        return "OK"

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
                mood_menu()
            )

            return "OK"

        threading.Thread(
            target=background_music,
            args=(
                chat_id,
                chat_id,
                mood
            ),
            daemon=True
        ).start()

        return "OK"

    # --------------------------------------------------------
    # USERS
    # --------------------------------------------------------

    if text == "/users":

        if is_admin(chat_id):

            send_message(
                chat_id,
                f"👥 Total users: "
                f"{get_users_count()}"
            )

        else:

            send_message(
                chat_id,
                "❌ Admin only."
            )

        return "OK"

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    if text == "/stats":

        send_stats(
            chat_id
        )

        return "OK"

    # --------------------------------------------------------
    # TELETHON STATUS
    # --------------------------------------------------------

    if text == "/telegram":

        if not is_admin(chat_id):

            send_message(
                chat_id,
                "❌ Admin only."
            )

            return "OK"

        if telethon_ready:

            send_message(
                chat_id,
                "🟢 Telethon is CONNECTED\n\n"
                "📡 Channel scanner is ready."
            )

        else:

            send_message(
                chat_id,
                "🔴 Telethon is NOT CONNECTED\n\n"
                "Check Render Logs."
            )

        return "OK"

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
            "/users → User count (Admin)\n"
            "/stats → Bot statistics (Admin)\n"
            "/telegram → Telegram status (Admin)\n"
            "/help → Help"

        )

        return "OK"

    return "OK"


# ============================================================
# WEBHOOK SETUP
# ============================================================

def setup_webhook():

    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN missing"
        )

        return

    if not RENDER_EXTERNAL_URL:

        print(
            "⚠️ RENDER_EXTERNAL_URL missing"
        )

        return

    webhook_url = (
        RENDER_EXTERNAL_URL.rstrip("/")
        + "/webhook"
    )

    print(
        "🔗 Webhook URL:",
        webhook_url
    )

    result = telegram(

        "setWebhook",

        {
            "url": webhook_url,

            "allowed_updates": [
                "message",
                "callback_query"
            ],

            "drop_pending_updates": True,

            "max_connections": 40
        },

        timeout=20
    )

    print(
        "WEBHOOK RESULT:",
        result
    )


# ============================================================
# STARTUP
# ============================================================

def startup():

    print("")
    print("=" * 65)
    print("🎧 NOT YOUR VIBE MUSIC BOT")
    print("=" * 65)

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    print(
        "📁 Database:",
        DB_PATH
    )

    try:

        init_db()

    except Exception as exc:

        print(
            "❌ DATABASE ERROR:",
            repr(exc)
        )

    # --------------------------------------------------------
    # BOT
    # --------------------------------------------------------

    if BOT_TOKEN:

        print(
            "✅ BOT_TOKEN found"
        )

    else:

        print(
            "❌ BOT_TOKEN missing"
        )

    # --------------------------------------------------------
    # ADMIN
    # --------------------------------------------------------

    if ADMIN_USER_ID:

        print(
            "✅ ADMIN_USER_ID found"
        )

    else:

        print(
            "⚠️ ADMIN_USER_ID missing"
        )

    # --------------------------------------------------------
    # CHANNELS
    # --------------------------------------------------------

    print("")
    print(
        "📡 MOOD CHANNELS"
    )

    for mood in MOODS:

        value = MOOD_CHANNELS.get(
            mood,
            ""
        )

        if value:

            print(
                f"✅ {mood.upper()} CHANNEL:",
                value
            )

        else:

            print(
                f"⚠️ {mood.upper()} CHANNEL missing"
            )

    # --------------------------------------------------------
    # WEBHOOK
    # --------------------------------------------------------

    setup_webhook()

    # --------------------------------------------------------
    # TELETHON
    # --------------------------------------------------------

    threading.Thread(
        target=telethon_worker,
        daemon=True
    ).start()

    print("")
    print("=" * 65)
    print("🚀 BOT SERVER READY")
    print("=" * 65)
    print("")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    startup()

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True
)
