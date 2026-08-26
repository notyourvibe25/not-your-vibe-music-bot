import os
import time
import random
import sqlite3
import threading

import requests
from flask import Flask

from telethon import TelegramClient


# ============================================================
# APP
# ============================================================

app = Flask(__name__)


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "").strip()

RENDER_EXTERNAL_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    ""
).strip()


# ------------------------------------------------------------
# Telethon
# TELETHON_* ကို ဦးစားပေးသုံးမယ်
# ------------------------------------------------------------

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
#
# Render Free မှာ persistent disk မသုံးဘူး။
#
# Database က restart ဖြစ်ရင် ပျောက်နိုင်တယ်။
# ဒါပေမယ့် Telethon က channel history ကို startup မှာ
# ပြန် scan လုပ်ပြီး database ကို rebuild လုပ်ပေးမယ်။
#
# ============================================================

DB_PATH = "music_bot.db"


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
#
# Render Environment Variables ထဲမှာ
# channel တစ်ခုချင်းစီရဲ့ value ထည့်ထားရမယ်။
#
# ============================================================

MOOD_CHANNELS = {
    "sad": os.getenv("SAD_CHANNEL", "").strip(),
    "love": os.getenv("LOVE_CHANNEL", "").strip(),
    "chill": os.getenv("CHILL_CHANNEL", "").strip(),
    "hype": os.getenv("HYPE_CHANNEL", "").strip(),
    "dark": os.getenv("DARK_CHANNEL", "").strip(),
    "energetic": os.getenv("ENERGETIC_CHANNEL", "").strip(),
    "night": os.getenv("NIGHT_CHANNEL", "").strip(),
    "melodic": os.getenv("MELODIC_CHANNEL", "").strip(),
}


# ============================================================
# HTTP SESSION
# ============================================================

http = requests.Session()

http.headers.update(
    {
        "User-Agent": "NOT-YOUR-VIBE-MUSIC-BOT/3.0"
    }
)


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
# DATABASE
# ============================================================

def get_db():
    """
    Flask / Thread အများကြီး run နေရင်
    SQLite connection တစ်ခုကို thread အများကြီး မမျှသုံးဘူး။
    Thread တစ်ခုတိုင်း connection အသစ်ယူမယ်။
    """

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False,
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA busy_timeout = 30000"
    )

    conn.execute(
        "PRAGMA journal_mode = WAL"
    )

    return conn


