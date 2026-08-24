import os
import random
import json
import sqlite3
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

DB_FILE = os.environ.get(
    "DB_FILE",
    "music.db"
)


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
# OLD CHANNEL IDS
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
    dict.fromkeys(MUSIC_IDS)
)


# =========================================================
# DATABASE
# =========================================================

def db():

    return sqlite3.connect(
        DB_FILE,
        check_same_thread=False
    )


def init_database():

    connection = db()

    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS songs (

            message_id INTEGER PRIMARY KEY,

            caption TEXT,

            moods TEXT,

            submoods TEXT,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

        )
    """)

    connection.commit()

    connection.close()


init_database()


# =========================================================
# DATABASE - ADD SONG
# =========================================================

def save_song(
    message_id,
    caption,
    moods,
    submoods
):

    connection = db()

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO songs
        (message_id, caption, moods, submoods)
        VALUES (?, ?, ?, ?)
        """,
        (
            message_id,
            caption,
            json.dumps(moods),
            json.dumps(submoods)
        )
    )

    connection.commit()

    connection.close()


# =========================================================
# DATABASE - GET SONGS
# =========================================================

def get_songs():

    connection = db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT message_id, caption, moods, submoods
        FROM songs
        """
    )

    rows = cursor.fetchall()

    connection.close()

    songs = []

    for row in rows:

        try:

            moods = json.loads(
                row[2]
            )

        except:

            moods = []


        try:

            submoods = json.loads(
                row[3]
            )

        except:

            submoods = []


        songs.append({

            "id": row[0],

            "caption": row[1] or "",

            "moods": moods,

            "submoods": submoods

        })


    return songs


# =========================================================
# TELEGRAM
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

        data[
            "reply_markup"
        ] = reply_markup


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
                    "text": "🤖 AI Suggestion",
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
                    "text": "🤖 Better Suggestion",
                    "callback_data":
                        "ai_help"
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
# AI CLASSIFIER
# =========================================================

def classify_music(
    text
):

    if not ai_client:

        return (
            ["melodic"],
            ["melodic"]
        )


    try:

        response = ai_client.responses.create(

            model="gpt-5.6",

            instructions="""

You are the NOT YOUR VIBE music classifier.

Analyze the provided Telegram music post.

Choose one or more MAIN moods:

sad
love
chill
hype
dark
energetic
night
melodic

Also choose useful SUB-MOODS.

Possible sub-moods include:

emotional
heartbreak
melancholic
deep
lonely
romantic
dreamy
relaxing
late_night
smooth
festival
bass
party
aggressive
powerful
dark_bass
moody
night_drive
atmospheric
cinematic
beautiful
uplifting
nostalgic
future_bass
melodic_bass
future_riddim
trap
dubstep
house

Return ONLY JSON:

{
  "moods": ["sad", "melodic"],
  "submoods": ["emotional", "heartbreak", "future_bass"]
}

Do not explain anything.

""",

            input=text
        )


        result = json.loads(
            response.output_text
        )


        moods = []

        for mood in result.get(
            "moods",
            []
        ):

            if mood in MOODS:

                if mood not in moods:

                    moods.append(
                        mood
                    )


        submoods = []

        for sub in result.get(
            "submoods",
            []
        ):

            if isinstance(
                sub,
                str
            ):

                submoods.append(
                    sub.lower()
                )


        if not moods:

            moods = ["melodic"]


        return (
            moods,
            submoods
        )


    except Exception as e:

        print(
            "Classifier error:",
            e
        )

        return (
            ["melodic"],
            []
        )


# =========================================================
# AI SUGGESTION
# =========================================================

def ai_suggestion(
    user_text
):

    if not ai_client:

        return None


    songs = get_songs()


    if not songs:

        return None


    # Send only channel songs to AI

    catalog = []


    for song in songs:

        catalog.append({

            "id":
                song["id"],

            "caption":
                song["caption"],

            "moods":
                song["moods"],

            "submoods":
                song["submoods"]

        })


    try:

        response = ai_client.responses.create(

            model="gpt-5.6",

            instructions="""

