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
# ENVIRONMENT
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

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
# MEMORY MUSIC DATABASE
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


# =========================================================
# TELETHON
# =========================================================

telethon_client = None

telethon_loop = None


# =========================================================
# BOT API
# =========================================================

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


    try:

        response = http.post(

            f"{BOT_API}/{method}",

            json=data or {},

            timeout=timeout

        )

        result = response.json()

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
        data
    )


# =========================================================
# CALLBACK
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
# USER REGISTER
# =========================================================

def register_user(user):

    if not user:

        return


    user_id = user.get("id")

    if not user_id:

        return


    with state_lock:

        if user_id not in USER_STATE:

            USER_STATE[user_id] = {

                "mood": None,

                "first_seen": int(
                    time.time()
                ),

                "last_seen": int(
                    time.time()
                )

            }

        else:

            USER_STATE[user_id][
                "last_seen"
            ] = int(time.time())


# =========================================================
# MUSIC CHECK
# =========================================================

def is_music_message(message):

    if message.audio:

        return True


    if message.document:

        mime = getattr(

            message.document,

            "mime_type",

            ""

        ) or ""


        if mime.startswith(
            "audio/"
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
            )

            print(
                "🎵 NEW TRACK:",
                MOOD_NAMES[mood],
                channel_id,
                message_id
            )


# =========================================================
# SCAN CHANNEL
# =========================================================

async def scan_channel(
    mood,
    channel
):

    print()
    print(
        "🔎 SCANNING:",
        MOOD_NAMES[mood],
        channel
    )


    try:

        entity = await telethon_client.get_entity(
            channel
        )

    except Exception as e:

        print(
            "❌ CHANNEL ACCESS ERROR:",
            mood,
            repr(e)
        )

        return


    count = 0


    try:

        async for message in telethon_client.iter_messages(
            entity
        ):

            if not is_music_message(
                message
            ):

                continue


            add_music(

                mood,

                entity.id,

                message.id

            )

            count += 1


        print(
            "✅",
            MOOD_NAMES[mood],
            "→",
            count
        )


    except Exception as e:

        print(
            "❌ SCAN ERROR:",
            mood,
            repr(e)
        )


# =========================================================
# SCAN ALL
# =========================================================

async def scan_all_channels():

    print()
    print(
        "======================================"
    )

    print(
        "🎧 NOT YOUR VIBE MUSIC SCANNER"
    )

    print(
        "======================================"
    )


    for mood in MOODS:

        await scan_channel(

            mood,

            MOOD_CHANNELS[mood]

        )


    print()

    print(
        "======================================"
    )

    print(
        "🎵 SCAN COMPLETE"
    )

    print(
        "======================================"
    )


    with music_lock:

        total = 0

        for mood in MOODS:

            count = len(
                MUSIC[mood]
            )

            total += count

            print(
                MOOD_NAMES[mood],
                "→",
                count
            )


        print(
            "TOTAL →",
            total
        )


# =========================================================
# TELETHON NEW MUSIC
# =========================================================

async def new_channel_post(
    event
):

    try:

        message = event.message

        chat = await event.get_chat()

        chat_id = str(
            chat.id
        )


        mood = None


        for candidate_mood, channel in MOOD_CHANNELS.items():

            try:

                entity = await telethon_client.get_entity(
                    channel
                )

                if str(entity.id) == chat_id:

                    mood = candidate_mood

                    break

            except Exception:

                continue


        if not mood:

            return


        if not is_music_message(
            message
        ):

            return


        add_music(

            mood,

            chat.id,

            message.id

        )


    except Exception as e:

        print(
            "NEW CHANNEL POST ERROR:",
            repr(e)
        )


# =========================================================
# START TELETHON
# =========================================================

def start_telethon():

    global telethon_client
    global telethon_loop


    if not TELETHON_SESSION:

        raise RuntimeError(
            "TELETHON_SESSION missing"
        )


    if not TELETHON_API_ID:

        raise RuntimeError(
            "TELETHON_API_ID missing"
        )


    if not TELETHON_API_HASH:

        raise RuntimeError(
            "TELETHON_API_HASH missing"
        )


    telethon_loop = asyncio.new_event_loop()

    asyncio.set_event_loop(
        telethon_loop
    )


    telethon_client = TelegramClient(

        StringSession(
            TELETHON_SESSION
        ),

        int(TELETHON_API_ID),

        TELETHON_API_HASH

    )


    telethon_client.add_event_handler(

        new_channel_post,

        events.NewMessage(
            chats=list(
                MOOD_CHANNELS.values()
            )
        )

    )


    async def runner():

        print(
            "🔐 Connecting Telegram account..."
        )

        await telethon_client.start()

        me = await telethon_client.get_me()

        print(
            "✅ TELETHON LOGIN:",
            me.id
        )


        await scan_all_channels()


        print(
            "✅ TELETHON READY"
        )


        await telethon_client.run_until_disconnected()


    telethon_loop.run_until_complete(
        runner()
    )


