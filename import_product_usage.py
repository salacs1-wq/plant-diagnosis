import csv
from db import get_connection

CSV_PATH = "product_usage_import_sample.csv"


def usage_exists(cur, product_id, scope_kultura, karosito_lista):
    cur.execute(
        """
        SELECT id FROM product_usage
        WHERE product_id=? AND scope_kultura=? AND karosito_lista=?
        """,
        (product_id, scope_kultura, karosito_lista),
    )
    return cur.fetchone() is not None


def import_product_usage():
    conn = get_connection()
    cur = conn.cursor()

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            if usage_exists(cur, row["product_id"], row["scope_kultura"], row["karosito_lista"]):
                continue

            cur.execute(
                """
                INSERT INTO product_usage (
                    product_id,
                    scope_kultura,
                    kultura_lista,
                    karosito_lista,
                    dozis,
                    vizmennyiseg,
                    max_kezeles_szam,
                    min_kezelesi_intervallum,
                    elelmezeseu_varakozasi_ido,
                    munkaegeszsegugyi_varakozasi_ido,
                    scope_megjegyzes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["product_id"],
                    row["scope_kultura"],
                    row["kultura_lista"],
                    row["karosito_lista"],
                    row["dozis"],
                    row["vizmennyiseg"],
                    row["max_kezeles_szam"],
                    row["min_kezelesi_intervallum"],
                    row["elelmezeseu_varakozasi_ido"],
                    row["munkaegeszsegugyi_varakozasi_ido"],
                    row["scope_megjegyzes"],
                ),
            )

    conn.commit()
    conn.close()
