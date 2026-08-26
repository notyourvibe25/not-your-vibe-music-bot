import os
import random
import asyncio
import threading
import time
import sqlite3

import requests

from flask import Flask, request

from telethon import TelegramClient
from telethon.errors import FloodWaitError


# =========================================================
# APP
# =========================================================

app = Flask(__name__)


# =========================================================
# ENV
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_USER_ID = os.getenv(
    "ADMIN_USER_ID",
    ""
).strip()

RENDER_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    ""
).strip()


# =========================================================
# TELETHON
# =========================================================
#
# IMPORTANT
#
# ဒီ 2 ခုကို Render Environment Variables ထဲထည့်ပါ။
#
# API_ID
# API_HASH
#
# Telegram my.telegram.org က ရတဲ့ API ID / API HASH
#
# =========================================================

API_ID_RAW = os.getenv(
    "API_ID",
    ""
).strip()

API_HASH = os.getenv(
    "API_HASH",
    ""
).strip()


try:

    API_ID = int(API_ID_RAW)

except ValueError:

    API_ID = 0


# =========================================================
# TELEGRAM BOT API
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
# CHANNELS
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
# MUSIC DATABASE
# =========================================================
#
# IMPORTANT
#
# ဒီ dictionary က permanent database မဟုတ်ဘူး။
#
# Telegram channel = MASTER DATABASE
#
# Render restart ဖြစ်ရင်
# scan_channels() က Telegram history ကနေ
# ပြန်တည်ဆောက်ပေးမယ်။
#
# =========================================================

MUSIC_DATABASE = {

    mood: []

    for mood in MOODS
}


# =========================================================
# LOCKS
# =========================================================

music_lock = threading.RLock()

user_lock = threading.RLock()


# =========================================================
# USER STATE
# =========================================================
#
# User mood/history ကို memory ထဲမှာထားမယ်။
#
# Render restart ဖြစ်ရင် user history reset ဖြစ်နိုင်တယ်။
#
# ဒါပေမယ့် MUSIC မပျောက်ဘူး။
#
# =========================================================

USER_STATE = {}

USER_HISTORY = {}


# =========================================================
# OPTIONAL SQLITE
# =========================================================
#
# User count အတွက် local DB သုံးထားတယ်။
#
# Music catalog အတွက် မသုံးဘူး။
#
# DB ပျောက်သွားရင်တောင်
# Telegram ကနေ music ပြန် scan လုပ်နိုင်တယ်။
#
# =========================================================

BASE_DIR = os.getcwd()

DB_PATH = os.path.join(
    BASE_DIR,
    "bot_users.db"
)


# =========================================================
# HTTP
# =========================================================

http = requests.Session()

http.headers.update({

    "User-Agent":
        "NOT-YOUR-VIBE-MUSIC-BOT/4.0"

})


# =========================================================
# TELETHON CLIENT
# =========================================================

telethon_client = None


telethon_loop = None


telethon_ready = False


# =========================================================
# DATABASE
# =========================================================

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

        conn.commit()

    finally:

        conn.close()


init_db()


# =========================================================
# TELEGRAM BOT API
# =========================================================

def telegram(
    method,
    data=None,
    timeout=15
):

    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN missing"
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

                "description":
                    "Invalid Telegram response"

            }


        if not result.get("ok"):

            print(

                "Telegram API ERROR:",

                method,

                result

            )


        return result


    except Exception as e:

        print(

            "Telegram REQUEST ERROR:",

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


    if keyboard:

        data["reply_markup"] = keyboard


    return telegram(

        "sendMessage",

        data,

        timeout=15

    )


# =========================================================
# CALLBACK
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

            "callback_query_id":
                callback_id,

            "text": text

        },

        timeout=5

    )


# =========================================================
# COPY MESSAGE
# =========================================================

