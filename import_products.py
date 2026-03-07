# import_products.py

from db import get_connection

def insert_sample_product():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO products (
            termek,
            nev_eredeti,
            engedelyszam,
            hatoanyagok,
            mehveszelyesseg,
            AKG,
            AOP1,
            AOP4,
            AOP5,
            kiszereles,
            ar_kedvezmenyes,
            pack_amount,
            price_per_ha_kedv,
            okirat_pdf_url,
            okirat_frissites_datum
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "TESZT TERMÉK",
        "TESZT TERMÉK",
        "TEST-001",
        "hatóanyag teszt",
        "nem jelölésköteles",
        "igen",
        "",
        "",
        "",
        "5 L",
        100000,
        5,
        4000,
        "",
        "2026-03-07"
    ))

    conn.commit()
    conn.close()


if __name__ == "__main__":
    insert_sample_product()
    print("sample product inserted")
