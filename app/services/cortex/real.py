"""Adapter for the corporate Cortex LLM endpoint.

TODO: plug in the corporate Cortex client here (same client as Looker-Helper). Two methods are needed:
`chat()` receives OpenAI-style messages and returns the reply text; `embed()` returns one vector per text.
Settings: SH_CORTEX_ENDPOINT, SH_CORTEX_API_KEY, SH_CORTEX_CHAT_MODEL, SH_CORTEX_EMBED_MODEL, SH_CORTEX_TIMEOUT.
"""

from app.config import Settings
from app.services.cortex.base import CortexClient, CortexError

_NOT_CONNECTED = ("The Cortex client hasn't been connected yet. Set SH_CORTEX_MODE=mock until it's added "
                  "in app/services/cortex/real.py.")


class RealCortex(CortexClient):
    name = "cortex"

    def __init__(self, settings: Settings):
        self.endpoint = settings.cortex_endpoint
        self.api_key = settings.cortex_api_key
        self.chat_model = settings.cortex_chat_model
        self.embed_model = settings.cortex_embed_model
        self.timeout = settings.cortex_timeout

    def _check(self) -> None:
        if not self.endpoint:
            raise CortexError("Cortex isn't set up yet. Set SH_CORTEX_ENDPOINT (and SH_CORTEX_API_KEY) in the .env file.")

    def chat(self, messages: list[dict], *, temperature: float = 0.0, max_tokens: int = 4000) -> str:
        self._check()
        # Replace with the corporate client call, e.g.:
        #   return client.chat(model=self.chat_model, messages=messages, temperature=temperature, max_tokens=max_tokens).text
        raise CortexError(_NOT_CONNECTED)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._check()
        #   return client.embeddings(model=self.embed_model, input=texts).vectors
        raise CortexError(_NOT_CONNECTED)
