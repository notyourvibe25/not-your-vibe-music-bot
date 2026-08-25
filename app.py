import os
import random
import threading
import time
import requests

from flask import Flask, request


# =========================================================
# APP
# =========================================================

app = Flask(__name__)


# =========================================================
# ENVIRONMENT
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

CHANNEL_USERNAME = "@notyourvibemp3collection"

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# =========================================================
# HTTP SESSION
# Reuse one connection -> faster requests
# =========================================================

http = requests.Session()

http.headers.update({
    "User-Agent": "NOT-YOUR-VIBE-MUSIC-BOT"
})


# =========================================================
# LOCK
# =========================================================

send_lock = threading.Lock()


# =========================================================
# MOODS
# =========================================================

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


# =========================================================
# =========================================================
# MUSIC DATABASE
# =========================================================
#
# IMPORTANT:
#
# ဒီ database က AI မဟုတ်ပါ။
#
# မင်းပေးထားတဲ့ list အတိုင်း manual mood database ဖြစ်ပါတယ်။
#
# ဒါကြောင့်
#
# "Sad collection is still being analyzed"
#
# ဆိုတာ မရှိတော့ပါ။
# =========================================================


MOOD_MUSIC = {

    # =====================================================
    # 😢 SAD
    # =====================================================
    #
    # Sad mood
    # Chill mood
    # Melodic
    #
    # User supplied:
    #
    # 543
    # 1594
    # 1844
    # 2105
    # 2621
    # 2406
    # 2316
    # 2286
    # 2713
    # 2553
    # 553
    # 557
    #
    # =====================================================

    "sad": [

        543,
        1594,
        1844,
        2105,
        2621,
        2406,
        2316,
        2286,
        2713,
        2553,
        553,
        557

    ],


    # =====================================================
    # ❤️ LOVE
    # =====================================================

    "love": [

        2366,
        2839,
        2825,
        2236,
        2246,
        2226,
        2216

    ],


    # =====================================================
    # 🌙 CHILL
    # =====================================================

    "chill": [

        2849,
        2859,
        2256,
        2446,
        2296,
        2196

    ],


    # =====================================================
    # 🔥 HYPE
    # =====================================================

    "hype": [

        2800,
        2703,
        2649,
        2639,
        2630,
        2572,
        2346,
        2276,
        2266,
        2216,
        2206,
        1009,
        538,

        2326,
        2356,
        2386,
        2582,
        2563,
        2536,
        2526,
        2466,
        2456,
        2426,
        2416,
        2296

    ],


    # =====================================================
    # 🖤 DARK
    # =====================================================

    "dark": [

        1603,
        2752,
        2742,
        2732,
        2722,
        2693,
        2675,
        2602,
        2592,
        2516,
        2486,
        2476,
        2396,
        2376,
        2336,
        2186,
        2176,
        2156

    ],


    # =====================================================
    # ⚡ ENERGETIC
    # =====================================================

    "energetic": [

        2800,
        2703,
        2649,
        2639,
        2630,
        2572,
        2346,
        2276,
        2266,
        2216,
        2206,
        1009,
        538,

        1603,
        2752,
        2742,
        2732,
        2722,
        2693,
        2675,
        2602,
        2592,
        2516,
        2486,
        2476,
        2396,
        2376,
        2336,
        2186,
        2176,
        2156,

        2326,
        2356,
        2386,
        2582,
        2563,
        2536,
        2526,
        2466,
        2456,
        2426,
        2416,
        2296

    ],


    # =====================================================
    # 🚗 NIGHT DRIVE
    # =====================================================

    "night": [

        2146,
        2166,
        2306,
        2506,
        2436

    ],


    # =====================================================
    # 🌌 MELODIC
    # =====================================================

    "melodic": [

        543,
        1594,
        1844,
        2105,
        2621,
        2406,
        2316,
        2286,
        2713,
        2553,
        553,
        557,

        2146,
        2166,
        2306,
        2506,
        2436

    ]

}


# =========================================================
# REMOVE DUPLICATES
# =========================================================

for mood in MOODS:

    MOOD_MUSIC[mood] = list(
        dict.fromkeys(
            MOOD_MUSIC[mood]
        )
    )


