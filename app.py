import os
import random
import threading
import time
from collections import defaultdict, deque

import requests
from flask import Flask, request


# =========================================================
# APP
# =========================================================

app = Flask(__name__)


# =========================================================
# ENV
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

if not BOT_TOKEN:
    print("WARNING: BOT_TOKEN is missing")

TELEGRAM_API = (
    f"https://api.telegram.org/bot{BOT_TOKEN}"
    if BOT_TOKEN
    else ""
)


# =========================================================
# HTTP SESSION
# Reuse connections -> faster
# =========================================================

http = requests.Session()

http.headers.update({
    "User-Agent": "NOT-YOUR-VIBE-MUSIC-BOT/2.0"
})


# =========================================================
# USER LOCKS
#
# User တစ်ယောက်တည်းက button နှစ်ခါမြန်မြန်နှိပ်ရင်
# သီချင်းနှစ်ပုဒ်တစ်ပြိုင်တည်းထွက်မသွားအောင်
# user တစ်ယောက်ချင်းစီ lock ခွဲထားတယ်။
# =========================================================

USER_LOCKS = defaultdict(threading.Lock)


# =========================================================
# USER STATE
#
# user တစ်ယောက်ချင်းစီမှာ
#
# mood
# history
# last_song
#
# သီးခြားရှိမယ်။
# =========================================================

USER_STATE = {}

STATE_LOCK = threading.Lock()


# =========================================================
# MOODS
# =========================================================

MOOD_NAMES = {

    "sad": "😢 SAD",

    "love": "❤️ LOVE",

    "chill": "🌙 CHILL",

    "hype": "🔥 HYPE",

    "energetic": "⚡ ENERGETIC",

    "dark": "🖤 DARK",

    "night": "🚗 NIGHT DRIVE",

    "melodic": "🌌 MELODIC"
}


MOODS = [
    "sad",
    "love",
    "chill",
    "hype",
    "energetic",
    "dark",
    "night",
    "melodic"
]


# =========================================================
# CHANNEL CONFIGURATION
#
# ---------------------------------------------------------
# ဒီနေရာမှာ channel ID တွေထည့်ရမယ်။
#
# Public channel ဖြစ်ရင် username သုံးလို့ရတယ်။
#
# Private channel ဖြစ်ရင်
# -100xxxxxxxxxx
# လို ID သုံးရမယ်။
#
# ID မသိသေးရင် ""
# ထားပါ။
#
# Bot ကို channel တစ်ခုချင်းစီမှာ ADMIN ထည့်ထားရမယ်။
# =========================================================

CHANNELS = {

    "sad": "",

    "love": "",

    "chill": "",

    "hype": "",

    "energetic": "",

    "dark": "",

    "night": "",

    "melodic": ""
}


# =========================================================
# CHANNEL USERNAME FALLBACK
#
# Public channel username ရှိတဲ့ channel တွေမှာ
# ID မထည့်သေးလည်း username နဲ့ copy လုပ်နိုင်တယ်။
#
# Private invite link (+xxxx) ကို Bot API
# from_chat_id အနေနဲ့ တိုက်ရိုက်မသုံးနိုင်ပါ။
# အဲဒီ channel ရဲ့ -100... ID လိုပါတယ်။
# =========================================================

PUBLIC_CHANNELS = {

    "sad": "@sadmooddatabase",

    "love": "@lovemooddatabase",

    "chill": "@chillmooddatabase",

    "energetic": "@energeticmooddatabase",

    "dark": "@darkmooddatabase",

    "night": "@nightdrivemooddatabase"

}


# =========================================================
# TRACK DATABASE
#
# ---------------------------------------------------------
# Private mood channel ထဲမှာရှိတဲ့ track တွေရဲ့
# Telegram MESSAGE ID တွေကို ဒီမှာထည့်နိုင်တယ်။
#
# ဥပမာ:
#
# "sad": [
#     1,
#     2,
#     3,
#     4
# ]
#
# IMPORTANT:
# Channel ID နဲ့ Message ID မတူပါ။
#
# Channel ID:
# -1001234567890
#
# Message ID:
# 15
#
# =========================================================

