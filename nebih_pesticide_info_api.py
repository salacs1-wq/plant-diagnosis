import csv
import re
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from nebih_api import connect, fold_text, normalize_permit_number
from nebih_actions_api import parse_limit, range_text, string_range, text


router = APIRouter(prefix="/action", tags=["NEBIH Pesticide Information"])

COMPANY_METADATA_PATH = Path(__file__).resolve().parent / "nebih_company_metadata.csv"
USAGE_SUPPLEMENT_PATH = Path(__file__).resolve().parent / "nebih_usage_supplement.csv"
PERMIT_KEY = "replace(replace(trim({column}), ' ', ''), '.', '/')"
TARGET_CATEGORY_ALIASES = {
    "parlagfu": [["ketsziku", "gyom"]],
    "amerikaiszolokaboca": [
        ["amerikai", "szolo", "kaboca"],
        ["kaboca"],
        ["scaphoideus", "titanus"],
    ],
    "kaboca": [["kaboca"], ["scaphoideus", "titanus"]],
    "scaphoideustitanus": [
        ["scaphoideus", "titanus"],
        ["amerikai", "szolo", "kaboca"],
        ["kaboca"],
    ],
    "peronoszpora": [["peronoszpora"], ["szoloperonoszpora"]],
    "szoloperonoszpora": [["peronoszpora"]],
    "lisztharmat": [["lisztharmat"], ["szololisztharmat"]],
    "szololisztharmat": [["lisztharmat"]],
    "szurkepenesz": [["szurkepenesz"], ["botritisz"], ["botrytis"]],
    "botritisz": [["szurkepenesz"], ["botritisz"], ["botrytis"]],
}
IGNORED_TARGET_WORDS = {"a", "az", "es", "ellen", "illetve", "valamint"}
USAGE_QUESTION_TYPES = {"dose", "usage", "phi", "recommendation"}
VERIFIED_USAGE = "VERIFIED_USAGE"
PRODUCT_ONLY = "PRODUCT_ONLY"
POPUP_ONLY = "POPUP_ONLY"
DOCUMENT_ONLY = "DOCUMENT_ONLY"
NOT_FOUND = "NOT_FOUND"
AMBIGUOUS_LIMITED = "AMBIGUOUS_LIMITED"


def query_value(value: Any) -> str:
    return text(value).strip()


def search_fold(value: Any) -> str:
    return (
        fold_text(query_value(value))
        .replace("õ", "o")
        .replace("Õ", "o")
        .replace("û", "u")
        .replace("Û", "u")
    )


def sql_fold_terms(value: str) -> list[str]:
    folded = fold_text(value)
    normalized = search_fold(value)
    terms = [folded, normalized]
    if "o" in normalized:
        terms.append(normalized.replace("o", "õ"))
    if "u" in normalized:
        terms.append(normalized.replace("u", "û"))
    return unique_strings(terms)


@lru_cache(maxsize=1)
def company_metadata() -> dict[tuple[str, str], dict[str, str]]:
    if not COMPANY_METADATA_PATH.is_file():
        return {}
    with COMPANY_METADATA_PATH.open(encoding="utf-8", newline="") as handle:
        return {
            (
                fold_text(row["product_name"]),
                normalize_permit_number(row["permit_number"]),
            ): row
            for row in csv.DictReader(handle)
        }


def company_value(product_name: Any, permit_number: Any, field: str) -> str:
    product = fold_text(query_value(product_name))
    permit = normalize_permit_number(query_value(permit_number))
    return company_metadata().get((product, permit), {}).get(field, "")


@lru_cache(maxsize=1)
def usage_supplements() -> list[dict[str, str]]:
    if not USAGE_SUPPLEMENT_PATH.is_file():
        return []
    with USAGE_SUPPLEMENT_PATH.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def register_company_functions(connection: Any) -> None:
    connection.create_function(
        "company_manufacturer",
        2,
        lambda product, permit: company_value(
            product, permit, "manufacturer"
        ),
        deterministic=True,
    )
    connection.create_function(
        "company_representative",
        2,
        lambda product, permit: company_value(
            product, permit, "representative"
        ),
        deterministic=True,
    )


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
        "popup_match_count": 0,
        "active_substance_count": 0,
        "document_count": 0,
        "has_more_usage": False,
        "total_usage_matches": 0,
        "note": note,
    }


def response_query(**values: Any) -> dict[str, str]:
    return {key: query_value(value) for key, value in values.items()}


