"""Optional visual-embedding backend (CLIP-style).

This is an *opt-in* enhancement (Settings -> "Detección avanzada mediante IA").
It compares a learned visual representation of each image rather than its
pixels, so it can link the same scene / composition even across crops,
re-framing or shots taken seconds apart.

It is deliberately isolated:
  * the rest of the app works with ``available()`` returning False
  * heavy imports happen lazily, only when a model is actually needed
  * embeddings are cached (hex-packed float16) in the analysis DB

Install a backend with:  pip install "pixmatch[ai]"
(open-clip-torch, or onnxruntime + a CLIP ONNX model).
"""

from __future__ import annotations

import math
import struct

from app.utils.logging_setup import get_logger

log = get_logger(__name__)

_backend = None  # cached backend object, or the sentinel False
_MODEL_NAME = "ViT-B-32"


def _load_backend():
    global _backend
    if _backend is not None:
        return _backend or None
    try:
        import open_clip  # type: ignore
        import torch  # type: ignore

        model, _, preprocess = open_clip.create_model_and_transforms(
            _MODEL_NAME, pretrained="laion2b_s34b_b79k"
        )
        model.eval()
        _backend = _OpenClipBackend(model, preprocess, torch)
        log.info("Visual-embedding backend: open_clip %s", _MODEL_NAME)
    except Exception as exc:
        log.info("No visual-embedding backend available: %s", exc)
        _backend = False
    return _backend or None


class _OpenClipBackend:
    def __init__(self, model, preprocess, torch) -> None:
        self._model = model
        self._pre = preprocess
        self._torch = torch

    def embed(self, path: str) -> list[float] | None:
        from PIL import Image

        try:
            with Image.open(path) as img:
                tensor = self._pre(img.convert("RGB")).unsqueeze(0)
            with self._torch.no_grad():
                vec = self._model.encode_image(tensor)[0]
                vec = vec / vec.norm()
            return [float(x) for x in vec.tolist()]
        except Exception as exc:
            log.debug("embed failed for %s: %s", path, exc)
            return None


# ---------------------------------------------------------------------------
def available() -> bool:
    return _load_backend() is not None


def backend_status() -> str:
    if available():
        return f"Backend de IA activo: {_MODEL_NAME}. Se usará para casos dudosos."
    return (
        "Activada, pero no hay backend instalado. Instala el extra:\n"
        'pip install "pixmatch[ai]"  (open-clip-torch)'
    )


def embed(path: str) -> list[float] | None:
    backend = _load_backend()
    return backend.embed(path) if backend else None


def cosine(a: list[float] | None, b: list[float] | None) -> float | None:
    if not a or not b or len(a) != len(b):
        return None
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na < 1e-9 or nb < 1e-9:
        return None
    return max(0.0, min(1.0, dot / (na * nb)))


def similarity_percent(a, b) -> float | None:
    c = cosine(a, b)
    return None if c is None else round(100.0 * c, 1)


# -- compact storage (float16 hex) ------------------------------------
def encode(vec: list[float] | None) -> str | None:
    if not vec:
        return None
    return struct.pack(f"<{len(vec)}e", *vec).hex()


def decode(blob: str | None) -> list[float] | None:
    if not blob:
        return None
    try:
        raw = bytes.fromhex(blob)
        return list(struct.unpack(f"<{len(raw) // 2}e", raw))
    except (ValueError, struct.error):
        return None
