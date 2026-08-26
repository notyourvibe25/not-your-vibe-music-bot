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

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

RENDER_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    ""
).strip()

ADMIN_USER_ID = os.getenv(
    "ADMIN_USER_ID",
    ""
).strip()


# =========================================================
# DATABASE PATH
# =========================================================
#
# Priority:
#
# 1. DB_PATH environment variable
# 2. /data if writable
# 3. /tmp fallback
# 4. local music_bot.db
#
# ဒီလိုလုပ်ထားလို့ /data permission error ကြောင့်
# Bot မစတင်နိုင်တာ မဖြစ်တော့ဘူး။
# =========================================================

def choose_db_path():

    custom_path = os.getenv(
        "DB_PATH",
        ""
    ).strip()

    if custom_path:
        directory = os.path.dirname(
            custom_path
        )

        if directory:
            try:
                os.makedirs(
                    directory,
                    exist_ok=True
                )

                test_file = os.path.join(
                    directory,
                    ".write_test"
                )

                with open(
                    test_file,
                    "w"
                ) as f:
                    f.write("ok")

                os.remove(
                    test_file
                )

                return custom_path

            except Exception as e:

                print(
                    "⚠️ Custom DB_PATH not writable:",
                    custom_path,
                    repr(e)
                )


    # =====================================================
    # Try /data
    # =====================================================

    data_dir = "/data"

    try:

        os.makedirs(
            data_dir,
            exist_ok=True
        )

        test_file = os.path.join(
            data_dir,
            ".write_test"
        )

        with open(
            test_file,
            "w"
        ) as f:
            f.write("ok")

        os.remove(
            test_file
        )

        print(
            "✅ Using persistent /data storage"
        )

        return "/data/notyourvibe.db"

    except Exception as e:

        print(
            "⚠️ /data is not writable:",
            repr(e)
        )


    # =====================================================
    # Try /tmp
    # =====================================================

    tmp_path = "/tmp/notyourvibe.db"

    try:

        test_file = "/tmp/.notyourvibe_test"

        with open(
            test_file,
            "w"
        ) as f:
            f.write("ok")

        os.remove(
            test_file
        )

        print(
            "⚠️ Using /tmp database."
        )

        print(
            "⚠️ Render restart/redeploy may erase database."
        )

        return tmp_path

    except Exception as e:

        print(
            "⚠️ /tmp unavailable:",
            repr(e)
        )


    # =====================================================
    # Last fallback
    # =====================================================

    return "music_bot.db"


DB_PATH = choose_db_path()


# =========================================================
# TELEGRAM API
# =========================================================

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

MOOD_CHANNELS = {

    "sad":
        "@sadmooddatabase",

    "love":
        "@lovemooddatabase",

    "chill":
        "@chillmooddatabase",

    "hype":
        "-1004427220481",

    "dark":
        "@darkmooddatabase",

    "energetic":
        "@energeticmooddatabase",

    "night":
        "@nightdrivemooddatabase",

    "melodic":
        "-1004446996297",
}


# =========================================================
# HTTP
# =========================================================

http = requests.Session()

http.headers.update({
    "User-Agent":
        "NOT-YOUR-VIBE-MUSIC-BOT/3.0"
})


# =========================================================
# LOCKS
# =========================================================

db_init_lock = threading.Lock()

reserve_lock = threading.Lock()


# =========================================================
# DATABASE
# =========================================================

def get_db():

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA busy_timeout = 30000"
    )

    conn.execute(
        "PRAGMA foreign_keys = ON"
    )

    return conn


def init_db():

    with db_init_lock:

        conn = get_db()

        try:

            # =================================================
            # WAL
            # =================================================

            try:

                conn.execute(
                    "PRAGMA journal_mode=WAL"
                )

            except Exception as e:

                print(
                    "WAL warning:",
                    repr(e)
                )


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

                    total_requests INTEGER NOT NULL
                    DEFAULT 0
                )
            """)


            # =================================================
            # USER STATE
            #
            # User တစ်ယောက်ချင်းစီရဲ့
            # လက်ရှိရွေးထားတဲ့ mood
            # =================================================

            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_state (

                    user_id INTEGER PRIMARY KEY,

                    selected_mood TEXT,

                    updated_at INTEGER NOT NULL
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
                idx_tracks_mood_channel
                ON tracks(mood, channel_id)
            """)


            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_history_user
                ON user_history(
                    user_id,
                    sent_at DESC
                )
            """)


            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_history_user_mood
                ON user_history(
                    user_id,
                    mood,
                    sent_at DESC
                )
            """)


            conn.commit()


        except Exception as e:

            print(
                "❌ DATABASE INIT ERROR:",
                repr(e)
            )

            raise

        finally:

            conn.close()


