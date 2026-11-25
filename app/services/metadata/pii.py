"""Detect columns holding personal data: BigQuery policy tags first, then common column names."""

import re

from app.schemas.meta import ColumnMeta

_PII_NAME = re.compile(
    r"(^|_)(email|e_mail|phone|mobile|telephone|date_of_birth|dob|birth_date|ssn|national_insurance|ni_number|"
    r"passport|sort_code|account_number|iban|card_number|pan|address|postcode|post_code|zip_code|"
    r"first_name|last_name|full_name|surname|forename|ip_address)($|_)"
)


def is_pii(column: ColumnMeta) -> bool:
    return bool(column.policy_tags) or bool(_PII_NAME.search(column.name.lower()))


def mark_pii(columns: list[ColumnMeta]) -> list[ColumnMeta]:
    for c in columns:
        c.is_pii = is_pii(c)
    return columns