You are the NOT YOUR VIBE Music Recommendation AI.

VERY IMPORTANT:

You may recommend ONLY songs whose IDs exist
in the supplied CHANNEL CATALOG.

Never invent a song.
Never invent a message ID.
Never recommend music outside the catalog.

Understand Burmese and English.

First understand what the user wants.

Examples:

"I'm sad"
→ emotional / melancholic

"My heart is broken"
→ heartbreak / emotional

"I need music for driving at night"
→ night_drive / atmospheric

"I want something crazy for a festival"
→ hype / energetic / festival

"I want emotional future bass"
→ emotional / future_bass / melodic

Then choose the BEST matching song.

Return ONLY JSON:

{
  "message_id": 123,
  "reason": "Short reason"
}

The message_id MUST be copied exactly
from the supplied catalog.

""",

            input=(
                "USER REQUEST:\n"
                + user_text
                + "\n\n"
                + "CHANNEL CATALOG:\n"
                + json.dumps(
                    catalog,
                    ensure_ascii=False
                )
            )
        )


        result = json.loads(
            response.output_text
        )


        message_id = int(
            result[
                "message_id"
            ]
        )


        valid_ids = [

            song["id"]

            for song in songs

        ]


        if message_id not in valid_ids:

            return None


        return {

            "message_id":
                message_id,

            "reason":
                result.get(
                    "reason",
                    "I found a matching track for you."
                )

        }


    except Exception as e:

        print(
            "AI suggestion error:",
            e
        )

        return None


# =========================================================
# SAVE NEW CHANNEL POST
# =========================================================

def add_new_channel_song(
    message_id,
    caption
):

    moods, submoods = classify_music(
        caption
    )


    save_song(

        message_id,

        caption,

        moods,

        submoods
    )


    print(
        "NEW CHANNEL SONG"
    )

    print(
        "ID:",
        message_id
    )

    print(
        "Caption:",
        caption
    )

    print(
        "Moods:",
        moods
    )

    print(
        "Submoods:",
        submoods
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
    # NEW CHANNEL POST
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
            .replace(
                "@",
                ""
            )
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

        chat_id = message[
            "chat"
        ][
            "id"
        ]


        text = message.get(
            "text",
            ""
        ).strip()


        # ================================================
        # START
        # ================================================

        if text == "/start":

            send_message(

                chat_id,

                "🎧 NOT YOUR VIBE MUSIC\n\n"
                "Welcome! 🔥\n\n"
                "Tell me how you're feeling "
                "or choose a mode below.",

                mood_menu()

            )


        # ================================================
        # MOOD
        # ================================================

        elif text == "/mood":

            send_message(

                chat_id,

                "🎧 Choose your mood 👇",

                mood_menu()

            )


        # ================================================
        # AI
        # ================================================

        elif text == "/ai":

            send_message(

                chat_id,

                "🤖 AI MUSIC SUGGESTION\n\n"

                "Tell me what kind of music "
                "you want.\n\n"

                "Examples:\n\n"

                "💔 I'm heartbroken\n"

                "🌧️ I want something emotional\n"

                "🚗 Give me something for a "
                "late night drive\n"

                "🔥 I need crazy festival energy\n"

                "🌌 I want emotional future bass\n\n"

                "I'll recommend ONLY music "
                "from NOT YOUR VIBE MP3 Collection."

            )


        # ================================================
        # HELP
        # ================================================

        elif text == "/help":

            send_message(

                chat_id,

                "🎧 NOT YOUR VIBE MUSIC BOT\n\n"

                "/start - Start\n"
                "/mood - Choose mood\n"
                "/ai - AI Suggestion\n"
                "/help - Help\n\n"

                "You can also simply tell me "
                "what you're feeling."

            )


        # ================================================
        # NORMAL TEXT → AI
        # ================================================

        elif text:

            suggestion = ai_suggestion(
                text
            )


            if suggestion:

                result = copy_music(

                    chat_id,

                    suggestion[
                        "message_id"
                    ]

                )


                if result.get(
                    "ok"
                ):

                    send_message(

                        chat_id,

                        "🤖 AI SUGGESTION\n\n"

                        + suggestion[
                            "reason"
                        ]

                        + "\n\n"
                        "🎧 From NOT YOUR VIBE "
                        "MP3 Collection",

                        music_buttons()

                    )

                else:

                    send_message(

                        chat_id,

                        "❌ I found a match, "
                        "but Telegram couldn't "
                        "send the track."

                    )

            else:

                send_message(

                    chat_id,

                    "🤖 I couldn't find a close "
                    "match in the collection yet.\n\n"

                    "Try something like:\n"
                    "• emotional\n"
                    "• heartbreak\n"
                    "• night drive\n"
                    "• festival hype\n"
                    "• dark bass\n"
                    "• melodic future bass",

                    mood_menu()

                )


    # =====================================================
    # CALLBACK
    # =====================================================

    callback = update.get(
        "callback_query"
    )


    if callback:

        callback_id = callback[
            "id"
        ]


        callback_message = callback.get(
            "message",
            {}
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


        data = callback.get(
            "data",
            ""
        )


        # ================================================
        # AI
        # ================================================

        if data == "ai_help":

            answer_callback(

                callback_id,

                "🤖 AI Suggestion"

            )


            send_message(

                chat_id,

                "🤖 Tell me what you're feeling.\n\n"

                "Examples:\n"

                "💔 heartbreak\n"
                "🌧️ emotional\n"
                "🚗 night drive\n"
                "🔥 festival hype\n"
                "🖤 dark bass\n"
                "🌌 emotional future bass\n\n"

                "I'll search ONLY "
                "NOT YOUR VIBE collection."

            )


        # ================================================
        # CHANGE MOOD
        # ================================================

        elif data == "change_mood":

            answer_callback(

                callback_id,

                "Choose your mood"

            )


            send_message(

                chat_id,

                "🎧 Choose your mood 👇",

                mood_menu()

            )


        # ================================================
        # NEXT
        # ================================================

        elif data == "next_music":

            answer_callback(

                callback_id,

                "🔀 Finding another track..."

            )


            songs = get_songs()


            if songs:

                song = random.choice(
                    songs
                )


                result = copy_music(

                    chat_id,

                    song["id"]
                )


                if result.get(
                    "ok"
                ):

                    send_message(

                        chat_id,

                        "🔀 Another track "
                        "from the collection 👇",

                        music_buttons()

                    )

                else:

                    send_message(

                        chat_id,

                        "❌ Couldn't send the track."

                    )


        # ================================================
        # MOOD BUTTON
        # ================================================

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


            songs = get_songs()


            matching = []


            for song in songs:

                if mood in song[
                    "moods"
                ]:

                    matching.append(
                        song
                    )


            # If AI-classified songs don't exist yet,
            # use old IDs as fallback.

            if not matching:

                old_ids = [

                    x

                    for x in MUSIC_IDS

                    if x not in [

                        s["id"]

                        for s in songs

                    ]

                ]


                if old_ids:

                    message_id = random.choice(
                        old_ids
                    )

                else:

                    message_id = None


            else:

                song = random.choice(
                    matching
                )

                message_id = song[
                    "id"
                ]


            if message_id:

                result = copy_music(

                    chat_id,

                    message_id

                )


                if result.get(
                    "ok"
                ):

                    send_message(

                        chat_id,

                        f"{MOOD_NAMES.get(mood)}\n\n"
                        "🎧 From NOT YOUR VIBE "
                        "MP3 Collection",

                        music_buttons()

                    )

                else:

                    send_message(

                        chat_id,

                        "❌ Couldn't send the track."

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
                    "channel_post"
                ]
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