def init_db():
    """
    Database tables တွေ create လုပ်မယ်။
    """

    with db_init_lock:

        conn = get_db()

        try:

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    first_seen INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL,
                    total_requests INTEGER NOT NULL DEFAULT 0
                )
                """
            )


            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mood TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(channel_id, message_id)
                )
                """
            )


            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    mood TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    sent_at INTEGER NOT NULL
                )
                """
            )


            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_state (
                    user_id INTEGER PRIMARY KEY,
                    mood TEXT,
                    updated_at INTEGER NOT NULL
                )
                """
            )


            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_tracks_mood
                ON tracks(mood)
                """
            )


            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_history_user
                ON user_history(user_id, sent_at DESC)
                """
            )


            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_history_user_mood
                ON user_history(user_id, mood, sent_at DESC)
                """
            )


            conn.commit()

        finally:

            conn.close()


# ============================================================
# TELEGRAM BOT API
# ============================================================

def telegram(
    method,
    data=None,
    timeout=20,
):
    """
    Telegram Bot API request helper.
    """

    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN is missing"
        )

        return {
            "ok": False,
            "description": "BOT_TOKEN missing",
        }


    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )


    try:

        response = http.post(
            url,
            json=data or {},
            timeout=timeout,
        )


        try:

            result = response.json()

        except Exception:

            result = {
                "ok": False,
                "description": response.text,
            }


        if not result.get("ok"):

            print(
                "TELEGRAM API ERROR:",
                method,
                result,
            )


        return result


    except Exception as exc:

        print(
            "TELEGRAM REQUEST ERROR:",
            method,
            repr(exc),
        )

        return {
            "ok": False,
            "description": str(exc),
        }


# ============================================================
# SEND MESSAGE
# ============================================================

def send_message(
    chat_id,
    text,
    keyboard=None,
):
    """
    Send normal Telegram message.
    """

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
        timeout=15,
    )


# ============================================================
# ANSWER CALLBACK
# ============================================================

def answer_callback(
    callback_id,
    text="",
):
    """
    Inline button loading ကိုပျောက်စေတယ်။
    """

    return telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text,
        },
        timeout=8,
    )


# ============================================================
# COPY MUSIC
# ============================================================

def copy_music(
    chat_id,
    channel_id,
    message_id,
):
    """
    Channel ထဲက music post ကို
    user chat ထဲ copy လုပ်မယ်။
    """

    return telegram(
        "copyMessage",
        {
            "chat_id": chat_id,
            "from_chat_id": channel_id,
            "message_id": message_id,
        },
        timeout=30,
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


    username = user.get(
        "username"
    )

    first_name = user.get(
        "first_name"
    )

    last_name = user.get(
        "last_name"
    )

    now = int(time.time())


    conn = get_db()

    try:

        conn.execute(
            """
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
            """,
            (
                user_id,
                username,
                first_name,
                last_name,
                now,
                now,
            ),
        )

        conn.commit()

    except Exception as exc:

        print(
            "REGISTER USER ERROR:",
            repr(exc),
        )

    finally:

        conn.close()


# ============================================================
# SAVE TRACK
# ============================================================

def save_track(
    mood,
    channel_id,
    message_id,
):
    """
    Track database ထဲထည့်မယ်။
    """

    if mood not in MOODS:

        return


    if not message_id:

        return


    now = int(time.time())


    conn = get_db()

    try:

        conn.execute(
            """
            INSERT OR IGNORE INTO tracks (
                mood,
                channel_id,
                message_id,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                mood,
                str(channel_id),
                int(message_id),
                now,
            ),
        )


        conn.commit()

    except Exception as exc:

        print(
            "SAVE TRACK ERROR:",
            repr(exc),
        )

    finally:

        conn.close()


# ============================================================
# GET TRACK COUNT
# ============================================================

def get_track_count(mood):

    conn = get_db()

    try:

        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM tracks
            WHERE mood = ?
            """,
            (mood,),
        ).fetchone()


        return int(
            row["total"]
        )

    finally:

        conn.close()


# ============================================================
# GET ALL TRACK COUNTS
# ============================================================

def get_all_track_counts():

    result = {
        mood: 0
        for mood in MOODS
    }


    conn = get_db()

    try:

        rows = conn.execute(
            """
            SELECT mood, COUNT(*) AS total
            FROM tracks
            GROUP BY mood
            """
        ).fetchall()


        for row in rows:

            mood = row["mood"]

            if mood in result:

                result[mood] = int(
                    row["total"]
                )


    finally:

        conn.close()


    return result


# ============================================================
# SET USER MOOD
# ============================================================

def set_user_mood(
    user_id,
    mood,
):

    if mood not in MOODS:

        return


    conn = get_db()

    try:

        conn.execute(
            """
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
            """,
            (
                user_id,
                mood,
                int(time.time()),
            ),
        )


        conn.commit()

    finally:

        conn.close()


# ============================================================
# GET USER MOOD
# ============================================================

def get_user_mood(user_id):

    conn = get_db()

    try:

        row = conn.execute(
            """
            SELECT mood
            FROM user_state
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()


        if row and row["mood"]:

            return row["mood"]


        return None

    finally:

        conn.close()


# ============================================================
# GET RECENT HISTORY
# ============================================================

def get_recent_history(
    user_id,
    mood,
    limit=50,
):
    """
    ဒီ user က ဒီ mood မှာ မကြာသေးခင်က
    ကြည့်ပြီးသား tracks တွေ။
    """

    conn = get_db()

    try:

        rows = conn.execute(
            """
            SELECT message_id
            FROM user_history
            WHERE user_id = ?
            AND mood = ?
            ORDER BY sent_at DESC, id DESC
            LIMIT ?
            """,
            (
                user_id,
                mood,
                limit,
            ),
        ).fetchall()


        return {
            int(row["message_id"])
            for row in rows
        }

    finally:

        conn.close()


