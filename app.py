import os
import time
import random
import sqlite3
import logging
import threading
from contextlib import contextmanager

import requests
from flask import Flask, request, jsonify


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("not-your-vibe")


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# ENVIRONMENT
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

# Render persistent disk မှာထားပါ
DB_PATH = os.getenv("DB_PATH", "/data/notyourvibe.db")

# Render က automatically ထည့်ပေးနိုင်ပါတယ်
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

WEBHOOK_SECRET = os.getenv(
    "WEBHOOK_SECRET",
    "change-this-secret"
)


# ============================================================
# VALIDATION
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not ADMIN_USER_ID:
    raise RuntimeError("ADMIN_USER_ID is missing")

try:
    ADMIN_USER_ID = int(ADMIN_USER_ID)
except ValueError:
    raise RuntimeError("ADMIN_USER_ID must be a Telegram numeric user ID")


# ============================================================
# TELEGRAM API
# ============================================================

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ============================================================
# MOODS
# ============================================================

MOODS = {
    "sad": {
        "name": "😢 SAD",
        "channel": "@sadmooddatabase",
        "channel_key": "@sadmooddatabase",
    },

    "love": {
        "name": "❤️ LOVE",
        "channel": "@lovemooddatabase",
        "channel_key": "@lovemooddatabase",
    },

    "chill": {
        "name": "🌙 CHILL",
        "channel": "@chillmooddatabase",
        "channel_key": "@chillmooddatabase",
    },

    "hype": {
        "name": "🔥 HYPE",
        "channel": -1004427220481,
        "channel_key": "-1004427220481",
    },

    "dark": {
        "name": "🖤 DARK",
        "channel": "@darkmooddatabase",
        "channel_key": "@darkmooddatabase",
    },

    "energetic": {
        "name": "⚡ ENERGETIC",
        "channel": "@energeticmooddatabase",
        "channel_key": "@energeticmooddatabase",
    },

    "night": {
        "name": "🚗 NIGHT DRIVE",
        "channel": "@nightdrivemooddatabase",
        "channel_key": "@nightdrivemooddatabase",
    },

    "melodic": {
        "name": "🌌 MELODIC",
        "channel": -1004446996297,
        "channel_key": "-1004446996297",
    },
}


# channel_id / username -> mood
CHANNEL_TO_MOOD = {
    str(info["channel"]): mood
    for mood, info in MOODS.items()
}


# ============================================================
# DATABASE
# ============================================================

def ensure_db_directory():
    directory = os.path.dirname(DB_PATH)

    if directory:
        os.makedirs(directory, exist_ok=True)


ensure_db_directory()


@contextmanager
def db_connection():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")

        yield conn

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def init_database():

    with db_connection() as conn:

        # ====================================================
        # TRACKS
        # ====================================================

        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                mood TEXT NOT NULL,

                channel_key TEXT NOT NULL,

                message_id INTEGER NOT NULL,

                created_at INTEGER NOT NULL,

                UNIQUE(channel_key, message_id)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tracks_mood
            ON tracks(mood)
        """)

        # ====================================================
        # USER STATE
        # ====================================================

        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_state (
                user_id INTEGER PRIMARY KEY,

                selected_mood TEXT,

                updated_at INTEGER NOT NULL
            )
        """)

        # ====================================================
        # USER HISTORY
        # ====================================================

        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_history (
                user_id INTEGER NOT NULL,

                mood TEXT NOT NULL,

                track_id INTEGER NOT NULL,

                used_at INTEGER NOT NULL,

                PRIMARY KEY(user_id, mood, track_id),

                FOREIGN KEY(track_id)
                    REFERENCES tracks(id)
                    ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_user_mood
            ON user_history(user_id, mood)
        """)

        # ====================================================
        # USERS
        # ====================================================

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,

                first_seen INTEGER NOT NULL,

                last_seen INTEGER NOT NULL,

                username TEXT,

                first_name TEXT
            )
        """)

    logger.info("Database initialized")


init_database()


# ============================================================
# USER LOCKS
# ============================================================

_user_locks = {}
_user_locks_guard = threading.Lock()


def get_user_lock(user_id):

    with _user_locks_guard:

        if user_id not in _user_locks:
            _user_locks[user_id] = threading.Lock()

        return _user_locks[user_id]


# ============================================================
# TELEGRAM REQUEST
# ============================================================