def query_route(query: dict[str, str]) -> str:
    usage_dimensions = (
        "crop",
        "target",
        "pest_or_disease",
        "weed",
        "bbch",
        "phi_days",
    )
    if any(query[field] for field in usage_dimensions):
        return "USAGE"
    if fold_text(query["question_type"]) in USAGE_QUESTION_TYPES:
        return "USAGE"
    return "META"


def failure(query: dict[str, str], error: Exception | str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": NOT_FOUND,
        "error": str(error),
        "query": query,
        "products": [],
        "active_substances": [],
        "usages": [],
        "popup_matches": [],
        "documents": [],
        "summary": empty_summary(),
    }


def unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def numeric_bbch(value: Any) -> int | None:
    match = re.search(r"\d+", query_value(value))
    return int(match.group()) if match else None


def crop_matches(value: Any, search: str) -> bool:
    crop = search_fold(value)
    term = search_fold(search)
    if not crop or not term:
        return False
    apple_terms = {"alma", "almatermesu", "almatermesuek", "almastermesu", "almastermesuek"}
    if term in apple_terms:
        return bool(
            re.search(r"(?<![a-z0-9(])alma(?![a-z0-9])", crop)
            or re.search(r"(?<![a-z0-9])almatermesu", crop)
        )
    if "szolo" in term:
        return "szolo" in crop or "borszolo" in crop or "csemegeszolo" in crop
    if crop == term or crop.startswith(term):
        return True
    return bool(
        re.search(
            rf"(?<![a-z0-9(]){re.escape(term)}(?![a-z0-9])",
            crop,
        )
    )


def split_popup_terms(value: Any) -> list[str]:
    raw = query_value(value)
    if not raw:
        return []
    parts = re.split(r"[,;\n]+", raw)
    cleaned = []
    for part in parts:
        item = re.sub(r"\s+-\s+[A-Z0-9]+$", "", part).strip()
        if item:
            cleaned.append(item)
    return unique_strings(cleaned)


def target_groups(terms: list[str]) -> tuple[list[list[str]], bool]:
    groups: list[list[str]] = []
    broader_category_used = False
    for term in terms:
        folded = search_fold(term)
        if not folded:
            continue
        groups.append([folded])
        tokens = [
            "gyom" if token.startswith("gyom") else token
            for token in re.findall(r"[a-z0-9]+", folded)
            if token not in IGNORED_TARGET_WORDS
        ]
        if len(tokens) > 1:
            groups.append(tokens)
        key = "".join(tokens) or folded
        if key in TARGET_CATEGORY_ALIASES:
            groups.extend(TARGET_CATEGORY_ALIASES[key])
            broader_category_used = True
    return groups, broader_category_used


def target_text_matches(value: Any, terms: list[str]) -> bool:
    if not terms:
        return True
    haystack = search_fold(value)
    groups, _broader = target_groups(terms)
    return any(all(word in haystack for word in group) for group in groups)


def popup_index(row: Any) -> dict[str, list[str]]:
    crops = split_popup_terms(row["crop_raw"])
    targets = split_popup_terms(row["target_raw"])
    weed_markers = ("gyom", "parlagfu", "fenyerc", "tarack", "arvakeles")
    disease_markers = (
        "peronoszpora",
        "lisztharmat",
        "szurkepenesz",
        "botritisz",
        "botrytis",
        "rozsda",
        "foltossag",
        "monilia",
        "fuzari",
        "alternaria",
        "feherpenesz",
        "betegseg",
    )
    pest_markers = (
        "kaboca",
        "scaphoideus",
        "bogar",
        "moly",
        "tetu",
        "lepke",
        "hernyo",
        "tripsz",
        "atka",
        "legy",
        "larva",
    )
    weeds: list[str] = []
    diseases: list[str] = []
    pests: list[str] = []
    for item in targets:
        folded = search_fold(item)
        if any(marker in folded for marker in weed_markers):
            weeds.append(item)
        if any(marker in folded for marker in disease_markers):
            diseases.append(item)
        if any(marker in folded for marker in pest_markers) or (
            item not in weeds and item not in diseases
        ):
            pests.append(item)
    return {
        "popup_crops": crops,
        "popup_targets": targets,
        "popup_pests": unique_strings(pests),
        "popup_diseases": unique_strings(diseases),
        "popup_weeds": unique_strings(weeds),
    }


