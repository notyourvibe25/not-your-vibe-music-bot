import os
import time
import random
import sqlite3
import threading
import asyncio

import requests
from flask import Flask, request

from telethon import TelegramClient
from telethon.sessions import StringSession


# ============================================================
# APP
# ============================================================

app = Flask(__name__)


# ============================================================
# ENVIRONMENT
# ============================================================

BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    ""
).strip()

ADMIN_USER_ID = os.getenv(
    "ADMIN_USER_ID",
    ""
).strip()

RENDER_EXTERNAL_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    ""
).strip()


# ============================================================
# TELETHON ENVIRONMENT
# ============================================================

TELETHON_API_ID = (
    os.getenv("TELETHON_API_ID")
    or os.getenv("API_ID")
    or ""
).strip()

TELETHON_API_HASH = (
    os.getenv("TELETHON_API_HASH")
    or os.getenv("API_HASH")
    or ""
).strip()

TELETHON_SESSION = os.getenv(
    "TELETHON_SESSION",
    ""
).strip()


# ============================================================
# DATABASE
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

DB_PATH = os.path.join(
    BASE_DIR,
    "music_bot.db"
)


# ============================================================
# MOODS
# ============================================================

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


# ============================================================
# CHANNELS
# ============================================================

MOOD_CHANNELS = {

    "sad": os.getenv(
        "SAD_CHANNEL",
        ""
    ).strip(),

    "love": os.getenv(
        "LOVE_CHANNEL",
        ""
    ).strip(),

    "chill": os.getenv(
        "CHILL_CHANNEL",
        ""
    ).strip(),

    "hype": os.getenv(
        "HYPE_CHANNEL",
        ""
    ).strip(),

    "dark": os.getenv(
        "DARK_CHANNEL",
        ""
    ).strip(),

    "energetic": os.getenv(
        "ENERGETIC_CHANNEL",
        ""
    ).strip(),

    "night": os.getenv(
        "NIGHT_CHANNEL",
        ""
    ).strip(),

    "melodic": os.getenv(
        "MELODIC_CHANNEL",
        ""
    ).strip(),
}


# ============================================================
# HTTP SESSION
# ============================================================

http = requests.Session()

http.headers.update({
    "User-Agent": "NOT-YOUR-VIBE-MUSIC-BOT/6.0"
})


# ============================================================
# LOCKS
# ============================================================

db_init_lock = threading.Lock()

user_locks = {}

user_locks_lock = threading.Lock()


# ============================================================
# TELETHON CLIENT
# ============================================================

telethon_client = None


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA busy_timeout=30000"
    )

    return conn


# ============================================================
# INITIALIZE DATABASE
# ============================================================

def init_db():

    with db_init_lock:

        conn = get_db()

        try:

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


            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_history (

                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    user_id INTEGER NOT NULL,

                    mood TEXT NOT NULL,

                    channel_id TEXT NOT NULL,

                    message_id INTEGER NOT NULL,

                    sent_at INTEGER NOT NULL
                )
            """)


            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_state (

                    user_id INTEGER PRIMARY KEY,

                    mood TEXT,

                    updated_at INTEGER NOT NULL
                )
            """)


            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_tracks_mood

                ON tracks(mood)
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

        finally:

            conn.close()


# ============================================================
# TELEGRAM API
# ============================================================

def telegram(
    method,
    data=None,
    timeout=20
):

    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN missing"
        )

        return {
            "ok": False,
            "description": "BOT_TOKEN missing"
        }


    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )


    try:

        response = http.post(
            url,
            json=data or {},
            timeout=timeout
        )


        try:

            result = response.json()

        except Exception:

            result = {
                "ok": False,
                "description": response.text
            }


        if not result.get("ok"):

            print(
                "⚠️ Telegram API ERROR:",
                method,
                result
            )


        return result


    except Exception as exc:

        print(
            "⚠️ Telegram REQUEST ERROR:",
            method,
            repr(exc)
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
    keyboard=None
):

    data = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True
    }


    if keyboard is not None:

        data["reply_markup"] = keyboard


    return telegram(
        "sendMessage",
        data,
        timeout=15
    )


