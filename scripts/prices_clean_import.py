import sqlite3
import pandas as pd
import re
from pathlib import Path

# ====== Beállítások ======
INPUT_FILE = "prices_source.xlsx"
DB_PATH = "database.db"
TABLE_NAME = "prices"


# ====== Segédfüggvények ======
def normalize_spaces(text: str) -> str:
    if text is None:
        return None
    text = str(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_name(text: str) -> str:
    """
    Joinhoz használható normalizált név.
    Kisbetűsít, szóközt egységesít.
    """
    text = normalize_spaces(text)
    if not text:
        return None
    return text.lower()


def clean_price(value):
    """
    Ármező tisztítása:
    - szóközök, Ft, egyéb karakterek kiszedése
    - vessző/pont kezelése
    - számmá alakítás
    """
    if pd.isna(value):
        return None

    s = str(value).strip()
    if not s:
        return None

    s = s.replace("\xa0", " ")
    s = s.replace("Ft", "").replace("ft", "")
    s = s.replace(" ", "")

    # csak szám, vessző, pont, mínusz maradjon
    s = re.sub(r"[^0-9,.\-]", "", s)

    if not s:
        return None

    # Ha mindkettő van benne, akkor valószínűleg ezres/pont + tizedes/vessző
    if "," in s and "." in s:
        s = s.replace(".", "")
        s = s.replace(",", ".")
    # Ha csak vessző van, azt tekintjük tizedeselválasztónak
    elif "," in s:
        s = s.replace(",", ".")

    try:
        num = float(s)
        # ha egésznek tűnik, legyen int
        if num.is_integer():
            return int(num)
        return num
    except ValueError:
        return None


def standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Oszlopnevek egységesítése.
    A te mintád alapján tipikus oszlopok:
    Név | Me | Fk | Kedvezm.ár | Kamatos ár
    """
    new_cols = []
    for col in df.columns:
        c = str(col).strip().lower()
        c = c.replace("\n", " ")
        c = re.sub(r"\s+", " ", c)

        if c in ["név", "nev", "termék", "termek", "cikknév", "cikknév"]:
            new_cols.append("nev")
        elif c in ["me", "m.e.", "mértékegység", "mertekegyseg"]:
            new_cols.append("me")
        elif c in ["fk", "kiszerelés", "kiszereles"]:
            new_cols.append("fk")
        elif c in ["kedvezm.ár", "kedvezm ár", "kedvezményes ár", "kedvezmenyes ar", "kedv.ar", "kedv ár"]:
            new_cols.append("kedvezmenyes_ar")
        elif c in ["kamatos ár", "kamatos ar", "kamatosár"]:
            new_cols.append("kamatos_ar")
        else:
            new_cols.append(c)

    df.columns = new_cols
    return df


def load_input_file(path: str) -> pd.DataFrame:
    ext = Path(path).suffix.lower()

    if ext in [".xlsx", ".xls"]:
        return pd.read_excel(path, header=4)
    elif ext == ".csv":
        # először próbáljuk pontosvesszővel, aztán vesszővel
        try:
            return pd.read_csv(path, sep=";", encoding="utf-8")
        except Exception:
            return pd.read_csv(path, sep=",", encoding="utf-8")
    else:
        raise ValueError(f"Nem támogatott fájlformátum: {ext}")


# ====== Fő folyamat ======
def main():
    print("Árlista betöltése...")
    df = load_input_file(INPUT_FILE)

    print(f"Eredeti sorok száma: {len(df)}")

    df = standardize_columns(df)

    # csak a releváns oszlopokat tartjuk meg, ha léteznek
    wanted = ["nev", "me", "fk", "kedvezmenyes_ar", "kamatos_ar"]
    existing = [c for c in wanted if c in df.columns]
    df = df[existing].copy()

    # ha nincs név oszlop, megállunk
    if "nev" not in df.columns:
        raise ValueError("Nem található megfelelő név oszlop az árlistában.")

    # szöveges mezők tisztítása
    for col in ["nev", "me", "fk"]:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: normalize_spaces(x) if pd.notna(x) else None)

    # ármezők tisztítása
    for col in ["kedvezmenyes_ar", "kamatos_ar"]:
        if col in df.columns:
            df[col] = df[col].apply(clean_price)

    # normalizált join mező
    df["nev_normalizalt"] = df["nev"].apply(normalize_name)

    # üres sorok kiszórása
    df = df[df["nev"].notna()]
    df = df[df["nev"].astype(str).str.strip() != ""]
    df = df[df["nev_normalizalt"].notna()]

    # teljesen duplikált sorok kiszedése
    df = df.drop_duplicates()

    # ha ugyanarra a névre több sor van, az első marad
    df = df.drop_duplicates(subset=["nev_normalizalt"], keep="first")

    # opcionális sorrend
    order_cols = ["nev", "nev_normalizalt", "me", "fk", "kedvezmenyes_ar", "kamatos_ar"]
    df = df[[c for c in order_cols if c in df.columns]]

    print(f"Tisztított sorok száma: {len(df)}")

    # SQLite mentés
    conn = sqlite3.connect(DB_PATH)

    # tábla újraírás
    df.to_sql(TABLE_NAME, conn, if_exists="replace", index=False)

    # index a gyors joinhoz
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_nev_norm ON {TABLE_NAME}(nev_normalizalt)")
    conn.commit()
    conn.close()

    print("Kész: prices tábla frissítve.")
    print(df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