def supplement_usage_item(row: dict[str, str], bbch_query: int | None) -> dict[str, str]:
    bbch_min = numeric_bbch(row["bbch_min"])
    bbch_max = numeric_bbch(row["bbch_max"])
    bbch_match = ""
    if bbch_query is not None:
        low = bbch_min if bbch_min is not None else bbch_max
        high = bbch_max if bbch_max is not None else bbch_min
        bbch_match = (
            "uncertain"
            if low is None or high is None
            else "match" if low <= bbch_query <= high else "no_match"
        )
    return {
        "status": VERIFIED_USAGE,
        "product_name": row["product_name"],
        "permit_number": row["permit_number"],
        "permit_type": row["permit_type"],
        "owner": company_value(row["product_name"], row["permit_number"], "owner"),
        "manufacturer": company_value(
            row["product_name"], row["permit_number"], "manufacturer"
        ),
        "representative": company_value(
            row["product_name"], row["permit_number"], "representative"
        ),
        "crop": row["crop"],
        "target": row["target"],
        "purpose": row["purpose"],
        "dose": row["dose"],
        "dose_unit": row["dose_unit"],
        "bbch": string_range(row["bbch_min"], row["bbch_max"]),
        "bbch_match": bbch_match,
        "treatment_time": row["treatment_time"],
        "phi": row["phi"],
        "max_treatments": row["max_treatments"],
        "min_interval_days": row["min_interval_days"],
        "expiry_date": row["expiry_date"],
        "latest_document": row["latest_document"],
        "source_pdf": row["source_pdf"],
    }


def supplement_matches(
    row: dict[str, str],
    query: dict[str, str],
    target_terms: list[str],
    bbch_number: int | None,
) -> bool:
    if query["active_substance"]:
        return False
    if any(
        query[field]
        for field in (
            "phi_days",
            "akg_allowed",
            "aop1_bee_allowed",
            "organic_allowed",
        )
    ):
        return False
    if query["product_name"] and fold_text(query["product_name"]) not in fold_text(
        row["product_name"]
    ):
        return False
    if query["crop"] and not crop_matches(row["crop"], query["crop"]):
        return False
    if target_terms and not target_text_matches(row["target"], target_terms):
        return False
    if query["purpose"] and fold_text(query["purpose"]) not in fold_text(row["purpose"]):
        return False
    company_blob = " ".join(
        (
            company_value(row["product_name"], row["permit_number"], "owner"),
            company_value(row["product_name"], row["permit_number"], "manufacturer"),
            company_value(row["product_name"], row["permit_number"], "representative"),
        )
    )
    if query["company"] and fold_text(query["company"]) not in fold_text(company_blob):
        return False
    for field in ("owner", "manufacturer", "representative"):
        if query[field] and fold_text(query[field]) not in fold_text(
            company_value(row["product_name"], row["permit_number"], field)
        ):
            return False
    if bbch_number is not None:
        item = supplement_usage_item(row, bbch_number)
        if item["bbch_match"] == "no_match":
            return False
    return True


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
        "status": VERIFIED_USAGE,
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


def product_item(row: Any) -> dict[str, Any]:
    popup = popup_index(row)
    return {
        "status": PRODUCT_ONLY,
        "product_name": query_value(row["product_name"]),
        "permit_number": query_value(row["permit_number"]),
        "permit_type": query_value(row["permit_type"]),
        "purpose": query_value(row["purpose"]),
        "formulation": query_value(row["formulation"]),
        "marketing_category": query_value(row["marketing_category"]),
        "issue_date": query_value(row["issue_date"]),
        "expiry_date": query_value(row["expiry_date"]),
        "owner": query_value(row["owner"]),
        "manufacturer": query_value(row["manufacturer"]),
        "representative": query_value(row["representative"]),
        "akg_allowed": bool(row["akg_allowed"]),
        "aop1_bee_allowed": bool(row["aop1_bee_allowed"]),
        "organic_allowed": bool(row["organic_allowed"]),
        "bee_risk": query_value(row["bee_risk"]),
        "crop_raw": query_value(row["crop_raw"]),
        "target_raw": query_value(row["target_raw"]),
        **popup,
        "latest_document": query_value(row["latest_document_title"]),
        "source_pdf": query_value(row["latest_document_url"]),
    }


def popup_match_item(row: Any) -> dict[str, Any]:
    popup = popup_index(row)
    return {
        "status": POPUP_ONLY,
        "product_name": query_value(row["product_name"]),
        "permit_number": query_value(row["permit_number"]),
        "permit_type": query_value(row["permit_type"]),
        "owner": query_value(row["owner"]),
        "manufacturer": query_value(row["manufacturer"]),
        "representative": query_value(row["representative"]),
        "purpose": query_value(row["purpose"]),
        "expiry_date": query_value(row["expiry_date"]),
        "latest_document": query_value(row["latest_document_title"]),
        "source_pdf": query_value(row["latest_document_url"]),
        "crop_raw": query_value(row["crop_raw"]),
        "target_raw": query_value(row["target_raw"]),
        **popup,
    }