def telegram(method, payload=None, timeout=20):

    url = f"{TELEGRAM_API}/{method}"

    try:

        response = requests.post(
            url,
            json=payload or {},
            timeout=timeout
        )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok"):
            logger.error(
                "Telegram API error: %s",
                data
            )

        return data

    except requests.RequestException as exc:

        logger.exception(
            "Telegram request failed: %s",
            exc
        )

        return {
            "ok": False,
            "description": str(exc)
        }


# ============================================================
# SEND MESSAGE
# ============================================================

def send_message(
    chat_id,
    text,
    reply_markup=None
):

    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }

    if reply_markup:
        payload["reply_markup"] = reply_markup

    return telegram(
        "sendMessage",
        payload
    )


# ============================================================
# COPY CHANNEL MESSAGE
# ============================================================

def copy_channel_message(
    chat_id,
    from_chat_id,
    message_id
):

    return telegram(
        "copyMessage",
        {
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "message_id": message_id,
        }
    )


# ============================================================
# DELETE MESSAGE
# ============================================================

def delete_message(chat_id, message_id):

    return telegram(
        "deleteMessage",
        {
            "chat_id": chat_id,
            "message_id": message_id
        }
    )


# ============================================================
# KEYBOARDS
# ============================================================

def mood_keyboard():

    return {
        "inline_keyboard": [
            [
                {
                    "text": "😢 SAD",
                    "callback_data": "mood:sad"
                },
                {
                    "text": "❤️ LOVE",
                    "callback_data": "mood:love"
                }
            ],

            [
                {
                    "text": "🌙 CHILL",
                    "callback_data": "mood:chill"
                },
                {
                    "text": "🔥 HYPE",
                    "callback_data": "mood:hype"
                }
            ],

            [
                {
                    "text": "🖤 DARK",
                    "callback_data": "mood:dark"
                },
                {
                    "text": "⚡ ENERGETIC",
                    "callback_data": "mood:energetic"
                }
            ],

            [
                {
                    "text": "🚗 NIGHT DRIVE",
                    "callback_data": "mood:night"
                },
                {
                    "text": "🌌 MELODIC",
                    "callback_data": "mood:melodic"
                }
            ]
        ]
    }


def music_keyboard(mood):

    return {
        "inline_keyboard": [

            [
                {
                    "text": "⏭ NEXT",
                    "callback_data": f"next:{mood}"
                }
            ],

            [
                {
                    "text": "🎵 CHANGE MOOD",
                    "callback_data": "change_mood"
                }
            ]
        ]
    }


# ============================================================
# USER RECORD
# ============================================================

def register_user(user):

    if not user:
        return

    user_id = user.get("id")

    if not user_id:
        return

    now = int(time.time())

    username = user.get("username")
    first_name = user.get("first_name")

    with db_connection() as conn:

        conn.execute("""
            INSERT INTO users (
                user_id,
                first_seen,
                last_seen,
                username,
                first_name
            )

            VALUES (?, ?, ?, ?, ?)

            ON CONFLICT(user_id)
            DO UPDATE SET
                last_seen = excluded.last_seen,
                username = excluded.username,
                first_name = excluded.first_name
        """, (
            user_id,
            now,
            now,
            username,
            first_name
        ))


# ============================================================
# SAVE USER MOOD
# ============================================================

def set_user_mood(user_id, mood):

    if mood not in MOODS:
        return False

    now = int(time.time())

    with db_connection() as conn:

        conn.execute("""
            INSERT INTO user_state (
                user_id,
                selected_mood,
                updated_at
            )

            VALUES (?, ?, ?)

            ON CONFLICT(user_id)
            DO UPDATE SET
                selected_mood = excluded.selected_mood,
                updated_at = excluded.updated_at
        """, (
            user_id,
            mood,
            now
        ))

    return True


# ============================================================
# GET USER MOOD
# ============================================================

def get_user_mood(user_id):

    with db_connection() as conn:

        row = conn.execute("""
            SELECT selected_mood
            FROM user_state
            WHERE user_id = ?
        """, (
            user_id,
        )).fetchone()

    if not row:
        return None

    return row["selected_mood"]


# ============================================================
# ADD TRACK
# ============================================================

