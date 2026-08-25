import os
import random
import threading
import requests

from flask import Flask, request
from openai import OpenAI


# =========================================================
# APP
# =========================================================

app = Flask(__name__)


# =========================================================
# ENVIRONMENT
# =========================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

CHANNEL_USERNAME = "@notyourvibemp3collection"

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# =========================================================
# HTTP SESSION
# Faster than creating a new connection every request
# =========================================================

session = requests.Session()


# =========================================================
# OPENAI
# AI is ONLY for /ai
# Mood buttons do NOT depend on AI
# =========================================================

ai_client = None

if OPENAI_API_KEY:

    try:

        ai_client = OpenAI(
            api_key=OPENAI_API_KEY
        )

        print("OPENAI: CONNECTED")

    except Exception as e:

        print(
            "OPENAI ERROR:",
            e
        )


# =========================================================
# MOODS
# =========================================================

MOODS = [
    "sad",
    "love",
    "chill",
    "hype",
    "dark",
    "energetic",
    "night",
    "melodic"
]


MOOD_NAMES = {

    "sad": "😢 SAD",

    "love": "❤️ LOVE",

    "chill": "🌙 CHILL",

    "hype": "🔥 HYPE",

    "dark": "🖤 DARK",

    "energetic": "⚡ ENERGETIC",

    "night": "🚗 NIGHT DRIVE",

    "melodic": "🌌 MELODIC"

}


# =========================================================
# MOOD MUSIC DATABASE
#
# IMPORTANT:
# These are direct Telegram message IDs.
# NO AI ANALYSIS.
# =========================================================

MOOD_MUSIC = {

    # =====================================================
    # SAD + CHILL + MELODIC
    # =====================================================

    "sad": [

        543,
        1594,
        1844,
        2105,
        2621,
        2406,
        2316,
        2286,
        2713,
        2553,
        553,
        557

    ],


    "love": [

        2366,
        2839,
        2825,
        2236,
        2246,
        2226,
        2216

    ],


    "chill": [

        543,
        1594,
        1844,
        2105,
        2621,
        2406,
        2316,
        2286,
        2713,
        2553,
        553,
        557,

        2849,
        2859,
        2256,
        2446,
        2296,
        2196

    ],


    # =====================================================
    # HYPE + ENERGETIC
    # =====================================================

    "hype": [

        2800,
        2703,
        2649,
        2639,
        2630,
        2572,
        2346,
        2276,
        2266,
        2216,
        2206,
        1009,
        538,

        2326,
        2356,
        2386,
        2582,
        2563,
        2536,
        2526,
        2466,
        2456,
        2426,
        2416,
        2296

    ],


    "energetic": [

        2800,
        2703,
        2649,
        2639,
        2630,
        2572,
        2346,
        2276,
        2266,
        2216,
        2206,
        1009,
        538,

        1603,
        2752,
        2742,
        2732,
        2722,
        2693,
        2675,
        2602,
        2592,
        2516,
        2486,
        2476,
        2396,
        2376,
        2336,
        2186,
        2176,
        2156,

        2326,
        2356,
        2386,
        2582,
        2563,
        2536,
        2526,
        2466,
        2456,
        2426,
        2416,
        2296

    ],


    # =====================================================
    # DARK + ENERGETIC
    # =====================================================

    "dark": [

        1603,
        2752,
        2742,
        2732,
        2722,
        2693,
        2675,
        2602,
        2592,
        2516,
        2486,
        2476,
        2396,
        2376,
        2336,
        2186,
        2176,
        2156

    ],


    # =====================================================
    # NIGHT DRIVE + MELODIC
    # =====================================================

    "night": [

        2146,
        2166,
        2306,
        2506,
        2436

    ],


    "melodic": [

        543,
        1594,
        1844,
        2105,
        2621,
        2406,
        2316,
        2286,
        2713,
        2553,
        553,
        557,

        2146,
        2166,
        2306,
        2506,
        2436

    ]

}


# =========================================================
# REMOVE DUPLICATES
# =========================================================

for mood in MOODS:

    MOOD_MUSIC[mood] = list(
        dict.fromkeys(
            MOOD_MUSIC[mood]
        )
    )


# =========================================================
# ALL MUSIC
# Used ONLY for Next button
# =========================================================

ALL_MUSIC = []

