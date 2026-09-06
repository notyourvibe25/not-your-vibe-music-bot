from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request
from psycopg2 import InterfaceError, OperationalError
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
except ImportError:
    TelegramClient = None
    StringSession = None

APP = Flask(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s | %(levelname)s | %(message)s")
LOG = logging.getLogger("not-your-vibe")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip().replace("postgres://", "postgresql://", 1)
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "").strip()
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
TZ = ZoneInfo(os.getenv("BOT_TIMEZONE", "Asia/Yangon"))
WORKERS = max(1, int(os.getenv("MUSIC_WORKER_COUNT", "4")))
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

MOODS = ("sad", "love", "chill", "hype", "dark", "energetic", "night", "melodic")
INFO = {
    "sad": ("SAD", "Stay with the feeling."),
    "love": ("LOVE", "For the moments that make your heart beat faster."),
    "chill": ("CHILL", "Slow down and let the world fade away."),
    "hype": ("HYPE", "Turn it up. Your energy starts here."),
    "dark": ("DARK", "Heavy bass. Brutal drops. No mercy."),
    "energetic": ("ENERGETIC", "No limits. No brakes."),
    "night": ("NIGHT DRIVE", "Lights outside. Music inside."),
    "melodic": ("MELODIC", "Let the melody take you somewhere else."),
}
CHANNELS = {m: os.getenv(f"{m.upper()}_CHANNEL", "").strip() for m in MOODS}
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".flac", ".wav", ".aac", ".ogg", ".opus", ".mp4", ".mkv", ".webm")
POOL = None
POOL_LOCK = threading.Lock()
HTTP = requests.Session()
EXECUTOR = ThreadPoolExecutor(max_workers=WORKERS)
PENDING = set()
PENDING_LOCK = threading.Lock()


def now_ts():
    return int(time.time())


def today():
    return datetime.now(TZ).date().isoformat()


@contextmanager
def db():
    global POOL
    if POOL is None:
        with POOL_LOCK:
            if POOL is None:
                if not DATABASE_URL:
                    raise RuntimeError("DATABASE_URL is required")
                POOL = ThreadedConnectionPool(1, 12, dsn=DATABASE_URL, connect_timeout=10)
    conn = POOL.getconn()
    try:
        conn.autocommit = False
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        POOL.putconn(conn)


@contextmanager
def cursor(conn):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield cur
    finally:
        cur.close()


def init_db():
    schema = """
    CREATE TABLE IF NOT EXISTS users(
        user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
        first_seen BIGINT NOT NULL, last_seen BIGINT NOT NULL,
        total_requests BIGINT NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS tracks(
        id BIGSERIAL PRIMARY KEY, mood TEXT NOT NULL, channel_id TEXT NOT NULL,
        message_id BIGINT NOT NULL, created_at BIGINT NOT NULL, title TEXT,
        UNIQUE(channel_id, message_id)
    );
    CREATE TABLE IF NOT EXISTS track_feedback(
        user_id BIGINT NOT NULL, channel_id TEXT NOT NULL, message_id BIGINT NOT NULL,
        mood TEXT NOT NULL, feedback TEXT NOT NULL, created_at BIGINT NOT NULL,
        PRIMARY KEY(user_id, channel_id, message_id)
    );
    CREATE TABLE IF NOT EXISTS user_history(
        id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, mood TEXT NOT NULL,
        channel_id TEXT NOT NULL, message_id BIGINT NOT NULL,
        action TEXT NOT NULL DEFAULT 'served', sent_at BIGINT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS user_state(
        user_id BIGINT PRIMARY KEY, mood TEXT, radio_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        updated_at BIGINT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS playlist_deliveries(
        user_id BIGINT NOT NULL, feature TEXT NOT NULL, day DATE NOT NULL,
        channel_id TEXT NOT NULL, message_id BIGINT NOT NULL, position INTEGER NOT NULL,
        created_at BIGINT NOT NULL,
        UNIQUE(user_id, feature, day, channel_id, message_id)
    );
    CREATE INDEX IF NOT EXISTS idx_history_user ON user_history(user_id, sent_at DESC);
    CREATE INDEX IF NOT EXISTS idx_tracks_mood ON tracks(mood);
    CREATE INDEX IF NOT EXISTS idx_playlist_user_day ON playlist_deliveries(user_id, feature, day);
    """
    with db() as conn:
        with cursor(conn) as cur:
            cur.execute(schema)