# ============================================================
# GET RANDOM TRACK
# ============================================================

def reserve_random_track(
    user_id,
    mood,
):
    """
    User တစ်ယောက်အတွက် track reserve လုပ်တယ်။

    User lock + SQLite transaction သုံးထားတာကြောင့်
    တစ်ချိန်တည်း Next အကြိမ်များစွာနှိပ်ရင်
    duplicate ဖြစ်နိုင်ခြေကို လျှော့ထားတယ်။
    """

    lock = get_user_lock(user_id)


    with lock:

        conn = get_db()

        try:

            recent = get_recent_history(
                user_id,
                mood,
                50,
            )


            rows = conn.execute(
                """
                SELECT
                    message_id,
                    channel_id
                FROM tracks
                WHERE mood = ?
                """,
                (mood,),
            ).fetchall()


            if not rows:

                return None


            fresh = []

            old = []


            for row in rows:

                item = (
                    int(row["message_id"]),
                    str(row["channel_id"]),
                )


                if item[0] in recent:

                    old.append(item)

                else:

                    fresh.append(item)


            if fresh:

                selected = random.choice(
                    fresh
                )

            else:

                selected = random.choice(
                    old
                )


            message_id = selected[0]

            channel_id = selected[1]


            conn.execute(
                """
                INSERT INTO user_history (
                    user_id,
                    mood,
                    channel_id,
                    message_id,
                    sent_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    mood,
                    channel_id,
                    message_id,
                    int(time.time()),
                ),
            )


            # ------------------------------------------------
            # History ကို user တစ်ယောက်အတွက် 100 records ထားမယ်
            # ------------------------------------------------

            conn.execute(
                """
                DELETE FROM user_history
                WHERE user_id = ?
                AND id NOT IN (
                    SELECT id
                    FROM user_history
                    WHERE user_id = ?
                    ORDER BY sent_at DESC, id DESC
                    LIMIT 100
                )
                """,
                (
                    user_id,
                    user_id,
                ),
            )


            conn.commit()


            return (
                message_id,
                channel_id,
            )


        except Exception as exc:

            conn.rollback()

            print(
                "RESERVE TRACK ERROR:",
                repr(exc),
            )

            return None

        finally:

            conn.close()


# ============================================================
# REMOVE HISTORY
# ============================================================

def remove_last_history(
    user_id,
    message_id,
):

    conn = get_db()

    try:

        conn.execute(
            """
            DELETE FROM user_history
            WHERE id = (
                SELECT id
                FROM user_history
                WHERE user_id = ?
                AND message_id = ?
                ORDER BY id DESC
                LIMIT 1
            )
            """,
            (
                user_id,
                message_id,
            ),
        )


        conn.commit()

    except Exception as exc:

        print(
            "REMOVE HISTORY ERROR:",
            repr(exc),
        )

    finally:

        conn.close()


# ============================================================
# MOOD MENU
# ============================================================

def mood_menu():

    return {
        "inline_keyboard": [
            [
                {
                    "text": "😢 Sad",
                    "callback_data": "mood_sad",
                },
                {
                    "text": "❤️ Love",
                    "callback_data": "mood_love",
                },
            ],
            [
                {
                    "text": "🌙 Chill",
                    "callback_data": "mood_chill",
                },
                {
                    "text": "🔥 Hype",
                    "callback_data": "mood_hype",
                },
            ],
            [
                {
                    "text": "🖤 Dark",
                    "callback_data": "mood_dark",
                },
                {
                    "text": "⚡ Energetic",
                    "callback_data": "mood_energetic",
                },
            ],
            [
                {
                    "text": "🚗 Night Drive",
                    "callback_data": "mood_night",
                },
                {
                    "text": "🌌 Melodic",
                    "callback_data": "mood_melodic",
                },
            ],
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
                    "callback_data": "next_music",
                }
            ],
            [
                {
                    "text": "🎧 Change Mood",
                    "callback_data": "change_mood",
                }
            ],
        ]
    }


# ============================================================
# SEND MUSIC
# ============================================================

def send_mood_track(
    chat_id,
    user_id,
    mood,
):
    """
    Selected mood channel ထဲက random track ပို့မယ်။
    """

    if mood not in MOODS:

        return


    count = get_track_count(
        mood
    )


    if count <= 0:

        send_message(
            chat_id,
            (
                f"{MOOD_NAMES[mood]}\n\n"
                "⚠️ ဒီ mood ထဲမှာ music မတွေ့သေးပါ။\n\n"
                "ခဏစောင့်ပြီး ထပ်စမ်းကြည့်ပါ။"
            ),
            mood_menu(),
        )

        return


    attempted = set()


    for _ in range(10):

        reserved = reserve_random_track(
            user_id,
            mood,
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
            message_id,
        )


        if result.get("ok"):

            send_message(
                chat_id,
                (
                    f"{MOOD_NAMES[mood]}\n\n"
                    "🎧 Enjoy your music! 🔥"
                ),
                music_buttons(),
            )

            print(
                "🎵 SENT:",
                user_id,
                mood,
                channel_id,
                message_id,
            )

            return


        print(
            "⚠️ COPY FAILED:",
            channel_id,
            message_id,
        )


        remove_last_history(
            user_id,
            message_id,
        )


    send_message(
        chat_id,
        (
            f"{MOOD_NAMES[mood]}\n\n"
            "❌ Music ပို့လို့မရပါ။\n\n"
            "Bot ကို အဲ့ဒီ channel တွေမှာ "
            "admin ထားထားတာ သေချာစစ်ပါ။"
        ),
        mood_menu(),
    )


# ============================================================
# BACKGROUND MUSIC
# ============================================================

def background_music(
    chat_id,
    user_id,
    mood,
):

    try:

        send_mood_track(
            chat_id,
            user_id,
            mood,
        )

    except Exception as exc:

        print(
            "BACKGROUND MUSIC ERROR:",
            repr(exc),
        )


# ============================================================
# USER STATS
# ============================================================

def get_users_count():

    conn = get_db()

    try:

        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM users
            """
        ).fetchone()


        return int(
            row["total"]
        )

    finally:

        conn.close()


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
# STATS
# ============================================================