TRACKS = {

    "sad": [],

    "love": [],

    "chill": [],

    "hype": [],

    "energetic": [],

    "dark": [],

    "night": [],

    "melodic": []
}


# =========================================================
# AUTO-DISCOVERED CHANNELS
#
# Bot က channel_post ရတဲ့အခါ
# Channel ID ကို ဒီ memory database ထဲထည့်မယ်။
#
# Render restart ဖြစ်ရင် memory ပျောက်မယ်။
# ဒါကြောင့် Logs ကနေ ID ကိုယူပြီး CHANNELS ထဲ
# permanent ထည့်ထားတာက အကောင်းဆုံး။
# =========================================================

DISCOVERED_CHANNELS = {}


# =========================================================
# AUTO-DISCOVERED TRACKS
#
# Bot ကို channel ထဲ admin ထည့်ပြီးနောက်
# channel ထဲ post အသစ်တင်ရင်
#
# message_id
#
# ကို ဒီထဲမှာ auto သိမ်းမယ်။
# =========================================================

DISCOVERED_TRACKS = {

    "sad": set(),

    "love": set(),

    "chill": set(),

    "hype": set(),

    "energetic": set(),

    "dark": set(),

    "night": set(),

    "melodic": set()
}


# =========================================================
# NORMALIZE DATABASE
# =========================================================

for mood in MOODS:

    TRACKS[mood] = list(
        dict.fromkeys(
            TRACKS.get(mood, [])
        )
    )


# =========================================================
# TELEGRAM REQUEST
# =========================================================