for mood in MOODS:

    ALL_MUSIC.extend(
        MOOD_MUSIC[mood]
    )


ALL_MUSIC = list(
    dict.fromkeys(
        ALL_MUSIC
    )
)


# =========================================================
# TELEGRAM API
# =========================================================

def telegram(
    method,
    data,
    timeout=10
):

    try:

        response = session.post(

            f"{TELEGRAM_API}/{method}",

            json=data,

            timeout=timeout

        )

        result = response.json()

        print(
            method,
            result.get(
                "ok"
            )
        )

        if not result.get("ok"):

            print(
                "TELEGRAM ERROR:",
                result
            )

        return result


    except Exception as e:

        print(
            "TELEGRAM REQUEST ERROR:",
            method,
            e
        )

        return {

            "ok":
                False,

            "description":
                str(e)

        }


# =========================================================
# SEND MESSAGE
# =========================================================

def send_message(
    chat_id,
    text,
    reply_markup=None
):

    data = {

        "chat_id":
            chat_id,

        "text":
            text

    }


    if reply_markup:

        data[
            "reply_markup"
        ] = reply_markup


    return telegram(
        "sendMessage",
        data
    )


# =========================================================
# CALLBACK ANSWER
#
# Must be called quickly
# =========================================================

def answer_callback(
    callback_id,
    text=""
):

    return telegram(

        "answerCallbackQuery",

        {

            "callback_query_id":
                callback_id,

            "text":
                text

        },

        timeout=5

    )


# =========================================================
# COPY MUSIC
# =========================================================

def copy_music(
    chat_id,
    message_id
):

    return telegram(

        "copyMessage",

        {

            "chat_id":
                chat_id,

            "from_chat_id":
                CHANNEL_USERNAME,

            "message_id":
                message_id

        },

        timeout=15

    )


# =========================================================
# MOOD MENU
# =========================================================