# ============================================================
# CALLBACK ANSWER
# ============================================================

def answer_callback(
    callback_id,
    text=""
):

    return telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text
        },
        timeout=8
    )


# ============================================================
# COPY MUSIC
# ============================================================

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
            "message_id": message_id
        },
        timeout=30
    )


# ============================================================
# USER LOCK
# ============================================================

def get_user_lock(user_id):

    with user_locks_lock:

        if user_id not in user_locks:

            user_locks[user_id] = threading.Lock()


        return user_locks[user_id]


# ============================================================
# REGISTER USER
# ============================================================

def register_user(user):

    if not user:
        return


    user_id = user.get("id")

    if not user_id:
        return


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

                username = excluded.username,

                first_name = excluded.first_name,

                last_name = excluded.last_name,

                last_seen = excluded.last_seen,

                total_requests =
                    users.total_requests + 1

        """, (

            user_id,

            user.get("username"),

            user.get("first_name"),

            user.get("last_name"),

            now,

            now

        ))


        conn.commit()


    except Exception as exc:

        print(
            "REGISTER USER ERROR:",
            repr(exc)
        )


    finally:

        conn.close()


# ============================================================
# SAVE TRACK
# ============================================================

def save_track(
    mood,
    channel_id,
    message_id
):

    if mood not in MOODS:
        return


    if not channel_id:
        return


    if not message_id:
        return


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

            int(message_id),

            int(time.time())

        ))


        conn.commit()


    except Exception as exc:

        print(
            "SAVE TRACK ERROR:",
            repr(exc)
        )


    finally:

        conn.close()


# ============================================================
# SAVE TELETHON MESSAGE
# ============================================================

def save_telethon_message(
    mood,
    entity,
    message
):

    if not message:
        return


    message_id = getattr(
        message,
        "id",
        None
    )


    if not message_id:
        return


    if not getattr(
        message,
        "media",
        None
    ):

        return


    entity_id = getattr(
        entity,
        "id",
        None
    )


    if not entity_id:
        return


    entity_id = str(
        entity_id
    )


    if entity_id.startswith("-100"):

        channel_id = entity_id

    else:

        channel_id = "-100" + entity_id


    save_track(
        mood,
        channel_id,
        message_id
    )


# ============================================================
# SCAN ONE CHANNEL
# ============================================================

async def scan_one_channel(
    mood,
    channel_value
):

    print("")
    print(
        "=========================================="
    )

    print(
        f"🔎 Scanning {mood.upper()}..."
    )

    print(
        f"📌 Channel: {channel_value}"
    )


    if not channel_value:

        print(
            f"⚠️ {mood.upper()} channel missing"
        )

        return 0


    if telethon_client is None:

        print(
            "❌ Telethon client unavailable"
        )

        return 0


    try:

        print(
            f"🔗 Getting {mood.upper()} channel..."
        )


        entity = await telethon_client.get_entity(
            channel_value
        )


        print(
            f"✅ {mood.upper()} channel found"
        )


        count = 0


        async for message in telethon_client.iter_messages(
            entity
        ):

            if not message:

                continue


            if not getattr(
                message,
                "media",
                None
            ):

                continue


            save_telethon_message(
                mood,
                entity,
                message
            )


            count += 1


            if count % 100 == 0:

                print(
                    f"🎵 {mood.upper()}: "
                    f"{count} tracks found..."
                )


        print(
            f"✅ {mood.upper()} scanned: {count}"
        )


        return count


    except Exception as exc:

        print(
            f"❌ {mood.upper()} SCAN ERROR:",
            repr(exc)
        )

        return 0


# ============================================================
# SCAN ALL CHANNELS
# ============================================================

async def scan_all_channels():

    print("")
    print(
        "##########################################"
    )

    print(
        "🎵 STARTING ALL MOOD CHANNEL SCAN"
    )

    print(
        "##########################################"
    )


    total = 0


    for mood in MOODS:

        try:

            count = await scan_one_channel(
                mood,
                MOOD_CHANNELS.get(
                    mood,
                    ""
                )
            )


            total += count


        except Exception as exc:

            print(
                "⚠️ CHANNEL SCAN ERROR:",
                mood,
                repr(exc)
            )


    print("")
    print(
        "##########################################"
    )

    print(
        f"🎵 TOTAL TRACKS SCANNED: {total}"
    )

    print(
        "##########################################"
    )


# ============================================================
# TELETHON WORKER
# ============================================================

def telethon_worker():

    global telethon_client


    print("")
    print(
        "=========================================="
    )

    print(
        "🔐 TELETHON WORKER STARTING"
    )

    print(
        "=========================================="
    )


    # --------------------------------------------------------
    # API ID
    # --------------------------------------------------------

    if not TELETHON_API_ID:

        print(
            "❌ TELETHON_API_ID missing"
        )

        return


    # --------------------------------------------------------
    # API HASH
    # --------------------------------------------------------

    if not TELETHON_API_HASH:

        print(
            "❌ TELETHON_API_HASH missing"
        )

        return


    # --------------------------------------------------------
    # SESSION
    # --------------------------------------------------------

    if not TELETHON_SESSION:

        print(
            "❌ TELETHON_SESSION missing"
        )

        return


    print(
        "✅ TELETHON_API_ID found"
    )

    print(
        "✅ TELETHON_API_HASH found"
    )

    print(
        "✅ TELETHON_SESSION found"
    )


    try:

        api_id = int(
            TELETHON_API_ID
        )


        print(
            "🔐 Creating Telegram client..."
        )


        telethon_client = TelegramClient(

            StringSession(
                TELETHON_SESSION
            ),

            api_id,

            TELETHON_API_HASH,

            connection_retries=5,

            retry_delay=5,

            timeout=20

        )


        print(
            "🔐 Telegram client created"
        )


        async def runner():

            print(
                "🔌 Connecting to Telegram..."
            )


            await telethon_client.connect()


            print(
                "🔌 Telegram connection established"
            )


            authorized = await telethon_client.is_user_authorized()


            if not authorized:

                print(
                    "❌ TELETHON SESSION NOT AUTHORIZED"
                )

                print(
                    "⚠️ Create a new TELETHON_SESSION"
                )

                await telethon_client.disconnect()

                return


            print(
                "✅ TELETHON LOGIN SUCCESS"
            )


            # ------------------------------------------------
            # GET ACCOUNT
            # ------------------------------------------------

            try:

                me = await telethon_client.get_me()

                if me:

                    username = getattr(
                        me,
                        "username",
                        None
                    )

                    first_name = getattr(
                        me,
                        "first_name",
                        None
                    )

                    print(
                        "👤 Telegram account:",
                        first_name,
                        username
                    )

            except Exception as exc:

                print(
                    "⚠️ Could not get account:",
                    repr(exc)
                )


            # ------------------------------------------------
            # SCAN
            # ------------------------------------------------

            print(
                "🔎 Starting channel scan..."
            )


            await scan_all_channels()


            print(
                "📡 Telegram account watcher ready"
            )


            # ------------------------------------------------
            # KEEP CONNECTION ALIVE
            # ------------------------------------------------

            print(
                "💓 Telethon waiting for updates..."
            )


            await telethon_client.run_until_disconnected()


        asyncio.run(
            runner()
        )


    except Exception as exc:

        print(
            "❌ TELETHON WORKER ERROR:",
            repr(exc)
        )

        print(
            "🔄 Telethon worker stopped"
        )

        time.sleep(10)


# ============================================================
# MOOD MENU
# ============================================================

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


# ============================================================
# MUSIC BUTTONS
# ============================================================

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


# ============================================================
# TRACK COUNT
# ============================================================

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


# ============================================================
# RECENT TRACKS
# ============================================================

def get_recent_tracks(
    user_id,
    mood,
    limit=30
):

    conn = get_db()

    try:

        rows = conn.execute("""
            SELECT message_id

            FROM user_history

            WHERE user_id = ?

            AND mood = ?

            ORDER BY sent_at DESC, id DESC

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


