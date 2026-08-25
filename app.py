import os
import random
import sqlite3
import threading
import time
import requests

from flask import Flask, request


# =========================================================
# APP
# =========================================================

app = Flask(__name__)


# =========================================================
# ENV
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# =========================================================
# ADMIN
# =========================================================
#
# Render Environment Variables မှာ
#
# ADMIN_ID=123456789
#
# ထည့်ပါ။
#
# မထည့်ရင် /stats နဲ့ /channels ကို လူတိုင်းသုံးနိုင်ပါတယ်။
#

ADMIN_ID = os.getenv("ADMIN_ID")

if ADMIN_ID:
    try:
        ADMIN_ID = int(ADMIN_ID)
    except:
        ADMIN_ID = None


# =========================================================
# DATABASE
# =========================================================

DB_FILE = "music_bot.db"

db_lock = threading.RLock()


# =========================================================
# TELEGRAM SESSION
# =========================================================

http = requests.Session()

http.headers.update({
    "User-Agent": "NOT-YOUR-VIBE-MUSIC-BOT"
})


# =========================================================
# MOODS
# =========================================================

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


# =========================================================
# MOOD -> CHANNEL
# =========================================================
#
# Username ရှိတဲ့ public channels
# username ကိုတိုက်ရိုက်သုံးနိုင်တယ်။
#
# ID ရှိပြီးသား channels
# ID ကိုသုံးထားတယ်။
#

MOOD_CHANNELS = {

    "sad": "@sadmooddatabase",

    "love": "@lovemooddatabase",

    "chill": "@chillmooddatabase",

    "energetic": "@energeticmooddatabase",

    "dark": "@darkmooddatabase",

    "night": "@nightdrivemooddatabase",

    "melodic": -1004446996297,

    "hype": -1004427220481

}


# =========================================================
# DATABASE INIT
# =========================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA journal_mode=WAL"
    )

    conn.execute(
        "PRAGMA busy_timeout=30000"
    )

    return conn


