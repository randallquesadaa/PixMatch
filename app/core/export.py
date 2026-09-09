"""Export analysis results to CSV, JSON or a self-contained HTML report.

All three are read-only snapshots of what the analysis found and what the user
decided. The HTML report embeds thumbnails as data: URIs so it is a single
portable file.
"""
from __future__ import annotations

import base64
import csv
import html
import json
from datetime import datetime
from pathlib import Path

from app.core.duplicate_groups import AnalysisResult, FileRecord
from app.utils.file_utils import human_size

_THUMB_LIMIT = 1500       # stop embedding thumbnails past this many files
_THUMB_EDGE = 160


def _resolution(rec: FileRecord) -> str:
    res = rec.resolution
    return f"{res[0]}x{res[1]}" if res else ""


def _decision(rec: FileRecord) -> str:
    return {
        "keep": "conservar",
        "delete": "eliminar",
        "undecided": "sin decidir",
    }.get(rec.decision.value, rec.decision.value)


def _rows(result: AnalysisResult):
    for group in result.groups:
        for rec in group.records:
            yield {
                "group_id": group.group_id,
                "category": group.category.value.key,
                "category_label": group.category.label,
                "similarity_percent": (
                    "" if rec.similarity_percent is None else f"{rec.similarity_percent:.1f}"
                ),
                "decision": _decision(rec),
                "deleted": "si" if rec.deleted else "no",
                "name": rec.name,
                "path": rec.path,
                "size_bytes": rec.size,
                "size_human": human_size(rec.size),
                "resolution": _resolution(rec),
                "sha256": rec.sha256 or "",
                "pixel_digest": rec.pixel_digest or "",
                "phash": "" if rec.phash is None else f"{rec.phash:016x}",
            }


