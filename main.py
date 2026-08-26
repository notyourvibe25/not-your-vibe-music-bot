import os
import time
import random
import sqlite3
import threading

import requests
from flask import Flask

from telethon import TelegramClient


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


# ------------------------------------------------------------
# Telethon
# TELETHON_* ကို ဦးစားပေးသုံးမယ်
# ------------------------------------------------------------

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
# Render Free မှာ persistent disk မသုံးဘူး။
#
# Database က restart ဖြစ်ရင် ပျောက်နိုင်တယ်။
# ဒါပေမယ့် Telethon က channel history ကို startup မှာ
# ပြန် scan လုပ်ပြီး database ကို rebuild လုပ်ပေးမယ်။
#
# ============================================================

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
#
# Render Environment Variables ထဲမှာ
# channel တစ်ခုချင်းစီရဲ့ value ထည့်ထားရမယ်။
#
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
# HTTP SESSION
# ============================================================

http = requests.Session()

http.headers.update(
    {
        "User-Agent": "NOT-YOUR-VIBE-MUSIC-BOT/3.0"
    }
)


# ============================================================
# LOCKS
# ============================================================

db_init_lock = threading.Lock()

user_locks = {}

user_locks_lock = threading.Lock()


# ============================================================
# TELETHON CLIENT
# ============================================================

telethon_client = None


# ============================================================
# DATABASE
# ============================================================

def get_db():
    """
    Flask / Thread အများကြီး run နေရင်
    SQLite connection တစ်ခုကို thread အများကြီး မမျှသုံးဘူး။
    Thread တစ်ခုတိုင်း connection အသစ်ယူမယ်။
    """

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False,
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA busy_timeout = 30000"
    )

    conn.execute(
        "PRAGMA journal_mode = WAL"
    )

    return conn


def init_db():
    """
    Database tables တွေ create လုပ်မယ်။
    """

    with db_init_lock:

        conn = get_db()

        try:

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    first_seen INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL,
                    total_requests INTEGER NOT NULL DEFAULT 0
                )
                """
            )


            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mood TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(channel_id, message_id)
                )
                """
            )


            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    mood TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    sent_at INTEGER NOT NULL
                )
                """
            )


            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_state (
                    user_id INTEGER PRIMARY KEY,
                    mood TEXT,
                    updated_at INTEGER NOT NULL
                )
                """
            )


            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_tracks_mood
                ON tracks(mood)
                """
            )


            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_history_user
                ON user_history(user_id, sent_at DESC)
                """
            )


            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_history_user_mood
                ON user_history(user_id, mood, sent_at DESC)
                """
            )


            conn.commit()

        finally:

            conn.close()


# ============================================================
# TELEGRAM BOT API
# ============================================================

def telegram(
    method,
    data=None,
    timeout=20,
):
    """
    Telegram Bot API request helper.
    """

    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN is missing"
        )

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
                "TELEGRAM API ERROR:",
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
    """
    Send normal Telegram message.
    """

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
    )


# ============================================================
# ANSWER CALLBACK
# ============================================================

def answer_callback(
    callback_id,
    text="",
):
    """
    Inline button loading ကိုပျောက်စေတယ်။
    """

    return telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text,
        },
        timeout=8,
    )


# ============================================================
# COPY MUSIC
# ============================================================

def copy_music(
    chat_id,
    channel_id,
    message_id,
):
    """
    Channel ထဲက music post ကို
    user chat ထဲ copy လုပ်မယ်။
    """

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


    username = user.get(
        "username"
    )

    first_name = user.get(
        "first_name"
    )

    last_name = user.get(
        "last_name"
    )

    now = int(time.time())


    conn