def send_stats(
    chat_id,
    user_id,
):

    if not is_admin(user_id):

        send_message(
            chat_id,
            "❌ Admin only.",
        )

        return


    users = get_users_count()

    counts = get_all_track_counts()

    total = sum(
        counts.values()
    )


    lines = [
        "📊 NOT YOUR VIBE MUSIC BOT",
        "",
        f"👥 Users: {users}",
        f"🎵 Total tracks: {total}",
        "",
    ]


    for mood in MOODS:

        lines.append(
            f"{MOOD_NAMES[mood]} → "
            f"{counts[mood]}"
        )


    send_message(
        chat_id,
        "\n".join(lines),
    )


# ============================================================
# TELETHON CHANNEL RESOLVER
# ============================================================

async def resolve_channel(
    value
):
    """
    @username / numeric ID နှစ်မျိုးလုံး handle လုပ်မယ်။
    """

    if not value:

        return None


    value = value.strip()


    try:

        if value.startswith("-100"):

            return await telethon_client.get_entity(
                int(value)
            )


        return await telethon_client.get_entity(
            value
        )

    except Exception as exc:

        print(
            "❌ CHANNEL RESOLVE ERROR:",
            value,
            repr(exc),
        )

        return None


# ============================================================
# IMPORT CHANNEL HISTORY
# ============================================================