# =========================================================
# GET RANDOM TRACK
# =========================================================

def get_random_track(
    user_id,
    mood
):

    with music_lock:

        tracks = list(
            MUSIC.get(
                mood,
                []
            )
        )


    if not tracks:

        return None


    with state_lock:

        history = USER_HISTORY.setdefault(
            user_id,
            []
        )


        recent = set(
            history[-30:]
        )


    available = [

        track

        for track in tracks

        if track not in recent

    ]


    if not available:

        available = tracks


    return random.choice(
        available
    )


# =========================================================
# SAVE USER HISTORY
# =========================================================

def save_history(
    user_id,
    mood,
    track
):

    with state_lock:

        history = USER_HISTORY.setdefault(

            user_id,

            []

        )


        history.append(
            track
        )


        if len(history) > 100:

            del history[
                :-100
            ]


        USER_STATE.setdefault(

            user_id,

            {}

        )["mood"] = mood


# =========================================================
# SEND RANDOM MUSIC
# =========================================================

def send_random_music(
    chat_id,
    user_id,
    mood
):

    track = get_random_track(

        user_id,

        mood

    )


    if not track:

        send_message(

            chat_id,

            f"{MOOD_NAMES[mood]}\n\n"
            "⚠️ ဒီ mood ထဲမှာ music မတွေ့သေးပါ။\n\n"
            "Channel access / Telegram account "
            "permission ကို စစ်ပါ။",

            mood_menu()

        )

        return


    channel_id, message_id = track


    result = copy_music(

        chat_id,

        channel_id,

        message_id

    )


    if not result.get("ok"):

        print(
            "❌ COPY FAILED:",
            result
        )


        # Remove broken track
        with music_lock:

            if track in MUSIC[mood]:

                MUSIC[mood].remove(
                    track
                )


        # Try another
        send_random_music(

            chat_id,

            user_id,

            mood

        )

        return


    save_history(

        user_id,

        mood,

        track

    )


    send_message(

        chat_id,

        f"{MOOD_NAMES[mood]}\n\n"
        "🎧 Enjoy your music! 🔥",

        music_buttons()

    )


# =========================================================
# ADMIN
# =========================================================

