import csv
from db import get_connection


CSV_PATH = "agromedium-com-2026-01-29-novszer.csv"


def is_package(row):

    name = (row.get("name") or "").lower()
    data = (row.get("data") or "").lower()

    if "pack" in name:
        return True

    if "csomag" in name:
        return True

    if "bundle" in name:
        return True

    if data == "package_2":
        return True

    return False


def import_products():

    conn = get_connection()
    cur = conn.cursor()

    with open(CSV_PATH, newline="", encoding="utf-8") as f:

        reader = csv.DictReader(f)

        for row in reader:

            if is_package(row):
                continue

            cur.execute(
                """
                INSERT INTO products (
                    termek,
                    nev_eredeti,
                    engedelyszam,
                    engedely_tipus,
                    hatoanyagok,
                    forgalmi_kategoria_nebih,
                    mehveszelyesseg,
                    kiszereles,
                    pack_amount,
                    okirat_pdf_url,
                    okirat_frissites_datum
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("name"),
                    row.get("name"),
                    row.get("permit"),
                    row.get("type"),
                    row.get("active"),
                    row.get("category"),
                    row.get("bee"),
                    row.get("pack"),
                    row.get("pack_amount"),
                    row.get("pdf"),
                    row.get("updated"),
                ),
            )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    import_products()
    print("products import done")