# =========================================================
# ALL MUSIC
# =========================================================

ALL_MUSIC = []

for mood in MOODS:

    ALL_MUSIC.extend(
        MOOD_MUSIC[mood]
    )


ALL_MUSIC = list(
    dict.fromkeys(
        ALL_MUSIC
    )
)


# =========================================================
# USER STATE
#
# User တစ်ယောက်ချင်းစီ ဘယ် mood ရွေးထားလဲ မှတ်ထားမယ်။
#
# Next နှိပ်ရင် အဲဒီ mood ထဲကပဲ ဆက်ရွေးမယ်။
# =========================================================

USER_STATE = {}


# =========================================================
# TELEGRAM API
# =========================================================

def telegram(
    method,
    data,
    timeout=10
):

    if not BOT_TOKEN:

        print(
            "ERROR: BOT_TOKEN is missing"
        )

        return {
            "ok": False,
            "description": "BOT_TOKEN missing"
        }


    try:

        response = http.post(

            f"{TELEGRAM_API}/{method}",

            json=data,

            timeout=timeout

        )


        result = response.json()


        if not result.get("ok"):

            print(
                "Telegram API error:",
                method,
                result
            )


        return result


    except Exception as e:

        print(
            "Telegram request error:",
            method,
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
    keyboard=None
):

    data = {

        "chat_id":
            chat_id,

        "text":
            text

    }


    if keyboard is not None:

        data["reply_markup"] = keyboard


    return telegram(

        "sendMessage",

        data,

        timeout=10

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

        },

        timeout=5

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

        },

        timeout=15

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
                        "🏠 Mood Menu",

                    "callback_data":
                        "change_mood"
                }

            ]

        ]
    }


# =========================================================
# GET SONGS FOR MOOD
# =========================================================

def get_mood_songs(
    mood
):

    return list(
        MOOD_MUSIC.get(
            mood,
            []
        )
    )


# =========================================================
# PICK SONG
#
# same song immediately repeat မဖြစ်အောင်
# last song ကို exclude လုပ်ထားမယ်။
# =========================================================

def pick_song(
    chat_id,
    mood
):

    songs = get_mood_songs(
        mood
    )


    if not songs:

        return None


    previous = (
        USER_STATE
        .get(
            chat_id,
            {}
        )
        .get(
            "last_song"
        )
    )


    if len(songs) > 1:

        candidates = [

            song

            for song in songs

            if song != previous

        ]

    else:

        candidates = songs


    if not candidates:

        candidates = songs


    return random.choice(
        candidates
    )


# =========================================================
# SEND MOOD TRACK
# =========================================================

def send_mood_track(
    chat_id,
    mood
):

    songs = get_mood_songs(
        mood
    )


    # =====================================================
    # NO SONG
    # =====================================================

    if not songs:

        send_message(

            chat_id,

            f"{MOOD_NAMES[mood]}\n\n"
            "⚠️ ဒီ mood အတွက် track မရှိသေးပါ။",

            mood_menu()

        )

        return


    # =====================================================
    # PICK
    # =====================================================

    message_id = pick_song(

        chat_id,

        mood

    )


    if message_id is None:

        send_message(

            chat_id,

            "❌ Track ရွေးလို့မရပါ။",

            mood_menu()

        )

        return


    # =====================================================
    # COPY
    # =====================================================

    result = copy_music(

        chat_id,

        message_id

    )


    # =====================================================
    # SUCCESS
    # =====================================================

    if result.get("ok"):

        USER_STATE[chat_id] = {

            "mood":
                mood,

            "last_song":
                message_id

        }


        print(

            "MUSIC SENT",

            "| chat:",
            chat_id,

            "| mood:",
            mood,

            "| message:",
            message_id

        )


        send_message(

            chat_id,

            f"{MOOD_NAMES[mood]}\n\n"
            "🎧 Enjoy your music! 🔥",

            music_buttons()

        )


        return


    # =====================================================
    # FAILURE
    # =====================================================

    print(

        "COPY FAILED",

        "| mood:",
        mood,

        "| message:",
        message_id,

        "| result:",
        result

    )


    send_message(

        chat_id,

        f"{MOOD_NAMES[mood]}\n\n"
        "❌ ဒီ track ကို channel ကနေ "
        "copy လုပ်လို့မရပါ။\n\n"
        "Bot ရဲ့ channel permission / "
        "Message ID ကိုစစ်ပါ။",

        mood_menu()

    )


