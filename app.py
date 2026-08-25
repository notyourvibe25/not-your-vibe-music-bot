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

# Admin Telegram user ID
# Render Environment ထဲမှာ ADMIN_USER_ID ထည့်ပါ
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "").strip()

# Database
DB_PATH = os.getenv("DB_PATH", "music_bot.db")

# Telegram API
TELEGRAM_API = (
    f"https://api.telegram.org/bot{BOT_TOKEN}"
    if BOT_TOKEN
    else ""
)


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
    "melodic": "🌌 MELODIC",
}

MOODS = [
    "sad",
    "love",
    "chill",
    "hype",
    "dark",
    "energetic",
    "night",
    "melodic",
]


# =========================================================
# CHANNEL CONFIG
# =========================================================
#
# မင်းပေးထားတဲ့ channel တွေအတိုင်း
#
# Public channel -> @username
# Private channel -> -100xxxxxxxxxx
#
# Hype / Melodic ကို မင်းပေးထားတဲ့ ID သုံးထားတယ်။
#
# =========================================================

MOOD_CHANNELS = {

    "sad": "@sadmooddatabase",

    "love": "@lovemooddatabase",

    "chill": "@chillmooddatabase",

    "hype": "-1004427220481",

    "dark": "@darkmooddatabase",

    "energetic": "@energeticmooddatabase",

    "night": "@nightdrivemooddatabase",

    "melodic": "-1004446996297",
}


# =========================================================
# HTTP SESSION
# =========================================================

http = requests.Session()

http.headers.update({
    "User-Agent": "NOT-YOUR-VIBE-MUSIC-BOT/2.0"
})


# =========================================================
# DATABASE
# =========================================================

db_init_lock = threading.Lock()


def get_db():
    """
    Thread တစ်ခုချင်းစီအတွက် SQLite connection အသစ်ယူမယ်။
    Flask threaded mode မှာ ပိုလုံခြုံတယ်။
    """

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    # Concurrent users များရင် SQLite lock နည်းအောင်
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    return conn


