from db import get_connection


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # products
    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            termek TEXT,
            nev_eredeti TEXT,
            engedelyszam TEXT,
            engedely_tipus TEXT,
            hatoanyagok TEXT,
            forgalmi_kategoria_nebih TEXT,
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

    # product_usage
    cur.execute("""
        CREATE TABLE IF NOT EXISTS product_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            scope_kultura TEXT,
            kultura_lista TEXT,
            karosito_lista TEXT,
            dozis TEXT,
            vizmennyiseg TEXT,
            max_kezeles_szam TEXT,
            min_kezelesi_intervallum TEXT,
            elelmezeseu_varakozasi_ido TEXT,
            munkaegeszsegugyi_varakozasi_ido TEXT,
            scope_megjegyzes TEXT
        )
    """)

    # product_weed_species
    cur.execute("""
        CREATE TABLE IF NOT EXISTS product_weed_species (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            crop TEXT,
            weed_latin TEXT,
            weed_hungarian TEXT,
            source_type TEXT,
            source_name TEXT,
            priority INTEGER,
            note TEXT
        )
    """)

    # weed_species_master
    cur.execute("""
        CREATE TABLE IF NOT EXISTS weed_species_master (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            weed_latin TEXT,
            weed_hungarian TEXT,
            group_type TEXT,
            main_crop TEXT,
            notes TEXT
        )
    """)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("database ready")
