import sqlite3

DB_PATH = "plant.db"


def get_connection():
    return sqlite3.connect(DB_PATH)


def load_laudis():
    conn = get_connection()
    cur = conn.cursor()

    # products
    cur.execute("""
    INSERT INTO products (
        termek,
        rendeltetes,
        hatoanyagok,
        engedelyszam,
        engedely_tipus,
        formulacio,
        forgalmi_kategoria,
        kiszereles,
        akg,
        aop,
        aop1,
        aop4,
        aop5,
        mehveszelyesseg,
        tulajdonos,
        hazai_kepviselet,
        forgalmazo,
        gyarto,
        agromedium_id,
        agromedium_url,
        forras_url
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "Laudis",
        "gyomirtó szer",
        "izoxadifen-etil 22 g/l; tembotrion 44 g/l",
        "02.5/2006/2/2007",
        "alapengedély",
        "olaj alapú szuszpenzió koncentrátum (OD)",
        "I. kategória",
        "1 l, 5 l",
        1,
        1,
        0,
        0,
        0,
        "nem jelölésköteles",
        "Bayer AG",
        "Bayer Hungária Kft.",
        "Bayer Hungária Kft.",
        "Bayer AG",
        "10003637",
        "https://www.agromedium.com/hu-hu/novenyvedo-szerek/10003637",
        "agromedium"
    ))

    # product_active_substances
    cur.execute("""
    INSERT INTO product_active_substances (
        termek,
        hatoanyag_nev,
        mennyiseg,
        hatoanyag_csoport,
        hatasmod,
        rac_besorolas,
        forras_url
    )
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        "Laudis",
        "izoxadifen-etil",
        "22 g/l",
        "",
        "",
        "",
        "agromedium"
    ))

    cur.execute("""
    INSERT INTO product_active_substances (
        termek,
        hatoanyag_nev,
        mennyiseg,
        hatoanyag_csoport,
        hatasmod,
        rac_besorolas,
        forras_url
    )
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        "Laudis",
        "tembotrion",
        "44 g/l",
        "triketon",
        "Plasztokinon bioszintézis (4-HPPD) gátlás",
        "HRAC F",
        "agromedium"
    ))

    # product_usages
    cur.execute("""
    INSERT INTO product_usages (
        termek,
        kultura,
        karosito,
        dozis,
        kezelesek_max_szama,
        kezeles_ideje,
        le_mennyiseg,
        elelmezes_egeszsegugyi_varakozasi_ido,
        munkaegeszsegugyi_varakozasi_ido,
        forras_url
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "Laudis",
        "kukorica (takarmány, vetőmag, csemege, siló, pattogatni való)",
        "magról kelő egyszikű gyomok",
        "1,75-2,25 l/ha",
        "1",
        "posztemergensen 8 leveles állapotig",
        "250-300 l/ha",
        "-",
        "0 nap",
        "agromedium"
    ))

    cur.execute("""
    INSERT INTO product_usages (
        termek,
        kultura,
        karosito,
        dozis,
        kezelesek_max_szama,
        kezeles_ideje,
        le_mennyiseg,
        elelmezes_egeszsegugyi_varakozasi_ido,
        munkaegeszsegugyi_varakozasi_ido,
        forras_url
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "Laudis",
        "kukorica (takarmány, vetőmag, csemege, siló, pattogatni való)",
        "magról kelő kétszikű gyomok",
        "1,75-2,25 l/ha",
        "1",
        "posztemergensen 8 leveles állapotig",
        "250-300 l/ha",
        "-",
        "0 nap",
        "agromedium"
    ))

    conn.commit()
    conn.close()

    print("OK - Laudis betöltve")


if __name__ == "__main__":
    load_laudis()