def popup_matches_query(
    connection: Any,
    product_where: str,
    product_parameters: list[Any],
    query: dict[str, str],
    target_terms: list[str],
    page_size: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        f"""
        SELECT
            product_name, permit_number, permit_type, purpose,
            expiry_date, COALESCE(NULLIF(owner_name, ''), owner, '') AS owner,
            company_manufacturer(product_name, permit_number) AS manufacturer,
            company_representative(product_name, permit_number) AS representative,
            latest_document_title, latest_document_url, crop_raw, target_raw
        FROM permit_index
        {product_where}
        ORDER BY fold(product_name), permit_number
        LIMIT ?
        """,
        [*product_parameters, max(page_size * 3, 30)],
    ).fetchall()
    matches = []
    for row in rows:
        if query["crop"] and not crop_matches(row["crop_raw"], query["crop"]):
            continue
        if target_terms and not target_text_matches(row["target_raw"], target_terms):
            continue
        if not query["crop"] and not target_terms:
            continue
        matches.append(popup_match_item(row))
        if len(matches) >= page_size:
            break
    return matches


def meta_search(
    connection: Any,
    query: dict[str, str],
    page_size: int,
    akg_value: int | None,
    bee_value: int | None,
    organic_value: int | None,
) -> dict[str, Any]:
    clauses: list[str] = []
    parameters: list[Any] = []
    if query["product_name"]:
        clauses.append("fold(p.product_name) LIKE ?")
        parameters.append(f"%{fold_text(query['product_name'])}%")
    if query["permit_number"]:
        clauses.append(f"{PERMIT_KEY.format(column='p.permit_number')} LIKE ?")
        parameters.append(
            f"%{normalize_permit_number(query['permit_number'])}%"
        )
    if query["permit_type"]:
        clauses.append("fold(p.permit_type) LIKE ?")
        parameters.append(f"%{fold_text(query['permit_type'])}%")
    if query["purpose"]:
        clauses.append("fold(p.purpose) LIKE ?")
        parameters.append(f"%{fold_text(query['purpose'])}%")
    if query["expiry_date"]:
        clauses.append("fold(p.expiry_date) LIKE ?")
        parameters.append(f"%{fold_text(query['expiry_date'])}%")
    if query["marketing_category"]:
        clauses.append("fold(p.marketing_category) LIKE ?")
        parameters.append(f"%{fold_text(query['marketing_category'])}%")
    if query["bee_risk"]:
        clauses.append("fold(p.bee_risk) LIKE ?")
        parameters.append(f"%{fold_text(query['bee_risk'])}%")
    if query["active_substance"]:
        clauses.append(
            """
            EXISTS (
                SELECT 1
                FROM active_substances AS a
                WHERE {active_permit} = {permit}
                  AND fold(a.active_substance_name) LIKE ?
            )
            """.format(
                active_permit=PERMIT_KEY.format(column="a.permit_number"),
                permit=PERMIT_KEY.format(column="p.permit_number"),
            )
        )
        parameters.append(f"%{fold_text(query['active_substance'])}%")
    if query["company"]:
        clauses.append(
            """
            (
                fold(coalesce(nullif(p.owner_name, ''), p.owner, '')) LIKE ?
                OR fold(company_manufacturer(
                    p.product_name, p.permit_number
                )) LIKE ?
                OR fold(company_representative(
                    p.product_name, p.permit_number
                )) LIKE ?
            )
            """
        )
        company_term = f"%{fold_text(query['company'])}%"
        parameters.extend([company_term] * 3)
    if query["owner"]:
        clauses.append(
            "fold(coalesce(nullif(p.owner_name, ''), p.owner, '')) LIKE ?"
        )
        parameters.append(f"%{fold_text(query['owner'])}%")
    if query["manufacturer"]:
        clauses.append(
            "fold(company_manufacturer(p.product_name, p.permit_number)) LIKE ?"
        )
        parameters.append(f"%{fold_text(query['manufacturer'])}%")
    if query["representative"]:
        clauses.append(
            "fold(company_representative(p.product_name, p.permit_number)) LIKE ?"
        )
        parameters.append(f"%{fold_text(query['representative'])}%")
    for column, value in (
        ("akg_allowed", akg_value),
        ("aop1_bee_allowed", bee_value),
        ("organic_allowed", organic_value),
    ):
        if value is not None:
            clauses.append(f"p.{column} = ?")
            parameters.append(value)

    where_sql = " WHERE " + " AND ".join(clauses) if clauses else " WHERE 0 = 1"
    product_rows = connection.execute(
        f"""
        SELECT
            p.product_name, p.permit_number, p.permit_type, p.purpose,
            p.formulation, p.marketing_category, p.issue_date, p.expiry_date,
            COALESCE(NULLIF(p.owner_name, ''), p.owner, '') AS owner,
            company_manufacturer(
                p.product_name, p.permit_number
            ) AS manufacturer,
            company_representative(
                p.product_name, p.permit_number
            ) AS representative,
            p.akg_allowed, p.aop1_bee_allowed, p.organic_allowed,
            p.bee_risk, p.crop_raw, p.target_raw,
            p.latest_document_title, p.latest_document_url
        FROM permit_index AS p
        {where_sql}
        ORDER BY fold(p.product_name), p.permit_number
        LIMIT ?
        """,
        [*parameters, page_size],
    ).fetchall()
    products = [product_item(row) for row in product_rows]
    permits = unique_strings([item["permit_number"] for item in products])

    substances: list[dict[str, str]] = []
    if permits:
        substance_clauses = [
            f"permit_number IN ({placeholders(permits)})"
        ]
        substance_parameters: list[Any] = list(permits)
        if query["active_substance"]:
            substance_clauses.append("fold(active_substance_name) LIKE ?")
            substance_parameters.append(
                f"%{fold_text(query['active_substance'])}%"
            )
        substance_rows = connection.execute(
            f"""
            SELECT
                product_name, permit_number,
                active_substance_name, content, unit
            FROM active_substances
            WHERE {" AND ".join(substance_clauses)}
            ORDER BY fold(active_substance_name), fold(product_name)
            LIMIT ?
            """,
            [*substance_parameters, page_size],
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

    documents: list[dict[str, Any]] = []
    if permits:
        document_rows = connection.execute(
            f"""
            SELECT
                product_name, permit_number, document_title,
                document_url, document_date, document_type,
                is_latest_document
            FROM document_links
            WHERE permit_number IN ({placeholders(permits)})
            ORDER BY
                is_latest_document DESC,
                document_order DESC, id DESC
            LIMIT ?
            """,
            [*permits, page_size],
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

    if not any((products, substances, documents)):
        return failure(query, "No matching data found")
    return {
        "ok": True,
        "status": PRODUCT_ONLY,
        "query": query,
        "products": products,
        "active_substances": substances,
        "usages": [],
        "popup_matches": [],
        "documents": documents,
        "summary": {
            "product_count": len(products),
            "usage_count": 0,
            "popup_match_count": 0,
            "active_substance_count": len(substances),
            "document_count": len(documents),
            "has_more_usage": False,
            "total_usage_matches": 0,
            "note": "META search; usage data was not queried.",
        },
    }


def target_search(
    terms: list[str],
) -> tuple[str, list[str], bool]:
    groups: list[str] = []
    parameters: list[str] = []
    word_groups, broader_category_used = target_groups(terms)
    for words in word_groups:
        word_sql = []
        for word in words:
            variants = sql_fold_terms(word)
            word_sql.append(
                "(" + " OR ".join("fold(u.target) LIKE ?" for _ in variants) + ")"
            )
            parameters.extend(f"%{variant}%" for variant in variants)
        groups.append("(" + " AND ".join(word_sql) + ")")
    return "(" + " OR ".join(groups) + ")", parameters, broader_category_used


def compact_usage_item(item: dict[str, Any]) -> dict[str, str]:
    return {
        "status": query_value(item.get("status")),
        "product_name": query_value(item.get("product_name")),
        "permit_number": query_value(item.get("permit_number")),
        "permit_type": query_value(item.get("permit_type")),
        "crop": query_value(item.get("crop")),
        "target": query_value(item.get("target")),
        "purpose": query_value(item.get("purpose")),
        "dose": query_value(item.get("dose")),
        "dose_unit": query_value(item.get("dose_unit")),
        "bbch": query_value(item.get("bbch")),
        "treatment_time": query_value(item.get("treatment_time")),
        "phi": query_value(item.get("phi")),
        "max_treatments": query_value(item.get("max_treatments")),
        "min_interval_days": query_value(item.get("min_interval_days")),
        "source_pdf": query_value(item.get("source_pdf")),
    }


def compact_product_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": query_value(item.get("status")),
        "product_name": query_value(item.get("product_name")),
        "permit_number": query_value(item.get("permit_number")),
        "permit_type": query_value(item.get("permit_type")),
        "purpose": query_value(item.get("purpose")),
        "owner": query_value(item.get("owner")),
        "manufacturer": query_value(item.get("manufacturer")),
        "representative": query_value(item.get("representative")),
        "expiry_date": query_value(item.get("expiry_date")),
        "akg_allowed": bool(item.get("akg_allowed")),
        "aop1_bee_allowed": bool(item.get("aop1_bee_allowed")),
        "organic_allowed": bool(item.get("organic_allowed")),
        "bee_risk": query_value(item.get("bee_risk")),
        "popup_crops": item.get("popup_crops", []),
        "popup_targets": item.get("popup_targets", []),
        "source_pdf": query_value(item.get("source_pdf")),
    }


def compact_substance_item(item: dict[str, Any]) -> dict[str, str]:
    return {
        "product_name": query_value(item.get("product_name")),
        "permit_number": query_value(item.get("permit_number")),
        "active_substance_name": query_value(item.get("active_substance_name")),
        "content": query_value(item.get("content")),
        "unit": query_value(item.get("unit")),
    }


def compact_document_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_name": query_value(item.get("product_name")),
        "permit_number": query_value(item.get("permit_number")),
        "document_title": query_value(item.get("document_title")),
        "document_url": query_value(item.get("document_url")),
        "document_date": query_value(item.get("document_date")),
        "is_latest_document": bool(item.get("is_latest_document")),
    }


