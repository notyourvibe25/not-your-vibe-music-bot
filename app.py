import os
import random
import sqlite3
import threading
import time

import requests
from flask import Flask, request


# =========================================================
# APP
# =========================================================

app = Flask(__name__)


# =========================================================
# ENVIRONMENT
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip()

# Admin Telegram User ID
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "").strip()


# =========================================================
# DATABASE
# =========================================================
#
# IMPORTANT:
# Render Free မှာ /data မသုံးပါ။
#
# Project ရဲ့ current working directory ထဲမှာ
# music_bot.db သိမ်းမယ်။
#
# Render restart/redeploy ဖြစ်ရင် Free instance ရဲ့
# local filesystem data ပျောက်နိုင်ပါတယ်။
#
# =========================================================

BASE_DIR = os.getcwd()

DB_PATH = os.path.join(
    BASE_DIR,
    "music_bot.db"
)


# =========================================================
# TELEGRAM API
# =========================================================

if BOT_TOKEN:
    TELEGRAM_API = (
        f"https://api.telegram.org/bot{BOT_TOKEN}"
    )
else:
    TELEGRAM_API = ""


# =========================================================
# MOODS
# =========================================================

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


# =========================================================
# MOOD CHANNELS
# =========================================================

MOOD_CHANNELS = {

    "sad": "@sadmooddatabase",

    "love": "@lovemooddatabase",

    "chill": "@chillmooddatabase",

    "hype": "-1004427220481",

    "dark": "@darkmooddatabase",

    "energetic": "@energeticmooddatabase",

    "night": "@nightdrivemooddatabase",

    "melodic": "-1004446996297",
}


# =========================================================
# HTTP SESSION
# =========================================================

http = requests.Session()

http.headers.update({
    "User-Agent": "NOT-YOUR-VIBE-MUSIC-BOT/3.0"
})


# =========================================================
# DATABASE LOCK
# =========================================================

db_init_lock = threading.Lock()


# =========================================================
# DATABASE CONNECTION
# =========================================================

def get_db():

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA busy_timeout = 30000"
    )

    conn.execute(
        "PRAGMA journal_mode = WAL"
    )

    conn.execute(
        "PRAGMA synchronous = NORMAL"
    )

    return conn


# =========================================================
# DATABASE INITIALIZATION
# =========================================================

def init_db():

    with db_init_lock:

        conn = get_db()

        try:

            # =================================================
            # USERS
            # =================================================

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


            # =================================================
            # USER STATE
            #
            # User ရွေးထားတဲ့ mood ကို သီးသန့်သိမ်းမယ်။
            #
            # History ကနေ mood ခန့်မှန်းစရာ မလိုတော့ဘူး။
            # =================================================

            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_state (

                    user_id INTEGER PRIMARY KEY,

                    selected_mood TEXT,

                    updated_at INTEGER NOT NULL
                )
            """)


            # =================================================
            # TRACKS
            # =================================================

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


            # =================================================
            # USER HISTORY
            # =================================================

            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_history (

                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    user_id INTEGER NOT NULL,

                    mood TEXT NOT NULL,

                    message_id INTEGER NOT NULL,

                    channel_id TEXT NOT NULL,

                    sent_at INTEGER NOT NULL
                )
            """)


            # =================================================
            # INDEXES
            # =================================================

            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_tracks_mood
                ON tracks(mood)
            """)


            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_tracks_channel
                ON tracks(channel_id)
            """)


            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_history_user
                ON user_history(
                    user_id,
                    sent_at DESC
                )
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

        except Exception as e:

            print(
                "DATABASE INIT ERROR:",
                repr(e)
            )

            raise

        finally:

            conn.close()


# =========================================================
# START DATABASE
# =========================================================

init_db()


# =========================================================
# TELEGRAM API REQUEST
# =========================================================

def telegram(
    method,
    data=None,
    timeout=15
):

    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN is missing"
        )

        return {
            "ok": False,
            "description": "BOT_TOKEN missing"
        }


    try:

        response = http.post(

            f"{TELEGRAM_API}/{method}",

            json=data or {},

            timeout=timeout
        )


        try:

            result = response.json()

        except Exception:

            result = {
                "ok": False,
                "description": (
                    "Telegram returned invalid JSON"
                )
            }


        if not result.get("ok"):

            print(
                "TELEGRAM API ERROR:",
                method,
                result
            )


        return result


    except requests.RequestException as e:

        print(
            "TELEGRAM NETWORK ERROR:",
            method,
            repr(e)
        )

        return {
            "ok": False,
            "description": str(e)
        }


    except Exception as e:

        print(
            "TELEGRAM ERROR:",
            method,
            repr(e)
        )

        return {
            "ok": False,
            "description": str(e)
        }


# =========================================================
# SEND MESSAGE
# =========================================================

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


# =========================================================
# ANSWER CALLBACK
# =========================================================

def answer_callback(
    callback_id,
    text=""
):

    if not callback_id:
        return


    return telegram(

        "answerCallbackQuery",

        {
            "callback_query_id": callback_id,

            "text": text,

            "show_alert": False
        },

        timeout=5
    )


