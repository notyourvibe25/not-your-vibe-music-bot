from __future__ import annotations
import asyncio
import json
import logging
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Optional
import requests
from flask import Flask, request
from psycopg2 import InterfaceError, OperationalError
from psycopg2.extras import RealDictCursor
from psycopg2.pool import PoolError, ThreadedConnectionPool
from telethon import TelegramClient, events
from telethon.sessions import StringSession
# ============================================================
# APP
# ============================================================
app = Flask(__name__)
# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    level=(os.getenv("LOG_LEVEL") or "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
)
logger = logging.getLogger("not-your-vibe-bot")
# ============================================================
# ENV HELPERS
# ============================================================
def env_text(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()
def env_int(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = env_text(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid %s. Using default %s",
            name,
            default,
        )
        return default
    if minimum <= value <= maximum:
        return value
    logger.warning(
        "%s out of range. Using default %s",
        name,
        default,
    )
    return default
def env_bool(
    name: str,
    default: bool = False,
) -> bool:
    raw = env_text(name).lower()
    if not raw:
        return default
    return raw in {
        "1",
        "true",
        "yes",
        "on",
    }
# ============================================================
# TELEGRAM
# ============================================================
BOT_TOKEN = env_text("BOT_TOKEN")
ADMIN_USER_ID = env_text("ADMIN_USER_ID")
RENDER_EXTERNAL_URL = env_text(
    "RENDER_EXTERNAL_URL"
)
if not RENDER_EXTERNAL_URL:
    hostname = env_text(
        "RENDER_EXTERNAL_HOSTNAME"
    )
    if hostname:
        RENDER_EXTERNAL_URL = (
            f"https://{hostname}"
        )
WEBHOOK_SECRET = env_text(
    "TELEGRAM_WEBHOOK_SECRET"
)
# ============================================================
# DATABASE
# ============================================================
DATABASE_URL = env_text(
    "DATABASE_URL"
)
# ============================================================
# OPENAI
# ============================================================
OPENAI_API_KEY = env_text(
    "OPENAI_API_KEY"
)
OPENAI_MODEL = env_text(
    "OPENAI_MODEL",
    "gpt-5-mini",
)
OPENAI_TIMEOUT = env_int(
    "OPENAI_TIMEOUT",
    40,
    10,
    120,
)
# ============================================================
# TELETHON
#
# Both new and old ENV names are supported.
# ============================================================
TELETHON_API_ID = (
    env_text("TELETHON_API_ID")
    or env_text("API_ID")
    or env_text("TELEGRAM_API_ID")
)
TELETHON_API_HASH = (
    env_text("TELETHON_API_HASH")
    or env_text("API_HASH")
    or env_text("TELEGRAM_API_HASH")
)
TELETHON_SESSION = env_text(
    "TELETHON_SESSION"
)
# ============================================================
# SETTINGS
# ============================================================
HTTP_TIMEOUT = env_int(
    "TELEGRAM_HTTP_TIMEOUT",
    20,
    5,
    120,
)
WORKER_COUNT = env_int(
    "MUSIC_WORKER_COUNT",
    4,
    1,
    16,
)
DB_POOL_MAX_CONNECTIONS = env_int(
    "DB_POOL_MAX_CONNECTIONS",
    8,
    2,
    30,
)
RECENT_HISTORY_LIMIT = env_int(
    "RECENT_HISTORY_LIMIT",
    40,
    5,
    500,
)
TRACK_CANDIDATE_LIMIT = env_int(
    "TRACK_CANDIDATE_LIMIT",
    80,
    10,
    500,
)
AUTO_SCAN_INTERVAL = env_int(
    "AUTO_SCAN_INTERVAL",
    300,
    60,
    3600,
)
AI_BATCH_SIZE = env_int(
    "AI_BATCH_SIZE",
    10,
    1,
    30,
)
AI_SCAN_INTERVAL = env_int(
    "AI_SCAN_INTERVAL",
    8,
    2,
    60,
)
TELETHON_RECONNECT_DELAY = env_int(
    "TELETHON_RECONNECT_DELAY",
    10,
    3,
    120,
)
WEBHOOK_MAX_CONNECTIONS = env_int(
    "WEBHOOK_MAX_CONNECTIONS",
    40,
    1,
    100,
)
DROP_PENDING_UPDATES = env_bool(
    "DROP_PENDING_UPDATES",
    False,
)
# ============================================================
# MOODS
# ============================================================
MOODS = (
    "sad",
    "love",
    "chill",
    "hype",
    "dark",
    "energetic",
    "night",
    "melodic",
)
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
# ============================================================
# 8 MOOD CHANNELS
#
# IMPORTANT:
# You can use USERNAME OR CHANNEL ID.
#
# Example:
# SAD_CHANNEL=sadmooddatabase
#
# Do NOT put @ if you don't want to.
# The code accepts both.
#
# Current known channels are pre-filled where available.
# Change them in Render ENV if needed.
# ============================================================
MOOD_CHANNELS = {
    "sad": env_text(
        "SAD_CHANNEL",
        "sadmooddatabase",
    ),
    "love": env_text(
        "LOVE_CHANNEL",
        "lovemooddatabase",
    ),
    "chill": env_text(
        "CHILL_CHANNEL",
        "chillmooddatabase",
    ),
    "hype": env_text(
        "HYPE_CHANNEL",
        "",
    ),
    "dark": env_text(
        "DARK_CHANNEL",
        "darkmooddatabase",
    ),
    "energetic": env_text(
        "ENERGETIC_CHANNEL",
        "energeticmooddatabase",
    ),
    "night": env_text(
        "NIGHT_CHANNEL",
        "nightdrivemusicdatabase",
    ),
    "melodic": env_text(
        "MELODIC_CHANNEL",
        "melodicmooddatabase",
    ),
}
# ============================================================
# AUDIO TYPES
# ============================================================
AUDIO_EXTENSIONS = (
    ".mp3",
    ".m4a",
    ".flac",
    ".wav",
    ".aac",
    ".ogg",
    ".opus",
    ".mp4",
    ".mkv",
    ".webm",
)
# ============================================================
# GLOBALS
# ============================================================
db_pool: Optional[
    ThreadedConnectionPool
] = None
db_pool_lock = threading.Lock()
telethon_client: Optional[
    TelegramClient
] = None
telethon_ready = threading.Event()
telethon_status_lock = threading.Lock()
telethon_status = "STARTING"
telethon_last_error = ""
telethon_last_connected_at = 0
telethon_thread: Optional[
    threading.Thread
] = None
telethon_start_lock = threading.Lock()
music_executor = ThreadPoolExecutor(
    max_workers=WORKER_COUNT,
    thread_name_prefix="music-worker",
)
pending_users: set[int] = set()
pending_users_lock = threading.Lock()
http_local = threading.local()
CHANNEL_MOOD_MAP: dict[str, str] = {}
CHANNEL_ENTITY_MAP: dict[str, Any] = {}
# ============================================================
# DATABASE
# ============================================================
def normalize_database_url(
    url: str,
) -> str:
    if url.startswith("postgres://"):
        return (
            "postgresql://"
            + url[len("postgres://"):]
        )
    return url
def initialize_db_pool() -> None:
    global db_pool
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is missing"
        )
    with db_pool_lock:
        if db_pool is not None:
            return
        logger.info(
            "Connecting to PostgreSQL..."
        )
        db_pool = ThreadedConnectionPool(
            1,
            DB_POOL_MAX_CONNECTIONS,
            dsn=normalize_database_url(
                DATABASE_URL
            ),
            connect_timeout=10,
            application_name=(
                "not-your-vibe-music-bot"
            ),
        )
        logger.info(
            "🟢 PostgreSQL pool ready"
        )
@contextmanager
def db_connection() -> Iterator[Any]:
    if db_pool is None:
        initialize_db_pool()
    assert db_pool is not None
    connection = None
    try:
        connection = db_pool.getconn()
        connection.autocommit = False
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except (
            OperationalError,
            InterfaceError,
        ):
            try:
                db_pool.putconn(
                    connection,
                    close=True,
                )
            except Exception:
                pass
            connection = db_pool.getconn()
            connection.autocommit = False
        yield connection
        connection.commit()
    except Exception:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        raise
    finally:
        if connection is not None:
            try:
                db_pool.putconn(
                    connection
                )
            except (
                PoolError,
                OperationalError,
                InterfaceError,
            ):
                logger.exception(
                    "Could not return DB connection"
                )
@contextmanager
def db_cursor(
    connection: Any,
) -> Iterator[Any]:
    cursor = connection.cursor(
        cursor_factory=RealDictCursor
    )
    try:
        yield cursor
    finally:
        cursor.close()
# ============================================================
# DATABASE SCHEMA
# ============================================================
def init_db() -> None:
    """Create/upgrade the PostgreSQL schema safely.

    Important: old databases may not have the AI columns yet.  We must
    add/upgrade columns BEFORE creating indexes that reference them.
    """
    initialize_db_pool()

    tables_schema = """
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        first_seen BIGINT NOT NULL,
        last_seen BIGINT NOT NULL,
        total_requests BIGINT NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS tracks (
        id BIGSERIAL PRIMARY KEY,
        mood TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        message_id BIGINT NOT NULL,
        artist TEXT,
        title TEXT,
        raw_text TEXT,
        ai_status TEXT NOT NULL DEFAULT 'pending',
        ai_mood TEXT,
        ai_genres TEXT,
        ai_energy INTEGER,
        ai_valence INTEGER,
        ai_profile TEXT,
        ai_scanned_at BIGINT,
        ai_error TEXT,
        created_at BIGINT NOT NULL,
        UNIQUE(channel_id, message_id)
    );
    CREATE TABLE IF NOT EXISTS user_history (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        track_id BIGINT,
        mood TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        message_id BIGINT NOT NULL,
        sent_at BIGINT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS user_state (
        user_id BIGINT PRIMARY KEY,
        mood TEXT,
        updated_at BIGINT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS user_radio_state (
        user_id BIGINT PRIMARY KEY,
        active BOOLEAN NOT NULL DEFAULT FALSE,
        updated_at BIGINT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS track_feedback (
        user_id BIGINT NOT NULL,
        track_id BIGINT NOT NULL,
        feedback TEXT NOT NULL,
        created_at BIGINT NOT NULL,
        PRIMARY KEY(user_id, track_id)
    );
    CREATE TABLE IF NOT EXISTS processed_updates (
        update_id BIGINT PRIMARY KEY,
        processed_at BIGINT NOT NULL
    );
    """

    with db_connection() as connection, db_cursor(connection) as cursor:
        cursor.execute(tables_schema)

    # Upgrade old installations. Each migration is isolated with a SAVEPOINT
    # so one harmless duplicate/legacy problem cannot abort the transaction.
    alter_statements = [
        "ALTER TABLE tracks ADD COLUMN IF NOT EXISTS artist TEXT",
        "ALTER TABLE tracks ADD COLUMN IF NOT EXISTS title TEXT",
        "ALTER TABLE tracks ADD COLUMN IF NOT EXISTS raw_text TEXT",
        "ALTER TABLE tracks ADD COLUMN IF NOT EXISTS ai_status TEXT",
        "ALTER TABLE tracks ADD COLUMN IF NOT EXISTS ai_mood TEXT",
        "ALTER TABLE tracks ADD COLUMN IF NOT EXISTS ai_genres TEXT",
        "ALTER TABLE tracks ADD COLUMN IF NOT EXISTS ai_energy INTEGER",
        "ALTER TABLE tracks ADD COLUMN IF NOT EXISTS ai_valence INTEGER",
        "ALTER TABLE tracks ADD COLUMN IF NOT EXISTS ai_profile TEXT",
        "ALTER TABLE tracks ADD COLUMN IF NOT EXISTS ai_scanned_at BIGINT",
        "ALTER TABLE tracks ADD COLUMN IF NOT EXISTS ai_error TEXT",
    ]

    with db_connection() as connection, db_cursor(connection) as cursor:
        for i, statement in enumerate(alter_statements):
            savepoint = f"sp_schema_{i}"
            try:
                cursor.execute(f"SAVEPOINT {savepoint}")
                cursor.execute(statement)
                cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception:
                cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
                logger.exception("Database upgrade statement failed: %s", statement)

        # Existing rows from older versions may have NULL AI state.
        cursor.execute(
            "UPDATE tracks SET ai_status='pending' WHERE ai_status IS NULL OR ai_status=''"
        )
        cursor.execute(
            "ALTER TABLE tracks ALTER COLUMN ai_status SET DEFAULT 'pending'"
        )
        cursor.execute(
            "ALTER TABLE tracks ALTER COLUMN ai_status SET NOT NULL"
        )

        # IMPORTANT: create the AI index only AFTER ai_status exists.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_tracks_mood ON tracks(mood)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_tracks_ai_status ON tracks(ai_status)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_user_time "
            "ON user_history(user_id, sent_at DESC, id DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_user "
            "ON track_feedback(user_id, feedback)"
        )
        cursor.execute(
            "DELETE FROM processed_updates WHERE processed_at < %s",
            (int(time.time()) - 604800,),
        )

    logger.info("🟢 PostgreSQL database ready (AI schema verified)")

