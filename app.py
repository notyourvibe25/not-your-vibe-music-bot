import os
import json
import random
import re
import requests

from flask import Flask, request
from openai import OpenAI


# =========================================================
# APP
# =========================================================

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
# MOOD KEYWORDS
# =========================================================

MOOD_KEYWORDS = {

    "sad": [
        "sad",
        "emotional",
        "melancholic",
        "heartbreak",
        "heartbroken",
        "lonely",
        "nostalgic",
        "nostalgia",
        "cry",
        "tears",
        "pain",
        "sorrow",
        "depressed",
        "blue",
        "lost"
    ],

    "love": [
        "love",
        "romantic",
        "romance",
        "lover",
        "passion",
        "intimate",
        "affection",
        "relationship",
        "kiss",
        "warm"
    ],

    "chill": [
        "chill",
        "relaxed",
        "relax",
        "smooth",
        "peaceful",
        "calm",
        "dreamy",
        "soft",
        "lofi",
        "laidback"
    ],

    "hype": [
        "hype",
        "party",
        "festival",
        "anthemic",
        "massive",
        "powerful",
        "swagger",
        "club",
        "crowd",
        "drop"
    ],

    "dark": [
        "dark",
        "sinister",
        "mysterious",
        "heavy",
        "aggressive",
        "evil",
        "gritty",
        "industrial",
        "underground",
        "brutal",
        "chaotic"
    ],

    "energetic": [
        "energetic",
        "energy",
        "high energy",
        "fast",
        "driving",
        "intense",
        "euphoric",
        "excited",
        "uplifting",
        "powerful"
    ],

    "night": [
        "night",
        "night drive",
        "late night",
        "neon",
        "midnight",
        "city",
        "after dark",
        "futuristic",
        "atmospheric",
        "deep"
    ],

    "melodic": [
        "melodic",
        "melody",
        "emotional",
        "euphoric",
        "atmospheric",
        "cinematic",
        "dreamy",
        "beautiful",
        "harmonic",
        "nostalgic"
    ]
}


# =========================================================
# GENRE KEYWORDS
# =========================================================

