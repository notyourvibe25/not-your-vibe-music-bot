import os
import requests
from flask import Flask, request

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

CHANNEL = "@notyourvibemp3collection"


def telegram(method, data):
    response = requests.post(
        f"{TELEGRAM_API}/{method}",
        json=data,
        timeout=20
    )
    return response.json()


def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        data["reply_markup"] = reply_markup

    return telegram("sendMessage", data)


def answer_callback(callback_id, text=""):
    return telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text
        }
    )


def mood_menu():
    return {
        "inline_keyboard": [
            [
                {"text": "😢 Sad", "callback_data": "mood_sad"},
                {"text": "❤️ Love", "callback_data": "mood_love"}
            ],
            [
                {"text": "🌙 Chill", "callback_data": "mood_chill"},
                {"text": "🔥 Hype", "callback_data": "mood_hype"}
            ],
            [
                {"text": "🖤 Dark", "callback_data": "mood_dark"},
                {"text": "⚡ Energetic", "callback_data": "mood_energetic"}
            ],
            [
                {"text": "🚗 Night Drive", "callback_data": "mood_night"},
                {"text": "🌌 Melodic", "callback_data": "mood_melodic"}
            ]
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
    # NORMAL MESSAGE
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
    # MOOD BUTTON
    # =========================

    callback = update.get("callback_query")

    if callback:

        callback_id = callback["id"]
        chat_id = callback["message"]["chat"]["id"]
        data = callback.get("data", "")

        moods = {
            "mood_sad": "😢 SAD",
            "mood_love": "❤️ LOVE",
            "mood_chill": "🌙 CHILL",
            "mood_hype": "🔥 HYPE",
            "mood_dark": "🖤 DARK",
            "mood_energetic": "⚡ ENERGETIC",
            "mood_night": "🚗 NIGHT DRIVE",
            "mood_melodic": "🌌 MELODIC"
        }

        if data in moods:

            mood_name = moods[data]

            answer_callback(
                callback_id,
                f"{mood_name} selected!"
            )

            send_message(
                chat_id,
                f"🎧 {mood_name}\n\n"
                "Searching for music...\n\n"
                "⏳ Music database ကို ချိတ်နေပါတယ်..."
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
