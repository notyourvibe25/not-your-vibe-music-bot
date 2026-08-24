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
    try:
        ai_client = OpenAI(
            api_key=OPENAI_API_KEY
        )
    except Exception as e:
        print("OpenAI init error:", e)


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
# OLD CHANNEL MESSAGE IDS
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

def get_db():

    return sqlite3.connect(
        DB_FILE,
        check_same_thread=False
    )


def init_database():

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS songs (

            message_id INTEGER PRIMARY KEY,

            caption TEXT DEFAULT '',

            moods TEXT DEFAULT '[]',

            submoods TEXT DEFAULT '[]',

            genre TEXT DEFAULT '',

            analyzed INTEGER DEFAULT 0

        )
    """)

    connection.commit()
    connection.close()


init_database()


# =========================================================
# DATABASE SAVE
# =========================================================

def save_song(
    message_id,
    caption="",
    moods=None,
    submoods=None,
    genre=""
):

    if moods is None:
        moods = []

    if submoods is None:
        submoods = []


    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO songs
        (
            message_id,
            caption,
            moods,
            submoods,
            genre,
            analyzed
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            message_id,
            caption,
            json.dumps(
                moods,
                ensure_ascii=False
            ),
            json.dumps(
                submoods,
                ensure_ascii=False
            ),
            genre,
            1
        )
    )

    connection.commit()
    connection.close()


# =========================================================
# GET DATABASE SONGS
# =========================================================

def get_database_songs():

    connection = get_db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            message_id,
            caption,
            moods,
            submoods,
            genre,
            analyzed
        FROM songs
        """
    )

    rows = cursor.fetchall()

    connection.close()


    songs = []

    for row in rows:

        try:
            moods = json.loads(row[2])
        except:
            moods = []


        try:
            submoods = json.loads(row[3])
        except:
            submoods = []


        songs.append({

            "id": row[0],

            "caption": row[1] or "",

            "moods": moods,

            "submoods": submoods,

            "genre": row[4] or "",

            "analyzed": row[5]

        })


    return songs


# =========================================================
# ENSURE OLD IDS EXIST
# =========================================================

def ensure_old_ids():

    connection = get_db()

    cursor = connection.cursor()


    for message_id in MUSIC_IDS:

        cursor.execute(
            """
            INSERT OR IGNORE INTO songs
            (
                message_id,
                caption,
                moods,
                submoods,
                genre,
                analyzed
            )
            VALUES (?, '', '[]', '[]', '', 0)
            """,
            (message_id,)
        )


    connection.commit()
    connection.close()


ensure_old_ids()


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
# ANSWER CALLBACK
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
# AI SONG CLASSIFIER
# =========================================================

