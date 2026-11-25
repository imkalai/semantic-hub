"""Built-in sample tables so the app can be used and tested without Google Cloud access.

Switch to real BigQuery with SH_METADATA_MODE=live.
"""

from app.schemas.meta import ColumnMeta, DatasetInfo, KeyConstraint, ProjectInfo, TableInfo, TableMeta
from app.services.metadata.base import MetadataError, MetadataProvider
from app.services.metadata.pii import mark_pii
from app.services.text import humanize

GB = 1024**3


def _c(name: str, type_: str, description: str = "", samples: list[str] | None = None, tags: list[str] | None = None) -> ColumnMeta:
    return ColumnMeta(name=name, type=type_, description=description, sample_values=samples or [], policy_tags=tags or [])


PII_TAG = "projects/acme-gov/locations/eu/taxonomies/1/policyTags/personal_data"

_PROJECTS = {
    "acme-analytics": "Acme Analytics",
    "acme-ops": "Acme Operations",
    "acme-mkt": "Acme Marketing",
}

_DATASETS = {
    "acme-analytics": [
        DatasetInfo(id="sales", name="Sales data", description="Orders, customers and products", location="EU"),
        DatasetInfo(id="crm", name="CRM data", description="Customer accounts and churn", location="EU"),
    ],
    "acme-ops": [DatasetInfo(id="warehouse", name="Warehouse data", description="Stock and warehouses", location="EU")],
    "acme-mkt": [DatasetInfo(id="web_events", name="Web events", description="Website sessions and conversions", location="EU")],
}

# (description, rows, size_bytes, partition_field, history_months, columns)
_TABLES: dict[tuple[str, str], dict[str, tuple]] = {
    ("acme-analytics", "sales"): {
        "orders": ("One row per customer order, with amount and status", 2_400_000, int(1.2 * 1024 * GB), "created_at", 60, [
            _c("order_id", "STRING", "Unique order reference"),
            _c("customer_id", "STRING", "Customer who placed the order"),
            _c("status", "STRING", "Order status", ["completed", "cancelled", "refunded", "pending"]),
            _c("channel", "STRING", "Where the order was placed", ["web", "app", "store", "phone"]),
            _c("amount", "NUMERIC"),
            _c("currency", "STRING", "ISO currency code", ["GBP", "EUR"]),
            _c("created_at", "TIMESTAMP", "When the order was placed"),
        ]),
        "order_items": ("Products included in each order", 9_100_000, int(310 * GB), None, None, [
            _c("order_item_id", "STRING", "Unique line reference"),
            _c("order_id", "STRING", "Order this line belongs to"),
            _c("product_id", "STRING", "Product sold"),
            _c("quantity", "INT64", "Units sold"),
            _c("unit_price", "NUMERIC", "Price per unit"),
            _c("line_amount", "NUMERIC", "Quantity × unit price"),
        ]),
        "customers": ("Customer details, including region and signup date", 310_000, int(0.4 * GB), None, None, [
            _c("customer_id", "STRING", "Unique customer reference"),
            _c("first_name", "STRING", "Customer first name", tags=[PII_TAG]),
            _c("email", "STRING", "Customer email address", tags=[PII_TAG]),
            _c("date_of_birth", "DATE", "Customer date of birth", tags=[PII_TAG]),
            _c("region", "STRING", "Sales region", ["North", "South", "East", "West", "Scotland", "Wales"]),
            _c("segment", "STRING", "Customer segment", ["Retail", "Business", "Premier"]),
            _c("signup_ts", "TIMESTAMP", "When the customer signed up"),
        ]),
        "products": ("Product catalogue with category and price", 8_200, int(0.01 * GB), None, None, [
            _c("product_id", "STRING", "Unique product reference"),
            _c("product_name", "STRING", "Product name"),
            _c("category", "STRING", "Product category", ["Savings", "Mortgages", "Cards", "Insurance"]),
            _c("list_price", "NUMERIC", "Standard price"),
        ]),
        "regions": ("Region names and sales territories", 42, 4_096, None, None, [
            _c("region", "STRING", "Region name"),
            _c("territory", "STRING", "Sales territory"),
            _c("regional_manager", "STRING", "Manager responsible"),
        ]),
        "returns": ("Returned orders and refund reasons", 61_000, int(0.2 * GB), "returned_at", 36, [
            _c("return_id", "STRING", "Unique return reference"),
            _c("order_id", "STRING", "Order that was returned"),
            _c("reason", "STRING", "Why the order was returned", ["damaged", "not as described", "changed mind"]),
            _c("refund_amount", "NUMERIC", "Amount refunded"),
            _c("returned_at", "TIMESTAMP", "When the return was logged"),
        ]),
    },
    ("acme-analytics", "crm"): {
        "accounts": ("Customer accounts and their current state", 520_000, int(1.1 * GB), None, None, [
            _c("account_id", "STRING", "Unique account reference"),
            _c("customer_name", "STRING", "Account holder name", tags=[PII_TAG]),
            _c("plan", "STRING", "Product plan", ["Basic", "Plus", "Premier"]),
            _c("region", "STRING", "Sales region"),
            _c("is_active", "BOOL", "Whether the account is active"),
            _c("monthly_fee", "NUMERIC", "Monthly fee charged"),
            _c("opened_date", "DATE", "When the account was opened"),
        ]),
        "churn_events": ("One row each time an account closes", 48_000, int(0.1 * GB), "churned_date", 36, [
            _c("churn_event_id", "STRING", "Unique event reference"),
            _c("account_id", "STRING", "Account that closed"),
            _c("churn_reason", "STRING", "Reason given", ["price", "service", "moved", "other"]),
            _c("churned_date", "DATE", "When the account closed"),
        ]),
    },
    ("acme-ops", "warehouse"): {
        "stock_levels": ("Daily stock count per product and warehouse", 14_000_000, int(95 * GB), "snapshot_date", 24, [
            _c("stock_level_id", "STRING"),
            _c("warehouse_id", "STRING", "Warehouse"),
            _c("product_id", "STRING", "Product"),
            _c("units_on_hand", "INT64", "Units in stock"),
            _c("snapshot_date", "DATE", "Date of the count"),
        ]),
        "warehouses": ("Warehouse locations", 12, 4_096, None, None, [
            _c("warehouse_id", "STRING", "Unique warehouse reference"),
            _c("warehouse_name", "STRING", "Warehouse name"),
            _c("city", "STRING", "City"),
        ]),
    },
    ("acme-mkt", "web_events"): {
        "sessions": ("One row per website visit", 52_000_000, int(640 * GB), "session_start", 24, [
            _c("session_id", "STRING", "Unique visit reference"),
            _c("traffic_source", "STRING", "Where the visitor came from", ["search", "social", "email", "direct"]),
            _c("device", "STRING", "Device type", ["desktop", "mobile", "tablet"]),
            _c("pages_viewed", "INT64", "Pages viewed in the visit"),
            _c("converted", "BOOL", "Whether the visit led to an application"),
            _c("session_start", "TIMESTAMP", "When the visit started"),
        ]),
    },
}