GENRE_KEYWORDS = {

    "melodic dubstep": [
        "emotional",
        "sad",
        "melancholic",
        "heartbreak",
        "lonely",
        "nostalgic",
        "cinematic"
    ],

    "melodic future bass": [
        "emotional",
        "uplifting",
        "dreamy",
        "romantic",
        "nostalgic",
        "euphoric"
    ],

    "future bass": [
        "happy",
        "emotional",
        "energetic",
        "uplifting",
        "euphoric",
        "festival"
    ],

    "future riddim": [
        "dark",
        "energetic",
        "aggressive",
        "heavy",
        "futuristic"
    ],

    "trap": [
        "dark",
        "hype",
        "energetic",
        "aggressive",
        "urban"
    ],

    "hard trap": [
        "dark",
        "aggressive",
        "hype",
        "heavy",
        "intense"
    ],

    "festival trap": [
        "hype",
        "festival",
        "party",
        "massive",
        "powerful"
    ],

    "hybrid trap": [
        "dark",
        "hype",
        "energetic",
        "heavy",
        "cinematic"
    ],

    "stutter house": [
        "happy",
        "energetic",
        "romantic",
        "groovy",
        "danceable"
    ],

    "future garage": [
        "sad",
        "melancholic",
        "chill",
        "lonely",
        "atmospheric",
        "deep"
    ],

    "house": [
        "happy",
        "energetic",
        "chill",
        "groovy",
        "danceable",
        "club"
    ],

    "melodic house": [
        "emotional",
        "romantic",
        "uplifting",
        "euphoric",
        "atmospheric",
        "dreamy"
    ],

    "deep house": [
        "chill",
        "romantic",
        "melancholic",
        "smooth",
        "deep",
        "atmospheric"
    ],

    "bass house": [
        "hype",
        "dark",
        "energetic",
        "heavy",
        "groovy",
        "club"
    ],

    "progressive house": [
        "emotional",
        "uplifting",
        "euphoric",
        "atmospheric",
        "festival"
    ],

    "electro house": [
        "hype",
        "energetic",
        "festival",
        "powerful",
        "party"
    ],

    "future house": [
        "happy",
        "energetic",
        "hype",
        "groovy",
        "uplifting"
    ],

    "dark bass": [
        "dark",
        "aggressive",
        "mysterious",
        "heavy",
        "underground"
    ],

    "chill electronic": [
        "chill",
        "relaxed",
        "melancholic",
        "peaceful",
        "dreamy"
    ],

    "night drive": [
        "night",
        "dark",
        "chill",
        "atmospheric",
        "deep",
        "futuristic"
    ],

    "psytrance": [
        "energetic",
        "euphoric",
        "hypnotic",
        "psychedelic",
        "trippy",
        "cosmic"
    ],

    "hardstyle": [
        "energetic",
        "euphoric",
        "aggressive",
        "powerful",
        "festival",
        "intense"
    ],

    "hard techno": [
        "dark",
        "aggressive",
        "energetic",
        "industrial",
        "raw",
        "underground",
        "intense"
    ],

    "drum and bass": [
        "energetic",
        "dark",
        "fast",
        "atmospheric",
        "powerful"
    ],

    "liquid drum and bass": [
        "emotional",
        "melancholic",
        "uplifting",
        "chill",
        "soulful",
        "atmospheric",
        "dreamy",
        "nostalgic"
    ],

    "riddim": [
        "dark",
        "aggressive",
        "hype",
        "heavy",
        "gritty",
        "underground"
    ],

    "jersey club": [
        "energetic",
        "hype",
        "playful",
        "bouncy",
        "groovy",
        "danceable",
        "party"
    ],

    "jengel terror": [
        "dark",
        "aggressive",
        "chaotic",
        "extreme",
        "heavy",
        "sinister",
        "underground"
    ],

    "melodic techno": [
        "emotional",
        "melancholic",
        "dark",
        "euphoric",
        "atmospheric",
        "deep",
        "hypnotic",
        "cinematic"
    ],

    "hardcore": [
        "aggressive",
        "dark",
        "energetic",
        "intense",
        "extreme",
        "heavy",
        "raw",
        "underground"
    ],

    "tech house": [
        "energetic",
        "hype",
        "groovy",
        "confident",
        "club",
        "dancefloor",
        "bouncy"
    ],

    "breakbeats": [
        "energetic",
        "hype",
        "groovy",
        "atmospheric",
        "rhythmic",
        "underground",
        "danceable"
    ],

    "raw trap": [
        "dark",
        "aggressive",
        "hype",
        "intense",
        "gritty",
        "heavy",
        "raw",
        "underground"
    ],

    "sub bass": [
        "dark",
        "deep",
        "mysterious",
        "chill",
        "atmospheric",
        "underground",
        "low end"
    ],

    "tearout": [
        "dark",
        "aggressive",
        "chaotic",
        "intense",
        "heavy",
        "brutal",
        "apocalyptic"
    ],

    "dubstep": [
        "dark",
        "energetic",
        "aggressive",
        "hypnotic",
        "heavy",
        "bass driven",
        "powerful"
    ],

    "future rave": [
        "euphoric",
        "energetic",
        "emotional",
        "powerful",
        "festival",
        "anthemic",
        "uplifting"
    ],

    "afro house": [
        "warm",
        "uplifting",
        "chill",
        "energetic",
        "organic",
        "tribal",
        "groovy",
        "sunset"
    ],

    "midtempo": [
        "dark",
        "mysterious",
        "emotional",
        "hypnotic",
        "atmospheric",
        "cinematic",
        "futuristic",
        "heavy"
    ]
}


# =========================================================
# SONG DATABASE
# =========================================================
#
# Each song:
#
# {
#   "message_id": 123,
#   "caption": "...",
#   "genre": "...",
#   "moods": [...]
# }
#
# =========================================================