# =========================================================
# TELEGRAM REQUEST
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
            "description":
                "BOT_TOKEN missing"
        }


    try:

        response = http.post(

            f"{TELEGRAM_API}/{method}",

            json=data or {},

            timeout=timeout
        )


        try:

            result = response.json()

        except Exception:

            return {
                "ok": False,
                "description":
                    "Invalid Telegram response"
            }


        if not result.get("ok"):

            print(
                "Telegram API ERROR:",
                method,
                result
            )


        return result


    except requests.RequestException as e:

        print(
            "Telegram NETWORK ERROR:",
            method,
            repr(e)
        )

        return {
            "ok": False,
            "description": str(e)
        }


    except Exception as e:

        print(
            "Telegram ERROR:",
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

        "chat_id":
            chat_id,

        "text":
            text,

        "disable_web_page_preview":
            True,
    }


    if keyboard is not None:

        data[
            "reply_markup"
        ] = keyboard


    return telegram(
        "sendMessage",
        data,
        timeout=15
    )


# =========================================================
# ANSWER CALLBACK
# =========================================================

def answer_callback(
    callback_id,
    text=""
):

    if not callback_id:

        return


    return telegram(

        "answerCallbackQuery",

        {
            "callback_query_id":
                callback_id,

            "text":
                text,

            "show_alert":
                False
        },

        timeout=5
    )


# =========================================================
# COPY MUSIC
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
                message_id,
        },

        timeout=30
    )


# =========================================================
# REGISTER USER
# =========================================================

def register_user(user):

    if not user:

        return


    user_id = user.get("id")

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

                username =
                    excluded.username,

                first_name =
                    excluded.first_name,

                last_name =
                    excluded.last_name,

                last_seen =
                    excluded.last_seen,

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
# SET USER MOOD
# =========================================================

def set_user_mood(
    user_id,
    mood
):

    if mood not in MOODS:

        return False


    now = int(
        time.time()
    )


    conn = get_db()

    try:

        conn.execute("""
            INSERT INTO user_state (

                user_id,

                selected_mood,

                updated_at

            )

            VALUES (?, ?, ?)

            ON CONFLICT(user_id)

            DO UPDATE SET

                selected_mood =
                    excluded.selected_mood,

                updated_at =
                    excluded.updated_at
        """, (

            user_id,

            mood,

            now
        ))


        conn.commit()

        return True


    except Exception as e:

        print(
            "SET USER MOOD ERROR:",
            repr(e)
        )

        return False


    finally:

        conn.close()


# =========================================================
# GET USER MOOD
# =========================================================

def get_user_mood(
    user_id
):

    conn = get_db()

    try:

        row = conn.execute("""
            SELECT selected_mood

            FROM user_state

            WHERE user_id = ?

            LIMIT 1
        """, (

            user_id,
        )).fetchone()


        if not row:

            return None


        mood = row[
            "selected_mood"
        ]


        if mood not in MOODS:

            return None


        return mood


    finally:

        conn.close()


# =========================================================
# SAVE CHANNEL TRACK
# =========================================================