def add_track(
    mood,
    channel_key,
    message_id
):

    if mood not in MOODS:
        return False

    now = int(time.time())

    with db_connection() as conn:

        cursor = conn.execute("""
            INSERT OR IGNORE INTO tracks (
                mood,
                channel_key,
                message_id,
                created_at
            )

            VALUES (?, ?, ?, ?)
        """, (
            mood,
            channel_key,
            message_id,
            now
        ))

        inserted = cursor.rowcount == 1

    if inserted:

        logger.info(
            "New track added | mood=%s | message_id=%s",
            mood,
            message_id
        )

    return inserted


# ============================================================
# GET RANDOM UNUSED TRACK
# ============================================================

def claim_random_track(
    user_id,
    mood
):

    if mood not in MOODS:
        return None

    lock = get_user_lock(user_id)

    with lock:

        # ====================================================
        # TRY MANY TIMES
        # ====================================================

        for _ in range(20):

            with db_connection() as conn:

                # ------------------------------------------------
                # Random unused track
                # ------------------------------------------------

                row = conn.execute("""
                    SELECT
                        t.id,
                        t.channel_key,
                        t.message_id

                    FROM tracks t

                    WHERE t.mood = ?

                    AND NOT EXISTS (
                        SELECT 1
                        FROM user_history h
                        WHERE h.user_id = ?
                        AND h.mood = ?
                        AND h.track_id = t.id
                    )

                    ORDER BY RANDOM()

                    LIMIT 1
                """, (
                    mood,
                    user_id,
                    mood
                )).fetchone()

                # ------------------------------------------------
                # No unused track
                # ------------------------------------------------

                if row is None:

                    count_row = conn.execute("""
                        SELECT COUNT(*) AS total
                        FROM tracks
                        WHERE mood = ?
                    """, (
                        mood,
                    )).fetchone()

                    total = count_row["total"]

                    if total == 0:
                        return None

                    # Reset this user's history ONLY for this mood
                    conn.execute("""
                        DELETE FROM user_history
                        WHERE user_id = ?
                        AND mood = ?
                    """, (
                        user_id,
                        mood
                    ))

                    # Try again after reset
                    continue

                # ------------------------------------------------
                # Claim track
                # ------------------------------------------------

                now = int(time.time())

                try:

                    conn.execute("""
                        INSERT INTO user_history (
                            user_id,
                            mood,
                            track_id,
                            used_at
                        )

                        VALUES (?, ?, ?, ?)
                    """, (
                        user_id,
                        mood,
                        row["id"],
                        now
                    ))

                    return {
                        "id": row["id"],
                        "channel_key": row["channel_key"],
                        "message_id": row["message_id"]
                    }

                except sqlite3.IntegrityError:

                    # Another request claimed it.
                    continue

    return None


# ============================================================
# SEND RANDOM TRACK
# ============================================================

def send_random_track(
    user_id,
    chat_id,
    mood
):

    track = claim_random_track(
        user_id,
        mood
    )

    # ========================================================
    # EMPTY MOOD
    # ========================================================

    if track is None:

        total = count_tracks(mood)

        if total == 0:

            send_message(
                chat_id,
                (
                    f"{MOODS[mood]['name']}\n\n"
                    "ဒီ mood ထဲမှာ သီချင်းမရှိသေးပါဘူး။"
                ),
                mood_keyboard()
            )

            return

        send_message(
            chat_id,
            "သီချင်းရွေးရာမှာ error ဖြစ်သွားပါတယ်။ နောက်တစ်ကြိမ်ကြိုးစားပါ။"
        )

        return

    # ========================================================
    # COPY SONG
    # ========================================================

    result = copy_channel_message(
        chat_id=chat_id,
        from_chat_id=track["channel_key"],
        message_id=track["message_id"]
    )

    if not result.get("ok"):

        logger.error(
            "copyMessage failed | mood=%s | channel=%s | message=%s | result=%s",
            mood,
            track["channel_key"],
            track["message_id"],
            result
        )

        # Copy failed ဖြစ်ရင် history ထဲက ပြန်ဖျက်
        remove_history_claim(
            user_id,
            mood,
            track["id"]
        )

        send_message(
            chat_id,
            (
                "ဒီ track ကို ဖွင့်လို့မရပါဘူး။\n"
                "နောက် track တစ်ပုဒ်ရွေးပေးနေပါတယ်..."
            )
        )

        # Try another
        time.sleep(0.2)

        send_random_track(
            user_id,
            chat_id,
            mood
        )

        return

    # ========================================================
    # CONTROLS
    # ========================================================

    send_message(
        chat_id,
        f"{MOODS[mood]['name']}\n\n⏭ Next နှိပ်ရင် ဒီ mood ထဲက နောက်တစ်ပုဒ်ရွေးပေးမယ်။",
        music_keyboard(mood)
    )


