from db import get_connection


def create_tables():
    conn = get_connection()
    cur = conn.cursor()

    # RESET TABLES
    cur.execute("DROP TABLE IF EXISTS product_active_substances")
    cur.execute("DROP TABLE IF EXISTS product_usages")
    cur.execute("DROP TABLE IF EXISTS products")

    # PRODUCTS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        termek TEXT,
        rendeltetes TEXT,
        hatoanyagok TEXT,
        engedelyszam TEXT,
        engedely_tipus TEXT,
        formulacio TEXT,
        forgalmi_kategoria TEXT,
        kiszereles TEXT,
        akg INTEGER,
        aop INTEGER,
        aop1 INTEGER,
        aop4 INTEGER,
        aop5 INTEGER,
        mehveszelyesseg TEXT,
        tulajdonos TEXT,
        hazai_kepviselet TEXT,
        forgalmazo TEXT,
        gyarto TEXT,
        agromedium_id TEXT,
        agromedium_url TEXT,
        forras_url TEXT
    )
    """)

    # PRODUCT_USAGES
    cur.execute("""
    CREATE TABLE IF NOT EXISTS product_usages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        termek TEXT,
        kultura TEXT,
        karosito TEXT,
        dozis TEXT,
        kezelesek_max_szama TEXT,
        kezeles_ideje TEXT,
        le_mennyiseg TEXT,
        elelmezes_egeszsegugyi_varakozasi_ido TEXT,
        munkaegeszsegugyi_varakozasi_ido TEXT,
        forras_url TEXT
    )
    """)

    # ACTIVE SUBSTANCES
    cur.execute("""
    CREATE TABLE IF NOT EXISTS product_active_substances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        termek TEXT,
        hatoanyag_nev TEXT,
        mennyiseg TEXT,
        hatoanyag_csoport TEXT,
        hatasmod TEXT,
        rac_besorolas TEXT,
        forras_url TEXT
    )
    """)

    conn.commit()
    conn.close()