# ============================================================
# AI API
# ============================================================
def openai_request(
    instructions: str,
    input_text: str,
) -> Optional[str]:
    if not OPENAI_API_KEY:
        return None
    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization":
                    f"Bearer {OPENAI_API_KEY}",
                "Content-Type":
                    "application/json",
            },
            json={
                "model": OPENAI_MODEL,
                "instructions": instructions,
                "input": input_text,
                "temperature": 0.2,
            },
            timeout=OPENAI_TIMEOUT,
        )
        if response.status_code >= 400:
            logger.warning(
                "OpenAI API error %s: %s",
                response.status_code,
                response.text[:500],
            )
            return None
        data = response.json()
        output_text = data.get(
            "output_text"
        )
        if isinstance(
            output_text,
            str,
        ):
            return output_text.strip()
        # Fallback parser.
        outputs = data.get(
            "output",
            [],
        )
        pieces = []
        for item in outputs:
            for content in item.get(
                "content",
                [],
            ):
                text = content.get(
                    "text"
                )
                if text:
                    pieces.append(text)
        result = "\n".join(
            pieces
        ).strip()
        return result or None
    except Exception:
        logger.exception(
            "OpenAI request failed"
        )
        return None
# ============================================================
# AI SONG ANALYSIS
# ============================================================
def ai_analyze_track(
    artist: str,
    title: str,
) -> Optional[dict[str, Any]]:
    instructions = """
You are the music analysis engine for NOT YOUR VIBE MUSIC.
Analyze songs using ONLY the artist name and song title.
Do not invent facts.
Return ONLY valid JSON.
Schema:
{
  "mood": "sad|love|chill|hype|dark|energetic|night|melodic",
  "genres": ["genre1", "genre2"],
  "energy": 1,
  "valence": 1,
  "profile": "short description"
}
energy:
1 = extremely calm
10 = extremely energetic
valence:
1 = very sad/dark
10 = very happy/uplifting
Important:
The mood must be exactly one of the eight allowed moods.
The profile should describe the musical feeling/style useful for
personal music recommendation.
Never create a fake song.
"""
    prompt = (
        f"Artist: {artist}\n"
        f"Title: {title}"
    )
    result = openai_request(
        instructions,
        prompt,
    )
    if not result:
        return None
    try:
        # Remove accidental markdown fences.
        result = result.strip()
        result = re.sub(
            r"^```(?:json)?",
            "",
            result,
            flags=re.I,
        )
        result = re.sub(
            r"```$",
            "",
            result,
        ).strip()
        data = json.loads(result)
        mood = data.get(
            "mood"
        )
        if mood not in MOODS:
            mood = "melodic"
        genres = data.get(
            "genres",
            [],
        )
        if not isinstance(
            genres,
            list,
        ):
            genres = []
        genres = [
            str(x)[:80]
            for x in genres[:8]
        ]
        try:
            energy = int(
                data.get(
                    "energy",
                    5,
                )
            )
        except Exception:
            energy = 5
        try:
            valence = int(
                data.get(
                    "valence",
                    5,
                )
            )
        except Exception:
            valence = 5
        energy = max(
            1,
            min(10, energy),
        )
        valence = max(
            1,
            min(10, valence),
        )
        profile = str(
            data.get(
                "profile",
                "",
            )
        )[:1000]
        return {
            "mood": mood,
            "genres": genres,
            "energy": energy,
            "valence": valence,
            "profile": profile,
        }
    except Exception:
        logger.warning(
            "AI returned invalid JSON: %s",
            result[:500],
        )
        return None
