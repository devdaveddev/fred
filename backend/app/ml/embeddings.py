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
            from fastembed import TextEmbedding

            _model = TextEmbedding(MODEL_NAME, cache_dir=get_settings().model_cache or None)
        return _model


def embed(texts: list[str]) -> np.ndarray:
    """Unit-normalised embeddings, cached by text so repeated analyses cost nothing."""
    keys = [hashlib.sha1(t.encode()).hexdigest() for t in texts]
    missing = sorted({k: t for k, t in zip(keys, texts) if k not in _cache}.items())
    if missing:
        vecs = np.array(list(_get().embed([t for _, t in missing], batch_size=128)), dtype=np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        if len(_cache) + len(missing) > _CACHE_MAX:
            _cache.clear()
        for (k, _), v in zip(missing, vecs):
            _cache[k] = v
    return np.stack([_cache[k] for k in keys]) if keys else np.zeros((0, MODEL_CARD["dimensions"]), np.float32)


def available() -> bool:
    try:
        _get()
        return True
    except Exception:
        return False
