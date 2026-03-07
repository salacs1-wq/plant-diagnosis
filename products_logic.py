# products_logic.py

def normalize_crop_name(crop_name):
    """
    Kultúranév egységesítése.
    """
    if not crop_name:
        return None

    return str(crop_name).strip().lower()


def build_product_query_context(crop=None, weed_name=None):
    """
    Egységes belső keresési kontextus a későbbi
    master tábla / SQLite lekérdezésekhez.
    """
    return {
        "crop": normalize_crop_name(crop),
        "weed_name": weed_name.strip() if weed_name else None
    }
