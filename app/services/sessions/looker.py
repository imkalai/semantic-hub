"""Looker source: a placeholder for a future integration.

When Looker is available, this reads the explores a session selects through the Looker API and returns
their measures (metrics), dimensions and dimension groups (time dimensions with their timeframes), and joins
(with sql_on and relationship), all as certified items. Fields are tied to BigQuery tables through each
view's sql_table_name. Those items outrank declared and inferred ones in the assembler, so turning Looker on
needs no other change. Plans built only from one explore's fields can then also run through Looker
(row-level permissions, symmetric aggregates).

Until then it's off (SH_LOOKER_MODE=off) and contributes nothing.
"""

from app.config import get_settings
from app.services.sessions.sources import SourceItems


class LookerSource:
    name = "Looker"

    @property
    def enabled(self) -> bool:
        return get_settings().looker_mode != "off"

    def items(self, explores: list[str], in_scope: set[str]) -> SourceItems:
        if not self.enabled or not explores:
            return SourceItems()
        # TODO(looker): GET /lookml_models/{model}/explores/{explore} for each selected explore, then map
        # measures -> CatalogMetric, dimensions -> CatalogDimension, joins -> CatalogJoin (trust="certified",
        # source="looker"), keeping only views whose sql_table_name is in `in_scope`.
        raise NotImplementedError("The Looker integration isn't built yet.")

    def health(self) -> tuple[str, str]:
        if not self.enabled:
            return "ok", "Off: placeholder for a future integration (SH_LOOKER_MODE=off)."
        return "warn", "Looker is switched on, but the integration isn't built yet."
