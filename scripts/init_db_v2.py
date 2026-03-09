from db import get_connection


def create_tables():

    conn = get_connection()
    cur = conn.cursor()

    # ===== RESET TABLES =====

    cur.execute("DROP TABLE IF EXISTS product_active_substances")
    cur.execute("DROP TABLE IF EXISTS product_usages")
    cur.execute("DROP TABLE IF EXISTS products")

    # ===== PRODUCTS =====

    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        name TEXT,
        rendeltetes TEXT,

        engedelyszam TEXT,
        engedely_tipus TEXT,

        tulajdonos TEXT,
        forgalmazo TEXT,

        formulacio TEXT,
        kategoria TEXT,

        kiszereles TEXT,
        eltarthatosag TEXT,

        aop1 TEXT,
        aop4 TEXT,
        aop5 TEXT,
        okologia TEXT

    )
    """)

    # ===== PRODUCT USAGES =====

    cur.execute("""
    CREATE TABLE IF NOT EXISTS product_usages (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        product_id INTEGER,

        kultura TEXT,
        karosito TEXT,

        dozis TEXT,
        kezeles_szam TEXT,
        kezeles_ideje TEXT,

        elelmezesi_varakozas TEXT,
        munkaegeszsegugyi_varakozas TEXT

    )
    """)

    # ===== ACTIVE SUBSTANCES =====

    cur.execute("""
    CREATE TABLE IF NOT EXISTS product_active_substances (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        product_id INTEGER,

        hatoanyag TEXT,
        mennyiseg TEXT,

        hatoanyag_csoport TEXT,
        hatasmod TEXT,
        rac TEXT

    )
    """)

    conn.commit()
    conn.close()
