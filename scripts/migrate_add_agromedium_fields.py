import sqlite3

DB_PATH = "plant.db"


def get_connection():
    return sqlite3.connect(DB_PATH)


def migrate():

    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            "ALTER TABLE products ADD COLUMN agromedium_id TEXT"
        )
    except:
        pass

    try:
        cur.execute(
            "ALTER TABLE products ADD COLUMN agromedium_url TEXT"
        )
    except:
        pass

    conn.commit()
    conn.close()

    print("OK - agromedium mezők hozzáadva")


if __name__ == "__main__":
    migrate()
