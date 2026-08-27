import os
import json
import time
import random
import sqlite3
import logging
import threading
from datetime import datetime, timedelta

import requests
from flask import Flask, request

from openai import OpenAI
from openai import RateLimitError, APIError

from telethon import TelegramClient
from telethon.sessions import StringSession


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger("not-your-vibe-bot")


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


# =========================================================
# ENVIRONMENT
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

SOURCE_CHANNEL = os.getenv(
    "SOURCE_CHANNEL",
    "@notyourvibemp3collection"
)

ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")


# =========================================================
# TELETHON
# =========================================================

TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELETHON_SESSION = os.getenv("TELETHON_SESSION")


# =========================================================
# DATABASE
# =========================================================

DB_FILE = os.getenv(
    "DB_FILE",
    "music.db"
)

db_lock = threading.RLock()


# =========================================================
# SETTINGS
# =========================================================

OPENAI_MODEL = os.getenv(
    "OPENAI_MODEL",
    "gpt-5-mini"
)

# AI request တစ်ခုပြီးတိုင်း စောင့်မယ့်အချိန်
AI_DELAY = float(
    os.getenv(
        "AI_DELAY",
        "1.0"
    )
)

# AI quota error ဖြစ်ရင် ဒီလောက်ကြာ API မခေါ်တော့ဘူး
AI_PAUSE_MINUTES = int(
    os.getenv(
        "AI_PAUSE_MINUTES",
        "60"
    )
)

# Historical import
IMPORT_LIMIT = int(
    os.getenv(
        "IMPORT_LIMIT",
        "0"
    )
)

# 0 = channel အကုန် scan
# ဥပမာ 500 ဆို နောက်ဆုံး 500 posts ကို scan


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
# GLOBAL STATES
# =========================================================

rescan_running = False
import_running = False

state_lock = threading.RLock()

ai_paused_until = 0.0


# =========================================================
# TELEGRAM BOT API
# =========================================================

if not BOT_TOKEN:
    logger.warning(
        "BOT_TOKEN missing"
    )


TELEGRAM_API = (
    f"https://api.telegram.org/bot{BOT_TOKEN}"
    if BOT_TOKEN
    else ""
)


def telegram_request(
    method,
    data=None,
    timeout=30
):
    """
    Telegram Bot API wrapper.
    """

    if not TELEGRAM_API:
        logger.error(
            "Telegram API unavailable"
        )
        return None

    try:

        response = requests.post(
            f"{TELEGRAM_API}/{method}",
            data=data or {},
            timeout=timeout
        )

        try:
            result = response.json()
        except Exception:
            logger.error(
                "Telegram returned invalid JSON: %s",
                response.text[:500]
            )
            return None

        if not result.get("ok"):

            logger.error(
                "Telegram API error: %s",
                result
            )

        return result

    except requests.RequestException:

        logger.exception(
            "Telegram request failed: %s",
            method
        )

        return None

    except Exception:

        logger.exception(
            "Unexpected Telegram error: %s",
            method
        )

        return None


# =========================================================
# SEND MESSAGE
# =========================================================

def send_message(
    chat_id,
    text,
    reply_markup=None
):

    data = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True
    }

    if reply_markup:
        data["reply_markup"] = json.dumps(
            reply_markup,
            ensure_ascii=False
        )

    return telegram_request(
        "sendMessage",
        data
    )


# =========================================================
# COPY CHANNEL MESSAGE
# =========================================================

def copy_channel_message(
    chat_id,
    message_id
):

    return telegram_request(
        "copyMessage",
        {
            "chat_id": chat_id,
            "from_chat_id": SOURCE_CHANNEL,
            "message_id": message_id
        }
    )


# =========================================================
# ANSWER CALLBACK
# =========================================================

def answer_callback(
    callback_id,
    text=None
):

    data = {
        "callback_query_id": callback_id
    }

    if text:
        data["text"] = text

    telegram_request(
        "answerCallbackQuery",
        data
    )


# =========================================================
# DATABASE CONNECTION
# =========================================================

def get_db():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    return conn


# =========================================================
# DATABASE INIT
# =========================================================

