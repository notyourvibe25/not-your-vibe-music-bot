import os
import random
import requests
from flask import Flask, request

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

CHANNEL_USERNAME = "@notyourvibemp3collection"


def telegram(method, data):
    return requests.post(
        f"{TELEGRAM_API}/{method}",
        json=data,
        timeout=20
    ).json()


def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        data["reply_markup"] = reply_markup

    return telegram("sendMessage", data)
    def copy_channel_message(chat_id, message_id):
    return telegram(
        "copyMessage",
        {
            "chat_id": chat_id,
            "from_chat_id": CHANNEL_USERNAME,
            "message_id": message_id
        }
    )


def answer_callback(callback_id, text=""):
    return telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text
        }
    )
if data in MOOD_GENRES:

    answer_callback(
        callback_id,
        "🎧 Finding your music..."
    )

    # TEST SONG
    result = copy_channel_message(
        chat_id,
        2065
    )

    print("COPY RESULT:", result)

    if not result.get("ok"):
        send_message(
            chat_id,
            "❌ ဒီသီချင်းကို ပြန်ပို့လို့မရသေးပါ။\n\n"
            f"Telegram error: {result.get('description')}"
)

def mood_menu():
    return {
        "inline_keyboard": [
            [
                {"text": "😢 Sad", "callback_data": "sad"},
                {"text": "❤️ Love", "callback_data": "love"}
            ],
            [
                {"text": "🌙 Chill", "callback_data": "chill"},
                {"text": "🔥 Hype", "callback_data": "hype"}
            ],
            [
                {"text": "🖤 Dark", "callback_data": "dark"},
                {"text": "⚡ Energetic", "callback_data": "energetic"}
            ],
            [
                {"text": "🚗 Night Drive", "callback_data": "night"},
                {"text": "🌌 Melodic", "callback_data": "melodic"}
            ]
        ]
    }


# Genre groups
MOOD_GENRES = {

    "sad": [
        "Melodic Dubstep",
        "Future Bass",
        "Emotional",
        "Melodic Bass",
        "Future Garage"
    ],

    "love": [
        "Future Bass",
        "Melodic House",
        "Future Pop",
        "Chill",
        "Deep House"
    ],

    "chill": [
        "Future Garage",
        "Chill",
        "Lo-Fi",
        "Ambient",
        "Downtempo",
        "Melodic"
    ],

    "hype": [
        "Festival Trap",
        "Trap",
        "Hardtrap",
        "Future Riddim",
        "Dubstep",
        "Bass House"
    ],

    "dark": [
        "Dark Trap",
        "Hardtrap",
        "Terror Bass",
        "Dark Bass",
        "Dubstep"
    ],

    "energetic": [
        "Future Riddim",
        "Hardtrap",
        "Festival Trap",
        "Dubstep",
        "Bass House",
        "EDM"
    ],

    "night": [
        "Future Garage",
        "Melodic House",
        "Deep House",
        "Chill",
        "Synthwave"
    ],

    "melodic": [
        "Melodic Dubstep",
        "Melodic Future Bass",
        "Future Bass",
        "Melodic House",
        "Future Garage"
    ]
}


@app.route("/", methods=["GET"])
def home():
    return "NOT YOUR VIBE Music Bot is running!"


@app.route("/health", methods=["GET"])
def health():
    return "OK"


@app.route("/webhook", methods=["POST"])
def webhook():

    update = request.get_json(silent=True)

    if not update:
        return "OK"

    # =========================
    # MESSAGE
    # =========================

    message = update.get("message")

    if message:

        chat_id = message["chat"]["id"]
        text = message.get("text", "").strip()

        if text == "/start":

            send_message(
                chat_id,
                "🎧 NOT YOUR VIBE MUSIC\n\n"
                "How are you feeling today?\n\n"
                "Choose your mood 👇",
                mood_menu()
            )

        elif text == "/mood":

            send_message(
                chat_id,
                "🎧 Choose your mood 👇",
                mood_menu()
            )

        elif text == "/help":

            send_message(
                chat_id,
                "🎧 NOT YOUR VIBE MUSIC BOT\n\n"
                "/start - Start Bot\n"
                "/mood - Choose Mood\n"
                "/help - Help"
            )

    # =========================
    # BUTTON
    # =========================

    callback = update.get("callback_query")

    if callback:

        callback_id = callback["id"]
        chat_id = callback["message"]["chat"]["id"]
        mood = callback.get("data")

        if mood in MOOD_GENRES:

            answer_callback(
                callback_id,
                "Searching..."
            )

            genres = MOOD_GENRES[mood]

            genre_text = "\n".join(
                f"• {genre}" for genre in genres
            )

            send_message(
                chat_id,
                f"🎧 MOOD: {mood.upper()}\n\n"
                f"🔎 Searching these music styles:\n\n"
                f"{genre_text}\n\n"
                f"📡 Channel: {CHANNEL_USERNAME}\n\n"
                f"⏳ Music database is being connected..."
            )

    return "OK"


# =========================
# WEBHOOK
# =========================

if BOT_TOKEN and RENDER_URL:

    webhook_url = f"{RENDER_URL}/webhook"

    response = requests.post(
        f"{TELEGRAM_API}/setWebhook",
        json={"url": webhook_url},
        timeout=20
    )

    print("Webhook:", response.text)


if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 10000)
    )

    app.run(
        host="0.0.0.0",
        port=port
        )
