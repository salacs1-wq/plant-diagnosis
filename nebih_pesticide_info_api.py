import re
from contextlib import closing
from typing import Any

from fastapi import APIRouter, Query

from nebih_api import connect, fold_text
from nebih_actions_api import parse_limit, range_text, string_range, text


router = APIRouter(prefix="/action", tags=["NEBIH Pesticide Information"])

PERMIT_KEY = "replace(replace(trim({column}), ' ', ''), '.', '/')"
TARGET_CATEGORY_ALIASES = {
    "parlagfu": ["ketsziku", "gyom"],
}
IGNORED_TARGET_WORDS = {"a", "az", "es", "ellen", "illetve", "valamint"}


def query_value(value: Any) -> str:
    return text(value).strip()


def parse_bool(value: str | None, name: str) -> int | None:
    if value is None or not value.strip():
        return None
    folded = fold_text(value.strip())
    if folded in {"true", "1", "yes", "igen"}:
        return 1
    if folded in {"false", "0", "no", "nem"}:
        return 0
    raise ValueError(f"{name} must be true or false.")


def parse_number(value: str | None, name: str) -> int | None:
    if value is None or not value.strip():
        return None
    match = re.search(r"\d+", value)
    if not match:
        raise ValueError(f"{name} must contain a number.")
    return int(match.group())


def empty_summary(note: str = "") -> dict[str, Any]:
    return {
        "product_count": 0,
        "usage_count": 0,
        "active_substance_count": 0,
        "document_count": 0,
        "note": note,
    }


def response_query(**values: Any) -> dict[str, str]:
    return {key: query_value(value) for key, value in values.items()}


def failure(query: dict[str, str], error: Exception | str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": str(error),
        "query": query,
        "products": [],
        "active_substances": [],
        "usages": [],
        "documents": [],
        "summary": empty_summary(),
    }


def unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def numeric_bbch(value: Any) -> int | None:
    match = re.search(r"\d+", query_value(value))
    return int(match.group()) if match else None


def crop_matches(value: Any, search: str) -> bool:
    crop = fold_text(query_value(value))
    term = fold_text(search)
    if not crop or not term:
        return False
    if crop == term or crop.startswith(term):
        return True
    return bool(
        re.search(
            rf"(?<![a-z0-9(]){re.escape(term)}(?![a-z0-9])",
            crop,
        )
    )


def usage_item(row: Any, bbch_query: int | None) -> dict[str, str]:
    bbch_min = numeric_bbch(row["bbch_min"])
    bbch_max = numeric_bbch(row["bbch_max"])
    bbch_match = ""
    if bbch_query is not None:
        if bbch_min is None and bbch_max is None:
            bbch_match = "uncertain"
        else:
            low = bbch_min if bbch_min is not None else bbch_max
            high = bbch_max if bbch_max is not None else bbch_min
            bbch_match = "match" if low <= bbch_query <= high else "no_match"
    dose = query_value(row["all_doses_raw"]) or range_text(
        row["dose_min"], row["dose_max"]
    )
    phi = query_value(row["phi_raw"]) or query_value(row["phi_days"])
    return {
        "product_name": query_value(row["product_name"]),
        "permit_number": query_value(row["permit_number"]),
        "permit_type": query_value(row["permit_type"]),
        "owner": query_value(row["owner"]),
        "manufacturer": query_value(row["manufacturer"]),
        "representative": query_value(row["representative"]),
        "crop": query_value(row["crop"]),
        "target": query_value(row["target"]),
        "purpose": query_value(row["purpose"]),
        "dose": dose,
        "dose_unit": query_value(row["dose_unit"]),
        "bbch": string_range(row["bbch_min"], row["bbch_max"]),
        "bbch_match": bbch_match,
        "treatment_time": query_value(row["treatment_time"]),
        "phi": phi,
        "max_treatments": query_value(row["max_treatments"]),
        "min_interval_days": query_value(row["min_interval_days"]),
        "expiry_date": query_value(row["expiry_date"]),
        "latest_document": query_value(row["latest_document"]),
        "source_pdf": query_value(row["source_pdf"]),
    }