def register(user):
    uid = int(user["id"])
    with db() as conn:
        with cursor(conn) as cur:
            cur.execute("""INSERT INTO users(user_id,username,first_name,last_name,first_seen,last_seen,total_requests)
                VALUES(%s,%s,%s,%s,%s,%s,1)
                ON CONFLICT(user_id) DO UPDATE SET username=EXCLUDED.username,
                first_name=EXCLUDED.first_name,last_name=EXCLUDED.last_name,
                last_seen=EXCLUDED.last_seen,total_requests=users.total_requests+1""",
                (uid, user.get("username"), user.get("first_name"), user.get("last_name"), now_ts(), now_ts()))


def set_mood(uid, mood):
    if mood not in MOODS:
        return
    with db() as conn:
        with cursor(conn) as cur:
            cur.execute("""INSERT INTO user_state(user_id,mood,radio_enabled,updated_at)
                VALUES(%s,%s,FALSE,%s) ON CONFLICT(user_id) DO UPDATE SET
                mood=EXCLUDED.mood,radio_enabled=FALSE,updated_at=EXCLUDED.updated_at""", (uid, mood, now_ts()))


def state(uid):
    with db() as conn:
        with cursor(conn) as cur:
            cur.execute("SELECT mood,radio_enabled FROM user_state WHERE user_id=%s", (uid,))
            row = cur.fetchone() or {}
    return {"mood": row.get("mood"), "radio": bool(row.get("radio_enabled", False))}


def set_radio(uid, enabled=True):
    with db() as conn:
        with cursor(conn) as cur:
            cur.execute("""INSERT INTO user_state(user_id,mood,radio_enabled,updated_at)
                VALUES(%s,NULL,%s,%s) ON CONFLICT(user_id) DO UPDATE SET
                radio_enabled=EXCLUDED.radio_enabled,updated_at=EXCLUDED.updated_at""", (uid, enabled, now_ts()))


def save_track(mood, channel_id, message_id, title=None):
    if mood not in MOODS or not channel_id or not message_id:
        return
    with db() as conn:
        with cursor(conn) as cur:
            cur.execute("""INSERT INTO tracks(mood,channel_id,message_id,created_at,title)
                VALUES(%s,%s,%s,%s,%s) ON CONFLICT(channel_id,message_id) DO UPDATE SET
                mood=EXCLUDED.mood,title=COALESCE(EXCLUDED.title,tracks.title)""",
                (mood, str(channel_id), int(message_id), now_ts(), title))


def history_keys(uid):
    with db() as conn:
        with cursor(conn) as cur:
            cur.execute("SELECT channel_id,message_id FROM user_history WHERE user_id=%s AND action='served'", (uid,))
            return {(str(r["channel_id"]), int(r["message_id"])) for r in cur.fetchall()}


def feedback_map(uid):
    with db() as conn:
        with cursor(conn) as cur:
            cur.execute("SELECT channel_id,message_id,feedback FROM track_feedback WHERE user_id=%s", (uid,))
            return {(str(r["channel_id"]), int(r["message_id"])): r["feedback"] for r in cur.fetchall()}


def set_feedback(uid, mood, channel_id, message_id, feedback):
    if feedback not in ("like", "not_for_me"):
        return
    with db() as conn:
        with cursor(conn) as cur:
            cur.execute("""INSERT INTO track_feedback(user_id,channel_id,message_id,mood,feedback,created_at)
                VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(user_id,channel_id,message_id) DO UPDATE SET
                mood=EXCLUDED.mood,feedback=EXCLUDED.feedback,created_at=EXCLUDED.created_at""",
                (uid, str(channel_id), int(message_id), mood, feedback, now_ts()))