def mood_menu():

    return {

        "inline_keyboard": [

            [

                {
                    "text":
                        "😢 Sad",

                    "callback_data":
                        "mood_sad"
                },

                {
                    "text":
                        "❤️ Love",

                    "callback_data":
                        "mood_love"
                }

            ],

            [

                {
                    "text":
                        "🌙 Chill",

                    "callback_data":
                        "mood_chill"
                },

                {
                    "text":
                        "🔥 Hype",

                    "callback_data":
                        "mood_hype"
                }

            ],

            [

                {
                    "text":
                        "🖤 Dark",

                    "callback_data":
                        "mood_dark"
                },

                {
                    "text":
                        "⚡ Energetic",

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
                    "text":
                        "🌌 Melodic",

                    "callback_data":
                        "mood_melodic"
                }

            ],

            [

                {
                    "text":
                        "🤖 Ask AI",

                    "callback_data":
                        "ai_help"
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

            ],

            [

                {
                    "text":
                        "🤖 Ask AI",

                    "callback_data":
                        "ai_help"
                }

            ]

        ]

    }


# =========================================================
# SEND MOOD MUSIC
#
# NO AI
# NO ANALYSIS
# DIRECT MESSAGE ID
# =========================================================

def send_mood_music(
    chat_id,
    mood
):

    songs = list(
        MOOD_MUSIC.get(
            mood,
            []
        )
    )


    if not songs:

        send_message(

            chat_id,

            f"{MOOD_NAMES.get(mood, mood)}\n\n"
            "⚠️ ဒီ mood collection ထဲမှာ "
            "သီချင်းမရှိသေးပါ။"

        )

        return


    # Random order
    random.shuffle(
        songs
    )


    print(
        "MOOD:",
        mood,
        "SONGS:",
        songs
    )


    # =====================================================
    # Try songs until one works
    # =====================================================

    for message_id in songs:

        result = copy_music(

            chat_id,

            message_id

        )


        if result.get("ok"):

            print(
                "MUSIC SENT:",
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

            "COPY FAILED:",
            message_id,
            result.get(
                "description"
            )

        )


    # =====================================================
    # Nothing worked
    # =====================================================

    send_message(

        chat_id,

        f"{MOOD_NAMES[mood]}\n\n"
        "❌ ဒီ collection ထဲက "
        "သီချင်းတွေကို copy မလုပ်နိုင်ပါ။\n\n"
        "Bot က channel ကို access လုပ်နိုင်/မလုပ်နိုင် "
        "စစ်ပေးပါ။"

    )


# =========================================================
# NEXT MUSIC
# =========================================================

def send_next_music(
    chat_id
):

    songs = list(
        ALL_MUSIC
    )


    if not songs:

        send_message(

            chat_id,

            "❌ Music collection empty."

        )

        return


    random.shuffle(
        songs
    )


    for message_id in songs:

        result = copy_music(

            chat_id,

            message_id

        )


        if result.get("ok"):

            send_message(

                chat_id,

                "🔀 Next track 👇",

                music_buttons()

            )

            return


    send_message(

        chat_id,

        "❌ Couldn't send next track."

    )


# =========================================================
# AI CHAT
#
# AI is completely separate from mood buttons.
# =========================================================

def ask_ai(
    text
):

    if not ai_client:

        return (

            "⚠️ AI is not connected.\n\n"
            "You can still use all mood buttons."

        )


    try:

        response = ai_client.responses.create(

            model="gpt-5-mini",

            instructions="""

You are NOT YOUR VIBE Music Assistant.

Understand Burmese and English.

Available moods:

😢 Sad
❤️ Love
🌙 Chill
🔥 Hype
🖤 Dark
⚡ Energetic
🚗 Night Drive
🌌 Melodic

SAD means emotional,
melancholic, heartbreak,
lonely, nostalgic music.

Give short useful answers.

Do not invent songs.

""",

            input=text

        )


        return response.output_text


    except Exception as e:

        print(
            "AI ERROR:",
            e
        )


        return (
            "⚠️ AI temporarily unavailable."
        )


# =========================================================
# BACKGROUND TASK
#
# Webhook responds immediately.
# Music sending happens separately.
# =========================================================

def background_mood(
    chat_id,
    mood
):

    try:

        send_mood_music(
            chat_id,
            mood
        )

    except Exception as e:

        print(
            "BACKGROUND MOOD ERROR:",
            e
        )


def background_next(
    chat_id
):

    try:

        send_next_music(
            chat_id
        )

    except Exception as e:

        print(
            "BACKGROUND NEXT ERROR:",
            e
        )


# =========================================================
# HOME
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return (
        "NOT YOUR VIBE MUSIC BOT - ONLINE"
    )


# =========================================================
# HEALTH
# =========================================================

@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return "OK"


# =========================================================
# WEBHOOK
# =========================================================

@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    update = request.get_json(
        silent=True
    )


    if not update:

        return "OK"


    # =====================================================
    # CHANNEL POST
    #
    # We receive it only to log new songs.
    # Mood database is manually controlled above.
    # =====================================================

    channel_post = update.get(
        "channel_post"
    )


    if channel_post:

        chat = channel_post.get(
            "chat",
            {}
        )


        username = (

            chat.get(
                "username",
                ""
            )

            or ""

        ).lower()


        expected = (

            CHANNEL_USERNAME
            .replace(
                "@",
                ""
            )
            .lower()

        )


        if username == expected:

            message_id = (
                channel_post.get(
                    "message_id"
                )
            )


            caption = (

                channel_post.get(
                    "caption",
                    ""
                )

                or channel_post.get(
                    "text",
                    ""
                )

                or ""

            )


            print(
                "NEW CHANNEL POST:",
                message_id
            )


            print(
                "CAPTION:",
                caption
            )


        return "OK"


    # =====================================================
    # CALLBACK QUERY
    # =====================================================

    callback = update.get(
        "callback_query"
    )


    if callback:

        callback_id = (
            callback.get(
                "id"
            )
        )


        callback_message = (
            callback.get(
                "message",
                {}
            )
        )


        chat = (
            callback_message.get(
                "chat",
                {}
            )
        )


        chat_id = chat.get(
            "id"
        )


        data = (
            callback.get(
                "data",
                ""
            )
        )


        # =================================================
        # MOOD BUTTON
        # =================================================

        if data.startswith(
            "mood_"
        ):

            mood = data.replace(
                "mood_",
                "",
                1
            )


            if mood not in MOODS:

                answer_callback(

                    callback_id,

                    "Invalid mood"

                )

                return "OK"


            # =================================================
            # IMPORTANT:
            # Answer button immediately.
            # =================================================

            answer_callback(

                callback_id,

                f"{MOOD_NAMES[mood]} selected!"

            )


            # =================================================
            # Send music in background
            # =================================================

            threading.Thread(

                target=background_mood,

                args=(
                    chat_id,
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

                "🔀 Finding next track..."

            )


            threading.Thread(

                target=background_next,

                args=(
                    chat_id,
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

                "🎧 Choose a mood"

            )


            send_message(

                chat_id,

                "🎧 Choose your mood 👇",

                mood_menu()

            )


            return "OK"


        # =================================================
        # AI HELP
        # =================================================

        if data == "ai_help":

            answer_callback(

                callback_id,

                "🤖 Ask AI"

            )


            send_message(

                chat_id,

                "🤖 AI MUSIC ASSISTANT\n\n"
                "Tell me what you're feeling.\n\n"
                "Examples:\n"
                "• I'm sad tonight\n"
                "• I want emotional music\n"
                "• Give me night drive music\n"
                "• I want dark bass"

            )


            return "OK"


        return "OK"


    # =====================================================
    # NORMAL MESSAGE
    # =====================================================

    message = update.get(
        "message"
    )


    if message:

        chat_id = (
            message
            .get(
                "chat",
                {}
            )
            .get(
                "id"
            )
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
                "Choose your mood 👇",

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
        # AI
        # =================================================

        if text == "/ai":

            send_message(

                chat_id,

                "🤖 AI MUSIC ASSISTANT\n\n"
                "Tell me what you're feeling.\n\n"
                "Example:\n"
                "I want emotional music for a "
                "night drive."

            )

            return "OK"


        # =================================================
        # HELP
        # =================================================

        if text == "/help":

            send_message(

                chat_id,

                "🎧 NOT YOUR VIBE MUSIC BOT\n\n"
                "/start - Start\n"
                "/mood - Choose mood\n"
                "/ai - AI Assistant\n"
                "/help - Help"

            )

            return "OK"


        # =================================================
        # AI CHAT
        # =================================================

        if text:

            answer = ask_ai(
                text
            )


            send_message(

                chat_id,

                "🤖 AI Music Assistant\n\n"
                + answer,

                mood_menu()

            )


    return "OK"


# =========================================================
# WEBHOOK SETUP
# =========================================================

def setup_webhook():

    if not BOT_TOKEN:

        print(
            "ERROR: BOT_TOKEN missing"
        )

        return


    if not RENDER_URL:

        print(
            "ERROR: RENDER_EXTERNAL_URL missing"
        )

        return


    webhook_url = (
        f"{RENDER_URL}/webhook"
    )


    try:

        result = telegram(

            "setWebhook",

            {

                "url":
                    webhook_url,

                "allowed_updates": [

                    "message",

                    "callback_query",

                    "channel_post"

                ],

                "max_connections":
                    40,

                "drop_pending_updates":
                    False

            },

            timeout=15

        )


        print(
            "WEBHOOK:",
            result
        )


    except Exception as e:

        print(
            "WEBHOOK SETUP ERROR:",
            e
        )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    setup_webhook()


    port = int(

        os.environ.get(
            "PORT",
            10000
        )

    )


    print(
        "================================="
    )

    print(
        "NOT YOUR VIBE MUSIC BOT"
    )

    print(
        "STATUS: ONLINE"
    )

    print(
        "MUSIC COUNT:",
        len(ALL_MUSIC)
    )

    print(
        "SAD COUNT:",
        len(MOOD_MUSIC["sad"])
    )

    print(
        "LOVE COUNT:",
        len(MOOD_MUSIC["love"])
    )

    print(
        "CHILL COUNT:",
        len(MOOD_MUSIC["chill"])
    )

    print(
        "HYPE COUNT:",
        len(MOOD_MUSIC["hype"])
    )

    print(
        "DARK COUNT:",
        len(MOOD_MUSIC["dark"])
    )

    print(
        "ENERGETIC COUNT:",
        len(MOOD_MUSIC["energetic"])
    )

    print(
        "NIGHT COUNT:",
        len(MOOD_MUSIC["night"])
    )

    print(
        "MELODIC COUNT:",
        len(MOOD_MUSIC["melodic"])
    )

    print(
        "================================="
    )


    app.run(

        host="0.0.0.0",

        port=port,

        threaded=True

    )
