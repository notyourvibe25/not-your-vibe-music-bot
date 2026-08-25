import os
import random
import json
import re
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
# YOUR CHANNEL MESSAGE IDS
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


MUSIC_IDS = list(
    dict.fromkeys(
        MUSIC_IDS
    )
)


# =========================================================
# SONG DATABASE
#
# message_id:
# {
#   "caption": "...",
#   "moods": [...]
# }
# =========================================================

SONGS = {}

for message_id in MUSIC_IDS:

    SONGS[message_id] = {

        "caption": "",

        "moods": []
    }


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
# TELEGRAM API
# =========================================================

def telegram(
    method,
    data
):

    try:

        response = requests.post(

            f"{TELEGRAM_API}/{method}",

            json=data,

            timeout=30
        )


        result = response.json()


        print(
            "TELEGRAM:",
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

        data["reply_markup"] = (
            reply_markup
        )


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
# NORMALIZE CAPTION
# =========================================================

def normalize_caption(
    caption
):

    if not caption:

        return ""

    text = caption.lower()

    text = text.replace(
        "\n",
        " "
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# =========================================================
# CHECK MELODIC DUBSTEP
#
# SAD ONLY
# =========================================================

def is_melodic_dubstep(
    caption
):

    text = normalize_caption(
        caption
    )


    keywords = [

        "melodic dubstep",

        "melodic-dubstep",

        "melodicdubstep",

        "melodic bass",

        "emotional dubstep"

    ]


    for keyword in keywords:

        if keyword in text:

            return True


    return False


# =========================================================
# SAD CAPTION CHECK
# =========================================================

def is_sad_caption(
    caption
):

    text = normalize_caption(
        caption
    )


    sad_words = [

        "sad",

        "emotional",

        "emotion",

        "melancholic",

        "melancholy",

        "heartbreak",

        "heartbroken",

        "broken heart",

        "lonely",

        "loneliness",

        "nostalgic",

        "nostalgia",

        "cry",

        "tears",

        "pain",

        "lost",

        "goodbye",

        "memories",

        "deep emotional",

        "emotional vocal",

        "emotional vocals"

    ]


    for word in sad_words:

        if word in text:

            return True


    return False


# =========================================================
# AI CLASSIFIER
#
# SAD IS NOT SENT HERE AS A GENERAL AI RESULT.
#
# SAD WILL BE HANDLED SEPARATELY.
# =========================================================

def classify_other_moods(
    caption
):

    if not ai_client:

        return []


    if not caption:

        return []


    try:

        response = ai_client.responses.create(

            model="gpt-5-mini",

            instructions="""

You are an EDM music mood classifier for
NOT YOUR VIBE.

Analyze the supplied music caption.

IMPORTANT:

Do NOT return "sad".

Sad is handled separately by the system.

Choose ONLY from:

love
chill
hype
dark
energetic
night
melodic

Use the actual caption.

Do not guess from genre alone.

Examples:

"Tech House / Club / Party"
=> ["hype"]

"Dark Techno / Industrial / Aggressive"
=> ["dark","energetic"]

"Melodic Techno / Night Drive / Deep"
=> ["night","melodic"]

"Afro House / Summer / Feel Good"
=> ["chill","energetic"]

"Festival / Big Room / Massive Drop"
=> ["hype","energetic"]

Return ONLY JSON.

Example:

["night","melodic"]

""",

            input=caption

        )


        raw = (
            response
            .output_text
            .strip()
        )


        print(
            "AI MOOD RAW:",
            raw
        )


        moods = json.loads(
            raw
        )


        if not isinstance(
            moods,
            list
        ):

            return []


        valid = []


        for mood in moods:

            if mood in MOODS:

                if mood != "sad":

                    if mood not in valid:

                        valid.append(
                            mood
                        )


        return valid


    except Exception as e:

        print(
            "AI CLASSIFIER ERROR:",
            e
        )

        return []


# =========================================================
# ANALYZE NEW SONG
# =========================================================

def analyze_song(
    message_id,
    caption
):

    if not message_id:

        return


    if message_id not in SONGS:

        SONGS[message_id] = {

            "caption": "",

            "moods": []
        }


    SONGS[message_id][
        "caption"
    ] = caption


    # =====================================================
    # SAD SPECIAL RULE
    #
    # ONLY MELODIC DUBSTEP
    # =====================================================

    if is_melodic_dubstep(
        caption
    ):

        if is_sad_caption(
            caption
        ):

            if message_id not in (
                MOOD_MUSIC["sad"]
            ):

                MOOD_MUSIC["sad"].append(
                    message_id
                )


            SONGS[message_id][
                "moods"
            ].append(
                "sad"
            )


            print(
                "SAD MATCH:",
                message_id,
                caption
            )


    # =====================================================
    # OTHER MOODS → AI + CAPTION
    # =====================================================

    other_moods = (
        classify_other_moods(
            caption
        )
    )


    for mood in other_moods:

        if message_id not in (
            MOOD_MUSIC[mood]
        ):

            MOOD_MUSIC[mood].append(
                message_id
            )


        if mood not in (
            SONGS[message_id][
                "moods"
            ]
        ):

            SONGS[message_id][
                "moods"
            ].append(
                mood
            )


    print(
        "ANALYZED SONG:",
        message_id
    )

    print(
        "CAPTION:",
        caption
    )

    print(
        "MOODS:",
        SONGS[message_id]["moods"]
    )


# =========================================================
# ADD NEW CHANNEL SONG
# =========================================================

def add_new_channel_song(
    message_id,
    caption
):

    if not message_id:

        return


    if message_id not in MUSIC_IDS:

        MUSIC_IDS.append(
            message_id
        )


    analyze_song(

        message_id,

        caption
    )


# =========================================================
# SEND RANDOM SONG
# =========================================================

def send_random_music(
    chat_id,
    mood=None
):

    # =====================================================
    # SAD
    #
    # ONLY MELODIC DUBSTEP
    # =====================================================

    if mood == "sad":

        songs = list(
            dict.fromkeys(
                MOOD_MUSIC["sad"]
            )
        )


        if not songs:

            send_message(

                chat_id,

                "😢 Sad collection is still being analyzed.\n\n"
                "Please add Melodic Dubstep captions "
                "with emotional/sad keywords."
            )

            return False


    # =====================================================
    # OTHER MOODS
    # =====================================================

    else:

        songs = list(

            dict.fromkeys(

                MOOD_MUSIC.get(
                    mood,
                    []
                )

            )

        )


        # -------------------------------------------------
        # If AI has not classified anything yet,
        # use all songs as fallback.
        # -------------------------------------------------

        if not songs:

            songs = list(
                MUSIC_IDS
            )


    if not songs:

        send_message(

            chat_id,

            "⚠️ No music is available yet."
        )

        return False


    random.shuffle(
        songs
    )


    # =====================================================
    # TRY MULTIPLE SONGS
    # =====================================================

    for message_id in songs[:15]:

        result = copy_music(

            chat_id,

            message_id
        )


        if result.get("ok"):

            print(
                "SENT:",
                message_id,
                "MOOD:",
                mood
            )

            return True


        print(
            "COPY FAILED:",
            message_id,
            result
        )


    send_message(

        chat_id,

        "❌ Couldn't send the track right now.\n"
        "Please try again."
    )


    return False


# =========================================================
# AI CHAT
# =========================================================

def ask_ai(
    text
):

    if not ai_client:

        return (

            "⚠️ AI is not connected.\n\n"

            "You can still use the mood buttons."

        )


    try:

        response = ai_client.responses.create(

            model="gpt-5-mini",

            instructions="""

You are NOT YOUR VIBE Music Assistant.

The user wants EDM/electronic music.

Available moods:

😢 Sad
❤️ Love
🌙 Chill
🔥 Hype
🖤 Dark
⚡ Energetic
🚗 Night Drive
🌌 Melodic

IMPORTANT:

Sad is specifically for Melodic Dubstep
with emotional/sad/melancholic/heartbreak/
lonely/nostalgic characteristics.

Understand Burmese and English.

If the user describes a feeling,
recommend the appropriate mood button.

Do not invent channel songs.

Keep answers short.

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

            "Please use the mood buttons below."

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


        username = (
            chat.get(
                "username",
                ""
            )
            or ""
        )


        if username.lower() == (

            CHANNEL_USERNAME
            .replace(
                "@",
                ""
            )
            .lower()

        ):

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

                or

                channel_post.get(
                    "text",
                    ""
                )

                or

                ""

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

        chat_id = (
            message["chat"]["id"]
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

                "Choose your mood below.\n"

                "I'll find music from "
                "NOT YOUR VIBE collection.",

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

                "Tell me how you're feeling.\n\n"

                "Examples:\n"

                "• I'm heartbroken\n"

                "• I want emotional music\n"

                "• Give me dark bass\n"

                "• Music for night driving\n"

                "• I need festival music"

            )


        # =================================================
        # HELP
        # =================================================

        elif text == "/help":

            send_message(

                chat_id,

                "🎧 NOT YOUR VIBE MUSIC BOT\n\n"

                "/start — Start\n"
                "/mood — Choose Mood\n"
                "/ai — AI Assistant\n"
                "/help — Help"

            )


        # =================================================
        # NORMAL TEXT
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

        callback_id = (
            callback["id"]
        )


        callback_message = (
            callback.get(
                "message",
                {}
            )
        )


        chat_id = (

            callback_message
            .get(
                "chat",
                {}
            )
            .get(
                "id"
            )

        )


        data = (
            callback.get(
                "data",
                ""
            )
        )


        # =================================================
        # AI
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

                "I feel lonely and want "
                "emotional melodic music."

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
        # NEXT
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

                    "🔀 Next track 👇",

                    music_buttons()

                )


        # =================================================
        # MOOD
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


            success = send_random_music(

                chat_id,

                mood

            )


            if success:

                send_message(

                    chat_id,

                    f"{MOOD_NAMES[mood]}\n\n"
                    "🎧 Here's your track.",

                    music_buttons()

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

                "url":
                    webhook_url,

                "allowed_updates": [

                    "message",

                    "callback_query",

                    "channel_post",

                    "edited_channel_post"

                ]

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