def is_admin(
    user_id
):

    return (

        ADMIN_USER_ID

        and

        str(user_id)
        == str(ADMIN_USER_ID)

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


    with music_lock:

        counts = {

            mood: len(
                MUSIC[mood]
            )

            for mood in MOODS

        }


    with state_lock:

        users = len(
            USER_STATE
        )


    total = sum(
        counts.values()
    )


    lines = [

        "📊 NOT YOUR VIBE MUSIC BOT",

        "",

        f"👥 Users: {users}",

        f"🎵 Total tracks: {total}",

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
# HANDLE MESSAGE
# =========================================================

def handle_message(
    message
):

    chat = message.get(
        "chat",
        {}
    )


    user = message.get(
        "from",
        {}
    )


    chat_id = chat.get(
        "id"
    )


    user_id = user.get(
        "id"
    )


    if not chat_id or not user_id:

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


    if text == "/start":

        send_message(

            chat_id,

            "🎧 NOT YOUR VIBE MUSIC\n\n"
            "Welcome! 🔥\n\n"
            "Choose your mood 👇",

            mood_menu()

        )

        return


    if text == "/mood":

        send_message(

            chat_id,

            "🎧 Choose your mood 👇",

            mood_menu()

        )

        return


    if text == "/stats":

        send_stats(

            chat_id,

            user_id

        )

        return


    if text == "/users":

        send_stats(

            chat_id,

            user_id

        )

        return


    if text == "/next":

        with state_lock:

            mood = USER_STATE.get(

                user_id,

                {}

            ).get(
                "mood"
            )


        if not mood:

            send_message(

                chat_id,

                "🎧 အရင်ဆုံး Mood ရွေးပါ 👇",

                mood_menu()

            )

            return


        send_random_music(

            chat_id,

            user_id,

            mood

        )

        return


    if text == "/help":

        send_message(

            chat_id,

            "🎧 NOT YOUR VIBE MUSIC BOT\n\n"

            "/start → Start\n"
            "/mood → Mood menu\n"
            "/next → Next track\n"
            "/stats → Admin statistics\n"
            "/users → Admin statistics\n"
            "/help → Help"

        )

        return


# =========================================================
# HANDLE CALLBACK
# =========================================================

def handle_callback(
    callback
):

    callback_id = callback.get(
        "id"
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


    user_id = user.get(
        "id"
    )


    chat_id = chat.get(
        "id"
    )


    data = callback.get(
        "data",
        ""
    )


    if not chat_id or not user_id:

        return


    register_user(
        user
    )


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

            return


        with state_lock:

            USER_STATE.setdefault(

                user_id,

                {}

            )["mood"] = mood


        answer_callback(

            callback_id,

            f"{MOOD_NAMES[mood]} ✓"

        )


        send_random_music(

            chat_id,

            user_id,

            mood

        )

        return


    if data == "next_music":

        answer_callback(

            callback_id,

            "🔀 Finding next..."

        )


        with state_lock:

            mood = USER_STATE.get(

                user_id,

                {}

            ).get(
                "mood"
            )


        if not mood:

            send_message(

                chat_id,

                "🎧 အရင်ဆုံး Mood ရွေးပါ 👇",

                mood_menu()

            )

            return


        send_random_music(

            chat_id,

            user_id,

            mood

        )

        return


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


# =========================================================
# BOT POLLING
# =========================================================

def bot_polling():

    print(
        "🤖 BOT POLLING STARTED"
    )


    # Remove old webhook
    telegram(
        "deleteWebhook",
        {
            "drop_pending_updates": True
        }
    )


    offset = 0


    while True:

        try:

            result = telegram(

                "getUpdates",

                {
                    "offset": offset,

                    "timeout": 50,

                    "allowed_updates": [

                        "message",

                        "callback_query"

                    ]

                },

                timeout=65

            )


            if not result.get(
                "ok"
            ):

                time.sleep(5)

                continue


            updates = result.get(
                "result",
                []
            )


            for update in updates:

                offset = (

                    update["update_id"]
                    + 1

                )


                try:

                    if "message" in update:

                        handle_message(

                            update["message"]

                        )


                    elif "callback_query" in update:

                        handle_callback(

                            update[
                                "callback_query"
                            ]

                        )


                except Exception as e:

                    print(

                        "UPDATE ERROR:",

                        repr(e)

                    )


        except Exception as e:

            print(

                "POLLING ERROR:",

                repr(e)

            )


            time.sleep(5)


# =========================================================
# HEALTH
# =========================================================

@app.route("/")
def home():

    return (
        "🎧 NOT YOUR VIBE MUSIC BOT ONLINE"
    )


@app.route("/health")
def health():

    return "OK"


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    print(
        "======================================"
    )

    print(
        "🎧 NOT YOUR VIBE MUSIC BOT"
    )

    print(
        "======================================"
    )


    # -------------------------------------
    # Validate ENV
    # -------------------------------------

    required = {

        "BOT_TOKEN": BOT_TOKEN,

        "ADMIN_USER_ID": ADMIN_USER_ID,

        "TELETHON_API_ID": TELETHON_API_ID,

        "TELETHON_API_HASH": TELETHON_API_HASH,

        "TELETHON_SESSION": TELETHON_SESSION,

    }


    missing = [

        name

        for name, value
        in required.items()

        if not value

    ]


    if missing:

        print(
            "❌ MISSING ENV:",
            ", ".join(missing)
        )

        raise SystemExit(1)


    # -------------------------------------
    # Telethon
    # -------------------------------------

    telethon_thread = threading.Thread(

        target=start_telethon,

        daemon=True

    )

    telethon_thread.start()


    # -------------------------------------
    # Bot
    # -------------------------------------

    bot_thread = threading.Thread(

        target=bot_polling,

        daemon=True

    )

    bot_thread.start()


    # -------------------------------------
    # Flask
    # -------------------------------------

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

        threaded=True

)
