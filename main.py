import os
import json
import time
import random
import sqlite3
import logging
import threading

import requests
from flask import Flask, request

from openai import OpenAI
from openai import RateLimitError, APIError

# Telethon
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
# ENV
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
# TELETHON ENV
# =========================================================

# Telegram API ID
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")

# Telegram API HASH
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")

# Telethon StringSession
#
# Render မှာ historical channel scan လုပ်ဖို့
# StringSession ထည့်ထားတာ အကောင်းဆုံး။
#
TELETHON_SESSION = os.getenv(
    "TELETHON_SESSION"
)


# =========================================================
# DATABASE
# =========================================================

DB_FILE = "music.db"

db_lock = threading.Lock()


# =========================================================
# SETTINGS
# =========================================================

OPENAI_MODEL = "gpt-5-mini"

AI_DELAY = 1.0

IMPORT_BATCH_SIZE = 100


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
# TELEGRAM BOT API
# =========================================================

if not BOT_TOKEN:

    logger.warning(
        "BOT_TOKEN is missing"
    )


TELEGRAM_API = (
    f"https://api.telegram.org/bot{BOT_TOKEN}"
    if BOT_TOKEN
    else ""
)


def telegram_request(
    method,
    data=None
):

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
        "text": text
    }

    if reply_markup:

        data["reply_markup"] = json.dumps(
            reply_markup
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
    from_chat_id,
    message_id
):

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

    logger.info(
        "Database initialized"
    )


# =========================================================
# USERS
# =========================================================

def save_user(user):

    if not user:
        return

    user_id = user.get("id")

    if not user_id:
        return

    username = user.get(
        "username"
    ) or ""

    first_name = user.get(
        "first_name"
    ) or ""

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

    return row["count"]


# =========================================================
# TRACK SAVE
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

            VALUES (
                ?, ?, ?, '[]', 'pending'
            )

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
                json.dumps(
                    moods,
                    ensure_ascii=False
                ),
                status,
                message_id
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
        "AI disabled."
    )


# =========================================================
# AI CLASSIFIER
# =========================================================

def classify_music(
    title,
    caption
):

    if not openai_client:

        logger.warning(
            "AI unavailable"
        )

        return []


    text = f"""
Song title:
{title}

Caption:
{caption}
"""


    instructions = f"""
You are the music mood classifier
for NOT YOUR VIBE MUSIC.

Use ONLY these moods:

{", ".join(MOODS)}

Return ONLY valid JSON.

Format:

{{
  "moods": ["melodic", "love"]
}}

Rules:

1. Use only allowed moods.
2. Return one or more moods.
3. Never invent mood names.
4. No explanation.
5. Judge from title, artist,
   genre, caption and musical feeling.
"""


    try:

        response = openai_client.responses.create(

            model=OPENAI_MODEL,

            instructions=instructions,

            input=text
        )


        raw = (
            response.output_text
            or ""
        ).strip()


        logger.info(
            "AI response: %s",
            raw
        )


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


        valid = []


        for mood in moods:

            mood = (
                str(mood)
                .lower()
                .strip()
            )

            if mood in MOODS:

                if mood not in valid:

                    valid.append(
                        mood
                    )


        return valid


    except RateLimitError:

        logger.error(
            "OPENAI QUOTA EXCEEDED. "
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

def classify_track(
    track
):

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
        "AI scanning %s | %s",
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

        update_track_moods(
            message_id,
            [],
            "pending"
        )

        logger.warning(
            "Track %s remains pending",
            message_id
        )


    time.sleep(
        AI_DELAY
    )


    return moods


# =========================================================
# FULL AI RESCAN
# =========================================================

rescan_lock = threading.Lock()

rescan_running = False


def full_rescan():

    global rescan_running


    with rescan_lock:

        if rescan_running:

            logger.warning(
                "Rescan already running"
            )

            return

        rescan_running = True


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


        success = 0

        failed = 0


        logger.info(
            "Database tracks: %s",
            total
        )


        for index, track in enumerate(
            tracks,
            1
        ):

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
            "Success=%s Pending=%s",
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
        daemon=True
    )

    thread.start()


# =========================================================
# TELETHON
# =========================================================

telethon_client = None

telethon_ready = False


def init_telethon():

    global telethon_client
    global telethon_ready


    if not TELEGRAM_API_ID:

        logger.warning(
            "TELEGRAM_API_ID missing"
        )

        return False


    if not TELEGRAM_API_HASH:

        logger.warning(
            "TELEGRAM_API_HASH missing"
        )

        return False


    if not TELETHON_SESSION:

        logger.warning(
            "TELETHON_SESSION missing. "
            "Historical import disabled."
        )

        return False


    try:

        telethon_client = TelegramClient(

            StringSession(
                TELETHON_SESSION
            ),

            int(
                TELEGRAM_API_ID
            ),

            TELEGRAM_API_HASH
        )


        telethon_client.start()


        telethon_ready = True


        logger.info(
            "Telethon connected"
        )


        return True


    except Exception:

        logger.exception(
            "Telethon initialization failed"
        )

        return False


