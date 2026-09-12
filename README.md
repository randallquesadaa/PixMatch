# PixMatch

[![CI](https://github.com/randallquesadaa/PixMatch/actions/workflows/ci.yml/badge.svg)](https://github.com/randallquesadaa/PixMatch/actions/workflows/ci.yml)
[![CodeQL](https://github.com/randallquesadaa/PixMatch/actions/workflows/codeql.yml/badge.svg)](https://github.com/randallquesadaa/PixMatch/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/randallquesadaa/PixMatch?sort=semver)](https://github.com/randallquesadaa/PixMatch/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Desktop tool for photo and video libraries. It has four tabs:

- **Duplicates** — finds duplicate and near-duplicate files (identical,
  pixel-identical, resized, cropped, similar) in a folder and its
  subfolders, with a side-by-side comparison, and lets **you** decide what
  to keep.
- **Rename by date** — cleans up file naming: renames to
  `YYYYMMDD_HHMMSS.ext` from EXIF metadata (or the modification date).
- **Import & organize** — moves photos / videos from a source folder (a
  phone, a memory card) into your ordered library
  `<Country>/<Year>/<Year-Month>/`, named by date, skipping what is **already
  there** (identical SHA-256 or identical pixels). The country comes from the
  file's own **GPS**, offline; no GPS goes to "No location".
- **Organize library** — rearranges a **single, already-existing folder** (an
  old drive, an unsorted dump) into that same structure, in place. It
  compares and evaluates nothing: every file moves to where it belongs, or is
  left alone if it's already there.

> **Nothing is deleted, moved or renamed without an explicit, confirmed
> action.** Duplicate analysis is read-only. Deleting (recycle bin by
> default, permanent as a separate mode), renaming, importing and organizing
> all require confirmation, are logged to an inspectable history, and can be
> undone. When importing or organizing, a move is *copy → verify SHA-256 at
> the destination → remove the source* (organizing uses an atomic rename
> when source and destination are on the same drive, the common case): if
> anything fails, the original is left untouched.

Current status: **v0.8.0** — full duplicate analysis + rename tool +
import/organize + library reorganization. See [`ROADMAP.md`](ROADMAP.md) for
the architecture, technical decisions and known limitations.

---

## What it does

- Folder selection (button, or **drag and drop** the folder onto the window).
- Recursive scan with no depth limit:
  - skips directory symlinks to avoid cycles (configurable),
  - handles Unicode names and spaces,
  - permission errors or unreadable files are logged and **do not** stop the
    analysis,
  - folder exclusion by name or path (always explicit, never automatic),
    minimum size, and ignored extensions.
- Live stats: files, images, videos, other, total size, folders scanned.
- **Exact duplicate** detection:
  - pre-grouping by size (an optimization only),
  - full **SHA-256** of the candidates,
  - two or more files sharing a SHA-256 → one `IDENTICAL FILE` group.
  - A group of 3, 4, … files is **one group**, not several pairs.
- **Pixel-by-pixel** detection:
  - only candidates with **matching dimensions** are decoded,
  - EXIF orientation normalized **in memory** (the file itself is untouched),
  - opaque RGB and opaque RGBA are treated as equal,
  - files with **different bytes** but **identical pixels** (e.g. PNG vs BMP,
    or the same PNG with/without metadata) → `PIXEL-BY-PIXEL IDENTICAL`
    group, clearly distinct from `IDENTICAL FILE`.
- **Smart similar-image** detection (opt-in, enabled in Settings):
  - **combined, configurable scoring engine**: pHash 40% + dHash 20% + aHash
    10% + **color histogram** 10% + **layout hash** (16×16 blocks) 20%. The
    color histogram and layout hash are what **stop two unrelated photos
    from grouping just because their colors look alike** (e.g. two different
    sunsets),
  - **configurable score bands** (visually identical ≥ 98%, very similar
    ≥ 90%, similar ≥ 78%; below that nothing groups) and **sensitivity**
    Low / Medium / High / Custom,
  - dedicated categories: `VISUALLY IDENTICAL`, `RESIZED` (same image at
    another resolution), `CROPPED` (one image is a partial frame of another —
    High sensitivity only), `VERY SIMILAR`, `SIMILAR`,
  - tolerates brightness / contrast / blur / noise / recompression,
  - **N-ary grouping** with a per-file **relation list**
    (`small 98.7% · crop 93.4%`) and **"Grouped because: ✓ …"**,
  - **"Compare in detail"** with **Normal / Side by side / Difference /
    Overlay** modes (with an opacity slider),
  - **AI-assisted detection** (CLIP visual embeddings) — optional,
    `pip install "pixmatch[ai]"`; falls back to the traditional methods alone
    when it isn't installed.
- **Video analysis** (opt-in — needs **FFmpeg**):
  - automatic FFmpeg/ffprobe detection (PATH → `imageio-ffmpeg`), with a
    **configurable path** and a "Check FFmpeg" button in Settings; without
    FFmpeg the app says so and continues with images,
  - videos with the **same SHA-256** → `IDENTICAL FILE`,
  - metadata (duration, resolution, codec, FPS, bitrate, audio, date) via
    ffprobe or, failing that, by parsing `ffmpeg -i`,
  - **staged frame comparison**: 5 frames (5/25/50/75/95%) are sampled from
    each video of similar duration and compared by **pHash** →
    `LIKELY SAME CONTENT` / `VERY SIMILAR` / `SIMILAR` categories (never
    "identical" from a handful of frames),
  - a video, its byte-identical copy, **and** its re-encoded version end up
    in **one group** (the category reflects the weakest link),
  - a video thumbnail (one frame), and **"🎬 Compare videos"**: a
    representative frame + metadata + estimated similarity for each video.
- **SQLite cache** with **incremental analysis**:
  - stores the hash, pixel-hash, perceptual hashes, metadata and video data
    (frames included) per file,
  - a file whose size and date haven't changed **is never reprocessed**,
  - the **"Analyze"** button is incremental; **"↻ Full analysis"** recomputes
    everything. If the database is corrupted, analysis continues without a
    cache.
- Duplicates view:
  - filters (All / Images / Videos / Identical file / Identical pixels /
    Similar / Unreviewed / Reviewed / Marked for deletion),
  - sorting (savings / duplicate count / size / **similarity** / name /
    path), search, "≥ N files per group",
  - Previous / Next navigation plus a side list of groups with their
    category,
  - side-by-side comparison with thumbnail, name, path, size, resolution,
    format, color mode, EXIF orientation, camera, dates, SHA-256, pixel hash,
    pHash and **similarity % vs. the group's reference file**,
  - per file: **🔍 Zoom**, Open file, Open folder, Copy path, and **🗑
    Delete** (deletes just that file, with confirmation and history),
  - **zoomed-in viewer**: zoom (wheel / +/−) and pan, fit / actual size,
    **◀ ▶ to page through the whole group** (or arrow keys) and decide
    *Keep / Mark / Delete now* while looking at the detail view; respects
    EXIF orientation **without touching the file**,
  - a **non-binding recommendation** of what to keep, with a score
    (`KEEP_SCORE`: resolution, size, lossless format, video bitrate,
    EXIF/camera/date metadata, age, a "copy"-looking name, backup/temp
    folder) and a **confidence percentage** — it never preselects anything,
  - a live indicator of how much space would be freed, and a warning if a
    decision would leave 0 copies.
- **Safe deletion**:
  - a "Keep this file" checkbox plus *Keep selected*, *Delete unselected*,
    *Keep all*, *Ignore group* buttons — all of them only **mark**; nothing
    is deleted until you click **"🗑 Delete marked files…"** and **confirm**,
  - by default files are **moved to the system recycle bin** (`Send2Trash`,
    recoverable); permanent deletion is a separate mode with a red warning,
  - the confirmation dialog lists the files, the space to be freed, and any
    warnings (a file that changed since analysis, a group that would end up
    with no copies at all → a mandatory checkbox),
  - each file is re-verified right before deletion; one that changed size or
    disappeared **is skipped** and reported,
  - a post-operation summary ("37 files moved to the recycle bin. Space
    freed: 8.72 GB"),
  - an **operation history** (Help → History): date, file, action, result —
    including failures,
  - deleted files stay visible, grayed out ("DELETED"); resolved groups move
    to the "Resolved" filter.
- **Export** (File → Export results): **CSV**, **JSON**, and a
  **self-contained HTML report** with embedded thumbnails, paths, hashes,
  similarity and each file's decision.
- Results dashboard: scan totals, files decoded / perceptually hashed /
  reused from cache, a breakdown by category, potential space recoverable,
  and files deleted in the session.
- **Dark and light theme** with **WCAG AA** contrast (dimmed text included),
  visible hover/pressed/focus/disabled button states, and buttons that
  disable themselves when their action doesn't apply.
- **Language**: Spanish / English (Settings → General). Spanish is the
  source language; the English translation covers the main interface and is
  easy to extend.
- Background analysis with **Pause / Resume / Cancel** (pausing also halts
  the decoding stages); the interface never freezes.
- Keyboard shortcuts: `←`/`→` previous/next group, `M` keep, `D` mark for
  deletion, `A` keep all, `I` ignore, `Esc` go back.
- A rotating on-disk log, viewable from **Settings → Logs**.

### Match categories

Never mixed together:

| Label | Confidence | Meaning |
|---|---|---|
| 🟢 `IDENTICAL FILE` | 100% | The files' bytes are identical (same SHA-256). |
| 🔵 `PIXEL-BY-PIXEL IDENTICAL` | 100% | Different bytes, but the decoded pixels (EXIF orientation normalized) are exactly equal. |
| 🟦 `VISUALLY IDENTICAL` | = score | A near-perfect perceptual match, not confirmed at the pixel level. |
| 🟦 `RESIZED` | = score | The same image saved at another resolution / compression / format. |
| 🟧 `CROPPED` | = score | One image looks like a partial frame (crop) of the other. |
| 🟦 `LIKELY SAME CONTENT` | = score | Video: the sampled frames match (never a certainty). |
| 🟡 `VERY SIMILAR` | = score | A likely variant (resolution/compression/format, minor edits). |
| 🟠 `SIMILAR` | = score | Same scene or subject with bigger differences; review it in detail. |
| 🔴 `DIFFERENT` | — | Not a duplicate (not shown as a group). |

The *score* is a similarity calculated by the algorithms, **not mathematical
truth**: PixMatch would rather say "possibly similar" than assert a duplicate
that isn't one.

### "Rename by date" tab

A second tab, independent of duplicate analysis, to **clean up the naming**
of a photo/video library:

- Scans a folder **and its subfolders**.
- Proposes a `YYYYMMDD_HHMMSS.ext` name for each file, with the date taken
  from (in order): **EXIF DateTimeOriginal → DateTimeDigitized → DateTime →
  video metadata (ffprobe) → the file's modification date** (always
  available, the fallback).
- **Table preview** (folder · current name · new name · date · date source)
  with a per-row checkbox to include/exclude.
- Configurable format (`strftime` pattern), an optional prefix (e.g. `IMG_`),
  include videos, lowercase extensions, `.jpeg`→`.jpg`.
- Files **never change folder**: only the name changes.
- **Never overwrites** an existing file — if two photos share a second (or
  the name is already taken), a deterministic `_2`, `_3`… suffix is added,
  per folder.
- Renaming is done in **two phases** (a temporary name, then the final one)
  so swaps and cycles are safe; anything that fails is rolled back.
- Everything goes to the **history**, with an **"Undo last rename"** action.

### "Import & organize" tab

A third tab to **empty a source** (a phone, an SD card, a downloads folder)
into an already-ordered library:

- You choose a **source folder** and the **library's root folder**.
- For each photo / video:
  - **capture date**, with the same priority as renaming (EXIF → video
    metadata → modification date),
  - **country** from the file's own **GPS** (EXIF `GPSLatitude/…` for
    photos; the `location` ISO 6709 tag via ffprobe for videos), resolved
    **offline** with a borders dataset bundled with the app
    (`app/resources/country_borders.json`, Natural Earth 1:50m, public
    domain). No GPS → the **"No location"** folder (the label is
    configurable).
  - **destination** `<library>/<Country>/<Year>/<Year-Month>/<YYYYMMDD_HHMMSS.ext>`.
    An existing country folder with different letter case is reused; there's
    a country **alias** map in the settings.
- **"Already there" detection** against the whole library:
  - **identical SHA-256** (with a size prefilter), and
  - **identical pixels** (same content in another format/with different
    metadata) — for this, the library's image dimensions are indexed; if you
    already analyzed it from the *Duplicates* tab, that cache is reused.
  - duplicates **within the batch itself** are also caught.
- **Table preview** (source · date · country · destination folder · name ·
  status) with a per-row checkbox. Files already in the library **are not
  moved**; you can tick their row to **send the source to the recycle bin**.
- **Move = copy → verify SHA-256 at the destination → remove the source.**
  If the copy fails or doesn't match, the original **is left untouched**. A
  library file is never overwritten: if two photos share a name, **neither**
  is left without a suffix — both get `_01`, `_02`… with a leading zero.
- Everything goes to the **history**, with an **"Undo last import"** action.

### "Organize library" tab

A fourth tab to put an **already-existing but disorganized** library in
order (an old hard drive, years of unsorted dumping): it rearranges that
same folder, in place, with the
`<Country>/<Year>/<Year-Month>/<YYYYMMDD_HHMMSS.ext>` structure — the same
date, GPS and naming logic as "Import & organize", but **without comparing
anything against anything**:

- There is no "already there" or duplicate detection — that isn't its job.
  Every file is placed where its own metadata says it belongs.
- A file that is **already** exactly where it belongs (same folder, same
  name) is left alone — running it twice in a row does nothing the second
  time.
- Two files that would land on the same name are kept apart with the same
  `_01`/`_02`… suffix import uses — never overwritten.
- The move is an **atomic rename** when source and destination are on the
  same drive (the normal case when reorganizing a single drive): no copying
  needed. If it crosses to another filesystem (e.g. a volume mounted inside
  the folder), it falls back to copy → verify SHA-256 → remove the source,
  same as importing.
- Everything goes to the **history**, with an **"Undo last reorganization"**
  action.

To regenerate the borders dataset:
`python scripts/build_country_borders.py` (downloads Natural Earth once).

---

## Download (users)

Ready-to-run executables on the
[**releases page**](https://github.com/randallquesadaa/PixMatch/releases/latest)
— no Python install needed:

| OS | File | How to open it |
|---|---|---|
| Windows 10/11 (x64) | `PixMatch-*-windows-x64.zip` | unzip → `PixMatch.exe` |
| macOS (Apple Silicon) | `PixMatch-*-macos-arm64.zip` | first run: right-click → *Open* |
| Linux (x86-64) | `PixMatch-*-linux-x86_64.tar.gz` | extract → `./PixMatch/PixMatch` |

Binaries are unsigned; verify the download against each release's
`SHA256SUMS.txt`. Analyzing **video** needs FFmpeg installed on the system
(PixMatch detects it automatically); **images** need nothing else.

Every release is built automatically by GitHub Actions when a version bump
lands on `main` and passes the full CI (tests on Linux/macOS/Windows, lint,
security analysis).

---

## Install from source

Requires **Python 3.11+** (tested with 3.14).

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e .
```

### Image formats

Built in (via Pillow): JPG/JPEG, PNG, WEBP, GIF, BMP, TIFF/TIF.

Optional (activate themselves once installed):

```bash
pip install pillow-heif        # HEIC / HEIF
pip install pillow-avif-plugin # AVIF
```

If a format has no decoder, the file is still scanned and hashed (**exact**
SHA-256 duplicate detection doesn't need to decode it); only its thumbnail,
metadata and pixel/perceptual comparison are skipped.

### FFmpeg (for video analysis)

Formats: MP4, M4V, MOV, AVI, MKV, WMV, WEBM, MPEG/MPG, 3GP, TS/M2TS, FLV…

1. **Recommended**: install the system FFmpeg and leave it on the PATH.
   - Windows: `winget install Gyan.FFmpeg` (or [gyan.dev](https://www.gyan.dev/ffmpeg/builds/))
   - macOS: `brew install ffmpeg`
   - Linux: `sudo apt install ffmpeg` / `sudo dnf install ffmpeg`
2. Or set the path manually in **Settings → Scanning → FFmpeg path** (accepts
   the executable or its folder).
3. Or, as a zero-config shortcut: `pip install imageio-ffmpeg` (bundled
   binary, ~30 MB) — the app detects it automatically.

Without FFmpeg, video analysis is skipped (the app says so), but
**byte-identical** videos are still detected via SHA-256.

### Settings (Settings → Scanning tab)

- Analyze images / **detect pixel-by-pixel identical** (both on by default).
- **Analyze similar images** (combined engine) — off by default because it
  decodes every image.
  - **Detection sensitivity**: *Low* (only very clear duplicates), *Medium*
    (+ very similar images), *High* (+ variants, crops and edits), *Custom*.
  - In *Custom*: edit the three **score bands**, the **weights** of the 5
    signals, and toggle **crop detection**.
  - **AI-assisted detection** (CLIP embeddings) — optional.
- **Analyze videos** (off by default) + **FFmpeg path** + **Check FFmpeg**.
- **Use on-disk cache** and its path.
- Follow symlinks, minimum size, excluded extensions and folders, number of
  *workers*, theme.
- **Default deletion mode** (General tab): system recycle bin (recommended)
  or permanent. Confirmation is **always** required.

---

## Running it

```bash
python main.py
# or, with a starting folder:
python main.py "/path/to/my photos"
```

---

## Tests and code quality

```bash
pip install -e ".[dev]"

pytest                     # 200 tests (UI included, headless)
ruff check .               # lint
ruff format --check .      # formatting
bandit -c pyproject.toml -r app --severity-level medium   # security analysis
pip-audit                                                 # dependency CVEs
```

UI tests use `QT_QPA_PLATFORM=offscreen` (set in CI). Video tests are
**skipped automatically** without FFmpeg; AI tests, without an embeddings
backend.

**Continuous integration** (`.github/workflows/`): runs on every pull
request and every push to `main`, and **everything has to pass**:

| Check | Tool |
|---|---|
| Functionality and reliability | `pytest` on **Linux · macOS · Windows** × Python **3.11 / 3.12 / 3.13** (with FFmpeg, so video tests actually run) |
| Code style | `ruff check` + `ruff format --check` |
| Code vulnerabilities | `bandit` (fails at medium+ severity) and **CodeQL** (`security-and-quality`, weekly) |
| Dependency vulnerabilities | `pip-audit` + **dependency-review** (blocks PRs with high-severity CVEs or GPL/AGPL licenses) |
| Dependencies up to date | Weekly **Dependabot** (pip + GitHub Actions) |

Coverage highlights, by area:

- **Exact duplicates** — SHA-256 vs `hashlib`, identical / different / empty,
  Unicode paths, cancellation, `partial_signature`; recursive scanning,
  exclusions, minimum size, symlink cycles, permissions; grouping (N-way
  groups, ordering, safety flags); pipeline (byte-identical copies group; a
  size collision with no real match does NOT); pause/resume; cancellation →
  partial result; an unreadable file doesn't abort the run; EXIF
  orientation; `RunController`; a GUI smoke test.
- **Pixel-identical & perceptual** — `test_perceptual.py` (64-bit hashes,
  identical match, a near resize, distant images don't, corrupt → None,
  BK-tree, `classify_similarity` never mixes categories);
  `test_image_analyzer.py` (PNG/BMP = same digest, opaque RGB vs RGBA, EXIF
  orientation normalized, corrupt → None, `difference_image`);
  `test_database.py` (upsert/get, validity, persistence, pruning, a
  corrupted DB degrades without raising); `test_analysis_phase2.py`
  (PNG+BMP → `PIXEL_IDENTICAL`; byte-identical copies stay
  `FILE_IDENTICAL`; a resized copy is similar only with perceptual matching
  on; incremental runs reuse the cache; the cache invalidates when a file
  changes; **pausing blocks the decoding stage**).
- **Safe deletion** — `test_deletion.py` (permanent deletes; recycle bin
  **moves** to a fake trash; a missing/changed file is skipped; a folder is
  never deleted; the preview flags empty groups + warnings; cancellation
  cuts the batch short; Unicode paths; a permission error is logged; the
  history records failures); `test_history.py` (persistence, clearing,
  graceful degradation); `test_export.py` (CSV, JSON round-trip, a
  self-contained and properly escaped HTML report); group resolution after
  deletion; the UI deletion flow.
- **Video** — `test_ffmpeg.py` (detection never raises, falls back on an
  invalid path, a file or folder path); `test_videos.py` (metadata; frame
  hashes + comparison: a re-encode scores ≥90%, an unrelated video <80%, no
  overlap → `None`; serialization; byte-identical → `FILE_IDENTICAL`; a
  re-encode → "likely same content"; unrelated videos don't group;
  **byte-identical + re-encode land in one group of 3**; missing FFmpeg
  doesn't crash anything; incremental runs reuse the video cache).
- **Theme / accessibility** — `test_theme.py`: switching theme **inverts**
  text colors (nested widgets already on screen included); body, dimmed and
  disabled text **meet WCAG contrast** in both themes (the ratio is computed
  and the test fails below the threshold).
- **Recommendation** — `test_recommendation.py`: prefers higher resolution /
  bitrate / an older file; penalizes "copy"-looking names and backup
  folders; low confidence for byte-identical groups with no other signal.
- **i18n** — `test_i18n.py`: Spanish is the identity; English translates
  known strings and passes unknown ones through (never blank).
- **Smart similarity** — `test_color_hist.py` (signature, brightness
  tolerated, a different palette scores low); `test_similarity_engine.py`
  (combined score, weights normalized and respected, promotion to
  `RESIZED`, bands, presets); `test_crop_detect.py` (a real crop is detected
  with its region, unrelated images = None, an almost-full-frame crop ≠ a
  crop); `test_similarity_phase6.py` (two different sunsets do NOT group;
  resize → `RESIZED`; crop → `CROPPED` only at High sensitivity;
  brightness/blur are still detected; low sensitivity is stricter; every
  match carries a label + confidence; the optional AI vote is harmless
  without a backend).
- **Rename by date** — `test_renamer.py`: EXIF vs. modification date; format
  and prefix; deterministic suffixes on a clash (same second or an existing
  file); subfolders numbered independently; **A↔B swap is safe** (two-phase
  rename); **never overwrites** (rollback); **undo**; Unicode paths;
  disabled rows are skipped.
- **Import & organize** — `test_geo.py`: EXIF GPS (DMS→decimal, missing,
  *null island*), ISO 6709, `CountryResolver` (known points incl. coastal
  ones via *snapping*, ocean = None, a missing dataset degrades gracefully).
  `test_importer.py`: `<Country>/<Year>/<Year-Month>/<name>` destination, no
  GPS → "No location", reuses an existing country folder ignoring accents,
  aliases; exact / pixel / in-batch duplicates; `_01`/`_02` suffix on a name
  clash (never one left bare next to numbered ones, or next to a file
  already on disk); a verified move + **undo**; the source is only removed
  once the copy succeeds; a duplicate's source is only trashed if ticked.
- **Organize library** — `test_organizer.py`: same destination as importing,
  but **without** evaluating duplicates; a file already in place →
  `UNCHANGED` and untouched; `_01`/`_02` suffix on a name clash; apply +
  **undo**; falls back to copy-verify-remove if the atomic rename fails by
  crossing a filesystem boundary (`EXDEV`).

---

## Install as a package (optional)

```bash
pip install .            # installs the `pixmatch` command
pip install ".[video]"   # + imageio-ffmpeg
pixmatch
```

---

## Building an executable

**You normally won't need to**: release executables are built by GitHub
Actions (`.github/workflows/release.yml`) for all three platforms and
published on the [releases page](https://github.com/randallquesadaa/PixMatch/releases).

The automated release flow:

1. In a PR, bump the version in `app/__init__.py` (`__version__`).
2. Merging to `main` runs the full CI.
3. If it passes and that version has no tag yet, Actions creates the
   `vX.Y.Z` tag, builds Linux/macOS/Windows with **PyInstaller**
   (one-folder), and publishes a Release with all three archives plus
   `SHA256SUMS.txt`.

Published binaries **do not** bundle `imageio-ffmpeg`: its FFmpeg is GPL and
would taint the executable's license. PixMatch stays MIT and detects a
system FFmpeg at runtime.

### Building manually

From the project root, on the target platform (no cross-compilation):

```bash
packaging/build_linux.sh          # -> dist/PixMatch/PixMatch
packaging/build_macos.sh          # -> dist/PixMatch.app  (generates .icns)
packaging\build_windows.bat       # -> dist\PixMatch\PixMatch.exe
```

Or directly: `pyinstaller packaging/PixMatch.spec --noconfirm`. The
`build_*` scripts do install `imageio-ffmpeg`, so a manually built
executable ships with FFmpeg embedded (and therefore **can't** be
redistributed except under the GPL).

- macOS: the app is unsigned; first run with right-click → Open, or
  `xattr -dr com.apple.quarantine dist/PixMatch.app`.
- The executable is about ~250 MB (PySide6), ~270 MB with FFmpeg bundled.

---

## Performance

Reproducible benchmark: `N=3000 python scripts/benchmark.py`.

Measured on the development machine (synthetic 800×600 images, ~all cores):

| Scenario | 3,000 images | peak RSS |
|---|---|---|
| Exact detection (SHA-256 + pixel) | ~4 s (~1.3 ms/img) | ~210 MB |
| + perceptual hashing | ~8.5 s (~2.8 ms/img) | ~212 MB |
| **Incremental** re-analysis | ~0.1 s | — |

* **Peak RSS stays flat** as N grows: streaming hashes, images decoded one
  at a time and released, an LRU thumbnail cache (max 400). No UI memory
  leak browsing hundreds of groups.
* Linear extrapolation: **100,000 images ≈ 2 min** (exact) / **≈ 5 min**
  (+perceptual); the incremental run is then a matter of seconds.
* Real 12 MP photos decode slower per image; the architecture (parallel +
  streaming + cache) stays the same.

---

## Architecture and technical decisions

`app/core/*` holds pure logic with no Qt import — the pipeline
(`run_analysis`) works through callbacks that the Qt workers translate into
signals, so most of the logic is tested without ever starting a GUI. Each
tool (duplicates, rename, import, organize) is a self-contained core module
plus a thin worker (background thread) plus a view (PySide6 widget); moves
and deletions always go through a verify-then-act step and are logged to an
undoable history.

The full file-by-file map, the reasoning behind each significant decision
(why a custom perceptual hash instead of `imagehash`, why SQLite in one
thread, why FFmpeg via subprocess, why Spanish is the UI's source language,
and more), scale/performance limits and known limitations all live in
[`ROADMAP.md`](ROADMAP.md) — kept there instead of duplicated here so it
never drifts out of sync with the code.

---

## License

This project is published under the **MIT license** (see
[`LICENSE`](LICENSE)).

### Third-party dependencies

| Package | License | Notes |
|---|---|---|
| **PySide6 / Qt** | LGPL v3 | Dynamically linked. Distributing **binaries** requires allowing the Qt libraries to be swapped out (PyInstaller's *one-folder* packaging already allows this) and including the LGPL text. |
| **Pillow** | MIT-CMU (HPND) | Permissive. |
| **Send2Trash** | BSD | Permissive. |
| `imageio-ffmpeg` *(optional)* | BSD, but **bundles an FFmpeg binary built with `--enable-gpl`** | The **source code** on GitHub is unaffected. But **do not distribute an executable that includes that FFmpeg** except under the GPL: for binary releases, use the system FFmpeg or an LGPL build, and don't bundle `imageio-ffmpeg`. |
| `open-clip-torch`, `torch` *(optional, `[ai]` extra)* | MIT / BSD | Model weights are downloaded at runtime. |

**In short:** publishing the **source code** under the MIT license is fine.
When building **executables for distribution**, keep Qt's LGPL in mind, and
the GPL of that FFmpeg if you bundle `imageio-ffmpeg`.