def telegram(
    method,
    data=None,
    timeout=10
):

    if not TELEGRAM_API:

        return {
            "ok": False,
            "description": "BOT_TOKEN missing"
        }


    try:

        response = http.post(

            f"{TELEGRAM_API}/{method}",

            json=data or {},

            timeout=timeout

        )


        result = response.json()


        if not result.get("ok"):

            print(
                "TELEGRAM ERROR:",
                method,
                result
            )


        return result


    except Exception as e:

        print(
            "TELEGRAM REQUEST ERROR:",
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

        "chat_id": chat_id,

        "text": text,

        "disable_web_page_preview": True
    }


    if keyboard is not None:

        data["reply_markup"] = keyboard


    return telegram(

        "sendMessage",

        data,

        timeout=8
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
# COPY MESSAGE
# =========================================================

def copy_music(
    target_chat_id,
    source_chat_id,
    message_id
):

    return telegram(

        "copyMessage",

        {

            "chat_id":
                target_chat_id,

            "from_chat_id":
                source_chat_id,

            "message_id":
                message_id

        },

        timeout=15
    )


# =========================================================
# GET CHANNEL SOURCE
# =========================================================

def get_channel_source(mood):

    # -----------------------------------------------------
    # 1. Manually configured ID
    # -----------------------------------------------------

    channel_id = CHANNELS.get(
        mood,
        ""
    )

    if channel_id:

        return channel_id


    # -----------------------------------------------------
    # 2. Auto discovered ID
    # -----------------------------------------------------

    discovered = DISCOVERED_CHANNELS.get(
        mood
    )

    if discovered:

        return discovered


    # -----------------------------------------------------
    # 3. Public username fallback
    # -----------------------------------------------------

    public_username = PUBLIC_CHANNELS.get(
        mood
    )

    if public_username:

        return public_username


    return None


# =========================================================
# IDENTIFY MOOD FROM CHANNEL ID
# =========================================================

def identify_channel_mood(channel_id):

    # -----------------------------------------------------
    # First check manual CHANNELS
    # -----------------------------------------------------

    for mood in MOODS:

        configured = CHANNELS.get(
            mood,
            ""
        )

        if configured and str(configured) == str(channel_id):

            return mood


    # -----------------------------------------------------
    # Then discovered channels
    # -----------------------------------------------------

    for mood, discovered_id in DISCOVERED_CHANNELS.items():

        if str(discovered_id) == str(channel_id):

            return mood


    return None


# =========================================================
# CHANNEL POST DEBUG / AUTO DISCOVERY
# =========================================================

def process_channel_post(
    channel_post
):

    chat = channel_post.get(
        "chat",
        {}
    )


    channel_id = chat.get(
        "id"
    )


    channel_title = chat.get(
        "title"
    )


    channel_username = chat.get(
        "username"
    )


    message_id = channel_post.get(
        "message_id"
    )


    if not channel_id:

        return


    print("")
    print("==============================================")
    print("📢 CHANNEL POST DETECTED")
    print("Channel ID       :", channel_id)
    print("Channel Title    :", channel_title)
    print("Channel Username :", channel_username)
    print("Message ID       :", message_id)
    print("==============================================")
    print("")


    # -----------------------------------------------------
    # Try to identify mood
    # -----------------------------------------------------

    mood = identify_channel_mood(
        channel_id
    )


    if mood:

        DISCOVERED_CHANNELS[mood] = channel_id

        if message_id:

            DISCOVERED_TRACKS[mood].add(
                int(message_id)
            )


        print(
            "AUTO MOOD DETECTED:",
            mood
        )

        print(
            "AUTO TRACK ADDED:",
            message_id
        )

        print(
            "TOTAL DISCOVERED:",
            len(
                DISCOVERED_TRACKS[mood]
            )
        )

        print("")


    else:

        # -------------------------------------------------
        # If channel not configured yet
        # -------------------------------------------------

        print(
            "⚠️ CHANNEL NOT MAPPED"
        )

        print(
            "Add this Channel ID to CHANNELS"
        )

        print(
            "Channel ID:",
            channel_id
        )

        print("")


# =========================================================
# GET TRACKS
# =========================================================

def get_tracks(
    mood
):

    result = []


    # -----------------------------------------------------
    # Manual tracks
    # -----------------------------------------------------

    result.extend(
        TRACKS.get(
            mood,
            []
        )
    )


    # -----------------------------------------------------
    # Auto discovered tracks
    # -----------------------------------------------------

    result.extend(
        list(
            DISCOVERED_TRACKS.get(
                mood,
                set()
            )
        )
    )


    # -----------------------------------------------------
    # Remove duplicates
    # -----------------------------------------------------

    result = list(
        dict.fromkeys(
            result
        )
    )


    return result


# =========================================================
# USER STATE
# =========================================================

def get_user_state(
    chat_id
):

    with STATE_LOCK:

        if chat_id not in USER_STATE:

            USER_STATE[chat_id] = {

                "mood": None,

                "last_song": None,

                # -----------------------------------------
                # Keep last 50 songs
                # -----------------------------------------

                "history": deque(
                    maxlen=50
                )
            }


        return USER_STATE[chat_id]


# =========================================================
# PICK NEW TRACK
# =========================================================

def pick_track(
    chat_id,
    mood
):

    tracks = get_tracks(
        mood
    )


    if not tracks:

        return None


    state = get_user_state(
        chat_id
    )


    history = list(
        state["history"]
    )


    # -----------------------------------------------------
    # First try songs not recently played
    # -----------------------------------------------------

    candidates = [

        track

        for track in tracks

        if track not in history
    ]


    # -----------------------------------------------------
    # If all songs have been played
    # allow old songs again,
    # but don't immediately repeat last one.
    # -----------------------------------------------------

    if not candidates:

        last_song = state.get(
            "last_song"
        )


        candidates = [

            track

            for track in tracks

            if track != last_song
        ]


    # -----------------------------------------------------
    # Only one track
    # -----------------------------------------------------

    if not candidates:

        candidates = tracks


    return random.choice(
        candidates
    )


# =========================================================
# SAVE USER TRACK
# =========================================================

def save_user_track(
    chat_id,
    mood,
    message_id
):

    with STATE_LOCK:

        state = get_user_state(
            chat_id
        )


        state["mood"] = mood

        state["last_song"] = message_id

        state["history"].append(
            message_id
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
                    "text": "⚡ Energetic",

                    "callback_data":
                        "mood_energetic"
                },

                {
                    "text": "🖤 Dark",

                    "callback_data":
                        "mood_dark"
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

            ]

        ]
    }


