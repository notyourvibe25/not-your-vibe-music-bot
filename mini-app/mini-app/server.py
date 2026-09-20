import os
from flask import Flask, send_from_directory, request, Response, jsonify
import requests

app = Flask(__name__, static_folder=".", static_url_path="")

BOT_SERVER = os.getenv(
    "BOT_SERVER",
    "https://not-your-vibe-music-bot-5dkn.onrender.com"
).rstrip("/")

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 180


@app.get("/")
def home():
    return send_from_directory(".", "index.html")


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "NOT YOUR VIBE Mini App"
    })


def proxy_request(path):
    url = f"{BOT_SERVER}/{path.lstrip('/')}"

    headers = {}

    # Telegram authentication
    init_data = request.headers.get("X-Telegram-Init-Data")
    if init_data:
        headers["X-Telegram-Init-Data"] = init_data

    # Forward content type
    content_type = request.headers.get("Content-Type")
    if content_type:
        headers["Content-Type"] = content_type

    try:
        upstream = requests.request(
            method=request.method,
            url=url,
            params=request.args,
            headers=headers,
            data=request.get_data(),
            stream=True,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            allow_redirects=False,
        )

    except requests.RequestException as exc:
        return jsonify({
            "error": "Bot server unavailable",
            "detail": str(exc)[:200]
        }), 502

    excluded_headers = {
        "content-encoding",
        "content-length",
        "transfer-encoding",
        "connection",
    }

    response_headers = [
        (key, value)
        for key, value in upstream.headers.items()
        if key.lower() not in excluded_headers
    ]

    return Response(
        upstream.iter_content(
            chunk_size=64 * 1024
        ),
        status=upstream.status_code,
        headers=response_headers,
    )


# -----------------------------
# API
# -----------------------------

@app.route(
    "/api/<path:path>",
    methods=[
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    ],
)
def api_proxy(path):

    if request.method == "OPTIONS":
        return "", 204

    return proxy_request(f"api/{path}")


# -----------------------------
# Cover
# -----------------------------

@app.get("/cover/<path:path>")
def cover_proxy(path):
    return proxy_request(f"cover/{path}")


# -----------------------------
# Webhook
# -----------------------------

@app.route(
    "/webhook",
    methods=["GET", "POST"]
)
def webhook_proxy():
    return proxy_request("webhook")


# -----------------------------
# Static files
# -----------------------------

@app.get("/<path:path>")
def static_files(path):

    full_path = os.path.join(".", path)

    if os.path.isfile(full_path):
        return send_from_directory(".", path)

    return jsonify({
        "error": "Not found"
    }), 404


if __name__ == "__main__":

    port = int(
        os.getenv("PORT", "10000")
    )

    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True,
    )