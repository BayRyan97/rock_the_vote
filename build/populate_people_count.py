"""
Add people_count column to households and populate it in batches.
Runs fast enough to stay under Supabase's 8s statement timeout.
"""
import psycopg2
import sys

DSN = "postgresql://postgres.sqpjghpvgmahbodlkffl:ugSfCdhhtDEXP65k@aws-1-us-west-2.pooler.supabase.com:5432/postgres"

conn = psycopg2.connect(DSN)
conn.autocommit = False
cur = conn.cursor()

# Add column if needed
print("Adding people_count column...")
cur.execute("ALTER TABLE households ADD COLUMN IF NOT EXISTS people_count smallint DEFAULT 0")
conn.commit()
print("Column ready.")

# Fetch all distinct household_ids in people table
print("Fetching distinct household IDs...")
cur.execute("SELECT DISTINCT household_id FROM people WHERE household_id IS NOT NULL")
rows = cur.fetchall()
hh_ids = [r[0] for r in rows]
print(f"Found {len(hh_ids):,} households with voters")

BATCH = 5000
total = len(hh_ids)
for i in range(0, total, BATCH):
    batch = hh_ids[i:i + BATCH]
    cur.execute(
        """
        UPDATE households h
        SET people_count = sub.cnt
        FROM (
            SELECT household_id, COUNT(*)::smallint AS cnt
            FROM people
            WHERE household_id = ANY(%s::uuid[])
            GROUP BY household_id
        ) sub
        WHERE h.id = sub.household_id
        """,
        (batch,)
    )
    conn.commit()
    done = min(i + BATCH, total)
    pct = done / total * 100
    print(f"  {done:,}/{total:,}  ({pct:.1f}%)", flush=True)

cur.close()
conn.close()
print("Done.")
