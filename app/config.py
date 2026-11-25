"""Settings, read from environment variables (prefix SH_) or a .env file next to the app."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
APP_NAME = "Semantic Hub"
APP_VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_prefix="SH_", extra="ignore")

    # Everything the app writes (SQLite database, Chroma index) lives here, on the user's laptop.
    data_dir: Path = BASE_DIR / "data"
    # Source of truth for the ontology: seeds the graph store and is the fallback when the graph is unusable.
    catalog_path: Path = BASE_DIR / "catalog" / "semantic_catalog.json"

    # The app is for one person on their own laptop and has no sign-in, so it only listens on loopback.
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = True

    metadata_mode: Literal["demo", "live"] = "demo"
    gcp_billing_project: str | None = None
    bq_allowed_projects: str = ""

    cortex_mode: Literal["mock", "real"] = "mock"
    cortex_endpoint: str = ""
    cortex_api_key: str = ""
    cortex_chat_model: str = "default"
    cortex_embed_model: str = "default"
    cortex_timeout: int = 60
    embedding_dim: int = 256  # mock embeddings only; real Cortex decides its own size
    # Vector matches below this cosine similarity are ignored. Tune it once real Cortex embeddings are in use.
    retrieval_min_similarity: float = 0.25
    # Build the metadata index on startup when it's empty
    auto_index: bool = True

    # Query planning
    default_months: int = 12  # period used when a question asks for a trend but names no period
    default_limit: int = 500
    max_rows: int = 5000
    plan_attempts: int = 2  # first try + one retry with the reasons the plan was rejected

    # Sessions
    max_session_tables: int = 60

    # Looker: placeholder for a future integration. "off" until Looker is available.
    looker_mode: Literal["off"] = "off"
    looker_base_url: str = ""
    looker_client_id: str = ""
    looker_client_secret: str = ""

    # Guardrails
    allow_pii: bool = False  # group by or filter on personal-data dimensions
    large_table_gb: float = 50.0  # tables this big must be limited to a period (partition pruning)
    max_group_by: int = 4  # more dimensions than this gets a warning

    # Seed the graph from the catalog on first start, so a fresh install works without running the CLI.
    auto_bootstrap: bool = True
    enable_api_docs: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "semantic_hub.db"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def allowed_projects(self) -> set[str]:
        return {p.strip() for p in self.bq_allowed_projects.split(",") if p.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