async def import_channel(
    mood,
    channel_value,
):
    """
    Channel history ကို Telethon နဲ့ scan လုပ်မယ်။

    audio / document / video / voice
    ပါတဲ့ post တွေကို database ထဲထည့်မယ်။

    Render restart ဖြစ်ပြီး DB ပျောက်သွားရင်
    ဒီ function က ပြန် scan လုပ်ပေးနိုင်တယ်။
    """

    if not channel_value:

        print(
            f"⚠️ {mood.upper()} channel is empty"
        )

        return


    entity = await resolve_channel(
        channel_value
    )


    if entity is None:

        print(
            f"❌ Cannot access {mood}: "
            f"{channel_value}"
        )

        return


    try:

        entity_id = str(
            entity.id
        )

    except Exception:

        entity_id = str(
            channel_value
        )


    print(
        f"🔎 Scanning {MOOD_NAMES[mood]}..."
    )


    found = 0


    try:

        async for message in telethon_client.iter_messages(
            entity
        ):

            has_music = False


            if getattr(
                message,
                "audio",
                None,
            ):

                has_music = True


            elif getattr(
                message,
                "voice",
                None,
            ):

                has_music = True


            elif getattr(
                message,
                "video",
                None,
            ):

                has_music = True


            elif getattr(
                message,
                "document",
                None,
            ):

                document = message.document

                mime = getattr(
                    document,
                    "mime_type",
                    "",
                ) or ""


                if (
                    mime.startswith("audio/")
                    or mime.startswith("video/")
                ):

                    has_music = True


            if not has_music:

                continue


            save_track(
                mood,
                entity_id,
                message.id,
            )


            found += 1


        total = get_track_count(
            mood
        )


        print(
            f"✅ {MOOD_NAMES[mood]} "
            f"scan complete: "
            f"{found} found / "
            f"{total} database"
        )


    except Exception as exc:

        print(
            f"❌ SCAN ERROR {mood}:",
            repr(exc),
        )


# ============================================================
# IMPORT ALL CHANNELS
# ============================================================

async def import_all_channels():

    print(
        "=========================================="
    )

    print(
        "📚 CHANNEL HISTORY SCAN STARTED"
    )

    print(
        "=========================================="
    )


    for mood in MOODS:

        channel = MOOD_CHANNELS.get(
            mood,
            "",
        )


        try:

            await import_channel(
                mood,
                channel,
            )

        except Exception as exc:

            print(
                "IMPORT ERROR:",
                mood,
                repr(exc),
            )


    print(
        "=========================================="
    )

    print(
        "📚 CHANNEL HISTORY SCAN FINISHED"
    )

    print(
        "=========================================="
    )


# ============================================================
# TELETHON START
# ============================================================

def start_telethon():

    global telethon_client


    if not TELETHON_API_ID:

        print(
            "❌ TELETHON_API_ID missing"
        )

        return False


    if not TELETHON_API_HASH:

        print(
            "❌ TELETHON_API_HASH missing"
        )

        return False


    if not TELETHON_SESSION:

        print(
            "❌ TELETHON_SESSION missing"
        )

        return False


    try:

        api_id = int(
            TELETHON_API_ID
        )

    except ValueError:

        print(
            "❌ TELETHON_API_ID must be a number"
        )

        return False


    print(
        "🔐 Connecting Telegram account..."
    )


    telethon_client = TelegramClient(
        TELETHON_SESSION,
        api_id,
        TELETHON_API_HASH,
    )


    try:

        telethon_client.start()


        print(
            "✅ Telegram account connected"
        )


        telethon_client.loop.run_until_complete(
            import_all_channels()
        )


        print(
            "✅ Telegram history imported"
        )


        return True


    except Exception as exc:

        print(
            "❌ TELETHON ERROR:",
            repr(exc),
        )

        return False


# ============================================================
# BOT POLLING
# ============================================================

