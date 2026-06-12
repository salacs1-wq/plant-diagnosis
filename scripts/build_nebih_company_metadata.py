import argparse
import csv
import re
import sqlite3
from pathlib import Path


REPRESENTATIVE_PATTERN = re.compile(
    r"1\.5\.\s*(?:Engedélyokirat|Engedély)\s+tulajdonos\s+hazai\s+"
    r"képviselője\s*:\s*(.*?)(?=\n\s*(?:1\.6\.|2(?:\.|\s)|[IVX]+\.\s|"
    r"Ikt\.\s*sz|## Page|Az engedélyokirat|A határozat|Jelen határozat)|$)",
    re.IGNORECASE | re.DOTALL,
)
MANUFACTURER_PATTERN = re.compile(
    r"2\.1\.\s*(?:A\s+)?(?:növényvédő\s+szer|készítmény|termék)\s+"
    r"gyártója\s*:\s*(.*?)(?=\n\s*(?:2\.[2-9]\.|3(?:\.|\s)|[IVX]+\.\s|"
    r"Ikt\.\s*sz|## Page|Az engedélyokirat|A határozat|Jelen határozat)|$)",
    re.IGNORECASE | re.DOTALL,
)
PERMIT_PATTERN = re.compile(r"^- permit_number:\s*(.+?)\s*$", re.MULTILINE)


def normalize_permit_number(value: str) -> str:
    return re.sub(r"\s+", "", value).replace(".", "/").strip("/")


def clean_value(value: str | None) -> str:
    if not value:
        return ""
    cleaned = " ".join(value.split()).strip(" ;")
    cleaned = re.split(
        r"\s+(?:Az engedély|A h atározat|A határozat|Jelen határozat|"
        r"Ikt\.\s*sz|II\.|III\.|INDOKOLÁS)",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" ;")
    if cleaned.startswith(("-", "–", "—")) or len(cleaned) > 500:
        return ""
    return cleaned


def extract_metadata(markdown_root: Path) -> dict[str, dict[str, str]]:
    metadata: dict[str, dict[str, str]] = {}
    for path in markdown_root.rglob("*.md"):
        text = path.read_text(encoding="utf-8", errors="replace")
        permit_match = PERMIT_PATTERN.search(text[:2000])
        if not permit_match:
            continue
        permit_number = normalize_permit_number(permit_match.group(1))
        representative_match = REPRESENTATIVE_PATTERN.search(text)
        manufacturer_match = MANUFACTURER_PATTERN.search(text)
        metadata[permit_number] = {
            "manufacturer": clean_value(
                manufacturer_match.group(1) if manufacturer_match else ""
            ),
            "representative": clean_value(
                representative_match.group(1) if representative_match else ""
            ),
            "metadata_source": path.name,
            "inherited_from_product": "",
        }
    return metadata


def propagate_metadata(
    database: Path,
    direct: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    permits = connection.execute(
        """
        SELECT
            product_name, permit_number, permit_type,
            reference_product_name,
            COALESCE(NULLIF(owner_name, ''), owner, '') AS owner
        FROM permit_index
        ORDER BY product_name, permit_number
        """
    ).fetchall()
    connection.close()

    by_product: dict[str, dict[str, str]] = {}
    for row in permits:
        permit = normalize_permit_number(row["permit_number"])
        values = direct.get(permit)
        if values and (values["manufacturer"] or values["representative"]):
            by_product.setdefault(row["product_name"].casefold(), values)

    output: list[dict[str, str]] = []
    for row in permits:
        permit = normalize_permit_number(row["permit_number"])
        values = direct.get(permit, {})
        inherited_from = ""
        if not values.get("manufacturer") and not values.get("representative"):
            reference_name = (row["reference_product_name"] or "").strip()
            reference = by_product.get(reference_name.casefold())
            if reference:
                values = reference
                inherited_from = reference_name
            else:
                same_name = by_product.get(row["product_name"].casefold())
                if same_name:
                    values = same_name
                    inherited_from = row["product_name"]
        output.append(
            {
                "product_name": row["product_name"],
                "permit_number": row["permit_number"],
                "permit_type": row["permit_type"],
                "owner": row["owner"],
                "manufacturer": values.get("manufacturer", ""),
                "representative": values.get("representative", ""),
                "metadata_source": values.get("metadata_source", ""),
                "inherited_from_product": inherited_from,
            }
        )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--markdown-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    direct = extract_metadata(args.markdown_root)
    rows = propagate_metadata(args.database, direct)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    manufacturer_count = sum(bool(row["manufacturer"]) for row in rows)
    representative_count = sum(bool(row["representative"]) for row in rows)
    print(f"rows={len(rows)}")
    print(f"manufacturer={manufacturer_count}")
    print(f"representative={representative_count}")


if __name__ == "__main__":
    main()
