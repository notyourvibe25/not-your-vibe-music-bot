import os
import sys
import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

def connect_database():
if not DATABASE_URL:
print("ERROR: DATABASE_URL is not set.")
sys.exit(1)

print("Connecting to PostgreSQL...")

return psycopg.connect(
    DATABASE_URL,
    row_factory=dict_row,
    connect_timeout=20,
)

def main():
print("=" * 50)
print("NOT YOUR VIBE AUDIO ANALYZER")
print("PHASE 1 - DATABASE TEST")
print("=" * 50)

try:
    with connect_database() as conn:
        print("PostgreSQL connection: OK")

        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) AS total_tracks,
                    COUNT(*) FILTER (
                        WHERE analyzed = TRUE
                    ) AS analyzed_tracks,
                    COUNT(*) FILTER (
                        WHERE analyzed = FALSE
                           OR analyzed IS NULL
                    ) AS pending_tracks
                FROM tracks
            """)

            result = cur.fetchone()

            total = result["total_tracks"] or 0
            analyzed = result["analyzed_tracks"] or 0
            pending = result["pending_tracks"] or 0

            print()
            print(f"Total tracks    : {total}")
            print(f"Analyzed tracks : {analyzed}")
            print(f"Pending tracks  : {pending}")
            print()

            cur.execute("""
                SELECT
                    id,
                    title,
                    artist,
                    channel_id,
                    message_id,
                    analyzed
                FROM tracks
                ORDER BY id
                LIMIT 5
            """)

            rows = cur.fetchall()

            print("First 5 tracks:")
            print("-" * 50)

            for row in rows:
                print(
                    f"ID={row['id']} | "
                    f"Title={row['title'] or 'Unknown'} | "
                    f"Artist={row['artist'] or 'Unknown'} | "
                    f"Message={row['message_id']} | "
                    f"Analyzed={row['analyzed']}"
                )

            print("-" * 50)
            print()
            print("DATABASE TEST SUCCESSFUL")
            print("No tracks were modified.")
            print("No Telegram files were downloaded.")

except Exception as e:
    print()
    print("DATABASE TEST FAILED")
    print(f"ERROR: {type(e).__name__}: {e}")
    sys.exit(1)

if name == "main":
main()
