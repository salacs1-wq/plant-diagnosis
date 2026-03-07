import csv
from db import get_connection

CSV_PATH = "weed_species_import_sample.csv"


def weed_species_exists(cur, product_id, crop, weed_latin):
    cur.execute(
        """
        SELECT id FROM product_weed_species
        WHERE product_id=? AND crop=? AND weed_latin=?
        """,
        (product_id, crop, weed_latin),
    )
    return cur.fetchone() is not None


def import_weed_species():
    conn = get_connection()
    cur = conn.cursor()

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            if weed_species_exists(cur, row["product_id"], row["crop"], row["weed_latin"]):
                continue

            cur.execute(
                """
                INSERT INTO product_weed_species (
                    product_id,
                    crop,
                    weed_latin,
                    weed_hungarian,
                    source_type,
                    source_name,
                    priority,
                    note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["product_id"],
                    row["crop"],
                    row["weed_latin"],
                    row["weed_hungarian"],
                    row["source_type"],
                    row["source_name"],
                    row["priority"],
                    row["note"],
                ),
            )

    conn.commit()
    conn.close()
