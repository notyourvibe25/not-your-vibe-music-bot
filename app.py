import os
import random
import json
import sqlite3
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

DB_FILE = "music_catalog.db"


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


def init_db():

    connection = db()

    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS songs (

            message_id INTEGER PRIMARY KEY,

            title TEXT DEFAULT '',

            caption TEXT DEFAULT '',

            performer TEXT DEFAULT '',

            genre TEXT DEFAULT '',

            moods TEXT DEFAULT '[]',

            analyzed INTEGER DEFAULT 0

        )
    """)

    connection.commit()

    connection.close()


init_db()


# =========================================================
# ADD OLD IDS TO DATABASE
# =========================================================

def register_old_songs():

    connection = db()

    cursor = connection.cursor()

    for message_id in MUSIC_IDS:

        cursor.execute(
            """
            INSERT OR IGNORE INTO songs
            (
                message_id
            )
            VALUES (?)
            """,
            (
                message_id,
            )
        )

    connection.commit()

    connection.close()


register_old_songs()


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
            method,
            result
        )

        return result

    except Exception as e:

        print(
            "Telegram ERROR:",
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
# COPY MUSIC FROM YOUR CHANNEL
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
# MAIN MENU
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
            ],

            [
                {
                    "text": "🤖 AI Suggestion",
                    "callback_data": "ai_help"
                }
            ]

        ]

    }


# =========================================================
# AFTER MUSIC BUTTONS
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
            ],

            [
                {
                    "text": "🤖 AI Suggestion",
                    "callback_data": "ai_help"
                }
            ]

        ]

    }


# =========================================================
# SAVE SONG
# =========================================================

def save_song(
    message_id,
    title,
    caption,
    performer,
    genre,
    moods
):

    connection = db()

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO songs
        (
            message_id,
            title,
            caption,
            performer,
            genre,
            moods,
            analyzed
        )
        VALUES (?, ?, ?, ?, ?, ?, 1)
        """,

        (
            message_id,

            title,

            caption,

            performer,

            genre,

            json.dumps(
                moods,
                ensure_ascii=False
            )

        )
    )

    connection.commit()

    connection.close()


# =========================================================
# GET SONGS
# =========================================================

def get_songs():

    connection = db()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            message_id,
            title,
            caption,
            performer,
            genre,
            moods,
            analyzed
        FROM songs
        """
    )

    rows = cursor.fetchall()

    connection.close()

    songs = []

    for row in rows:

        try:

            moods = json.loads(
                row[5]
            )

        except:

            moods = []


        songs.append({

            "id": row[0],

            "title": row[1] or "",

            "caption": row[2] or "",

            "performer": row[3] or "",

            "genre": row[4] or "",

            "moods": moods,

            "analyzed": row[6]

        })

    return songs


# =========================================================
# AI CLASSIFY
# =========================================================

def classify_song(
    title,
    caption,
    performer
):

    if not ai_client:

        return [], ""


    information = (

        "TITLE: "
        + title

        + "\nPERFORMER: "
        + performer

        + "\nCAPTION: "
        + caption

    )


    try:

        response = ai_client.responses.create(

            model="gpt-5.6-luna",

            instructions="""

You are the music classifier for
NOT YOUR VIBE.

Classify ONLY from the information supplied.

Available moods:

sad
love
chill
hype
dark
energetic
night
melodic

IMPORTANT:

Do NOT put every mood.

SAD means:
emotional, heartbreak, melancholic,
lonely, nostalgic, deep sadness.

LOVE means:
romantic, relationship, affection,
dreamy love.

CHILL means:
relaxed, smooth, calm, laid-back.

HYPE means:
festival, party, powerful,
high-energy, aggressive.

DARK means:
dark, sinister, moody,
dark bass, heavy atmosphere.

ENERGETIC means:
energetic, uplifting, powerful,
dance/festival energy.

NIGHT means:
night drive, late night,
urban night, atmospheric,
deep nighttime feeling.

MELODIC means:
strong melody, emotional melody,
dreamy, atmospheric.

Return ONLY JSON:

{
  "moods": [],
  "genre": ""
}

Never invent information.