# ============================================================
# REMOVE HISTORY CLAIM
# ============================================================

def remove_history_claim(
    user_id,
    mood,
    track_id
):

    with db_connection() as conn:

        conn.execute("""
            DELETE FROM user_history

            WHERE user_id = ?
            AND mood = ?
            AND track_id = ?
        """, (
            user_id,
            mood,
            track_id
        ))


# ============================================================
# COUNT TRACKS
# ============================================================

def count_tracks(mood=None):

    with db_connection() as conn:

        if mood:

            row = conn.execute("""
                SELECT COUNT(*) AS total
                FROM tracks
                WHERE mood = ?
            """, (
                mood,
            )).fetchone()

        else:

            row = conn.execute("""
                SELECT COUNT(*) AS total
                FROM tracks
            """).fetchone()

    return row["total"]


# ============================================================
# COUNT USERS
# ============================================================

def count_users():

    with db_connection() as conn:

        row = conn.execute("""
            SELECT COUNT(*) AS total
            FROM users
        """).fetchone()

    return row["total"]


# ============================================================
# STATS
# ============================================================

def stats_text():

    lines = [
        "📊 NOT YOUR VIBE MUSIC BOT",
        "",
        f"👥 Users: {count_users()}",
        "",
        "🎵 Tracks:"
    ]

    for mood, info in MOODS.items():

        lines.append(
            f"{info['name']}: {count_tracks(mood)}"
        )

    return "\n".join(lines)


# ============================================================
# ADMIN CHECK
# ============================================================

def is_admin(user_id):

    return int(user_id) == ADMIN_USER_ID


# ============================================================
# START COMMAND
# ============================================================

def handle_start(message):

    user = message.get("from", {})
    chat = message.get("chat", {})

    user_id = user.get("id")
    chat_id = chat.get("id")

    register_user(user)

    send_message(
        chat_id,
        (
            "🎵 NOT YOUR VIBE MUSIC BOT\n\n"
            "မင်းရဲ့ mood ကိုရွေးပါ။\n"
            "ရွေးထားတဲ့ mood ထဲကပဲ သီချင်းတွေကို random ရွေးပေးမယ်။"
        ),
        mood_keyboard()
    )


# ============================================================
# CALLBACK QUERY
# ============================================================

def handle_callback(callback):

    callback_id = callback.get("id")

    user = callback.get("from", {})
    message = callback.get("message", {})

    user_id = user.get("id")
    chat_id = message.get("chat", {}).get("id")

    data = callback.get("data", "")

    register_user(user)

    # Always answer callback quickly
    telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id
        }
    )

    # ========================================================
    # MOOD
    # ========================================================

    if data.startswith("mood:"):

        mood = data.split(":", 1)[1]

        if mood not in MOODS:
            return

        set_user_mood(
            user_id,
            mood
        )

        send_message(
            chat_id,
            (
                f"{MOODS[mood]['name']} selected.\n\n"
                "သီချင်းရွေးနေပါတယ်..."
            )
        )

        send_random_track(
            user_id,
            chat_id,
            mood
        )

        return

    # ========================================================
    # NEXT
    # ========================================================

    if data.startswith("next:"):

        mood_from_button = data.split(":", 1)[1]

        selected_mood = get_user_mood(user_id)

        # User state ကို အဓိကထား
        mood = selected_mood or mood_from_button

        if mood not in MOODS:
            send_message(
                chat_id,
                "အရင်ဆုံး mood တစ်ခုရွေးပါ။",
                mood_keyboard()
            )
            return

        send_random_track(
            user_id,
            chat_id,
            mood
        )

        return

    # ========================================================
    # CHANGE MOOD
    # ========================================================

    if data == "change_mood":

        send_message(
            chat_id,
            "🎧 Mood ပြောင်းရွေးပါ။",
            mood_keyboard()
        )

        return


# ============================================================
# TEXT COMMANDS
# ============================================================

