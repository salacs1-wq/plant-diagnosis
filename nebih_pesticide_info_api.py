import csv
import re
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from nebih_api import compact_fold_text, connect, fold_text, normalize_permit_number
from nebih_actions_api import parse_limit, range_text, string_range, text


router = APIRouter(prefix="/action", tags=["NEBIH Pesticide Information"])

COMPANY_METADATA_PATH = Path(__file__).resolve().parent / "nebih_company_metadata.csv"
USAGE_SUPPLEMENT_PATH = Path(__file__).resolve().parent / "nebih_usage_supplement.csv"
VERIFIED_USAGE_SUPPLEMENT_PATH = (
    Path(__file__).resolve().parent / "nebih_usage_supplement_verified.csv"
)
PERMIT_KEY = "replace(replace(trim({column}), ' ', ''), '.', '/')"
TARGET_CATEGORY_ALIASES = {
    "parlagfu": ["ketsziku", "gyom"],
    "parlagfu ellen": ["ketsziku", "gyom"],
    "ambrozia": ["ketsziku", "gyom"],
    "ambrosia artemisiifolia": ["ketsziku", "gyom"],
    "amerikai szolokaboca": ["kaboca"],
    "scaphoideus titanus": ["kaboca"],
    "szoloperonoszpora": ["peronoszpora"],
    "szololisztharmat": ["lisztharmat"],
    "botritisz": ["szurkepenesz"],
}
IGNORED_TARGET_WORDS = {"a", "az", "es", "ellen", "illetve", "valamint"}
USAGE_QUESTION_TYPES = {"dose", "usage", "phi", "recommendation"}
CROP_QUERY_ALIASES = {
    "alma": ["alma", "almatermesu"],
    "korte": ["korte", "almatermesu"],
    "birs": ["birs", "almatermesu"],
    "naspolya": ["naspolya", "almatermesu"],
    "almatermesu": ["alma", "korte", "birs", "naspolya", "almatermesu"],
    "almatermesuek": ["alma", "korte", "birs", "naspolya", "almatermesu"],
    "almastermesu": ["alma", "korte", "birs", "naspolya", "almatermesu"],
    "almastermesuek": ["alma", "korte", "birs", "naspolya", "almatermesu"],
    "szolo": ["szolo", "borszolo", "csemegeszolo"],
    "borszolo": ["szolo", "borszolo"],
    "csemegeszolo": ["szolo", "csemegeszolo"],
    "kaposzta": ["kaposzta", "kaposztafele"],
    "karfiol": ["karfiol", "kaposztafele"],
    "brokkoli": ["brokkoli", "kaposztafele"],
    "kelbimbo": ["kelbimbo", "kaposztafele"],
    "kelkaposzta": ["kelkaposzta", "kaposztafele"],
    "karalabe": ["karalabe", "kaposztafele"],
}


def query_value(value: Any) -> str:
    return text(value).strip()


def clean_dose_unit(dose: Any, unit: Any) -> str:
    dose_text = query_value(dose).casefold()
    unit_text = query_value(unit)
    if unit_text and unit_text.casefold() in dose_text:
        return ""
    return unit_text


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
    rows: list[dict[str, str]] = []
    for path in (USAGE_SUPPLEMENT_PATH, VERIFIED_USAGE_SUPPLEMENT_PATH):
        if not path.is_file():
            continue
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows.extend(csv.DictReader(handle))
    return rows


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


def parse_offset(value: str | None) -> int:
    if value is None or not value.strip():
        return 0
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError("offset must be an integer.") from error
    if parsed < 0:
        raise ValueError("offset must be 0 or greater.")
    return parsed


def empty_summary(note: str = "") -> dict[str, Any]:
    return {
        "product_count": 0,
        "usage_count": 0,
        "active_substance_count": 0,
        "document_count": 0,
        "status": "NOT_FOUND",
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
        "error": str(error),
        "query": query,
        "products": [],
        "active_substances": [],
        "usages": [],
        "documents": [],
        "summary": empty_summary(),
    }


