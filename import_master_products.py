import csv
from db import get_connection


CSV_PATH = "products_master.csv"


def is_package(row):
    name = (row.get("name") or "").strip().lower()
    data = (row.get("data") or "").strip().lower()

    if not name:
        return False

    if data == "package_2":
        return True

    if "pack" in name or "csomag" in name or "bundle" in name:
        return True

    return False


def import_products():
    conn = get_connection()
    cur = conn.cursor()

    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=",")

        for row in reader:
            termek = (row.get("name") or "").strip()

            # Üres sorok kihagyása
            if not termek:
                continue

            # Csomagok kihagyása
            if is_package(row):
                continue

            cur.execute(
                """
                INSERT INTO products (
                    termek,
                    nev_eredeti,
                    engedely_tipus,
                    hatoanyagok,
                    forgalmi_kategoria_nebih,
                    okirat_pdf_url,
                    okirat_frissites_datum
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    termek,                         # name
                    termek,                         # name
                    (row.get("data2") or "").strip() or None,
                    (row.get("description") or "").strip() or None,
                    (row.get("data3") or "").strip() or None,
                    (row.get("image") or "").strip() or None,
                    (row.get("data4") or "").strip() or None,
                ),
            )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    import_products()
    print("products import done")
