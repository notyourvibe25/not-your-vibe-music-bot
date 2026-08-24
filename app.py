import os
import random
import json
import requests

from flask import Flask, request
from openai import OpenAI


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
# OPENAI
# =========================================================

ai_client = None

if OPENAI_API_KEY:
    ai_client = OpenAI(
        api_key=OPENAI_API_KEY
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
# OLD CHANNEL MUSIC
# =========================================================

MUSIC_IDS = [
    2045, 1995, 1834, 2075, 2105, 2125, 2115,
    1864, 1874, 1844, 2095, 1905, 1975, 1925,
    1935, 1945, 1965, 2035, 1985, 2005, 1955,
    2025, 1915, 2015, 2055, 1895, 1885, 2065,

    1824, 1814, 1802, 1782, 1772, 1762, 1752,
    1739, 1729, 1711, 1701, 1692, 1643, 1632,
    1622, 1612, 1603, 1594, 1585, 1560, 1570,
    1549, 1544, 1539, 1534, 1529, 1524, 1514,
    1503, 1495, 1485, 1476, 1457, 1452, 1441,
    1391, 1379, 1369, 1359, 1348, 1336, 1326,
    1306, 1291, 1281, 1276, 1266, 1262, 1252,
    1251, 1241, 1237, 1231, 1221, 1217, 1207,
    1205, 1202, 1192, 1183, 1173, 1165, 1155,
    1150, 1140, 1130, 1119, 1117, 1093, 1017,
    985, 943, 948, 892, 855, 826, 784, 794, 762,
    696, 685, 675, 661, 650, 643
]


# Remove duplicates
MUSIC_IDS = list(dict.fromkeys(MUSIC_IDS))


# =========================================================
# MOOD DATABASE
# =========================================================

MOOD_MUSIC = {

    "sad": [],

    "love": [],

    "chill": [],

    "hype": [],

    "dark": [],

    "energetic": [],

    "night": [],

    "melodic": []
}


# =========================================================
# Put old songs into general pool
# =========================================================

for music_id in MUSIC_IDS:

    for mood in MOODS:

        MOOD_MUSIC[mood].append(
            music_id
        )


# =========================================================
# TELEGRAM REQUEST
# =========================================================

def telegram(method, data):

    try:

        response = requests.post(

            f"{TELEGRAM_API}/{method}",

            json=data,

            timeout=30
        )

        return response.json()

    except Exception as e:

        print(
            "Telegram error:",
            e
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
    reply_markup=None
):

    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:

        data["reply_markup"] = reply_markup

    return telegram(
        "sendMessage",
        data
    )


# =========================================================
# CALLBACK ANSWER
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
        }
    )


# =========================================================
# COPY CHANNEL MESSAGE
# =========================================================

def copy_music(
    chat_id,
    message_id
):

    return telegram(

        "copyMessage",

        {
            "chat_id": chat_id,

            "from_chat_id":
                CHANNEL_USERNAME,

            "message_id":
                message_id
        }
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
            ],

            [
                {
                    "text": "🤖 Ask AI",
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
            ],

            [
                {
                    "text": "🤖 Ask AI",
                    "callback_data":
                        "ai_help"
                }
            ]
        ]
    }


# =========================================================
# AI MOOD CLASSIFIER
# =========================================================

def classify_music(text):

    if not ai_client:

        return ["melodic"]


    try:

        response = ai_client.responses.create(

            model="gpt-5-mini",

            instructions="""
You are a music mood classifier.

Choose one or more moods from ONLY these:

sad
love
chill
hype
dark
energetic
night
melodic

Return ONLY a JSON array.

Example:
["hype","energetic"]

Do not write explanations.
""",

            input=text
        )


        result_text = response.output_text.strip()


        moods = json.loads(
            result_text
        )


        valid = []

        for mood in moods:

            if mood in MOODS:

                if mood not in valid:

                    valid.append(
                        mood
                    )


        if valid:

            return valid


    except Exception as e:

        print(
            "AI classifier error:",
            e
        )


    return ["melodic"]


# =========================================================
# AI CHAT
# =========================================================

def ask_ai(text):

    if not ai_client:

        return (
            "⚠️ AI is not connected yet."
        )


    try:

        response = ai_client.responses.create(

            model="gpt-5-mini",

            instructions="""
You are NOT YOUR VIBE MUSIC AI.

You help users discover EDM and electronic music.

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

Give concise, friendly music recommendations.

Do not invent specific songs from the user's
channel unless they are provided to you.
""",

            input=text
        )


        return response.output_text


    except Exception as e:

        print(
            "AI chat error:",
            e
        )

        return (
            "❌ AI error. Please try again."
        )


# =========================================================
# ADD NEW CHANNEL SONG
# =========================================================

