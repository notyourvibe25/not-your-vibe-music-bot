import os
import asyncio
import sqlite3
import time

from telethon import TelegramClient


# ============================================================
# ENV
# ============================================================

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")

SESSION_NAME = os.getenv(
    "SESSION_NAME",
    "notyourvibe_importer"
)

DB_PATH = os.getenv(
    "DB_PATH",
    "/data/notyourvibe.db"
)


# ============================================================
# CHANNELS
# ============================================================

CHANNELS = {

    "sad": "@sadmooddatabase",

    "love": "@lovemooddatabase",

    "chill": "@chillmooddatabase",

    "hype": -1004427220481,

    "dark": "@darkmooddatabase",

    "energetic": "@energeticmooddatabase",

    "night": "@nightdrivemooddatabase",

    "melodic": -1004446996297,
}


# ============================================================
# DATABASE
# ============================================================

def add_track(
    mood,
    channel_key,
    message_id
):

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    try:

        conn.execute("""
            INSERT OR IGNORE INTO tracks (
                mood,
                channel_key,
                message_id,
                created_at
            )

            VALUES (?, ?, ?, ?)
        """, (
            mood,
            str(channel_key),
            message_id,
            int(time.time())
        ))

        conn.commit()

    finally:

        conn.close()


# ============================================================
# IMPORT
# ============================================================

async def import_channel(
    client,
    mood,
    channel
):

    print()
    print("=" * 60)
    print("MOOD:", mood)
    print("CHANNEL:", channel)
    print("=" * 60)

    entity = await client.get_entity(channel)

    count = 0

    async for message in client.iter_messages(entity):

        # Empty service messages skip
        if not message:
            continue

        # Text-only message လည်း track အဖြစ်
        # သိမ်းနိုင်တယ်။
        #
        # Music/file/photo/document/video စတာတွေ
        # အားလုံး copyMessage နဲ့ ပြန်ပို့နိုင်ပါတယ်။

        add_track(
            mood=mood,
            channel_key=str(channel),
            message_id=message.id
        )

        count += 1

        if count % 100 == 0:

            print(
                f"{mood}: imported {count}"
            )

    print(
        f"FINISHED {mood}: {count} messages"
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    client = TelegramClient(
        SESSION_NAME,
        API_ID,
        API_HASH
    )

    await client.start()

    print()
    print("Telegram session connected.")
    print()

    for mood, channel in CHANNELS.items():

        try:

            await import_channel(
                client,
                mood,
                channel
            )

        except Exception as exc:

            print(
                f"ERROR importing {mood}: {exc}"
            )

    await client.disconnect()

    print()
    print("====================================")
    print("IMPORT COMPLETED")
    print("====================================")


if __name__ == "__main__":

    asyncio.run(
        main()
)