def init_db():

    with db_lock:

        conn = get_db()

        cursor = conn.cursor()

        # -------------------------------------------------
        # TRACKS
        # -------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tracks (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                message_id INTEGER UNIQUE NOT NULL,

                title TEXT DEFAULT '',

                caption TEXT DEFAULT '',

                moods TEXT DEFAULT '[]',

                ai_status TEXT DEFAULT 'pending',

                created_at DATETIME
                    DEFAULT CURRENT_TIMESTAMP,

                updated_at DATETIME
                    DEFAULT CURRENT_TIMESTAMP

            )
            """
        )

        # -------------------------------------------------
        # USERS
        # -------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (

                user_id INTEGER PRIMARY KEY,

                username TEXT DEFAULT '',

                first_name TEXT DEFAULT '',

                last_seen DATETIME
                    DEFAULT CURRENT_TIMESTAMP

            )
            """
        )

        # -------------------------------------------------
        # USER STATE
        # -------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_state (

                user_id INTEGER PRIMARY KEY,

                mood TEXT DEFAULT '',

                updated_at DATETIME
                    DEFAULT CURRENT_TIMESTAMP

            )
            """
        )

        # -------------------------------------------------
        # INDEX
        # -------------------------------------------------

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_tracks_ai_status
            ON tracks(ai_status)
            """
        )

        conn.commit()

        conn.close()

    logger.info(
        "Database initialized"
    )


# =========================================================
# USER SAVE
# =========================================================

def save_user(
    user
):

    if not user:
        return

    user_id = user.get("id")

    if not user_id:
        return

    username = (
        user.get("username")
        or ""
    )

    first_name = (
        user.get("first_name")
        or ""
    )

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
# USER COUNT
# =========================================================

def get_user_count():

    with db_lock:

        conn = get_db()

        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM users
            """
        ).fetchone()

        conn.close()

    return int(
        row["count"]
    )


# =========================================================
# USER MOOD
# =========================================================

def set_user_mood(
    user_id,
    mood
):

    if not user_id:
        return

    if mood not in MOODS:
        return

    with db_lock:

        conn = get_db()

        conn.execute(
            """
            INSERT INTO user_state (
                user_id,
                mood
            )

            VALUES (?, ?)

            ON CONFLICT(user_id)

            DO UPDATE SET

                mood = excluded.mood,

                updated_at =
                    CURRENT_TIMESTAMP
            """,
            (
                int(user_id),
                mood
            )
        )

        conn.commit()

        conn.close()


def get_user_mood(
    user_id
):

    if not user_id:
        return None

    with db_lock:

        conn = get_db()

        row = conn.execute(
            """
            SELECT mood
            FROM user_state
            WHERE user_id = ?
            """,
            (
                int(user_id),
            )
        ).fetchone()

        conn.close()

    if not row:
        return None

    mood = row["mood"]

    if mood in MOODS:
        return mood

    return None


# =========================================================
# TRACK SAVE
# =========================================================

def save_track(
    message_id,
    title="",
    caption=""
):

    if not message_id:
        return

    with db_lock:

        conn = get_db()

        # IMPORTANT:
        # Existing classified moods ကို
        # নতুন webhook update လာလို့ မဖျက်ဘူး။

        conn.execute(
            """
            INSERT INTO tracks (
                message_id,
                title,
                caption,
                moods,
                ai_status
            )

            VALUES (
                ?, ?, ?, '[]', 'pending'
            )

            ON CONFLICT(message_id)

            DO UPDATE SET

                title =
                    CASE
                        WHEN excluded.title != ''
                        THEN excluded.title
                        ELSE tracks.title
                    END,

                caption =
                    CASE
                        WHEN excluded.caption != ''
                        THEN excluded.caption
                        ELSE tracks.caption
                    END,

                updated_at =
                    CURRENT_TIMESTAMP
            """,
            (
                int(message_id),
                title or "",
                caption or ""
            )
        )

        conn.commit()

        conn.close()


# =========================================================
# GET TRACK
# =========================================================

def get_track(
    message_id
):

    with db_lock:

        conn = get_db()

        row = conn.execute(
            """
            SELECT *
            FROM tracks
            WHERE message_id = ?
            """,
            (
                int(message_id),
            )
        ).fetchone()

        conn.close()

    return row


# =========================================================
# GET ALL TRACKS
# =========================================================

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


# =========================================================
# GET PENDING TRACKS
# =========================================================

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
# UPDATE MOODS
# =========================================================

def update_track_moods(
    message_id,
    moods,
    status="classified"
):

    if not isinstance(
        moods,
        list
    ):
        moods = []

    clean = []

    for mood in moods:

        mood = (
            str(mood)
            .lower()
            .strip()
        )

        if mood in MOODS:
            if mood not in clean:
                clean.append(mood)

    with db_lock:

        conn = get_db()

        conn.execute(
            """
            UPDATE tracks

            SET

                moods = ?,

                ai_status = ?,

                updated_at =
                    CURRENT_TIMESTAMP

            WHERE message_id = ?
            """,
            (
                json.dumps(
                    clean,
                    ensure_ascii=False
                ),
                status,
                int(message_id)
            )
        )

        conn.commit()

        conn.close()


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
        "OPENAI_API_KEY missing. "
        "AI classification disabled."
    )


# =========================================================
# AI PAUSE
# =========================================================

def ai_is_paused():

    with state_lock:

        return (
            time.time()
            < ai_paused_until
        )


def pause_ai():

    global ai_paused_until

    with state_lock:

        ai_paused_until = (
            time.time()
            + (
                AI_PAUSE_MINUTES
                * 60
            )
        )

    logger.warning(
        "AI paused for %s minutes",
        AI_PAUSE_MINUTES
    )


def resume_ai():

    global ai_paused_until

    with state_lock:

        ai_paused_until = 0

    logger.info(
        "AI manually resumed"
    )


# =========================================================
# AI CLASSIFIER
# =========================================================

def classify_music(
    title,
    caption
):

    if not openai_client:

        return []

    # -----------------------------------------------------
    # IMPORTANT
    # Quota error ဖြစ်ပြီးရင်
    # Track တစ်ပုဒ်ချင်းစီ API ထပ်မခေါ်တော့ဘူး
    # -----------------------------------------------------

    if ai_is_paused():

        logger.warning(
            "AI currently paused"
        )

        return []

    text = f"""