def init_db():

    with db_init_lock:

        conn = get_db()

        try:

            # =================================================
            # USERS
            # =================================================

            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    first_seen INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL,
                    total_requests INTEGER DEFAULT 0
                )
            """)


            # =================================================
            # TRACKS
            # =================================================

            conn.execute("""
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    mood TEXT NOT NULL,

                    channel_id TEXT NOT NULL,

                    message_id INTEGER NOT NULL,

                    created_at INTEGER NOT NULL,

                    UNIQUE(channel_id, message_id)
                )
            """)


            # =================================================
            # USER HISTORY
            # =================================================

            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    user_id INTEGER NOT NULL,

                    mood TEXT NOT NULL,

                    message_id INTEGER NOT NULL,

                    channel_id TEXT NOT NULL,

                    sent_at INTEGER NOT NULL
                )
            """)


            # =================================================
            # INDEXES
            # =================================================

            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_tracks_mood
                ON tracks(mood)
            """)


            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_history_user
                ON user_history(user_id, sent_at DESC)
            """)


            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_history_user_mood
                ON user_history(user_id, mood, sent_at DESC)
            """)


            conn.commit()

        finally:

            conn.close()


# =========================================================
# DATABASE STARTUP
# =========================================================

init_db()


# =========================================================
# TELEGRAM REQUEST
# =========================================================

def telegram(
    method,
    data=None,
    timeout=15
):

    if not BOT_TOKEN:

        print("❌ BOT_TOKEN is missing")

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


        result = response.json()


        if not result.get("ok"):

            print(
                "Telegram API ERROR:",
                method,
                result
            )


        return result


    except Exception as e:

        print(
            "Telegram REQUEST ERROR:",
            method,
            repr(e)
        )

        return {
            "ok": False,
            "description": str(e)
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
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }


    if keyboard is not None:

        data["reply_markup"] = keyboard


    return telegram(
        "sendMessage",
        data,
        timeout=10
    )


# =========================================================
# ANSWER CALLBACK
# =========================================================

def answer_callback(
    callback_id,
    text=""
):

    return telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text,
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
            "chat_id": chat_id,
            "from_chat_id": channel_id,
            "message_id": message_id,
        },
        timeout=20
    )


# =========================================================
# USER REGISTER
# =========================================================

def register_user(user):

    if not user:
        return


    user_id = user.get("id")

    if not user_id:
        return


    username = user.get("username")
    first_name = user.get("first_name")
    last_name = user.get("last_name")

    now = int(time.time())


    conn = get_db()

    try:

        conn.execute("""
            INSERT INTO users (
                user_id,
                username,
                first_name,
                last_name,
                first_seen,
                last_seen,
                total_requests
            )

            VALUES (?, ?, ?, ?, ?, ?, 1)

            ON CONFLICT(user_id)

            DO UPDATE SET

                username = excluded.username,

                first_name = excluded.first_name,

                last_name = excluded.last_name,

                last_seen = excluded.last_seen,

                total_requests =
                    users.total_requests + 1
        """, (
            user_id,
            username,
            first_name,
            last_name,
            now,
            now
        ))


        conn.commit()

    except Exception as e:

        print(
            "REGISTER USER ERROR:",
            repr(e)
        )

    finally:

        conn.close()


# =========================================================
# SAVE NEW CHANNEL TRACK
# =========================================================

def save_channel_track(
    mood,
    channel_id,
    message_id
):

    if mood not in MOODS:
        return


    now = int(time.time())


    conn = get_db()

    try:

        conn.execute("""
            INSERT OR IGNORE INTO tracks (
                mood,
                channel_id,
                message_id,
                created_at
            )

            VALUES (?, ?, ?, ?)
        """, (
            mood,
            str(channel_id),
            message_id,
            now
        ))


        conn.commit()


        print(
            "NEW TRACK SAVED:",
            mood,
            channel_id,
            message_id
        )


    except Exception as e:

        print(
            "SAVE TRACK ERROR:",
            repr(e)
        )

    finally:

        conn.close()


# =========================================================
# FIND MOOD BY CHANNEL
# =========================================================

def mood_from_channel(channel):

    if not channel:
        return None


    channel_id = str(
        channel.get("id")
    )


    username = channel.get(
        "username"
    )


    # Numeric ID
    for mood, configured in MOOD_CHANNELS.items():

        if str(configured) == channel_id:

            return mood


    # Username
    if username:

        username = username.lower()

        for mood, configured in MOOD_CHANNELS.items():

            configured_username = str(
                configured
            ).lower().lstrip("@")

            if (
                configured_username
                == username
            ):

                return mood


    return None


# =========================================================
# PROCESS CHANNEL POST
# =========================================================

def process_channel_post(post):

    if not post:
        return


    channel = post.get(
        "chat",
        {}
    )


    mood = mood_from_channel(
        channel
    )


    if not mood:
        return


    message_id = post.get(
        "message_id"
    )


    if not message_id:
        return


    channel_id = channel.get(
        "id"
    )


    # =====================================================
    # ONLY SAVE MUSIC-LIKE POSTS
    #
    # audio
    # document
    # video
    # voice
    #
    # Text-only posts are ignored.
    # =====================================================

    has_media = any([
        post.get("audio"),
        post.get("document"),
        post.get("video"),
        post.get("voice"),
    ])


    if not has_media:

        print(
            "CHANNEL POST IGNORED:",
            mood,
            message_id,
            "(no audio/media)"
        )

        return


    save_channel_track(
        mood,
        channel_id,
        message_id
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
                    "text": "🔀 Next",
                    "callback_data": "next_music"
                }
            ],

            [
                {
                    "text": "🎧 Change Mood",
                    "callback_data": "change_mood"
                }
            ]

        ]
    }


# =========================================================
# GET TRACK COUNT
# =========================================================

def get_track_count(mood):

    conn = get_db()

    try:

        row = conn.execute("""
            SELECT COUNT(*) AS count
            FROM tracks
            WHERE mood = ?
        """, (
            mood,
        )).fetchone()


        return int(
            row["count"]
        )


    finally:

        conn.close()


# =========================================================
# GET USER RECENT TRACKS
# =========================================================

def get_recent_tracks(
    user_id,
    limit=30
):

    conn = get_db()

    try:

        rows = conn.execute("""
            SELECT message_id
            FROM user_history
            WHERE user_id = ?
            ORDER BY sent_at DESC, id DESC
            LIMIT ?
        """, (
            user_id,
            limit
        )).fetchall()


        return {
            int(row["message_id"])
            for row in rows
        }


    finally:

        conn.close()


# =========================================================
# RESERVE RANDOM TRACK
# =========================================================
#
# User တစ်ယောက်တည်းကို
# အရင်ပို့ထားတဲ့ track တွေကို ရှောင်မယ်။
#
# =========================================================

def reserve_random_track(
    user_id,
    mood
):

    channel_id = str(
        MOOD_CHANNELS[mood]
    )


    recent = get_recent_tracks(
        user_id,
        30
    )


    conn = get_db()

    try:

        # =================================================
        # First choice:
        # recent 30 tracks မပါတဲ့ track
        # =================================================

        rows = conn.execute("""
            SELECT
                message_id,
                channel_id

            FROM tracks

            WHERE mood = ?

            ORDER BY RANDOM()
        """, (
            mood,
        )).fetchall()


        candidates = []


        for row in rows:

            message_id = int(
                row["message_id"]
            )

            if message_id not in recent:

                candidates.append(
                    (
                        message_id,
                        str(row["channel_id"])
                    )
                )


        # =================================================
        # If all tracks were recently used
        # =================================================

        if not candidates:

            for row in rows:

                candidates.append(
                    (
                        int(row["message_id"]),
                        str(row["channel_id"])
                    )
                )


        if not candidates:

            return None


        # Random
        message_id, db_channel = random.choice(
            candidates
        )


        # =================================================
        # Insert history BEFORE sending
        #
        # ဒီလိုလုပ်ထားတာကြောင့်
        # user double-click လုပ်ရင်တောင်
        # တူတဲ့ track ကို reserve လုပ်ဖို့
        # အခွင့်အရေးနည်းသွားမယ်။
        # =================================================

        now = int(time.time())


        conn.execute("""
            INSERT INTO user_history (
                user_id,
                mood,
                message_id,
                channel_id,
                sent_at
            )

            VALUES (?, ?, ?, ?, ?)
        """, (
            user_id,
            mood,
            message_id,
            db_channel,
            now
        ))


        # =================================================
        # Keep database small
        #
        # User တစ်ယောက်အတွက် history 100 ထက်မပိုစေဘူး
        # =================================================

        conn.execute("""
            DELETE FROM user_history

            WHERE user_id = ?

            AND id NOT IN (

                SELECT id

                FROM user_history

                WHERE user_id = ?

                ORDER BY sent_at DESC, id DESC

                LIMIT 100
            )
        """, (
            user_id,
            user_id
        ))


        conn.commit()


        return (
            message_id,
            db_channel
        )


    except Exception as e:

        conn.rollback()

        print(
            "RESERVE TRACK ERROR:",
            repr(e)
        )

        return None


    finally:

        conn.close()


# =========================================================
# REMOVE FAILED HISTORY
# =========================================================

def remove_history(
    user_id,
    message_id
):

    conn = get_db()

    try:

        conn.execute("""
            DELETE FROM user_history

            WHERE user_id = ?

            AND message_id = ?

            AND id = (

                SELECT id

                FROM user_history

                WHERE user_id = ?

                AND message_id = ?

                ORDER BY id DESC

                LIMIT 1
            )
        """, (
            user_id,
            message_id,
            user_id,
            message_id
        ))


        conn.commit()


    except Exception as e:

        print(
            "REMOVE HISTORY ERROR:",
            repr(e)
        )

    finally:

        conn.close()


# =========================================================
# SEND TRACK
# =========================================================

def send_mood_track(
    chat_id,
    user_id,
    mood
):

    count = get_track_count(
        mood
    )


    # =====================================================
    # NO TRACK YET
    # =====================================================

    if count == 0:

        send_message(

            chat_id,

            f"{MOOD_NAMES[mood]}\n\n"
            "⚠️ ဒီ channel ထဲမှာ Bot ကသိထားတဲ့ "
            "track မရှိသေးပါ။\n\n"
            "Channel ထဲကို music အသစ်တင်ပြီး "
            "Bot ကို admin ထားထားရင် "
            "နောက်ပိုင်း auto-add ဖြစ်ပါမယ်။",

            mood_menu()

        )

        return


    # =====================================================
    # TRY MULTIPLE TRACKS
    #
    # Deleted / unavailable track ရှိရင်
    # နောက်တစ်ပုဒ်ကို ဆက်စမ်းမယ်။
    # =====================================================

    attempted = set()


    max_attempts = min(
        count,
        8
    )


    for _ in range(
        max_attempts
    ):

        reserved = reserve_random_track(
            user_id,
            mood
        )


        if not reserved:

            break


        message_id, channel_id = reserved


        if message_id in attempted:

            continue


        attempted.add(
            message_id
        )


        result = copy_music(

            chat_id,

            channel_id,

            message_id

        )


        if result.get("ok"):

            print(
                "TRACK SENT:",
                "user=",
                user_id,
                "mood=",
                mood,
                "channel=",
                channel_id,
                "message=",
                message_id
            )


            send_message(

                chat_id,

                f"{MOOD_NAMES[mood]}\n\n"
                "🎧 Enjoy your music! 🔥",

                music_buttons()

            )

            return


        # =================================================
        # Copy failed
        # history ကနေဖယ်
        # =================================================

        print(
            "COPY FAILED:",
            channel_id,
            message_id,
            result
        )


        remove_history(
            user_id,
            message_id
        )


    # =====================================================
    # NOTHING WORKED
    # =====================================================

    send_message(

        chat_id,

        f"{MOOD_NAMES[mood]}\n\n"
        "❌ ဒီ mood ထဲက track တွေကို "
        "copy လုပ်လို့မရပါ။\n\n"
        "Bot ရဲ့ channel admin permission ကိုစစ်ပါ။",

        mood_menu()

    )


# =========================================================
# BACKGROUND SEND
# =========================================================

def background_send(
    chat_id,
    user_id,
    mood
):

    try:

        send_mood_track(
            chat_id,
            user_id,
            mood
        )

    except Exception as e:

        print(
            "BACKGROUND SEND ERROR:",
            repr(e)
        )


# =========================================================
# GET USERS COUNT
# =========================================================

def get_users_count():

    conn = get_db()

    try:

        row = conn.execute("""
            SELECT COUNT(*) AS count
            FROM users
        """).fetchone()


        return int(
            row["count"]
        )

    finally:

        conn.close()


# =========================================================
# GET TRACK COUNTS
# =========================================================

def get_all_track_counts():

    conn = get_db()

    try:

        rows = conn.execute("""
            SELECT
                mood,
                COUNT(*) AS count

            FROM tracks

            GROUP BY mood
        """).fetchall()


        result = {
            mood: 0
            for mood in MOODS
        }


        for row in rows:

            result[
                row["mood"]
            ] = int(
                row["count"]
            )


        return result

    finally:

        conn.close()


# =========================================================
# ADMIN CHECK
# =========================================================

def is_admin(
    user_id
):

    if not ADMIN_USER_ID:
        return False


    return str(user_id) == str(
        ADMIN_USER_ID
    )


# =========================================================
# /users
# =========================================================

def send_user_stats(
    chat_id
):

    if not is_admin(
        chat_id
    ):

        send_message(
            chat_id,
            "❌ Admin only."
        )

        return


    users = get_users_count()

    counts = get_all_track_counts()


    lines = [
        "📊 NOT YOUR VIBE STATS",
        "",
        f"👥 Users: {users}",
        "",
        "🎵 TRACK DATABASE",
    ]


    for mood in MOODS:

        lines.append(
            f"{MOOD_NAMES[mood]} : "
            f"{counts[mood]}"
        )


    send_message(
        chat_id,
        "\n".join(lines)
    )


# =========================================================
# /stats
# =========================================================

def send_stats(
    chat_id
):

    if not is_admin(
        chat_id
    ):

        send_message(
            chat_id,
            "❌ Admin only."
        )

        return


    users = get_users_count()

    counts = get_all_track_counts()


    total_tracks = sum(
        counts.values()
    )


    text = (
        "📊 NOT YOUR VIBE MUSIC BOT\n\n"
        f"👥 Total users: {users}\n"
        f"🎵 Total tracks: {total_tracks}\n\n"
    )


    for mood in MOODS:

        text += (
            f"{MOOD_NAMES[mood]} "
            f"→ {counts[mood]}\n"
        )


    send_message(
        chat_id,
        text
    )


# =========================================================
# HOME
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return (
        "🎧 NOT YOUR VIBE MUSIC BOT ONLINE"
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
        )


        if not update:

            return "OK"


        # =================================================
        # CHANNEL POST
        #
        # အသစ်တင်တဲ့ music ကို database ထဲထည့်
        # =================================================

        channel_post = update.get(
            "channel_post"
        )


        if channel_post:

            try:

                process_channel_post(
                    channel_post
                )

            except Exception as e:

                print(
                    "CHANNEL POST ERROR:",
                    repr(e)
                )


            return "OK"


        # =================================================
        # CALLBACK
        # =================================================

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


            callback_from = (
                callback.get(
                    "from"
                )
                or {}
            )


            callback_message = (
                callback.get(
                    "message"
                )
                or {}
            )


            chat = (
                callback_message.get(
                    "chat"
                )
                or {}
            )


            chat_id = chat.get(
                "id"
            )


            user_id = callback_from.get(
                "id"
            )


            if not chat_id or not user_id:

                answer_callback(
                    callback_id,
                    "Chat error"
                )

                return "OK"


            # =================================================
            # REGISTER USER
            # =================================================

            register_user(
                callback_from
            )


            # =================================================
            # MOOD
            # =================================================

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


                # callback immediately
                answer_callback(
                    callback_id,
                    f"{MOOD_NAMES[mood]} ✓"
                )


                # music background
                threading.Thread(
                    target=background_send,
                    args=(
                        chat_id,
                        user_id,
                        mood
                    ),
                    daemon=True
                ).start()


                return "OK"


            # =================================================
            # NEXT
            # =================================================

            if data == "next_music":

                answer_callback(
                    callback_id,
                    "🔀 Finding next..."
                )


                # last selected mood ကို database history
                # ထဲကနေရယူ
                conn = get_db()

                try:

                    row = conn.execute("""
                        SELECT mood
                        FROM user_history
                        WHERE user_id = ?
                        ORDER BY sent_at DESC, id DESC
                        LIMIT 1
                    """, (
                        user_id,
                    )).fetchone()

                finally:

                    conn.close()


                if not row:

                    send_message(
                        chat_id,
                        "🎧 အရင်ဆုံး Mood ရွေးပါ 👇",
                        mood_menu()
                    )

                    return "OK"


                mood = row["mood"]


                threading.Thread(
                    target=background_send,
                    args=(
                        chat_id,
                        user_id,
                        mood
                    ),
                    daemon=True
                ).start()


                return "OK"


            # =================================================
            # CHANGE MOOD
            # =================================================

            if data == "change_mood":

                answer_callback(
                    callback_id,
                    "🎧 Choose mood"
                )


                send_message(
                    chat_id,
                    "🎧 Choose your mood 👇",
                    mood_menu()
                )


                return "OK"


            return "OK"


        # =================================================
        # NORMAL MESSAGE
        # =================================================

        message = update.get(
            "message"
        )


        if message:

            chat = message.get(
                "chat",
                {}
            )


            chat_id = chat.get(
                "id"
            )


            user = message.get(
                "from"
            )


            if not chat_id:

                return "OK"


            # Register
            register_user(
                user
            )


            text = (
                message.get(
                    "text",
                    ""
                )
                or ""
            ).strip()


            # =================================================
            # START
            # =================================================

            if text == "/start":

                send_message(

                    chat_id,

                    "🎧 NOT YOUR VIBE MUSIC\n\n"
                    "Welcome! 🔥\n\n"
                    "Mood တစ်ခုရွေးပြီး "
                    "အဲ့ဒီ mood channel ထဲက "
                    "random track ကို နားထောင်ပါ 👇",

                    mood_menu()

                )

                return "OK"


            # =================================================
            # MOOD
            # =================================================

            if text == "/mood":

                send_message(

                    chat_id,

                    "🎧 Choose your mood 👇",

                    mood_menu()

                )

                return "OK"


            # =================================================
            # NEXT
            # =================================================

            if text == "/next":

                conn = get_db()

                try:

                    row = conn.execute("""
                        SELECT mood
                        FROM user_history
                        WHERE user_id = ?
                        ORDER BY sent_at DESC, id DESC
                        LIMIT 1
                    """, (
                        chat_id,
                    )).fetchone()

                finally:

                    conn.close()


                if not row:

                    send_message(
                        chat_id,
                        "🎧 အရင်ဆုံး Mood ရွေးပါ 👇",
                        mood_menu()
                    )

                    return "OK"


                mood = row["mood"]


                threading.Thread(
                    target=background_send,
                    args=(
                        chat_id,
                        chat_id,
                        mood
                    ),
                    daemon=True
                ).start()


                return "OK"


            # =================================================
            # USERS
            # =================================================

            if text == "/users":

                send_user_stats(
                    chat_id
                )

                return "OK"


            # =================================================
            # STATS
            # =================================================

            if text == "/stats":

                send_stats(
                    chat_id
                )

                return "OK"


            # =================================================
            # HELP
            # =================================================

            if text == "/help":

                send_message(

                    chat_id,

                    "🎧 NOT YOUR VIBE MUSIC BOT\n\n"

                    "/start → Start\n"
                    "/mood → Mood menu\n"
                    "/next → Next track\n"
                    "/help → Help\n\n"

                    "🎵 Mood တစ်ခုရွေးရင် "
                    "အဲ့ဒီ mood channel ထဲက "
                    "track ကို random ပို့ပေးပါတယ်။"

                )

                return "OK"


        return "OK"


    except Exception as e:

        # =====================================================
        # IMPORTANT
        #
        # Webhook ကို 500 မပြန်စေဖို့
        # exception ကို catch လုပ်ထားတယ်။
        # =====================================================

        print(
            "WEBHOOK FATAL ERROR:",
            repr(e)
        )

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
        RENDER_URL.rstrip("/")
        + "/webhook"
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

            "max_connections": 40,

            "drop_pending_updates": True
        },

        timeout=20

    )


    print(
        "WEBHOOK RESULT:",
        result
    )


# =========================================================
# WEBHOOK INFO
# =========================================================

def print_webhook_info():

    result = telegram(
        "getWebhookInfo",
        {},
        timeout=10
    )


    print(
        "WEBHOOK INFO:",
        result
    )


# =========================================================
# START
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

    print(
        "Database:",
        DB_PATH
    )

    print(
        "Webhook:",
        RENDER_URL
    )

    print(
        "=========================================="
    )


    # Database
    init_db()


    # Webhook
    setup_webhook()


    # Debug
    print_webhook_info()


    print(
        "=========================================="
    )

    print(
        "BOT IS READY"
    )

    print(
        "=========================================="
    )


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