""",

            input=information

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


        genre = result.get(
            "genre",
            ""
        )


        if not isinstance(
            genre,
            str
        ):

            genre = ""


        return moods, genre


    except Exception as e:

        print(
            "CLASSIFY ERROR:",
            e
        )

        return [], ""


# =========================================================
# PROCESS NEW CHANNEL POST
# =========================================================

def process_channel_post(
    post
):

    message_id = post.get(
        "message_id"
    )


    if not message_id:

        return


    caption = (
        post.get("caption")
        or
        post.get("text")
        or
        ""
    )


    title = ""

    performer = ""

    genre = ""


    # MP3 / AUDIO

    audio = post.get(
        "audio"
    )


    if audio:

        title = (
            audio.get(
                "title"
            )
            or
            ""
        )


        performer = (
            audio.get(
                "performer"
            )
            or
            ""
        )


    # DOCUMENT fallback

    document = post.get(
        "document"
    )


    if document and not title:

        file_name = document.get(
            "file_name",
            ""
        )

        title = file_name


    combined = (

        title
        + " "
        + performer
        + " "
        + caption

    ).strip()


    print(
        "NEW CHANNEL SONG:",
        message_id
    )

    print(
        "INFO:",
        combined
    )


    # AI classify

    moods, genre = classify_song(

        title,

        caption,

        performer

    )


    save_song(

        message_id,

        title,

        caption,

        performer,

        genre,

        moods

    )


    print(
        "AI MOODS:",
        moods
    )

    print(
        "GENRE:",
        genre
    )


# =========================================================
# GET MOOD SONGS
# =========================================================

def get_mood_songs(
    mood
):

    songs = get_songs()

    matching = []


    for song in songs:

        if song["analyzed"] != 1:

            continue


        if mood in song["moods"]:

            matching.append(
                song
            )


    return matching


# =========================================================
# AI SELECT SONG
# =========================================================

def ai_select_song(
    mood,
    user_text=""
):

    if not ai_client:

        return None


    songs = get_songs()


    analyzed = [

        song

        for song in songs

        if song["analyzed"] == 1

        and song["moods"]

    ]


    if not analyzed:

        return None


    catalog = []


    for song in analyzed:

        catalog.append({

            "message_id":
                song["id"],

            "title":
                song["title"],

            "performer":
                song["performer"],

            "caption":
                song["caption"],

            "genre":
                song["genre"],

            "moods":
                song["moods"]

        })


    try:

        response = ai_client.responses.create(

            model="gpt-5.6-luna",

            instructions="""

You recommend ONE track from
the supplied NOT YOUR VIBE catalog.

You MUST select an existing message_id.

Never invent a message_id.

Mood rules:

SAD:
strong preference for emotional,
heartbreak, melancholic, lonely,
nostalgic, deep.

LOVE:
romantic, love, dreamy, emotional.

CHILL:
relaxed, smooth, calm, dreamy.

HYPE:
festival, party, energetic,
powerful, bass.

DARK:
dark, moody, aggressive,
dark_bass.

ENERGETIC:
energetic, festival, uplifting,
powerful.

NIGHT:
night_drive, late_night,
atmospheric, deep.

MELODIC:
melodic, emotional, dreamy,
atmospheric.

Do NOT recommend a Hype track
for Sad just because it is EDM.

Return ONLY:

{
  "message_id": 123,
  "reason": "short reason"
}