def response_status(
    *,
    products: list[dict[str, Any]],
    substances: list[dict[str, Any]],
    usages: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    limited: bool = False,
    popup_only: bool = False,
) -> str:
    if limited:
        return "AMBIGUOUS_LIMITED"
    if usages:
        return "VERIFIED_USAGE"
    if popup_only:
        return "POPUP_ONLY"
    if documents and not products and not substances:
        return "DOCUMENT_ONLY"
    if products or substances:
        return "PRODUCT_ONLY"
    return "NOT_FOUND"


def unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def compact_contains(value: Any, term: str) -> bool:
    compact_term = compact_fold_text(term)
    return bool(compact_term and compact_term in compact_fold_text(query_value(value)))


def numeric_bbch(value: Any) -> int | None:
    match = re.search(r"\d+", query_value(value))
    return int(match.group()) if match else None


def crop_alias_terms(search: str) -> list[str]:
    term = fold_text(search)
    if "napraforgo" in term:
        return ["napraforgo"]
    if term in CROP_QUERY_ALIASES:
        return CROP_QUERY_ALIASES[term]
    return [term]


def crop_sql_filter(column: str, search: str) -> tuple[str, list[str]]:
    term = fold_text(search)
    if "napraforgo" in term:
        sql = f"fold({column}) LIKE '%napraforgo%'"
        if "hagyomanyos" in term:
            sql += (
                f" AND fold({column}) NOT LIKE '%imisun%'"
                f" AND fold({column}) NOT LIKE '%imidazolinon%'"
                f" AND fold({column}) NOT LIKE '%express%'"
                f" AND fold({column}) NOT LIKE '%rezisztens%'"
                f" AND fold({column}) NOT LIKE '%tolerans%'"
            )
        return f"({sql})", []
    terms = crop_alias_terms(search)
    return (
        "("
        + " OR ".join(
            f"(fold({column}) LIKE ? OR compact_fold({column}) LIKE ?)"
            for _ in terms
        )
        + ")",
        [
            value
            for alias in terms
            for value in (f"%{alias}%", f"%{compact_fold_text(alias)}%")
        ],
    )


def crop_matches(value: Any, search: str) -> bool:
    crop = fold_text(query_value(value))
    term = fold_text(search)
    if not crop or not term:
        return False
    if "napraforgo" in term:
        if "napraforgo" not in crop:
            return False
        if "hagyomanyos" in term:
            excluded = ("imisun", "imidazolinon", "express", "rezisztens", "tolerans")
            return not any(word in crop for word in excluded)
        return True
    if term == "alma":
        return bool(
            re.search(r"(?<![a-z0-9(])alma(?![a-z0-9])", crop)
            or re.search(r"(?<![a-z0-9])almatermesu", crop)
        )
    if term in {"almatermesu", "almatermesuek", "almastermesu", "almastermesuek"}:
        return bool(
            re.search(r"(?<![a-z0-9])almatermesu", crop)
            or re.search(r"(?<![a-z0-9(])alma(?![a-z0-9])", crop)
            or re.search(r"(?<![a-z0-9(])korte(?![a-z0-9])", crop)
            or re.search(r"(?<![a-z0-9(])birs(?![a-z0-9])", crop)
            or re.search(r"(?<![a-z0-9(])naspolya(?![a-z0-9])", crop)
        )
    for alias in crop_alias_terms(search):
        if alias and (
            re.search(rf"(?<![a-z0-9]){re.escape(alias)}", crop)
            or compact_contains(value, alias)
        ):
            return True
    if crop == term or crop.startswith(term):
        return True
    if compact_contains(value, term):
        return True
    return bool(
        re.search(
            rf"(?<![a-z0-9(]){re.escape(term)}(?![a-z0-9])",
            crop,
        )
    )


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
    dose = row["dose"]
    return {
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
        "dose": dose,
        "dose_unit": clean_dose_unit(dose, row["dose_unit"]),
        "bbch": string_range(row["bbch_min"], row["bbch_max"]),
        "bbch_match": bbch_match,
        "treatment_time": row["treatment_time"],
        "phi": row["phi"],
        "max_treatments": row["max_treatments"],
        "min_interval_days": row["min_interval_days"],
        "verification_status": "VERIFIED_USAGE",
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
    if target_terms and not all(
        fold_text(term) in fold_text(row["target"]) or compact_contains(row["target"], term)
        for term in target_terms
    ):
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
        "dose_unit": clean_dose_unit(dose, row["dose_unit"]),
        "bbch": string_range(row["bbch_min"], row["bbch_max"]),
        "bbch_match": bbch_match,
        "treatment_time": query_value(row["treatment_time"]),
        "phi": phi,
        "max_treatments": query_value(row["max_treatments"]),
        "min_interval_days": query_value(row["min_interval_days"]),
        "verification_status": "VERIFIED_USAGE",
        "expiry_date": query_value(row["expiry_date"]),
        "latest_document": query_value(row["latest_document"]),
        "source_pdf": query_value(row["source_pdf"]),
    }