# ============================================================
# SET USER MOOD
# ============================================================

def set_user_mood(
    user_id,
    mood
):

    conn = get_db()

    try:

        conn.execute("""
            INSERT INTO user_state (

                user_id,
                mood,
                updated_at

            )

            VALUES (?, ?, ?)

            ON CONFLICT(user_id)

            DO UPDATE SET

                mood = excluded.mood,

                updated_at = excluded.updated_at

        """, (

            user_id,

            mood,

            int(time.time())

        ))


        conn.commit()


    finally:

        conn.close()


# ============================================================
# GET USER MOOD
# ============================================================

def get_user_mood(user_id):

    conn = get_db()

    try:

        row = conn.execute("""
            SELECT mood

            FROM user_state

            WHERE user_id = ?

        """, (
            user_id,
        )).fetchone()


        if not row:

            return None


        return row["mood"]


    finally:

        conn.close()


# ============================================================
# RESERVE TRACK
# ============================================================

def reserve_track(
    user_id,
    mood
):

    lock = get_user_lock(
        user_id
    )


    with lock:

        conn = get_db()

        try:

            recent = get_recent_tracks(
                user_id,
                mood,
                30
            )


            rows = conn.execute("""
                SELECT

                    message_id,

                    channel_id

                FROM tracks

                WHERE mood = ?

                ORDER BY RANDOM()

                LIMIT 100

            """, (
                mood,
            )).fetchall()


            if not rows:

                return None


            candidates = []


            for row in rows:

                message_id = int(
                    row["message_id"]
                )


                if message_id not in recent:

                    candidates.append(

                        (
                            message_id,

                            str(
                                row["channel_id"]
                            )
                        )

                    )


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


            message_id, channel_id = random.choice(
                candidates
            )


            conn.execute("""
                INSERT INTO user_history (

                    user_id,
                    mood,
                    channel_id,
                    message_id,
                    sent_at

                )

                VALUES (?, ?, ?, ?, ?)

            """, (

                user_id,

                mood,

                channel_id,

                message_id,

                int(time.time())

            ))


            conn.commit()


            return (
                message_id,
                channel_id
            )


        except Exception as exc:

            conn.rollback()

            print(
                "RESERVE ERROR:",
                repr(exc)
            )

            return None


        finally:

            conn.close()