# =========================================================
# EXTRACT TELETHON MESSAGE
# =========================================================

def telethon_message_text(
    message
):

    text = (
        message.message
        or ""
    )

    return text


def get_telethon_title(
    message
):

    text = telethon_message_text(
        message
    )


    lines = [

        x.strip()

        for x in text.splitlines()

        if x.strip()
    ]


    if lines:

        return lines[0]


    return "Unknown Track"


# =========================================================
# IMPORT OLD CHANNEL TRACKS
# =========================================================

import_running = False

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


    try:

        if not telethon_ready:

            logger.error(
                "Telethon is not ready"
            )

            return


        logger.info(
            "================================"
        )

        logger.info(
            "OLD CHANNEL IMPORT STARTED"
        )

        logger.info(
            "================================"
        )


        imported = 0

        skipped = 0


        entity = (
            telethon_client.get_entity(
                SOURCE_CHANNEL
            )
        )


        for message in telethon_client.iter_messages(
            entity,
            reverse=True
        ):

            if not message:

                continue


            message_id = (
                message.id
            )


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


            title = get_telethon_title(
                message
            )


            save_track(
                message_id=message_id,
                title=title,
                caption=caption
            )


            imported += 1


            logger.info(
                "Imported old track: %s | %s",
                message_id,
                title
            )


            # Don't immediately call AI here.
            # First import everything.
            #
            # After import:
            # /rescan


        logger.info(
            "================================"
        )

        logger.info(
            "OLD CHANNEL IMPORT FINISHED"
        )

        logger.info(
            "Imported=%s Skipped=%s",
            imported,
            skipped
        )

        logger.info(
            "================================"
        )


    except Exception:

        logger.exception(
            "Historical import failed"
        )


    finally:

        import_running = False


def start_import():

    thread = threading.Thread(
        target=import_old_tracks,
        daemon=True
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
# USER MOOD MEMORY
# =========================================================

user_moods = {}

user_moods_lock = threading.Lock()


def set_user_mood(
    user_id,
    mood
):

    with user_moods_lock:

        user_moods[
            int(user_id)
        ] = mood


def get_user_mood(
    user_id
):

    with user_moods_lock:

        return user_moods.get(
            int(user_id)
        )


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

        try:

            moods = json.loads(
                row["moods"]
                or "[]"
            )

        except Exception:

            moods = []


        if mood in moods:

            results.append(
                row
            )


    return results


# =========================================================
# SEND RANDOM MUSIC
# =========================================================

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
            "ဒီ Mood အတွက် Track မတွေ့သေးပါဘူး။\n\n"
            "AI classification မပြီးသေးတာ "
            "ဖြစ်နိုင်ပါတယ်။"

        )

        return


    track = random.choice(
        tracks
    )


    result = copy_channel_message(

        chat_id=chat_id,

        from_chat_id=SOURCE_CHANNEL,

        message_id=track[
            "message_id"
        ]
    )


    if not result or not result.get(
        "ok"
    ):

        send_message(

            chat_id,

            "⚠️ ဒီ Track ကို ပို့လို့မရသေးပါဘူး။\n\n"
            "Bot ကို Source Channel ထဲမှာ "
            "Administrator အဖြစ် ထည့်ထားတာ "
            "သေချာစစ်ပါ။"

        )

        return


    send_message(

        chat_id,

        f"🎵 {MOOD_NAMES.get(mood, mood)}",

        music_buttons()

    )


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


    tracks = get_tracks_by_mood(
        mood
    )


    if not tracks:

        send_message(

            chat_id,

            "😔 ဒီ Mood မှာ Track မရှိသေးပါဘူး။"

        )

        return


    track = random.choice(
        tracks
    )


    result = copy_channel_message(

        chat_id=chat_id,

        from_chat_id=SOURCE_CHANNEL,

        message_id=track[
            "message_id"
        ]
    )


    if not result or not result.get(
        "ok"
    ):

        send_message(

            chat_id,

            "⚠️ Track ပို့မရပါဘူး။\n"
            "Bot ရဲ့ Channel permission ကိုစစ်ပါ။"

        )

        return


    send_message(

        chat_id,

        f"🎵 {MOOD_NAMES.get(mood, mood)}",

        music_buttons()

    )


# =========================================================
# CALLBACK ANSWER
# =========================================================

def answer_callback(
    callback_id
):

    telegram_request(

        "answerCallbackQuery",

        {
            "callback_query_id":
                callback_id
        }

    )


# =========================================================
# CALLBACK HANDLER
# =========================================================