SONGS = {}


# =========================================================
# YOUR OLD SONG IDS
# =========================================================

OLD_IDS = [
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


# =========================================================
# INITIALIZE OLD SONGS
# =========================================================

for song_id in OLD_IDS:

    SONGS[str(song_id)] = {

        "message_id": song_id,

        "caption": "",

        "genre": "",

        "moods": []
    }


# =========================================================
# TELEGRAM
# =========================================================

def telegram(method, data):

    try:

        r = requests.post(

            f"{TELEGRAM_API}/{method}",

            json=data,

            timeout=30
        )

        result = r.json()

        if not result.get("ok"):

            print(
                "Telegram API error:",
                result
            )

        return result

    except Exception as e:

        print(
            "Telegram request error:",
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
# COPY MUSIC
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

            "text": text
        }
    )


# =========================================================
# MENU
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
                    "text": "🤖 Ask AI",
                    "callback_data": "ai_help"
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
                    "text": "🤖 Ask AI",
                    "callback_data": "ai_help"
                }
            ]
        ]
    }


# =========================================================
# NORMALIZE TEXT
# =========================================================

def normalize(text):

    if not text:

        return ""

    text = text.lower()

    text = text.replace(
        "&",
        "and"
    )

    text = re.sub(
        r"[^a-z0-9\s/+-]",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# =========================================================
# EXTRACT CAPTION DATA
# =========================================================

def extract_caption_data(caption):

    text = normalize(caption)

    genre = ""

    genre_match = re.search(
        r"genre\s*:\s*(.+?)(?:mood\s*:|vibe\s*:|energy\s*:|$)",
        text,
        re.I
    )

    if genre_match:

        genre = genre_match.group(1).strip()

    mood_text = ""

    mood_match = re.search(
        r"mood\s*:\s*(.+?)(?:vibe\s*:|energy\s*:|$)",
        text,
        re.I
    )

    if mood_match:

        mood_text = mood_match.group(1).strip()

    vibe_text = ""

    vibe_match = re.search(
        r"vibe\s*:\s*(.+?)(?:energy\s*:|$)",
        text,
        re.I
    )

    if vibe_match:

        vibe_text = vibe_match.group(1).strip()

    energy_text = ""

    energy_match = re.search(
        r"energy\s*:\s*(.+?)$",
        text,
        re.I
    )

    if energy_match:

        energy_text = energy_match.group(1).strip()

    combined = " ".join(
        [
            genre,
            mood_text,
            vibe_text,
            energy_text
        ]
    )

    moods = []

    for mood in MOODS:

        keywords = MOOD_KEYWORDS.get(
            mood,
            []
        )

        for keyword in keywords:

            if keyword in combined:

                if mood not in moods:

                    moods.append(
                        mood
                    )

                break

    return {

        "genre": genre,

        "moods": moods,

        "mood_text": mood_text,

        "vibe_text": vibe_text,

        "energy_text": energy_text
    }


# =========================================================
# AI ANALYZE CAPTION
# =========================================================

def ai_analyze_song(caption):

    if not ai_client:

        return None

    try:

        prompt = f"""
Analyze this EDM track caption.

Return ONLY valid JSON.

Allowed moods:
sad
love
chill
hype
dark
energetic
night
melodic

The genre is not the mood.

For example:
Hardstyle does NOT automatically mean Hype.
Melodic Dubstep does NOT automatically mean Sad.
Liquid Drum & Bass CAN be Sad if the caption says emotional/melancholic.
Melodic Techno CAN be Sad or Night Drive if appropriate.

Use the actual Mood, Vibe and Energy.

Caption:

{caption}

Return:

{{
  "genre": "string",
  "moods": ["sad"],
  "energy": "Low / Medium / High / Very High",
  "confidence": 0-100
}}
"""

        response = ai_client.responses.create(

            model="gpt-5.6",

            input=prompt
        )

        raw = response.output_text.strip()

        result = json.loads(raw)

        valid_moods = []

        for mood in result.get(
            "moods",
            []
        ):

            if mood in MOODS:

                if mood not in valid_moods:

                    valid_moods.append(
                        mood
                    )

        result["moods"] = valid_moods

        return result

    except Exception as e:

        print(
            "AI analyze error:",
            e
        )

        return None


# =========================================================
# ANALYZE SONG
# =========================================================

def analyze_song(
    message_id,
    caption
):

    basic = extract_caption_data(
        caption
    )

    genre = basic["genre"]

    moods = list(
        basic["moods"]
    )

    # Genre-specific intelligence

    genre_lower = normalize(
        genre
    )

    genre_keywords = GENRE_KEYWORDS.get(
        genre_lower,
        []
    )

    caption_normalized = normalize(
        caption
    )

    # Add genre-related mood hints
    for keyword in genre_keywords:

        if keyword in caption_normalized:

            for mood in MOODS:

                if keyword in MOOD_KEYWORDS.get(
                    mood,
                    []
                ):

                    if mood not in moods:

                        moods.append(
                            mood
                        )

    # AI analysis
    ai_result = ai_analyze_song(
        caption
    )

    if ai_result:

        if ai_result.get("genre"):

            genre = ai_result.get(
                "genre"
            )

        for mood in ai_result.get(
            "moods",
            []
        ):

            if mood not in moods:

                moods.append(
                    mood
                )

    SONGS[str(message_id)] = {

        "message_id":
            message_id,

        "caption":
            caption,

        "genre":
            genre,

        "moods":
            moods,

        "mood_text":
            basic["mood_text"],

        "vibe_text":
            basic["vibe_text"],

        "energy_text":
            basic["energy_text"]
    }

    print(
        "ANALYZED:",
        message_id,
        genre,
        moods
    )


# =========================================================
# SCORE SONG
# =========================================================

def score_song(
    song,
    wanted_mood
):

    score = 0

    caption = normalize(
        song.get(
            "caption",
            ""
        )
    )

    genre = normalize(
        song.get(
            "genre",
            ""
        )
    )

    moods = song.get(
        "moods",
        []
    )

    mood_text = normalize(
        song.get(
            "mood_text",
            ""
        )
    )

    vibe_text = normalize(
        song.get(
            "vibe_text",
            ""
        )
    )

    energy_text = normalize(
        song.get(
            "energy_text",
            ""
        )
    )

    # =====================================================
    # STRONG MOOD MATCH
    # =====================================================

    if wanted_mood in moods:

        score += 55

    # =====================================================
    # EXPLICIT MOOD FIELD
    # =====================================================

    wanted_words = MOOD_KEYWORDS.get(
        wanted_mood,
        []
    )

    for word in wanted_words:

        if word in mood_text:

            score += 15

        if word in vibe_text:

            score += 10

        if word in caption:

            score += 5

    # =====================================================
    # GENRE COMPATIBILITY
    # =====================================================

    genre_words = GENRE_KEYWORDS.get(
        genre,
        []
    )

    for word in genre_words:

        if word in wanted_words:

            score += 4

    # =====================================================
    # ENERGY MATCH
    # =====================================================

    if wanted_mood == "sad":

        if (
            "low" in energy_text
            or "medium" in energy_text
        ):

            score += 10

        if (
            "very high" in energy_text
        ):

            score -= 8

    elif wanted_mood == "chill":

        if "low" in energy_text:

            score += 10

        elif "medium" in energy_text:

            score += 5

        elif "very high" in energy_text:

            score -= 10

    elif wanted_mood in [
        "hype",
        "energetic"
    ]:

        if "high" in energy_text:

            score += 10

        if "very high" in energy_text:

            score += 12

    elif wanted_mood == "night":

        if (
            "low" in energy_text
            or "medium" in energy_text
        ):

            score += 5

    # =====================================================
    # SPECIAL RULES
    # =====================================================

    # Sad should strongly favor emotional language

    if wanted_mood == "sad":

        emotional_words = [
            "emotional",
            "melancholic",
            "heartbreak",
            "lonely",
            "nostalgic",
            "sad",
            "sorrow",
            "pain"
        ]

        emotional_hits = 0

        for word in emotional_words:

            if word in caption:

                emotional_hits += 1

        score += (
            emotional_hits * 8
        )

    # Love

    if wanted_mood == "love":

        love_words = [
            "love",
            "romantic",
            "romance",
            "warm",
            "passion",
            "dreamy"
        ]

        for word in love_words:

            if word in caption:

                score += 7

    # Dark

    if wanted_mood == "dark":

        dark_words = [
            "dark",
            "sinister",
            "aggressive",
            "heavy",
            "gritty",
            "industrial",
            "underground"
        ]

        for word in dark_words:

            if word in caption:

                score += 7

    # Night

    if wanted_mood == "night":

        night_words = [
            "night",
            "late night",
            "neon",
            "midnight",
            "atmospheric",
            "futuristic",
            "deep"
        ]

        for word in night_words:

            if word in caption:

                score += 7

    return max(
        score,
        0
    )


# =========================================================
# GET BEST SONG
# =========================================================

def get_best_song(
    mood,
    exclude_ids=None
):

    exclude_ids = exclude_ids or []

    candidates = []

    for song in SONGS.values():

        message_id = song.get(
            "message_id"
        )

        if message_id in exclude_ids:

            continue

        if not message_id:

            continue

        score = score_song(
            song,
            mood
        )

        candidates.append(
            (
                score,
                message_id
            )
        )

    if not candidates:

        return None

    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    # =====================================================
    # RANDOMIZE AMONG VERY GOOD MATCHES
    # =====================================================

    top_score = candidates[0][0]

    good = [
        item
        for item in candidates
        if item[0] >= max(
            top_score - 12,
            1
        )
    ]

    selected = random.choice(
        good[:10]
    )

    return selected


# =========================================================
# SEND MOOD MUSIC
# =========================================================

def send_mood_music(
    chat_id,
    mood,
    exclude_ids=None
):

    result = get_best_song(
        mood,
        exclude_ids
    )

    if not result:

        # Absolute fallback
        # Never say "AI analyzed not enough"

        all_ids = [
            song["message_id"]
            for song in SONGS.values()
            if song.get("message_id")
            and song["message_id"]
            not in (exclude_ids or [])
        ]

        if not all_ids:

            send_message(
                chat_id,
                "❌ No music is available right now."
            )

            return None

        message_id = random.choice(
            all_ids
        )

        score = 0

    else:

        score, message_id = result

    copied = copy_music(
        chat_id,
        message_id
    )

    print(
        "SEND:",
        mood,
        message_id,
        "score:",
        score,
        "result:",
        copied
    )

    if copied.get("ok"):

        send_message(

            chat_id,

            f"{MOOD_NAMES.get(mood)}\n\n"
            "🎧 Best match from NOT YOUR VIBE Collection\n"
            "🔥 Enjoy the track!",

            music_buttons()
        )

        return message_id

    send_message(
        chat_id,
        "❌ Couldn't send this track."
    )

    return None


# =========================================================
# CHANNEL POST
# =========================================================

def process_channel_post(
    channel_post
):

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
    ).lower()

    target = CHANNEL_USERNAME.replace(
        "@",
        ""
    ).lower()

    if username != target:

        return

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

    if not message_id:

        return

    analyze_song(
        message_id,
        caption
    )

    print(
        "CHANNEL SONG ADDED:",
        message_id
    )