def save_channel_track(
    mood,
    channel_id,
    message_id
):

    if mood not in MOODS:

        return False


    if not message_id:

        return False


    now = int(
        time.time()
    )


    conn = get_db()

    try:

        cursor = conn.execute("""
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

            int(message_id),

            now
        ))


        conn.commit()


        if cursor.rowcount:

            print(
                "✅ NEW TRACK:",
                MOOD_NAMES[mood],
                channel_id,
                message_id
            )

            return True


        return False


    except Exception as e:

        print(
            "SAVE TRACK ERROR:",
            repr(e)
        )

        return False


    finally:

        conn.close()


# =========================================================
# FIND MOOD BY CHANNEL
# =========================================================

def mood_from_channel(
    channel
):

    if not channel:

        return None


    channel_id = str(
        channel.get("id", "")
    )


    username = (
        channel.get("username")
        or ""
    ).lower().lstrip("@").strip()


    for mood, configured in \
        MOOD_CHANNELS.items():

        configured_text = str(
            configured
        ).strip()


        # Numeric ID
        if configured_text == channel_id:

            return mood


        # Username
        configured_username = (
            configured_text
            .lower()
            .lstrip("@")
            .strip()
        )


        if username and (
            configured_username
            == username
        ):

            return mood


    return None


# =========================================================
# PROCESS CHANNEL POST
# =========================================================

def process_channel_post(
    post
):

    if not post:

        return


    channel = post.get(
        "chat"
    ) or {}


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
    # MUSIC / MEDIA ONLY
    # =====================================================

    has_media = any([

        post.get("audio"),

        post.get("document"),

        post.get("video"),

        post.get("voice"),

    ])


    if not has_media:

        print(
            "ℹ️ Ignored non-media post:",
            mood,
            message_id
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
                    "text":
                        "😢 Sad",

                    "callback_data":
                        "mood_sad"
                },

                {
                    "text":
                        "❤️ Love",

                    "callback_data":
                        "mood_love"
                }

            ],

            [

                {
                    "text":
                        "🌙 Chill",

                    "callback_data":
                        "mood_chill"
                },

                {
                    "text":
                        "🔥 Hype",

                    "callback_data":
                        "mood_hype"
                }

            ],

            [

                {
                    "text":
                        "🖤 Dark",

                    "callback_data":
                        "mood_dark"
                },

                {
                    "text":
                        "⚡ Energetic",

                    "callback_data":
                        "mood_energetic"
                }

            ],

            [

                {
                    "text":
                        "🚗 Night Drive",

                    "callback_data":
                        "mood_night"
                },

                {
                    "text":
                        "🌌 Melodic",

                    "callback_data":
                        "mood_melodic"
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
# TRACK COUNT
# =========================================================

def get_track_count(
    mood
):

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
# RECENT HISTORY
# =========================================================

def get_recent_tracks(
    user_id,
    mood,
    limit=50
):

    conn = get_db()

    try:

        rows = conn.execute("""
            SELECT message_id

            FROM user_history

            WHERE user_id = ?

            AND mood = ?

            ORDER BY
                sent_at DESC,
                id DESC

            LIMIT ?
        """, (

            user_id,

            mood,

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
# Important:
#
# Transaction + lock သုံးထားလို့
# concurrent Next requests တွေမှာ
# race condition လျော့မယ်။
#
# =========================================================

def reserve_random_track(
    user_id,
    mood
):

    if mood not in MOODS:

        return None


    with reserve_lock:

        conn = get_db()

        try:

            conn.execute(
                "BEGIN IMMEDIATE"
            )


            recent = get_recent_tracks(
                user_id,
                mood,
                50
            )


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


            if not rows:

                conn.rollback()

                return None


            candidates = [

                (

                    int(row["message_id"]),

                    str(row["channel_id"])

                )

                for row in rows

                if int(
                    row["message_id"]
                ) not in recent

            ]


            # =================================================
            # All recently used
            #
            # Track အကုန်သုံးပြီးသွားရင်
            # database ထဲမှာရှိတဲ့ track တွေထဲက
            # random ပြန်ရွေးမယ်။
            # =================================================

            if not candidates:

                candidates = [

                    (

                        int(
                            row["message_id"]
                        ),

                        str(
                            row["channel_id"]
                        )

                    )

                    for row in rows
                ]


            if not candidates:

                conn.rollback()

                return None


            message_id, channel_id = \
                random.choice(
                    candidates
                )


            now = int(
                time.time()
            )


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

                channel_id,

                now
            ))


            # =================================================
            # Keep latest 100 history
            # =================================================

            conn.execute("""
                DELETE FROM user_history

                WHERE user_id = ?

                AND id NOT IN (

                    SELECT id

                    FROM user_history

                    WHERE user_id = ?

                    ORDER BY
                        sent_at DESC,
                        id DESC

                    LIMIT 100
                )
            """, (

                user_id,

                user_id
            ))


            conn.commit()


            return (
                message_id,
                channel_id
            )


        except sqlite3.OperationalError as e:

            try:
                conn.rollback()
            except Exception:
                pass


            print(
                "RESERVE SQLITE ERROR:",
                repr(e)
            )

            return None


        except Exception as e:

            try:
                conn.rollback()
            except Exception:
                pass


            print(
                "RESERVE TRACK ERROR:",
                repr(e)
            )

            return None


        finally:

            conn.close()


# =========================================================
# REMOVE HISTORY
# =========================================================

def remove_history(
    user_id,
    message_id
):

    conn = get_db()

    try:

        conn.execute("""
            DELETE FROM user_history

            WHERE id = (

                SELECT id

                FROM user_history

                WHERE user_id = ?

                AND message_id = ?

                ORDER BY id DESC

                LIMIT 1
            )
        """, (

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
# SEND MOOD TRACK
# =========================================================

def send_mood_track(
    chat_id,
    user_id,
    mood
):

    if mood not in MOODS:

        send_message(

            chat_id,

            "❌ Invalid mood.",

            mood_menu()

        )

        return


    count = get_track_count(
        mood
    )


    # =====================================================
    # NO TRACK
    # =====================================================

    if count <= 0:

        send_message(

            chat_id,

            f"{MOOD_NAMES[mood]}\n\n"

            "⚠️ ဒီ mood ထဲမှာ "
            "Bot သိထားတဲ့ track မရှိသေးပါ။\n\n"

            "Channel ထဲကို music တင်ပြီး "
            "Bot ကို channel admin ထားပေးပါ။",

            mood_menu()

        )

        return


    attempted = set()


    # Maximum 8 attempts
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


        message_id, channel_id = \
            reserved


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

                "✅ TRACK SENT |",

                "user=",
                user_id,

                "| mood=",
                mood,

                "| channel=",
                channel_id,

                "| message=",
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
        # =================================================

        print(

            "⚠️ COPY FAILED |",

            channel_id,

            message_id,

            result
        )


        remove_history(

            user_id,

            message_id
        )


    # =====================================================
    # Nothing worked
    # =====================================================

    send_message(

        chat_id,

        f"{MOOD_NAMES[mood]}\n\n"

        "❌ Track ပို့လို့မရပါ။\n\n"

        "Bot က ဒီ channel တွေမှာ "
        "admin ဖြစ်/မဖြစ် စစ်ပေးပါ။",

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
# USERS COUNT
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
# TRACK COUNTS
# =========================================================

def get_all_track_counts():

    result = {
        mood: 0
        for mood in MOODS
    }


    conn = get_db()

    try:

        rows = conn.execute("""
            SELECT

                mood,

                COUNT(*) AS count

            FROM tracks

            GROUP BY mood
        """).fetchall()


        for row in rows:

            mood = row["mood"]


            if mood in result:

                result[mood] = int(
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


    try:

        return int(user_id) == int(
            ADMIN_USER_ID
        )

    except Exception:

        return False


# =========================================================
# ADMIN STATS
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


    lines = [

        "📊 NOT YOUR VIBE MUSIC BOT",

        "",

        f"👥 Total users: {users}",

        f"🎵 Total tracks: {total_tracks}",

        "",

        "MOOD DATABASE",

    ]


    for mood in MOODS:

        lines.append(

            f"{MOOD_NAMES[mood]} "
            f"→ {counts[mood]}"

        )


    send_message(

        chat_id,

        "\n".join(lines)

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

            return "OK", 200


        # =================================================
        # CHANNEL POST
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


            return "OK", 200


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
                callback.get("from")
                or {}
            )


            callback_message = (
                callback.get("message")
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

                return "OK", 200


            register_user(
                callback_from
            )


            # =================================================
            # MOOD SELECT
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

                    return "OK", 200


                # Save selected mood
                set_user_mood(

                    user_id,

                    mood
                )


                answer_callback(

                    callback_id,

                    f"{MOOD_NAMES[mood]} ✓"

                )


                threading.Thread(

                    target=background_send,

                    args=(

                        chat_id,

                        user_id,

                        mood

                    ),

                    daemon=True

                ).start()


                return "OK", 200


            # =================================================
            # NEXT
            # =================================================

            if data == "next_music":

                answer_callback(

                    callback_id,

                    "🔀 Finding next..."

                )


                mood = get_user_mood(
                    user_id
                )


                if not mood:

                    send_message(

                        chat_id,

                        "🎧 အရင်ဆုံး Mood ရွေးပါ 👇",

                        mood_menu()

                    )

                    return "OK", 200


                threading.Thread(

                    target=background_send,

                    args=(

                        chat_id,

                        user_id,

                        mood

                    ),

                    daemon=True

                ).start()


                return "OK", 200


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


                return "OK", 200


            return "OK", 200


        # =================================================
        # NORMAL MESSAGE
        # =================================================

        message = update.get(
            "message"
        )


        if message:

            chat = (
                message.get("chat")
                or {}
            )


            chat_id = chat.get(
                "id"
            )


            user = message.get(
                "from"
            )


            if not chat_id:

                return "OK", 200


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

                    "Mood တစ်ခုရွေးပါ။\n"

                    "ရွေးထားတဲ့ mood ထဲက "
                    "random music ပို့ပေးမယ် 👇",

                    mood_menu()

                )

                return "OK", 200


            # =================================================
            # MOOD
            # =================================================

            if text == "/mood":

                send_message(

                    chat_id,

                    "🎧 Choose your mood 👇",

                    mood_menu()

                )

                return "OK", 200


            # =================================================
            # NEXT
            # =================================================

            if text == "/next":

                mood = get_user_mood(
                    chat_id
                )


                if not mood:

                    send_message(

                        chat_id,

                        "🎧 အရင်ဆုံး Mood ရွေးပါ 👇",

                        mood_menu()

                    )

                    return "OK", 200


                threading.Thread(

                    target=background_send,

                    args=(

                        chat_id,

                        chat_id,

                        mood

                    ),

                    daemon=True

                ).start()


                return "OK", 200


            # =================================================
            # USERS
            # =================================================

            if text == "/users":

                send_stats(
                    chat_id
                )

                return "OK", 200


            # =================================================
            # STATS
            # =================================================

            if text == "/stats":

                send_stats(
                    chat_id
                )

                return "OK", 200


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
                    "/stats → Admin stats\n"
                    "/users → Admin user count\n"
                    "/help → Help"

                )

                return "OK", 200


        return "OK", 200


    except Exception as e:

        # =====================================================
        # NEVER RETURN 500
        # =====================================================

        print(
            "🔥 WEBHOOK FATAL ERROR:",
            repr(e)
        )


        return "OK", 200


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


    print(
        "🔗 Setting webhook:",
        webhook_url
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
                True
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
# STARTUP VALIDATION
# =========================================================

def startup_check():

    print(
        "=========================================="
    )

    print(
        "🎧 NOT YOUR VIBE MUSIC BOT"
    )

    print(
        "=========================================="
    )


    # BOT TOKEN
    if BOT_TOKEN:

        print(
            "✅ BOT_TOKEN: configured"
        )

    else:

        print(
            "❌ BOT_TOKEN: MISSING"
        )


    # ADMIN
    if ADMIN_USER_ID:

        try:

            int(ADMIN_USER_ID)

            print(
                "✅ ADMIN_USER_ID: configured"
            )

        except ValueError:

            print(
                "⚠️ ADMIN_USER_ID is not numeric"
            )

    else:

        print(
            "⚠️ ADMIN_USER_ID: not configured"
        )

        print(
            "⚠️ Admin commands will be disabled."
        )


    # RENDER URL
    if RENDER_URL:

        print(
            "✅ RENDER_EXTERNAL_URL:",
            RENDER_URL
        )

    else:

        print(
            "⚠️ RENDER_EXTERNAL_URL missing"
        )


    # DATABASE
    print(
        "💾 DATABASE:",
        DB_PATH
    )


    print(
        "=========================================="
    )


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    startup_check()


    # =====================================================
    # DATABASE
    # =====================================================

    init_db()


    # =====================================================
    # WEBHOOK
    # =====================================================

    setup_webhook()


    # =====================================================
    # WEBHOOK INFO
    # =====================================================

    print_webhook_info()


    print(
        "=========================================="
    )

    print(
        "🚀 BOT IS READY"
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
