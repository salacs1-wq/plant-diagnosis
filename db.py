# db.py

import sqlite3

DB_PATH = "plant.db"


def get_connection():
    """
    SQLite kapcsolat létrehozása.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def test_connection():
    """
    Teszt lekérdezés.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1")
    result = cur.fetchone()
    conn.close()
    return result is not None
