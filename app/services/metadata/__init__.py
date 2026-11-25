from app.config import get_settings
from app.schemas.meta import DatasetInfo, ProjectInfo, TableInfo, TableMeta
from app.services.metadata.base import MetadataError, MetadataProvider


class _AllowedProjectsOnly(MetadataProvider):
    """Limits every call to SH_BQ_ALLOWED_PROJECTS. The app reads BigQuery with its own credentials,
    so this is what stops users browsing projects they shouldn't."""

    def __init__(self, inner: MetadataProvider, allowed: set[str]):
        self.inner, self.allowed = inner, allowed

    def _check(self, project: str) -> None:
        if project not in self.allowed:
            raise MetadataError(f"Project '{project}' isn't available in Semantic Hub. Ask an administrator to add it.")

    def list_projects(self) -> list[ProjectInfo]:
        return [p for p in self.inner.list_projects() if p.id in self.allowed]

    def list_datasets(self, project: str) -> list[DatasetInfo]:
        self._check(project)
        return self.inner.list_datasets(project)

    def list_tables(self, project: str, dataset: str) -> list[TableInfo]:
        self._check(project)
        return self.inner.list_tables(project, dataset)

    def get_tables(self, project: str, dataset: str, names: list[str]) -> list[TableMeta]:
        self._check(project)
        return self.inner.get_tables(project, dataset, names)

    def dry_run_bytes(self, project: str, sql: str) -> int | None:
        return self.inner.dry_run_bytes(project, sql)

    def get_constraints(self, project: str, dataset: str):
        self._check(project)
        return self.inner.get_constraints(project, dataset)


def get_metadata_provider() -> MetadataProvider:
    settings = get_settings()
    if settings.metadata_mode == "live":
        from app.services.metadata.live import LiveBigQuery

        provider: MetadataProvider = LiveBigQuery()
    else:
        from app.services.metadata.demo import DemoMetadata

        provider = DemoMetadata()
    allowed = settings.allowed_projects
    return _AllowedProjectsOnly(provider, allowed) if allowed else provider


__all__ = ["MetadataError", "MetadataProvider", "get_metadata_provider"]
