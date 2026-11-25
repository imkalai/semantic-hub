"""Stand-in for Cortex during development: no network, deterministic answers.

Embeddings are hashed bags of words and word pieces, so texts sharing words (or word stems) land close
together. That's enough to exercise the Chroma index end to end; real Cortex embeddings replace it.
"""

import hashlib
import json
import math
import re

from app.services.cortex.base import CortexClient, CortexError
from app.services.cortex.mock_planner import plan_from_context

_TASK = re.compile(r"TASK:\s*(\w+)")
_CONTEXT = re.compile(r"<context>(.*?)</context>", re.S)
_WORD = re.compile(r"[a-z0-9]+")
# Filler words carry no meaning in a bag of words; leaving them in makes unrelated texts look alike.
_FILLER = {"a", "an", "the", "of", "by", "per", "for", "in", "on", "and", "or", "to", "is", "are", "was", "were",
           "be", "it", "its", "as", "at", "with", "from", "each", "every", "this", "that", "what", "how", "which",
           "me", "show", "also", "called", "not", "into"}


def _bucket(token: str, dim: int) -> tuple[int, float]:
    h = hashlib.blake2b(token.encode(), digest_size=8).digest()
    n = int.from_bytes(h, "big")
    return n % dim, 1.0 if (n >> 63) & 1 else -1.0


def hashed_embedding(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    for word in _WORD.findall(text.lower()):
        if word in _FILLER:
            continue
        tokens = [word] + [word[i:i + 4] for i in range(max(1, len(word) - 3))]  # word + 4-letter pieces
        for i, tok in enumerate(tokens):
            idx, sign = _bucket(tok, dim)
            vec[idx] += sign * (1.0 if i == 0 else 0.5)
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class MockCortex(CortexClient):
    name = "mock"

    def __init__(self, dim: int = 256):
        self.dim = dim

    def chat(self, messages: list[dict], *, temperature: float = 0.0, max_tokens: int = 4000) -> str:
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        task = (_TASK.search(system) or [None, ""])[1]
        if task == "ping":
            return "OK"
        if task == "plan":
            user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            match = _CONTEXT.search(user)
            if not match:
                raise CortexError("The request was missing its context.")
            return json.dumps(plan_from_context(json.loads(match.group(1))))
        raise CortexError(f"The development stand-in for Cortex doesn't handle '{task or 'this'}' requests yet.")

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [hashed_embedding(t, self.dim) for t in texts]

    def health(self) -> tuple[bool, str]:
        return True, "Using the built-in stand-in for Cortex (development mode)"