def handle_text(message):

    user = message.get("from", {})
    chat = message.get("chat", {})

    user_id = user.get("id")
    chat_id = chat.get("id")

    text = message.get("text", "").strip()

    register_user(user)

    # ========================================================
    # START
    # ========================================================

    if text.startswith("/start"):

        handle_start(message)

        return

    # ========================================================
    # MOOD
    # ========================================================

    if text == "/mood":

        send_message(
            chat_id,
            "🎧 Mood ရွေးပါ။",
            mood_keyboard()
        )

        return

    # ========================================================
    # STATS
    # ========================================================

    if text == "/stats":

        if not is_admin(user_id):
            return

        send_message(
            chat_id,
            stats_text()
        )

        return

    # ========================================================
    # USERS
    # ========================================================

    if text == "/users":

        if not is_admin(user_id):
            return

        send_message(
            chat_id,
            f"👥 Total users: {count_users()}"
        )

        return

    # ========================================================
    # HELP
    # ========================================================

    if text == "/help":

        send_message(
            chat_id,
            (
                "🎵 Commands\n\n"
                "/start - Start bot\n"
                "/mood - Choose mood\n"
                "/help - Help"
            )
        )

        return


# ============================================================
# CHANNEL POST
# ============================================================

def handle_channel_post(channel_post):

    chat = channel_post.get("chat", {})

    message_id = channel_post.get("message_id")

    if not message_id:
        return

    channel_id = chat.get("id")
    channel_username = chat.get("username")

    # ========================================================
    # Try numeric ID first
    # ========================================================

    mood = CHANNEL_TO_MOOD.get(
        str(channel_id)
    )

    # ========================================================
    # Try @username
    # ========================================================

    if not mood and channel_username:

        mood = CHANNEL_TO_MOOD.get(
            f"@{channel_username}"
        )

    if not mood:

        logger.info(
            "Ignoring channel post from unknown channel: %s",
            channel_id
        )

        return

    channel_key = MOODS[mood]["channel_key"]

    add_track(
        mood=mood,
        channel_key=channel_key,
        message_id=message_id
    )


# ============================================================
# WEBHOOK PROCESSOR
# ============================================================

def process_update(update):

    try:

        # ====================================================
        # MESSAGE
        # ====================================================

        if "message" in update:

            handle_text(
                update["message"]
            )

        # ====================================================
        # CALLBACK
        # ====================================================

        elif "callback_query" in update:

            handle_callback(
                update["callback_query"]
            )

        # ====================================================
        # CHANNEL POST
        # ====================================================

        elif "channel_post" in update:

            handle_channel_post(
                update["channel_post"]
            )

        else:

            logger.info(
                "Unhandled update type: %s",
                list(update.keys())
            )

    except Exception:

        logger.exception(
            "Update processing failed"
        )


# ============================================================
# WEBHOOK
# ============================================================

@app.route(
    f"/webhook/{WEBHOOK_SECRET}",
    methods=["POST"]
)
def webhook():

    try:

        update = request.get_json(
            silent=True
        )

        if not update:

            # Telegram expects successful response
            return jsonify({
                "ok": True
            })

        # ====================================================
        # IMPORTANT
        #
        # Return 200 quickly.
        # Process update in background.
        # ====================================================

        thread = threading.Thread(
            target=process_update,
            args=(update,),
            daemon=True
        )

        thread.start()

        return jsonify({
            "ok": True
        }), 200

    except Exception:

        # Never return 500 for normal webhook handling
        logger.exception(
            "Webhook handler failed"
        )

        return jsonify({
            "ok": True
        }), 200


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/")
def home():

    return "NOT YOUR VIBE MUSIC BOT OK", 200


@app.route("/health")
def health():

    return jsonify({
        "ok": True,
        "users": count_users(),
        "tracks": count_tracks()
    })


# ============================================================
# WEBHOOK SETUP
# ============================================================

def setup_webhook():

    if not RENDER_EXTERNAL_URL:

        logger.warning(
            "RENDER_EXTERNAL_URL is missing. "
            "Webhook was not configured."
        )

        return

    webhook_url = (
        f"{RENDER_EXTERNAL_URL.rstrip('/')}"
        f"/webhook/{WEBHOOK_SECRET}"
    )

    logger.info(
        "Setting webhook: %s",
        webhook_url
    )

    result = telegram(
        "setWebhook",
        {
            "url": webhook_url,

            "allowed_updates": [
                "message",
                "callback_query",
                "channel_post"
            ],

            "drop_pending_updates": False
        }
    )

    logger.info(
        "setWebhook result: %s",
        result
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    logger.info(
        "Starting NOT YOUR VIBE MUSIC BOT..."
    )

    init_database()

    # Webhook setup
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