def copy_music(
    chat_id,
    channel_id,
    message_id
):

    return telegram(

        "copyMessage",

        {

            "chat_id":
                chat_id,

            "from_chat_id":
                channel_id,

            "message_id":
                message_id

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


    now = int(
        time.time()
    )


    username = user.get(
        "username"
    )

    first_name = user.get(
        "first_name"
    )

    last_name = user.get(
        "last_name"
    )


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

                username =
                    excluded.username,

                first_name =
                    excluded.first_name,

                last_name =
                    excluded.last_name,

                last_seen =
                    excluded.last_seen,

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

            "REGISTER ERROR:",

            repr(e)

        )


    finally:

        conn.close()


# =========================================================
# TELETHON SCANNER
# =========================================================

def create_telethon_client():

    global telethon_client

    global telethon_loop

    global telethon_ready


    if not API_ID:

        print(
            "❌ API_ID missing"
        )

        return


    if not API_HASH:

        print(
            "❌ API_HASH missing"
        )

        return


    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN missing"
        )

        return


    print(
        "Starting Telegram scanner..."
    )


    loop = asyncio.new_event_loop()

    asyncio.set_event_loop(
        loop
    )

    telethon_loop = loop


    telethon_client = TelegramClient(

        "music_scanner_bot",

        API_ID,

        API_HASH

    )


    try:

        loop.run_until_complete(

            telethon_client.start(

                bot_token=BOT_TOKEN

            )

        )


        telethon_ready = True


        print(
            "✅ TELETHON SCANNER READY"
        )


        # Scan all channels

        loop.run_until_complete(

            scan_all_channels()

        )


        print(
            "================================"
        )

        print(
            "🎵 MUSIC DATABASE READY"
        )

        print(
            "================================"
        )


        # Keep Telethon alive

        loop.run_forever()


    except Exception as e:

        telethon_ready = False

        print(

            "❌ TELETHON ERROR:",

            repr(e)

        )


# =========================================================
# CHECK MUSIC MESSAGE
# =========================================================

def is_music_message(message):

    if not message:

        return False


    # Audio

    if getattr(
        message,
        "audio",
        None
    ):

        return True


    # Document
    #
    # MP3 / FLAC / WAV / M4A etc.
    #

    document = getattr(

        message,

        "document",

        None

    )


    if document:

        mime = getattr(

            document,

            "mime_type",

            ""

        ) or ""


        if (

            mime.startswith(
                "audio/"
            )

        ):

            return True


        # Telegram sometimes has no mime
        # but filename is music

        attributes = getattr(

            document,

            "attributes",

            []

        )


        for attribute in attributes:

            filename = getattr(

                attribute,

                "file_name",

                ""

            ) or ""


            lower = filename.lower()


            if lower.endswith((

                ".mp3",

                ".wav",

                ".flac",

                ".m4a",

                ".aac",

                ".ogg",

                ".opus"

            )):

                return True


    # Voice

    if getattr(

        message,

        "voice",

        None

    ):

        return True


    return False


# =========================================================
# SCAN ONE CHANNEL
# =========================================================

async def scan_channel(
    mood,
    channel
):

    print(
        f"🔎 Scanning {MOOD_NAMES[mood]}..."
    )

    print(
        "Channel:",
        channel
    )


    found = []


    try:

        entity = await telethon_client.get_entity(
            channel
        )


        async for message in telethon_client.iter_messages(

            entity,

            limit=None

        ):

            if not message:

                continue


            if not is_music_message(
                message
            ):

                continue


            found.append(

                {

                    "message_id":
                        int(message.id),

                    "channel_id":
                        str(
                            getattr(
                                entity,
                                "id",
                                channel
                            )
                        )

                }

            )


        # Remove duplicates

        unique = {}

        for item in found:

            unique[
                item["message_id"]
            ] = item


        found = list(
            unique.values()
        )


        # Newest first

        found.sort(

            key=lambda x:
                x["message_id"],

            reverse=True

        )


        with music_lock:

            MUSIC_DATABASE[mood] = found


        print(

            f"✅ {MOOD_NAMES[mood]}: "
            f"{len(found)} tracks"

        )


    except FloodWaitError as e:

        print(

            "⏳ Telegram FloodWait:",

            e.seconds,

            "seconds"

        )

        await asyncio.sleep(
            e.seconds
        )


    except Exception as e:

        print(

            f"❌ SCAN ERROR "
            f"{mood}:",

            repr(e)

        )


        # Don't destroy existing list

        with music_lock:

            if mood not in MUSIC_DATABASE:

                MUSIC_DATABASE[mood] = []