""",

            input=(

                "REQUESTED MOOD: "
                + mood

                + "\nUSER REQUEST: "
                + user_text

                + "\n\nCATALOG:\n"

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
            result["message_id"]
        )


        allowed = [

            song["id"]

            for song in analyzed

        ]


        if message_id not in allowed:

            return None


        return {

            "message_id":
                message_id,

            "reason":
                result.get(
                    "reason",
                    "This track matches your mood."
                )

        }


    except Exception as e:

        print(
            "AI SELECT ERROR:",
            e
        )

        return None


# =========================================================
# SEND MOOD MUSIC
# =========================================================

def send_mood_music(
    chat_id,
    mood
):

    # -----------------------------------------------------
    # FIRST:
    # Exact AI analyzed mood songs
    # -----------------------------------------------------

    matching = get_mood_songs(
        mood
    )


    if matching:

        song = random.choice(
            matching
        )


        result = copy_music(

            chat_id,

            song["id"]

        )


        if result.get("ok"):

            send_message(

                chat_id,

                MOOD_NAMES[mood]
                + "\n\n"
                + "🎧 Recommended from "
                  "your collection.",

                music_buttons()

            )

            return


    # -----------------------------------------------------
    # SECOND:
    # AI chooses from analyzed catalog
    # -----------------------------------------------------

    recommendation = ai_select_song(
        mood
    )


    if recommendation:

        result = copy_music(

            chat_id,

            recommendation[
                "message_id"
            ]

        )


        if result.get("ok"):

            send_message(

                chat_id,

                MOOD_NAMES[mood]

                + "\n\n"

                + recommendation[
                    "reason"
                ],

                music_buttons()

            )

            return


    # -----------------------------------------------------
    # THIRD:
    # IMPORTANT FALLBACK
    #
    # Never show "not enough AI analyzed"
    # Never leave the button dead.
    # -----------------------------------------------------

    if MUSIC_IDS:

        message_id = random.choice(
            MUSIC_IDS
        )


        result = copy_music(

            chat_id,

            message_id

        )


        if result.get("ok"):

            send_message(

                chat_id,

                MOOD_NAMES[mood]

                + "\n\n"

                + "🎧 From NOT YOUR VIBE "
                  "MP3 Collection.",

                music_buttons()

            )

        else:

            send_message(

                chat_id,

                "❌ Couldn't send the music."

            )

    else:

        send_message(

            chat_id,

            "❌ No music available."

        )


# =========================================================
# AI USER SUGGESTION
# =========================================================

def user_ai_suggestion(
    chat_id,
    text
):

    recommendation = ai_select_song(

        "user request",

        text

    )


    if recommendation:

        result = copy_music(

            chat_id,

            recommendation[
                "message_id"
            ]

        )


        if result.get("ok"):

            send_message(

                chat_id,

                "🤖 AI SUGGESTION\n\n"

                + recommendation[
                    "reason"
                ]

                + "\n\n"

                + "🎧 From NOT YOUR VIBE "
                  "MP3 Collection.",

                music_buttons()

            )

            return


    send_message(

        chat_id,

        "🤖 I don't have enough analyzed "
        "tracks for that exact request yet.\n\n"

        "Try one of the moods below 👇",

        mood_menu()

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
            or
            ""
        ).lower()


        expected = CHANNEL_USERNAME.replace(
            "@",
            ""
        ).lower()


        if username == expected:

            try:

                process_channel_post(
                    channel_post
                )

            except Exception as e:

                print(
                    "CHANNEL PROCESS ERROR:",
                    e
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


        text = (
            message.get(
                "text",
                ""
            )
            or
            ""
        ).strip()


        # -------------------------------------------------
        # START
        # -------------------------------------------------

        if text == "/start":

            send_message(

                chat_id,

                "🎧 NOT YOUR VIBE MUSIC\n\n"

                "Welcome! 🔥\n\n"

                "Choose your mood below. 👇",

                mood_menu()

            )


        # -------------------------------------------------
        # MOOD
        # -------------------------------------------------

        elif text == "/mood":

            send_message(

                chat_id,

                "🎧 Choose your mood 👇",

                mood_menu()

            )


        # -------------------------------------------------
        # AI
        # -------------------------------------------------

        elif text == "/ai":

            send_message(

                chat_id,

                "🤖 AI MUSIC SUGGESTION\n\n"

                "Tell me how you're feeling.\n\n"

                "Examples:\n\n"

                "💔 I'm heartbroken\n"
                "🌧 I want something emotional\n"
                "🚗 I need music for a night drive\n"
                "🔥 Give me festival energy\n"
                "🖤 I want dark bass\n\n"

                "I'll recommend music from "
                "NOT YOUR VIBE Collection.",

                mood_menu()

            )


        # -------------------------------------------------
        # HELP
        # -------------------------------------------------

        elif text == "/help":

            send_message(

                chat_id,

                "🎧 NOT YOUR VIBE MUSIC BOT\n\n"

                "/start - Start\n"
                "/mood - Choose mood\n"
                "/ai - AI Suggestion\n"
                "/help - Help"

            )


        # -------------------------------------------------
        # NORMAL AI REQUEST
        # -------------------------------------------------

        elif text:

            user_ai_suggestion(

                chat_id,

                text

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


        data = callback.get(
            "data",
            ""
        )


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


        # -------------------------------------------------
        # AI
        # -------------------------------------------------

        if data == "ai_help":

            answer_callback(

                callback_id,

                "🤖 AI Suggestion"

            )


            send_message(

                chat_id,

                "🤖 AI MUSIC SUGGESTION\n\n"

                "Tell me what you're feeling.\n\n"

                "For example:\n"

                "💔 heartbreak\n"
                "🌧 emotional\n"
                "🚗 night drive\n"
                "🔥 festival\n"
                "🖤 dark bass\n\n"

                "I'll search your "
                "NOT YOUR VIBE collection."

            )


        # -------------------------------------------------
        # CHANGE MOOD
        # -------------------------------------------------

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


        # -------------------------------------------------
        # NEXT
        # -------------------------------------------------

        elif data == "next_music":

            answer_callback(

                callback_id,

                "🔀 Finding another track..."

            )


            if MUSIC_IDS:

                message_id = random.choice(
                    MUSIC_IDS
                )


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

                else:

                    send_message(

                        chat_id,

                        "❌ Couldn't send the track."

                    )

            else:

                send_message(

                    chat_id,

                    "❌ No music available."

                )


        # -------------------------------------------------
        # MOOD
        # -------------------------------------------------

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
            #
            # This NEVER blocks because AI analysis
            # is missing.
            #
            # AI -> analyzed mood -> fallback channel.

            send_mood_music(

                chat_id,

                mood

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


    print(
        "================================"
    )

    print(
        "NOT YOUR VIBE MUSIC BOT"
    )

    print(
        "SERVER STARTING..."
    )

    print(
        "================================"
    )


    app.run(

        host="0.0.0.0",

        port=port

)