# ============================================================
# AI UPDATE
# ============================================================
def mark_ai_success(
    track_id: int,
    analysis: dict[str, Any],
) -> None:
    try:
        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):
            cursor.execute(
                """
                UPDATE tracks
                SET
                    ai_status='done',
                    ai_mood=%s,
                    ai_genres=%s,
                    ai_energy=%s,
                    ai_valence=%s,
                    ai_profile=%s,
                    ai_scanned_at=%s,
                    ai_error=NULL
                WHERE id=%s
                """,
                (
                    analysis["mood"],
                    json.dumps(
                        analysis["genres"],
                        ensure_ascii=False,
                    ),
                    analysis["energy"],
                    analysis["valence"],
                    analysis["profile"],
                    int(time.time()),
                    track_id,
                ),
            )
    except Exception:
        logger.exception(
            "Could not save AI analysis"
        )
def mark_ai_failed(
    track_id: int,
    error: str,
) -> None:
    try:
        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):
            cursor.execute(
                """
                UPDATE tracks
                SET
                    ai_status='failed',
                    ai_error=%s
                WHERE id=%s
                """,
                (
                    error[:1000],
                    track_id,
                ),
            )
    except Exception:
        logger.exception(
            "Could not save AI failure"
        )
# ============================================================
# AI SCAN ONE
# ============================================================
def scan_track_with_ai(
    track: Mapping[str, Any],
) -> bool:
    track_id = int(
        track["id"]
    )
    artist = (
        track.get("artist")
        or "Unknown Artist"
    )
    title = (
        track.get("title")
        or "Unknown Track"
    )
    analysis = ai_analyze_track(
        artist,
        title,
    )
    if not analysis:
        mark_ai_failed(
            track_id,
            "AI analysis failed",
        )
        return False
    mark_ai_success(
        track_id,
        analysis,
    )
    logger.info(
        (
            "🤖 AI SCAN DONE | "
            "%s - %s | mood=%s"
        ),
        artist,
        title,
        analysis["mood"],
    )
    return True