def placeholders(values: list[Any]) -> str:
    return ", ".join("?" for _ in values)


def target_search(
    terms: list[str],
) -> tuple[str, list[str], bool]:
    groups: list[str] = []
    parameters: list[str] = []
    broader_category_used = False
    for term in terms:
        folded = fold_text(term)
        alternatives: list[tuple[list[str], bool]] = [([folded], False)]
        tokens = [
            "gyom" if token.startswith("gyom") else token
            for token in re.findall(r"[a-z0-9]+", folded)
            if token not in IGNORED_TARGET_WORDS
        ]
        if len(tokens) > 1:
            alternatives.append((tokens, False))
        if folded in TARGET_CATEGORY_ALIASES:
            alternatives.append((TARGET_CATEGORY_ALIASES[folded], True))
        alternative_sql: list[str] = []
        for words, is_broader in alternatives:
            alternative_sql.append(
                "(" + " AND ".join("fold(u.target) LIKE ?" for _ in words) + ")"
            )
            parameters.extend(f"%{word}%" for word in words)
            broader_category_used = broader_category_used or is_broader
        groups.append("(" + " OR ".join(alternative_sql) + ")")
    return "(" + " OR ".join(groups) + ")", parameters, broader_category_used


@router.get("/pesticide-info", operation_id="getPesticideInformation")
def pesticide_information(
    product_name: str | None = Query(default=None),
    active_substance: str | None = Query(default=None),
    company: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    manufacturer: str | None = Query(default=None),
    representative: str | None = Query(default=None),
    crop: str | None = Query(default=None),
    target: str | None = Query(default=None),
    pest_or_disease: str | None = Query(default=None),
    weed: str | None = Query(default=None),
    purpose: str | None = Query(default=None),
    bbch: str | None = Query(default=None),
    phi_days: str | None = Query(default=None),
    akg_allowed: str | None = Query(default=None),
    aop1_bee_allowed: str | None = Query(default=None),
    organic_allowed: str | None = Query(default=None),
    question_type: str | None = Query(default="general"),
    limit: str | None = Query(default="20"),
) -> dict[str, Any]:
    query = response_query(
        product_name=product_name,
        active_substance=active_substance,
        company=company,
        owner=owner,
        manufacturer=manufacturer,
        representative=representative,
        crop=crop,
        target=target,
        pest_or_disease=pest_or_disease,
        weed=weed,
        purpose=purpose,
        bbch=bbch,
        phi_days=phi_days,
        akg_allowed=akg_allowed,
        aop1_bee_allowed=aop1_bee_allowed,
        organic_allowed=organic_allowed,
        question_type=question_type or "general",
    )
    try:
        page_size = parse_limit(limit, default=20, maximum=50)
        if not any(
            query[key]
            for key in (
                "product_name",
                "active_substance",
                "company",
                "owner",
                "manufacturer",
                "representative",
                "crop",
                "target",
                "pest_or_disease",
                "weed",
            )
        ):
            return failure(
                query,
                "Please specify a product name, active substance, crop or target.",
            )

        bbch_number = parse_number(bbch, "bbch")
        phi_number = parse_number(phi_days, "phi_days")
        akg_value = parse_bool(akg_allowed, "akg_allowed")
        bee_value = parse_bool(aop1_bee_allowed, "aop1_bee_allowed")
        organic_value = parse_bool(organic_allowed, "organic_allowed")
        target_terms = unique_strings(
            [
                query["target"],
                query["pest_or_disease"],
                query["weed"],
            ]
        )
        note_parts: list[str] = []

        with closing(connect()) as connection:
            resolved_names: list[str] = []
            if query["product_name"]:
                folded_name = fold_text(query["product_name"])
                rows = connection.execute(
                    """
                    SELECT DISTINCT product_name
                    FROM permit_index
                    WHERE fold(product_name) LIKE ?
                    UNION
                    SELECT DISTINCT product_name
                    FROM usage
                    WHERE fold(product_name) LIKE ?
                    ORDER BY product_name
                    """,
                    (f"%{folded_name}%", f"%{folded_name}%"),
                ).fetchall()
                resolved_names = [fold_text(row["product_name"]) for row in rows]

            active_permits: list[str] = []
            if query["active_substance"]:
                active_rows = connection.execute(
                    """
                    SELECT DISTINCT permit_number
                    FROM active_substances
                    WHERE fold(active_substance_name) LIKE ?
                    """,
                    (f"%{fold_text(query['active_substance'])}%",),
                ).fetchall()
                active_permits = [query_value(row["permit_number"]) for row in active_rows]

            usage_clauses: list[str] = []
            usage_parameters: list[Any] = []
            if resolved_names:
                usage_clauses.append(
                    f"fold(u.product_name) IN ({placeholders(resolved_names)})"
                )
                usage_parameters.extend(resolved_names)
            elif query["product_name"]:
                usage_clauses.append("fold(u.product_name) LIKE ?")
                usage_parameters.append(f"%{fold_text(query['product_name'])}%")
            if active_permits:
                usage_clauses.append(
                    f"u.permit_number IN ({placeholders(active_permits)})"
                )
                usage_parameters.extend(active_permits)
            elif query["active_substance"]:
                usage_clauses.append("0 = 1")
            if query["crop"]:
                usage_clauses.append("fold(u.crop) LIKE ?")
                usage_parameters.append(f"%{fold_text(query['crop'])}%")
            if target_terms:
                target_sql, target_parameters, broader_target = target_search(
                    target_terms
                )
                usage_clauses.append(target_sql)
                usage_parameters.extend(target_parameters)
                if broader_target:
                    note_parts.append(
                        "The exact weed species was not present in the usage "
                        "table; a broader weed category was also searched."
                    )
            if query["purpose"]:
                usage_clauses.append("fold(p.purpose) LIKE ?")
                usage_parameters.append(f"%{fold_text(query['purpose'])}%")
            if query["company"]:
                usage_clauses.append(
                    "fold(coalesce(nullif(p.owner_name, ''), p.owner, '')) LIKE ?"
                )
                usage_parameters.append(f"%{fold_text(query['company'])}%")
            if query["owner"]:
                usage_clauses.append(
                    "fold(coalesce(nullif(p.owner_name, ''), p.owner, '')) LIKE ?"
                )
                usage_parameters.append(f"%{fold_text(query['owner'])}%")
            if query["manufacturer"]:
                usage_clauses.append("0 = 1")
            if query["representative"]:
                usage_clauses.append("0 = 1")
            if phi_number is not None:
                usage_clauses.append(
                    "(trim(u.phi_days) = ? OR fold(u.phi_raw) LIKE ?)"
                )
                usage_parameters.extend([str(phi_number), f"%{phi_number}%"])
            for column, value in (
                ("akg_allowed", akg_value),
                ("aop1_bee_allowed", bee_value),
                ("organic_allowed", organic_value),
            ):
                if value is not None:
                    usage_clauses.append(f"p.{column} = ?")
                    usage_parameters.append(value)

            where_sql = (
                " WHERE " + " AND ".join(usage_clauses)
                if usage_clauses
                else ""
            )
            candidate_limit = min(max(page_size * 10, 100), 500)
            usage_rows = connection.execute(
                f"""
                WITH permits AS (
                    SELECT
                        *,
                        ROW_NUMBER() OVER (
                            PARTITION BY {PERMIT_KEY.format(column='permit_number')}
                            ORDER BY id
                        ) AS permit_rank
                    FROM permit_index
                ),
                filtered_usage AS (
                    SELECT
                        u.id AS usage_id,
                        u.product_name, u.permit_number, u.permit_type,
                        u.crop, u.target, u.dose_min, u.dose_max,
                        u.dose_unit, u.all_doses_raw,
                        u.bbch_min, u.bbch_max, u.treatment_time,
                        u.phi_days, u.phi_raw, u.max_treatments,
                        u.min_interval_days, p.purpose,
                        COALESCE(NULLIF(p.owner_name, ''), p.owner, '') AS owner,
                        '' AS manufacturer,
                        '' AS representative,
                        p.expiry_date,
                        p.latest_document_title AS latest_document,
                        COALESCE(
                            (
                                SELECT d.document_url
                                FROM document_links AS d
                                WHERE {PERMIT_KEY.format(column='d.permit_number')}
                                    = {PERMIT_KEY.format(column='u.permit_number')}
                                ORDER BY
                                    d.is_latest_document DESC,
                                    d.document_order DESC,
                                    d.id DESC
                                LIMIT 1
                            ),
                            ''
                        ) AS source_pdf
                    FROM usage AS u
                    LEFT JOIN permits AS p
                        ON {PERMIT_KEY.format(column='p.permit_number')}
                            = {PERMIT_KEY.format(column='u.permit_number')}
                        AND p.permit_rank = 1
                    {where_sql}
                ),
                ranked_usage AS (
                    SELECT
                        *,
                        ROW_NUMBER() OVER (
                            PARTITION BY fold(product_name)
                            ORDER BY fold(crop), fold(target), usage_id
                        ) AS product_row
                    FROM filtered_usage
                )
                SELECT
                    product_name, permit_number, permit_type,
                    crop, target, dose_min, dose_max,
                    dose_unit, all_doses_raw,
                    bbch_min, bbch_max, treatment_time,
                    phi_days, phi_raw, max_treatments,
                    min_interval_days, purpose, owner,
                    manufacturer, representative, expiry_date,
                    latest_document, source_pdf
                FROM ranked_usage
                ORDER BY product_row, fold(product_name), fold(crop), usage_id
                LIMIT ?
                """,
                [*usage_parameters, candidate_limit],
            ).fetchall()

            usage_items = [usage_item(row, bbch_number) for row in usage_rows]
            if query["crop"]:
                usage_items = [
                    item
                    for item in usage_items
                    if crop_matches(item["crop"], query["crop"])
                ]
            if bbch_number is not None:
                exact_items = [
                    item for item in usage_items if item["bbch_match"] == "match"
                ]
                uncertain_items = [
                    item
                    for item in usage_items
                    if item["bbch_match"] == "uncertain"
                ]
                if exact_items:
                    usage_items = exact_items
                elif uncertain_items:
                    usage_items = uncertain_items
                    note_parts.append(
                        "BBCH could not be matched exactly; crop-relevant records "
                        "with uncertain BBCH were returned."
                    )
                else:
                    usage_items = []
            usage_items = usage_items[:page_size]
            usage_permits = unique_strings(
                [item["permit_number"] for item in usage_items]
            )

            product_identity_clauses: list[str] = []
            product_filter_clauses: list[str] = []
            product_parameters: list[Any] = []
            if resolved_names:
                product_identity_clauses.append(
                    f"fold(product_name) IN ({placeholders(resolved_names)})"
                )
                product_parameters.extend(resolved_names)
            if active_permits:
                product_identity_clauses.append(
                    f"permit_number IN ({placeholders(active_permits)})"
                )
                product_parameters.extend(active_permits)
            if usage_permits:
                product_identity_clauses.append(
                    f"permit_number IN ({placeholders(usage_permits)})"
                )
                product_parameters.extend(usage_permits)
            if query["purpose"]:
                product_filter_clauses.append("fold(purpose) LIKE ?")
                product_parameters.append(f"%{fold_text(query['purpose'])}%")
            if query["company"]:
                product_filter_clauses.append(
                    "fold(coalesce(nullif(owner_name, ''), owner, '')) LIKE ?"
                )
                product_parameters.append(f"%{fold_text(query['company'])}%")
            if query["owner"]:
                product_filter_clauses.append(
                    "fold(coalesce(nullif(owner_name, ''), owner, '')) LIKE ?"
                )
                product_parameters.append(f"%{fold_text(query['owner'])}%")
            if query["manufacturer"]:
                product_filter_clauses.append("0 = 1")
            if query["representative"]:
                product_filter_clauses.append("0 = 1")
            for column, value in (
                ("akg_allowed", akg_value),
                ("aop1_bee_allowed", bee_value),
                ("organic_allowed", organic_value),
            ):
                if value is not None:
                    product_filter_clauses.append(f"{column} = ?")
                    product_parameters.append(value)
            product_where_parts = []
            if product_identity_clauses:
                product_where_parts.append(
                    "(" + " OR ".join(product_identity_clauses) + ")"
                )
            product_where_parts.extend(product_filter_clauses)
            product_where = (
                " WHERE " + " AND ".join(product_where_parts)
                if product_where_parts
                else " WHERE 0 = 1"
            )
            product_rows = connection.execute(
                f"""
                SELECT
                    product_name, permit_number, permit_type, purpose,
                    formulation, issue_date, expiry_date,
                    COALESCE(NULLIF(owner_name, ''), owner, '') AS owner,
                    akg_allowed, aop1_bee_allowed, organic_allowed,
                    bee_risk, latest_document_title, latest_document_url
                FROM permit_index
                {product_where}
                ORDER BY fold(product_name), permit_number
                LIMIT ?
                """,
                [*product_parameters, page_size],
            ).fetchall()
            products = [
                {
                    "product_name": query_value(row["product_name"]),
                    "permit_number": query_value(row["permit_number"]),
                    "permit_type": query_value(row["permit_type"]),
                    "purpose": query_value(row["purpose"]),
                    "formulation": query_value(row["formulation"]),
                    "issue_date": query_value(row["issue_date"]),
                    "expiry_date": query_value(row["expiry_date"]),
                    "owner": query_value(row["owner"]),
                    "manufacturer": "",
                    "representative": "",
                    "akg_allowed": bool(row["akg_allowed"]),
                    "aop1_bee_allowed": bool(row["aop1_bee_allowed"]),
                    "organic_allowed": bool(row["organic_allowed"]),
                    "bee_risk": query_value(row["bee_risk"]),
                    "latest_document": query_value(
                        row["latest_document_title"]
                    ),
                    "source_pdf": query_value(row["latest_document_url"]),
                }
                for row in product_rows
            ]
            product_permits = unique_strings(
                [item["permit_number"] for item in products] + usage_permits
            )

            substances: list[dict[str, str]] = []
            if product_permits:
                substance_rows = connection.execute(
                    f"""
                    SELECT
                        product_name, permit_number,
                        active_substance_name, content, unit
                    FROM active_substances
                    WHERE permit_number IN ({placeholders(product_permits)})
                    ORDER BY fold(active_substance_name), fold(product_name)
                    LIMIT ?
                    """,
                    [*product_permits, page_size],
                ).fetchall()
                substances = [
                    {
                        "product_name": query_value(row["product_name"]),
                        "permit_number": query_value(row["permit_number"]),
                        "active_substance_name": query_value(
                            row["active_substance_name"]
                        ),
                        "content": query_value(row["content"]),
                        "unit": query_value(row["unit"]),
                    }
                    for row in substance_rows
                ]

            documents: list[dict[str, str]] = []
            if product_permits:
                document_rows = connection.execute(
                    f"""
                    SELECT
                        product_name, permit_number, document_title,
                        document_url, document_date, document_type,
                        is_latest_document
                    FROM document_links
                    WHERE permit_number IN ({placeholders(product_permits)})
                    ORDER BY
                        is_latest_document DESC,
                        document_order DESC, id DESC
                    LIMIT ?
                    """,
                    [*product_permits, page_size],
                ).fetchall()
                documents = [
                    {
                        "product_name": query_value(row["product_name"]),
                        "permit_number": query_value(row["permit_number"]),
                        "document_title": query_value(row["document_title"]),
                        "document_url": query_value(row["document_url"]),
                        "document_date": query_value(row["document_date"]),
                        "document_type": query_value(row["document_type"]),
                        "is_latest_document": bool(row["is_latest_document"]),
                    }
                    for row in document_rows
                ]

        if len(usage_rows) > page_size:
            note_parts.append(
                f"Large result set; the first {page_size} usage records were returned."
            )
        if not any((products, substances, usage_items, documents)):
            return failure(query, "No matching data found")
        return {
            "ok": True,
            "query": query,
            "products": products,
            "active_substances": substances,
            "usages": usage_items,
            "documents": documents,
            "summary": {
                "product_count": len(products),
                "usage_count": len(usage_items),
                "active_substance_count": len(substances),
                "document_count": len(documents),
                "note": " ".join(note_parts),
            },
        }
    except Exception as error:
        return failure(query, error)
