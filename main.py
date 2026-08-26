import os
import random
import threading
import time
import asyncio
import requests

from flask import Flask

from telethon import TelegramClient, events
from telethon.sessions import StringSession


# =========================================================
# APP
# =========================================================

app = Flask(__name__)


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    ""
).strip()

ADMIN_USER_ID = os.getenv(
    "ADMIN_USER_ID",
    ""
).strip()

TELETHON_API_ID = os.getenv(
    "TELETHON_API_ID",
    ""
).strip()

TELETHON_API_HASH = os.getenv(
    "TELETHON_API_HASH",
    ""
).strip()

TELETHON_SESSION = os.getenv(
    "TELETHON_SESSION",
    ""
).strip()


# =========================================================
# TELEGRAM BOT API
# =========================================================

BOT_API = (
    f"https://api.telegram.org/bot{BOT_TOKEN}"
    if BOT_TOKEN
    else ""
)


http = requests.Session()

http.headers.update({
    "User-Agent": "NOT-YOUR-VIBE-MUSIC-BOT"
})


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
# CHANNEL CONFIG
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
# MEMORY DATABASE
#
# Render Free မှာ Disk မသုံးတဲ့အတွက်
# ဒီ data တွေကို RAM ထဲမှာပဲထားမယ်။
#
# Restart ဖြစ်ရင် Telethon က channel history
# ကို ပြန် scan လုပ်ပြီး MUSIC ကို ပြန်တည်ဆောက်မယ်။
# =========================================================

music_lock = threading.RLock()

MUSIC = {
    mood: []
    for mood in MOODS
}


# =========================================================
# USER STATE
# =========================================================

state_lock = threading.RLock()

USER_STATE = {}

USER_HISTORY = {}

USER_LOCKS = {}

USER_COUNT_LOCK = threading.RLock()


# =========================================================
# TELETHON GLOBALS
# =========================================================

telethon_client = None

telethon_loop = None

resolved_channels = {}

channel_id_to_mood = {}


# =========================================================
# BOT API REQUEST
# =========================================================

def telegram(
    method,
    data=None,
    timeout=20
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

            f"{BOT_API}/{method}",

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
                "❌ TELEGRAM API ERROR:",
                method,
                result
            )


        return result


    except requests.Timeout:

        print(
            "❌ TELEGRAM TIMEOUT:",
            method
        )

        return {
            "ok": False,
            "description": "Telegram request timeout"
        }


    except Exception as e:

        print(
            "❌ TELEGRAM REQUEST ERROR:",
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

    return telegram(

        "answerCallbackQuery",

        {
            "callback_query_id": callback_id,

            "text": text
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

        timeout=30

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


# =========================================================
# MUSIC BUTTONS
# =========================================================

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


# =========================================================
# USER LOCK
# =========================================================

def get_user_lock(
    user_id
):

    with state_lock:

        if user_id not in USER_LOCKS:

            USER_LOCKS[user_id] = (
                threading.Lock()
            )


        return USER_LOCKS[user_id]


# =========================================================
# REGISTER USER
# =========================================================

def register_user(
    user
):

    if not user:

        return


    user_id = user.get(
        "id"
    )


    if not user_id:

        return


    now = int(
        time.time()
    )


    with state_lock:

        if user_id not in USER_STATE:

            USER_STATE[user_id] = {

                "mood": None,

                "first_seen": now,

                "last_seen": now

            }

        else:

            USER_STATE[user_id][
                "last_seen"
            ] = now


# =========================================================
# MUSIC MESSAGE CHECK
# =========================================================

def is_music_message(
    message
):

    # Telegram audio
    if getattr(
        message,
        "audio",
        None
    ):

        return True


    # Telegram document
    document = getattr(
        message,
        "document",
        None
    )


    if document:

        mime_type = getattr(

            document,

            "mime_type",

            ""

        ) or ""


        if mime_type.startswith(
            "audio/"
        ):

            return True


        # File name စစ်
        attributes = getattr(
            document,
            "attributes",
            []
        )


        for attribute in attributes:

            file_name = getattr(
                attribute,
                "file_name",
                ""
            ) or ""


            if file_name.lower().endswith(
                (
                    ".mp3",
                    ".wav",
                    ".flac",
                    ".m4a",
                    ".aac",
                    ".ogg",
                    ".opus"
                )
            ):

                return True


    return False


# =========================================================
# ADD MUSIC
# =========================================================

def add_music(
    mood,
    channel_id,
    message_id
):

    if mood not in MOODS:

        return


    track = (

        str(channel_id),

        int(message_id)

    )


    with music_lock:

        if track not in MUSIC[mood]:

            MUSIC[mood].append(
                track
