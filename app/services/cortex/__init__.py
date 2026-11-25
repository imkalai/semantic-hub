from app.config import get_settings
from app.services.cortex.base import CortexClient, CortexError


def get_cortex() -> CortexClient:
    settings = get_settings()
    if settings.cortex_mode == "real":
        from app.services.cortex.real import RealCortex

        return RealCortex(settings)
    from app.services.cortex.mock import MockCortex

    return MockCortex(settings.embedding_dim)


__all__ = ["CortexClient", "CortexError", "get_cortex"]