# =========================================================
# SCAN ALL CHANNELS
# =========================================================

async def scan_all_channels():

    print(
        "================================"
    )

    print(
        "🔎 SCANNING TELEGRAM CHANNELS"
    )

    print(
        "================================"
    )


    for mood in MOODS:

        channel = MOOD_CHANNELS[mood]


        await scan_channel(

            mood,

            channel

        )


        # Small pause

        await asyncio.sleep(
            0.5
        )


    total = 0


    with music_lock:

        for mood in MOODS:

            total += len(
                MUSIC_DATABASE[mood]
            )


    print(
        "================================"
    )

    print(
        "🎵 TOTAL TRACKS:",
        total
    )

    print(
        "================================"
    )


# =========================================================
# START TELETHON THREAD
# =========================================================

def start_scanner():

    thread = threading.Thread(

        target=create_telethon_client,

        daemon=True

    )


    thread.start()


    return thread


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
                    "text":
                        "🚗 Night Drive",

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
                    "text":
                        "🔀 Next",

                    "callback_data":
                        "next_music"
                }

            ],

            [

                {
                    "text":
                        "🎧 Change Mood",

                    "callback_data":
                        "change_mood"
                }

            ]

        ]

    }


# =========================================================
# USER MOOD
# =========================================================

def set_user_mood(
    user_id,
    mood
):

    with user_lock:

        USER_STATE[
            user_id
        ] = mood


def get_user_mood(
    user_id
):

    with user_lock:

        return USER_STATE.get(
            user_id
        )


# =========================================================
# USER HISTORY
# =========================================================

def get_user_history(
    user_id,
    mood
):

    with user_lock:

        history = USER_HISTORY.get(

            user_id,

            {}

        )


        return set(

            history.get(

                mood,

                []

            )

        )


def add_user_history(
    user_id,
    mood,
    message_id
):

    with user_lock:

        if user_id not in USER_HISTORY:

            USER_HISTORY[
                user_id
            ] = {}


        if mood not in USER_HISTORY[user_id]:

            USER_HISTORY[user_id][
                mood
            ] = []


        history = USER_HISTORY[user_id][
            mood
        ]


        history.append(
            message_id
        )


        # Keep last 100

        if len(history) > 100:

            del history[
                :-100
            ]


# =========================================================
# GET TRACKS
# =========================================================

def get_tracks(
    mood
):

    with music_lock:

        return list(

            MUSIC_DATABASE.get(

                mood,

                []

            )

        )


# =========================================================
# RESERVE TRACK
# =========================================================

def reserve_random_track(
    user_id,
    mood
):

    tracks = get_tracks(
        mood
    )


    if not tracks:

        return None


    recent = get_user_history(

        user_id,

        mood

    )


    # Prefer unused

    available = [

        track

        for track in tracks

        if track["message_id"]
        not in recent

    ]


    if not available:

        # User has already heard
        # everything in this mood.

        # Start cycle again.

        available = tracks


    track = random.choice(
        available
    )


    add_user_history(

        user_id,

        mood,

        track["message_id"]

    )


    return track


# =========================================================
# SEND TRACK
# =========================================================

