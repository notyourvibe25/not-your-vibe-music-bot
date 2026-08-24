import os
import random
import requests
from flask import Flask, request

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

CHANNEL_USERNAME = "@notyourvibemp3collection"


# ==========================================
# YOUR CHANNEL MESSAGE IDs
# ==========================================

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
    2065
]


# ==========================================
# TELEGRAM API
# ==========================================

def telegram(method, data):

    try:

        response = requests.post(
            f"{TELEGRAM_API}/{method}",
            json=data,
            timeout=30
        )

        return response.json()

    except Exception as e:

        print("Telegram Error:", e)

        return {
            "ok": False,
            "description": str(e)
        }


# ==========================================
# SEND TEXT
# ==========================================

def send_message(chat_id, text, reply_markup=None):

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


# ==========================================
# ANSWER BUTTON
# ==========================================

def answer_callback(callback_id, text=""):

    return telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text
        }
    )


# ==========================================
# COPY MUSIC FROM CHANNEL
# ==========================================

def copy_music(chat_id, message_id):

    return telegram(
        "copyMessage",
        {
            "chat_id": chat_id,
            "from_chat_id": CHANNEL_USERNAME,
            "message_id": message_id
        }
    )


# ==========================================
# MOOD MENU
# ==========================================

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


# ==========================================
# MUSIC BUTTONS
# ==========================================

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


# ==========================================
# HOME
# ==========================================

@app.route("/", methods=["GET"])
def home():

    return "NOT YOUR VIBE Music Bot is running!"


# ==========================================
# HEALTH
# ==========================================

@app.route("/health", methods=["GET"])
def health():

    return "OK"


# ==========================================
# WEBHOOK
# ==========================================

@app.route("/webhook", methods=["POST"])
def webhook():

    update = request.get_json(silent=True)

    if not update:

        return "OK"


    # ======================================
    # NORMAL MESSAGE
    # ======================================

    message = update.get("message")

    if message:

        chat_id = message["chat"]["id"]

        text = message.get(
            "text",
            ""
        ).strip()


        # ==============================
        # START
        # ==============================

        if text == "/start":

            send_message(
                chat_id,

                "🎧 NOT YOUR VIBE MUSIC\n\n"
                "Welcome to NOT YOUR VIBE.\n\n"
                "Choose your mood and I'll find music for you 👇",

                mood_menu()
            )


        # ==============================
        # MOOD
        # ==============================

        elif text == "/mood":

            send_message(
                chat_id,

                "🎧 What's your mood today?\n\n"
                "Choose one 👇",

                mood_menu()
            )


        # ==============================
        # HELP
        # ==============================

        elif text == "/help":

            send_message(
                chat_id,

                "🎧 NOT YOUR VIBE MUSIC BOT\n\n"
                "/start - Start the bot\n"
                "/mood - Choose your mood\n"
                "/help - Help"
            )


    # ======================================
    # CALLBACK BUTTON
    # ======================================

    callback = update.get("callback_query")

    if callback:

        callback_id = callback["id"]

        callback_message = callback.get(
            "message",
            {}
        )

        chat_id = callback_message.get(
            "chat",
            {}
        ).get(
            "id"
        )

        data = callback.get(
            "data",
            ""
        )


        # ==================================
        # CHANGE MOOD
        # ==================================

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


        # ==================================
        # NEXT MUSIC
        # ==================================

        elif data == "next_music":

            answer_callback(
                callback_id,
                "🔀 Finding another track..."
            )

            message_id = random.choice(
                MUSIC_IDS
            )

            result = copy_music(
                chat_id,
                message_id
            )

            print(
                "NEXT MUSIC:",
                message_id,
                result
            )

            if not result.get("ok"):

                send_message(
                    chat_id,

                    "❌ Couldn't send this track.\n\n"
                    "Trying another one..."
                )

                # Try another message

                backup_id = random.choice(
                    MUSIC_IDS
                )

                backup_result = copy_music(
                    chat_id,
                    backup_id
                )

                print(
                    "BACKUP MUSIC:",
                    backup_id,
                    backup_result
                )

            else:

                send_message(
                    chat_id,

                    "🔀 Next track 👇",

                    music_buttons()
                )


        # ==================================
        # MOOD SELECTION
        # ==================================

        elif data.startswith("mood_"):

            mood = data.replace(
                "mood_",
                ""
            )

            mood_names = {

                "sad": "😢 SAD",

                "love": "❤️ LOVE",

                "chill": "🌙 CHILL",

                "hype": "🔥 HYPE",

                "dark": "🖤 DARK",

                "energetic": "⚡ ENERGETIC",

                "night": "🚗 NIGHT DRIVE",

                "melodic": "🌌 MELODIC"
            }

            mood_name = mood_names.get(
                mood,
                "🎧 MUSIC"
            )


            answer_callback(
                callback_id,

                f"{mood_name} selected!"
            )


            send_message(
                chat_id,

                f"🎧 {mood_name}\n\n"
                "🔎 Finding a track for your mood..."
            )


            # Choose random music

            message_id = random.choice(
                MUSIC_IDS
            )


            result = copy_music(
                chat_id,
                message_id
            )


            print(
                "MOOD:",
                mood,
                "MESSAGE:",
                message_id,
                "RESULT:",
                result
            )


            # ==============================
            # SUCCESS
            # ==============================

            if result.get("ok"):

                send_message(
                    chat_id,

                    f"🎧 {mood_name}\n\n"
                    "Enjoy your music! 🔥",

                    music_buttons()
                )


            # ==============================
            # ERROR
            # ==============================

            else:

                print(
                    "COPY ERROR:",
                    result
                )

                send_message(
                    chat_id,

                    "❌ I couldn't get the music "
                    "from the channel.\n\n"
                    "Please check that the bot can access "
                    "the channel."
                )


    return "OK"


# ==========================================
# AUTOMATIC WEBHOOK
# ==========================================

if BOT_TOKEN and RENDER_URL:

    webhook_url = (
        f"{RENDER_URL}/webhook"
    )

    try:

        response = requests.post(

            f"{TELEGRAM_API}/setWebhook",

            json={
                "url": webhook_url
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


# ==========================================
# RUN SERVER
# ==========================================

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