# =========================================================
# NEXT TRACK
#
# IMPORTANT:
#
# Next = လက်ရှိရွေးထားတဲ့ mood ထဲကပဲ
# =========================================================

def send_next_track(
    chat_id
):

    state = USER_STATE.get(
        chat_id
    )


    # =====================================================
    # User hasn't selected mood
    # =====================================================

    if not state:

        send_message(

            chat_id,

            "🎧 အရင်ဆုံး Mood တစ်ခုရွေးပါ 👇",

            mood_menu()

        )

        return


    mood = state.get(
        "mood"
    )


    if mood not in MOODS:

        send_message(

            chat_id,

            "🎧 Mood ရွေးပါ 👇",

            mood_menu()

        )

        return


    songs = get_mood_songs(
        mood
    )


    if not songs:

        send_message(

            chat_id,

            f"{MOOD_NAMES[mood]}\n\n"
            "⚠️ ဒီ mood မှာ track မရှိသေးပါ။",

            mood_menu()

        )

        return


    previous = state.get(
        "last_song"
    )


    # =====================================================
    # Don't immediately repeat
    # =====================================================

    if len(songs) > 1:

        candidates = [

            song

            for song in songs

            if song != previous

        ]

    else:

        candidates = songs


    # =====================================================
    # Randomize
    # =====================================================

    random.shuffle(
        candidates
    )


    # =====================================================
    # Try songs until successful
    #
    # Channel မှာ deleted / inaccessible ID ရှိနေရင်
    # နောက် ID ကိုဆက်စမ်းမယ်။
    # =====================================================

    for message_id in candidates:

        result = copy_music(

            chat_id,

            message_id

        )


        if result.get("ok"):

            USER_STATE[chat_id] = {

                "mood":
                    mood,

                "last_song":
                    message_id

            }


            print(

                "NEXT SENT",

                "| chat:",
                chat_id,

                "| mood:",
                mood,

                "| message:",
                message_id

            )


            send_message(

                chat_id,

                f"{MOOD_NAMES[mood]}\n\n"
                "🔀 Next track 👇",

                music_buttons()

            )


            return


        print(

            "NEXT FAILED",

            "| mood:",
            mood,

            "| message:",
            message_id

        )


    # =====================================================
    # NOTHING WORKED
    # =====================================================

    send_message(

        chat_id,

        f"{MOOD_NAMES[mood]}\n\n"
        "❌ ဒီ mood ထဲက track တွေကို "
        "channel ကနေ copy မလုပ်နိုင်ပါ။",

        mood_menu()

    )


# =========================================================
# BACKGROUND MOOD
# =========================================================

def background_mood(
    chat_id,
    mood
):

    try:

        send_mood_track(

            chat_id,

            mood

        )

    except Exception as e:

        print(
            "BACKGROUND MOOD ERROR:",
            e
        )


# =========================================================
# BACKGROUND NEXT
# =========================================================

