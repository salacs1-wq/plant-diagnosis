import sqlite3

DB_PATH = "plant.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    return conn


def create_tables():

    conn = get_connection()
    cur = conn.cursor()

    # -----------------------
    # PRODUCTS
    # -----------------------

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
        dokumentum_url TEXT,
        forras_url TEXT
    )
    """)

    # -----------------------
    # PRODUCT_USAGES
    # -----------------------

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

    # -----------------------
    # ACTIVE SUBSTANCES
    # -----------------------

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

    print("OK")


if __name__ == "__main__":
    create_tables()