# ============================================================
# AI SCAN STATUS
# ============================================================
def get_ai_status() -> dict[str, int]:
    result = {
        "total": 0,
        "done": 0,
        "pending": 0,
        "failed": 0,
    }
    try:
        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER(
                        WHERE ai_status='done'
                    ) AS done,
                    COUNT(*) FILTER(
                        WHERE ai_status='pending'
                    ) AS pending,
                    COUNT(*) FILTER(
                        WHERE ai_status='failed'
                    ) AS failed
                FROM tracks
                """
            )
            row = cursor.fetchone()
            if row:
                for key in result:
                    result[key] = int(
                        row[key] or 0
                    )
    except Exception:
        logger.exception(
            "Could not get AI status"
        )
    return result
def get_pending_ai_tracks(
    limit: int,
) -> list[dict[str, Any]]:
    try:
        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):
            cursor.execute(
                """
                SELECT *
                FROM tracks
                WHERE COALESCE(ai_status, 'pending') IN(
                    'pending',
                    'failed'
                )
                ORDER BY id ASC
                LIMIT %s
                """,
                (limit,),
            )
            return [
                dict(row)
                for row in cursor.fetchall()
            ]
    except Exception:
        logger.exception(
            "Could not get AI pending tracks"
        )
        return []
# ============================================================
# BACKGROUND AI SCANNER
# ============================================================
def ai_scanner_worker() -> None:
    if not OPENAI_API_KEY:
        logger.warning(
            "🤖 AI scanner disabled: OPENAI_API_KEY missing"
        )
        return
    logger.info(
        "🤖 AI scanner started"
    )
    while True:
        try:
            tracks = get_pending_ai_tracks(
                AI_BATCH_SIZE
            )
            if not tracks:
                time.sleep(
                    AI_SCAN_INTERVAL
                )
                continue
            for track in tracks:
                try:
                    scan_track_with_ai(
                        track
                    )
                except Exception:
                    logger.exception(
                        "AI track scan failed"
                    )
                time.sleep(1)
        except Exception:
            logger.exception(
                "AI scanner error"
            )
            time.sleep(10)
def start_ai_scanner() -> None:
    thread = threading.Thread(
        target=ai_scanner_worker,
        name="ai-scanner",
        daemon=True,
    )
    thread.start()
# ============================================================
# TELETHON MESSAGE SAVE
# ============================================================
def save_telethon_message(
    mood: str,
    entity: Any,
    message: Any,
) -> bool:
    if not is_music_message(
        message
    ):
        return False
    channel_id = normalize_channel_id(
        entity
    )
    message_id = getattr(
        message,
        "id",
        None,
    )
    if not channel_id or not message_id:
        return False
    raw_text = message_music_text(
        message
    )
    artist, title = (
        extract_artist_title(
            raw_text
        )
    )
    track_id = save_track(
        mood,
        channel_id,
        int(message_id),
        artist,
        title,
        raw_text,
    )
    return track_id is not None
# ============================================================
# SCAN ONE CHANNEL
# ============================================================
async def scan_one_channel(
    mood: str,
    channel_value: str,
) -> int:
    if (
        not channel_value
        or telethon_client is None
    ):
        return 0
    try:
        lookup: Any
        raw = channel_value.strip()
        if raw.startswith("@"):
            lookup = raw
        elif raw.lstrip("-").isdigit():
            lookup = int(raw)
        else:
            lookup = raw
        entity = await (
            telethon_client.get_entity(
                lookup
            )
        )
        normalized = normalize_channel_id(
            entity
        )
        if normalized:
            CHANNEL_MOOD_MAP[
                normalized
            ] = mood
            CHANNEL_ENTITY_MAP[
                normalized
            ] = entity
        found = 0
        logger.info(
            "🔎 Scanning %s channel...",
            mood.upper(),
        )
        async for message in (
            telethon_client.iter_messages(
                entity
            )
        ):
            try:
                if save_telethon_message(
                    mood,
                    entity,
                    message,
                ):
                    found += 1
            except Exception:
                logger.exception(
                    "Message save failed"
                )
        logger.info(
            (
                "🔎 %s scan completed | "
                "new/updated=%s"
            ),
            mood.upper(),
            found,
        )
        return found
    except Exception:
        logger.exception(
            "%s channel scan failed",
            mood.upper(),
        )
        return 0
# ============================================================
# SCAN ALL CHANNELS
# ============================================================
async def scan_all_channels() -> None:
    rebuild_channel_map()
    logger.info(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    logger.info(
        "🔎 FULL CHANNEL SCAN STARTED"
    )
    logger.info(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    for mood in MOODS:
        channel = MOOD_CHANNELS.get(
            mood,
            "",
        )
        if not channel:
            logger.warning(
                "%s channel not configured",
                mood.upper(),
            )
            continue
        await scan_one_channel(
            mood,
            channel,
        )
        await asyncio.sleep(1)
    counts = get_track_counts()
    logger.info(
        "📊 CHANNEL TRACK COUNTS: %s",
        counts,
    )
    logger.info(
        "🔎 FULL CHANNEL SCAN FINISHED"
    )
# ============================================================
# REAL-TIME NEW SONG WATCHER
# ============================================================
def register_telethon_events(
    client: TelegramClient,
) -> None:
    @client.on(
        events.NewMessage(
            incoming=True
        )
    )
    async def new_music_handler(
        event: Any,
    ) -> None:
        try:
            chat_id = getattr(
                event,
                "chat_id",
                None,
            )
            if chat_id is None:
                return
            normalized = (
                normalize_config_channel(
                    str(chat_id)
                )
            )
            if not normalized:
                # Try actual entity ID.
                normalized = (
                    "-100"
                    + str(
                        abs(int(chat_id))
                    )
                )
            mood = (
                CHANNEL_MOOD_MAP.get(
                    normalized
                )
            )
            if not mood:
                # Rebuild in case config changed.
                rebuild_channel_map()
                mood = (
                    CHANNEL_MOOD_MAP.get(
                        normalized
                    )
                )
            if not mood:
                return
            message = event.message
            if not is_music_message(
                message
            ):
                return
            entity = await event.get_chat()
            raw_text = message_music_text(
                message
            )
            artist, title = (
                extract_artist_title(
                    raw_text
                )
            )
            message_id = getattr(
                message,
                "id",
                None,
            )
            if not message_id:
                return
            track_id = save_track(
                mood,
                normalized,
                int(message_id),
                artist,
                title,
                raw_text,
            )
            if track_id:
                logger.info(
                    (
                        "🚀 NEW SONG | "
                        "%s | %s - %s"
                    ),
                    mood.upper(),
                    artist,
                    title,
                )
        except Exception:
            logger.exception(
                "Real-time music event error"
            )
# ============================================================
# PERIODIC CHANNEL SCAN
# ============================================================
async def periodic_scanner() -> None:
    while True:
        try:
            await asyncio.sleep(
                AUTO_SCAN_INTERVAL
            )
            if not telethon_ready.is_set():
                continue
            logger.info(
                "⏰ Periodic channel rescan..."
            )
            await scan_all_channels()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception(
                "Periodic scanner error"
            )
            await asyncio.sleep(10)
# ============================================================
# TELETHON WORKER
# ============================================================
def _set_telethon_status(status: str, error: str = "") -> None:
    global telethon_status, telethon_last_error
    with telethon_status_lock:
        telethon_status = status
        telethon_last_error = error[-500:] if error else ""


def get_telethon_status() -> tuple[str, str, int]:
    with telethon_status_lock:
        return (telethon_status, telethon_last_error, telethon_last_connected_at)


async def _telethon_health_watch(client: TelegramClient) -> None:
    """Keep the Telethon connection healthy and force reconnect when needed."""
    global telethon_last_connected_at

    while True:
        try:
            await asyncio.sleep(15)

            if not client.is_connected():
                telethon_ready.clear()
                _set_telethon_status("RECONNECTING", "Client is not connected")
                logger.warning("⚠️ Telethon health check: disconnected")
                try:
                    await client.connect()
                except Exception as exc:
                    logger.warning("⚠️ Telethon reconnect attempt failed: %s", exc)
                    continue

                if client.is_connected():
                    if await client.is_user_authorized():
                        telethon_ready.set()
                        telethon_last_connected_at = int(time.time())
                        _set_telethon_status("CONNECTED")
                        logger.info("🟢 Telethon health check: reconnected")
                    else:
                        telethon_ready.clear()
                        _set_telethon_status("UNAUTHORIZED", "Telethon session is unauthorized")
                        logger.error("❌ Telethon session became unauthorized")
                continue

            # A lightweight request proves that the connection can actually
            # communicate with Telegram, rather than only reporting a socket
            # as connected.
            try:
                await client.get_me()
            except Exception as exc:
                telethon_ready.clear()
                _set_telethon_status("RECONNECTING", str(exc))
                logger.warning("⚠️ Telethon health request failed: %s", exc)
                try:
                    await client.disconnect()
                except Exception:
                    pass
                continue

            if not telethon_ready.is_set():
                telethon_ready.set()
                telethon_last_connected_at = int(time.time())
                _set_telethon_status("CONNECTED")

        except asyncio.CancelledError:
            return
        except Exception as exc:
            telethon_ready.clear()
            _set_telethon_status("RECONNECTING", str(exc))
            logger.exception("Telethon health watcher error")


def telethon_worker() -> None:
    global telethon_client, telethon_last_connected_at

    if not TELETHON_API_ID or not TELETHON_API_HASH or not TELETHON_SESSION:
        _set_telethon_status("NOT_CONFIGURED", "Missing TELETHON_API_ID/API_HASH/SESSION")
        logger.error("❌ Telethon configuration missing")
        return

    try:
        api_id = int(TELETHON_API_ID)
    except (TypeError, ValueError):
        _set_telethon_status("NOT_CONFIGURED", "TELETHON_API_ID must be numeric")
        logger.error("❌ TELETHON_API_ID must be numeric")
        return

    async def runner() -> None:
        global telethon_client, telethon_last_connected_at

        while True:
            client = None
            scanner_task = None
            health_task = None

            try:
                _set_telethon_status("CONNECTING")
                logger.info("🔌 Creating Telethon client...")

                client = TelegramClient(
                    StringSession(TELETHON_SESSION),
                    api_id,
                    TELETHON_API_HASH,
                    connection_retries=-1,
                    retry_delay=5,
                    request_retries=5,
                    timeout=30,
                    auto_reconnect=True,
                    flood_sleep_threshold=60,
                    sequential_updates=False,
                )

                telethon_client = client
                register_telethon_events(client)

                logger.info("🔌 Connecting Telethon...")
                await client.connect()

                if not client.is_connected():
                    raise ConnectionError("Telethon connect() returned disconnected")

                if not await client.is_user_authorized():
                    telethon_ready.clear()
                    _set_telethon_status("UNAUTHORIZED", "Telethon session is unauthorized")
                    logger.error("❌ Telethon session is unauthorized")
                    return

                telethon_ready.set()
                telethon_last_connected_at = int(time.time())
                _set_telethon_status("CONNECTED")
                logger.info("🟢 TELETHON CONNECTED")

                try:
                    await scan_all_channels()
                except Exception:
                    logger.exception("Initial channel scan failed")

                scanner_task = asyncio.create_task(periodic_scanner())
                health_task = asyncio.create_task(_telethon_health_watch(client))
                logger.info("👀 Real-time watcher ACTIVE")
                logger.info("❤️ Telethon health watcher ACTIVE")

                # This future completes when the client is disconnected.
                await client.run_until_disconnected()

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                telethon_ready.clear()
                _set_telethon_status("RECONNECTING", str(exc))
                logger.exception("⚠️ Telethon connection error")

            finally:
                telethon_ready.clear()

                if scanner_task is not None:
                    scanner_task.cancel()
                    try:
                        await scanner_task
                    except (asyncio.CancelledError, Exception):
                        pass

                if health_task is not None:
                    health_task.cancel()
                    try:
                        await health_task
                    except (asyncio.CancelledError, Exception):
                        pass

                if client is not None:
                    try:
                        if client.is_connected():
                            await client.disconnect()
                    except Exception:
                        pass

                if telethon_client is client:
                    telethon_client = None

            _set_telethon_status("RECONNECTING")
            logger.warning(
                "🔄 Telethon disconnected. Reconnecting in %s seconds...",
                TELETHON_RECONNECT_DELAY,
            )
            await asyncio.sleep(max(3, TELETHON_RECONNECT_DELAY))

    while True:
        try:
            asyncio.run(runner())
            return
        except Exception as exc:
            telethon_ready.clear()
            _set_telethon_status("RESTARTING", str(exc))
            logger.exception("❌ Telethon worker crashed. Restarting...")
            time.sleep(max(3, TELETHON_RECONNECT_DELAY))


def start_telethon_worker() -> None:
    global telethon_thread

    with telethon_start_lock:
        if telethon_thread and telethon_thread.is_alive():
            return

        telethon_thread = threading.Thread(
            target=telethon_worker,
            name="telethon-worker",
            daemon=True,
        )
        telethon_thread.start()

# ============================================================
# TRACK COUNTS
# ============================================================
def get_track_counts() -> dict[str, int]:
    counts = {
        mood: 0
        for mood in MOODS
    }
    try:
        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):
            cursor.execute(
                """
                SELECT mood, COUNT(*) AS count
                FROM tracks
                GROUP BY mood
                """
            )
            for row in cursor.fetchall():
                mood = row["mood"]
                if mood in counts:
                    counts[mood] = int(
                        row["count"]
                    )
    except Exception:
        logger.exception(
            "Could not get track counts"
        )
    return counts
# ============================================================
# FEEDBACK
#
# ❤️ = like
# 😴 = unlike
# ============================================================
def save_feedback(
    user_id: int,
    track_id: int,
    feedback: str,
) -> bool:
    if feedback not in (
        "like",
        "unlike",
    ):
        return False
    try:
        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):
            cursor.execute(
                """
                INSERT INTO track_feedback(
                    user_id,
                    track_id,
                    feedback,
                    created_at
                )
                VALUES(%s,%s,%s,%s)
                ON CONFLICT(
                    user_id,
                    track_id
                )
                DO UPDATE SET
                    feedback=EXCLUDED.feedback,
                    created_at=EXCLUDED.created_at
                """,
                (
                    user_id,
                    track_id,
                    feedback,
                    int(time.time()),
                ),
            )
        return True
    except Exception:
        logger.exception(
            "Could not save feedback"
        )
        return False
def get_user_likes(
    user_id: int,
    limit: int = 30,
) -> list[dict[str, Any]]:
    try:
        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):
            cursor.execute(
                """
                SELECT
                    t.*
                FROM track_feedback f
                JOIN tracks t
                    ON t.id=f.track_id
                WHERE f.user_id=%s
                AND f.feedback='like'
                ORDER BY
                    f.created_at DESC
                LIMIT %s
                """,
                (
                    user_id,
                    limit,
                ),
            )
            return [
                dict(row)
                for row in cursor.fetchall()
            ]
    except Exception:
        logger.exception(
            "Could not get user likes"
        )
        return []
def get_user_unlikes(
    user_id: int,
    limit: int = 100,
) -> set[int]:
    try:
        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):
            cursor.execute(
                """
                SELECT track_id
                FROM track_feedback
                WHERE user_id=%s
                AND feedback='unlike'
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (
                    user_id,
                    limit,
                ),
            )
            return {
                int(row["track_id"])
                for row in cursor.fetchall()
            }
    except Exception:
        return set()