def dedupe_usage_items(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, ...]] = set()
    unique: list[dict[str, str]] = []
    # Audited point-4 rows that preserve a compound dose verbatim intentionally
    # have no separate unit. Prefer them over an older parser row for the same
    # permit/crop/target/timing combination.
    ordered_items = sorted(
        items,
        key=lambda item: (
            not (bool(item["dose"]) and not bool(item["dose_unit"])),
            fold_text(item["product_name"]),
            item["permit_number"],
            fold_text(item["crop"]),
            fold_text(item["target"]),
        ),
    )
    for item in ordered_items:
        key = (
            fold_text(item["product_name"]),
            fold_text(item["crop"]),
            fold_text(item["target"]),
            item["bbch"],
            item["phi"],
            item["max_treatments"],
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def placeholders(values: list[Any]) -> str:
    return ", ".join("?" for _ in values)


def product_item(row: Any) -> dict[str, Any]:
    return {
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
        "verification_status": query_value(
            row["verification_status"] if "verification_status" in row.keys() else "PRODUCT_ONLY"
        ),
        "latest_document": query_value(row["latest_document_title"]),
        "source_pdf": query_value(row["latest_document_url"]),
    }


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
            p.bee_risk, p.latest_document_title, p.latest_document_url
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
        "query": query,
        "products": products,
        "active_substances": substances,
        "usages": [],
        "documents": documents,
        "summary": {
            "product_count": len(products),
            "usage_count": 0,
            "active_substance_count": len(substances),
            "document_count": len(documents),
            "status": response_status(
                products=products,
                substances=substances,
                usages=[],
                documents=documents,
            ),
            "note": "META search; usage data was not queried.",
        },
    }


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
        for alias, words in TARGET_CATEGORY_ALIASES.items():
            if alias in folded:
                alternatives.append((words, True))
        alternative_sql: list[str] = []
        for words, is_broader in alternatives:
            alternative_sql.append(
                "("
                + " AND ".join(
                    "(fold(u.target) LIKE ? OR compact_fold(u.target) LIKE ?)"
                    for _ in words
                )
                + ")"
            )
            for word in words:
                parameters.extend([f"%{word}%", f"%{compact_fold_text(word)}%"])
            broader_category_used = broader_category_used or is_broader
        groups.append("(" + " OR ".join(alternative_sql) + ")")
    return "(" + " OR ".join(groups) + ")", parameters, broader_category_used


def popup_target_search(
    terms: list[str],
) -> tuple[str, list[str], bool]:
    sql, parameters, broader = target_search(terms)
    return (
        sql.replace("fold(u.target)", "fold(p.target_raw)").replace(
            "compact_fold(u.target)", "compact_fold(p.target_raw)"
        ),
        parameters,
        broader,
    )