# ---------------------------------------------------------------------------
def export_csv(result: AnalysisResult, path: str | Path) -> Path:
    path = Path(path)
    fields = [
        "group_id", "category", "category_label", "similarity_percent",
        "decision", "deleted", "name", "path", "size_bytes", "size_human",
        "resolution", "sha256", "pixel_digest", "phash",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in _rows(result):
            writer.writerow(row)
    return path


def export_json(result: AnalysisResult, path: str | Path) -> Path:
    path = Path(path)
    scan = result.scan_stats
    payload = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "root": result.root,
        "incremental": result.incremental,
        "summary": {
            "files_found": getattr(scan, "files_found", None),
            "images": getattr(scan, "images", None),
            "videos": getattr(scan, "videos", None),
            "folders_scanned": getattr(scan, "folders_scanned", None),
            "total_size_bytes": getattr(scan, "total_size", None),
            "groups": len(result.groups),
            "file_identical_groups": len(result.file_identical_groups),
            "pixel_identical_groups": len(result.pixel_identical_groups),
            "similar_groups": len(result.similar_groups),
            "redundant_copies": result.redundant_copies,
            "reclaimable_bytes": result.reclaimable_bytes,
            "elapsed_seconds": round(result.elapsed_seconds, 2),
        },
        "groups": [
            {
                "group_id": g.group_id,
                "category": g.category.value.key,
                "category_label": g.category.label,
                "similarity_percent": g.similarity_percent,
                "potential_reclaimable_bytes": g.potential_reclaimable,
                "files": [
                    {
                        "name": r.name,
                        "path": r.path,
                        "size_bytes": r.size,
                        "resolution": _resolution(r),
                        "sha256": r.sha256,
                        "pixel_digest": r.pixel_digest,
                        "phash": None if r.phash is None else f"{r.phash:016x}",
                        "similarity_percent": r.similarity_percent,
                        "decision": _decision(r),
                        "deleted": r.deleted,
                    }
                    for r in g.records
                ],
            }
            for g in result.groups
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
_HTML_HEAD = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PixMatch - Informe</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 14px/1.5 system-ui, sans-serif; margin: 0; background: #f6f7f9; color: #1b1f24; }}
  header {{ background: #1f2937; color: #fff; padding: 20px 28px; }}
  header h1 {{ margin: 0 0 4px; font-size: 20px; }}
  .wrap {{ max-width: 1100px; margin: 0 auto; padding: 24px 28px 60px; }}
  .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 18px 0 32px; }}
  .summary div {{ background: #fff; border: 1px solid #e2e5ea; border-radius: 10px; padding: 12px 14px; }}
  .summary b {{ display: block; font-size: 18px; }}
  .group {{ background: #fff; border: 1px solid #e2e5ea; border-radius: 12px; padding: 16px; margin-bottom: 18px; }}
  .badge {{ display: inline-block; padding: 2px 10px; border-radius: 9px; color: #fff; font-weight: 600; font-size: 12px; }}
  .files {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; margin-top: 12px; }}
  .file {{ border: 1px solid #e8eaee; border-radius: 8px; padding: 10px; font-size: 12px; word-break: break-all; }}
  .file img {{ display: block; max-width: 100%; height: auto; border-radius: 4px; margin-bottom: 8px; background: #eee; }}
  .k {{ color: #6b7280; }}
  .d-keep {{ color: #15803d; font-weight: 700; }}
  .d-delete {{ color: #b91c1c; font-weight: 700; }}
  .d-deleted {{ color: #b91c1c; font-weight: 700; text-decoration: line-through; }}
  code {{ font-size: 11px; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #16181d; color: #e6e6e6; }}
    .group, .summary div, .file {{ background: #21242b; border-color: #333; }}
  }}
</style></head><body>
<header><h1>PixMatch &mdash; Informe de duplicados</h1>
<div>{root}</div><div>Generado: {generated}</div></header>
<div class="wrap">
"""


def _thumb_data_uri(cache, rec: FileRecord) -> str:
    try:
        png = cache.get_png(rec.path, rec.size, rec.mtime)
    except Exception:  # noqa: BLE001
        png = None
    if not png:
        return ""
    b64 = base64.b64encode(png).decode("ascii")
    return f"data:image/png;base64,{b64}"


def export_html(
    result: AnalysisResult,
    path: str | Path,
    *,
    include_thumbnails: bool = True,
) -> Path:
    path = Path(path)
    scan = result.scan_stats

    total_files = sum(g.count for g in result.groups)
    embed = include_thumbnails and total_files <= _THUMB_LIMIT
    cache = None
    if embed:
        from app.utils.thumbnail_cache import ThumbnailCache

        cache = ThumbnailCache(max_edge=_THUMB_EDGE)

    parts = [
        _HTML_HEAD.format(
            root=html.escape(result.root),
            generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
    ]

    parts.append('<div class="summary">')
    for label, value in [
        ("Archivos analizados", f"{getattr(scan, 'files_found', 0):,}"),
        ("Grupos de duplicados", f"{len(result.groups):,}"),
        ("Archivo idéntico", f"{len(result.file_identical_groups):,}"),
        ("Pixel idéntico", f"{len(result.pixel_identical_groups):,}"),
        ("Similares", f"{len(result.similar_groups):,}"),
        ("Copias redundantes", f"{result.redundant_copies:,}"),
        ("Espacio recuperable", human_size(result.reclaimable_bytes)),
    ]:
        parts.append(f"<div><span class='k'>{html.escape(label)}</span><b>{html.escape(value)}</b></div>")
    parts.append("</div>")

    if not embed and include_thumbnails:
        parts.append(
            f"<p class='k'>({total_files:,} archivos: se omiten las miniaturas para "
            f"mantener el informe manejable.)</p>"
        )

    for group in result.groups:
        style = group.category.style
        sim = "" if group.similarity_percent is None else f" &middot; similitud ≈ {group.similarity_percent:.1f}%"
        parts.append('<div class="group">')
        parts.append(
            f'<span class="badge" style="background:{style.color}">'
            f'{html.escape(style.short_es)}</span> '
            f'&nbsp;<b>Grupo #{group.group_id}</b> &mdash; {group.count} archivos{sim} '
            f'&middot; recuperable {human_size(group.potential_reclaimable)}'
        )
        parts.append('<div class="files">')
        for rec in group.records:
            parts.append('<div class="file">')
            if embed:
                uri = _thumb_data_uri(cache, rec)
                if uri:
                    parts.append(f'<img src="{uri}" alt="">')
            if rec.deleted:
                dcls, dtxt = "d-deleted", "ELIMINADO"
            elif rec.decision.value == "keep":
                dcls, dtxt = "d-keep", "CONSERVAR"
            elif rec.decision.value == "delete":
                dcls, dtxt = "d-delete", "MARCADO PARA ELIMINAR"
            else:
                dcls, dtxt = "k", "sin decidir"
            res = _resolution(rec)
            parts.append(f'<div class="{dcls}">{dtxt}</div>')
            parts.append(f'<b>{html.escape(rec.name)}</b><br>')
            parts.append(f'<span class="k">{html.escape(rec.path)}</span><br>')
            parts.append(f'{human_size(rec.size)}')
            if res:
                parts.append(f' &middot; {res}')
            if rec.similarity_percent is not None:
                parts.append(f' &middot; {rec.similarity_percent:.1f}% sim.')
            if rec.sha256:
                parts.append(f'<br><code>sha256:{html.escape(rec.sha256[:24])}…</code>')
            parts.append('</div>')
        parts.append('</div></div>')

    parts.append("</div></body></html>")
    path.write_text("".join(parts), encoding="utf-8")
    return path