# ============================================================
# HISTORY
# ============================================================
def save_history(
    user_id: int,
    track: Mapping[str, Any],
) -> None:
    try:
        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):
            cursor.execute(
                """
                INSERT INTO user_history(
                    user_id,
                    track_id,
                    mood,
                    channel_id,
                    message_id,
                    sent_at
                )
                VALUES(
                    %s,%s,%s,%s,%s,%s
                )
                """,
                (
                    user_id,
                    int(track["id"]),
                    track["mood"],
                    str(track["channel_id"]),
                    int(track["message_id"]),
                    int(time.time()),
                ),
            )
    except Exception:
        logger.exception(
            "Could not save history"
        )
def get_recent_track_ids(
    user_id: int,
    limit: int = 40,
) -> set[int]:
    try:
        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):
            cursor.execute(
                """
                SELECT track_id
                FROM user_history
                WHERE user_id=%s
                AND track_id IS NOT NULL
                ORDER BY
                    sent_at DESC,
                    id DESC
                LIMIT %s
                """,
                (
                    user_id,
                    limit,
                ),
            )
            return {
                int(row["track_id"])
                for row in cursor.fetchall()
            }
    except Exception:
        return set()
# ============================================================
# NORMAL TRACK RESERVATION
# ============================================================
def reserve_track(
    user_id: int,
    mood: str,
) -> Optional[dict[str, Any]]:
    if mood not in MOODS:
        return None
    try:
        with db_connection() as connection:
            with db_cursor(connection) as cursor:
                cursor.execute(
                    """
                    SELECT pg_advisory_xact_lock(%s)
                    """,
                    (user_id,),
                )
                recent_ids = get_recent_track_ids(
                    user_id,
                    RECENT_HISTORY_LIMIT,
                )
                cursor.execute(
                    """
                    SELECT *
                    FROM tracks
                    WHERE mood=%s
                    ORDER BY RANDOM()
                    LIMIT %s
                    """,
                    (
                        mood,
                        TRACK_CANDIDATE_LIMIT,
                    ),
                )
                rows = [
                    dict(row)
                    for row in cursor.fetchall()
                ]
                if not rows:
                    return None
                unliked = get_user_unlikes(
                    user_id
                )
                candidates = [
                    row
                    for row in rows
                    if (
                        int(row["id"])
                        not in recent_ids
                        and int(row["id"])
                        not in unliked
                    )
                ]
                if not candidates:
                    candidates = [
                        row
                        for row in rows
                        if int(row["id"])
                        not in unliked
                    ]
                if not candidates:
                    candidates = rows
                track = random.choice(
                    candidates
                )
                save_history(
                    user_id,
                    track,
                )
                return track
    except Exception:
        logger.exception(
            "Could not reserve track"
        )
        return None
# ============================================================
# AI PERSONAL RADIO
# ============================================================
def build_radio_candidate_text(
    tracks: list[dict[str, Any]],
) -> str:
    lines = []
    for track in tracks:
        genres = track.get(
            "ai_genres"
        )
        if isinstance(
            genres,
            str,
        ):
            try:
                genres = json.loads(
                    genres
                )
            except Exception:
                genres = []
        if not isinstance(
            genres,
            list,
        ):
            genres = []
        lines.append(
            (
                f"ID={track['id']} | "
                f"Artist={track.get('artist') or 'Unknown'} | "
                f"Title={track.get('title') or 'Unknown'} | "
                f"Mood={track.get('mood')} | "
                f"AI Mood={track.get('ai_mood')} | "
                f"Genres={','.join(map(str, genres))} | "
                f"Energy={track.get('ai_energy')} | "
                f"Valence={track.get('ai_valence')} | "
                f"Profile={track.get('ai_profile') or ''}"
            )
        )
    return "\n".join(lines)
def ai_choose_radio_track(
    likes: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> Optional[int]:
    if not OPENAI_API_KEY:
        return None
    if not candidates:
        return None
    liked_text = build_radio_candidate_text(
        likes
    )
    candidate_text = build_radio_candidate_text(
        candidates
    )
    instructions = """
You are the recommendation engine for
NOT YOUR VIBE MUSIC.
The user has liked some songs.
Choose ONE candidate track that best matches
the user's liked music.
Use:
- artist similarity
- title/style clues
- mood
- genre
- energy
- valence
- AI profile
IMPORTANT:
You may ONLY choose an ID from the candidate list.
Never invent an ID.
Return ONLY JSON:
{
  "track_id": 123,
  "reason": "short reason"
}
"""
    prompt = (
        "LIKED TRACKS:\n"
        + liked_text
        + "\n\n"
        + "CANDIDATES:\n"
        + candidate_text
    )
    result = openai_request(
        instructions,
        prompt,
    )
    if not result:
        return None
    try:
        result = re.sub(
            r"^```(?:json)?",
            "",
            result.strip(),
            flags=re.I,
        )
        result = re.sub(
            r"```$",
            "",
            result,
        ).strip()
        data = json.loads(
            result
        )
        track_id = int(
            data["track_id"]
        )
        allowed = {
            int(track["id"])
            for track in candidates
        }
        if track_id in allowed:
            return track_id
    except Exception:
        logger.warning(
            "Invalid AI radio response: %s",
            result[:500],
        )
    return None
def reserve_radio_track(
    user_id: int,
) -> Optional[dict[str, Any]]:
    """
    Personal Radio selection:
    1. Prefer tracks the user has NEVER been served.
    2. Do NOT prioritize liked tracks.
    3. Respect NOT-for-me tracks.
    4. Avoid recently served tracks when possible.
    5. If all new tracks are exhausted, reuse older tracks.
    """
    try:
        recent_ids = get_recent_track_ids(
            user_id,
            RECENT_HISTORY_LIMIT,
        )
        unlikes = get_user_unlikes(user_id)

        with db_connection() as connection:
            with db_cursor(connection) as cursor:
                cursor.execute(
                    """
                    SELECT pg_advisory_xact_lock(%s)
                    """,
                    (user_id,),
                )

                # -------------------------------------------------
                # NEW / UNSERVED POOL
                # These tracks have never been served to this user.
                # Liked tracks are intentionally NOT given priority.
                # -------------------------------------------------
                cursor.execute(
                    """
                    SELECT t.*
                    FROM tracks t
                    WHERE t.id <> ALL(%s)
                      AND NOT EXISTS (
                          SELECT 1
                          FROM user_history h
                          WHERE h.user_id = %s
                            AND h.track_id = t.id
                            AND h.action = 'served'
                      )
                    ORDER BY RANDOM()
                    LIMIT %s
                    """,
                    (
                        list(unlikes) or [-1],
                        user_id,
                        TRACK_CANDIDATE_LIMIT,
                    ),
                )
                new_candidates = [
                    dict(row)
                    for row in cursor.fetchall()
                ]

                # Prefer the selected mood only when it has new tracks.
                selected_mood = get_user_mood(user_id)
                if selected_mood and new_candidates:
                    mood_new = [
                        track
                        for track in new_candidates
                        if track.get("mood") == selected_mood
                    ]
                    if mood_new:
                        new_candidates = mood_new

                if new_candidates:
                    chosen = random.choice(new_candidates)
                    save_history(user_id, chosen)
                    return chosen

                # -------------------------------------------------
                # FALLBACK: already-served tracks.
                # Still avoid NOT-for-me and recent tracks first.
                # -------------------------------------------------
                cursor.execute(
                    """
                    SELECT t.*
                    FROM tracks t
                    WHERE t.id <> ALL(%s)
                    ORDER BY RANDOM()
                    LIMIT %s
                    """,
                    (
                        list(unlikes) or [-1],
                        TRACK_CANDIDATE_LIMIT * 2,
                    ),
                )
                fallback = [
                    dict(row)
                    for row in cursor.fetchall()
                ]

                non_recent = [
                    track
                    for track in fallback
                    if int(track["id"]) not in recent_ids
                ]

                candidates = non_recent or fallback
                if not candidates:
                    return None

                if selected_mood:
                    mood_candidates = [
                        track
                        for track in candidates
                        if track.get("mood") == selected_mood
                    ]
                    if mood_candidates:
                        candidates = mood_candidates

                chosen = random.choice(candidates)
                save_history(user_id, chosen)
                return chosen

    except Exception:
        logger.exception(
            "Could not reserve radio track"
        )
        return None
# ============================================================
# TELEGRAM HTTP
# ============================================================
def get_http_session() -> requests.Session:
    session = getattr(
        http_local,
        "session",
        None,
    )
    if session is None:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent":
                    "NOT-YOUR-VIBE-MUSIC-BOT/4.0"
            }
        )
        http_local.session = session
    return session
