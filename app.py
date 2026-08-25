import os
import random
import json
import re
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
# OLD CHANNEL MESSAGE IDS
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


MUSIC_IDS = list(
    dict.fromkeys(
        MUSIC_IDS
    )
)


# =========================================================
# SONG DATABASE
# =========================================================

SONGS = {}


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
# NORMALIZE TEXT
# =========================================================

def normalize(text):

    if not text:

        return ""

    text = str(
        text
    ).lower()

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

def detect_genre(text):

    text = normalize(
        text
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
    "sorrow",
    "depressed",
    "dark emotional"

]


# =========================================================
# LOVE KEYWORDS
# =========================================================

LOVE_KEYWORDS = [

    "love",
    "romantic",
    "romance",
    "lover",
    "loving",
    "kiss",
    "relationship",
    "couple",
    "passion"

]


# =========================================================
# CHILL KEYWORDS
# =========================================================

CHILL_KEYWORDS = [

    "chill",
    "relax",
    "relaxing",
    "calm",
    "smooth",
    "dreamy",
    "peaceful",
    "ambient"

]


# =========================================================
# HYPE KEYWORDS
# =========================================================

HYPE_KEYWORDS = [

    "hype",
    "festival",
    "party",
    "mainstage",
    "anthem",
    "massive",
    "banger",
    "big drop"

]


# =========================================================
# DARK KEYWORDS
# =========================================================

DARK_KEYWORDS = [

    "dark",
    "evil",
    "sinister",
    "aggressive",
    "industrial",
    "ominous",
    "distorted"

]


# =========================================================
# ENERGETIC KEYWORDS
# =========================================================

ENERGETIC_KEYWORDS = [

    "energetic",
    "energy",
    "powerful",
    "high energy",
    "fast",
    "intense",
    "hard hitting"

]


# =========================================================
# NIGHT KEYWORDS
# =========================================================

NIGHT_KEYWORDS = [

    "night",
    "night drive",
    "driving",
    "drive",
    "neon",
    "midnight",
    "late night",
    "city lights"

]


# =========================================================
# MELODIC KEYWORDS
# =========================================================

MELODIC_KEYWORDS = [

    "melodic",
    "melody",
    "euphoric",
    "atmospheric",
    "beautiful",
    "uplifting",
    "harmonic"

]


# =========================================================
# TEXT MATCH
# =========================================================

def has_keyword(
    text,
    keywords
):

    text = normalize(
        text
    )


    for keyword in keywords:

        if keyword in text:

            return True


    return False


# =========================================================
# SAD CHECK
# =========================================================

def is_melodic_dubstep(text):

    return has_keyword(

        text,

        GENRE_KEYWORDS[
            "melodic_dubstep"
        ]

    )


def is_sad(text):

    return has_keyword(

        text,

        SAD_KEYWORDS

    )


# =========================================================
# AI ANALYZER
# =========================================================

