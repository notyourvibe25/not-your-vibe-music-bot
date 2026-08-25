import os
import random
import json
import requests

from flask import Flask, request
from openai import OpenAI


app = Flask(__name__)


# =========================================================
# ENV
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
    try:
        ai_client = OpenAI(
            api_key=OPENAI_API_KEY
        )
        print("OPENAI: CONNECTED")
    except Exception as e:
        print("OPENAI ERROR:", e)


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
# YOUR EXISTING CHANNEL MESSAGE IDS
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
# FALLBACK POOL
#
# IMPORTANT:
# Every song is initially available as fallback.
# Therefore mood selection NEVER becomes empty.
# =========================================================

for message_id in MUSIC_IDS:

    for mood in MOODS:

        if message_id not in MOOD_MUSIC[mood]:

            MOOD_MUSIC[mood].append(
                message_id
            )


# =========================================================
# TELEGRAM API
# =========================================================

def telegram(method, data):

    try:

        response = requests.post(

            f"{TELEGRAM_API}/{method}",

            json=data,

            timeout=30
        )

        result = response.json()

        print(
            "TELEGRAM",
            method,
            result
        )

        return result

    except Exception as e:

        print(
            "TELEGRAM ERROR:",
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
            "callback_query_id":
                callback_id,

            "text":
                text
        }
    )


# =========================================================
# COPY MUSIC FROM CHANNEL
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
# AI CLASSIFY
# =========================================================

def classify_music(caption):

    if not ai_client:

        return ["melodic"]


    if not caption:

        return ["melodic"]


    try:

        response = ai_client.responses.create(

            model="gpt-5-mini",

            instructions="""

You are an EDM music mood classifier.

Read the music metadata/caption.

Choose the moods that genuinely match the song.

Available moods ONLY:

sad
love
chill
hype
dark
energetic
night
melodic

Rules:

- Do NOT classify every song as melodic.
- Emotional / melancholic / heartbreak / lonely
  should strongly favor sad.
- Romantic / love / vocal romance
  should favor love.
- Relaxed / atmospheric / soft
  should favor chill.
- Festival / party / big drop
  should favor hype or energetic.
- Heavy / aggressive / sinister / industrial
  should favor dark.
- Driving / powerful / high BPM
  can favor energetic.
- Night driving / neon / late night
  should favor night.
- Beautiful melody / euphoric / emotional melody
  can favor melodic.

Return ONLY a JSON array.

Example:

["sad","melodic"]

""",

            input=caption
        )


        raw = response.output_text.strip()

        print(
            "AI RAW:",
            raw
        )


        moods = json.loads(raw)


        if not isinstance(
            moods,
            list
        ):

            return ["melodic"]


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
            "AI CLASSIFY ERROR:",
            e
        )


    return ["melodic"]


# =========================================================
# ADD NEW SONG
# =========================================================

