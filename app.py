import os
import random
import threading
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

TELEGRAM_API = (
    f"https://api.telegram.org/bot{BOT_TOKEN}"
)


# =========================================================
# HTTP SESSION
# =========================================================

http = requests.Session()

http.headers.update({
    "User-Agent": "NOT-YOUR-VIBE-MUSIC-BOT/1.0"
})


# =========================================================
# STATE LOCK
#
# လူအများသုံးတဲ့အချိန် user state ကို
# တစ်ချိန်တည်း update လုပ်ပြီး conflict မဖြစ်အောင်
# =========================================================

STATE_LOCK = threading.RLock()


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
# =========================================================
# MANUAL MOOD DATABASE
# =========================================================
#
# ဒီနေရာမှာ AI မပါပါ။
#
# မင်းပေးထားတဲ့ Message ID တွေကို
# Mood အလိုက် တိတိကျကျ သတ်မှတ်ထားပါတယ်။
#
# =========================================================


MOOD_MUSIC = {

    # =====================================================
    # 😢 SAD
    # Sad + Chill + Melodic collection
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
    # Love + Chill
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
    #
    # Chill list ထဲမှာ
    # Sad collection က songs မထည့်ထားပါ။
    # မင်းပေးထားတဲ့ Chill list ကိုပဲ သုံးမယ်။
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

ALL_MUSIC = sorted(
    set(
        song
        for mood in MOODS
        for song in MOOD_MUSIC[mood]
    )
)


# =========================================================
# USER STATE
#
# {
#     chat_id: {
#
#         "mood": "sad",
#
#         "history": [
#             543,
#             1594,
#             ...
#         ]
#
#     }
# }
#
# User တစ်ယောက်ချင်းစီ သီးသန့်။
# =========================================================

USER_STATE = {}


# =========================================================
# HISTORY LIMIT
#
# Memory မကြီးလာအောင် user တစ်ယောက်ရဲ့
# နောက်ဆုံး 100 songs ပဲ သိမ်းထားမယ်။
# =========================================================

MAX_HISTORY = 100


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
            "❌ BOT_TOKEN is missing"
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
                "Telegram API ERROR:",
                method,
                result
            )


        return result


    except Exception as e:

        print(
            "Telegram REQUEST ERROR:",
            method,
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

        timeout=8

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

        },

        timeout=5

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

            ]

        ]
    }


# =========================================================
# GET USER HISTORY
# =========================================================

def get_history(chat_id):

    with STATE_LOCK:

        state = USER_STATE.get(
            chat_id
        )

        if not state:

            return []

        return list(
            state.get(
                "history",
                []
            )
        )


# =========================================================
# SAVE SONG
# =========================================================

def save_song(
    chat_id,
    mood,
    message_id
):

    with STATE_LOCK:

        state = USER_STATE.setdefault(

            chat_id,

            {

                "mood":
                    mood,

                "history":
                    []

            }

        )


        state["mood"] = mood


        history = state.setdefault(

            "history",

            []

        )


        if message_id in history:

            history.remove(
                message_id
            )


        history.append(
            message_id
        )


        if len(history) > MAX_HISTORY:

            del history[
                :-MAX_HISTORY
            ]


# =========================================================
# SET USER MOOD
# =========================================================

def set_user_mood(
    chat_id,
    mood
):

    with STATE_LOCK:

        state = USER_STATE.setdefault(

            chat_id,

            {

                "mood":
                    mood,

                "history":
                    []

            }

        )


        state["mood"] = mood


# =========================================================
# GET USER MOOD
# =========================================================

def get_user_mood(
    chat_id
):

    with STATE_LOCK:

        state = USER_STATE.get(
            chat_id
        )


        if not state:

            return None


        return state.get(
            "mood"
        )


# =========================================================
# PICK NEW SONG
#
# User တစ်ယောက်တည်းအတွက်
# history ထဲရှိပြီးသား song ကို မရွေးဘူး။
#
# =========================================================

def pick_new_song(
    chat_id,
    mood
):

    songs = list(
        MOOD_MUSIC.get(
            mood,
            []
        )
    )


    if not songs:

        return None


    history = get_history(
        chat_id
    )


    # =====================================================
    # First try:
    # User မကြားဖူးသေးတဲ့ songs
    # =====================================================

    fresh = [

        song

        for song in songs

        if song not in history

    ]


    if fresh:

        return random.choice(
            fresh
        )


    # =====================================================
    # User က ဒီ mood ထဲက songs အားလုံးနီးပါး
    # ကြားပြီးသွားပြီ။
    #
    # History အကုန်ထပ်မပိတ်တော့ဘဲ
    # oldest history ကိုဖယ်ပြီး
    # ပြန်ရွေးခွင့်ပေးမယ်။
    # =====================================================

    if len(songs) > 1:

        last_song = (
            history[-1]
            if history
            else None
        )


        candidates = [

            song

            for song in songs

            if song != last_song

        ]


        if candidates:

            return random.choice(
                candidates
            )


    return songs[0]


# =========================================================
# SEND TRACK WITH RETRY
#
# Message ID တစ်ခု error ဖြစ်ရင်
# အခြား track ကို ဆက်စမ်းမယ်။
# =========================================================

