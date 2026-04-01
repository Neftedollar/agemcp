"""Optional embedding support for semantic search via fastembed + pgvector."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_embedder = None
_init_attempted = False


class Embedder:
    """Thin wrapper around fastembed TextEmbedding (384-dim BGE model)."""

    DIMENSIONS = 384

    def __init__(self):
        from fastembed import TextEmbedding
        self.model = TextEmbedding("BAAI/bge-small-en-v1.5")

    def embed(self, text: str) -> list[float]:
        return list(self.model.embed([text]))[0].tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [e.tolist() for e in self.model.embed(texts)]


def get_embedder() -> Optional[Embedder]:
    """Lazy-init the embedder. Returns None if fastembed is not installed."""
    global _embedder, _init_attempted
    if _init_attempted:
        return _embedder
    _init_attempted = True
    try:
        _embedder = Embedder()
        logger.info("Embedder initialized (fastembed BAAI/bge-small-en-v1.5, 384 dims)")
    except ImportError:
        logger.info("fastembed not installed — vector search disabled. Install with: pip install 'agemcp[vector]'")
    except Exception as e:
        logger.warning(f"Failed to initialize embedder: {e}")
    return _embedder
