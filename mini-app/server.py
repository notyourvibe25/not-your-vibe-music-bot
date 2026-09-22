import os

import requests
from flask import Flask, Response, jsonify, request, send_from_directory
from requests.adapters import HTTPAdapter


app = Flask(__name__, static_folder=".", static_url_path="")


# Main Bot Server
# Render မှာ BOT_SERVER environment variable ထည့်ထားရင်
# အဲဒီ value ကို အသုံးပြုမယ်။
BOT_SERVER = (
    os.getenv("BOT_SERVER")
    or "https://not-your-vibe-bot-server.onrender.com"
).rstrip("/")


CONNECT_TIMEOUT = 10
READ_TIMEOUT = 180

_UPSTREAM = requests.Session()
_UPSTREAM.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=16, max_retries=0))
_UPSTREAM.mount("http://", HTTPAdapter(pool_connections=2, pool_maxsize=8, max_retries=0))


@app.get("/")
def home():
    return send_from_directory(".", "index.html")


@app.get("/health")
def health():
    return jsonify(
        {
            "ok": True,
            "service": "NOT YOUR VIBE Mini App Server",
        }
    )


def proxy_request(path):
    """
    Forward Mini App requests to the Main Bot Server.

    The Mini App Server does not access PostgreSQL or Telethon directly.
    It only acts as a lightweight gateway/proxy.
    """
    url = f"{BOT_SERVER}/{path.lstrip('/')}"

    headers = {}

    # Telegram WebApp authentication data.
    init_data = request.headers.get("X-Telegram-Init-Data")
    if init_data:
        headers["X-Telegram-Init-Data"] = init_data

    content_type = request.headers.get("Content-Type")
    if content_type:
        headers["Content-Type"] = content_type

    # Media elements commonly send range and conditional requests. Forward
    # them so cached audio can resume/seek without restarting a full Telegram
    # download, which also prevents an avoidable pause between tracks.
    for header_name in (
        "Accept",
        "Range",
        "If-Range",
        "If-None-Match",
        "If-Modified-Since",
        "User-Agent",
    ):
        value = request.headers.get(header_name)
        if value:
            headers[header_name] = value

    # requests does not reliably decode Brotli responses, while this proxy
    # intentionally strips hop-by-hop/content-encoding headers. Force the bot
    # server to return plain bytes so API JSON is never delivered as opaque
    # compressed data to the browser. Audio range responses are not compressed.
    headers["Accept-Encoding"] = "identity"

    try:
        upstream = _UPSTREAM.request(
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
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Bot server unavailable",
                    "detail": str(exc)[:200],
                }
            ),
            502,
        )

    # Hop-by-hop headers should not be forwarded by the proxy.
    excluded_headers = {
        "content-encoding",
        "transfer-encoding",
        "connection",
    }

    response_headers = [
        (key, value)
        for key, value in upstream.headers.items()
        if key.lower() not in excluded_headers
    ]

    return Response(
        upstream.iter_content(chunk_size=64 * 1024),
        status=upstream.status_code,
        headers=response_headers,
    )


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
    """
    Proxy Mini App API requests to the Main Bot Server.

    Example:
        Mini App:
            /api/me

        becomes:
            https://not-your-vibe-music-bot.onrender.com/api/me
    """
    if request.method == "OPTIONS":
        return "", 204

    return proxy_request(f"api/{path}")


@app.get("/cover/<path:path>")
def cover_proxy(path):
    """
    Proxy cover-image requests to the Main Bot Server.
    """
    return proxy_request(f"cover/{path}")


@app.get("/share/<path:path>")
def share_proxy(path):
    """Proxy share pages and their Open Graph cover URLs to the Bot server."""
    return proxy_request(f"share/{path}")


@app.get("/<path:path>")
def static_files(path):
    """
    Serve static Mini App files.

    index.html is served by / above.
    Other existing files are served directly.
    """
    full_path = os.path.join(".", path)

    if os.path.isfile(full_path):
        return send_from_directory(".", path)

    return jsonify({"ok": False, "error": "Not found"}), 404


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))

    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True,
                    )