def record_served(uid, mood, message_id, channel_id):
    with db() as conn:
        with cursor(conn) as cur:
            cur.execute("INSERT INTO user_history(user_id,mood,channel_id,message_id,sent_at) VALUES(%s,%s,%s,%s,%s)",
                        (uid, mood, str(channel_id), int(message_id), now_ts()))


def liked_count(uid):
    with db() as conn:
        with cursor(conn) as cur:
            cur.execute("SELECT COUNT(*) AS n FROM track_feedback WHERE user_id=%s AND feedback='like'", (uid,))
            return int(cur.fetchone()["n"])


def candidate_rows(uid, mood=None, liked_only=False):
    clauses, args = [], [uid, uid]
    if mood:
        clauses.append("t.mood=%s")
    if liked_only:
        clauses.append("f.feedback='like'")
        join = "JOIN track_feedback f ON f.user_id=%s AND f.channel_id=t.channel_id AND f.message_id=t.message_id"
        args = [uid, uid]
    else:
        join = "LEFT JOIN track_feedback f ON f.user_id=%s AND f.channel_id=t.channel_id AND f.message_id=t.message_id"
    if mood:
        args.append(mood)
    where = (" AND " + " AND ".join(clauses)) if clauses else ""
    query = f"""SELECT t.mood,t.message_id,t.channel_id,t.title,t.created_at
        FROM tracks t {join} WHERE NOT EXISTS(
        SELECT 1 FROM track_feedback nf WHERE nf.user_id=%s AND nf.channel_id=t.channel_id
        AND nf.message_id=t.message_id AND nf.feedback='not_for_me'){where}
        ORDER BY t.created_at DESC,t.id DESC"""
    with db() as conn:
        with cursor(conn) as cur:
            cur.execute(query, args)
            return cur.fetchall()


def pick_playlist(uid, feature, mood=None, liked_only=False, limit=10):
    rows = candidate_rows(uid, mood, liked_only)
    if not rows:
        return []
    seen = history_keys(uid)
    salt = f"{feature}:{uid}:{today()}"
    def sort_key(row):
        key = (str(row["channel_id"]), int(row["message_id"]))
        unseen = 0 if key not in seen else 1
        digest = hashlib.sha256(f"{salt}:{key[0]}:{key[1]}".encode()).hexdigest()
        return unseen, digest
    return sorted(rows, key=sort_key)[:limit]


def radio_track(uid):
    # Radio deliberately remains feedback-ratio based; it is not For You.
    with db() as conn:
        with cursor(conn) as cur:
            cur.execute("""SELECT t.mood,COUNT(*) FILTER(WHERE f.feedback='like') AS likes,
                COUNT(*) FILTER(WHERE f.feedback='not_for_me') AS dislikes
                FROM tracks t LEFT JOIN track_feedback f ON f.user_id=%s AND f.channel_id=t.channel_id
                AND f.message_id=t.message_id GROUP BY t.mood""", (uid,))
            rows = cur.fetchall()
    if not rows:
        return None
    def score(r):
        likes, dislikes = int(r["likes"] or 0), int(r["dislikes"] or 0)
        return ((likes + 1) / (likes + dislikes + 2)) * (1 + min(likes, 20) * .35) / (1 + dislikes * .2)
    mood = max(rows, key=score)["mood"]
    playlist = pick_playlist(uid, "radio", mood=mood, limit=1)
    return playlist[0] if playlist else None


def tg(method, payload=None):
    response = HTTP.post(f"{API}/{method}", json=payload or {}, timeout=30)
    response.raise_for_status()
    return response.json()