# ============================================================
# REMOVE FAILED HISTORY
# ============================================================

def remove_last_history(
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


    except Exception as exc:

        print(
            "REMOVE HISTORY ERROR:",
            repr(exc)
        )


    finally:

        conn.close()


# ============================================================
# SEND MUSIC
# ============================================================

def send_music(
    chat_id,
    user_id,
    mood
):

    try:

        count = get_track_count(
            mood
        )

    except Exception as exc:

        print(
            "TRACK COUNT ERROR:",
            repr(exc)
        )

        send_message(
            chat_id,
            "⚠️ Database ခဏပြဿနာဖြစ်နေပါတယ်။"
        )

        return


    print(
        f"🎵 {mood.upper()} tracks available:",
        count
    )


    if count <= 0:

        send_message(

            chat_id,

            f"{MOOD_NAMES[mood]}\n\n"
            "⚠️ ဒီ mood ထဲမှာ music မတွေ့သေးပါ။",

            mood_menu()

        )

        return


    attempts = min(
        count,
        10
    )


    for _ in range(attempts):

        reserved = reserve_track(
            user_id,
            mood
        )


        if not reserved:

            break


        message_id, channel_id = reserved


        print(
            "🎧 Trying track:",
            channel_id,
            message_id
        )


        result = copy_music(

            chat_id,

            channel_id,

            message_id

        )


        if result.get("ok"):

            send_message(

                chat_id,

                f"{MOOD_NAMES[mood]}\n\n"
                "🎧 Enjoy your music! 🔥",

                music_buttons()

            )


            print(
                "✅ MUSIC SENT:",
                user_id,
                mood,
                message_id
            )


            return


        print(
            "⚠️ COPY FAILED:",
            channel_id,
            message_id
        )


        remove_last_history(
            user_id,
            message_id
        )


    send_message(

        chat_id,

        f"{MOOD_NAMES[mood]}\n\n"
        "⚠️ ဒီ track ကို ပို့လို့မရပါ။ "
        "နောက်တစ်ပုဒ်ကို စမ်းကြည့်ပါ။",

        music_buttons()

    )


# ============================================================
# BACKGROUND MUSIC
# ============================================================

def background_music(
    chat_id,
    user_id,
    mood
):

    try:

        send_music(
            chat_id,
            user_id,
            mood
        )


    except Exception as exc:

        print(
            "BACKGROUND MUSIC ERROR:",
            repr(exc)
        )


        try:

            send_message(
                chat_id,
                "⚠️ ခဏအကြာမှာ ပြန်စမ်းကြည့်ပါ။"
            )

        except Exception:

            pass


# ============================================================
# ADMIN
# ============================================================

def is_admin(user_id):

    if not ADMIN_USER_ID:

        return False


    return str(user_id) == str(
        ADMIN_USER_ID
    )


# ============================================================
# USER COUNT
# ============================================================

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


# ============================================================
# TRACK COUNTS
# ============================================================

def get_track_counts():

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


    finally:

        conn.close()


    return result


# ============================================================
# STATS
# ============================================================

def send_stats(chat_id):

    if not is_admin(chat_id):

        send_message(
            chat_id,
            "❌ Admin only."
        )

        return


    users = get_users_count()

    counts = get_track_counts()

    total = sum(
        counts.values()
    )


    lines = [

        "📊 NOT YOUR VIBE MUSIC BOT",

        "",

        f"👥 Users: {users}",

        f"🎵 Total tracks: {total}",

        ""

    ]


    for mood in MOODS:

        lines.append(

            f"{MOOD_NAMES[mood]} → "
            f"{counts[mood]}"

        )


    send_message(
        chat_id,
        "\n".join(lines)
    )


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    return (
        "🎧 NOT YOUR VIBE MUSIC BOT ONLINE"
    )


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    return "OK"


# ============================================================
# WEBHOOK
# ============================================================

@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    try:

        update = request.get_json(
            silent=True
        )

    except Exception as exc:

        print(
            "WEBHOOK JSON ERROR:",
            repr(exc)
        )

        update = None


    if not update:

        return "OK"


    # ========================================================
    # CALLBACK
    # ========================================================

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


        user = callback.get(
            "from"
        ) or {}


        message = callback.get(
            "message"
        ) or {}


        chat = message.get(
            "chat"
        ) or {}


        chat_id = chat.get(
            "id"
        )


        user_id = user.get(
            "id"
        )


        if not chat_id or not user_id:

            return "OK"


        register_user(
            user
        )


        # ----------------------------------------------------
        # MOOD
        # ----------------------------------------------------

        if data.startswith(
            "mood_"
        ):

            mood = data[
                5:
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


            threading.Thread(

                target=background_music,

                args=(

                    chat_id,

                    user_id,

                    mood

                ),

                daemon=True

            ).start()


            return "OK"


        # ----------------------------------------------------
        # NEXT
        # ----------------------------------------------------

        if data == "next_music":

            mood = get_user_mood(
                user_id
            )


            if not mood:

                answer_callback(
                    callback_id,
                    "Choose mood first"
                )


                send_message(

                    chat_id,

                    "🎧 Choose your mood 👇",

                    mood_menu()

                )


                return "OK"


            answer_callback(

                callback_id,

                "🔀 Finding next..."

            )


            threading.Thread(

                target=background_music,

                args=(

                    chat_id,

                    user_id,

                    mood

                ),

                daemon=True

            ).start()


            return "OK"


        # ----------------------------------------------------
        # CHANGE MOOD
        # ----------------------------------------------------

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


    # ========================================================
    # NORMAL MESSAGE
    # ========================================================

    message = update.get(
        "message"
    )


    if not message:

        return "OK"


    chat = message.get(
        "chat"
    ) or {}


    user = message.get(
        "from"
    ) or {}


    chat_id = chat.get(
        "id"
    )


    if not chat_id:

        return "OK"


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


    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    if text == "/start":

        send_message(

            chat_id,

            "🎧 NOT YOUR VIBE MUSIC\n\n"
            "Welcome! 🔥\n\n"
            "Mood တစ်ခုရွေးပါ 👇",

            mood_menu()

        )

        return "OK"


    # --------------------------------------------------------
    # MOOD
    # --------------------------------------------------------

    if text == "/mood":

        send_message(

            chat_id,

            "🎧 Choose your mood 👇",

            mood_menu()

        )

        return "OK"


    # --------------------------------------------------------
    # NEXT
    # --------------------------------------------------------

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

            return "OK"


        threading.Thread(

            target=background_music,

            args=(

                chat_id,

                chat_id,

                mood

            ),

            daemon=True

        ).start()


        return "OK"


    # --------------------------------------------------------
    # USERS
    # --------------------------------------------------------

    if text == "/users":

        if is_admin(chat_id):

            send_message(

                chat_id,

                f"👥 Total users: "
                f"{get_users_count()}"

            )

        else:

            send_message(

                chat_id,

                "❌ Admin only."

            )


        return "OK"


    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    if text == "/stats":

        send_stats(
            chat_id
        )

        return "OK"


    # --------------------------------------------------------
    # HELP
    # --------------------------------------------------------

    if text == "/help":

        send_message(

            chat_id,

            "🎧 NOT YOUR VIBE MUSIC BOT\n\n"

            "/start → Start\n"
            "/mood → Mood menu\n"
            "/next → Next track\n"
            "/users → User count (Admin)\n"
            "/stats → Bot statistics (Admin)\n"
            "/help → Help"

        )

        return "OK"


    return "OK"


# ============================================================
# SET WEBHOOK
# ============================================================

def setup_webhook():

    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN missing"
        )

        return


    if not RENDER_EXTERNAL_URL:

        print(
            "⚠️ RENDER_EXTERNAL_URL missing"
        )

        return


    webhook_url = (

        RENDER_EXTERNAL_URL.rstrip("/")

        + "/webhook"

    )


    print(
        "🌐 Webhook URL:",
        webhook_url
    )


    result = telegram(

        "setWebhook",

        {

            "url": webhook_url,

            "allowed_updates": [

                "message",

                "callback_query"

            ],

            "drop_pending_updates": True,

            "max_connections": 40

        },

        timeout=20

    )


    print(
        "WEBHOOK RESULT:",
        result
    )


# ============================================================
# STARTUP
# ============================================================

def startup():

    print("")
    print(
        "=================================================="
    )

    print(
        "🎧 NOT YOUR VIBE MUSIC BOT"
    )

    print(
        "=================================================="
    )


    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    print(
        "📁 Database:",
        DB_PATH
    )


    try:

        init_db()

        print(
            "✅ Database ready"
        )

    except Exception as exc:

        print(
            "❌ DATABASE ERROR:",
            repr(exc)
        )


    # --------------------------------------------------------
    # BOT TOKEN
    # --------------------------------------------------------

    if BOT_TOKEN:

        print(
            "✅ BOT_TOKEN found"
        )

    else:

        print(
            "❌ BOT_TOKEN missing"
        )


    # --------------------------------------------------------
    # ADMIN
    # --------------------------------------------------------

    if ADMIN_USER_ID:

        print(
            "✅ ADMIN_USER_ID found"
        )

    else:

        print(
            "⚠️ ADMIN_USER_ID missing"
        )


    # --------------------------------------------------------
    # CHANNELS
    # --------------------------------------------------------

    print("")
    print(
        "🎵 MOOD CHANNELS"
    )


    for mood in MOODS:

        value = MOOD_CHANNELS.get(
            mood,
            ""
        )


        if value:

            print(
                f"✅ {mood.upper()} CHANNEL: "
                f"{value}"
            )

        else:

            print(
                f"⚠️ {mood.upper()} CHANNEL missing"
            )


    # --------------------------------------------------------
    # WEBHOOK
    # --------------------------------------------------------

    setup_webhook()


    # --------------------------------------------------------
    # TELETHON
    # --------------------------------------------------------

    threading.Thread(

        target=telethon_worker,

        daemon=True

    ).start()


    # --------------------------------------------------------
    # READY
    # --------------------------------------------------------

    print("")
    print(
        "=================================================="
    )

    print(
        "🚀 BOT SERVER READY"
    )

    print(
        "=================================================="
    )

    print("")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    startup()


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