def background_next(
    chat_id
):

    try:

        send_next_track(

            chat_id

        )

    except Exception as e:

        print(
            "BACKGROUND NEXT ERROR:",
            e
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
        "NOT YOUR VIBE MUSIC BOT ONLINE"
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
    # CALLBACK QUERY
    # =====================================================

    callback = update.get(
        "callback_query"
    )


    if callback:

        callback_id = callback.get(
            "id"
        )


        data = callback.get(
            "data",
            ""
        )


        callback_message = (
            callback.get(
                "message"
            )
            or {}
        )


        callback_chat = (
            callback_message
            .get(
                "chat"
            )
            or {}
        )


        chat_id = callback_chat.get(
            "id"
        )


        if not chat_id:

            answer_callback(

                callback_id,

                "Chat not found"

            )

            return "OK"


        # =================================================
        # MOOD
        # =================================================

        if data.startswith(
            "mood_"
        ):

            mood = data[
                len("mood_"):
            ]


            if mood not in MOODS:

                answer_callback(

                    callback_id,

                    "Invalid mood"

                )

                return "OK"


            # =================================================
            # SAVE MOOD IMMEDIATELY
            # =================================================

            USER_STATE.setdefault(

                chat_id,

                {}

            )


            USER_STATE[chat_id][
                "mood"
            ] = mood


            # =================================================
            # FAST CALLBACK
            # =================================================

            answer_callback(

                callback_id,

                f"{MOOD_NAMES[mood]} ✓"

            )


            # =================================================
            # MUSIC IN BACKGROUND
            # =================================================

            threading.Thread(

                target=background_mood,

                args=(

                    chat_id,

                    mood

                ),

                daemon=True

            ).start()


            return "OK"


        # =================================================
        # NEXT
        # =================================================

        if data == "next_music":

            answer_callback(

                callback_id,

                "🔀 Finding next..."

            )


            threading.Thread(

                target=background_next,

                args=(

                    chat_id,

                ),

                daemon=True

            ).start()


            return "OK"


        # =================================================
        # CHANGE MOOD
        # =================================================

        if data == "change_mood":

            answer_callback(

                callback_id,

                "🎧 Choose mood"

            )


            send_message(

                chat_id,

                "🎧 Choose your mood 👇",

                mood_menu()

            )


            return "OK"


        return "OK"


    # =====================================================
    # NORMAL MESSAGE
    # =====================================================

    message = update.get(
        "message"
    )


    if message:

        chat = message.get(
            "chat",
            {}
        )


        chat_id = chat.get(
            "id"
        )


        text = (

            message
            .get(
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

            return "OK"


        # =================================================
        # MOOD
        # =================================================

        if text == "/mood":

            send_message(

                chat_id,

                "🎧 Choose your mood 👇",

                mood_menu()

            )

            return "OK"


        # =================================================
        # HELP
        # =================================================

        if text == "/help":

            send_message(

                chat_id,

                "🎧 NOT YOUR VIBE MUSIC BOT\n\n"

                "/start - Start bot\n"
                "/mood - Mood menu\n"
                "/help - Help\n\n"

                "🎵 Select a mood to receive music."

            )

            return "OK"


        # =================================================
        # NEXT COMMAND
        # =================================================

        if text == "/next":

            threading.Thread(

                target=background_next,

                args=(

                    chat_id,

                ),

                daemon=True

            ).start()


            return "OK"


    return "OK"


# =========================================================
# SET WEBHOOK
# =========================================================

def setup_webhook():

    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN is missing"
        )

        return


    if not RENDER_URL:

        print(
            "❌ RENDER_EXTERNAL_URL is missing"
        )

        return


    webhook_url = (
        f"{RENDER_URL}/webhook"
    )


    result = telegram(

        "setWebhook",

        {

            "url":
                webhook_url,

            "allowed_updates": [

                "message",

                "callback_query"

            ],

            "max_connections":
                40,

            "drop_pending_updates":
                True

        },

        timeout=15

    )


    print(
        "WEBHOOK:",
        result
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    print(
        "=========================================="
    )

    print(
        "🎧 NOT YOUR VIBE MUSIC BOT"
    )

    print(
        "=========================================="
    )


    print(
        "Channel:",
        CHANNEL_USERNAME
    )


    print(
        "Total unique tracks:",
        len(ALL_MUSIC)
    )


    for mood in MOODS:

        print(

            MOOD_NAMES[mood],
            "=",
            len(
                MOOD_MUSIC[mood]
            ),
            "tracks"

        )


    print(
        "=========================================="
    )


    # =====================================================
    # WEBHOOK
    # =====================================================

    setup_webhook()


    # =====================================================
    # PORT
    # =====================================================

    port = int(

        os.getenv(
            "PORT",
            "10000"
        )

    )


    # =====================================================
    # RUN
    # =====================================================

    app.run(

        host="0.0.0.0",

        port=port,

        threaded=True

)
