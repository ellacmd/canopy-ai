"""Bounded server-only embedding adapter; independent of the chat model."""

import math
from typing import Literal, Protocol

from google import genai
from google.genai import types

from canopy.config import Settings

# Changing this space requires a migration, reindex, and retrieval evaluation.
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768
EMBEDDING_SPACE = "gemini-embedding-001:768:unit-v1"


class EmbeddingUnavailable(RuntimeError):
    pass


class Embedder(Protocol):
    space: str

    def embed(
        self, texts: list[str], *, task: Literal["document", "query"]
    ) -> list[list[float]]: ...


def unit_vector(values) -> list[float]:
    """Reject invalid provider data and normalize reduced-dimensional embeddings."""
    try:
        vector = [float(value) for value in values]
        if len(vector) != EMBEDDING_DIMENSIONS or not all(map(math.isfinite, vector)):
            raise ValueError
        norm = math.sqrt(sum(value * value for value in vector))
        if not math.isfinite(norm) or norm <= 0:
            raise ValueError
        return [value / norm for value in vector]
    except (TypeError, ValueError, OverflowError):
        raise EmbeddingUnavailable("Embedding provider returned an invalid vector") from None


class GeminiEmbedder:
    space = EMBEDDING_SPACE

    def __init__(self, settings: Settings, *, client=None):
        if client is None and not settings.gemini_api_key.get_secret_value():
            raise EmbeddingUnavailable("Set GEMINI_API_KEY before using policy embeddings")
        self.client = client or genai.Client(
            api_key=settings.gemini_api_key.get_secret_value(),
            http_options=types.HttpOptions(
                timeout=int(settings.gemini_timeout_seconds * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    def embed(self, texts: list[str], *, task: Literal["document", "query"]) -> list[list[float]]:
        if task not in {"document", "query"}:
            raise ValueError("Unsupported embedding task")
        if not 1 <= len(texts) <= 16 or (task == "query" and len(texts) != 1):
            raise ValueError("Embedding batch must contain 1–16 documents or one query")
        # UTF-8 bytes conservatively bound token use even for non-ASCII input.
        if any(not text.strip() or len(text.encode("utf-8")) > 2000 for text in texts):
            raise ValueError("Embedding input must contain 1–2000 UTF-8 bytes")
        try:
            result = self.client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT" if task == "document" else "RETRIEVAL_QUERY",
                    output_dimensionality=EMBEDDING_DIMENSIONS,
                ),
            )
            embeddings = result.embeddings or []
            if len(embeddings) != len(texts):
                raise EmbeddingUnavailable("Embedding provider returned an incomplete batch")
            return [unit_vector(item.values) for item in embeddings]
        except EmbeddingUnavailable:
            raise
        except Exception:
            raise EmbeddingUnavailable(
                "Embedding request failed; check model access, API key, quota, and connectivity"
            ) from None

    def close(self) -> None:
        self.client.close()
