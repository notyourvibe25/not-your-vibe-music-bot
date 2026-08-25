import os
import random
import json
import re
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

ai = None

if OPENAI_API_KEY:
    try:
        ai = OpenAI(
            api_key=OPENAI_API_KEY
        )

        print("OPENAI CONNECTED")

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
# OLD MESSAGE IDS
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


# =========================================================
# MOOD POOLS
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
# TEXT NORMALIZER
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
# GENRE KEYWORDS
# =========================================================

GENRE_KEYWORDS = {

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
        "drum n bass",
        "dnb"
    ],

    "riddim": [
        "riddim"
    ],

    "jersey_club": [
        "jersey club",
        "jerseyclub"
    ],

    "jungle_terror": [
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
        "breakbeat",
        "breakbeats"
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


# =========================================================
# DETECT GENRE
# =========================================================

def detect_genre(caption):

    text = normalize(
        caption
    )

    for genre, keywords in GENRE_KEYWORDS.items():

        for keyword in keywords:

            if keyword in text:

                return genre

    return ""


# =========================================================
# SAD KEYWORDS
# =========================================================

SAD_KEYWORDS = [

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
    "tears",
    "cry",
    "crying",
    "pain",
    "lost",
    "missing",
    "goodbye",
    "memories",
    "regret",
    "sorrow"

]


# =========================================================
# CHECK SAD
# =========================================================

def is_sad(text):

    text = normalize(
        text
    )

    for word in SAD_KEYWORDS:

        if word in text:

            return True

    return False


# =========================================================
# KEYWORD MOOD DETECTION
# =========================================================
#
# This means the bot can work WITHOUT AI.
# =========================================================

MOOD_KEYWORDS = {

    "love": [

        "love",
        "romantic",
        "romance",
        "lover",
        "loving",
        "kiss",
        "relationship",
        "couple",
        "passion",
        "heart"

    ],

    "chill": [

        "chill",
        "relax",
        "relaxing",
        "calm",
        "smooth",
        "dreamy",
        "peaceful",
        "laid back",
        "ambient"

    ],

    "hype": [

        "hype",
        "festival",
        "party",
        "big drop",
        "mainstage",
        "crowd",
        "anthem",
        "massive",
        "banger"

    ],

    "dark": [

        "dark",
        "evil",
        "sinister",
        "aggressive",
        "industrial",
        "heavy",
        "ominous",
        "distorted"

    ],

    "energetic": [

        "energetic",
        "energy",
        "powerful",
        "high energy",
        "fast",
        "intense",
        "hard hitting",
        "power"

    ],

    "night": [

        "night",
        "night drive",
        "driving",
        "drive",
        "neon",
        "midnight",
        "late night",
        "city lights"

    ],

    "melodic": [

        "melodic",
        "melody",
        "euphoric",
        "atmospheric",
        "beautiful",
        "emotional",
        "uplifting",
        "harmonic"

    ]

}


# =========================================================
# AI MOOD ANALYSIS
# =========================================================

def ai_analyze(caption):

    if not ai:

        return []

    if not caption:

        return []


    try:

        response = ai.responses.create(

            model="gpt-5-mini",

            instructions="""

You are the mood classifier for
NOT YOUR VIBE EDM MUSIC BOT.

Analyze ONLY the music caption.

Choose zero or more from:

sad
love
chill
hype
dark
energetic
night
melodic

IMPORTANT:

Do not guess randomly.

Use:
Genre
Mood
Vibe
Energy
Description

Return ONLY JSON array.

Examples:

"Heartbreak / Lonely / Melancholic"
=> ["sad"]

"Romantic / Love / Warm"
=> ["love"]

"Relaxing / Dreamy / Calm"
=> ["chill"]

"Festival / Massive Drop / Party"
=> ["hype","energetic"]

"Dark / Aggressive / Heavy"
=> ["dark","energetic"]

"Night Drive / Neon / Midnight"
=> ["night"]

"Melodic / Euphoric / Atmospheric"
=> ["melodic"]

""",

            input=caption

        )


        raw = response.output_text.strip()

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

                if mood not in valid:

                    valid.append(
                        mood
                    )


        return valid


    except Exception as e:

        print(
            "AI ERROR:",
            e
        )

        return []


# =========================================================
# ADD SONG TO MOOD POOL
# =========================================================

def analyze_song(
    message_id,
    caption
):

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
    # SAD
    #
    # STRICT:
    # MELODIC DUBSTEP ONLY
    # =====================================================

    if genre == "melodic_dubstep":

        if is_sad(caption):

            if message_id not in MOOD_MUSIC["sad"]:

                MOOD_MUSIC["sad"].append(
                    message_id
                )


            SONGS[message_id]["moods"].append(
                "sad"
            )


    # =====================================================
    # OTHER MOODS
    # =====================================================

    text = normalize(
        caption
    )


    for mood, keywords in MOOD_KEYWORDS.items():

        if mood == "sad":

            continue


        matched = False


        for keyword in keywords:

            if keyword in text:

                matched = True

                break


        if matched:

            if message_id not in MOOD_MUSIC[mood]:

                MOOD_MUSIC[mood].append(
                    message_id
                )


            if mood not in SONGS[message_id]["moods"]:

                SONGS[message_id]["moods"].append(
                    mood
                )


    # =====================================================
    # AI ADDITIONAL ANALYSIS
    # =====================================================

    ai_moods = ai_analyze(
        caption
    )


    for mood in ai_moods:

        # Sad remains STRICT
        if mood == "sad":

            if genre == "melodic_dubstep":

                if is_sad(caption):

                    if message_id not in MOOD_MUSIC["sad"]:

                        MOOD_MUSIC["sad"].append(
                            message_id
                        )

                    if "sad" not in SONGS[message_id]["moods"]:

                        SONGS[message_id]["moods"].append(
                            "sad"
                        )

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
        "ANALYZED:",
        message_id,
        genre,
        SONGS[message_id]["moods"]
    )