def init_database():

    with db_lock:

        conn = db_connect()

        try:

            # ---------------------------------------------
            # USERS
            # ---------------------------------------------

            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (

                    user_id INTEGER PRIMARY KEY,

                    username TEXT,

                    first_name TEXT,

                    last_name TEXT,

                    last_mood TEXT,

                    last_song INTEGER,

                    created_at INTEGER,

                    updated_at INTEGER

                )
            """)


            # ---------------------------------------------
            # TRACKS
            # ---------------------------------------------

            conn.execute("""
                CREATE TABLE IF NOT EXISTS tracks (

                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    mood TEXT NOT NULL,

                    channel_id TEXT NOT NULL,

                    message_id INTEGER NOT NULL,

                    active INTEGER DEFAULT 1,

                    created_at INTEGER,

                    UNIQUE(channel_id, message_id)

                )
            """)


            # ---------------------------------------------
            # USER HISTORY
            # ---------------------------------------------

            conn.execute("""
                CREATE TABLE IF NOT EXISTS history (

                    user_id INTEGER NOT NULL,

                    mood TEXT NOT NULL,

                    message_id INTEGER NOT NULL,

                    created_at INTEGER,

                    PRIMARY KEY(
                        user_id,
                        mood,
                        message_id
                    )

                )
            """)


            # ---------------------------------------------
            # INDEX
            # ---------------------------------------------

            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_tracks_mood
                ON tracks(mood)
            """)


            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_history_user_mood
                ON history(user_id, mood)
            """)


            conn.commit()

        finally:

            conn.close()


# =========================================================
# TELEGRAM API
# =========================================================

def telegram(
    method,
    data=None,
    timeout=15
):

    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN is missing"
        )

        return {
            "ok": False,
            "description": "BOT_TOKEN missing"
        }


    try:

        response = http.post(

            f"{TELEGRAM_API}/{method}",

            json=data or {},

            timeout=timeout

        )


        try:

            result = response.json()

        except:

            result = {
                "ok": False,
                "description": response.text
            }


        if not result.get("ok"):

            print(
                "Telegram API error:",
                method,
                result
            )


        return result


    except Exception as e:

        print(
            "Telegram request error:",
            method,
            str(e)
        )

        return {

            "ok": False,

            "description":
                str(e)

        }


# =========================================================
# SEND MESSAGE
# =========================================================

def send_message(
    chat_id,
    text,
    keyboard=None
):

    data = {

        "chat_id":
            chat_id,

        "text":
            text

    }


    if keyboard:

        data[
            "reply_markup"
        ] = keyboard


    return telegram(

        "sendMessage",

        data,

        timeout=10

    )


# =========================================================
# CALLBACK
# =========================================================

def answer_callback(
    callback_id,
    text=""
):

    return telegram(

        "answerCallbackQuery",

        {

            "callback_query_id":
                callback_id,

            "text":
                text

        },

        timeout=5

    )


# =========================================================
# COPY MESSAGE
# =========================================================

def copy_music(
    chat_id,
    channel_id,
    message_id
):

    return telegram(

        "copyMessage",

        {

            "chat_id":
                chat_id,

            "from_chat_id":
                channel_id,

            "message_id":
                message_id

        },

        timeout=20

    )


# =========================================================
# USER
# =========================================================

def save_user(
    user
):

    if not user:

        return


    user_id = user.get(
        "id"
    )


    if not user_id:

        return


    username = user.get(
        "username"
    )

    first_name = user.get(
        "first_name"
    )

    last_name = user.get(
        "last_name"
    )


    now = int(
        time.time()
    )


    with db_lock:

        conn = db_connect()

        try:

            conn.execute(
                """
                INSERT INTO users
                (
                    user_id,
                    username,
                    first_name,
                    last_name,
                    created_at,
                    updated_at
                )

                VALUES
                (
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?
                )

                ON CONFLICT(user_id)

                DO UPDATE SET

                    username=excluded.username,

                    first_name=excluded.first_name,

                    last_name=excluded.last_name,

                    updated_at=excluded.updated_at
                """,

                (
                    user_id,
                    username,
                    first_name,
                    last_name,
                    now,
                    now
                )

            )

            conn.commit()

        finally:

            conn.close()


# =========================================================
# USER MOOD STATE
# =========================================================

def set_user_mood(
    user_id,
    mood
):

    now = int(
        time.time()
    )


    with db_lock:

        conn = db_connect()

        try:

            conn.execute(
                """
                UPDATE users

                SET

                    last_mood=?,

                    updated_at=?

                WHERE user_id=?
                """,

                (
                    mood,
                    now,
                    user_id
                )

            )

            conn.commit()

        finally:

            conn.close()


def get_user_state(
    user_id
):

    with db_lock:

        conn = db_connect()

        try:

            row = conn.execute(
                """
                SELECT
                    last_mood,
                    last_song
                FROM users
                WHERE user_id=?
                """,

                (
                    user_id,
                )

            ).fetchone()

            if not row:

                return None


            return {

                "mood":
                    row["last_mood"],

                "last_song":
                    row["last_song"]

            }

        finally:

            conn.close()


# =========================================================
# SAVE LAST SONG
# =========================================================

def save_last_song(
    user_id,
    mood,
    message_id
):

    now = int(
        time.time()
    )


    with db_lock:

        conn = db_connect()

        try:

            conn.execute(
                """
                UPDATE users

                SET

                    last_mood=?,

                    last_song=?,

                    updated_at=?

                WHERE user_id=?
                """,

                (
                    mood,
                    message_id,
                    now,
                    user_id
                )

            )


            # ---------------------------------------------
            # HISTORY
            # ---------------------------------------------

            conn.execute(
                """
                INSERT OR IGNORE INTO history
                (
                    user_id,
                    mood,
                    message_id,
                    created_at
                )

                VALUES
                (
                    ?,
                    ?,
                    ?,
                    ?
                )
                """,

                (
                    user_id,
                    mood,
                    message_id,
                    now
                )

            )


            conn.commit()

        finally:

            conn.close()


# =========================================================
# GET RANDOM UNUSED TRACK
# =========================================================

def get_random_track(
    user_id,
    mood
):

    channel_id = str(
        MOOD_CHANNELS[mood]
    )


    with db_lock:

        conn = db_connect()

        try:

            # ---------------------------------------------
            # First: unused tracks
            # ---------------------------------------------

            rows = conn.execute(
                """
                SELECT
                    t.message_id

                FROM tracks t

                WHERE

                    t.mood=?

                    AND t.channel_id=?

                    AND t.active=1

                    AND NOT EXISTS (

                        SELECT 1

                        FROM history h

                        WHERE

                            h.user_id=?

                            AND h.mood=?

                            AND h.message_id=t.message_id

                    )

                ORDER BY RANDOM()

                LIMIT 20
                """,

                (
                    mood,
                    channel_id,
                    user_id,
                    mood
                )

            ).fetchall()


            if rows:

                return [

                    row["message_id"]

                    for row in rows

                ]


            # ---------------------------------------------
            # All tracks already used
            #
            # Reset this user's mood history
            # ---------------------------------------------

            conn.execute(
                """
                DELETE FROM history

                WHERE

                    user_id=?

                    AND mood=?
                """,

                (
                    user_id,
                    mood
                )

            )

            conn.commit()


            # ---------------------------------------------
            # Start new cycle
            # ---------------------------------------------

            rows = conn.execute(
                """
                SELECT
                    message_id

                FROM tracks

                WHERE

                    mood=?

                    AND channel_id=?

                    AND active=1

                ORDER BY RANDOM()

                LIMIT 20
                """,

                (
                    mood,
                    channel_id
                )

            ).fetchall()


            return [

                row["message_id"]

                for row in rows

            ]


        finally:

            conn.close()


# =========================================================
# ADD TRACK
# =========================================================

def add_track(
    mood,
    channel_id,
    message_id
):

    now = int(
        time.time()
    )


    with db_lock:

        conn = db_connect()

        try:

            conn.execute(
                """
                INSERT OR IGNORE INTO tracks
                (
                    mood,
                    channel_id,
                    message_id,
                    active,
                    created_at
                )

                VALUES
                (
                    ?,
                    ?,
                    ?,
                    1,
                    ?
                )
                """,

                (
                    mood,
                    str(channel_id),
                    message_id,
                    now
                )

            )

            conn.commit()

        finally:

            conn.close()


# =========================================================
# CHANNEL ID CHECK
# =========================================================

def check_channels():

    print(
        "\n========== CHANNEL CHECK ==========\n"
    )


    for mood in MOODS:

        channel = MOOD_CHANNELS[mood]


        result = telegram(

            "getChat",

            {
                "chat_id":
                    channel
            },

            timeout=10

        )


        if result.get("ok"):

            chat = result[
                "result"
            ]


            real_id = chat.get(
                "id"
            )


            title = chat.get(
                "title",
                ""
            )


            username = chat.get(
                "username",
                ""
            )


            print(
                f"{mood.upper():12} "
                f"ID={real_id} "
                f"TITLE={title} "
                f"USERNAME=@{username}"
            )


        else:

            print(

                f"{mood.upper():12} "
                f"ERROR={result.get('description')}"

            )


    print(
        "\n===================================\n"
    )


# =========================================================
# ADMIN CHANNEL CHECK COMMAND
# =========================================================

def get_channel_info(
    chat_id
):

    lines = []

    for mood in MOODS:

        channel = MOOD_CHANNELS[mood]


        result = telegram(

            "getChat",

            {
                "chat_id":
                    channel
            },

            timeout=10

        )


        if result.get("ok"):

            chat = result[
                "result"
            ]


            real_id = chat.get(
                "id"
            )

            title = chat.get(
                "title",
                "Unknown"
            )


            lines.append(

                f"{MOOD_NAMES[mood]}\n"
                f"ID: `{real_id}`\n"
                f"Title: {title}"

            )

        else:

            lines.append(

                f"{MOOD_NAMES[mood]}\n"
                f"❌ {result.get('description')}"

            )


    send_message(

        chat_id,

        "📡 CHANNEL INFORMATION\n\n"
        + "\n\n".join(lines)

    )


# =========================================================
# COUNT USERS
# =========================================================

def count_users():

    with db_lock:

        conn = db_connect()

        try:

            row = conn.execute(
                """
                SELECT COUNT(*)
                AS total
                FROM users
                """
            ).fetchone()


            return row["total"]

        finally:

            conn.close()


# =========================================================
# COUNT TRACKS
# =========================================================

def count_tracks():

    result = {}


    with db_lock:

        conn = db_connect()

        try:

            rows = conn.execute(
                """
                SELECT
                    mood,
                    COUNT(*) AS total

                FROM tracks

                WHERE active=1

                GROUP BY mood
                """
            ).fetchall()


            for row in rows:

                result[
                    row["mood"]
                ] = row["total"]


        finally:

            conn.close()


    return result


# =========================================================
# USER STATS
# =========================================================

def send_stats(
    chat_id
):

    if ADMIN_ID:

        if chat_id != ADMIN_ID:

            send_message(
                chat_id,
                "❌ Admin only."
            )

            return


    total = count_users()

    tracks = count_tracks()


    text = (
        "📊 NOT YOUR VIBE BOT\n\n"

        f"👥 Users: {total}\n\n"

        "🎵 Tracks:\n"
    )


    for mood in MOODS:

        text += (

            f"{MOOD_NAMES[mood]}: "
            f"{tracks.get(mood, 0)}\n"

        )


    send_message(
        chat_id,
        text
    )


# =========================================================
# MOOD MENU
# =========================================================

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


# =========================================================
# MUSIC BUTTONS
# =========================================================

def music_buttons():

    return {

        "inline_keyboard": [

            [

                {
                    "text":
                        "🔀 Next",

                    "callback_data":
                        "next_music"
                }

            ],

            [

                {
                    "text":
                        "🎧 Change Mood",

                    "callback_data":
                        "change_mood"
                }

            ]

        ]
    }


# =========================================================
# SEND TRACK
# =========================================================

def send_track(
    user_id,
    mood
):

    channel_id = MOOD_CHANNELS[mood]


    candidates = get_random_track(
        user_id,
        mood
    )


    if not candidates:

        send_message(

            user_id,

            f"{MOOD_NAMES[mood]}\n\n"

            "⚠️ ဒီ mood channel ထဲမှာ "
            "track database မရှိသေးပါ။\n\n"

            "Bot ကို channel ထဲမှာ "
            "Admin ထည့်ပြီး track အသစ်တင်ပါ။",

            mood_menu()

        )

        return


    # =====================================================
    # Try several candidates
    # =====================================================

    for message_id in candidates:

        result = copy_music(

            user_id,

            channel_id,

            message_id

        )


        if result.get("ok"):

            save_last_song(

                user_id,

                mood,

                message_id

            )


            print(

                "TRACK SENT",

                "| user:",
                user_id,

                "| mood:",
                mood,

                "| channel:",
                channel_id,

                "| message:",
                message_id

            )


            send_message(

                user_id,

                f"{MOOD_NAMES[mood]}\n\n"
                "🎧 Enjoy your music!",

                music_buttons()

            )


            return


        print(

            "TRACK FAILED",

            "| user:",
            user_id,

            "| mood:",
            mood,

            "| message:",
            message_id,

            "| error:",
            result.get("description")

        )


    send_message(

        user_id,

        f"{MOOD_NAMES[mood]}\n\n"

        "❌ Track ပို့မရပါ။\n\n"

        "Channel permission / "
        "message ID ကိုစစ်ပါ။",

        mood_menu()

    )


# =========================================================
# NEXT TRACK
# =========================================================

def next_track(
    user_id
):

    state = get_user_state(
        user_id
    )


    if not state:

        send_message(

            user_id,

            "🎧 အရင်ဆုံး Mood ရွေးပါ 👇",

            mood_menu()

        )

        return


    mood = state.get(
        "mood"
    )


    if mood not in MOODS:

        send_message(

            user_id,

            "🎧 Mood ရွေးပါ 👇",

            mood_menu()

        )

        return


    send_track(

        user_id,

        mood

    )


# =========================================================
# AUTO DISCOVER TRACK
# =========================================================
#
# Channel ထဲက Bot ရရှိတဲ့ message ကို
# database ထဲထည့်မယ်။
#
# Music message ဖြစ်မဖြစ် အကြမ်းဖျင်းစစ်တယ်။
#

def process_channel_message(
    message
):

    chat = message.get(
        "chat",
        {}
    )


    chat_id = chat.get(
        "id"
    )


    message_id = message.get(
        "message_id"
    )


    if not chat_id or not message_id:

        return


    # -----------------------------------------------------
    # Find mood from configured channel
    # -----------------------------------------------------

    mood = None


    for m in MOODS:

        configured = str(
            MOOD_CHANNELS[m]
        )


        if configured == str(
            chat_id
        ):

            mood = m

            break


        username = chat.get(
            "username"
        )


        if username:

            if configured.lower() == (
                "@" + username.lower()
            ):

                mood = m

                break


    if not mood:

        return


    # -----------------------------------------------------
    # Accept music/media messages
    # -----------------------------------------------------

    is_music = False


    if message.get(
        "audio"
    ):

        is_music = True


    if message.get(
        "document"
    ):

        is_music = True


    if message.get(
        "video"
    ):

        is_music = True


    if message.get(
        "voice"
    ):

        is_music = True


    if not is_music:

        return


    add_track(

        mood,

        chat_id,

        message_id

    )


    print(

        "NEW TRACK",

        "| mood:",
        mood,

        "| channel:",
        chat_id,

        "| message:",
        message_id

    )


# =========================================================
# START
# =========================================================

def handle_start(
    chat_id,
    user
):

    save_user(
        user
    )


    send_message(

        chat_id,

        "🎧 NOT YOUR VIBE MUSIC\n\n"

        "Welcome! 🔥\n\n"

        "Choose your mood 👇",

        mood_menu()

    )


# =========================================================
# WEBHOOK
# =========================================================

@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    update = request.get_json(
        silent=True
    )


    if not update:

        return "OK"


    # =====================================================
    # CHANNEL POST
    # =====================================================

    channel_post = update.get(
        "channel_post"
    )


    if channel_post:

        process_channel_message(
            channel_post
        )

        return "OK"


    # =====================================================
    # CALLBACK
    # =====================================================

    callback = update.get(
        "callback_query"
    )


    if callback:

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


        user = callback.get(
            "from"
        ) or {}


        chat_id = chat.get(
            "id"
        )


        user_id = user.get(
            "id"
        )


        if not chat_id or not user_id:

            answer_callback(
                callback_id,
                "Error"
            )

            return "OK"


        save_user(
            user
        )


        # -------------------------------------------------
        # MOOD
        # -------------------------------------------------

        if data.startswith(
            "mood_"
        ):

            mood = data[
                len("mood_"):
            ]


            if mood not in MOODS:

                answer_callback(
                    callback_id,
                    "Invalid mood"
                )

                return "OK"


            set_user_mood(

                user_id,

                mood

            )


            answer_callback(

                callback_id,

                f"{MOOD_NAMES[mood]} ✓"

            )


            # ---------------------------------------------
            # Send directly
            # ---------------------------------------------

            send_track(

                user_id,

                mood

            )


            return "OK"


        # -------------------------------------------------
        # NEXT
        # -------------------------------------------------

        if data == "next_music":

            answer_callback(

                callback_id,

                "🔀 Finding next..."

            )


            next_track(
                user_id
            )


            return "OK"


        # -------------------------------------------------
        # CHANGE MOOD
        # -------------------------------------------------

        if data == "change_mood":

            answer_callback(

                callback_id,

                "🎧 Choose mood"

            )


            send_message(

                user_id,

                "🎧 Choose your mood 👇",

                mood_menu()

            )


            return "OK"


        return "OK"


    # =====================================================
    # NORMAL MESSAGE
    # =====================================================

    message = update.get(
        "message"
    )


    if message:

        chat = message.get(
            "chat",
            {}
        )


        user = message.get(
            "from"
        ) or {}


        chat_id = chat.get(
            "id"
        )


        user_id = user.get(
            "id"
        )


        text = (

            message.get(
                "text",
                ""
            )

            or ""

        ).strip()


        if user:

            save_user(
                user
            )


        # -------------------------------------------------
        # START
        # -------------------------------------------------

        if text == "/start":

            handle_start(

                chat_id,

                user

            )

            return "OK"


        # -------------------------------------------------
        # MOOD
        # -------------------------------------------------

        if text == "/mood":

            send_message(

                chat_id,

                "🎧 Choose your mood 👇",

                mood_menu()

            )

            return "OK"


        # -------------------------------------------------
        # NEXT
        # -------------------------------------------------

        if text == "/next":

            next_track(
                user_id
            )

            return "OK"


        # -------------------------------------------------
        # HELP
        # -------------------------------------------------

        if text == "/help":

            send_message(

                chat_id,

                "🎧 NOT YOUR VIBE MUSIC BOT\n\n"

                "/start - Start\n"
                "/mood - Choose mood\n"
                "/next - Next track\n"
                "/help - Help\n\n"

                "Select a mood and the bot "
                "will send a random track."

            )

            return "OK"


        # -------------------------------------------------
        # STATS
        # -------------------------------------------------

        if text == "/stats":

            send_stats(
                chat_id
            )

            return "OK"


        # -------------------------------------------------
        # CHANNELS
        # -------------------------------------------------

        if text == "/channels":

            if ADMIN_ID:

                if chat_id != ADMIN_ID:

                    send_message(

                        chat_id,

                        "❌ Admin only."

                    )

                    return "OK"


            get_channel_info(
                chat_id
            )

            return "OK"


    return "OK"


# =========================================================
# HOME
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return (
        "NOT YOUR VIBE MUSIC BOT ONLINE"
    )


# =========================================================
# HEALTH
# =========================================================

@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return "OK"


# =========================================================
# SET WEBHOOK
# =========================================================

def setup_webhook():

    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN missing"
        )

        return


    if not RENDER_URL:

        print(
            "❌ RENDER_EXTERNAL_URL missing"
        )

        return


    webhook_url = (
        f"{RENDER_URL}/webhook"
    )


    result = telegram(

        "setWebhook",

        {

            "url":
                webhook_url,

            "allowed_updates": [

                "message",

                "callback_query",

                "channel_post"

            ],

            "max_connections":
                40,

            "drop_pending_updates":
                False

        },

        timeout=15

    )


    print(
        "WEBHOOK:",
        result
    )


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    print(
        "=========================================="
    )

    print(
        "🎧 NOT YOUR VIBE MUSIC BOT"
    )

    print(
        "=========================================="
    )


    # -----------------------------------------------------
    # Database
    # -----------------------------------------------------

    init_database()


    # -----------------------------------------------------
    # Check channels
    # -----------------------------------------------------

    check_channels()


    # -----------------------------------------------------
    # Track statistics
    # -----------------------------------------------------

    print(
        "TRACK DATABASE:"
    )


    track_stats = count_tracks()


    for mood in MOODS:

        print(

            MOOD_NAMES[mood],

            "=",

            track_stats.get(
                mood,
                0
            )

        )


    print(
        "TOTAL USERS:",
        count_users()
    )


    print(
        "=========================================="
    )


    # -----------------------------------------------------
    # Webhook
    # -----------------------------------------------------

    setup_webhook()


    # -----------------------------------------------------
    # PORT
    # -----------------------------------------------------

    port = int(

        os.getenv(
            "PORT",
            "10000"
        )

    )


    # -----------------------------------------------------
    # RUN
    # -----------------------------------------------------

    app.run(

        host="0.0.0.0",

        port=port,

        threaded=True

)