def telegram(
    method: str,
    data: Optional[
        dict[str, Any]
    ] = None,
    timeout: int = HTTP_TIMEOUT,
) -> dict[str, Any]:
    if not BOT_TOKEN:
        return {
            "ok": False,
            "description":
                "BOT_TOKEN missing",
        }
    try:
        response = get_http_session().post(
            (
                "https://api.telegram.org/"
                f"bot{BOT_TOKEN}/{method}"
            ),
            json=data or {},
            timeout=timeout,
        )
        try:
            result = response.json()
        except ValueError:
            result = {
                "ok": False,
                "description":
                    (
                        "HTTP "
                        f"{response.status_code}"
                    ),
            }
        if (
            response.status_code >= 400
            or not result.get("ok")
        ):
            logger.warning(
                "Telegram %s failed: %s",
                method,
                result.get(
                    "description",
                    result,
                ),
            )
        return result
    except requests.RequestException as exc:
        logger.warning(
            "Telegram request failed: %s",
            exc,
        )
        return {
            "ok": False,
            "description": str(exc),
        }
# ============================================================
# TELEGRAM UI
# ============================================================
def send_message(
    chat_id: int,
    text: str,
    keyboard: Optional[
        dict[str, Any]
    ] = None,
) -> dict[str, Any]:
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
def answer_callback(
    callback_id: Any,
    text: str = "",
) -> None:
    if not callback_id:
        return
    telegram(
        "answerCallbackQuery",
        {
            "callback_query_id":
                callback_id,
            "text":
                text,
            "show_alert":
                False,
        },
        timeout=8,
    )
def copy_music(
    chat_id: int,
    channel_id: str,
    message_id: int,
) -> dict[str, Any]:
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
        timeout=30,
    )
# ============================================================
# PREMIUM MOOD MENU
# ============================================================
def mood_menu() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "😢  SAD",
                    "callback_data": "mood_sad",
                },
                {
                    "text": "❤️  LOVE",
                    "callback_data": "mood_love",
                },
            ],
            [
                {
                    "text": "🌙  CHILL",
                    "callback_data": "mood_chill",
                },
                {
                    "text": "🔥  HYPE",
                    "callback_data": "mood_hype",
                },
            ],
            [
                {
                    "text": "🖤  DARK",
                    "callback_data": "mood_dark",
                },
                {
                    "text": "⚡  ENERGETIC",
                    "callback_data": "mood_energetic",
                },
            ],
            [
                {
                    "text": "🚗  NIGHT DRIVE",
                    "callback_data": "mood_night",
                },
                {
                    "text": "🌌  MELODIC",
                    "callback_data": "mood_melodic",
                },
            ],
            [
                {
                    "text":
                        "📻  START MY RADIO",
                    "callback_data":
                        "radio_start",
                },
            ],
        ]
    }
# ============================================================
# PREMIUM MUSIC BUTTONS
# ============================================================
def music_buttons(
    track_id: int,
    radio: bool = False,
) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "❤️",
                    "callback_data":
                        f"like_{track_id}",
                },
                {
                    "text":
                        "😴",
                    "callback_data":
                        f"unlike_{track_id}",
                },
            ],
            [
                {
                    "text":
                        "⏭  NEXT",
                    "callback_data":
                        "next_music",
                },
                {
                    "text":
                        "🎛  CHANGE MOOD",
                    "callback_data":
                        "change_mood",
                },
            ],
            [
                {
                    "text":
                        "📻  RADIO",
                    "callback_data":
                        "radio_start",
                },
            ],
            (
                [
                    {
                        "text":
                            "⏹  STOP RADIO",
                        "callback_data":
                            "radio_stop",
                    }
                ]
                if radio
                else []
            ),
        ]
    }
# ============================================================
# SEND TRACK
# ============================================================
def send_track(
    chat_id: int,
    user_id: int,
    track: Mapping[str, Any],
    radio: bool,
) -> bool:
    result = copy_music(
        chat_id,
        str(track["channel_id"]),
        int(track["message_id"]),
    )
    if not result.get("ok"):
        logger.warning(
            "Could not copy track %s",
            track["id"],
        )
        return False
    artist = (
        track.get("artist")
        or "Unknown Artist"
    )
    title = (
        track.get("title")
        or "Unknown Track"
    )
    mood = (
        track.get("ai_mood")
        or track.get("mood")
        or "melodic"
    )
    if radio:
        text = (
            "📻  MY RADIO\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"🎧 {artist}\n"
            f"🎵 {title}\n\n"
            f"{MOOD_NAMES.get(mood, mood)}\n\n"
            "🆕 New / unplayed track ကို ဦးစားပေးရွေးထားပါတယ်။"
        )
    else:
        text = (
            f"{MOOD_NAMES.get(mood, mood)}\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"🎧 {artist}\n"
            f"🎵 {title}\n\n"
            "Enjoy your track. ✨"
        )
    send_message(
        chat_id,
        text,
        music_buttons(
            int(track["id"]),
            radio=radio,
        ),
    )
    return True
# ============================================================
# NORMAL MUSIC WORKER
# ============================================================
def send_music(
    chat_id: int,
    user_id: int,
    mood: str,
) -> None:
    for _ in range(5):
        track = reserve_track(
            user_id,
            mood,
        )
        if not track:
            break
        if send_track(
            chat_id,
            user_id,
            track,
            radio=False,
        ):
            return
    send_message(
        chat_id,
        (
            f"{MOOD_NAMES.get(mood, mood)}\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "⚠️ ဒီ mood ထဲက track ကို "
            "အခု copy မလုပ်နိုင်သေးပါ။\n\n"
            "⏭ NEXT ကို ပြန်နှိပ်ပါ။"
        ),
        mood_menu(),
    )
# ============================================================
# RADIO WORKER
# ============================================================
def send_radio_track(
    chat_id: int,
    user_id: int,
) -> None:
    track = reserve_radio_track(
        user_id
    )
    if not track:
        send_message(
            chat_id,
            (
                "📻  MY RADIO\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "⚠️ Radio အတွက် track "
                "မတွေ့သေးပါ။\n\n"
                "အရင်ဆုံး Mood တစ်ခုရွေးပြီး "
                "track အနည်းငယ်နားထောင်ပါ။"
            ),
            mood_menu(),
        )
        return
    if not send_track(
        chat_id,
        user_id,
        track,
        radio=True,
    ):
        send_message(
            chat_id,
            (
                "📻  MY RADIO\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "⚠️ ဒီ track ကို copy "
                "မလုပ်နိုင်သေးပါ။\n\n"
                "⏭ NEXT ကို ပြန်နှိပ်ပါ။"
            ),
            music_buttons(
                int(track["id"]),
                radio=True,
            ),
        )
# ============================================================
# WORKERS / ANTI DOUBLE CLICK
# ============================================================
def schedule_music(
    chat_id: int,
    user_id: int,
    mood: str,
) -> bool:
    with pending_users_lock:
        if user_id in pending_users:
            return False
        pending_users.add(
            user_id
        )
    def worker():
        try:
            send_music(
                chat_id,
                user_id,
                mood,
            )
        finally:
            with pending_users_lock:
                pending_users.discard(
                    user_id
                )
    try:
        music_executor.submit(
            worker
        )
        return True
    except Exception:
        with pending_users_lock:
            pending_users.discard(
                user_id
            )
        return False
def schedule_radio(
    chat_id: int,
    user_id: int,
) -> bool:
    with pending_users_lock:
        if user_id in pending_users:
            return False
        pending_users.add(
            user_id
        )
    def worker():
        try:
            send_radio_track(
                chat_id,
                user_id,
            )
        finally:
            with pending_users_lock:
                pending_users.discard(
                    user_id
                )
    try:
        music_executor.submit(
            worker
        )
        return True
    except Exception:
        with pending_users_lock:
            pending_users.discard(
                user_id
            )
        return False
# ============================================================
# ADMIN
# ============================================================
def is_admin(
    user_id: Any,
) -> bool:
    return bool(
        ADMIN_USER_ID
        and user_id is not None
        and str(user_id)
        == ADMIN_USER_ID
    )