# =========================================================
# ADD NEW CHANNEL SONG
# =========================================================

def add_song(
    message_id,
    caption
):

    if message_id not in MUSIC_IDS:

        MUSIC_IDS.append(
            message_id
        )


    analyze_song(
        message_id,
        caption
    )


# =========================================================
# TELEGRAM REQUEST
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
            "TELEGRAM ERROR:",
            e
        )

        return {
            "ok": False,
            "description": str(e)
        }


# =========================================================
# SEND TEXT
# =========================================================

def send_message(
    chat_id,
    text,
    keyboard=None
):

    data = {

        "chat_id":
            chat_id,

        "text":
            text

    }


    if keyboard:

        data["reply_markup"] = keyboard


    return telegram(
        "sendMessage",
        data
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
# GET MUSIC FOR MOOD
# =========================================================

def get_mood_songs(mood):

    songs = list(
        dict.fromkeys(
            MOOD_MUSIC.get(
                mood,
                []
            )
        )
    )


    return songs


# =========================================================
# SEND MOOD MUSIC
# =========================================================

def send_mood_music(
    chat_id,
    mood
):

    songs = get_mood_songs(
        mood
    )


    # =====================================================
    # IF MOOD POOL EXISTS
    # =====================================================

    if songs:

        random.shuffle(
            songs
        )


        # Try several messages in case
        # one message cannot be copied.

        for message_id in songs[:20]:

            result = copy_music(

                chat_id,

                message_id

            )


            if result.get("ok"):

                send_message(

                    chat_id,

                    f"{MOOD_NAMES[mood]}\n\n"
                    "🎧 Here's your track.",

                    music_buttons()

                )

                return True


    # =====================================================
    # SAD FALLBACK
    #
    # STRICTLY MELODIC DUBSTEP
    #
    # We NEVER send random genres for Sad.
    # =====================================================

    if mood == "sad":

        melodic_songs = []


        for message_id in MUSIC_IDS:

            song = SONGS.get(
                message_id,
                {}
            )


            genre = song.get(
                "genre",
                ""
            )


            if genre == "melodic_dubstep":

                melodic_songs.append(
                    message_id
                )


        if melodic_songs:

            random.shuffle(
                melodic_songs
            )


            for message_id in melodic_songs[:20]:

                result = copy_music(

                    chat_id,

                    message_id

                )


                if result.get("ok"):

                    send_message(

                        chat_id,

                        "😢 SAD\n\n"
                        "🎧 Melodic Dubstep for you.",

                        music_buttons()

                    )

                    return True


    # =====================================================
    # LAST FALLBACK
    #
    # For OTHER moods only.
    # =====================================================

    if mood != "sad":

        fallback = list(
            MUSIC_IDS
        )


        random.shuffle(
            fallback
        )


        for message_id in fallback[:20]:

            result = copy_music(

                chat_id,

                message_id

            )


            if result.get("ok"):

                send_message(

                    chat_id,

                    f"{MOOD_NAMES[mood]}\n\n"
                    "🎧 Here's a track from the collection.",

                    music_buttons()

                )

                return True


    # =====================================================
    # NOTHING AVAILABLE
    # =====================================================

    send_message(

        chat_id,

        f"{MOOD_NAMES[mood]}\n\n"
        "⚠️ No track is available yet."

    )


    return False


# =========================================================
# NEXT
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

            send_message(

                chat_id,

                "🔀 Next track 👇",

                music_buttons()

            )

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

    if not ai:

        return (
            "⚠️ AI is not connected.\n"
            "Mood buttons are still available."
        )


    try:

        response = ai.responses.create(

            model="gpt-5-mini",

            instructions="""

You are NOT YOUR VIBE Music Assistant.

Help users choose EDM moods.

Available:

😢 Sad
❤️ Love
🌙 Chill
🔥 Hype
🖤 Dark
⚡ Energetic
🚗 Night Drive
🌌 Melodic

Sad specifically means:
Melodic Dubstep + Emotional /
Sad / Melancholic / Heartbreak /
Lonely / Nostalgic.

Understand Burmese and English.

Be concise.

Do not invent song names.

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
            "⚠️ AI temporarily unavailable."
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
                "NEW SONG:",
                message_id
            )

            print(
                "CAPTION:",
                caption
            )


            add_song(

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


        if text == "/start":

            send_message(

                chat_id,

                "🎧 NOT YOUR VIBE MUSIC\n\n"
                "Welcome 🔥\n\n"
                "Choose your mood 👇",

                mood_menu()

            )


        elif text == "/mood":

            send_message(

                chat_id,

                "🎧 Choose your mood 👇",

                mood_menu()

            )


        elif text == "/ai":

            send_message(

                chat_id,

                "🤖 AI MUSIC ASSISTANT\n\n"
                "Tell me what you're feeling.\n\n"
                "Example:\n"
                "I'm lonely tonight."

            )


        elif text == "/help":

            send_message(

                chat_id,

                "🎧 NOT YOUR VIBE MUSIC BOT\n\n"
                "/start - Start\n"
                "/mood - Mood\n"
                "/ai - AI\n"
                "/help - Help"

            )


        elif text:

            answer = ask_ai(
                text
            )


            send_message(

                chat_id,

                "🤖 AI Music Assistant\n\n"
                + answer,

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
                "I want emotional music."

            )


        # =================================================
        # CHANGE MOOD
        # =================================================

        elif data == "change_mood":

            answer_callback(

                callback_id,

                "🎧 Choose mood"

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

                "🔀 Finding next..."

            )


            send_next(
                chat_id
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


            send_mood_music(

                chat_id,

                mood

            )


    return "OK"


# =========================================================
# WEBHOOK
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
