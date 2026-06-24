"""``[local-llm]`` embedder — fully-offline semantic embeddings via sentence-transformers.

The offline default (``HashEmbedder``, lexical buckets) needs no deps and no model download,
but has no semantic generalization. This adds a real local embedding model behind the
``[local-llm]`` extra, import-guarded so core stays ``pydantic + numpy`` only (I9): the heavy
import happens at instantiation, never at module import. After swapping to it, run
``Memory.reembed()`` (CLI ``cold-frame reembed``) to re-index existing notes (I8/I10).

No API key, no network at query time, no data leaves the machine — on-brand for local-first.
"""

from __future__ import annotations

import numpy as np

from cold_frame.llm.base import Embedder, EmbedderMeta

_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"  # 384-dim, small + strong; downloaded once, then local


class SentenceTransformerEmbedder(Embedder):
    """A local sentence-transformers model as a coldframe ``Embedder``.

    ``embedder_id`` = ``"local:<model-name>"`` so its vectors are distinct from the hash default
    (I10 KNN filters on embedder_id); ``dim`` is read from the model (never hardcoded, I8).
    """

    def __init__(self, model: str = _DEFAULT_MODEL) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # the one place the optional dep is required
            raise ImportError(
                "SentenceTransformerEmbedder needs the '[local-llm]' extra — install it with: "
                "uv sync --extra local-llm  (or pip install 'cold-frame[local-llm]')"
            ) from exc
        self._model = SentenceTransformer(model)
        dim = int(self._model.get_sentence_embedding_dimension())
        self._meta = EmbedderMeta(embedder_id=f"local:{model.rsplit('/', 1)[-1]}", dim=dim)

    @property
    def meta(self) -> EmbedderMeta:
        return self._meta

    @property
    def is_local(self) -> bool:
        return True  # runs in-process, no network → admission-safe (I7)

    def embed(self, texts: list[str]) -> np.ndarray:
        # normalize_embeddings=True → L2-normalized, so the KNN cosine matmul is a plain dot.
        vecs = self._model.encode(
            texts, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
        )
        return np.ascontiguousarray(vecs, dtype=np.float32)