def exact_target_present(items: list[dict[str, str]], terms: list[str]) -> bool:
    exact_terms = [fold_text(term) for term in terms if term]
    if not exact_terms:
        return False
    return any(
        any(
            term in fold_text(item["target"]) or compact_contains(item["target"], term)
            for term in exact_terms
        )
        for item in items
    )


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
    limit: str | None = Query(default="50"),
    offset: str | None = Query(default="0"),
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
        page_size = parse_limit(limit, default=50, maximum=200)
        page_offset = parse_offset(offset)
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
                       OR fold(reference_product_name) LIKE ?
                    UNION
                    SELECT DISTINCT product_name
                    FROM usage
                    WHERE fold(product_name) LIKE ?
                       OR fold(reference_product_name) LIKE ?
                    ORDER BY product_name
                    """,
                    (
                        f"%{folded_name}%",
                        f"%{folded_name}%",
                        f"%{folded_name}%",
                        f"%{folded_name}%",
                    ),
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
                crop_sql, crop_parameters = crop_sql_filter("u.crop", query["crop"])
                usage_clauses.append(crop_sql)
                usage_parameters.extend(crop_parameters)
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
            candidate_limit = min(max(page_size * 10, 200), 2000)
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
                    latest_document, source_pdf
                FROM ranked_usage
                ORDER BY product_row, fold(product_name), fold(crop), usage_id
                LIMIT ?
                """,
                [*usage_parameters, candidate_limit],
            ).fetchall()

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
            usage_items = dedupe_usage_items(usage_items)
            if exact_target_present(usage_items, target_terms):
                note_parts = [
                    note
                    for note in note_parts
                    if "broader weed category" not in note
                ]
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
            total_usage_items = len(usage_items)
            usage_items = usage_items[page_offset : page_offset + page_size]
            usage_permits = unique_strings(
                [item["permit_number"] for item in usage_items]
            )
            popup_product_permits: list[str] = []
            if not usage_items and (query["crop"] or target_terms):
                popup_clauses: list[str] = []
                popup_parameters: list[Any] = []
                if resolved_names:
                    popup_clauses.append(
                        f"fold(p.product_name) IN ({placeholders(resolved_names)})"
                    )
                    popup_parameters.extend(resolved_names)
                elif query["product_name"]:
                    popup_clauses.append("fold(p.product_name) LIKE ?")
                    popup_parameters.append(f"%{fold_text(query['product_name'])}%")
                if active_permits:
                    popup_clauses.append(
                        f"p.permit_number IN ({placeholders(active_permits)})"
                    )
                    popup_parameters.extend(active_permits)
                elif query["active_substance"]:
                    popup_clauses.append("0 = 1")
                if query["permit_number"]:
                    popup_clauses.append(
                        f"{PERMIT_KEY.format(column='p.permit_number')} LIKE ?"
                    )
                    popup_parameters.append(
                        f"%{normalize_permit_number(query['permit_number'])}%"
                    )
                if query["permit_type"]:
                    popup_clauses.append("fold(p.permit_type) LIKE ?")
                    popup_parameters.append(f"%{fold_text(query['permit_type'])}%")
                if query["crop"]:
                    crop_sql, crop_parameters = crop_sql_filter(
                        "p.crop_raw", query["crop"]
                    )
                    popup_clauses.append(crop_sql)
                    popup_parameters.extend(crop_parameters)
                if target_terms:
                    popup_target_sql, popup_target_parameters, broader_target = (
                        popup_target_search(target_terms)
                    )
                    popup_clauses.append(popup_target_sql)
                    popup_parameters.extend(popup_target_parameters)
                    if broader_target:
                        note_parts.append(
                            "Popup/meta scope was checked with a broader target category."
                        )
                if query["purpose"]:
                    popup_clauses.append("fold(p.purpose) LIKE ?")
                    popup_parameters.append(f"%{fold_text(query['purpose'])}%")
                if query["company"]:
                    popup_clauses.append(
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
                    popup_parameters.extend([company_term] * 3)
                if query["owner"]:
                    popup_clauses.append(
                        "fold(coalesce(nullif(p.owner_name, ''), p.owner, '')) LIKE ?"
                    )
                    popup_parameters.append(f"%{fold_text(query['owner'])}%")
                if query["manufacturer"]:
                    popup_clauses.append(
                        "fold(company_manufacturer(p.product_name, p.permit_number)) LIKE ?"
                    )
                    popup_parameters.append(f"%{fold_text(query['manufacturer'])}%")
                if query["representative"]:
                    popup_clauses.append(
                        "fold(company_representative(p.product_name, p.permit_number)) LIKE ?"
                    )
                    popup_parameters.append(f"%{fold_text(query['representative'])}%")
                popup_where = (
                    " WHERE " + " AND ".join(popup_clauses)
                    if popup_clauses
                    else " WHERE 0 = 1"
                )
                popup_rows = connection.execute(
                    f"""
                    SELECT DISTINCT p.permit_number
                    FROM permit_index AS p
                    {popup_where}
                    ORDER BY p.permit_number
                    LIMIT ?
                    """,
                    [*popup_parameters, page_size],
                ).fetchall()
                popup_product_permits = unique_strings(
                    [query_value(row["permit_number"]) for row in popup_rows]
                )
                if popup_product_permits:
                    note_parts.append(
                        "No verified usage row matched, but popup/meta scope matched; document check is required before giving dose, BBCH, PHI or treatment count."
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
            if popup_product_permits:
                product_identity_clauses.append(
                    f"permit_number IN ({placeholders(popup_product_permits)})"
                )
                product_parameters.extend(popup_product_permits)
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
                    formulation, issue_date, expiry_date,
                    COALESCE(NULLIF(owner_name, ''), owner, '') AS owner,
                    company_manufacturer(
                        product_name, permit_number
                    ) AS manufacturer,
                    company_representative(
                        product_name, permit_number
                    ) AS representative,
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
                    "manufacturer": query_value(row["manufacturer"]),
                    "representative": query_value(row["representative"]),
                    "akg_allowed": bool(row["akg_allowed"]),
                    "aop1_bee_allowed": bool(row["aop1_bee_allowed"]),
                    "organic_allowed": bool(row["organic_allowed"]),
                    "bee_risk": query_value(row["bee_risk"]),
                    "verification_status": (
                        "VERIFIED_USAGE"
                        if query_value(row["permit_number"]) in usage_permits
                        else "POPUP_ONLY"
                        if query_value(row["permit_number"]) in popup_product_permits
                        else "PRODUCT_ONLY"
                    ),
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

        total_usage_items = locals().get("total_usage_items", len(usage_items))
        candidate_truncated = len(locals().get("usage_rows", [])) >= locals().get(
            "candidate_limit", page_size + 1
        )
        has_more = page_offset + page_size < total_usage_items or candidate_truncated
        if has_more:
            next_offset = page_offset + page_size
            note_parts.append(
                f"Large result set; returned {len(usage_items)} usage records "
                f"from offset {page_offset}. Use offset={next_offset} for the next page."
            )
        else:
            next_offset = None
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
                "total_usage_count": total_usage_items,
                "limit": page_size,
                "offset": page_offset,
                "has_more": has_more,
                "next_offset": next_offset,
                "active_substance_count": len(substances),
                "document_count": len(documents),
                "status": response_status(
                    products=products,
                    substances=substances,
                    usages=usage_items,
                    documents=documents,
                    limited=has_more,
                    popup_only=bool(popup_product_permits),
                ),
                "note": " ".join(note_parts),
            },
        }
    except Exception as error:
        return failure(query, error)
