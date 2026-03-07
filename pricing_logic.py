# pricing_logic.py

def apply_company_markup(discounted_price, markup=0.03):
    """
    Kedvezményes árra ráteszi a cég 3%-át.
    """
    if discounted_price is None:
        return None

    return discounted_price * (1 + markup)


def calculate_treatable_area(pack_amount, dose_per_ha):
    """
    Kiszerelés / dózis = kezelhető hektár.
    """
    if not pack_amount or not dose_per_ha:
        return None

    if dose_per_ha == 0:
        return None

    return pack_amount / dose_per_ha


def calculate_price_per_ha(final_price, treatable_area):
    """
    Ár / kezelhető hektár = Ft/ha.
    """
    if not final_price or not treatable_area:
        return None

    if treatable_area == 0:
        return None

    return final_price / treatable_area
