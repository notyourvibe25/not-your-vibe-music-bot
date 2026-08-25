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
# SONG DATABASE
# =========================================================

SONGS = {}


for message_id in MUSIC_IDS:

    SONGS[message_id] = {

        "caption": "",

        "genre": "",

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
# TELEGRAM
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

        "chat_id":
            chat_id,

        "text":
            text

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
# NORMALIZE TEXT
# =========================================================

def normalize(text):

    if not text:

        return ""

    text = text.lower()

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
# GENRE DETECTION
# =========================================================

def detect_genre(caption):

    text = normalize(caption)

    genres = {

        "melodic_dubstep": [
            "melodic dubstep",
            "melodic-dubstep",
            "melodicdubstep",
            "emotional dubstep"
        ],

        "psytrance": [
            "psytrance",
            "psy trance"
        ],

        "hardstyle": [
            "hardstyle"
        ],

        "hardtechno": [
            "hard techno",
            "hardtechno"
        ],

        "drum_and_bass": [
            "drum and bass",
            "drum & bass",
            "dnb",
            "drum n bass"
        ],

        "riddim": [
            "riddim"
        ],

        "jersey_club": [
            "jersey club",
            "jerseyclub"
        ],

        "terror": [
            "jungle terror",
            "jungleterror"
        ],

        "melodic_techno": [
            "melodic techno"
        ],

        "hardcore": [
            "hardcore"
        ],

        "tech_house": [
            "tech house"
        ],

        "breakbeats": [
            "breakbeats",
            "breakbeat"
        ],

        "rawtrap": [
            "rawtrap",
            "raw trap"
        ],

        "sub_bass": [
            "sub bass",
            "subbass"
        ],

        "tearout": [
            "tearout",
            "tear out"
        ],

        "dubstep": [
            "dubstep"
        ],

        "future_rave": [
            "future rave"
        ],

        "afro_house": [
            "afro house"
        ],

        "midtempo": [
            "midtempo",
            "mid tempo"
        ],

        "liquid_dnb": [
            "liquid drum and bass",
            "liquid dnb",
            "liquid d&b"
        ]

    }


    for genre, keywords in genres.items():

        for keyword in keywords:

            if keyword in text:

                return genre


    return ""


# =========================================================
# SAD KEYWORDS
# =========================================================

SAD_WORDS = [

    "sad",
    "emotional",
    "melancholic",
    "melancholy",
    "heartbreak",
    "heartbroken",
    "broken heart",
    "lonely",
    "loneliness",
    "nostalgic",
    "nostalgia",
    "tears",
    "cry",
    "crying",
    "pain",
    "lost",
    "goodbye",
    "memories",
    "emotional vocal",
    "emotional vocals"

]


# =========================================================
# CHECK SAD
# =========================================================

def is_sad_caption(caption):

    text = normalize(caption)

    for word in SAD_WORDS:

        if word in text:

            return True

    return False


# =========================================================
# AI CLASSIFIER
#
# SAD IS ALSO ALLOWED HERE,
# BUT THE GENRE RULE BELOW HAS PRIORITY.
# =========================================================

def ai_classify(caption):

    if not ai_client:

        return []


    if not caption:

        return []


    try:

        response = ai_client.responses.create(

            model="gpt-5-mini",

            instructions="""

You are the music classifier for
NOT YOUR VIBE.

Analyze the provided EDM music caption.

Choose ONLY from:

sad
love
chill
hype
dark
energetic
night
melodic

You may return multiple moods.

Important:

Do not classify only from the genre.

Look at:
- Mood
- Vibe
- Energy
- Description
- Emotional words
- Atmosphere

Examples:

Emotional / Heartbreak / Lonely
=> ["sad"]

Love / Romantic / Warm
=> ["love"]

Chill / Relaxing / Dreamy
=> ["chill"]

Festival / Party / Big Drop
=> ["hype","energetic"]

Dark / Aggressive / Industrial
=> ["dark","energetic"]

Night Drive / Neon / Late Night
=> ["night","melodic"]

Melodic / Euphoric / Atmospheric
=> ["melodic"]

Return ONLY JSON.

Example:
["dark","energetic"]

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

            return []


        valid = []


        for mood in moods:

            if mood in MOODS:

                if mood not in valid:

                    valid.append(mood)


        return valid


    except Exception as e:

        print(
            "AI CLASSIFY ERROR:",
            e
        )

        return []


# =========================================================
# ANALYZE SONG
# =========================================================

def analyze_song(
    message_id,
    caption
):

    if not message_id:

        return


    genre = detect_genre(
        caption
    )


    SONGS[message_id] = {

        "caption":
            caption,

        "genre":
            genre,

        "moods":
            []

    }


    # =====================================================
    # SAD HARD RULE
    #
    # MUST BE MELODIC DUBSTEP
    #
    # AND MUST HAVE SAD/EMOTIONAL VIBE
    # =====================================================

    if genre == "melodic_dubstep":

        if is_sad_caption(caption):

            if message_id not in MOOD_MUSIC["sad"]:

                MOOD_MUSIC["sad"].append(
                    message_id
                )


            SONGS[message_id]["moods"].append(
                "sad"
            )


    # =====================================================
    # AI FOR ALL OTHER MOODS
    # =====================================================

    ai_moods = ai_classify(
        caption
    )


    for mood in ai_moods:

        # Never allow AI to override
        # the special Sad genre rule.

        if mood == "sad":

            if genre == "melodic_dubstep":

                if is_sad_caption(caption):

                    if message_id not in MOOD_MUSIC["sad"]:

                        MOOD_MUSIC["sad"].append(
                            message_id
                        )

            continue


        if mood not in MOODS:

            continue


        if message_id not in MOOD_MUSIC[mood]:

            MOOD_MUSIC[mood].append(
                message_id
            )


        if mood not in SONGS[message_id]["moods"]:

            SONGS[message_id]["moods"].append(
                mood
            )


    print(
        "SONG ANALYZED:",
        message_id
    )

    print(
        "GENRE:",
        genre
    )

    print(
        "MOODS:",
        SONGS[message_id]["moods"]
    )


# =========================================================
# ADD NEW SONG
# =========================================================

def add_new_song(
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
# REBUILD OLD SONG DATABASE
#
# IMPORTANT:
# Existing IDs without captions cannot be AI-classified.
# They remain available as general fallback.
# New channel posts are automatically analyzed.
# =========================================================

def rebuild_database():

    print(
        "DATABASE READY:",
        len(MUSIC_IDS),
        "songs"
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
# SEND MUSIC
# =========================================================

def send_music(
    chat_id,
    mood
):

    # =====================================================
    # SAD
    #
    # STRICT:
    # MELODIC DUBSTEP + SAD CAPTION
    # =====================================================

    if mood == "sad":

        songs = list(
            dict.fromkeys(
                MOOD_MUSIC["sad"]
            )
        )


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


    # =====================================================
    # NO MATCH
    # =====================================================

    if not songs:

        send_message(

            chat_id,

            f"{MOOD_NAMES[mood]}\n\n"
            "⚠️ I couldn't find a matching track "
            "in the analyzed collection yet.\n\n"
            "Try another mood or add more "
            "caption-tagged songs."

        )

        return False


    # =====================================================
    # RANDOM
    # =====================================================

    random.shuffle(
        songs
    )


    # =====================================================
    # TRY UP TO 15
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

        "❌ Couldn't send the music.\n"
        "Please try again."

    )


    return False


# =========================================================
# NEXT MUSIC
# =========================================================

def send_next(
    chat_id
):

    songs = list(
        MUSIC_IDS
    )


    random.shuffle(
        songs
    )


    for message_id in songs[:20]:

        result = copy_music(

            chat_id,

            message_id
        )


        if result.get("ok"):

            return True


    send_message(

        chat_id,

        "❌ Couldn't send the next track."

    )


    return False


# =========================================================
# AI CHAT
# =========================================================

def ask_ai(text):

    if not ai_client:

        return (

            "⚠️ AI is not connected.\n\n"
            "You can still use the mood buttons."

        )


    try:

        response = ai_client.responses.create(

            model="gpt-5-mini",

            instructions="""

You are NOT YOUR VIBE MUSIC AI.

Help users choose EDM music moods.

Moods:

😢 Sad
❤️ Love
🌙 Chill
🔥 Hype
🖤 Dark
⚡ Energetic
🚗 Night Drive
🌌 Melodic

Important:

Sad means:
Melodic Dubstep + Emotional /
Sad / Melancholic / Heartbreak /
Lonely / Nostalgic.

Understand Burmese and English.

Keep answers concise.

Do not invent specific songs.

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
            "⚠️ AI is temporarily unavailable."
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


            print(
                "NEW CHANNEL POST:",
                message_id
            )


            print(
                "CAPTION:",
                caption
            )


            add_new_song(

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
                "Choose your mood below 👇",

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
                "Example:\n"
                "I'm lonely tonight and want "
                "emotional music."

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
                "I want emotional music "
                "for a night drive."

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


            if send_next(
                chat_id
            ):

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


            if send_music(

                chat_id,

                mood

            ):

                send_message(

                    chat_id,

                    f"{MOOD_NAMES[mood]}\n\n"
                    "🎧 Here's your matching track.",

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
# START
# =========================================================

rebuild_database()


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
