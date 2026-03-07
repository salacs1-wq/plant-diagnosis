import csv
from db import get_connection


CSV_PATH = "products_import_sample.csv"


def import_products():

    conn = get_connection()
    cur = conn.cursor()

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:

            cur.execute(
                """
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
                """,
                (
                    row["termek"],
                    row["nev_eredeti"],
                    row["engedelyszam"],
                    row["hatoanyagok"],
                    row["mehveszelyesseg"],
                    row["AKG"],
                    row["AOP1"],
                    row["AOP4"],
                    row["AOP5"],
                    row["kiszereles"],
                    row["ar_kedvezmenyes"],
                    row["pack_amount"],
                    row["price_per_ha_kedv"],
                    row["okirat_pdf_url"],
                    row["okirat_frissites_datum"],
                ),
            )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    import_products()
    print("import done")
