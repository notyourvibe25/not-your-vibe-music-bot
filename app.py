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

DB_FILE = "music.db"


# =========================================================
# OPENAI
# =========================================================

ai_client = None

if OPENAI_API_KEY:

    try:

        ai_client = OpenAI(
            api_key=OPENAI_API_KEY
        )

        print("OpenAI: CONNECTED")

    except Exception as e:

        print(
            "OpenAI init error:",
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
# YOUR CHANNEL MUSIC IDS
# =========================================================

MUSIC_IDS = [

    2045,
    1995,
    1834,
    2075,
    2105,
    2125,
    2115,
    1864,
    1874,
    1844,
    2095,
    1905,
    1975,
    1925,
    1935,
    1945,
    1965,
    2035,
    1985,
    2005,
    1955,
    2025,
    1915,
    2015,
    2055,
    1895,
    1885,
    2065,

    1824,
    1814,
    1802,
    1782,
    1772,
    1762,
    1752,
    1739,
    1729,
    1711,
    1701,
    1692,
    1643,
    1632,
    1622,
    1612,
    1603,
    1594,
    1585,
    1560,
    1570,
    1549,
    1544,
    1539,
    1534,
    1529,
    1524,
    1514,
    1503,
    1495,
    1485,
    1476,
    1457,
    1452,
    1441,
    1391,
    1379,
    1369,
    1359,
    1348,
    1336,
    1326,
    1306,
    1291,
    1281,
    1276,
    1266,
    1262,
    1252,
    1251,
    1241,
    1237,
    1231,
    1221,
    1217,
    1207,
    1205,
    1202,
    1192,
    1183,
    1173,
    1165,
    1155,
    1150,
    1140,
    1130,
    1119,
    1117,
    1093,
    1017,
    985,
    943,
    948,
    892,
    855,
    826,
    784,
    794,
    762,
    696,
    685,
    675,
    661,
    650,
    643

]


# Remove duplicates

MUSIC_IDS = list(
    dict.fromkeys(
        MUSIC_IDS
    )
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

    db = get_db()

    cursor = db.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS songs (

            message_id INTEGER PRIMARY KEY,

            caption TEXT DEFAULT '',

            moods TEXT DEFAULT '[]',

            submoods TEXT DEFAULT '[]',

            genre TEXT DEFAULT '',

            analyzed INTEGER DEFAULT 0

        )
        """
    )

    db.commit()

    db.close()


init_database()


# =========================================================
# ADD OLD SONG IDS TO DATABASE
# =========================================================

def ensure_old_ids():

    db = get_db()

    cursor = db.cursor()

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

            (
                message_id,
            )

        )

    db.commit()

    db.close()


ensure_old_ids()


# =========================================================
# SAVE SONG
# =========================================================

def save_song(
    message_id,
    caption,
    moods,
    submoods,
    genre
):

    db = get_db()

    cursor = db.cursor()

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
        VALUES (?, ?, ?, ?, ?, 1)
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

            genre

        )

    )

    db.commit()

    db.close()


# =========================================================
# GET ALL SONGS
# =========================================================

def get_songs():

    db = get_db()

    cursor = db.cursor()

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

    db.close()


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


        songs.append(

            {

                "id": row[0],

                "caption": row[1] or "",

                "moods": moods,

                "submoods": submoods,

                "genre": row[4] or "",

                "analyzed": row[5]

            }

        )


    return songs


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
            "Telegram:",
            method,
            result
        )

        return result


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
# COPY CHANNEL MUSIC
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
# MAIN MENU
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
                        "🤖 AI Suggestion",

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
                        "🤖 Better Suggestion",

                    "callback_data":
                        "ai_help"
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
# AI CLASSIFY SONG
# =========================================================

def classify_song(
    caption
):

    if not ai_client:

        return [], [], ""


    if not caption:

        return [], [], ""


    try:

        response = ai_client.responses.create(

            model="gpt-5-mini",

            instructions="""

You are an EDM music mood classifier.

Analyze the provided song title/caption.

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

Submoods can include:

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
night_drive
festival
party
bass
aggressive
dark_bass
moody
atmospheric
cinematic
uplifting
nostalgic

Genre can include:

Future Bass
Melodic Dubstep
Future Riddim
Trap
House
Future Garage
Dubstep
EDM
Melodic House
etc.

IMPORTANT:

Do NOT assign every mood.

Sad music should actually feel emotional,
melancholic, heartbreaking, lonely, deep
or nostalgic.

Hype music should actually be energetic,
festival, powerful or party-oriented.

Dark music should actually be dark,
moody, aggressive or dark-bass oriented.

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


        for item in result.get(
            "submoods",
            []
        ):

            if isinstance(
                item,
                str
            ):

                submoods.append(
                    item.lower()
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
            "AI classify error:",
            e
        )

        return [], [], ""


# =========================================================
# PROCESS NEW CHANNEL SONG
# =========================================================

def process_channel_song(
    message_id,
    caption
):

    print(
        "Analyzing channel song:",
        message_id
    )


    moods, submoods, genre = classify_song(
        caption
    )


    if ai_client and caption:

        save_song(

            message_id,

            caption,

            moods,

            submoods,

            genre

        )


        print(
            "AI MOODS:",
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

    else:

        print(
            "AI unavailable. "
            "Song saved without analysis."
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


    songs = get_songs()


    analyzed = [

        song

        for song in songs

        if song["analyzed"] == 1

        and len(song["moods"]) > 0

    ]


    if not analyzed:

        return None


    catalog = []


    for song in analyzed:

        catalog.append(

            {

                "message_id":
                    song["id"],

                "caption":
                    song["caption"],

                "moods":
                    song["moods"],

                "submoods":
                    song["submoods"],

                "genre":
                    song["genre"]

            }

        )


    try:

        response = ai_client.responses.create(

            model="gpt-5-mini",

            instructions="""

You are the NOT YOUR VIBE AI music recommender.

You can ONLY select a song from the supplied
CHANNEL CATALOG.

NEVER invent a song.
NEVER invent a message_id.

The recommendation must match the requested
mood.

SAD:

Prefer:
emotional
heartbreak
melancholic
lonely
deep
nostalgic

Avoid:
hype
party
festival
aggressive

LOVE:

Prefer:
romantic
love
dreamy
emotional
missing

CHILL:

Prefer:
relaxing
smooth
dreamy
atmospheric

HYPE:

Prefer:
festival
party
energetic
bass
powerful

DARK:

Prefer:
dark
dark_bass
moody
aggressive

ENERGETIC:

Prefer:
energetic
festival
bass
powerful

NIGHT DRIVE:

Prefer:
night_drive
late_night
atmospheric
deep
melodic

MELODIC:

Prefer:
melodic
dreamy
emotional
atmospheric

Return ONLY:

{
  "message_id": 123,
  "reason": "short reason"
}

The message_id MUST exist in the catalog.

""",

            input=(

                "USER:\n"

                + user_text

                + "\n\n"

                + "PREFERRED MOOD:\n"

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

            for song in analyzed

        ]


        if message_id not in valid_ids:

            return None


        selected = None


        for song in analyzed:

            if song["id"] == message_id:

                selected = song

                break


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
            "AI recommend error:",
            e
        )

        return None


# =========================================================
# GET ANALYZED MOOD SONGS
# =========================================================

def get_mood_songs(
    mood
):

    songs = get_songs()


    result = []


    for song in songs:

        if song["analyzed"] != 1:

            continue


        if mood in song["moods"]:

            result.append(
                song
            )


    return result


# =========================================================
# FALLBACK MUSIC
# =========================================================

def fallback_music(
    mood
):

    # First try AI analyzed songs
    mood_songs = get_mood_songs(
        mood
    )


    if mood_songs:

        return random.choice(
            mood_songs
        )


    # If no AI analyzed song yet,
    # use your existing channel IDs.
    #
    # This guarantees that pressing
    # a mode still sends music.

    if MUSIC_IDS:

        message_id = random.choice(
            MUSIC_IDS
        )


        return {

            "id":
                message_id,

            "caption":
                "",

            "moods":
                [],

            "submoods":
                [],

            "genre":
                "",

            "analyzed":
                0

        }


    return None


# =========================================================
# SEND RECOMMENDATION
# =========================================================

def send_recommendation(
    chat_id,
    mood
):

    # =====================================================
    # TRY AI
    # =====================================================

    recommendation = ai_recommend(

        user_text=
            MOOD_NAMES[mood],

        preferred_mood=
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
                ]

                + "\n\n"

                + "🎧 From NOT YOUR VIBE "
                  "MP3 Collection",

                music_buttons()

            )

            return


    # =====================================================
    # FALLBACK
    # =====================================================

    song = fallback_music(
        mood
    )


    if not song:

        send_message(

            chat_id,

            "❌ No music found.",

            mood_menu()

        )

        return


    result = copy_music(

        chat_id,

        song["id"]

    )


    if result.get("ok"):

        if song["analyzed"] == 1:

            text = (

                MOOD_NAMES[mood]

                + "\n\n"

                + "🎧 Here's a matching track "
                  "from the collection."

            )

        else:

            text = (

                MOOD_NAMES[mood]

                + "\n\n"

                + "🎧 Here's a track from "
                  "NOT YOUR VIBE Collection.\n\n"

                + "🤖 AI is still learning "
                  "the collection, so "
                  "recommendations will "
                  "become more accurate "
                  "as new songs are analyzed."

            )


        send_message(

            chat_id,

            text,

            music_buttons()

        )


    else:

        send_message(

            chat_id,

            "❌ Couldn't send the music."

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


            process_channel_song(

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

                "Choose your mood below "
                "or ask AI for a suggestion. 👇",

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

                "🚗 Music for a night drive\n"

                "🔥 I need festival energy\n"

                "🖤 I want dark bass\n"

                "🌌 Emotional future bass\n\n"

                "I'll recommend ONLY music "
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
        # AI CHAT / SUGGESTION
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


                if result.get("ok"):

                    send_message(

                        chat_id,

                        "🤖 AI SUGGESTION\n\n"

                        + recommendation[
                            "reason"
                        ]

                        + "\n\n"

                        + "🎧 From NOT YOUR VIBE "
                          "MP3 Collection",

                        music_buttons()

                    )

                else:

                    send_message(

                        chat_id,

                        "❌ I found the track, "
                        "but Telegram couldn't "
                        "send it."

                    )


            else:

                send_message(

                    chat_id,

                    "🤖 I couldn't find a strong "
                    "AI match yet.\n\n"

                    "Try describing your feeling "
                    "more specifically.\n\n"

                    "Example:\n"
                    "💔 heartbreak\n"
                    "🌧️ emotional night\n"
                    "🚗 late night drive\n"
                    "🖤 dark bass",

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

                "🤖 AI MUSIC SUGGESTION\n\n"

                "Tell me what you're feeling.\n\n"

                "Examples:\n\n"

                "💔 I'm heartbroken\n"
                "🌧️ Something emotional\n"
                "🚗 Night drive music\n"
                "🔥 Festival hype\n"
                "🖤 Dark bass\n"
                "🌌 Emotional future bass\n\n"

                "I'll search ONLY your "
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
        # NEXT MUSIC
        # =================================================

        elif data == "next_music":

            answer_callback(

                callback_id,

                "🔀 Finding another track..."

            )


            songs = get_songs()


            analyzed = [

                song

                for song in songs

                if song["analyzed"] == 1

            ]


            if analyzed:

                song = random.choice(
                    analyzed
                )


                result = copy_music(

                    chat_id,

                    song["id"]

                )


            else:

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


        # =================================================
        # MOOD BUTTONS
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


            # This is the important part:
            #
            # 1. AI matching first
            # 2. If AI has no analyzed song,
            #    fallback to channel music
            #
            # Therefore the mode will ALWAYS
            # try to send music.

            send_recommendation(

                chat_id,

                mood

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


    print(
        "Starting NOT YOUR VIBE Music Bot..."
    )


    app.run(

        host="0.0.0.0",

        port=port

)