def compact_response(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("ok"):
        return {
            "ok": False,
            "status": query_value(payload.get("status")) or NOT_FOUND,
            "error": query_value(payload.get("error")) or "No matching data found",
            "query": payload.get("query", {}),
            "products": [],
            "active_substances": [],
            "usages": [],
            "documents": [],
            "summary": payload.get("summary", empty_summary()),
        }
    products = [
        compact_product_item(item)
        for item in payload.get("products", [])[:10]
    ]
    usages = [
        compact_usage_item(item)
        for item in payload.get("usages", [])[:50]
    ]
    popup_products = [
        compact_product_item(item)
        for item in payload.get("popup_matches", [])[:10]
    ]
    substances = [
        compact_substance_item(item)
        for item in payload.get("active_substances", [])[:20]
    ]
    documents = [
        compact_document_item(item)
        for item in payload.get("documents", [])
        if item.get("is_latest_document")
    ][:10]
    if payload.get("status") in {POPUP_ONLY, PRODUCT_ONLY} and popup_products:
        products = popup_products
    return {
        "ok": True,
        "status": payload.get("status"),
        "query": payload.get("query", {}),
        "products": products,
        "active_substances": substances,
        "usages": usages,
        "documents": documents,
        "summary": payload.get("summary", empty_summary()),
    }


@router.get("/pesticide-info", operation_id="getPesticideInformation")
def pesticide_information(
    product_name: str | None = Query(default=None),
    active_substance: str | None = Query(default=None),
    permit_number: str | None = Query(default=None),
    permit_type: str | None = Query(default=None),
    expiry_date: str | None = Query(default=None),
    marketing_category: str | None = Query(default=None),
    bee_risk: str | None = Query(default=None),
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
        permit_number=permit_number,
        permit_type=permit_type,
        expiry_date=expiry_date,
        marketing_category=marketing_category,
        bee_risk=bee_risk,
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
    query["query_type"] = query_route(query)
    try:
        page_size = parse_limit(limit, default=20, maximum=50)
        if not any(
            query[key]
            for key in (
                "product_name",
                "active_substance",
                "permit_number",
                "permit_type",
                "expiry_date",
                "marketing_category",
                "bee_risk",
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
        total_usage_matches = 0
        has_more_usage = False

        with closing(connect()) as connection:
            register_company_functions(connection)
            if query["query_type"] == "META":
                return meta_search(
                    connection,
                    query,
                    page_size,
                    akg_value,
                    bee_value,
                    organic_value,
                )
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
            if query["permit_number"]:
                usage_clauses.append(
                    f"{PERMIT_KEY.format(column='u.permit_number')} LIKE ?"
                )
                usage_parameters.append(
                    f"%{normalize_permit_number(query['permit_number'])}%"
                )
            if query["permit_type"]:
                usage_clauses.append("fold(u.permit_type) LIKE ?")
                usage_parameters.append(f"%{fold_text(query['permit_type'])}%")
            if query["crop"]:
                crop_term = search_fold(query["crop"])
                if crop_term in {
                    "alma",
                    "almatermesu",
                    "almatermesuek",
                    "almastermesu",
                    "almastermesuek",
                }:
                    usage_clauses.append(
                        "(fold(u.crop) LIKE '%alma%' "
                        "OR fold(u.crop) LIKE '%almatermesu%')"
                    )
                elif "szolo" in crop_term:
                    usage_clauses.append(
                        "(fold(u.crop) LIKE '%szolo%' "
                        "OR fold(u.crop) LIKE '%szõlõ%')"
                    )
                else:
                    crop_terms = sql_fold_terms(query["crop"])
                    usage_clauses.append(
                        "("
                        + " OR ".join("fold(u.crop) LIKE ?" for _ in crop_terms)
                        + ")"
                    )
                    usage_parameters.extend(f"%{term}%" for term in crop_terms)
            if target_terms:
                target_sql, target_parameters, broader_target = target_search(
                    target_terms
                )
                usage_clauses.append(target_sql)
                usage_parameters.extend(target_parameters)
                if broader_target:
                    note_parts.append(
                        "The exact target term was not present in the usage "
                        "table; a broader synonym/category was also searched."
                    )
            if query["purpose"]:
                usage_clauses.append("fold(p.purpose) LIKE ?")
                usage_parameters.append(f"%{fold_text(query['purpose'])}%")
            if query["expiry_date"]:
                usage_clauses.append("fold(p.expiry_date) LIKE ?")
                usage_parameters.append(f"%{fold_text(query['expiry_date'])}%")
            if query["marketing_category"]:
                usage_clauses.append("fold(p.marketing_category) LIKE ?")
                usage_parameters.append(
                    f"%{fold_text(query['marketing_category'])}%"
                )
            if query["bee_risk"]:
                usage_clauses.append("fold(p.bee_risk) LIKE ?")
                usage_parameters.append(f"%{fold_text(query['bee_risk'])}%")
            if query["company"]:
                usage_clauses.append(
                    """
                    (
                        fold(coalesce(nullif(p.owner_name, ''), p.owner, '')) LIKE ?
                        OR fold(company_manufacturer(
                            u.product_name, u.permit_number
                        )) LIKE ?
                        OR fold(company_representative(
                            u.product_name, u.permit_number
                        )) LIKE ?
                    )
                    """
                )
                company_term = f"%{fold_text(query['company'])}%"
                usage_parameters.extend([company_term] * 3)
            if query["owner"]:
                usage_clauses.append(
                    "fold(coalesce(nullif(p.owner_name, ''), p.owner, '')) LIKE ?"
                )
                usage_parameters.append(f"%{fold_text(query['owner'])}%")
            if query["manufacturer"]:
                usage_clauses.append(
                    "fold(company_manufacturer("
                    "u.product_name, u.permit_number)) LIKE ?"
                )
                usage_parameters.append(
                    f"%{fold_text(query['manufacturer'])}%"
                )
            if query["representative"]:
                usage_clauses.append(
                    "fold(company_representative("
                    "u.product_name, u.permit_number)) LIKE ?"
                )
                usage_parameters.append(
                    f"%{fold_text(query['representative'])}%"
                )
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
                        company_manufacturer(
                            u.product_name, u.permit_number
                        ) AS manufacturer,
                        company_representative(
                            u.product_name, u.permit_number
                        ) AS representative,
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
                    latest_document, source_pdf,
                    COUNT(*) OVER() AS total_usage_matches
                FROM ranked_usage
                ORDER BY product_row, fold(product_name), fold(crop), usage_id
                LIMIT ?
                """,
                [*usage_parameters, candidate_limit],
            ).fetchall()

            total_usage_matches = (
                int(usage_rows[0]["total_usage_matches"]) if usage_rows else 0
            )
            usage_items = [usage_item(row, bbch_number) for row in usage_rows]
            supplemental_items = [
                supplement_usage_item(row, bbch_number)
                for row in usage_supplements()
                if supplement_matches(
                    row,
                    query,
                    target_terms,
                    bbch_number,
                )
            ]
            existing_keys = {
                (
                    item["product_name"],
                    item["permit_number"],
                    item["crop"],
                    item["target"],
                    item["dose"],
                    item["dose_unit"],
                )
                for item in usage_items
            }
            usage_items.extend(
                item
                for item in supplemental_items
                if (
                    item["product_name"],
                    item["permit_number"],
                    item["crop"],
                    item["target"],
                    item["dose"],
                    item["dose_unit"],
                )
                not in existing_keys
            )
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
            filtered_usage_count = len(usage_items)
            has_more_usage = (
                filtered_usage_count > page_size
                or total_usage_matches > page_size
                or len(usage_rows) >= candidate_limit
            )
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
                    """
                    (
                        fold(coalesce(nullif(owner_name, ''), owner, '')) LIKE ?
                        OR fold(company_manufacturer(
                            product_name, permit_number
                        )) LIKE ?
                        OR fold(company_representative(
                            product_name, permit_number
                        )) LIKE ?
                    )
                    """
                )
                company_term = f"%{fold_text(query['company'])}%"
                product_parameters.extend([company_term] * 3)
            if query["owner"]:
                product_filter_clauses.append(
                    "fold(coalesce(nullif(owner_name, ''), owner, '')) LIKE ?"
                )
                product_parameters.append(f"%{fold_text(query['owner'])}%")
            if query["manufacturer"]:
                product_filter_clauses.append(
                    "fold(company_manufacturer("
                    "product_name, permit_number)) LIKE ?"
                )
                product_parameters.append(
                    f"%{fold_text(query['manufacturer'])}%"
                )
            if query["representative"]:
                product_filter_clauses.append(
                    "fold(company_representative("
                    "product_name, permit_number)) LIKE ?"
                )
                product_parameters.append(
                    f"%{fold_text(query['representative'])}%"
                )
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
                    formulation, marketing_category, issue_date, expiry_date,
                    COALESCE(NULLIF(owner_name, ''), owner, '') AS owner,
                    company_manufacturer(
                        product_name, permit_number
                    ) AS manufacturer,
                    company_representative(
                        product_name, permit_number
                    ) AS representative,
                    akg_allowed, aop1_bee_allowed, organic_allowed,
                    bee_risk, crop_raw, target_raw,
                    latest_document_title, latest_document_url
                FROM permit_index
                {product_where}
                ORDER BY fold(product_name), permit_number
                LIMIT ?
                """,
                [*product_parameters, page_size],
            ).fetchall()
            products = [product_item(row) for row in product_rows]
            popup_matches = popup_matches_query(
                connection,
                product_where,
                product_parameters,
                query,
                target_terms,
                page_size,
            )
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

        if has_more_usage:
            note_parts.append(
                "Lehetséges további találat; a limit miatt a usage lista nem biztos, hogy teljes."
            )
        if usage_items:
            status = AMBIGUOUS_LIMITED if has_more_usage else VERIFIED_USAGE
        elif popup_matches:
            status = POPUP_ONLY
            note_parts.append(
                "Popup/meta alapján gyanús találat van, de usage rekord nincs; dokumentumellenőrzés szükséges."
            )
        elif products or substances or documents:
            status = PRODUCT_ONLY if products or substances else DOCUMENT_ONLY
            note_parts.append(
                "Product/meta találat van, de igazolt usage rekord nincs; dózis nem adható."
            )
        else:
            status = NOT_FOUND

        if not any((products, substances, usage_items, popup_matches, documents)):
            return failure(query, "No matching data found")
        return {
            "ok": True,
            "status": status,
            "query": query,
            "products": products,
            "active_substances": substances,
            "usages": usage_items,
            "popup_matches": popup_matches,
            "documents": documents,
            "summary": {
                "product_count": len(products),
                "usage_count": len(usage_items),
                "popup_match_count": len(popup_matches),
                "active_substance_count": len(substances),
                "document_count": len(documents),
                "has_more_usage": has_more_usage,
                "total_usage_matches": total_usage_matches,
                "note": " ".join(note_parts),
            },
        }
    except Exception as error:
        return failure(query, error)


@router.get("/pesticide-answer", operation_id="getPesticideAnswer")
def pesticide_answer(
    product_name: str | None = Query(default=None),
    active_substance: str | None = Query(default=None),
    permit_number: str | None = Query(default=None),
    permit_type: str | None = Query(default=None),
    expiry_date: str | None = Query(default=None),
    marketing_category: str | None = Query(default=None),
    bee_risk: str | None = Query(default=None),
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
    payload = pesticide_information(
        product_name=product_name,
        active_substance=active_substance,
        permit_number=permit_number,
        permit_type=permit_type,
        expiry_date=expiry_date,
        marketing_category=marketing_category,
        bee_risk=bee_risk,
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
        question_type=question_type,
        limit=limit,
    )
    return compact_response(payload)
