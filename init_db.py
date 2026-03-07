# init_db.py

from db import get_connection


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            termek TEXT,
            nev_eredeti TEXT,
            engedelyszam TEXT,
            hatoanyagok TEXT,
            mehveszelyesseg TEXT,
            AKG TEXT,
            AOP1 TEXT,
            AOP4 TEXT,
            AOP5 TEXT,
            kiszereles TEXT,
            ar_kedvezmenyes REAL,
            pack_amount REAL,
            price_per_ha_kedv REAL,
            okirat_pdf_url TEXT,
            okirat_frissites_datum TEXT
        )
    """)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("products table ready")
