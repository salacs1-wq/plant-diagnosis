from contextlib import closing
from typing import Any

from fastapi import APIRouter, Query

from nebih_api import connect, fold_text, normalize_permit_number


router = APIRouter(prefix="/action", tags=["NEBIH Actions"])


def text(value: Any) -> str:
    return "" if value is None else str(value)


def number_text(value: Any) -> str:
    if value is None:
        return ""
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def range_text(minimum: Any, maximum: Any) -> str:
    low = number_text(minimum)
    high = number_text(maximum)
    if not low:
        return high
    if not high or high == low:
        return low
    return f"{low}-{high}"


def string_range(minimum: Any, maximum: Any) -> str:
    low = text(minimum).strip()
    high = text(maximum).strip()
    if not low:
        return high
    if not high or high == low:
        return low
    return f"{low}-{high}"


def clean_dose_unit(dose: Any, unit: Any) -> str:
    dose_text = text(dose).strip().casefold()
    unit_text = text(unit).strip()
    if unit_text and unit_text.casefold() in dose_text:
        return ""
    return unit_text


def parse_limit(value: str | None, default: int, maximum: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError("A limit egész szám legyen.") from error
    if parsed < 1 or parsed > maximum:
        raise ValueError(f"A limit 1 és {maximum} közötti szám legyen.")
    return parsed


def success(items: list[dict[str, str]]) -> dict[str, Any]:
    return {"ok": True, "count": len(items), "items": items}


def failure(error: Exception | str) -> dict[str, Any]:
    return {"ok": False, "error": str(error), "items": []}


def action_item(
    *,
    product_name: Any = None,
    permit_number: Any = None,
    crop: Any = None,
    target: Any = None,
    dose: Any = None,
    dose_unit: Any = None,
    bbch: Any = None,
    phi: Any = None,
    max_treatments: Any = None,
    source_pdf: Any = None,
) -> dict[str, str]:
    return {
        "product_name": text(product_name),
        "permit_number": text(permit_number),
        "crop": text(crop),
        "target": text(target),
        "dose": text(dose),
        "dose_unit": clean_dose_unit(dose, dose_unit),
        "bbch": text(bbch),
        "phi": text(phi),
        "max_treatments": text(max_treatments),
        "source_pdf": text(source_pdf),
    }


@router.get("/products", operation_id="actionSearchNebihProducts")
def products(
    q: str | None = Query(default=None),
    limit: str | None = Query(default="5"),
) -> dict[str, Any]:
    try:
        if not q or not q.strip():
            raise ValueError("A q paraméter kötelező.")
        page_size = parse_limit(limit, default=5, maximum=10)
        folded_query = f"%{fold_text(q.strip())}%"
        with closing(connect()) as connection:
            rows = connection.execute(
                """
                SELECT
                    product_name, permit_number, latest_document_url
                FROM permit_index
                WHERE fold(product_name) LIKE ?
                ORDER BY
                    CASE WHEN fold(product_name) = ? THEN 0 ELSE 1 END,
                    fold(product_name), permit_number
                LIMIT ?
                """,
                (folded_query, fold_text(q.strip()), page_size),
            ).fetchall()
        return success(
            [
                action_item(
                    product_name=row["product_name"],
                    permit_number=row["permit_number"],
                    source_pdf=row["latest_document_url"],
                )
                for row in rows
            ]
        )
    except Exception as error:
        return failure(error)


@router.get("/usage", operation_id="actionSearchNebihUsage")
def usage(
    product_name: str | None = Query(default=None),
    crop: str | None = Query(default=None),
    target: str | None = Query(default=None),
    limit: str | None = Query(default="50"),
) -> dict[str, Any]:
    try:
        if not product_name or not product_name.strip():
            raise ValueError("A product_name paraméter kötelező.")
        page_size = parse_limit(limit, default=50, maximum=200)
        clauses = ["fold(u.product_name) LIKE ?"]
        parameters: list[Any] = [f"%{fold_text(product_name.strip())}%"]
        if crop and crop.strip():
            clauses.append("fold(u.crop) LIKE ?")
            parameters.append(f"%{fold_text(crop.strip())}%")
        if target and target.strip():
            clauses.append("fold(u.target) LIKE ?")
            parameters.append(f"%{fold_text(target.strip())}%")
        parameters.append(page_size)
        with closing(connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT
                    u.product_name, u.permit_number, u.crop, u.target,
                    u.dose_min, u.dose_max, u.dose_unit, u.all_doses_raw,
                    u.bbch_min, u.bbch_max, u.phi_days, u.phi_raw,
                    COALESCE(
                        (
                            SELECT d.document_url
                            FROM document_links AS d
                            WHERE d.permit_number = u.permit_number
                            ORDER BY
                                d.is_latest_document DESC,
                                d.document_order DESC,
                                d.id DESC
                            LIMIT 1
                        ),
                        ''
                    ) AS source_pdf
                FROM usage AS u
                WHERE {' AND '.join(clauses)}
                ORDER BY
                    CASE WHEN fold(u.product_name) = ? THEN 0 ELSE 1 END,
                    fold(u.product_name), u.permit_number, u.id
                LIMIT ?
                """,
                [
                    *parameters[:-1],
                    fold_text(product_name.strip()),
                    parameters[-1],
                ],
            ).fetchall()
        items = []
        for row in rows:
            dose = text(row["all_doses_raw"]).strip() or range_text(
                row["dose_min"], row["dose_max"]
            )
            phi = text(row["phi_raw"]).strip() or text(row["phi_days"]).strip()
            items.append(
                action_item(
                    product_name=row["product_name"],
                    permit_number=row["permit_number"],
                    crop=row["crop"],
                    target=row["target"],
                    dose=dose,
                    dose_unit=row["dose_unit"],
                    bbch=string_range(row["bbch_min"], row["bbch_max"]),
                    phi=phi,
                    source_pdf=row["source_pdf"],
                )
            )
        return success(items)
    except Exception as error:
        return failure(error)


@router.get("/dose", operation_id="getPesticideDose")
def dose(
    product_name: str | None = Query(default=None),
    crop: str | None = Query(default=None),
    limit: str | None = Query(default="50"),
) -> dict[str, Any]:
    try:
        if not product_name or not product_name.strip():
            raise ValueError("A product_name paraméter kötelező.")
        page_size = parse_limit(limit, default=50, maximum=200)
        folded_product = fold_text(product_name.strip())
        with closing(connect()) as connection:
            exact_usage = connection.execute(
                "SELECT 1 FROM usage WHERE fold(product_name) = ? LIMIT 1",
                (folded_product,),
            ).fetchone()
            matched_products = connection.execute(
                """
                SELECT DISTINCT product_name
                FROM permit_index
                WHERE fold(product_name) LIKE ?
                ORDER BY
                    CASE WHEN fold(product_name) = ? THEN 0 ELSE 1 END,
                    fold(product_name)
                """,
                (f"%{folded_product}%", folded_product),
            ).fetchall()
            resolved_names = {
                fold_text(row["product_name"]) for row in matched_products
            }
            if exact_usage:
                resolved_names.add(folded_product)
            if not resolved_names:
                usage_names = connection.execute(
                    """
                    SELECT DISTINCT product_name
                    FROM usage
                    WHERE fold(product_name) LIKE ?
                    ORDER BY fold(product_name)
                    """,
                    (f"%{folded_product}%",),
                ).fetchall()
                resolved_names.update(
                    fold_text(row["product_name"]) for row in usage_names
                )
            if not resolved_names:
                return failure(
                    f"Nem található termék vagy felhasználás: {product_name.strip()}"
                )
            ordered_names = sorted(
                resolved_names,
                key=lambda name: (name != folded_product, name),
            )
            placeholders = ", ".join("?" for _ in ordered_names)
            parameters: list[Any] = list(ordered_names)
            crop_clause = ""
            if crop and crop.strip():
                crop_clause = " AND fold(u.crop) LIKE ?"
                parameters.append(f"%{fold_text(crop.strip())}%")
            parameters.append(page_size)
            rows = connection.execute(
                f"""
                WITH ranked AS (
                    SELECT
                        u.product_name, u.permit_number, u.permit_type,
                        u.crop, u.target, u.dose_min, u.dose_max,
                        u.dose_unit, u.all_doses_raw,
                        u.bbch_min, u.bbch_max, u.phi_days, u.phi_raw,
                        u.max_treatments,
                        COALESCE(
                            (
                                SELECT d.document_url
                                FROM document_links AS d
                                WHERE d.permit_number = u.permit_number
                                ORDER BY
                                    d.is_latest_document DESC,
                                    d.document_order DESC,
                                    d.id DESC
                                LIMIT 1
                            ),
                            ''
                        ) AS source_pdf,
                        ROW_NUMBER() OVER (
                            PARTITION BY
                                fold(u.product_name), fold(u.crop),
                                fold(u.target), u.dose_min, u.dose_max,
                                fold(u.dose_unit), fold(u.all_doses_raw),
                                fold(u.bbch_min), fold(u.bbch_max),
                                fold(u.phi_days), fold(u.phi_raw),
                                fold(u.max_treatments)
                            ORDER BY
                                CASE
                                    WHEN fold(u.permit_type) = 'alapengedely'
                                    THEN 0 ELSE 1
                                END,
                                u.permit_number, u.id
                        ) AS usage_rank
                    FROM usage AS u
                    WHERE fold(u.product_name) IN ({placeholders}){crop_clause}
                )
                SELECT *
                FROM ranked
                WHERE usage_rank = 1
                ORDER BY fold(crop), fold(target), permit_number
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        items = []
        for row in rows:
            dose_value = text(row["all_doses_raw"]).strip() or range_text(
                row["dose_min"], row["dose_max"]
            )
            phi = text(row["phi_raw"]).strip() or text(row["phi_days"]).strip()
            items.append(
                action_item(
                    product_name=row["product_name"],
                    permit_number=row["permit_number"],
                    crop=row["crop"],
                    target=row["target"],
                    dose=dose_value,
                    dose_unit=row["dose_unit"],
                    bbch=string_range(row["bbch_min"], row["bbch_max"]),
                    phi=phi,
                    max_treatments=row["max_treatments"],
                    source_pdf=row["source_pdf"],
                )
            )
        return success(items)
    except Exception as error:
        return failure(error)


@router.get("/documents", operation_id="actionGetNebihDocuments")
def documents(
    permit_number: str | None = Query(default=None),
    limit: str | None = Query(default="10"),
) -> dict[str, Any]:
    try:
        if not permit_number or not permit_number.strip():
            raise ValueError("A permit_number paraméter kötelező.")
        page_size = parse_limit(limit, default=10, maximum=20)
        normalized = normalize_permit_number(permit_number)
        with closing(connect()) as connection:
            rows = connection.execute(
                """
                SELECT
                    product_name, permit_number, document_url
                FROM document_links
                WHERE replace(replace(trim(permit_number), ' ', ''), '.', '/') = ?
                ORDER BY is_latest_document DESC, document_order DESC, id DESC
                LIMIT ?
                """,
                (normalized, page_size),
            ).fetchall()
        return success(
            [
                action_item(
                    product_name=row["product_name"],
                    permit_number=row["permit_number"],
                    source_pdf=row["document_url"],
                )
                for row in rows
            ]
        )
    except Exception as error:
        return failure(error)
