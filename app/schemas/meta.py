from pydantic import BaseModel, Field

NUMERIC_TYPES = {"INT64", "INTEGER", "NUMERIC", "BIGNUMERIC", "FLOAT64", "FLOAT", "DECIMAL", "BIGDECIMAL"}
TIME_TYPES = {"DATE", "DATETIME", "TIMESTAMP"}
STRING_TYPES = {"STRING"}
BOOL_TYPES = {"BOOL", "BOOLEAN"}


class ColumnMeta(BaseModel):
    name: str
    type: str
    mode: str = "NULLABLE"
    description: str = ""
    policy_tags: list[str] = Field(default_factory=list)
    is_pii: bool = False
    sample_values: list[str] = Field(default_factory=list)

    @property
    def is_numeric(self) -> bool:
        return self.type.upper() in NUMERIC_TYPES

    @property
    def is_time(self) -> bool:
        return self.type.upper() in TIME_TYPES

    @property
    def is_string(self) -> bool:
        return self.type.upper() in STRING_TYPES

    @property
    def is_bool(self) -> bool:
        return self.type.upper() in BOOL_TYPES

    @property
    def is_supported(self) -> bool:
        return self.mode.upper() != "REPEATED" and self.type.upper() not in {"RECORD", "STRUCT"}


class TableMeta(BaseModel):
    name: str
    project: str
    dataset: str
    description: str = ""
    row_count: int | None = None
    size_bytes: int | None = None
    partition_field: str | None = None
    partition_type: str | None = None
    clustering: list[str] = Field(default_factory=list)
    history_months: int | None = None  # demo data only: how many months the table covers
    columns: list[ColumnMeta] = Field(default_factory=list)

    def column(self, name: str) -> ColumnMeta | None:
        name = name.lower()
        return next((c for c in self.columns if c.name.lower() == name), None)

    @property
    def full_name(self) -> str:
        return f"{self.project}.{self.dataset}.{self.name}"


class ProjectInfo(BaseModel):
    id: str
    name: str


class DatasetInfo(BaseModel):
    id: str
    name: str
    description: str = ""
    location: str = ""
    table_count: int | None = None


class TableInfo(BaseModel):
    name: str
    label: str
    description: str = ""
    row_count: int | None = None
    size_bytes: int | None = None