def add_new_channel_song(
    message_id,
    caption
):

    if message_id in MUSIC_IDS:

        return


    MUSIC_IDS.append(
        message_id
    )


    moods = classify_music(
        caption
    )


    for mood in moods:

        if message_id not in MOOD_MUSIC[mood]:

            MOOD_MUSIC[mood].append(
                message_id
            )


    print(
        "NEW SONG:",
        message_id
    )

    print(
        "CAPTION:",
        caption
    )

    print(
        "AI MOODS:",
        moods
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
        "NOT YOUR VIBE Music Bot is running!"
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
    # =====================================================

    channel_post = update.get(
        "channel_post"
    )


    if channel_post:

        chat = channel_post.get(
            "chat",
            {}
        )


        username = chat.get(
            "username",
            ""
        )


        if username.lower() == (
            CHANNEL_USERNAME
            .replace("@", "")
            .lower()
        ):

            message_id = channel_post.get(
                "message_id"
            )


            caption = (
                channel_post.get(
                    "caption",
                    ""
                )
                or
                channel_post.get(
                    "text",
                    ""
                )
                or
                "New music"
            )


            add_new_channel_song(
                message_id,
                caption
            )


        return "OK"


    # =====================================================
    # NORMAL USER MESSAGE
    # =====================================================

    message = update.get(
        "message"
    )


    if message:

        chat_id = message["chat"]["id"]


        text = message.get(
            "text",
            ""
        ).strip()


        # =================================================
        # START
        # =================================================

        if text == "/start":

            send_message(

                chat_id,

                "🎧 NOT YOUR VIBE MUSIC\n\n"
                "Welcome! 🔥\n\n"
                "Choose your mood below "
                "or talk to AI. 👇",

                mood_menu()
            )


        # =================================================
        # MOOD
        # =================================================

        elif text == "/mood":

            send_message(

                chat_id,

                "🎧 Choose your mood 👇",

                mood_menu()
            )


        # =================================================
        # AI
        # =================================================

        elif text == "/ai":

            send_message(

                chat_id,

                "🤖 AI MUSIC ASSISTANT\n\n"

                "Tell me what you're feeling.\n\n"

                "Examples:\n"
                "• I'm sad tonight\n"
                "• Give me hype music\n"
                "• Music for night driving\n"
                "• I want dark bass\n"
                "• Emotional future bass"
            )


        # =================================================
        # HELP
        # =================================================

        elif text == "/help":

            send_message(

                chat_id,

                "🎧 NOT YOUR VIBE MUSIC BOT\n\n"

                "/start - Start\n"
                "/mood - Choose mood\n"
                "/ai - Talk to AI\n"
                "/help - Help"
            )


        # =================================================
        # AI CHAT
        # =================================================

        elif text:

            response = ask_ai(
                text
            )


            send_message(

                chat_id,

                "🤖 AI Music Assistant\n\n"
                + response,

                mood_menu()
            )


    # =====================================================
    # CALLBACK
    # =====================================================

    callback = update.get(
        "callback_query"
    )


    if callback:

        callback_id = callback["id"]


        callback_message = callback.get(
            "message",
            {}
        )


        chat_id = (
            callback_message
            .get("chat", {})
            .get("id")
        )


        data = callback.get(
            "data",
            ""
        )


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

                "Example:\n"
                "I want something emotional "
                "for a night drive."
            )


        # =================================================
        # CHANGE MOOD
        # =================================================

        elif data == "change_mood":

            answer_callback(
                callback_id,
                "Choose another mood"
            )


            send_message(

                chat_id,

                "🎧 Choose your mood 👇",

                mood_menu()
            )


        # =================================================
        # NEXT
        # =================================================

        elif data == "next_music":

            answer_callback(

                callback_id,

                "🔀 Finding another track..."
            )


            # Collect all available songs

            available = list(
                dict.fromkeys(
                    MUSIC_IDS
                )
            )


            if available:

                message_id = random.choice(
                    available
                )


                result = copy_music(

                    chat_id,

                    message_id
                )


                print(
                    "NEXT:",
                    message_id,
                    result
                )


                if result.get("ok"):

                    send_message(

                        chat_id,

                        "🔀 Next track 👇",

                        music_buttons()
                    )


                else:

                    send_message(

                        chat_id,

                        "❌ Couldn't send the track."
                    )


        # =================================================
        # MOOD
        # =================================================

        elif data.startswith(
            "mood_"
        ):

            mood = data.replace(
                "mood_",
                ""
            )


            answer_callback(

                callback_id,

                f"{MOOD_NAMES.get(mood)} selected!"
            )


            songs = MOOD_MUSIC.get(
                mood,
                []
            )


            if not songs:

                songs = MUSIC_IDS


            if songs:

                message_id = random.choice(
                    songs
                )


                result = copy_music(

                    chat_id,

                    message_id
                )


                print(
                    "MOOD:",
                    mood,
                    "MESSAGE:",
                    message_id,
                    "RESULT:",
                    result
                )


                if result.get("ok"):

                    send_message(

                        chat_id,

                        f"{MOOD_NAMES.get(mood)}\n\n"
                        "🎧 Enjoy your music! 🔥",

                        music_buttons()
                    )


                else:

                    send_message(

                        chat_id,

                        "❌ Couldn't send the music."
                    )


    return "OK"


# =========================================================
# WEBHOOK SETUP
# =========================================================

if BOT_TOKEN and RENDER_URL:

    webhook_url = (
        f"{RENDER_URL}/webhook"
    )


    try:

        response = requests.post(

            f"{TELEGRAM_API}/setWebhook",

            json={
                "url": webhook_url
            },

            timeout=20
        )


        print(
            "Webhook:",
            response.text
        )


    except Exception as e:

        print(
            "Webhook Error:",
            e
        )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )


    app.run(

        host="0.0.0.0",

        port=port
)
