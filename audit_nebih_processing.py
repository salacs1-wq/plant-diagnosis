import csv
import re
from contextlib import closing
from pathlib import Path
from typing import Any

from nebih_api import connect


REPORT_PATH = Path(__file__).resolve().parent / "nebih_processing_audit_report.csv"


def count(connection: Any, sql: str, parameters: tuple[Any, ...] = ()) -> int:
    return int(connection.execute(sql, parameters).fetchone()[0])


def sample(connection: Any, sql: str, parameters: tuple[Any, ...] = ()) -> str:
    rows = connection.execute(sql, parameters).fetchall()
    return "; ".join(
        f"{row['product_name']} ({row['permit_number']})" for row in rows
    )


def write_report(rows: list[dict[str, Any]]) -> None:
    with REPORT_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("metric", "value", "detail"),
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    with closing(connect()) as connection:
        product_records_without_usage = count(
            connection,
            """
            SELECT count(*)
            FROM permit_index AS p
            WHERE NOT EXISTS (
                SELECT 1
                FROM usage AS u
                WHERE replace(replace(trim(u.permit_number), ' ', ''), '.', '/')
                    = replace(replace(trim(p.permit_number), ' ', ''), '.', '/')
            )
            """,
        )
        usage_missing_bbch = count(
            connection,
            """
            SELECT count(*)
            FROM usage
            WHERE coalesce(trim(bbch_min), '') = ''
              AND coalesce(trim(bbch_max), '') = ''
              AND coalesce(trim(treatment_time), '') = ''
            """,
        )
        usage_missing_phi = count(
            connection,
            """
            SELECT count(*)
            FROM usage
            WHERE coalesce(trim(phi_days), '') = ''
              AND coalesce(trim(phi_raw), '') = ''
            """,
        )
        usage_multiple_crops = count(
            connection,
            """
            SELECT count(*)
            FROM usage
            WHERE crop LIKE '%,%'
               OR crop LIKE '%;%'
               OR fold(crop) LIKE '% es %'
            """,
        )
        usage_multiple_targets = count(
            connection,
            """
            SELECT count(*)
            FROM usage
            WHERE target LIKE '%,%'
               OR target LIKE '%;%'
               OR fold(target) LIKE '% es %'
            """,
        )
        products_above_action_limit = count(
            connection,
            """
            SELECT count(*)
            FROM (
                SELECT permit_number, product_name, count(*) AS usage_count
                FROM usage
                GROUP BY permit_number, product_name
                HAVING usage_count > 20
            )
            """,
        )
        popup_crop_records = count(
            connection,
            """
            SELECT count(*)
            FROM permit_index
            WHERE coalesce(trim(crop_raw), '') <> ''
            """,
        )
        popup_target_records = count(
            connection,
            """
            SELECT count(*)
            FROM permit_index
            WHERE coalesce(trim(target_raw), '') <> ''
            """,
        )
        popup_fields_crop_like = count(
            connection,
            """
            SELECT count(*)
            FROM popup_fields
            WHERE fold(field_name) LIKE '%kultura%'
               OR fold(field_name) LIKE '%noveny%'
               OR fold(field_value) LIKE '%alma%'
               OR fold(field_value) LIKE '%kukorica%'
               OR fold(field_value) LIKE '%szolo%'
               OR fold(field_value) LIKE '%szõlõ%'
            """,
        )
        popup_fields_target_like = count(
            connection,
            """
            SELECT count(*)
            FROM popup_fields
            WHERE fold(field_name) LIKE '%karosito%'
               OR fold(field_name) LIKE '%cel%'
               OR fold(field_value) LIKE '%gyom%'
               OR fold(field_value) LIKE '%lisztharmat%'
               OR fold(field_value) LIKE '%peronoszpora%'
               OR fold(field_value) LIKE '%kaboca%'
               OR fold(field_value) LIKE '%kabóca%'
            """,
        )

        rows = [
            {
                "metric": "product_records_without_usage",
                "value": product_records_without_usage,
                "detail": sample(
                    connection,
                    """
                    SELECT p.product_name, p.permit_number
                    FROM permit_index AS p
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM usage AS u
                        WHERE replace(replace(trim(u.permit_number), ' ', ''), '.', '/')
                            = replace(replace(trim(p.permit_number), ' ', ''), '.', '/')
                    )
                    ORDER BY fold(p.product_name)
                    LIMIT 5
                    """,
                ),
            },
            {
                "metric": "usage_missing_bbch",
                "value": usage_missing_bbch,
                "detail": "Missing bbch_min, bbch_max and treatment_time.",
            },
            {
                "metric": "usage_missing_phi",
                "value": usage_missing_phi,
                "detail": "Missing phi_days and phi_raw.",
            },
            {
                "metric": "usage_multiple_crops_in_one_cell",
                "value": usage_multiple_crops,
                "detail": "Comma, semicolon or 'es' detected in crop cell.",
            },
            {
                "metric": "usage_multiple_targets_in_one_cell",
                "value": usage_multiple_targets,
                "detail": "Comma, semicolon or 'es' detected in target cell.",
            },
            {
                "metric": "potentially_truncated_by_limit",
                "value": products_above_action_limit,
                "detail": "Product/permit pairs with more than 20 usage rows.",
            },
            {
                "metric": "popup_meta_crop_records",
                "value": popup_crop_records,
                "detail": "permit_index.crop_raw is populated.",
            },
            {
                "metric": "popup_meta_target_records",
                "value": popup_target_records,
                "detail": "permit_index.target_raw is populated.",
            },
            {
                "metric": "popup_fields_crop_like_records",
                "value": popup_fields_crop_like,
                "detail": "popup_fields contains crop-like field names or values.",
            },
            {
                "metric": "popup_fields_target_like_records",
                "value": popup_fields_target_like,
                "detail": "popup_fields contains target-like field names or values.",
            },
        ]
    write_report(rows)
    print(REPORT_PATH)


if __name__ == "__main__":
    main()