def send(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    return tg("sendMessage", payload)


def copy_track(chat_id, channel_id, message_id):
    return tg("copyMessage", {"chat_id": chat_id, "from_chat_id": channel_id, "message_id": message_id})


def track_buttons(uid, mood, channel_id, message_id):
    fb = feedback_map(uid).get((str(channel_id), int(message_id)))
    return {"inline_keyboard": [[
        {"text": "❤️✓" if fb == "like" else "❤️", "callback_data": f"like:{mood}:{channel_id}:{message_id}"},
        {"text": "😴✓" if fb == "not_for_me" else "😴", "callback_data": f"notme:{mood}:{channel_id}:{message_id}"},
    ], [{"text": "⏭ NEXT", "callback_data": "next_music"}, {"text": "📻 RADIO", "callback_data": "radio"}]]}


def send_playlist(chat_id, uid, rows, header):
    if not rows:
        return send(chat_id, "⚠️ No suitable tracks found.", mood_menu())
    send(chat_id, f"{header}\n━━━━━━━━━━━━━━━━━━\n\n🎵 {len(rows)} tracks\nUnplayed tracks are prioritized.")
    for position, row in enumerate(rows, 1):
        try:
            result = copy_track(chat_id, row["channel_id"], row["message_id"])
            if not result.get("ok"):
                continue
            record_served(uid, row["mood"], row["message_id"], row["channel_id"])
            label = (row.get("title") or f"Track #{row['message_id']}").replace("\n", " ")[:100]
            send(chat_id, f"{position}. 🎵 {label}\n{INFO[row['mood']][0]}",
                 track_buttons(uid, row["mood"], row["channel_id"], row["message_id"]))
        except Exception:
            LOG.exception("playlist delivery failed")


def send_one(chat_id, uid, row, header):
    if not row:
        return send(chat_id, "⚠️ No suitable track found.", mood_menu())
    result = copy_track(chat_id, row["channel_id"], row["message_id"])
    if not result.get("ok"):
        return send(chat_id, "⚠️ This track could not be delivered.", mood_menu())
    record_served(uid, row["mood"], row["message_id"], row["channel_id"])
    return send(chat_id, f"{header}\n━━━━━━━━━━━━━━━━━━\n\n{INFO[row['mood']][0]}",
                track_buttons(uid, row["mood"], row["channel_id"], row["message_id"]))


def insufficient_likes(chat_id):
    return send(chat_id, "Raver အနေနဲ့ Like 10 ခုပေးအောင်မလုပ်ရသေးသဖြင့် BOT မှမပို့ပေးနိုင်သေးကြောင်းပါ", mood_menu())


def mood_menu():
    keyboard = [[{"text": INFO[a][0], "callback_data": f"mood_{a}"}, {"text": INFO[b][0], "callback_data": f"mood_{b}"}]
                for a, b in zip(MOODS[::2], MOODS[1::2])]
    keyboard += [[{"text": "🔥 DAILY VIBE", "callback_data": "daily_vibe"}, {"text": "🧠 FOR YOU", "callback_data": "for_you"}],
                 [{"text": "📻 RADIO", "callback_data": "radio"}, {"text": "🎵 TRACK OF THE DAY", "callback_data": "track_of_day"}]]
    return {"inline_keyboard": keyboard}


def handle_callback(update):
    cb = update.get("callback_query", {})
    uid, data = cb.get("from", {}).get("id"), cb.get("data", "")
    chat = cb.get("message", {}).get("chat", {}).get("id")
    if not isinstance(uid, int) or not isinstance(chat, int):
        return
    tg("answerCallbackQuery", {"callback_query_id": cb.get("id")})
    register(cb["from"])
    if data.startswith("mood_"):
        mood = data[5:]
        set_mood(uid, mood)
        send_one(chat, uid, pick_playlist(uid, "daily_vibe", mood=mood, limit=1)[0] if pick_playlist(uid, "daily_vibe", mood=mood, limit=1) else None, f"🎧 NOW PLAYING — {INFO[mood][0]}")
    elif data == "radio":
        set_radio(uid, True)
        send_one(chat, uid, radio_track(uid), "📻 YOUR RADIO")
    elif data == "for_you":
        if liked_count(uid) < 10:
            insufficient_likes(chat)
        else:
            send_playlist(chat, uid, pick_playlist(uid, "for_you", liked_only=True), "🧠 FOR YOU — YOUR LIKED PLAYLIST")
    elif data == "track_of_day":
        if liked_count(uid) < 10:
            insufficient_likes(chat)
        else:
            send_playlist(chat, uid, pick_playlist(uid, "track_of_day", liked_only=True), "🎵 TRACK OF THE DAY — YOUR LIKED PLAYLIST")
    elif data == "daily_vibe":
        mood = state(uid)["mood"]
        if mood:
            send_playlist(chat, uid, pick_playlist(uid, "daily_vibe", mood=mood), f"🔥 DAILY VIBE — {INFO[mood][0]}")
        else:
            send(chat, "🎧 Choose a mood first for Daily Vibe.", mood_menu())
    elif data == "next_music":
        st = state(uid)
        send_one(chat, uid, radio_track(uid) if st["radio"] else (pick_playlist(uid, "daily_vibe", mood=st["mood"], limit=1)[0] if st["mood"] and pick_playlist(uid, "daily_vibe", mood=st["mood"], limit=1) else None), "⏭ NEXT")
    elif data.startswith("like:") or data.startswith("notme:"):
        parts = data.split(":", 3)
        if len(parts) == 4:
            set_feedback(uid, parts[1], parts[2], int(parts[3]), "like" if parts[0] == "like" else "not_for_me")
            send(chat, "✅ Feedback saved.")
    else:
        send(chat, "🎧 Choose an option.", mood_menu())


def handle_message(update):
    msg = update.get("message", {})
    user, chat = msg.get("from", {}), msg.get("chat", {}).get("id")
    if not user or not isinstance(chat, int):
        return
    register(user)
    text = (msg.get("text") or "").strip().lower().split("@", 1)[0]
    if text in ("/start", "/mood"):
        send(chat, "🎧 NOT YOUR VIBE\nChoose your mood.", mood_menu())
    elif text == "/radio":
        set_radio(user["id"], True); send_one(chat, user["id"], radio_track(user["id"]), "📻 YOUR RADIO")
    elif text in ("/foryou", "/today"):
        if liked_count(user["id"]) < 10:
            insufficient_likes(chat)
        else:
            feature = "for_you" if text == "/foryou" else "track_of_day"
            title = "🧠 FOR YOU — YOUR LIKED PLAYLIST" if feature == "for_you" else "🎵 TRACK OF THE DAY — YOUR LIKED PLAYLIST"
            send_playlist(chat, user["id"], pick_playlist(user["id"], feature, liked_only=True), title)
    elif text == "/dailyvibe":
        mood = state(user["id"])["mood"]
        send_playlist(chat, user["id"], pick_playlist(user["id"], "daily_vibe", mood=mood), f"🔥 DAILY VIBE — {INFO[mood][0]}") if mood else send(chat, "🎧 Choose a mood first.", mood_menu())
    elif text == "/next":
        st = state(user["id"]); row = radio_track(user["id"]) if st["radio"] else (pick_playlist(user["id"], "daily_vibe", mood=st["mood"], limit=1)[0] if st["mood"] else None); send_one(chat, user["id"], row, "⏭ NEXT")
    else:
        send(chat, "🎧 Choose an option.", mood_menu())


@APP.post("/webhook")
def webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return {"ok": False}, 403
    update = request.get_json(silent=True) or {}
    try:
        if "callback_query" in update:
            handle_callback(update)
        elif "message" in update:
            handle_message(update)
    except Exception:
        LOG.exception("update failed")
    return {"ok": True}


@APP.get("/")
def health():
    return "OK"


if __name__ == "__main__":
    init_db()
    APP.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