def send_track(
    chat_id,
    mood
):

    songs = list(
        MOOD_MUSIC.get(
            mood,
            []
        )
    )


    if not songs:

        send_message(

            chat_id,

            f"{MOOD_NAMES[mood]}\n\n"
            "⚠️ ဒီ mood အတွက် track မရှိသေးပါ။",

            mood_menu()

        )

        return


    history = get_history(
        chat_id
    )


    # =====================================================
    # Fresh tracks first
    # =====================================================

    fresh = [

        song

        for song in songs

        if song not in history

    ]


    random.shuffle(
        fresh
    )


    # =====================================================
    # If all were already played,
    # use non-last songs.
    # =====================================================

    old = [

        song

        for song in songs

        if song in history

    ]


    random.shuffle(
        old
    )


    last_song = (
        history[-1]
        if history
        else None
    )


    candidates = fresh + [

        song

        for song in old

        if song != last_song

    ]


    if not candidates:

        candidates = songs[:]


    # =====================================================
    # Try candidates
    # =====================================================

    tried = set()


    for message_id in candidates:

        if message_id in tried:

            continue


        tried.add(
            message_id
        )


        result = copy_music(

            chat_id,

            message_id

        )


        if result.get("ok"):

            save_song(

                chat_id,

                mood,

                message_id

            )


            print(

                "TRACK SENT",

                "| chat =", chat_id,

                "| mood =", mood,

                "| message =", message_id

            )


            send_message(

                chat_id,

                f"{MOOD_NAMES[mood]}\n\n"
                "🎧 Enjoy your music! 🔥",

                music_buttons()

            )


            return


        print(

            "TRACK FAILED",

            "| chat =", chat_id,

            "| mood =", mood,

            "| message =", message_id

        )


    # =====================================================
    # Nothing worked
    # =====================================================

    send_message(

        chat_id,

        f"{MOOD_NAMES[mood]}\n\n"
        "❌ ဒီ mood ထဲက track တွေကို "
        "channel ကနေ copy လုပ်လို့မရပါ။\n\n"
        "Channel permission / Message ID "
        "ကို စစ်ပေးပါ။",

        mood_menu()

    )


# =========================================================
# BACKGROUND SEND
# =========================================================

def background_send(
    chat_id,
    mood
):

    try:

        send_track(

            chat_id,

            mood

        )

    except Exception as e:

        print(

            "BACKGROUND SEND ERROR:",

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


        message = (
            callback.get(
                "message"
            )
            or {}
        )


        chat = (
            message.get(
                "chat"
            )
            or {}
        )


        chat_id = chat.get(
            "id"
        )


        if not chat_id:

            answer_callback(

                callback_id,

                "Chat not found"

            )

            return "OK"


        # =================================================
        # MOOD BUTTON
        # =================================================

        if data.startswith(
            "mood_"
        ):

            mood = data[
                5:
            ]


            if mood not in MOODS:

                answer_callback(

                    callback_id,

                    "Invalid mood"

                )

                return "OK"


            # =================================================
            # Save mood immediately
            # =================================================

            set_user_mood(

                chat_id,

                mood

            )


            # =================================================
            # Answer callback immediately
            # =================================================

            answer_callback(

                callback_id,

                f"{MOOD_NAMES[mood]} ✓"

            )


            # =================================================
            # Send music in background
            # =================================================

            threading.Thread(

                target=background_send,

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

            mood = get_user_mood(
                chat_id
            )


            answer_callback(

                callback_id,

                "🔀 Finding next track..."

            )


            if not mood:

                send_message(

                    chat_id,

                    "🎧 အရင်ဆုံး Mood တစ်ခုရွေးပါ 👇",

                    mood_menu()

                )

                return "OK"


            threading.Thread(

                target=background_send,

                args=(

                    chat_id,

                    mood

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

        chat = (
            message.get(
                "chat"
            )
            or {}
        )


        chat_id = chat.get(
            "id"
        )


        text = (

            message.get(
                "text",
                ""
            )

            or ""

        ).strip()


        if not chat_id:

            return "OK"


        # =================================================
        # START
        # =================================================

        if text == "/start":

            send_message(

                chat_id,

                "🎧 NOT YOUR VIBE MUSIC\n\n"
                "Welcome! 🔥\n\n"
                "Mood တစ်ခုရွေးပြီး "
                "သီချင်းနားထောင်ပါ 👇",

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
        # NEXT
        # =================================================

        if text == "/next":

            mood = get_user_mood(
                chat_id
            )


            if not mood:

                send_message(

                    chat_id,

                    "🎧 အရင်ဆုံး Mood တစ်ခုရွေးပါ 👇",

                    mood_menu()

                )

                return "OK"


            threading.Thread(

                target=background_send,

                args=(

                    chat_id,

                    mood

                ),

                daemon=True

            ).start()


            return "OK"


        # =================================================
        # HELP
        # =================================================

        if text == "/help":

            send_message(

                chat_id,

                "🎧 NOT YOUR VIBE MUSIC BOT\n\n"

                "/start — Start\n"
                "/mood — Mood Menu\n"
                "/next — Next Track\n"
                "/help — Help\n\n"

                "🎵 Mood တစ်ခုရွေးပါ။\n"
                "Bot က အဲဒီ mood ထဲက track ကို "
                "ရွေးပြီး ပို့ပေးပါမယ်။"

            )

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
        f"{RENDER_URL.rstrip('/')}/webhook"
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
        "WEBHOOK RESULT:",
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

            f"{MOOD_NAMES[mood]} = "
            f"{len(MOOD_MUSIC[mood])} tracks"

        )


    print(
        "=========================================="
    )


    setup_webhook()


    port = int(

        os.getenv(

            "PORT",

            "10000"

        )

    )


    app.run(

        host="0.0.0.0",

        port=port,

        threaded=True

    )