def send_mood_track(
    chat_id,
    user_id,
    mood
):

    tracks = get_tracks(
        mood
    )


    if not tracks:

        send_message(

            chat_id,

            f"{MOOD_NAMES[mood]}\n\n"

            "⚠️ ဒီ mood channel ထဲက "
            "music ကို Bot က မတွေ့သေးပါ။\n\n"

            "Channel permission / "
            "API ID / API HASH / "
            "channel ID ကို စစ်ပါ။",

            mood_menu()

        )

        return


    attempted = set()


    for _ in range(
        min(10, len(tracks))
    ):

        track = reserve_random_track(

            user_id,

            mood

        )


        if not track:

            break


        message_id = track[
            "message_id"
        ]


        channel_id = track[
            "channel_id"
        ]


        if message_id in attempted:

            continue


        attempted.add(
            message_id
        )


        result = copy_music(

            chat_id,

            channel_id,

            message_id

        )


        if result.get("ok"):

            print(

                "✅ TRACK SENT",

                "user=",
                user_id,

                "mood=",
                mood,

                "message=",
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

            "❌ COPY FAILED",

            channel_id,

            message_id,

            result

        )


    send_message(

        chat_id,

        "❌ Track ပို့မရပါ။\n\n"

        "Bot ကို mood channel တွေအားလုံးမှာ "
        "admin ထားထားတာ သေချာစစ်ပါ။",

        mood_menu()

    )


# =========================================================
# BACKGROUND SEND
# =========================================================

def background_send(
    chat_id,
    user_id,
    mood
):

    try:

        send_mood_track(

            chat_id,

            user_id,

            mood

        )

    except Exception as e:

        print(

            "BACKGROUND SEND ERROR:",

            repr(e)

        )


# =========================================================
# USERS COUNT
# =========================================================

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


# =========================================================
# TRACK COUNT
# =========================================================

def get_track_counts():

    with music_lock:

        return {

            mood:
                len(
                    MUSIC_DATABASE[mood]
                )

            for mood in MOODS

        }


# =========================================================
# ADMIN
# =========================================================

def is_admin(
    user_id
):

    if not ADMIN_USER_ID:

        return False


    return str(user_id) == str(
        ADMIN_USER_ID
    )


# =========================================================
# STATS
# =========================================================

def send_stats(
    chat_id,
    user_id
):

    if not is_admin(
        user_id
    ):

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

        f"Scanner: "
        f"{'ONLINE' if telethon_ready else 'OFFLINE'}",

        ""

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


# =========================================================
# HEALTH
# =========================================================

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

    return {

        "status": "ok",

        "scanner":
            telethon_ready,

        "tracks":
            sum(
                len(
                    MUSIC_DATABASE[mood]
                )

                for mood in MOODS
            )

    }


# =========================================================
# WEBHOOK
# =========================================================

