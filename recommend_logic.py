from db import get_connection


def find_products_by_crop_and_weed(crop, weed_latin):

    conn = get_connection()
    cur = conn.cursor()

    query = """
    SELECT p.*
    FROM products p

    JOIN product_usage u
        ON u.product_id = p.id

    JOIN product_weed_species w
        ON w.product_id = p.id

    WHERE
        w.crop = ?
        AND w.weed_latin = ?
    """

    cur.execute(query, (crop, weed_latin))

    rows = cur.fetchall()

    conn.close()

    return [dict(r) for r in rows]
