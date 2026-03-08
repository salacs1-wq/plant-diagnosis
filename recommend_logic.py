from db import get_connection


def find_products_by_crop_and_weed(crop, weed_latin):

    conn = get_connection()
    cur = conn.cursor()

    query = """
    SELECT DISTINCT p.*
    FROM products p
    JOIN product_weed_species w
        ON w.product_id = p.id
    WHERE
        lower(trim(w.crop)) = lower(trim(?))
        AND lower(trim(w.weed_latin)) = lower(trim(?))
    """

    cur.execute(query, (crop, weed_latin))

    rows = cur.fetchall()

    conn.close()

    return [dict(r) for r in rows]