# ============================================================
# AI STATUS MESSAGE
# ============================================================
def ai_status_text() -> str:
    status = get_ai_status()
    total = status["total"]
    done = status["done"]
    pending = status["pending"]
    failed = status["failed"]
    if total:
        progress = (
            done / total
        ) * 100
    else:
        progress = 0
    if pending == 0:
        if failed == 0:
            state = "🟢 COMPLETED"
        else:
            state = "🟡 COMPLETED WITH ERRORS"
    else:
        state = "🔄 SCANNING"
    ai_online = bool(
        OPENAI_API_KEY
    )
    return (
        "🤖  AI SCANNER\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"{state}\n\n"
        f"🎵 Total: {total}\n"
        f"✅ Analyzed: {done}\n"
        f"⏳ Pending: {pending}\n"
        f"❌ Failed: {failed}\n\n"
        f"📊 Progress: {progress:.1f}%\n\n"
        + (
            "🟢 OpenAI: ONLINE"
            if ai_online
            else
            "🔴 OpenAI: NOT CONFIGURED"
        )
    )
# ============================================================
# ADMIN STATS
# ============================================================
def send_stats(
    chat_id: int,
    user_id: int,
) -> None:
    if not is_admin(user_id):
        send_message(
            chat_id,
            "❌ Admin only.",
        )
        return
    counts = get_track_counts()
    total = sum(
        counts.values()
    )
    text = [
        "💎  NOT YOUR VIBE",
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"👥 Users: {get_users_count()}",
        f"🎵 Total Tracks: {total}",
        "",
        "📡 MOODS",
        "",
    ]
    for mood in MOODS:
        text.append(
            (
                f"{MOOD_NAMES[mood]} "
                f"→ {counts[mood]}"
            )
        )
    text.extend(
        [
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            ai_status_text(),
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            (
                f"🟢 Telegram/Telethon: {get_telethon_status()[0]}"
                if telethon_ready.is_set()
                else
                f"🟡 Telegram/Telethon: {get_telethon_status()[0]}"
            ),
        ]
    )
    send_message(
        chat_id,
        "\n".join(text),
    )
# ============================================================
# CALLBACK
# ============================================================
def handle_callback(
    callback: Mapping[str, Any],
) -> None:
    callback_id = callback.get(
        "id"
    )
    data = (
        callback.get("data")
        or ""
    )
    user = (
        callback.get("from")
        or {}
    )
    message = (
        callback.get("message")
        or {}
    )
    chat = (
        message.get("chat")
        or {}
    )
    chat_id = chat.get(
        "id"
    )
    user_id = user.get(
        "id"
    )
    if not isinstance(
        chat_id,
        int,
    ):
        return
    if not isinstance(
        user_id,
        int,
    ):
        return
    register_user(
        user
    )
    # ========================================================
    # LIKE
    # ========================================================
    if data.startswith(
        "like_"
    ):
        try:
            track_id = int(
                data[5:]
            )
        except ValueError:
            answer_callback(
                callback_id,
                "Invalid track",
            )
            return
        if save_feedback(
            user_id,
            track_id,
            "like",
        ):
            answer_callback(
                callback_id,
                "❤️ Added to your Radio",
            )
            send_message(
                chat_id,
                (
                    "❤️  LIKED\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    "ဒီ track ကို မင်းရဲ့ "
                    "Personal Radio က မှတ်ထားပါပြီ။\n\n"
                    "📻 နောက်တစ်ခါ Radio ဖွင့်ရင် "
                    "ဒီလို style တွေကို ဦးစားပေးပါမယ်။"
                ),
            )
        return
    # ========================================================
    # UNLIKE
    # ========================================================
    if data.startswith(
        "unlike_"
    ):
        try:
            track_id = int(
                data[7:]
            )
        except ValueError:
            answer_callback(
                callback_id,
                "Invalid track",
            )
            return
        if save_feedback(
            user_id,
            track_id,
            "unlike",
        ):
            answer_callback(
                callback_id,
                "😴 Noted",
            )
            send_message(
                chat_id,
                (
                    "😴  SKIPPED\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    "ဒီ track ကို မင်းမကြိုက်တာ "
                    "မှတ်ထားပါပြီ။\n\n"
                    "📻 Personal Radio က "
                    "နောက်တစ်ခါ ဒီ track ကို "
                    "ရှောင်ပေးပါမယ်။"
                ),
            )
        return
    # ========================================================
    # MOOD
    # ========================================================
    if data.startswith(
        "mood_"
    ):
        mood = data[5:]
        if mood not in MOODS:
            answer_callback(
                callback_id,
                "Invalid mood",
            )
            return
        set_radio_state(
            user_id,
            False,
        )
        set_user_mood(
            user_id,
            mood,
        )
        answer_callback(
            callback_id,
            f"{MOOD_NAMES[mood]} ✓",
        )
        send_message(
            chat_id,
            (
                f"{MOOD_NAMES[mood]}\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "🎧 Finding your track..."
            ),
        )
        if not schedule_music(
            chat_id,
            user_id,
            mood,
        ):
            send_message(
                chat_id,
                "⏳ Track is already being prepared.",
            )
        return
    # ========================================================
    # RADIO START
    # ========================================================
    if data == "radio_start":
        set_radio_state(
            user_id,
            True,
        )
        answer_callback(
            callback_id,
            "📻 Radio ON",
        )
        send_message(
            chat_id,
            (
                "📻  MY RADIO\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Your Personal Radio is ON. ✨\n\n"
                "🆕 New / unplayed tracks ကို ဦးစားပေးပါမယ်\n"
                "🚫 NOT for me tracks ကို ရှောင်ပါမယ်\n"
                "🎛 Selected mood ကို baseline အဖြစ်သုံးပါမယ်\n\n"
                "Like လုပ်ထားတဲ့ track တွေကို အထူးဦးစားမပေးပါဘူး။\n"
                "အရင်မကြားရသေးတဲ့ track တွေကို အရင်ရှာပေးပါမယ်။"
            ),
        )
        if not schedule_radio(
            chat_id,
            user_id,
        ):
            send_message(
                chat_id,
                "⏳ Radio track is already being prepared.",
                mood_menu(),
            )
        return
    # ========================================================
    # RADIO STOP
    # ========================================================
    if data == "radio_stop":
        set_radio_state(
            user_id,
            False,
        )
        answer_callback(
            callback_id,
            "Radio stopped",
        )
        send_message(
            chat_id,
            (
                "⏹  RADIO OFF\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Personal Radio stopped."
            ),
            mood_menu(),
        )
        return
    # ========================================================
    # NEXT
    # ========================================================
    if data == "next_music":
        if is_radio_active(
            user_id
        ):
            answer_callback(
                callback_id,
                "📻 Finding next...",
            )
            if not schedule_radio(
                chat_id,
                user_id,
            ):
                answer_callback(
                    callback_id,
                    "⏳ Already preparing",
                )
            return
        mood = get_user_mood(
            user_id
        )
        if not mood:
            answer_callback(
                callback_id,
                "Choose mood first",
            )
            send_message(
                chat_id,
                "🎧 Choose your mood 👇",
                mood_menu(),
            )
            return
        answer_callback(
            callback_id,
            "⏭ Finding next...",
        )
        if not schedule_music(
            chat_id,
            user_id,
            mood,
        ):
            answer_callback(
                callback_id,
                "⏳ Already preparing",
            )
        return
    # ========================================================
    # CHANGE MOOD
    # ========================================================
    if data == "change_mood":
        set_radio_state(
            user_id,
            False,
        )
        answer_callback(
            callback_id,
            "🎛 Choose your mood",
        )
        send_message(
            chat_id,
            (
                "🎛  MOOD SELECTOR\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "What are you feeling right now?"
            ),
            mood_menu(),
        )
        return
    answer_callback(
        callback_id
    )
# ============================================================
# COMMAND
# ============================================================
def extract_command(
    text: str,
) -> str:
    if not text.startswith("/"):
        return ""
    return (
        text
        .split(maxsplit=1)[0]
        .lower()
        .split("@", 1)[0]
    )
