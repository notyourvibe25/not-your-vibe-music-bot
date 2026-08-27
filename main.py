import os
import json
import time
import sqlite3
import logging
import threading
import requests

from flask import Flask, request

# OpenAI
from openai import OpenAI
from openai import RateLimitError, APIError

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger("not-your-vibe-bot")


# =========================================================
# APP
# =========================================================

app = Flask(__name__)


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Example:
# @notyourvibemp3collection
SOURCE_CHANNEL = os.getenv(
    "SOURCE_CHANNEL",
    "@notyourvibemp3collection"
)

ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

# Render gives this automatically
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

# Database
DB_FILE = "music.db"


# =========================================================
# SETTINGS
# =========================================================

OPENAI_MODEL = "gpt-5-mini"

# AI request delay
AI_DELAY = 1.0

# Number of tracks processed per scan batch
SCAN_BATCH_SIZE = 20


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
# TELEGRAM API
# =========================================================

if not BOT_TOKEN:
    logger.warning("BOT_TOKEN is missing")

TELEGRAM_API = (
    f"https://api.telegram.org/bot{BOT_TOKEN}"
    if BOT_TOKEN
    else ""
)


def telegram_request(method, data=None):
    """
    Safe Telegram API request.
    """

    if not TELEGRAM_API:
        return None

    try:
        response = requests.post(
            f"{TELEGRAM_API}/{method}",
            data=data or {},
            timeout=30
        )

        result = response.json()

        if not result.get("ok"):
            logger.error(
                "Telegram API error: %s",
                result
            )

        return result

    except Exception:
        logger.exception(
            "Telegram request failed: %s",
            method
        )
        return None


def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)

    return telegram_request(
        "sendMessage",
        data
    )


def copy_channel_message(
    chat_id,
    from_chat_id,
    message_id
):
    """
    Copy an existing song post from the source channel.
    """

    return telegram_request(
        "copyMessage",
        {
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "message_id": message_id
        }
    )


# =========================================================
# DATABASE
# =========================================================

db_lock = threading.Lock()


