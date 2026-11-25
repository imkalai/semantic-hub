from abc import ABC, abstractmethod


class CortexError(Exception):
    """Cortex couldn't answer. The message can be shown to the user."""


class CortexClient(ABC):
    """Access to the corporate LLM endpoint: chat for planning, embeddings for the metadata index.
    Chat messages are dicts with "role" (system | user | assistant) and "content"."""

    name: str = "cortex"

    @abstractmethod
    def chat(self, messages: list[dict], *, temperature: float = 0.0, max_tokens: int = 4000) -> str: ...

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per text, all the same length."""

    def health(self) -> tuple[bool, str]:
        try:
            self.chat([{"role": "system", "content": "TASK: ping"}, {"role": "user", "content": "Reply with OK."}], max_tokens=5)
            vectors = self.embed(["ping"])
            return True, f"Connected (embeddings: {len(vectors[0])} dimensions)"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