# ============================================================
# MESSAGE HANDLER
# ============================================================
def handle_message(
    message: Mapping[str, Any],
) -> None:
    chat = (
        message.get("chat")
        or {}
    )
    user = (
        message.get("from")
        or {}
    )
    chat_id = chat.get(
        "id"
    )
    user_id = user.get(
        "id"
    )
    if not isinstance(
        chat_id,
        int,
    ):
        return
    register_user(
        user
    )
    text = (
        message.get("text")
        or ""
    ).strip()
    command = extract_command(
        text
    )
    # ========================================================
    # START
    # ========================================================
    if command == "/start":
        send_message(
            chat_id,
            (
                "💎  NOT YOUR VIBE MUSIC\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Welcome to your personal "
                "mood music experience. 🎧\n\n"
                "🎛 Choose your mood\n"
                "❤️ Like what you love\n"
                "😴 Skip what you don't like\n"
                "📻 Let AI build your Personal Radio\n\n"
                "👇 SELECT YOUR MOOD"
            ),
            mood_menu(),
        )
        return
    # ========================================================
    # MOOD
    # ========================================================
    if command == "/mood":
        send_message(
            chat_id,
            (
                "🎛  MOOD SELECTOR\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "What are you feeling right now?\n\n"
                "👇 Choose your mood"
            ),
            mood_menu(),
        )
        return
    # ========================================================
    # NEXT
    # ========================================================
    if command == "/next":
        if not isinstance(
            user_id,
            int,
        ):
            return
        if is_radio_active(
            user_id
        ):
            if not schedule_radio(
                chat_id,
                user_id,
            ):
                send_message(
                    chat_id,
                    "⏳ Radio track is already being prepared.",
                )
            return
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
        if not schedule_music(
            chat_id,
            user_id,
            mood,
        ):
            send_message(
                chat_id,
                "⏳ Track ရှာနေပြီးသားပါ။",
            )
        return
    # ========================================================
    # RADIO
    # ========================================================
    if command == "/radio":
        if not isinstance(
            user_id,
            int,
        ):
            return
        set_radio_state(
            user_id,
            True,
        )
        send_message(
            chat_id,
            (
                "📻  MY RADIO\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "AI Personal Radio is ON. ✨\n\n"
                "❤️ Your Likes\n"
                "🎧 Your History\n"
                "🎛 Your Mood\n"
                "🤖 AI Similarity\n\n"
                "အားလုံးကိုအသုံးပြုပြီး "
                "music ရွေးပေးပါမယ်။"
            ),
        )
        if not schedule_radio(
            chat_id,
            user_id,
        ):
            send_message(
                chat_id,
                "⏳ Radio track is already being prepared.",
                mood_menu(),
            )
        return
    # ========================================================
    # STOP RADIO
    # ========================================================
    if command == "/stopradio":
        if isinstance(
            user_id,
            int,
        ):
            set_radio_state(
                user_id,
                False,
            )
        send_message(
            chat_id,
            (
                "⏹  RADIO OFF\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Personal Radio stopped."
            ),
            mood_menu(),
        )
        return
    # ========================================================
    # USERS
    # ========================================================
    if command == "/users":
        if is_admin(
            user_id
        ):
            send_message(
                chat_id,
                (
                    "👥  USER STATISTICS\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    f"Total users: "
                    f"{get_users_count()}"
                ),
            )
        else:
            send_message(
                chat_id,
                "❌ Admin only.",
            )
        return
    # ========================================================
    # STATS
    # ========================================================
    if command == "/stats":
        if isinstance(
            user_id,
            int,
        ):
            send_stats(
                chat_id,
                user_id,
            )
        return
    # ========================================================
    # AI STATUS
    # ========================================================
    if command in (
        "/scan",
        "/aistatus",
    ):
        if not is_admin(
            user_id
        ):
            send_message(
                chat_id,
                "❌ Admin only.",
            )
            return
        send_message(
            chat_id,
            ai_status_text(),
        )
        return
    # ========================================================
    # TELETHON
    # ========================================================
    if command == "/telegram":
        if not is_admin(
            user_id
        ):
            send_message(
                chat_id,
                "❌ Admin only.",
            )
            return
        send_message(
            chat_id,
            (
                "📡  TELEGRAM CHANNEL SCANNER\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                + (
                    "🟢 Telethon: CONNECTED\n"
                    "👀 Watcher: ACTIVE\n"
                    "🚀 New songs: AUTO SAVE\n"
                    "🔄 Reconnect: ON"
                    if telethon_ready.is_set()
                    else
                    "🔴 Telethon: DISCONNECTED"
                )
            ),
        )
        return
    # ========================================================
    # HELP
    # ========================================================
    if command == "/help":
        send_message(
            chat_id,
            (
                "💎  NOT YOUR VIBE MUSIC\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "/start → Start\n"
                "/mood → Mood selector\n"
                "/next → Next track\n"
                "/radio → AI Personal Radio\n"
                "/stopradio → Stop Radio\n\n"
                "❤️ Like → Save your taste\n"
                "😴 Unlike → Avoid track\n\n"
                "👑 ADMIN\n"
                "/users → User count\n"
                "/stats → Bot statistics\n"
                "/scan → AI scan status\n"
                "/telegram → Telethon status"
            ),
        )
        return
# ============================================================
# UPDATE
# ============================================================
def handle_update(
    update: Mapping[str, Any],
) -> None:
    if not claim_update(
        update.get("update_id")
    ):
        return
    callback = update.get(
        "callback_query"
    )
    if isinstance(
        callback,
        Mapping,
    ):
        handle_callback(
            callback
        )
        return
    message = update.get(
        "message"
    )
    if isinstance(
        message,
        Mapping,
    ):
        handle_message(
            message
        )
# ============================================================
# WEB
# ============================================================
@app.route("/")
def home() -> str:
    return (
        "💎 NOT YOUR VIBE MUSIC BOT ONLINE"
    )
@app.route("/health")
def health():
    db_ok = False
    try:
        with (
            db_connection() as connection,
            db_cursor(connection) as cursor
        ):
            cursor.execute(
                "SELECT 1"
            )
            db_ok = True
    except Exception:
        db_ok = False
    if db_ok:
        return (
            "OK",
            200,
        )
    return (
        "Database not ready",
        503,
    )
# ============================================================
# WEBHOOK
# ============================================================
@app.route(
    "/webhook",
    methods=["POST"],
)
def webhook():
    if (
        WEBHOOK_SECRET
        and request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token",
            "",
        )
        != WEBHOOK_SECRET
    ):
        return (
            "Forbidden",
            403,
        )
    try:
        update = request.get_json(
            silent=True
        )
        if isinstance(
            update,
            Mapping,
        ):
            handle_update(
                update
            )
    except Exception:
        logger.exception(
            "Webhook error"
        )
    return (
        "OK",
        200,
    )
# ============================================================
# SET WEBHOOK
# ============================================================
def setup_webhook() -> None:
    if (
        not BOT_TOKEN
        or not RENDER_EXTERNAL_URL
    ):
        logger.warning(
            (
                "Webhook not configured. "
                "BOT_TOKEN or RENDER_EXTERNAL_URL missing."
            )
        )
        return
    payload = {
        "url":
            (
                RENDER_EXTERNAL_URL.rstrip("/")
                + "/webhook"
            ),
        "allowed_updates": [
            "message",
            "callback_query",
        ],
        "drop_pending_updates":
            DROP_PENDING_UPDATES,
        "max_connections":
            WEBHOOK_MAX_CONNECTIONS,
    }
    if WEBHOOK_SECRET:
        payload[
            "secret_token"
        ] = WEBHOOK_SECRET
    result = telegram(
        "setWebhook",
        payload,
        timeout=20,
    )
    if result.get("ok"):
        logger.info(
            "🟢 Telegram webhook configured"
        )
    else:
        logger.error(
            "🔴 Webhook failed: %s",
            result,
        )
# ============================================================
# STARTUP
# ============================================================
def startup() -> bool:
    logger.info(
        "========================================"
    )
    logger.info(
        "💎 NOT YOUR VIBE MUSIC BOT v4"
    )
    logger.info(
        "========================================"
    )
    # --------------------------------------------------------
    # Basic configuration check
    # --------------------------------------------------------
    if not BOT_TOKEN:
        logger.error(
            "❌ BOT_TOKEN missing"
        )
        return False
    if not DATABASE_URL:
        logger.error(
            "❌ DATABASE_URL missing"
        )
        return False
    # --------------------------------------------------------
    # Database
    # --------------------------------------------------------
    try:
        init_db()
    except Exception:
        logger.exception(
            "❌ PostgreSQL initialization failed"
        )
        return False
    # --------------------------------------------------------
    # AI
    # --------------------------------------------------------
    if OPENAI_API_KEY:
        logger.info(
            "🟢 OpenAI AI configured"
        )
    else:
        logger.warning(
            (
                "🟡 OPENAI_API_KEY missing. "
                "AI scanner/recommendation disabled."
            )
        )
    # --------------------------------------------------------
    # Webhook
    # --------------------------------------------------------
    setup_webhook()
    # --------------------------------------------------------
    # Telethon
    # --------------------------------------------------------
    start_telethon_worker()
    # --------------------------------------------------------
    # AI Scanner
    # --------------------------------------------------------
    start_ai_scanner()
    logger.info(
        "🟢 BOT SERVER READY"
    )
    return True
# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    startup()
    port = env_int(
        "PORT",
        10000,
        1,
        65535,
    )
    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True,
        use_reloader=False,
    )