@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    try:

        update = request.get_json(
            silent=True
        )


        if not update:

            return "OK"


        # =================================================
        # CALLBACK
        # =================================================

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


            if not chat_id or not user_id:

                answer_callback(

                    callback_id,

                    "Chat error"

                )

                return "OK"


            register_user(
                user
            )


            # =================================================
            # MOOD
            # =================================================

            if data.startswith(
                "mood_"
            ):

                mood = data[
                    5:
                ]


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

                    target=background_send,

                    args=(

                        chat_id,

                        user_id,

                        mood

                    ),

                    daemon=True

                ).start()


                return "OK"


            # =================================================
            # NEXT
            # =================================================

            if data == "next_music":

                answer_callback(

                    callback_id,

                    "🔀 Finding next..."

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

                    return "OK"


                threading.Thread(

                    target=background_send,

                    args=(

                        chat_id,

                        user_id,

                        mood

                    ),

                    daemon=True

                ).start()


                return "OK"


            # =================================================
            # CHANGE MOOD
            # =================================================

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


        # =================================================
        # NORMAL MESSAGE
        # =================================================

        message = update.get(
            "message"
        )


        if message:

            chat = message.get(
                "chat",
                {}
            )


            chat_id = chat.get(
                "id"
            )


            user = message.get(
                "from"
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


            # =================================================
            # START
            # =================================================

            if text == "/start":

                send_message(

                    chat_id,

                    "🎧 NOT YOUR VIBE MUSIC\n\n"

                    "Welcome! 🔥\n\n"

                    "Mood တစ်ခုရွေးပြီး "
                    "အဲ့ဒီ mood channel ထဲက "
                    "random track ကို "
                    "နားထောင်ပါ 👇",

                    mood_menu()

                )

                return "OK"


            # =================================================
            # MOOD
            # =================================================

            if text == "/mood":

                send_message(

                    chat_id,

                    "🎧 Choose your mood 👇",

                    mood_menu()

                )

                return "OK"


            # =================================================
            # NEXT
            # =================================================

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

                    target=background_send,

                    args=(

                        chat_id,

                        chat_id,

                        mood

                    ),

                    daemon=True

                ).start()


                return "OK"


            # =================================================
            # STATS
            # =================================================

            if text == "/stats":

                send_stats(

                    chat_id,

                    chat_id

                )

                return "OK"


            # =================================================
            # USERS
            # =================================================

            if text == "/users":

                send_stats(

                    chat_id,

                    chat_id

                )

                return "OK"


            # =================================================
            # HELP
            # =================================================

            if text == "/help":

                send_message(

                    chat_id,

                    "🎧 NOT YOUR VIBE MUSIC BOT\n\n"

                    "/start → Start\n"
                    "/mood → Mood menu\n"
                    "/next → Next track\n"
                    "/stats → Admin stats\n"
                    "/help → Help"

                )

                return "OK"


        return "OK"


    except Exception as e:

        print(

            "WEBHOOK ERROR:",

            repr(e)

        )

        # IMPORTANT:
        # Telegram ကို 500 မပေးဘူး

        return "OK"


# =========================================================
# SET WEBHOOK
# =========================================================

def setup_webhook():

    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN missing"
        )

        return


    if not RENDER_URL:

        print(
            "❌ RENDER_EXTERNAL_URL missing"
        )

        return


    url = (

        RENDER_URL.rstrip("/")

        + "/webhook"

    )


    print(
        "Setting webhook:",
        url
    )


    result = telegram(

        "setWebhook",

        {

            "url": url,

            "allowed_updates": [

                "message",

                "callback_query"

            ],

            "max_connections": 40,

            "drop_pending_updates": True

        },

        timeout=20

    )


    print(
        "WEBHOOK RESULT:",
        result
    )


# =========================================================
# WEBHOOK INFO
# =========================================================

def webhook_info():

    result = telegram(

        "getWebhookInfo",

        {},

        timeout=10

    )


    print(
        "WEBHOOK INFO:",
        result
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    print(
        "=========================================="
    )

    print(
        "🎧 NOT YOUR VIBE MUSIC BOT"
    )

    print(
        "=========================================="
    )

    print(
        "Admin configured:",
        bool(ADMIN_USER_ID)
    )

    print(
        "API ID configured:",
        bool(API_ID)
    )

    print(
        "API HASH configured:",
        bool(API_HASH)
    )

    print(
        "=========================================="
    )


    # =====================================================
    # START TELETHON SCANNER
    # =====================================================

    start_scanner()


    # =====================================================
    # WEBHOOK
    # =====================================================

    setup_webhook()

    time.sleep(1)

    webhook_info()


    # =====================================================
    # FLASK
    # =====================================================

    port = int(

        os.getenv(
            "PORT",
            "10000"
        )

    )


    print(
        "=========================================="
    )

    print(
        "🚀 BOT SERVER STARTING"
    )

    print(
        "=========================================="
    )


    app.run(

        host="0.0.0.0",

        port=port,

        threaded=True

)