def ai_analyze(
    caption
):

    if not ai:

        return []


    if not caption:

        return []


    try:

        response = ai.responses.create(

            model="gpt-5-mini",

            instructions="""

You are the music mood classifier
for NOT YOUR VIBE.

Analyze the provided music caption.

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

For SAD:

Only classify as SAD when the
music is Melodic Dubstep AND
the caption indicates:

Emotional
Sad
Melancholic
Heartbreak
Lonely
Nostalgic
Pain
Lost
Tears
Sorrow

Do NOT classify other genres as SAD.

Return ONLY a JSON array.

Example:

["sad"]

or

["hype","energetic"]

or

["night","melodic"]

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
            "AI ANALYZE ERROR:",
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

    caption = caption or ""


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

    if (

        is_melodic_dubstep(
            caption
        )

        and

        is_sad(
            caption
        )

    ):

        if message_id not in MOOD_MUSIC["sad"]:

            MOOD_MUSIC["sad"].append(
                message_id
            )


        SONGS[message_id]["moods"].append(
            "sad"
        )


    # =====================================================
    # LOVE
    # =====================================================

    if has_keyword(
        caption,
        LOVE_KEYWORDS
    ):

        MOOD_MUSIC["love"].append(
            message_id
        )

        SONGS[message_id]["moods"].append(
            "love"
        )


    # =====================================================
    # CHILL
    # =====================================================

    if has_keyword(
        caption,
        CHILL_KEYWORDS
    ):

        MOOD_MUSIC["chill"].append(
            message_id
        )

        SONGS[message_id]["moods"].append(
            "chill"
        )


    # =====================================================
    # HYPE
    # =====================================================

    if has_keyword(
        caption,
        HYPE_KEYWORDS
    ):

        MOOD_MUSIC["hype"].append(
            message_id
        )

        SONGS[message_id]["moods"].append(
            "hype"
        )


    # =====================================================
    # DARK
    # =====================================================

    if has_keyword(
        caption,
        DARK_KEYWORDS
    ):

        MOOD_MUSIC["dark"].append(
            message_id
        )

        SONGS[message_id]["moods"].append(
            "dark"
        )


    # =====================================================
    # ENERGETIC
    # =====================================================

    if has_keyword(
        caption,
        ENERGETIC_KEYWORDS
    ):

        MOOD_MUSIC["energetic"].append(
            message_id
        )

        SONGS[message_id]["moods"].append(
            "energetic"
        )


    # =====================================================
    # NIGHT
    # =====================================================

    if has_keyword(
        caption,
        NIGHT_KEYWORDS
    ):

        MOOD_MUSIC["night"].append(
            message_id
        )

        SONGS[message_id]["moods"].append(
            "night"
        )


    # =====================================================
    # MELODIC
    # =====================================================

    if has_keyword(
        caption,
        MELODIC_KEYWORDS
    ):

        MOOD_MUSIC["melodic"].append(
            message_id
        )

        SONGS[message_id]["moods"].append(
            "melodic"
        )


    # =====================================================
    # AI
    # =====================================================

    ai_moods = ai_analyze(
        caption
    )


    for mood in ai_moods:

        # Sad stays strict
        if mood == "sad":

            if (

                is_melodic_dubstep(
                    caption
                )

                and

                is_sad(
                    caption
                )

            ):

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


    # Remove duplicates
    for mood in MOODS:

        MOOD_MUSIC[mood] = list(
            dict.fromkeys(
                MOOD_MUSIC[mood]
            )
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
# ADD SONG
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
# SAD SONGS
# =========================================================

def get_sad_songs():

    songs = list(
        dict.fromkeys(
            MOOD_MUSIC["sad"]
        )
    )


    # =====================================================
    # Check database again
    # =====================================================

    for message_id, song in SONGS.items():

        caption = song.get(
            "caption",
            ""
        )


        if (

            is_melodic_dubstep(
                caption
            )

            and

            is_sad(
                caption
            )

        ):

            if message_id not in songs:

                songs.append(
                    message_id
                )


    return list(
        dict.fromkeys(
            songs
        )
    )


# =========================================================
# SEND SAD
# =========================================================

def send_sad_music(
    chat_id
):

    songs = get_sad_songs()


    print(
        "SAD SONG COUNT:",
        len(songs)
    )


    if not songs:

        send_message(

            chat_id,

            "😢 SAD\n\n"
            "⚠️ Sad collection ထဲမှာ "
            "Melodic Dubstep + Emotional "
            "track မတွေ့သေးပါ။"

        )

        return


    random.shuffle(
        songs
    )


    # =====================================================
    # TRY UP TO 30 SONGS
    # =====================================================

    for message_id in songs[:30]:

        result = copy_music(

            chat_id,

            message_id

        )


        if result.get("ok"):

            send_message(

                chat_id,

                "😢 SAD\n\n"
                "🎧 Melodic Dubstep\n"
                "💔 Emotional / Melancholic",

                music_buttons()

            )

            print(
                "SAD SENT:",
                message_id
            )

            return


    # =====================================================
    # ERROR
    # =====================================================

    send_message(

        chat_id,

        "😢 SAD\n\n"
        "❌ Track ရှိပေမယ့် Telegram က "
        "copy မလုပ်နိုင်သေးပါ။"

    )


# =========================================================
# SEND OTHER MOOD
# =========================================================

def send_mood_music(
    chat_id,
    mood
):

    songs = list(
        dict.fromkeys(
            MOOD_MUSIC.get(
                mood,
                []
            )
        )
    )


    random.shuffle(
        songs
    )


    print(
        mood.upper(),
        "POOL:",
        songs
    )


    # =====================================================
    # FIRST TRY MATCHED MOOD
    # =====================================================

    for message_id in songs[:30]:

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

            return


    # =====================================================
    # FALLBACK FOR OTHER MOODS
    # =====================================================

    fallback = list(
        MUSIC_IDS
    )


    random.shuffle(
        fallback
    )


    for message_id in fallback[:30]:

        result = copy_music(

            chat_id,

            message_id

        )


        if result.get("ok"):

            send_message(

                chat_id,

                f"{MOOD_NAMES[mood]}\n\n"
                "🎧 Here's a track from "
                "NOT YOUR VIBE collection.",

                music_buttons()

            )

            return


    send_message(

        chat_id,

        "❌ Couldn't send a track."

    )


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


    for message_id in songs[:30]:

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
# =========================================================

def ask_ai(
    text
):

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

Available moods:

😢 Sad
❤️ Love
🌙 Chill
🔥 Hype
🖤 Dark
⚡ Energetic
🚗 Night Drive
🌌 Melodic

SAD specifically means:

Melodic Dubstep
+
Emotional / Sad / Melancholic /
Heartbreak / Lonely / Nostalgic.

Understand Burmese and English.

Give short useful answers.

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
                "NEW CHANNEL SONG:",
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
                "I'm sad tonight.\n"
                "I want emotional music.\n"
                "I want music for night driving."

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
                "/ai - AI Assistant\n"
                "/help - Help"

            )


        # =================================================
        # AI CHAT
        # =================================================

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


            # =================================================
            # SAD SPECIAL
            # =================================================

            if mood == "sad":

                send_sad_music(

                    chat_id

                )


            # =================================================
            # OTHER MOODS
            # =================================================

            else:

                send_mood_music(

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

                    "channel_post",

                    "edited_channel_post"

                ]

            },

            timeout=20

        )


        print(
            "WEBHOOK RESULT:",
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