Song title:
{title or ""}

Caption:
{caption or ""}
"""


    instructions = f"""
You are the music mood classifier
for NOT YOUR VIBE MUSIC.

Allowed moods ONLY:

{", ".join(MOODS)}

Return ONLY JSON.

Example:

{{
  "moods": ["melodic", "love"]
}}

Rules:

1. Use ONLY allowed moods.
2. Return one or more moods.
3. Never create another mood.
4. No explanation.
5. Judge from title, artist,
   genre, caption and likely
   musical feeling.
6. Prefer accurate moods.
7. JSON only.
"""


    try:

        response = openai_client.responses.create(

            model=OPENAI_MODEL,

            instructions=instructions,

            input=text

        )

        raw = (
            getattr(
                response,
                "output_text",
                ""
            )
            or ""
        ).strip()

        logger.info(
            "AI response: %s",
            raw
        )

        if not raw:
            return []

        # -------------------------------------------------
        # JSON fence ဖြစ်လာရင် ဖြုတ်
        # -------------------------------------------------

        if raw.startswith(
            "```"
        ):

            raw = raw.replace(
                "```json",
                ""
            )

            raw = raw.replace(
                "```",
                ""
            )

            raw = raw.strip()

        data = json.loads(
            raw
        )

        moods = data.get(
            "moods",
            []
        )

        if not isinstance(
            moods,
            list
        ):
            return []

        result = []

        for mood in moods:

            mood = (
                str(mood)
                .lower()
                .strip()
            )

            if mood in MOODS:

                if mood not in result:

                    result.append(
                        mood
                    )

        return result

    except RateLimitError:

        logger.error(
            "OPENAI QUOTA/RATE LIMIT ERROR. "
            "AI temporarily paused."
        )

        pause_ai()

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

def classify_track(
    track
):

    if not track:
        return []

    message_id = track[
        "message_id"
    ]

    title = (
        track["title"]
        or ""
    )

    caption = (
        track["caption"]
        or ""
    )

    logger.info(
        "AI scanning message_id=%s | title=%s",
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
            "CLASSIFIED %s -> %s",
            message_id,
            moods
        )

    else:

        # -------------------------------------------------
        # AI မရသေးရင် pending
        #
        # ဒါပေမယ့် User music sending ကို
        # မပိတ်ဘူး။
        # -------------------------------------------------

        update_track_moods(
            message_id,
            [],
            "pending"
        )

        logger.warning(
            "Track %s remains pending",
            message_id
        )

    if AI_DELAY > 0:

        time.sleep(
            AI_DELAY
        )

    return moods


# =========================================================
# CLASSIFY NEW TRACK
# =========================================================

def classify_new_track(
    message_id
):

    track = get_track(
        message_id
    )

    if not track:
        return

    # AI paused ဖြစ်နေရင်
    # background thread က API မခေါ်
    if ai_is_paused():
        return

    classify_track(
        track
    )


# =========================================================
# FULL RESCAN
# =========================================================

rescan_lock = threading.Lock()


def full_rescan():

    global rescan_running

    with rescan_lock:

        if rescan_running:

            logger.warning(
                "Rescan already running"
            )

            return

        rescan_running = True

    success = 0
    failed = 0

    try:

        logger.info(
            "================================"
        )

        logger.info(
            "FULL AI RESCAN STARTED"
        )

        logger.info(
            "================================"
        )

        tracks = get_all_tracks()

        total = len(
            tracks
        )

        logger.info(
            "Total tracks: %s",
            total
        )

        for index, track in enumerate(
            tracks,
            1
        ):

            # Quota pause ဖြစ်ရင်
            # API မခေါ်တော့ဘဲ ရပ်
            if ai_is_paused():

                logger.warning(
                    "AI paused. "
                    "Rescan stopping safely."
                )

                break

            logger.info(
                "RESCAN %s/%s",
                index,
                total
            )

            moods = classify_track(
                track
            )

            if moods:
                success += 1
            else:
                failed += 1

        logger.info(
            "================================"
        )

        logger.info(
            "FULL AI RESCAN FINISHED"
        )

        logger.info(
            "Success=%s Pending/Failed=%s",
            success,
            failed
        )

        logger.info(
            "================================"
        )

    except Exception:

        logger.exception(
            "Full rescan crashed"
        )

    finally:

        rescan_running = False


def start_full_rescan():

    thread = threading.Thread(
        target=full_rescan,
        daemon=True,
        name="ai-rescan"
    )

    thread.start()


# =========================================================
# PENDING RESCAN
# =========================================================

def pending_rescan():

    global rescan_running

    with rescan_lock:

        if rescan_running:

            logger.warning(
                "Rescan already running"
            )

            return

        rescan_running = True

    success = 0
    failed = 0

    try:

        tracks = get_pending_tracks()

        logger.info(
            "PENDING RESCAN: %s tracks",
            len(tracks)
        )

        for track in tracks:

            if ai_is_paused():

                break

            moods = classify_track(
                track
            )

            if moods:
                success += 1
            else:
                failed += 1

        logger.info(
            "PENDING RESCAN FINISHED "
            "success=%s failed=%s",
            success,
            failed
        )

    except Exception:

        logger.exception(
            "Pending rescan crashed"
        )

    finally:

        rescan_running = False


def start_pending_rescan():

    thread = threading.Thread(
        target=pending_rescan,
        daemon=True,
        name="pending-rescan"
    )

    thread.start()


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
                }
            ],

            [
                {
                    "text": "🔄 CHANGE MOOD",
                    "callback_data": "change_mood"
                }
            ]
        ]
    }


# =========================================================
# TRACK MOODS PARSER
# =========================================================

def parse_moods(
    moods_text
):

    try:

        moods = json.loads(
            moods_text or "[]"
        )

        if not isinstance(
            moods,
            list
        ):
            return []

        return [
            mood
            for mood in moods
            if mood in MOODS
        ]

    except Exception:

        return []


# =========================================================
# GET TRACKS BY MOOD
# =========================================================

def get_tracks_by_mood(
    mood
):

    if mood not in MOODS:
        return []

    rows = get_all_tracks()

    results = []

    for row in rows:

        moods = parse_moods(
            row["moods"]
        )

        if mood in moods:

            results.append(
                row
            )

    return results


# =========================================================
# GET FALLBACK TRACKS
# =========================================================

def get_fallback_tracks():

    """
    AI classification မပြီးသေးတဲ့ Track တွေ။
    """

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
# GET ANY TRACK
# =========================================================

def get_any_track():

    with db_lock:

        conn = get_db()

        row = conn.execute(
            """
            SELECT *
            FROM tracks
            ORDER BY RANDOM()
            LIMIT 1
            """
        ).fetchone()

        conn.close()

    return row


# =========================================================
# SEND TRACK
# =========================================================

def send_track_to_user(
    chat_id,
    track,
    mood=None,
    fallback=False
):

    if not track:

        send_message(
            chat_id,
            "😔 Track မတွေ့သေးပါဘူး။"
        )

        return False

    result = copy_channel_message(
        chat_id,
        track["message_id"]
    )

    if not result or not result.get(
        "ok"
    ):

        logger.error(
            "copyMessage failed: %s",
            result
        )

        send_message(

            chat_id,

            "⚠️ ဒီ Track ကို ပို့မရသေးပါဘူး။\n\n"
            "Bot ကို Source Channel ထဲမှာ "
            "Administrator ထည့်ထားပြီး "
            "permission ရှိမရှိ စစ်ပါ။"

        )

        return False

    if mood:

        if fallback:

            send_message(

                chat_id,

                f"🎵 {MOOD_NAMES.get(mood, mood)}\n\n"
                "🤖 AI classification မပြီးသေးလို့ "
                "available Track တစ်ပုဒ်ကို ပို့ပေးထားပါတယ်။",

                music_buttons()

            )

        else:

            send_message(

                chat_id,

                f"🎵 {MOOD_NAMES.get(mood, mood)}",

                music_buttons()

            )

    else:

        send_message(
            chat_id,
            "🎵 NOT YOUR VIBE",
            music_buttons()
        )

    return True


# =========================================================
# SEND RANDOM MUSIC
# =========================================================

def send_random_music(
    chat_id,
    mood
):

    # -----------------------------------------------------
    # 1. အရင်ဆုံး အဲဒီ Mood ရဲ့ classified tracks
    # -----------------------------------------------------

    tracks = get_tracks_by_mood(
        mood
    )

    if tracks:

        track = random.choice(
            tracks
        )

        return send_track_to_user(
            chat_id,
            track,
            mood,
            False
        )

    # -----------------------------------------------------
    # 2. မရှိသေးရင် pending tracks
    # -----------------------------------------------------

    pending = get_fallback_tracks()

    if pending:

        track = random.choice(
            pending
        )

        return send_track_to_user(
            chat_id,
            track,
            mood,
            True
        )

    # -----------------------------------------------------
    # 3. DB လုံးဝမရှိ
    # -----------------------------------------------------

    send_message(

        chat_id,

        f"😔 {MOOD_NAMES.get(mood, mood)}\n\n"
        "ဒီ Bot ထဲမှာ Track မရှိသေးပါဘူး။\n\n"
        "Admin က /import လုပ်ဖို့လိုပါတယ်။"

    )

    return False


# =========================================================
# NEXT MUSIC
# =========================================================

def send_next_music(
    chat_id,
    user_id
):

    mood = get_user_mood(
        user_id
    )

    if not mood:

        send_message(

            chat_id,

            "🎧 အရင်ဆုံး Mood တစ်ခုရွေးပါ။",

            mood_keyboard()

        )

        return

    send_random_music(
        chat_id,
        mood
    )


# =========================================================
# TELETHON IMPORT
# =========================================================

def create_telethon_client():

    if not TELEGRAM_API_ID:

        logger.error(
            "TELEGRAM_API_ID missing"
        )

        return None

    if not TELEGRAM_API_HASH:

        logger.error(
            "TELEGRAM_API_HASH missing"
        )

        return None

    if not TELETHON_SESSION:

        logger.error(
            "TELETHON_SESSION missing"
        )

        return None

    try:

        client = TelegramClient(

            StringSession(
                TELETHON_SESSION
            ),

            int(
                TELEGRAM_API_ID
            ),

            TELEGRAM_API_HASH

        )

        client.start()

        logger.info(
            "Telethon connected"
        )

        return client

    except Exception:

        logger.exception(
            "Telethon connection failed"
        )

        return None


# =========================================================
# TELETHON TITLE
# =========================================================

def telethon_title(
    message
):

    text = (
        message.message
        or ""
    )

    lines = [

        line.strip()

        for line in text.splitlines()

        if line.strip()

    ]

    if lines:

        return lines[0]

    return "Unknown Track"


# =========================================================
# HISTORICAL IMPORT
# =========================================================

import_lock = threading.Lock()


def import_old_tracks():

    global import_running

    with import_lock:

        if import_running:

            logger.warning(
                "Import already running"
            )

            return

        import_running = True

    client = None

    imported = 0
    skipped = 0

    try:

        logger.info(
            "================================"
        )

        logger.info(
            "HISTORICAL IMPORT STARTED"
        )

        logger.info(
            "Source: %s",
            SOURCE_CHANNEL
        )

        logger.info(
            "================================"
        )

        client = create_telethon_client()

        if not client:

            return

        entity = client.get_entity(
            SOURCE_CHANNEL
        )

        logger.info(
            "Channel entity loaded"
        )

        kwargs = {
            "reverse": True
        }

        if IMPORT_LIMIT > 0:

            kwargs["limit"] = IMPORT_LIMIT

        for message in client.iter_messages(
            entity,
            **kwargs
        ):

            if not message:
                continue

            message_id = message.id

            existing = get_track(
                message_id
            )

            if existing:

                skipped += 1

                continue

            caption = (
                message.message
                or ""
            )

            title = telethon_title(
                message
            )

            save_track(
                message_id,
                title,
                caption
            )

            imported += 1

            logger.info(
                "Imported %s | %s",
                message_id,
                title
            )

        logger.info(
            "================================"
        )

        logger.info(
            "HISTORICAL IMPORT FINISHED"
        )

        logger.info(
            "Imported=%s Skipped=%s",
            imported,
            skipped
        )

        logger.info(
            "Next step: /rescan"
        )

        logger.info(
            "================================"
        )

    except Exception:

        logger.exception(
            "Historical import failed"
        )

    finally:

        if client:

            try:
                client.disconnect()
            except Exception:
                pass

        import_running = False


def start_import():

    thread = threading.Thread(
        target=import_old_tracks,
        daemon=True,
        name="channel-import"
    )

    thread.start()


# =========================================================
# ADMIN CHECK
# =========================================================

def is_admin(
    user_id
):

    if not ADMIN_USER_ID:
        return False

    try:

        return int(user_id) == int(
            ADMIN_USER_ID
        )

    except Exception:

        return False


# =========================================================
# ADMIN COMMANDS
# =========================================================

def handle_admin_command(
    chat_id,
    user_id,
    text
):

    if not is_admin(
        user_id
    ):

        return False

    command = (
        text
        .strip()
        .lower()
        .split()[0]
    )

    # -----------------------------------------------------
    # /admin
    # -----------------------------------------------------

    if command == "/admin":

        send_message(

            chat_id,

            "🛠 NOT YOUR VIBE ADMIN\n\n"

            "/import\n"
            "📥 Channel အဟောင်း Track တွေ DB ထဲထည့်\n\n"

            "/rescan\n"
            "🤖 Track အားလုံး AI ပြန်စစ်\n\n"

            "/rescan_pending\n"
            "🤖 Pending Track တွေပဲ AI ပြန်စစ်\n\n"

            "/rescan_status\n"
            "🔎 AI status ကြည့်\n\n"

            "/ai_resume\n"
            "▶️ AI pause ကို ပြန်ဖွင့်\n\n"

            "/stats\n"
            "📊 User / Track statistics"

        )

        return True

    # -----------------------------------------------------
    # /import
    # -----------------------------------------------------

    if command == "/import":

        if import_running:

            send_message(
                chat_id,
                "⏳ Historical import already running."
            )

        else:

            send_message(

                chat_id,

                "📥 Channel အဟောင်း Track တွေကို "
                "Database ထဲသွင်းနေပါတယ်။\n\n"
                "ပြီးသွားရင် /rescan လုပ်ပါ။"

            )

            start_import()

        return True

    # -----------------------------------------------------
    # /rescan
    # -----------------------------------------------------

    if command == "/rescan":

        if rescan_running:

            send_message(
                chat_id,
                "⏳ AI Rescan already running."
            )

        else:

            send_message(

                chat_id,

                "🤖 FULL AI RESCAN စပါပြီ။\n\n"
                "Database ထဲက Track အားလုံးကို "
                "AI နဲ့ ပြန်စစ်နေပါတယ်။"

            )

            start_full_rescan()

        return True

    # -----------------------------------------------------
    # /rescan_pending
    # -----------------------------------------------------

    if command == "/rescan_pending":

        if rescan_running:

            send_message(
                chat_id,
                "⏳ AI Rescan already running."
            )

        else:

            pending = get_pending_tracks()

            send_message(

                chat_id,

                "🤖 Pending AI Rescan စပါပြီ။\n\n"
                f"⏳ Pending tracks: {len(pending)}"

            )

            start_pending_rescan()

        return True

    # -----------------------------------------------------
    # /ai_resume
    # -----------------------------------------------------

    if command == "/ai_resume":

        resume_ai()

        send_message(

            chat_id,

            "▶️ AI classification ပြန်ဖွင့်လိုက်ပါပြီ။"

        )

        return True

    # -----------------------------------------------------
    # /rescan_status
    # -----------------------------------------------------

    if command == "/rescan_status":

        tracks = get_all_tracks()

        classified = 0
        pending = 0

        for track in tracks:

            if track["ai_status"] == "classified":

                classified += 1

            else:

                pending += 1

        paused = ai_is_paused()

        send_message(

            chat_id,

            "🔎 AI STATUS\n\n"

            f"🎵 Total: {len(tracks)}\n"

            f"✅ Classified: {classified}\n"

            f"⏳ Pending: {pending}\n\n"

            f"🤖 Rescan Running: "
            f"{'YES' if rescan_running else 'NO'}\n"

            f"📥 Import Running: "
            f"{'YES' if import_running else 'NO'}\n"

            f"⏸ AI Paused: "
            f"{'YES' if paused else 'NO'}"

        )

        return True

    # -----------------------------------------------------
    # /stats
    # -----------------------------------------------------

    if command == "/stats":

        tracks = get_all_tracks()

        classified = sum(

            1

            for track in tracks

            if track["ai_status"]
            == "classified"

        )

        pending = (
            len(tracks)
            - classified
        )

        users = get_user_count()

        send_message(

            chat_id,

            "📊 NOT YOUR VIBE MUSIC BOT\n\n"

            f"👤 Users: {users}\n"

            f"🎵 Tracks: {len(tracks)}\n"

            f"🤖 AI Classified: {classified}\n"

            f"⏳ Pending: {pending}\n\n"

            f"📥 Import: "
            f"{'RUNNING' if import_running else 'IDLE'}\n"

            f"🤖 Rescan: "
            f"{'RUNNING' if rescan_running else 'IDLE'}"

        )

        return True

    return False


# =========================================================
# NORMAL MESSAGE HANDLER
# =========================================================

def handle_message(
    message
):

    chat = message.get(
        "chat"
    ) or {}

    chat_id = chat.get(
        "id"
    )

    user = message.get(
        "from"
    ) or {}

    user_id = user.get(
        "id"
    )

    text = (
        message.get("text")
        or ""
    ).strip()

    save_user(
        user
    )

    # -----------------------------------------------------
    # ADMIN
    # -----------------------------------------------------

    if text.startswith("/"):

        handled = handle_admin_command(

            chat_id,

            user_id,

            text

        )

        if handled:

            return

    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    if text == "/start":

        send_message(

            chat_id,

            "🎧 Welcome to "
            "NOT YOUR VIBE MUSIC BOT\n\n"

            "Mood တစ်ခုရွေးပြီး "
            "Music နားထောင်ပါ 👇",

            mood_keyboard()

        )

        return

    # -----------------------------------------------------
    # MOOD
    # -----------------------------------------------------

    if text == "/mood":

        send_message(

            chat_id,

            "🎧 Choose your mood:",

            mood_keyboard()

        )

        return


# =========================================================
# CHANNEL TITLE
# =========================================================

def extract_channel_title(
    message
):

    caption = (

        message.get(
            "caption"
        )

        or message.get(
            "text"
        )

        or ""

    )

    lines = [

        line.strip()

        for line in caption.splitlines()

        if line.strip()

    ]

    if lines:

        return lines[0]

    return "Unknown Track"


# =========================================================
# CHANNEL POST
# =========================================================

def handle_channel_post(
    message
):

    chat = message.get(
        "chat"
    ) or {}

    chat_username = (
        chat.get("username")
        or ""
    )

    message_id = message.get(
        "message_id"
    )

    if not message_id:

        return

    source_username = (
        SOURCE_CHANNEL
        .lstrip("@")
        .lower()
    )

    # -----------------------------------------------------
    # Public channel filtering
    # -----------------------------------------------------

    if chat_username:

        if (
            chat_username.lower()
            != source_username
        ):

            return

    caption = (

        message.get(
            "caption"
        )

        or message.get(
            "text"
        )

        or ""

    )

    title = extract_channel_title(
        message
    )

    save_track(

        message_id,

        title,

        caption

    )

    logger.info(

        "NEW CHANNEL TRACK: %s | %s",

        message_id,

        title

    )

    # -----------------------------------------------------
    # AI background
    # -----------------------------------------------------

    thread = threading.Thread(

        target=classify_new_track,

        args=(message_id,),

        daemon=True,

        name=f"ai-track-{message_id}"

    )

    thread.start()


# =========================================================
# CALLBACK HANDLER
# =========================================================

def handle_callback(
    callback
):

    callback_id = callback.get(
        "id"
    )

    data = (
        callback.get(
            "data",
            ""
        )
        or ""
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

    user = (
        callback.get(
            "from"
        )
        or {}
    )

    user_id = user.get(
        "id"
    )

    answer_callback(
        callback_id
    )

    # -----------------------------------------------------
    # MOOD
    # -----------------------------------------------------

    if data.startswith(
        "mood:"
    ):

        mood = data.split(
            ":",
            1
        )[1]

        if mood not in MOODS:

            return

        set_user_mood(
            user_id,
            mood
        )

        send_random_music(

            chat_id,

            mood

        )

        return

    # -----------------------------------------------------
    # CHANGE MOOD
    # -----------------------------------------------------

    if data == "change_mood":

        send_message(

            chat_id,

            "🎧 Choose your mood:",

            mood_keyboard()

        )

        return

    # -----------------------------------------------------
    # NEXT
    # -----------------------------------------------------

    if data == "next":

        send_next_music(

            chat_id,

            user_id

        )

        return


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

        # -------------------------------------------------
        # NORMAL MESSAGE
        # -------------------------------------------------

        message = update.get(
            "message"
        )

        if message:

            handle_message(
                message
            )

        # -------------------------------------------------
        # CALLBACK
        # -------------------------------------------------

        callback = update.get(
            "callback_query"
        )

        if callback:

            handle_callback(
                callback
            )

        # -------------------------------------------------
        # CHANNEL POST
        # -------------------------------------------------

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

        # Telegram ကို 200 ပြန်ပေး
        # မဟုတ်ရင် retry ဖြစ်နိုင်တယ်

        return {
            "ok": False
        }, 200


# =========================================================
# HOME
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return (
        "NOT YOUR VIBE MUSIC BOT is running."
    )


# =========================================================
# HEALTH
# =========================================================

@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return {

        "status": "ok",

        "ai": bool(
            openai_client
        ),

        "ai_paused":
            ai_is_paused(),

        "rescan_running":
            rescan_running,

        "import_running":
            import_running,

        "source_channel":
            SOURCE_CHANNEL

    }


# =========================================================
# SET WEBHOOK
# =========================================================

def set_webhook():

    if not RENDER_URL:

        logger.warning(
            "RENDER_EXTERNAL_URL missing"
        )

        return False

    webhook_url = (

        RENDER_URL.rstrip("/")

        + "/webhook"

    )

    result = telegram_request(

        "setWebhook",

        {
            "url": webhook_url,

            "allowed_updates": json.dumps(
                [
                    "message",
                    "callback_query",
                    "channel_post"
                ]
            )
        }

    )

    logger.info(
        "Webhook result: %s",
        result
    )

    return bool(
        result
        and result.get("ok")
    )


# =========================================================
# STARTUP
# =========================================================

def startup_tasks():

    time.sleep(
        3
    )

    # -----------------------------------------------------
    # WEBHOOK
    # -----------------------------------------------------

    set_webhook()

    # -----------------------------------------------------
    # ENV CHECK
    # -----------------------------------------------------

    if TELEGRAM_API_ID:

        logger.info(
            "TELEGRAM_API_ID: OK"
        )

    else:

        logger.warning(
            "TELEGRAM_API_ID missing"
        )

    if TELEGRAM_API_HASH:

        logger.info(
            "TELEGRAM_API_HASH: OK"
        )

    else:

        logger.warning(
            "TELEGRAM_API_HASH missing"
        )

    if TELETHON_SESSION:

        logger.info(
            "TELETHON_SESSION: OK"
        )

    else:

        logger.warning(
            "TELETHON_SESSION missing"
        )

    if OPENAI_API_KEY:

        logger.info(
            "OPENAI_API_KEY: OK"
        )

    else:

        logger.warning(
            "OPENAI_API_KEY missing"
        )


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    logger.info(
        "========================================"
    )

    logger.info(
        "STARTING NOT YOUR VIBE MUSIC BOT"
    )

    logger.info(
        "========================================"
    )

    # Database
    init_db()

    # Startup
    startup_thread = threading.Thread(

        target=startup_tasks,

        daemon=True,

        name="startup"

    )

    startup_thread.start()

    # Render PORT
    port = int(

        os.getenv(
            "PORT",
            "10000"
        )

    )

    # Flask
    app.run(

        host="0.0.0.0",

        port=port,

        threaded=True

)
