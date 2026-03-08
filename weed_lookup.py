import sqlite3

DB_PATH = "plant_diagnosis.db"


def find_weed_by_scientific_name(scientific_name: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT id, scientific_name, hungarian_name
        FROM weed_species_master
        WHERE lower(scientific_name) = lower(?)
        LIMIT 1
    """, (scientific_name,))

    row = cur.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None
