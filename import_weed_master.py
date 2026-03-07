import csv
from db import get_connection

CSV_PATH = "weed_species_clean.csv"


def weed_exists(cur, weed_latin):
    cur.execute(
        "SELECT id FROM weed_species_master WHERE weed_latin=?",
        (weed_latin,),
    )
    return cur.fetchone() is not None


def import_weed_master():

    conn = get_connection()
    cur = conn.cursor()

    with open(CSV_PATH, newline="", encoding="utf-8") as f:

        reader = csv.DictReader(f)

        for row in reader:

            if weed_exists(cur, row["weed_latin"]):
                continue

            cur.execute(
                """
                INSERT INTO weed_species_master (
                    weed_latin,
                    weed_hungarian,
                    group_type,
                    main_crop,
                    notes
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["weed_latin"],
                    row["weed_hungarian"],
                    row["group_type"],
                    row["main_crop"],
                    row["notes"],
                ),
            )

    conn.commit()
    conn.close()
