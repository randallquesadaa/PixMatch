"""Read-only metadata extraction for images.

Uses Pillow. HEIC/HEIF and AVIF are supported automatically when the optional
plugins (``pillow-heif`` / ``pillow-avif-plugin``) are installed.

Nothing here ever writes to a file.
"""

from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass

from PIL import ExifTags, Image

from app.utils.file_utils import extended_path

# Register optional format plugins if available (safe no-ops otherwise).
try:  # pragma: no cover - depends on the environment
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
except Exception:
    # optional dependency - HEIC/HEIF just stays unsupported if it is missing
    # or fails to register; never a reason to stop the app.
    pass

try:  # pragma: no cover
    import pillow_avif  # type: ignore  # noqa: F401
except Exception:
    # optional dependency - AVIF stays unsupported if the plugin is absent.
    pass

# Raise the default 89 MP limit (many phone panoramas exceed it) but keep a
# finite ceiling so a corrupt or hostile file cannot exhaust memory when we
# decode it for a pixel digest. ~300 MP -> ~1.2 GB RGBA worst case; anything
# above raises DecompressionBombError, which we treat as "not processable".
Image.MAX_IMAGE_PIXELS = 300_000_000

_ORIENTATION_TAG = 0x0112  # EXIF "Orientation"
_EXIF_NAME = {v: k for k, v in ExifTags.TAGS.items()}


@dataclass(slots=True)
class ImageInfo:
    width: int = 0
    height: int = 0
    format: str = ""
    mode: str = ""
    orientation: int = 1  # raw EXIF orientation (1 == normal)
    has_exif: bool = False
    camera_make: str = ""
    camera_model: str = ""
    date_taken: str | None = None
    error: str | None = None

    @property
    def oriented_size(self) -> tuple[int, int]:
        """(width, height) after applying EXIF orientation."""
        if self.orientation in (5, 6, 7, 8):
            return self.height, self.width
        return self.width, self.height

    @property
    def megapixels(self) -> float:
        return (self.width * self.height) / 1_000_000


def _decode(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip("\x00 ").strip()
    return str(value).strip()


def read_image_info(path: str | os.PathLike[str]) -> ImageInfo:
    """Best-effort metadata. On any failure returns an ImageInfo with ``error`` set."""
    try:
        with Image.open(extended_path(path)) as img:
            info = ImageInfo(
                width=img.width,
                height=img.height,
                format=(img.format or "").upper(),
                mode=img.mode,
            )
            try:
                exif = img.getexif()
            except Exception:
                exif = None

            if exif:
                info.has_exif = len(exif) > 0
                info.orientation = int(exif.get(_ORIENTATION_TAG, 1) or 1)
                make = exif.get(_EXIF_NAME.get("Make", -1))
                model = exif.get(_EXIF_NAME.get("Model", -1))
                dt = exif.get(_EXIF_NAME.get("DateTimeOriginal", -1)) or exif.get(
                    _EXIF_NAME.get("DateTime", -1)
                )
                if make:
                    info.camera_make = _decode(make)
                if model:
                    info.camera_model = _decode(model)
                if dt:
                    info.date_taken = _decode(dt)
            return info
    except FileNotFoundError:
        return ImageInfo(error="El archivo ya no existe.")
    except PermissionError:
        return ImageInfo(error="Permisos insuficientes para leer el archivo.")
    except Image.UnidentifiedImageError:
        return ImageInfo(error="Formato no reconocido o imagen no procesable.")
    except Exception as exc:
        return ImageInfo(error=f"Imagen no procesable: {exc}")


def file_timestamps(path: str | os.PathLike[str]) -> tuple[str | None, str]:
    """Return (creation_iso_or_None, modified_iso). Creation time is not
    available on every platform/filesystem."""
    try:
        st = os.stat(extended_path(path))
    except OSError:
        return None, ""
    modified = _dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
    created_ts = getattr(st, "st_birthtime", None)
    if created_ts is None and os.name == "nt":
        created_ts = st.st_ctime
    created = (
        _dt.datetime.fromtimestamp(created_ts).isoformat(timespec="seconds") if created_ts else None
    )
    return created, modified
