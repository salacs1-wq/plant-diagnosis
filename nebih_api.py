import os
import re
import sqlite3
import unicodedata
from contextlib import closing
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, HTTPException, Path as ApiPath, Query


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = BASE_DIR / "nebih_master.db"
MAX_PAGE_SIZE = 200

router = APIRouter(tags=["NEBIH"])


def database_path() -> Path:
    return Path(os.environ.get("NEBIH_MASTER_DB", DEFAULT_DATABASE)).resolve()


def fold_text(value: object) -> str:
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKD", str(value).casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def compact_fold_text(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", fold_text(value))


def normalize_permit_number(value: str) -> str:
    return re.sub(r"\s+", "", value).replace(".", "/").strip("/")


def connect() -> sqlite3.Connection:
    path = database_path()
    if not path.is_file():
        raise RuntimeError(f"NEBIH database not found: {path}")
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.create_function("fold", 1, fold_text, deterministic=True)
    connection.create_function("compact_fold", 1, compact_fold_text, deterministic=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def rows_as_dicts(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


def add_folded_contains(
    clauses: list[str],
    parameters: list[Any],
    column: str,
    value: str | None,
) -> None:
    if value and value.strip():
        clauses.append(f"fold({column}) LIKE ?")
        parameters.append(f"%{fold_text(value.strip())}%")


def add_permit_type_filter(
    clauses: list[str],
    parameters: list[Any],
    column: str,
    permit_type: str | None,
) -> None:
    if permit_type and permit_type.strip():
        clauses.append(f"fold({column}) = ?")
        parameters.append(fold_text(permit_type.strip()))


def paged_response(
    connection: sqlite3.Connection,
    table_sql: str,
    select_sql: str,
    clauses: list[str],
    parameters: list[Any],
    order_sql: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    total = connection.execute(
        f"SELECT count(*) {table_sql}{where_sql}", parameters
    ).fetchone()[0]
    cursor = connection.execute(
        f"{select_sql} {table_sql}{where_sql} {order_sql} LIMIT ? OFFSET ?",
        [*parameters, limit, offset],
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": rows_as_dicts(cursor),
    }


@router.get("/products/search")
def search_products(
    q: str | None = Query(default=None, description="Product name fragment"),
    permit_number: str | None = Query(default=None),
    permit_type: str | None = Query(default=None),
    purpose: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    clauses: list[str] = []
    parameters: list[Any] = []
    add_folded_contains(clauses, parameters, "product_name", q)
    add_folded_contains(clauses, parameters, "permit_number", permit_number)
    add_permit_type_filter(clauses, parameters, "permit_type", permit_type)
    add_folded_contains(clauses, parameters, "purpose", purpose)
    if owner and owner.strip():
        clauses.append("fold(coalesce(owner_name, owner)) LIKE ?")
        parameters.append(f"%{fold_text(owner.strip())}%")

    with closing(connect()) as connection:
        return paged_response(
            connection,
            "FROM permit_index",
            """
            SELECT
                id, product_name, permit_number, permit_type,
                reference_product_name, issue_date, expiry_date,
                owner_name, purpose, formulation, marketing_category,
                aerial_application, organic_allowed, bee_risk,
                latest_document_title, latest_document_url
            """,
            clauses,
            parameters,
            "ORDER BY fold(product_name), permit_number",
            limit,
            offset,
        )


@router.get("/usage/search")
def search_usage(
    q: str | None = Query(
        default=None,
        description="Search product, crop, target and treatment time",
    ),
    product_name: str | None = Query(default=None),
    permit_number: str | None = Query(default=None),
    permit_type: str | None = Query(default=None),
    crop: str | None = Query(default=None),
    target: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    clauses: list[str] = []
    parameters: list[Any] = []
    if q and q.strip():
        folded = f"%{fold_text(q.strip())}%"
        clauses.append(
            """
            (
                fold(product_name) LIKE ?
                OR fold(crop) LIKE ?
                OR fold(target) LIKE ?
                OR fold(treatment_time) LIKE ?
            )
            """
        )
        parameters.extend([folded] * 4)
    add_folded_contains(clauses, parameters, "product_name", product_name)
    add_folded_contains(clauses, parameters, "permit_number", permit_number)
    add_permit_type_filter(clauses, parameters, "permit_type", permit_type)
    add_folded_contains(clauses, parameters, "crop", crop)
    add_folded_contains(clauses, parameters, "target", target)

    with closing(connect()) as connection:
        return paged_response(
            connection,
            "FROM usage",
            """
            SELECT
                id, product_name, permit_number, permit_type,
                authorization_start, authorization_end, crop, target,
                max_treatments, min_interval_days,
                dose_min, dose_max, dose_unit, all_doses_raw,
                spray_volume_min, spray_volume_max, spray_volume_unit,
                bbch_min, bbch_max, treatment_time, application_method,
                max_area_ha, phi_days, phi_raw, source_page,
                confidence, remarks
            """,
            clauses,
            parameters,
            "ORDER BY fold(product_name), permit_number, id",
            limit,
            offset,
        )


@router.get("/product/{permit_number:path}")
def get_product(
    permit_number: Annotated[
        str, ApiPath(description="Permit number, including slashes")
    ],
) -> dict[str, Any]:
    normalized = normalize_permit_number(permit_number)
    with closing(connect()) as connection:
        products = rows_as_dicts(
            connection.execute(
                """
                SELECT *
                FROM permit_index
                WHERE replace(replace(trim(permit_number), ' ', ''), '.', '/') = ?
                ORDER BY fold(product_name), permit_type
                """,
                (normalized,),
            )
        )
        if not products:
            raise HTTPException(status_code=404, detail="Permit not found.")
        usage_count = connection.execute(
            """
            SELECT count(*) FROM usage
            WHERE replace(replace(trim(permit_number), ' ', ''), '.', '/') = ?
            """,
            (normalized,),
        ).fetchone()[0]
        document_count = connection.execute(
            """
            SELECT count(*) FROM document_links
            WHERE replace(replace(trim(permit_number), ' ', ''), '.', '/') = ?
            """,
            (normalized,),
        ).fetchone()[0]
        substances = rows_as_dicts(
            connection.execute(
                """
                SELECT active_substance_name, content, unit
                FROM active_substances
                WHERE replace(replace(trim(permit_number), ' ', ''), '.', '/') = ?
                ORDER BY fold(active_substance_name)
                """,
                (normalized,),
            )
        )
    return {
        "permit_number": permit_number,
        "products": products,
        "active_substances": substances,
        "usage_count": usage_count,
        "document_count": document_count,
    }


@router.get("/documents/{permit_number:path}")
def get_documents(
    permit_number: Annotated[
        str, ApiPath(description="Permit number, including slashes")
    ],
) -> dict[str, Any]:
    normalized = normalize_permit_number(permit_number)
    with closing(connect()) as connection:
        documents = rows_as_dicts(
            connection.execute(
                """
                SELECT
                    id, product_name, permit_number, permit_type,
                    document_title, document_url, document_order,
                    document_date, document_type, is_pdf, is_latest_document
                FROM document_links
                WHERE replace(replace(trim(permit_number), ' ', ''), '.', '/') = ?
                ORDER BY document_order, id
                """,
                (normalized,),
            )
        )
    if not documents:
        raise HTTPException(status_code=404, detail="Documents not found.")
    return {
        "permit_number": permit_number,
        "total": len(documents),
        "items": documents,
    }


@router.get("/active-substances/search")
def search_active_substances(
    q: str | None = Query(default=None, description="Active substance name fragment"),
    product_name: str | None = Query(default=None),
    permit_number: str | None = Query(default=None),
    permit_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    clauses: list[str] = []
    parameters: list[Any] = []
    add_folded_contains(clauses, parameters, "active_substance_name", q)
    add_folded_contains(clauses, parameters, "product_name", product_name)
    add_folded_contains(clauses, parameters, "permit_number", permit_number)
    add_permit_type_filter(clauses, parameters, "permit_type", permit_type)

    with closing(connect()) as connection:
        return paged_response(
            connection,
            "FROM active_substances",
            """
            SELECT
                id, product_name, permit_number, permit_type,
                active_substance_name, content, unit
            """,
            clauses,
            parameters,
            "ORDER BY fold(active_substance_name), fold(product_name), permit_number",
            limit,
            offset,
        )


# Imports are intentionally placed after the shared database helpers and SQL
# routes so the Action modules can reuse them without a circular import.
from nebih_actions_api import router as actions_router
from nebih_pesticide_info_api import router as pesticide_info_router


app = FastAPI(
    title="NEBIH SQL API",
    description="Read-only NEBIH product and permit lookup service.",
    version="1.0.0",
)
app.include_router(router)
app.include_router(actions_router)
app.include_router(pesticide_info_router)


@app.get("/health", operation_id="nebihHealth")
def health() -> dict[str, str]:
    return {"status": "ok"}