def add_new_channel_song(
    message_id,
    caption
):

    if not message_id:

        return


    if message_id in MUSIC_IDS:

        return


    MUSIC_IDS.append(
        message_id
    )


    # -----------------------------------------------------
    # ALWAYS ADD TO FALLBACK POOL
    # -----------------------------------------------------

    for mood in MOODS:

        if message_id not in MOOD_MUSIC[mood]:

            MOOD_MUSIC[mood].append(
                message_id
            )


    # -----------------------------------------------------
    # AI ANALYSIS
    # -----------------------------------------------------

    moods = classify_music(
        caption
    )


    print(
        "NEW CHANNEL SONG:",
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
# SEND RANDOM MUSIC
# =========================================================

def send_random_music(
    chat_id,
    mood=None
):

    # -----------------------------------------------------
    # Choose pool
    # -----------------------------------------------------

    if mood:

        songs = MOOD_MUSIC.get(
            mood,
            []
        )

    else:

        songs = MUSIC_IDS


    # -----------------------------------------------------
    # FINAL FALLBACK
    # -----------------------------------------------------

    if not songs:

        songs = MUSIC_IDS


    if not songs:

        send_message(

            chat_id,

            "⚠️ No music is available yet."
        )

        return False


    # -----------------------------------------------------
    # Try several songs if one fails
    # -----------------------------------------------------

    candidates = list(
        dict.fromkeys(
            songs
        )
    )


    random.shuffle(
        candidates
    )


    for message_id in candidates[:10]:

        result = copy_music(

            chat_id,

            message_id
        )


        if result.get("ok"):

            print(
                "MUSIC SENT:",
                message_id
            )

            return True


        print(
            "FAILED MUSIC:",
            message_id
        )


    # -----------------------------------------------------
    # Nothing worked
    # -----------------------------------------------------

    send_message(

        chat_id,

        "❌ I couldn't send a music file right now.\n\n"
        "Please try again."
    )


    return False


# =========================================================
# AI CHAT
# =========================================================

def ask_ai(text):

    if not ai_client:

        return (
            "⚠️ AI is not connected.\n"
            "But you can still use the mood buttons."
        )


    try:

        response = ai_client.responses.create(

            model="gpt-5-mini",

            instructions="""

You are NOT YOUR VIBE Music Assistant.

You recommend EDM/electronic music.

Available moods:

Sad
Love
Chill
Hype
Dark
Energetic
Night Drive
Melodic

Understand Burmese and English.

Give short and useful recommendations.

IMPORTANT:

Do not invent specific tracks.

If the user asks for a mood,
tell them which mood button to choose.

""",

            input=text
        )


        return response.output_text


    except Exception as e:

        print(
            "AI CHAT ERROR:",
            e
        )

        return (
            "⚠️ AI is temporarily unavailable.\n\n"
            "You can still choose a mood below."
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
    # USER MESSAGE
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

                "Choose your mood and I'll send "
                "music from NOT YOUR VIBE collection.",

                mood_menu()
            )


        # =================================================
        # MOOD
        # =================================================

        elif text == "/mood":

            send_message(

                chat_id,

                "🎧 What are you feeling?",

                mood_menu()
            )


        # =================================================
        # AI
        # =================================================

        elif text == "/ai":

            send_message(

                chat_id,

                "🤖 AI MUSIC ASSISTANT\n\n"

                "Tell me what you want.\n\n"

                "Examples:\n"

                "• I'm heartbroken\n"

                "• I need emotional music\n"

                "• Give me dark bass\n"

                "• I want something for night driving\n"

                "• I need festival hype music"
            )


        # =================================================
        # HELP
        # =================================================

        elif text == "/help":

            send_message(

                chat_id,

                "🎧 NOT YOUR VIBE MUSIC BOT\n\n"

                "/start — Start\n"
                "/mood — Choose mood\n"
                "/ai — AI Assistant\n"
                "/help — Help"
            )


        # =================================================
        # NORMAL TEXT → AI
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
    # CALLBACK QUERY
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

                "🤖 AI Music Assistant"
            )


            send_message(

                chat_id,

                "🤖 Tell me how you're feeling.\n\n"

                "Example:\n"
                "I feel lonely and want emotional "
                "melodic bass music."
            )


        # =================================================
        # CHANGE MOOD
        # =================================================

        elif data == "change_mood":

            answer_callback(

                callback_id,

                "🎧 Choose another mood"
            )


            send_message(

                chat_id,

                "🎧 Choose your mood 👇",

                mood_menu()
            )


        # =================================================
        # NEXT MUSIC
        # =================================================

        elif data == "next_music":

            answer_callback(

                callback_id,

                "🔀 Finding another track..."
            )


            success = send_random_music(
                chat_id
            )


            if success:

                send_message(

                    chat_id,

                    "🔀 Another track 👇",

                    music_buttons()
                )


        # =================================================
        # MOOD MUSIC
        # =================================================

        elif data.startswith(
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


            answer_callback(

                callback_id,

                f"{MOOD_NAMES[mood]} selected!"
            )


            # ------------------------------------------------
            # IMPORTANT:
            # This ALWAYS has fallback.
            # It will NEVER say:
            # "not enough AI analyzed songs"
            # ------------------------------------------------

            success = send_random_music(

                chat_id,

                mood
            )


            if success:

                send_message(

                    chat_id,

                    f"{MOOD_NAMES[mood]}\n\n"
                    "🎧 Here's a track from "
                    "NOT YOUR VIBE collection.",

                    music_buttons()
                )


    return "OK"


# =========================================================
# SET WEBHOOK
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
            "WEBHOOK:",
            response.text
        )


    except Exception as e:

        print(
            "WEBHOOK ERROR:",
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
