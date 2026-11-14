"""Pre-trained sentence embeddings (no training, no LLM, CPU only).

One small open model does several jobs: clustering companies into segments, lookalike search,
and zero-shot tagging of customer comments against plain-English theme descriptions.
"""
from __future__ import annotations

import hashlib
import threading

import numpy as np

from ..config import get_settings

MODEL_NAME = "BAAI/bge-small-en-v1.5"
MODEL_CARD = {
    "name": MODEL_NAME,
    "source": "Hugging Face (Beijing Academy of Artificial Intelligence)",
    "license": "MIT",
    "dimensions": 384,
    "size_mb": 67,
    "runtime": "ONNX Runtime on CPU via fastembed (no GPU, no PyTorch)",
    "used_for": ["Customer segment discovery (market researcher)", "Lookalike lead search",
                 "Zero-shot tagging of churn reasons and NPS comments (business analyst)"],
}

_model = None
_lock = threading.Lock()
_cache: dict[str, np.ndarray] = {}
_CACHE_MAX = 200_000


def _get():
    global _model
    with _lock:
        if _model is None:
            import os

            from fastembed import TextEmbedding

            threads = int(os.environ.get("EMBED_THREADS", "0")) or None  # None = all cores the runtime sees
            _model = TextEmbedding(MODEL_NAME, cache_dir=get_settings().model_cache or None, threads=threads)
        return _model


def _db_get(keys: list[str]) -> dict[str, np.ndarray]:
    """Vectors computed before (by any process, before any restart). Best effort: no DB, no cache."""
    try:
        from sqlalchemy import text

        from ..db import engine

        out = {}
        with engine.begin() as c:
            for i in range(0, len(keys), 5000):
                for h, v in c.execute(text("SELECT text_hash, vec FROM embedding_cache WHERE model = :m AND "
                                           "text_hash = ANY(:k)"), {"m": MODEL_NAME, "k": keys[i:i + 5000]}):
                    out[h] = np.frombuffer(bytes(v), dtype=np.float32)
        return out
    except Exception:
        return {}


def _db_put(items: list[tuple[str, np.ndarray]]) -> None:
    try:
        from sqlalchemy import text

        from ..db import engine

        with engine.begin() as c:
            c.execute(text("""INSERT INTO embedding_cache (text_hash, model, vec) VALUES (:h, :m, :v)
                              ON CONFLICT (text_hash) DO NOTHING"""),
                      [{"h": k, "m": MODEL_NAME, "v": v.astype(np.float32).tobytes()} for k, v in items])
    except Exception as e:  # the cache is an optimisation; never fail an analysis over it
        import logging

        logging.getLogger("fred.embeddings").warning("embedding cache write skipped: %s", e)


def embed(texts: list[str]) -> np.ndarray:
    """Unit-normalised embeddings. Cached by text hash in memory and in Postgres, so a text is embedded once."""
    keys = [hashlib.sha1(t.encode()).hexdigest() for t in texts]
    missing = {k: t for k, t in zip(keys, texts) if k not in _cache}
    if missing:
        for k, v in _db_get(list(missing)).items():
            _cache[k] = v
            missing.pop(k, None)
    if missing:
        todo = sorted(missing.items())
        vecs = np.array(list(_get().embed([t for _, t in todo], batch_size=128)), dtype=np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        if len(_cache) + len(todo) > _CACHE_MAX:
            _cache.clear()
        for (k, _), v in zip(todo, vecs):
            _cache[k] = v
        _db_put([(k, v) for (k, _), v in zip(todo, vecs)])
    return np.stack([_cache[k] for k in keys]) if keys else np.zeros((0, MODEL_CARD["dimensions"]), np.float32)


def available() -> bool:
    try:
        _get()
        return True
    except Exception:
        return False