# =========================================================
# SEND TRACK
# =========================================================

def send_track(
    chat_id,
    mood
):

    lock = USER_LOCKS[
        chat_id
    ]


    # -----------------------------------------------------
    # User တစ်ယောက်တည်းက တစ်ချိန်တည်း request
    # နှစ်ခုမလုပ်နိုင်အောင်
    # -----------------------------------------------------

    with lock:

        tracks = get_tracks(
            mood
        )


        # -------------------------------------------------
        # No tracks
        # -------------------------------------------------

        if not tracks:

            send_message(

                chat_id,

                f"{MOOD_NAMES[mood]}\n\n"
                "⚠️ ဒီ mood channel ထဲမှာ "
                "track မတွေ့သေးပါ။\n\n"
                "Bot ကို channel ထဲ ADMIN ထည့်ပြီး "
                "track post တစ်ခုတင်ပါ။",

                mood_menu()
            )

            return


        # -------------------------------------------------
        # Channel source
        # -------------------------------------------------

        source = get_channel_source(
            mood
        )


        if not source:

            send_message(

                chat_id,

                f"{MOOD_NAMES[mood]}\n\n"
                "⚠️ ဒီ mood channel ID မသတ်မှတ်ရသေးပါ။",

                mood_menu()
            )

            return


        # -------------------------------------------------
        # Try several tracks
        #
        # Deleted / unavailable track တစ်ခုရှိရင်
        # နောက် track ကို ဆက်စမ်းမယ်။
        # -------------------------------------------------

        state = get_user_state(
            chat_id
        )


        history = list(
            state["history"]
        )


        candidates = [

            track

            for track in tracks

            if track not in history
        ]


        if not candidates:

            candidates = [

                track

                for track in tracks

                if track != state.get(
                    "last_song"
                )
            ]


        if not candidates:

            candidates = tracks


        random.shuffle(
            candidates
        )


        # -------------------------------------------------
        # Try maximum 10 tracks
        # -------------------------------------------------

        attempts = candidates[:10]


        for message_id in attempts:

            result = copy_music(

                chat_id,

                source,

                message_id

            )


            if result.get("ok"):

                save_user_track(

                    chat_id,

                    mood,

                    message_id

                )


                print(
                    "✅ MUSIC SENT",
                    "| user:",
                    chat_id,
                    "| mood:",
                    mood,
                    "| channel:",
                    source,
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


            print(
                "❌ COPY FAILED",
                "| mood:",
                mood,
                "| source:",
                source,
                "| message:",
                message_id,
                "| result:",
                result
            )


        # -------------------------------------------------
        # Nothing worked
        # -------------------------------------------------

        send_message(

            chat_id,

            f"{MOOD_NAMES[mood]}\n\n"
            "❌ Track ပို့လို့မရပါ။\n\n"
            "Channel ID / Message ID / "
            "Bot Admin permission ကိုစစ်ပါ။",

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
# NEXT TRACK
# =========================================================

def background_next(
    chat_id
):

    try:

        state = get_user_state(
            chat_id
        )


        mood = state.get(
            "mood"
        )


        if not mood:

            send_message(

                chat_id,

                "🎧 အရင်ဆုံး Mood တစ်ခုရွေးပါ 👇",

                mood_menu()
            )

            return


        send_track(

            chat_id,

            mood

        )


    except Exception as e:

        print(
            "NEXT ERROR:",
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
    # CHANNEL POST
    #
    # ဒီနေရာက Private channel ID ရှာဖို့ အရေးကြီးဆုံး
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
    # CALLBACK
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


        chat = (
            callback_message.get(
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
                len("mood_"):
            ]


            if mood not in MOODS:

                answer_callback(

                    callback_id,

                    "Invalid mood"
                )

                return "OK"


            # ---------------------------------------------
            # Save mood immediately
            # ---------------------------------------------

            state = get_user_state(
                chat_id
            )


            with STATE_LOCK:

                state["mood"] = mood

                # -----------------------------------------
                # Mood ပြောင်းရင် history မဖျက်ဘူး။
                #
                # ဒါကြောင့် mood တစ်ခုချင်းစီမှာ
                # သီချင်းထပ်မှုနည်းမယ်။
                # -----------------------------------------

            # ---------------------------------------------
            # Callback ကိုချက်ချင်းဖြေ
            # ---------------------------------------------

            answer_callback(

                callback_id,

                f"{MOOD_NAMES[mood]} ✓"
            )


            # ---------------------------------------------
            # Music send background
            # ---------------------------------------------

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

            answer_callback(

                callback_id,

                "🔀 Finding next track..."
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

            get_user_state(
                chat_id
            )


            send_message(

                chat_id,

                "🎧 NOT YOUR VIBE MUSIC\n\n"
                "Welcome! 🔥\n\n"
                "Mood တစ်ခုရွေးပါ 👇",

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

            threading.Thread(

                target=background_next,

                args=(
                    chat_id,
                ),

                daemon=True

            ).start()


            return "OK"


        # =================================================
        # RESET
        # =================================================

        if text == "/reset":

            with STATE_LOCK:

                USER_STATE.pop(
                    chat_id,
                    None
                )


            send_message(

                chat_id,

                "♻️ Your music history has been reset.\n\n"
                "Choose a mood 👇",

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

                "/start - Start\n"
                "/mood - Mood menu\n"
                "/next - Next track\n"
                "/reset - Reset your history\n"
                "/help - Help\n\n"

                "🎵 Mood ရွေးလိုက်တာနဲ့ "
                "သက်ဆိုင်ရာ mood channel ထဲက "
                "track ကို random ရွေးပေးပါတယ်။"
            )


            return "OK"


    return "OK"


# =========================================================
# SET WEBHOOK
# =========================================================

def setup_webhook():

    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN missing"
        )

        return


    if not RENDER_URL:

        print(
            "❌ RENDER_EXTERNAL_URL missing"
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

            # ------------------------------------------------
            # IMPORTANT:
            # channel_post ထည့်ထားတယ်။
            # ------------------------------------------------

            "allowed_updates": [

                "message",

                "callback_query",

                "channel_post"

            ],

            # ------------------------------------------------
            # Render load များရင် concurrent webhook delivery
            # ပိုကောင်းစေဖို့
            # ------------------------------------------------

            "max_connections":
                40,

            # ------------------------------------------------
            # Old pending updates မလိုချင်
            # ------------------------------------------------

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
# WEBHOOK INFO
# =========================================================

def show_webhook_info():

    result = telegram(

        "getWebhookInfo",

        {},

        timeout=10
    )


    print(
        "=============================================="
    )

    print(
        "WEBHOOK INFO"
    )

    print(
        result
    )

    print(
        "=============================================="
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    print(
        "=============================================="
    )

    print(
        "🎧 NOT YOUR VIBE MUSIC BOT v2"
    )

    print(
        "=============================================="
    )

    print(
        "Webhook URL:",
        RENDER_URL
    )

    print(
        "=============================================="
    )

    print(
        "Configured channels:"
    )


    for mood in MOODS:

        print(
            mood,
            "=>",
            CHANNELS.get(
                mood
            )
            or PUBLIC_CHANNELS.get(
                mood
            )
            or "NOT SET"
        )


    print(
        "=============================================="
    )


    # -----------------------------------------------------
    # Configure webhook
    # -----------------------------------------------------

    setup_webhook()


    # -----------------------------------------------------
    # Show webhook status
    # -----------------------------------------------------

    show_webhook_info()


    # -----------------------------------------------------
    # PORT
    # -----------------------------------------------------

    port = int(

        os.getenv(
            "PORT",
            "10000"
        )
    )


    print(
        "Starting server on port:",
        port
    )


    # -----------------------------------------------------
    # Flask
    # -----------------------------------------------------

    app.run(

        host="0.0.0.0",

        port=port,

        threaded=True

)
