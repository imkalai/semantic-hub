from abc import ABC, abstractmethod

from app.schemas.meta import DatasetInfo, ProjectInfo, TableInfo, TableMeta


class MetadataError(Exception):
    """A problem reading BigQuery, with a message that can be shown to the user."""


class MetadataProvider(ABC):
    @abstractmethod
    def list_projects(self) -> list[ProjectInfo]: ...

    @abstractmethod
    def list_datasets(self, project: str) -> list[DatasetInfo]: ...

    @abstractmethod
    def list_tables(self, project: str, dataset: str) -> list[TableInfo]: ...

    @abstractmethod
    def get_tables(self, project: str, dataset: str, names: list[str]) -> list[TableMeta]: ...

    @abstractmethod
    def dry_run_bytes(self, project: str, sql: str) -> int | None:
        """Bytes BigQuery would read for this query, or None if it can't be estimated."""