def process_update(update):
    """
    Telegram update တစ်ခုချင်းစီကို handle လုပ်တယ်။
    """

    # --------------------------------------------------------
    # Normal message
    # --------------------------------------------------------

    message = update.get(
        "message"
    )


    if message:

        chat = message.get(
            "chat",
            {},
        )


        chat_id = chat.get(
            "id"
        )


        user = message.get(
            "from",
            {},
        )


        if not chat_id:

            return


        register_user(
            user
        )


        user_id = user.get(
            "id"
        )


        text = (
            message.get(
                "text",
                "",
            )
            or ""
        ).strip()


        if text == "/start":

            send_message(
                chat_id,
                (
                    "🎧 NOT YOUR VIBE MUSIC\n\n"
                    "Welcome! 🔥\n\n"
                    "Mood တစ်ခုရွေးပါ 👇"
                ),
                mood_menu(),
            )

            return


        if text == "/mood":

            send_message(
                chat_id,
                "🎧 Choose your mood 👇",
                mood_menu(),
            )

            return


        if text == "/next":

            mood = get_user_mood(
                user_id
            )


            if not mood:

                send_message(
                    chat_id,
                    "🎧 အရင်ဆုံး Mood ရွေးပါ 👇",
                    mood_menu(),
                )

                return


            threading.Thread(
                target=background_music,
                args=(
                    chat_id,
                    user_id,
                    mood,
                ),
                daemon=True,
            ).start()


            return


        if text == "/stats":

            send_stats(
                chat_id,
                user_id,
            )

            return


        if text == "/users":

            send_stats(
                chat_id,
                user_id,
            )

            return


        if text == "/help":

            send_message(
                chat_id,
                (
                    "🎧 NOT YOUR VIBE MUSIC BOT\n\n"
                    "/start → Start\n"
                    "/mood → Mood menu\n"
                    "/next → Next music\n"
                    "/stats → Admin stats\n"
                    "/help → Help"
                ),
            )

            return


    # --------------------------------------------------------
    # Callback
    # --------------------------------------------------------

    callback = update.get(
        "callback_query"
    )


    if not callback:

        return


    callback_id = callback.get(
        "id"
    )


    callback_user = callback.get(
        "from",
        {},
    )


    callback_message = callback.get(
        "message",
        {},
    )


    callback_chat = callback_message.get(
        "chat",
        {},
    )


    chat_id = callback_chat.get(
        "id"
    )


    user_id = callback_user.get(
        "id"
    )


    if not chat_id or not user_id:

        if callback_id:

            answer_callback(
                callback_id,
                "Error",
            )

        return


    register_user(
        callback_user
    )


    data = callback.get(
        "data",
        "",
    )


    # --------------------------------------------------------
    # Mood
    # --------------------------------------------------------

    if data.startswith(
        "mood_"
    ):

        mood = data[
            len("mood_"):
        ]


        if mood not in MOODS:

            answer_callback(
                callback_id,
                "Invalid mood",
            )

            return


        set_user_mood(
            user_id,
            mood,
        )


        answer_callback(
            callback_id,
            f"{MOOD_NAMES[mood]} selected",
        )


        threading.Thread(
            target=background_music,
            args=(
                chat_id,
                user_id,
                mood,
            ),
            daemon=True,
        ).start()


        return


    # --------------------------------------------------------
    # Next
    # --------------------------------------------------------

    if data == "next_music":

        mood = get_user_mood(
            user_id
        )


        answer_callback(
            callback_id,
            "🔀 Finding next...",
        )


        if not mood:

            send_message(
                chat_id,
                "🎧 အရင်ဆုံး Mood ရွေးပါ 👇",
                mood_menu(),
            )

            return


        threading.Thread(
            target=background_music,
            args=(
                chat_id,
                user_id,
                mood,
            ),
            daemon=True,
        ).start()


        return


    # --------------------------------------------------------
    # Change Mood
    # --------------------------------------------------------

    if data == "change_mood":

        answer_callback(
            callback_id,
            "🎧 Choose mood",
        )


        send_message(
            chat_id,
            "🎧 Choose your mood 👇",
            mood_menu(),
        )

        return


# ============================================================
# POLLING LOOP
# ============================================================

def bot_polling():

    print(
        "🤖 BOT POLLING STARTED"
    )


    offset = 0


    # --------------------------------------------------------
    # Remove old webhook
    # --------------------------------------------------------

    result = telegram(
        "deleteWebhook",
        {
            "drop_pending_updates": True
        },
        timeout=20,
    )


    print(
        "DELETE WEBHOOK:",
        result,
    )


    while True:

        try:

            result = telegram(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 30,
                    "allowed_updates": [
                        "message",
                        "callback_query",
                        "channel_post",
                    ],
                },
                timeout=40,
            )


            if not result.get("ok"):

                print(
                    "❌ GET UPDATES ERROR:",
                    result,
                )

                time.sleep(5)

                continue


            updates = result.get(
                "result",
                [],
            )


            for update in updates:

                try:

                    offset = (
                        update.get(
                            "update_id",
                            offset,
                        )
                        + 1
                    )


                    # ------------------------------------------------
                    # Channel post
                    # ------------------------------------------------

                    channel_post = update.get(
                        "channel_post"
                    )


                    if channel_post:

                        process_channel_post(
                            channel_post
                        )


                    # ------------------------------------------------
                    # Normal / callback
                    # ------------------------------------------------

                    process_update(
                        update
                    )


                except Exception as exc:

                    print(
                        "UPDATE ERROR:",
                        repr(exc),
                    )


        except Exception as exc:

            print(
                "POLLING ERROR:",
                repr(exc),
            )

            time.sleep(5)