# =========================================================
# START
# =========================================================

def start_bot(
    chat_id
):

    send_message(

        chat_id,

        "🎧 NOT YOUR VIBE MUSIC\n\n"
        "Welcome! 🔥\n\n"
        "Choose your mood and I'll find "
        "the closest track from our MP3 Collection.\n\n"
        "🤖 AI understands your mood.\n"
        "🎵 Music comes only from our channel.",

        mood_menu()
    )


# =========================================================
# AI CHAT
# =========================================================

def ask_ai(
    text
):

    if not ai_client:

        return (
            "🤖 AI is currently unavailable.\n\n"
            "You can still use the mood buttons "
            "to get music."
        )

    try:

        response = ai_client.responses.create(

            model="gpt-5.6",

            input=f"""
You are NOT YOUR VIBE Music Assistant.

User:
{text}

Give a short helpful response about EDM/electronic music.

Available moods:
Sad
Love
Chill
Hype
Dark
Energetic
Night Drive
Melodic

Do not invent channel songs.
If the user wants a song, tell them to choose
one of the mood buttons.
"""
        )

        return response.output_text

    except Exception as e:

        print(
            "AI chat error:",
            e
        )

        return (
            "🤖 AI is temporarily unavailable.\n\n"
            "Please use the mood buttons below."
        )


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

        process_channel_post(
            channel_post
        )

        return "OK"


    # =====================================================
    # EDITED CHANNEL POST
    # =====================================================

    edited_channel_post = update.get(
        "edited_channel_post"
    )

    if edited_channel_post:

        process_channel_post(
            edited_channel_post
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

        text = (
            message.get(
                "text",
                ""
            )
            or ""
        ).strip()


        if text == "/start":

            start_bot(
                chat_id
            )

            return "OK"


        if text == "/mood":

            send_message(

                chat_id,

                "🎧 Choose your mood 👇",

                mood_menu()
            )

            return "OK"


        if text == "/ai":

            send_message(

                chat_id,

                "🤖 AI MUSIC ASSISTANT\n\n"
                "Tell me what you're feeling.\n\n"
                "Examples:\n\n"
                "• I'm sad tonight\n"
                "• I want emotional music\n"
                "• Give me dark bass\n"
                "• Music for night driving\n"
                "• I want something euphoric"
            )

            return "OK"


        if text == "/help":

            send_message(

                chat_id,

                "🎧 NOT YOUR VIBE MUSIC BOT\n\n"
                "/start - Start\n"
                "/mood - Choose mood\n"
                "/ai - AI Assistant\n"
                "/help - Help"
            )

            return "OK"


        if text:

            response = ask_ai(
                text
            )

            send_message(

                chat_id,

                "🤖 AI Music Assistant\n\n"
                + response,

                mood_menu()
            )

            return "OK"


    # =====================================================
    # CALLBACK
    # =====================================================

    callback = update.get(
        "callback_query"
    )

    if callback:

        callback_id = callback["id"]

        data = (
            callback.get(
                "data",
                ""
            )
            or ""
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

            return "OK"


        # =================================================
        # CHANGE MOOD
        # =================================================

        if data == "change_mood":

            answer_callback(
                callback_id,
                "Choose another mood"
            )

            send_message(

                chat_id,

                "🎧 Choose your mood 👇",

                mood_menu()
            )

            return "OK"


        # =================================================
        # NEXT MUSIC
        # =================================================

        if data == "next_music":

            answer_callback(
                callback_id,
                "🔀 Finding another track..."
            )

            # Random fallback from whole collection

            all_ids = [
                song["message_id"]
                for song in SONGS.values()
                if song.get("message_id")
            ]

            if not all_ids:

                send_message(
                    chat_id,
                    "❌ No music available."
                )

                return "OK"

            message_id = random.choice(
                all_ids
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

            return "OK"


        # =================================================
        # MOOD
        # =================================================

        if data.startswith(
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

            send_mood_music(

                chat_id,

                mood
            )

            return "OK"


    return "OK"


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
# WEBHOOK SETUP
# =========================================================

if BOT_TOKEN and RENDER_URL:

    webhook_url = (
        RENDER_URL.rstrip("/")
        + "/webhook"
    )

    try:

        response = requests.post(

            f"{TELEGRAM_API}/setWebhook",

            json={

                "url": webhook_url,

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
            "Webhook:",
            response.text
        )

    except Exception as e:

        print(
            "Webhook setup error:",
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