def get_db():
    conn = sqlite3.connect(
        DB_FILE,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    with db_lock:

        conn = get_db()

        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                message_id INTEGER UNIQUE NOT NULL,

                title TEXT DEFAULT '',

                caption TEXT DEFAULT '',

                moods TEXT DEFAULT '[]',

                ai_status TEXT DEFAULT 'pending',

                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,

                username TEXT DEFAULT '',

                first_name TEXT DEFAULT '',

                last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.commit()
        conn.close()

    logger.info("Database initialized")


# =========================================================
# USER DATABASE
# =========================================================

def save_user(user):

    if not user:
        return

    user_id = user.get("id")

    if not user_id:
        return

    username = user.get("username") or ""
    first_name = user.get("first_name") or ""

    with db_lock:

        conn = get_db()

        conn.execute(
            """
            INSERT INTO users (
                user_id,
                username,
                first_name
            )
            VALUES (?, ?, ?)

            ON CONFLICT(user_id)
            DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_seen = CURRENT_TIMESTAMP
            """,
            (
                user_id,
                username,
                first_name
            )
        )

        conn.commit()
        conn.close()


# =========================================================
# TRACK DATABASE
# =========================================================

def save_track(
    message_id,
    title="",
    caption=""
):

    with db_lock:

        conn = get_db()

        conn.execute(
            """
            INSERT INTO tracks (
                message_id,
                title,
                caption,
                moods,
                ai_status
            )
            VALUES (?, ?, ?, '[]', 'pending')

            ON CONFLICT(message_id)
            DO UPDATE SET
                title = excluded.title,
                caption = excluded.caption,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                message_id,
                title,
                caption
            )
        )

        conn.commit()
        conn.close()


def get_track(message_id):

    with db_lock:

        conn = get_db()

        row = conn.execute(
            """
            SELECT *
            FROM tracks
            WHERE message_id = ?
            """,
            (message_id,)
        ).fetchone()

        conn.close()

    return row


def update_track_moods(
    message_id,
    moods,
    status="classified"
):

    with db_lock:

        conn = get_db()

        conn.execute(
            """
            UPDATE tracks
            SET
                moods = ?,
                ai_status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE message_id = ?
            """,
            (
                json.dumps(moods),
                status,
                message_id
            )
        )

        conn.commit()
        conn.close()


def get_all_tracks():

    with db_lock:

        conn = get_db()

        rows = conn.execute(
            """
            SELECT *
            FROM tracks
            ORDER BY id ASC
            """
        ).fetchall()

        conn.close()

    return rows


def get_pending_tracks():

    with db_lock:

        conn = get_db()

        rows = conn.execute(
            """
            SELECT *
            FROM tracks
            WHERE ai_status != 'classified'
            ORDER BY id ASC
            """
        ).fetchall()

        conn.close()

    return rows


# =========================================================
# OPENAI
# =========================================================

openai_client = None

if OPENAI_API_KEY:

    try:

        openai_client = OpenAI(
            api_key=OPENAI_API_KEY
        )

        logger.info(
            "OpenAI client initialized"
        )

    except Exception:

        logger.exception(
            "OpenAI initialization failed"
        )

else:

    logger.warning(
        "OPENAI_API_KEY not found. "
        "AI classification disabled."
    )


# =========================================================
# AI CLASSIFIER
# =========================================================

def classify_music(title, caption):

    """
    AI classifies a song into one or more
    predefined moods.

    IMPORTANT:
    If OpenAI quota is exhausted,
    this function DOES NOT crash the bot.
    """

    if not openai_client:

        logger.warning(
            "AI unavailable: OPENAI_API_KEY missing"
        )

        return []


    text = f"""
Song title:
{title}

Caption:
{caption}
"""


    instructions = f"""
You are the music mood classifier for
NOT YOUR VIBE MUSIC.

Classify the track using ONLY these moods:

{", ".join(MOODS)}

Rules:

1. Return ONLY valid JSON.
2. JSON must contain a key named "moods".
3. "moods" must be an array.
4. Use one or more moods.
5. Never create new mood names.
6. Do not explain anything.
7. Judge based on title, artist, genre,
   lyrics/caption information and overall
   musical feeling when available.

Example:

{{
  "moods": ["melodic", "love"]
}}
"""


    try:

        response = openai_client.responses.create(
            model=OPENAI_MODEL,

            instructions=instructions,

            input=text
        )

        raw = response.output_text.strip()

        logger.info(
            "AI response: %s",
            raw
        )

        data = json.loads(raw)

        moods = data.get("moods", [])

        if not isinstance(moods, list):
            return []

        valid_moods = []

        for mood in moods:

            mood = str(mood).lower().strip()

            if mood in MOODS:
                valid_moods.append(mood)

        # Remove duplicates
        valid_moods = list(
            dict.fromkeys(valid_moods)
        )

        return valid_moods

    except RateLimitError:

        logger.error(
            "OpenAI quota exceeded. "
            "AI classification skipped."
        )

        return []

    except APIError:

        logger.exception(
            "OpenAI API error"
        )

        return []

    except json.JSONDecodeError:

        logger.error(
            "AI returned invalid JSON"
        )

        return []

    except Exception:

        logger.exception(
            "AI classification failed"
        )

        return []


# =========================================================
# CLASSIFY ONE TRACK
# =========================================================

def classify_track(track):

    message_id = track["message_id"]

    title = track["title"] or ""

    caption = track["caption"] or ""

    logger.info(
        "AI scanning message_id=%s title=%s",
        message_id,
        title
    )

    moods = classify_music(
        title,
        caption
    )

    if moods:

        update_track_moods(
            message_id,
            moods,
            "classified"
        )

        logger.info(
            "Classified %s -> %s",
            message_id,
            moods
        )

    else:

        # Important:
        # Don't mark it permanently classified
        # when AI failed.
        update_track_moods(
            message_id,
            [],
            "pending"
        )

        logger.warning(
            "Track %s remains pending",
            message_id
        )

    time.sleep(AI_DELAY)

    return moods


# =========================================================
# FULL RESCAN
# =========================================================

rescan_lock = threading.Lock()

rescan_running = False


def full_rescan():

    global rescan_running

    if rescan_running:

        logger.warning(
            "Full rescan already running"
        )

        return

    with rescan_lock:

        if rescan_running:
            return

        rescan_running = True

    try:

        logger.info(
            "===================================="
        )

        logger.info(
            "FULL AI RESCAN STARTED"
        )

        logger.info(
            "===================================="
        )

        tracks = get_all_tracks()

        total = len(tracks)

        logger.info(
            "Total tracks in database: %s",
            total
        )

        success = 0
        failed = 0

        for index, track in enumerate(tracks, 1):

            logger.info(
                "Rescan %s/%s",
                index,
                total
            )

            moods = classify_track(track)

            if moods:
                success += 1
            else:
                failed += 1

        logger.info(
            "===================================="
        )

        logger.info(
            "FULL RESCAN FINISHED"
        )

        logger.info(
            "Success: %s | Pending/Failed: %s",
            success,
            failed
        )

        logger.info(
            "===================================="
        )

    except Exception:

        logger.exception(
            "Full rescan crashed"
        )

    finally:

        rescan_running = False


# =========================================================
# START FULL RESCAN IN BACKGROUND
# =========================================================

def start_full_rescan():

    thread = threading.Thread(
        target=full_rescan,
        daemon=True
    )

    thread.start()

    logger.info(
        "Full rescan thread started"
    )


# =========================================================
# ADMIN COMMAND
# =========================================================

def is_admin(user_id):

    if not ADMIN_USER_ID:
        return False

    try:
        return int(user_id) == int(
            ADMIN_USER_ID
        )

    except Exception:
        return False


def handle_admin_command(
    chat_id,
    user_id,
    text
):

    if not is_admin(user_id):
        return False

    command = text.strip().lower()

    if command == "/rescan":

        if rescan_running:

            send_message(
                chat_id,
                "⏳ Full AI Rescan is already running."
            )

        else:

            send_message(
                chat_id,
                "🔄 Full AI Rescan started.\n\n"
                "အဟောင်း Track တွေကို AI နဲ့ "
                "ပြန်စစ်နေပါတယ်။"
            )

            start_full_rescan()

        return True


    if command == "/rescan_status":

        tracks = get_all_tracks()

        classified = 0
        pending = 0

        for track in tracks:

            if track["ai_status"] == "classified":
                classified += 1
            else:
                pending += 1

        status = (
            "🔎 AI RESCAN STATUS\n\n"
            f"🎵 Total: {len(tracks)}\n"
            f"✅ Classified: {classified}\n"
            f"⏳ Pending: {pending}\n\n"
            f"🔄 Running: "
            f"{'YES' if rescan_running else 'NO'}"
        )

        send_message(
            chat_id,
            status
        )

        return True


    if command == "/stats":

        tracks = get_all_tracks()

        users = get_user_count()

        classified = sum(
            1
            for track in tracks
            if track["ai_status"] == "classified"
        )

        send_message(
            chat_id,
            "📊 NOT YOUR VIBE BOT\n\n"
            f"👤 Users: {users}\n"
            f"🎵 Tracks: {len(tracks)}\n"
            f"🤖 AI Classified: {classified}\n"
            f"⏳ Pending: "
            f"{len(tracks) - classified}"
        )

        return True

    return False


def get_user_count():

    with db_lock:

        conn = get_db()

        row = conn.execute(
            "SELECT COUNT(*) AS count FROM users"
        ).fetchone()

        conn.close()

    return row["count"]


# =========================================================
# MOOD KEYBOARD
# =========================================================

def mood_keyboard():

    return {
        "inline_keyboard": [
            [
                {
                    "text": MOOD_NAMES["sad"],
                    "callback_data": "mood:sad"
                },
                {
                    "text": MOOD_NAMES["love"],
                    "callback_data": "mood:love"
                }
            ],
            [
                {
                    "text": MOOD_NAMES["chill"],
                    "callback_data": "mood:chill"
                },
                {
                    "text": MOOD_NAMES["hype"],
                    "callback_data": "mood:hype"
                }
            ],
            [
                {
                    "text": MOOD_NAMES["dark"],
                    "callback_data": "mood:dark"
                },
                {
                    "text": MOOD_NAMES["energetic"],
                    "callback_data": "mood:energetic"
                }
            ],
            [
                {
                    "text": MOOD_NAMES["night"],
                    "callback_data": "mood:night"
                },
                {
                    "text": MOOD_NAMES["melodic"],
                    "callback_data": "mood:melodic"
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
                    "text": "⏭ NEXT",
                    "callback_data": "next"
                },
                {
                    "text": "🔄 CHANGE MOOD",
                    "callback_data": "change_mood"
                }
            ]
        ]
    }


# =========================================================
# GET MUSIC BY MOOD
# =========================================================

def get_tracks_by_mood(mood):

    if mood not in MOODS:
        return []

    rows = get_all_tracks()

    results = []

    for row in rows:

        try:
            moods = json.loads(
                row["moods"] or "[]"
            )

        except Exception:
            moods = []

        if mood in moods:

            results.append(row)

    return results


# =========================================================
# SEND RANDOM MUSIC
# =========================================================

import random


def send_random_music(
    chat_id,
    mood
):

    tracks = get_tracks_by_mood(
        mood
    )

    if not tracks:

        send_message(
            chat_id,
            f"😔 {MOOD_NAMES.get(mood, mood)}\n\n"
            "ဒီ Mood အတွက် Track မတွေ့သေးပါဘူး။"
        )

        return

    track = random.choice(tracks)

    result = copy_channel_message(
        chat_id=chat_id,
        from_chat_id=SOURCE_CHANNEL,
        message_id=track["message_id"]
    )

    if not result or not result.get("ok"):

        send_message(
            chat_id,
            "⚠️ ဒီ Track ကို ပို့လို့မရသေးပါဘူး။"
        )

        return

    send_message(
        chat_id,
        f"🎵 {MOOD_NAMES.get(mood, mood)}",
        music_buttons()
    )


# =========================================================
# CALLBACK QUERY
# =========================================================

def answer_callback(callback_id):

    telegram_request(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id
        }
    )


def handle_callback(callback):

    callback_id = callback.get("id")

    data = callback.get("data", "")

    message = callback.get("message") or {}

    chat = message.get("chat") or {}

    chat_id = chat.get("id")

    user = callback.get("from") or {}

    user_id = user.get("id")

    answer_callback(callback_id)

    if data.startswith("mood:"):

        mood = data.split(
            ":",
            1
        )[1]

        if mood not in MOODS:
            return

        send_random_music(
            chat_id,
            mood
        )

        return


    if data == "change_mood":

        send_message(
            chat_id,
            "🎧 Choose your mood:",
            mood_keyboard()
        )

        return


    if data == "next":

        send_message(
            chat_id,
            "🎧 Choose a mood first:",
            mood_keyboard()
        )

        return


# =========================================================
# TEXT MESSAGE HANDLER
# =========================================================

def handle_message(message):

    chat = message.get("chat") or {}

    chat_id = chat.get("id")

    user = message.get("from") or {}

    user_id = user.get("id")

    text = message.get("text") or ""

    save_user(user)

    # Admin commands
    if text.startswith("/"):

        handled = handle_admin_command(
            chat_id,
            user_id,
            text
        )

        if handled:
            return

    if text == "/start":

        send_message(
            chat_id,
            "🎧 Welcome to NOT YOUR VIBE MUSIC BOT\n\n"
            "Choose your mood 👇",
            mood_keyboard()
        )

        return

    if text == "/mood":

        send_message(
            chat_id,
            "🎧 Choose your mood:",
            mood_keyboard()
        )

        return


# =========================================================
# CHANNEL POST HANDLER
# =========================================================

def extract_channel_title(message):

    caption = (
        message.get("caption")
        or message.get("text")
        or ""
    )

    # Use first line as title
    lines = [
        line.strip()
        for line in caption.splitlines()
        if line.strip()
    ]

    if lines:
        return lines[0]

    return "Unknown Track"


def handle_channel_post(message):

    chat = message.get("chat") or {}

    chat_username = chat.get("username")

    chat_id = chat.get("id")

    message_id = message.get("message_id")

    if not message_id:
        return

    # Only process configured source channel
    source_username = SOURCE_CHANNEL.lstrip("@").lower()

    if chat_username:

        if chat_username.lower() != source_username:

            return

    caption = (
        message.get("caption")
        or message.get("text")
        or ""
    )

    title = extract_channel_title(
        message
    )

    save_track(
        message_id=message_id,
        title=title,
        caption=caption
    )

    logger.info(
        "New channel track added: %s | %s",
        message_id,
        title
    )

    # AI classification in background
    thread = threading.Thread(
        target=classify_new_track,
        args=(message_id,),
        daemon=True
    )

    thread.start()


# =========================================================
# NEW TRACK AI CLASSIFICATION
# =========================================================

def classify_new_track(message_id):

    track = get_track(
        message_id
    )

    if not track:
        return

    classify_track(
        track
    )


# =========================================================
# WEBHOOK
# =========================================================

@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    try:

        update = request.get_json(
            silent=True
        ) or {}

        logger.info(
            "Telegram update received"
        )

        # Normal message
        message = update.get("message")

        if message:

            handle_message(
                message
            )

        # Callback
        callback = update.get(
            "callback_query"
        )

        if callback:

            handle_callback(
                callback
            )

        # Channel post
        channel_post = update.get(
            "channel_post"
        )

        if channel_post:

            handle_channel_post(
                channel_post
            )

        return {
            "ok": True
        }

    except Exception:

        logger.exception(
            "Webhook error"
        )

        return {
            "ok": False
        }, 200


# =========================================================
# HEALTH CHECK
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return (
        "NOT YOUR VIBE MUSIC BOT is running."
    )


@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return {
        "status": "ok",
        "ai": bool(openai_client),
        "rescan_running": rescan_running
    }


# =========================================================
# SET WEBHOOK
# =========================================================

def set_webhook():

    if not RENDER_URL:

        logger.warning(
            "RENDER_EXTERNAL_URL missing"
        )

        return

    webhook_url = (
        RENDER_URL.rstrip("/")
        + "/webhook"
    )

    result = telegram_request(
        "setWebhook",
        {
            "url": webhook_url
        }
    )

    logger.info(
        "Webhook result: %s",
        result
    )


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    logger.info(
        "Starting NOT YOUR VIBE MUSIC BOT"
    )

    init_db()

    # Set webhook after Flask starts
    threading.Timer(
        3.0,
        set_webhook
    ).start()

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