# ============================================================
# CHANNEL POST AUTO SAVE
# ============================================================

def process_channel_post(
    post
):
    """
    Channel အသစ်တင်တဲ့ music ကို
    database ထဲ auto save လုပ်မယ်။
    """

    chat = post.get(
        "chat",
        {},
    )


    channel_id = str(
        chat.get(
            "id",
            "",
        )
    )


    username = (
        chat.get(
            "username",
            "",
        )
        or ""
    ).lower()


    message_id = post.get(
        "message_id"
    )


    if not message_id:

        return


    mood = None


    for candidate_mood in MOODS:

        configured = (
            MOOD_CHANNELS.get(
                candidate_mood,
                "",
            )
            or ""
        ).strip()


        if not configured:

            continue


        configured_lower = configured.lower()


        if configured_lower == channel_id:

            mood = candidate_mood

            break


        configured_username = (
            configured_lower
            .lstrip("@")
        )


        if (
            username
            and configured_username == username
        ):

            mood = candidate_mood

            break


    if not mood:

        return


    has_media = any(
        [
            post.get("audio"),
            post.get("document"),
            post.get("video"),
            post.get("voice"),
        ]
    )


    if not has_media:

        return


    save_track(
        mood,
        channel_id,
        message_id,
    )


    print(
        "🆕 NEW TRACK:",
        MOOD_NAMES[mood],
        channel_id,
        message_id,
    )


# ============================================================
# FLASK ROUTES
# ============================================================

@app.route(
    "/",
    methods=["GET"],
)
def home():

    return (
        "🎧 NOT YOUR VIBE MUSIC BOT ONLINE"
    )


@app.route(
    "/health",
    methods=["GET"],
)
def health():

    return "OK"


# ============================================================
# STARTUP
# ============================================================

def startup():

    print(
        "=========================================="
    )

    print(
        "🎧 NOT YOUR VIBE MUSIC BOT"
    )

    print(
        "=========================================="
    )


    init_db()


    # --------------------------------------------------------
    # Check environment
    # --------------------------------------------------------

    missing = []


    if not BOT_TOKEN:

        missing.append(
            "BOT_TOKEN"
        )


    if not ADMIN_USER_ID:

        missing.append(
            "ADMIN_USER_ID"
        )


    if not TELETHON_API_ID:

        missing.append(
            "TELETHON_API_ID"
        )


    if not TELETHON_API_HASH:

        missing.append(
            "TELETHON_API_HASH"
        )


    if not TELETHON_SESSION:

        missing.append(
            "TELETHON_SESSION"
        )


    if missing:

        print(
            "❌ MISSING ENV:",
            ", ".join(missing)
        )


        return


    # --------------------------------------------------------
    # Print channels
    # --------------------------------------------------------

    print(
        "📡 MOOD CHANNELS"
    )


    for mood in MOODS:

        print(
            f"  {MOOD_NAMES[mood]} → "
            f"{MOOD_CHANNELS.get(mood, '')}"
        )


    print(
        "=========================================="
    )


    # --------------------------------------------------------
    # Telethon
    # --------------------------------------------------------

    telethon_ok = start_telethon()


    if not telethon_ok:

        print(
            "❌ Telethon startup failed"
        )

    else:

        print(
            "✅ Telethon ready"
        )


    # --------------------------------------------------------
    # Bot polling
    # --------------------------------------------------------

    polling_thread = threading.Thread(
        target=bot_polling,
        daemon=True,
    )


    polling_thread.start()


    print(
        "🚀 BOT SERVICES STARTED"
    )


    print(
        "=========================================="
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    startup()


    port = int(
        os.getenv(
            "PORT",
            "10000",
        )
    )


    print(
        "🚀 WEB SERVER STARTING..."
    )


    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True,
    )
