import os
import requests
from flask import Flask, request

app = Flask(__name__)

BOT_TOKEN = os.environ.get("8529418251AAEKmfORfiQo5Ia9G8uTTZehm7kgpoarung")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")

TELEGRAM_API = f"https://api.telegram.org/bot{8529418251AAEKmfORfiQo5Ia9G8uTTZehm7kgpoarung}

def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        data["reply_markup"] = reply_markup

    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json=data,
        timeout=20
    )


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

    message = update.get("message")

    if message:
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        if text == "/start":
            keyboard = {
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
                        {"text": "🚗 Night Drive", "callback_data": "night_drive"},
                        {"text": "🌌 Melodic", "callback_data": "melodic"}
                    ]
                ]
            }

            send_message(
                chat_id,
                "🎧 NOT YOUR VIBE MUSIC\n\nHow are you feeling today?",
                keyboard
            )

    return "OK"


# Automatically connect Telegram to Render
if BOT_TOKEN and RENDER_URL:
    webhook_url = f"{RENDER_URL}/webhook"

    response = requests.post(
        f"{TELEGRAM_API}/setWebhook",
        json={"url": webhook_url},
        timeout=20
    )

    print("Webhook:", response.text)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