def handle_callback(
    callback
):

    callback_id = callback.get(
        "id"
    )

    data = callback.get(
        "data",
        ""
    )


    message = callback.get(
        "message"
    ) or {}


    chat = message.get(
        "chat"
    ) or {}


    chat_id = chat.get(
        "id"
    )


    user = callback.get(
        "from"
    ) or {}


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
# ADMIN
# =========================================================

def is_admin(
    user_id
):

    if not ADMIN_USER_ID:

        return False


    try:

        return int(
            user_id
        ) == int(
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
    )


    # -----------------------------------------------------
    # RESCAN
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

                "🤖 Full AI Rescan စပါပြီ။\n\n"
                "Database ထဲက Track အားလုံးကို "
                "AI နဲ့ ပြန်စစ်နေပါတယ်။"

            )

            start_full_rescan()


        return True


    # -----------------------------------------------------
    # IMPORT
    # -----------------------------------------------------

    if command == "/import":

        if import_running:

            send_message(

                chat_id,

                "⏳ Channel import already running."

            )

        else:

            send_message(

                chat_id,

                "📥 Channel အဟောင်း Track တွေကို "
                "Database ထဲ ပြန်ထည့်နေပါတယ်။\n\n"
                "ပြီးသွားရင် /rescan လုပ်ပါ။"

            )

            start_import()


        return True


    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    if command == "/rescan_status":

        tracks = get_all_tracks()


        classified = 0

        pending = 0


        for track in tracks:

            if track[
                "ai_status"
            ] == "classified":

                classified += 1

            else:

                pending += 1


        send_message(

            chat_id,

            "🔎 AI STATUS\n\n"

            f"🎵 Total: {len(tracks)}\n"

            f"✅ Classified: {classified}\n"

            f"⏳ Pending: {pending}\n\n"

            f"🤖 AI Running: "
            f"{'YES' if rescan_running else 'NO'}\n"

            f"📥 Import Running: "
            f"{'YES' if import_running else 'NO'}"

        )


        return True


    # -----------------------------------------------------
    # STATS
    # -----------------------------------------------------

    if command == "/stats":

        tracks = get_all_tracks()


        classified = sum(

            1

            for track in tracks

            if track[
                "ai_status"
            ] == "classified"

        )


        users = get_user_count()


        send_message(

            chat_id,

            "📊 NOT YOUR VIBE MUSIC BOT\n\n"

            f"👤 Users: {users}\n"

            f"🎵 Tracks: {len(tracks)}\n"

            f"🤖 AI Classified: {classified}\n"

            f"⏳ Pending: "
            f"{len(tracks) - classified}"

        )


        return True


    # -----------------------------------------------------
    # ADMIN HELP
    # -----------------------------------------------------

    if command == "/admin":

        send_message(

            chat_id,

            "🛠 ADMIN COMMANDS\n\n"

            "/import\n"
            "→ Channel အဟောင်းတွေ DB ထဲထည့်\n\n"

            "/rescan\n"
            "→ DB ထဲက Track အားလုံးကို AI ပြန်စစ်\n\n"

            "/rescan_status\n"
            "→ AI status ကြည့်\n\n"

            "/stats\n"
            "→ User / Track statistics"

        )


        return True


    return False


# =========================================================
# MESSAGE HANDLER
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


    text = message.get(
        "text"
    ) or ""


    save_user(
        user
    )


    # Admin
    if text.startswith(
        "/"
    ):

        handled = handle_admin_command(

            chat_id,

            user_id,

            text

        )


        if handled:

            return


    # Start
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


    # Mood
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

        message.get("caption")

        or message.get("text")

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


    chat_username = chat.get(
        "username"
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


    # Public channel check
    if chat_username:

        if (
            chat_username.lower()
            != source_username
        ):

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

        message_id,

        title,

        caption

    )


    logger.info(

        "New channel track saved: %s | %s",

        message_id,

        title

    )


    # AI in background
    thread = threading.Thread(

        target=classify_new_track,

        args=(message_id,),

        daemon=True

    )


    thread.start()


# =========================================================
# NEW TRACK CLASSIFICATION
# =========================================================

def classify_new_track(
    message_id
):

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


        # Normal message
        message = update.get(
            "message"
        )


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

        "telethon": telethon_ready,

        "rescan_running":
            rescan_running,

        "import_running":
            import_running

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
            "url":
                webhook_url
        }

    )


    logger.info(
        "Webhook result: %s",
        result
    )


# =========================================================
# STARTUP
# =========================================================

def startup_tasks():

    time.sleep(
        3
    )


    # Telegram webhook
    set_webhook()


    # Telethon
    init_telethon()


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    logger.info(
        "================================"
    )

    logger.info(
        "STARTING NOT YOUR VIBE MUSIC BOT"
    )

    logger.info(
        "================================"
    )


    init_db()


    startup_thread = threading.Thread(

        target=startup_tasks,

        daemon=True

    )


    startup_thread.start()


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