def _meta(project: str, dataset: str, name: str) -> TableMeta:
    desc, rows, size, part, months, cols = _TABLES[(project, dataset)][name]
    return TableMeta(
        name=name, project=project, dataset=dataset, description=desc, row_count=rows, size_bytes=size,
        partition_field=part, partition_type="DAY" if part else None, history_months=months,
        columns=mark_pii([c.model_copy() for c in cols]),
    )


class DemoMetadata(MetadataProvider):
    def list_projects(self) -> list[ProjectInfo]:
        return [ProjectInfo(id=k, name=v) for k, v in _PROJECTS.items()]

    def list_datasets(self, project: str) -> list[DatasetInfo]:
        if project not in _DATASETS:
            raise MetadataError(f"Project '{project}' wasn't found, or you don't have access to it.")
        return [d.model_copy(update={"table_count": len(_TABLES[(project, d.id)])}) for d in _DATASETS[project]]

    def list_tables(self, project: str, dataset: str) -> list[TableInfo]:
        tables = _TABLES.get((project, dataset))
        if tables is None:
            raise MetadataError(f"Dataset '{project}.{dataset}' wasn't found, or you don't have access to it.")
        return [
            TableInfo(name=n, label=humanize(n), description=t[0], row_count=t[1], size_bytes=t[2])
            for n, t in tables.items()
        ]

    def get_tables(self, project: str, dataset: str, names: list[str]) -> list[TableMeta]:
        tables = _TABLES.get((project, dataset), {})
        missing = [n for n in names if n not in tables]
        if missing:
            raise MetadataError(f"These tables weren't found in {project}.{dataset}: {', '.join(missing)}")
        return [_meta(project, dataset, n) for n in names]

    def dry_run_bytes(self, project: str, sql: str) -> int | None:
        return None  # demo mode estimates from table sizes instead

    def get_constraints(self, project: str, dataset: str) -> list[KeyConstraint]:
        if (project, dataset) not in _TABLES:
            raise MetadataError(f"Dataset '{project}.{dataset}' wasn't found, or you don't have access to it.")
        return [c for c in _CONSTRAINTS if c.table.startswith(f"{project}.{dataset}.")]


# Keys declared on a few demo tables, as a data team might declare them in BigQuery. The rest are inferred.
_S = "acme-analytics.sales"
_CONSTRAINTS = [
    KeyConstraint(kind="primary", table=f"{_S}.orders", columns=["order_id"]),
    KeyConstraint(kind="primary", table=f"{_S}.customers", columns=["customer_id"]),
    KeyConstraint(kind="primary", table=f"{_S}.products", columns=["product_id"]),
    KeyConstraint(kind="foreign", table=f"{_S}.orders", columns=["customer_id"],
                  ref_table=f"{_S}.customers", ref_columns=["customer_id"]),
]