def classify_song(
    caption
):

    if not ai_client:

        return (
            [],
            [],
            ""
        )


    if not caption:

        return (
            [],
            [],
            ""
        )


    try:

        response = ai_client.responses.create(

            model="gpt-5-mini",

            instructions="""

You are the NOT YOUR VIBE EDM music classifier.

Analyze the song title/caption.

Choose ONLY moods that genuinely match.

Available moods:

sad
love
chill
hype
dark
energetic
night
melodic

Use submoods such as:

emotional
heartbreak
melancholic
lonely
deep
romantic
dreamy
relaxing
smooth
late_night
festival
party
bass
aggressive
dark_bass
moody
atmospheric
night_drive
cinematic
uplifting
nostalgic
future_bass
melodic_bass
future_riddim
trap
dubstep
house

IMPORTANT:

Do NOT assign every mood.

For example:

A clearly emotional song can be:
["sad", "melodic"]

A festival banger can be:
["hype", "energetic"]

A romantic song can be:
["love", "melodic"]

A dark aggressive bass track can be:
["dark", "energetic"]

Return ONLY JSON:

{
    "moods": [],
    "submoods": [],
    "genre": ""
}

""",

            input=caption

        )


        result = json.loads(
            response.output_text.strip()
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

        for submood in result.get(
            "submoods",
            []
        ):

            if isinstance(
                submood,
                str
            ):

                submoods.append(
                    submood.lower()
                )


        genre = result.get(
            "genre",
            ""
        )


        if not isinstance(
            genre,
            str
        ):

            genre = ""


        return (
            moods,
            submoods,
            genre
        )


    except Exception as e:

        print(
            "Song classifier error:",
            e
        )

        return (
            [],
            [],
            ""
        )


# =========================================================
# SAVE NEW CHANNEL SONG
# =========================================================

def add_new_channel_song(
    message_id,
    caption
):

    moods, submoods, genre = classify_song(
        caption
    )


    save_song(

        message_id,

        caption,

        moods,

        submoods,

        genre

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
        "MOODS:",
        moods
    )

    print(
        "SUBMOODS:",
        submoods
    )

    print(
        "GENRE:",
        genre
    )


# =========================================================
# AI RECOMMENDATION
# =========================================================

def ai_recommend(
    user_text,
    preferred_mood=None
):

    if not ai_client:

        return None


    songs = get_database_songs()


    # IMPORTANT:
    # Only analyzed songs can be AI recommended.
    analyzed_songs = [

        song

        for song in songs

        if song["analyzed"] == 1

        and song["moods"]

    ]


    if not analyzed_songs:

        return None


    catalog = []


    for song in analyzed_songs:

        catalog.append({

            "message_id":
                song["id"],

            "caption":
                song["caption"],

            "genre":
                song["genre"],

            "moods":
                song["moods"],

            "submoods":
                song["submoods"]

        })


    try:

        response = ai_client.responses.create(

            model="gpt-5-mini",

            instructions="""

You are NOT YOUR VIBE AI MUSIC RECOMMENDER.

You MUST recommend ONLY one song from the
provided CHANNEL CATALOG.

Never invent a song.
Never invent a message ID.

Understand Burmese and English.

Match the user's feeling to the song's
actual moods, submoods, genre and caption.

IMPORTANT MOOD RULE:

If the requested mood is SAD, strongly prefer:

emotional
heartbreak
melancholic
lonely
deep
nostalgic

Do NOT recommend a song merely because it
has the word "melodic".

For SAD, avoid:

festival
party
hype
energetic

unless the song is genuinely emotional too.

If the user requests LOVE, prefer:

romantic
love
sweet
dreamy
missing

If NIGHT DRIVE:

night_drive
late_night
atmospheric
deep
melodic

If HYPE:

festival
party
bass
energetic
powerful

If DARK:

dark
dark_bass
aggressive
moody

If CHILL:

relaxing
dreamy
smooth
atmospheric

Return ONLY:

{
    "message_id": 123,
    "reason": "Short reason"
}

The message_id MUST be copied from the
catalog exactly.

""",

            input=(

                "USER REQUEST:\n"

                + user_text

                + "\n\n"

                + "SELECTED MOOD:\n"

                + str(
                    preferred_mood
                )

                + "\n\n"

                + "CHANNEL CATALOG:\n"

                + json.dumps(
                    catalog,
                    ensure_ascii=False
                )

            )

        )


        result = json.loads(
            response.output_text.strip()
        )


        message_id = int(
            result[
                "message_id"
            ]
        )


        valid_ids = [

            song["id"]

            for song in analyzed_songs

        ]


        if message_id not in valid_ids:

            return None


        selected = next(

            (
                song

                for song in analyzed_songs

                if song["id"] == message_id
            ),

            None

        )


        if not selected:

            return None


        return {

            "message_id":
                message_id,

            "reason":
                result.get(
                    "reason",
                    "This track matches your mood."
                ),

            "song":
                selected

        }


    except Exception as e:

        print(
            "AI recommendation error:",
            e
        )

        return None


# =========================================================
# FALLBACK MOOD MATCH
# =========================================================

def fallback_mood_song(
    mood
):

    songs = get_database_songs()


    matching = []


    for song in songs:

        if song["analyzed"] != 1:
            continue


        if mood in song["moods"]:

            matching.append(
                song
            )


    if not matching:

        return None


    return random.choice(
        matching
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


        expected = CHANNEL_USERNAME.replace(
            "@",
            ""
        ).lower()


        if username.lower() == expected:

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


        # =================================================
        # START
        # =================================================

        if text == "/start":

            send_message(

                chat_id,

                "🎧 NOT YOUR VIBE MUSIC\n\n"

                "Welcome! 🔥\n\n"

                "Choose a mood or tell me "
                "what you're feeling.",

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

                "🤖 AI MUSIC SUGGESTION\n\n"

                "Tell me what you're feeling.\n\n"

                "Examples:\n\n"

                "💔 I'm heartbroken\n"

                "🌧️ I want something emotional\n"

                "🚗 Music for a late night drive\n"

                "🔥 I need festival energy\n"

                "🖤 I want dark bass\n"

                "🌌 Emotional future bass\n\n"

                "I'll recommend ONLY tracks "
                "from NOT YOUR VIBE Collection."

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
                "/ai - AI Suggestion\n"
                "/help - Help"

            )


        # =================================================
        # NORMAL TEXT → AI
        # =================================================

        elif text:

            recommendation = ai_recommend(
                text
            )


            if recommendation:

                result = copy_music(

                    chat_id,

                    recommendation[
                        "message_id"
                    ]

                )


                if result.get(
                    "ok"
                ):

                    send_message(

                        chat_id,

                        "🤖 AI SUGGESTION\n\n"

                        + recommendation[
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

                        "❌ I found the song, "
                        "but Telegram couldn't "
                        "send it."

                    )

            else:

                send_message(

                    chat_id,

                    "🤖 I don't have enough "
                    "analyzed tracks yet.\n\n"

                    "Try another feeling or "
                    "choose a mood below.",

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


        # =================================================
        # AI HELP
        # =================================================

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

                "I'll search ONLY the "
                "NOT YOUR VIBE collection."

            )


        # =================================================
        # CHANGE MOOD
        # =================================================

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


        # =================================================
        # NEXT
        # =================================================

        elif data == "next_music":

            answer_callback(

                callback_id,

                "🔀 Finding another track..."

            )


            songs = get_database_songs()


            analyzed = [

                song

                for song in songs

                if song["analyzed"] == 1

                and song["moods"]

            ]


            if not analyzed:

                send_message(

                    chat_id,

                    "⚠️ I don't have enough "
                    "analyzed tracks yet.",

                    mood_menu()

                )

            else:

                song = random.choice(
                    analyzed
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


            if mood not in MOODS:

                answer_callback(
                    callback_id,
                    "Unknown mood"
                )

                return "OK"


            answer_callback(

                callback_id,

                f"{MOOD_NAMES[mood]} selected!"

            )


            # IMPORTANT:
            # AI gets the mood context.
            recommendation = ai_recommend(

                user_text=MOOD_NAMES[mood],

                preferred_mood=mood

            )


            if recommendation:

                result = copy_music(

                    chat_id,

                    recommendation[
                        "message_id"
                    ]

                )


                if result.get(
                    "ok"
                ):

                    send_message(

                        chat_id,

                        f"{MOOD_NAMES[mood]}\n\n"

                        + recommendation[
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

                        "❌ Couldn't send the "
                        "recommended track."

                    )

            else:

                # IMPORTANT:
                # NO RANDOM FALLBACK.
                # This prevents SAD from
                # accidentally returning HYPE.

                send_message(

                    chat_id,

                    f"{MOOD_NAMES[mood]}\n\n"

                    "⚠️ I don't have enough "
                    "AI-analyzed tracks for "
                    "this mood yet.\n\n"

                    "Try 🤖 AI Suggestion and "
                    "describe what you're feeling.",

                    mood_menu()

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