# =========================================================
# COPY MUSIC
# =========================================================

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

        timeout=20
    )


# =========================================================
# REGISTER USER
# =========================================================

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

            username,

            first_name,

            last_name,

            now,

            now

        ))


        conn.commit()


    except Exception as e:

        print(
            "REGISTER USER ERROR:",
            repr(e)
        )


    finally:

        conn.close()


# =========================================================
# SET USER MOOD
# =========================================================

def set_user_mood(
    user_id,
    mood
):

    if mood not in MOODS:
        return False


    now = int(time.time())


    conn = get_db()

    try:

        conn.execute("""

            INSERT INTO user_state (

                user_id,

                selected_mood,

                updated_at

            )

            VALUES (?, ?, ?)

            ON CONFLICT(user_id)

            DO UPDATE SET

                selected_mood =
                    excluded.selected_mood,

                updated_at =
                    excluded.updated_at

        """, (

            user_id,

            mood,

            now

        ))


        conn.commit()

        return True


    except Exception as e:

        print(
            "SET USER MOOD ERROR:",
            repr(e)
        )

        return False


    finally:

        conn.close()


# =========================================================
# GET USER MOOD
# =========================================================

def get_user_mood(
    user_id
):

    conn = get_db()

    try:

        row = conn.execute("""

            SELECT selected_mood

            FROM user_state

            WHERE user_id = ?

            LIMIT 1

        """, (

            user_id,

        )).fetchone()


        if not row:
            return None


        mood = row["selected_mood"]


        if mood not in MOODS:
            return None


        return mood


    finally:

        conn.close()


# =========================================================
# SAVE NEW CHANNEL TRACK
# =========================================================

def save_channel_track(
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


    now = int(time.time())


    conn = get_db()

    try:

        cursor = conn.execute("""

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

            now

        ))


        conn.commit()


        if cursor.rowcount:

            print(

                "✅ NEW TRACK SAVED:",

                mood,

                channel_id,

                message_id

            )


    except Exception as e:

        print(
            "SAVE TRACK ERROR:",
            repr(e)
        )


    finally:

        conn.close()


# =========================================================
# FIND MOOD FROM CHANNEL
# =========================================================

def mood_from_channel(channel):

    if not channel:
        return None


    channel_id = str(
        channel.get("id", "")
    )


    username = channel.get(
        "username"
    )


    # Numeric ID
    for mood, configured in MOOD_CHANNELS.items():

        if str(configured) == channel_id:

            return mood


    # Username
    if username:

        username = (
            str(username)
            .lower()
            .lstrip("@")
        )


        for mood, configured in MOOD_CHANNELS.items():

            configured_username = (

                str(configured)
                .lower()
                .lstrip("@")

            )


            if (
                configured_username
                == username
            ):

                return mood


    return None


# =========================================================
# PROCESS CHANNEL POST
# =========================================================

def process_channel_post(post):

    if not post:
        return


    channel = post.get(
        "chat",
        {}
    )


    mood = mood_from_channel(
        channel
    )


    if not mood:

        return


    message_id = post.get(
        "message_id"
    )


    if not message_id:

        return


    channel_id = channel.get(
        "id"
    )


    # =====================================================
    # MUSIC MEDIA
    # =====================================================

    has_music_media = any([

        post.get("audio"),

        post.get("document"),

        post.get("video"),

        post.get("voice")

    ])


    if not has_music_media:

        print(

            "CHANNEL POST IGNORED:",

            mood,

            message_id,

            "no media"

        )

        return


    save_channel_track(

        mood,

        channel_id,

        message_id

    )


# =========================================================
# MOOD MENU
# =========================================================

def mood_menu():

    return {

        "inline_keyboard": [

            [

                {
                    "text": "😢 Sad",

                    "callback_data":
                        "mood_sad"
                },

                {
                    "text": "❤️ Love",

                    "callback_data":
                        "mood_love"
                }

            ],

            [

                {
                    "text": "🌙 Chill",

                    "callback_data":
                        "mood_chill"
                },

                {
                    "text": "🔥 Hype",

                    "callback_data":
                        "mood_hype"
                }

            ],

            [

                {
                    "text": "🖤 Dark",

                    "callback_data":
                        "mood_dark"
                },

                {
                    "text": "⚡ Energetic",

                    "callback_data":
                        "mood_energetic"
                }

            ],

            [

                {
                    "text": "🚗 Night Drive",

                    "callback_data":
                        "mood_night"
                },

                {
                    "text": "🌌 Melodic",

                    "callback_data":
                        "mood_melodic"
                }

            ]

        ]

    }


# =========================================================
# MUSIC BUTTONS
# =========================================================

def music_buttons():

    return {

        "inline_keyboard": [

            [

                {
                    "text": "🔀 Next",

                    "callback_data":
                        "next_music"
                }

            ],

            [

                {
                    "text": "🎧 Change Mood",

                    "callback_data":
                        "change_mood"
                }

            ]

        ]

    }


# =========================================================
# GET TRACK COUNT
# =========================================================

def get_track_count(
    mood
):

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
            row
