# PixMatch

Herramienta de escritorio para bibliotecas de fotos y vídeos. Tiene dos
pestañas:

- **Duplicados** — encuentra archivos duplicados y casi-duplicados (idénticos,
  pixel-idénticos, redimensionados, recortes, similares) en una carpeta y sus
  subcarpetas, con comparación lado a lado, y deja que **tú** decidas qué
  conservar.
- **Renombrar por fecha** — pone orden en la nomenclatura: renombra a
  `AAAAMMDD_HHMMSS.ext` según los metadatos EXIF (o la fecha de modificación).

> **Nada se elimina, mueve ni renombra sin una acción explícita y confirmada.**
> El análisis de duplicados es de solo lectura. Borrar (papelera por defecto,
> permanente como modo aparte) y renombrar requieren confirmación, quedan en un
> historial consultable y el renombrado se puede deshacer.

Estado actual: **v0.7.0** — análisis de duplicados completo + herramienta de
renombrado. Ver [`ROADMAP.md`](ROADMAP.md) para la arquitectura, las decisiones
técnicas y las limitaciones conocidas.

---

## Qué hace

- Selección de carpeta (botón o **arrastrar y soltar** la carpeta a la ventana).
- Escaneo recursivo sin límite de profundidad:
  - ignora enlaces simbólicos de carpeta para evitar ciclos (configurable),
  - maneja nombres Unicode y con espacios,
  - los errores de permisos o archivos ilegibles se registran y **no**
    detienen el análisis,
  - exclusión de carpetas por nombre o ruta (siempre explícita, nunca
    automática), tamaño mínimo y extensiones ignoradas.
- Estadísticas en vivo: archivos, imágenes, videos, otros, tamaño total,
  carpetas analizadas.
- Detección de **duplicados exactos** (Fase 1):
  - agrupación previa por tamaño (solo optimización),
  - **SHA-256** completo de los candidatos,
  - dos o más archivos con el mismo SHA-256 → un grupo `ARCHIVO IDÉNTICO`.
  - Un grupo de 3, 4, … archivos es **un solo grupo**, no varios pares.
- Detección **pixel por pixel** (Fase 2):
  - se decodifican solo los candidatos con **dimensiones coincidentes**,
  - orientación EXIF normalizada **en memoria** (el archivo no se toca),
  - RGB opaco y RGBA opaco se consideran iguales,
  - archivos con **bytes distintos** pero **píxeles idénticos** (p. ej. PNG vs
    BMP, o el mismo PNG con/sin metadatos) → grupo `PIXEL POR PIXEL IDÉNTICO`,
    claramente distinto de `ARCHIVO IDÉNTICO`.
- Detección **inteligente de imágenes similares** (Fase 6, *opcional* — se
  activa en Configuración):
  - **motor de puntuación combinado y configurable**: pHash 40 % + dHash 20 % +
    aHash 10 % + **histograma de color** 10 % + **hash de composición** (bloques
    16×16) 20 %. El histograma de color y el hash de composición son lo que
    **impide agrupar dos fotos distintas solo porque tienen colores parecidos**
    (p. ej. dos atardeceres diferentes),
  - **bandas de score configurables** (visualmente idéntico ≥ 98 %, muy similar
    ≥ 90 %, similar ≥ 78 %; por debajo no se agrupa) y **sensibilidad**
    Baja / Media / Alta / Personalizada,
  - categorías propias: `VISUALMENTE IDÉNTICO`, `REDIMENSIONADO` (misma imagen a
    otra resolución), `RECORTE` (una imagen es un encuadre parcial de otra —
    solo en sensibilidad Alta), `MUY SIMILAR`, `SIMILAR`,
  - tolera brillo / contraste / desenfoque / ruido / recompresión,
  - **agrupación N-aria** con **lista de relación** por archivo
    (`small 98.7 % · crop 93.4 %`) y **"Se agruparon porque: ✓ …"**,
  - **"Comparar en detalle"** con modos **Normal / Lado a lado / Diferencia /
    Superposición** (con slider de opacidad),
  - **Detección avanzada mediante IA** (embeddings visuales CLIP) — *opcional*,
    `pip install "pixmatch[ai]"`; si no está instalada se usan solo
    los métodos tradicionales.
- **Análisis de vídeo** (Fase 4, *opcional* — necesita **FFmpeg**):
  - detección automática de FFmpeg/ffprobe (PATH → `imageio-ffmpeg`), con
    **ruta configurable** y botón "Comprobar FFmpeg" en Configuración; si no
    hay FFmpeg la app lo dice y sigue con las imágenes,
  - vídeos con el **mismo SHA-256** → `ARCHIVO IDÉNTICO`,
  - metadatos (duración, resolución, códec, FPS, bitrate, audio, fecha) por
    ffprobe o, si no está, parseando `ffmpeg -i`,
  - **comparación escalonada de fotogramas**: se muestrean 5 fotogramas
    (5/25/50/75/95 %) de cada vídeo con duración parecida y se comparan sus
    **pHash** → categorías `MISMO CONTENIDO PROBABLE` / `MUY SIMILAR` /
    `SIMILAR` (nunca "idéntico" a partir de unos fotogramas),
  - un vídeo, su copia byte-idéntica **y** su versión recomprimida acaban en
    **un solo grupo** (la categoría refleja el enlace más débil),
  - miniatura de vídeo (un fotograma), y **"🎬 Comparar vídeos"**: fotograma
    representativo + metadatos + similitud estimada por cada vídeo.
- **Caché SQLite** con **análisis incremental** (Fase 2):
  - guarda hash, pixel-hash, hashes perceptuales, metadatos y datos de vídeo
    (fotogramas incluidos) por archivo,
  - un archivo cuyo tamaño y fecha no cambiaron **no se vuelve a procesar**,
  - botón **"Analizar"** = incremental; **"↻ Análisis completo"** = recalcula
    todo. Si la base de datos se corrompe, el análisis sigue sin caché.
- Vista de duplicados:
  - filtros (Todas / Imágenes / Videos / Archivo idéntico / Pixel idéntico /
    Similares / Sin revisar / Revisados / Marcados para eliminar),
  - orden (ahorro / nº duplicados / tamaño / **similitud** / nombre / ruta),
    búsqueda, "≥ N archivos por grupo",
  - navegación Anterior / Siguiente + lista lateral de grupos con su categoría,
  - comparación lado a lado con miniatura, nombre, ruta, tamaño, resolución,
    formato, modo de color, orientación EXIF, cámara, fechas, SHA-256,
    hash de píxeles, pHash y **% de similitud vs. la referencia del grupo**,
  - por archivo: **🔍 Ampliar**, Abrir archivo, Abrir carpeta, Copiar ruta y
    **🗑 Eliminar** (borra solo ese archivo, con confirmación e historial),
  - **visor ampliado**: zoom (rueda / +/−) y pan, ajustar / tamaño original,
    **◀ ▶ para recorrer todo el grupo** (o flechas del teclado) y decidir
    *Mantener / Marcar / Eliminar ahora* mientras miras el detalle; respeta la
    orientación EXIF **sin tocar el archivo**,
  - **recomendación no vinculante** de cuál conservar, con puntuación
    (`KEEP_SCORE`: resolución, tamaño, formato sin pérdidas, bitrate de vídeo,
    metadatos EXIF/cámara/fecha, antigüedad, nombre "copia", carpeta
    backup/temporal) y **porcentaje de confianza** — nunca preselecciona nada,
  - indicador en vivo del espacio que se liberaría y aviso si una decisión
    dejaría 0 copias.
- **Eliminación segura** (Fase 3):
  - checkbox "Mantener este archivo" + botones *Mantener seleccionados*,
    *Eliminar no seleccionados*, *Mantener todos*, *Ignorar grupo* — todos
    solo **marcan**; nada se borra hasta pulsar **"🗑 Eliminar archivos
    marcados…"** y **confirmar**,
  - por defecto los archivos se **mueven a la papelera del sistema**
    (`Send2Trash`, recuperable); la eliminación permanente es un modo separado
    con aviso en rojo,
  - el diálogo de confirmación lista los archivos, el espacio a liberar y los
    avisos (archivo que cambió desde el análisis, grupo que quedaría sin
    ninguna copia → casilla obligatoria),
  - re-verificación de cada archivo justo antes de borrarlo; un archivo que
    cambió de tamaño o desapareció **se omite** y se informa,
  - resumen post-operación ("Se movieron 37 archivos a la papelera. Espacio
    recuperado: 8.72 GB"),
  - **historial de operaciones** (Ayuda → Historial): fecha, archivo, acción,
    resultado — incluidos los fallos,
  - los archivos eliminados quedan visibles en gris ("ELIMINADO"); los grupos
    resueltos pasan al filtro "Resueltos".
- **Exportación** (Archivo → Exportar resultados): **CSV**, **JSON** y un
  **informe HTML autocontenido** con miniaturas embebidas, rutas, hashes,
  similitud y la decisión de cada archivo.
- Dashboard de resultados: totales del escaneo, archivos decodificados / con
  hash perceptual / reutilizados de caché, desglose por categoría, espacio
  potencial recuperable y archivos eliminados en la sesión.
- **Tema oscuro y claro** con contraste **WCAG AA** (el texto atenuado también),
  estados de botón hover/pressed/focus/disabled visibles, y botones que se
  deshabilitan cuando su acción no aplica.
- **Idioma** ES / EN (Configuración → General). Español es el idioma base;
  la traducción al inglés cubre la interfaz principal y se amplía fácilmente.
- Análisis en segundo plano con **Pausar / Reanudar / Cancelar** (la pausa
  detiene también las fases de decodificación); la interfaz nunca se congela.
- Atajos de teclado: `←`/`→` grupo anterior/siguiente, `M` mantener, `D`
  marcar para eliminar, `A` mantener todos, `I` ignorar, `Esc` volver.
- Log rotativo en disco, consultable desde **Configuración → Logs**.

### Categorías de coincidencia

Nunca se mezclan:

| Etiqueta | Confianza | Significado |
|---|---|---|
| 🟢 `ARCHIVO IDÉNTICO` | 100 % | Los bytes de los archivos son idénticos (mismo SHA-256). |
| 🔵 `PIXEL POR PIXEL IDÉNTICO` | 100 % | Bytes distintos, pero los píxeles decodificados (orientación EXIF normalizada) son exactamente iguales. |
| 🟦 `VISUALMENTE IDÉNTICO` | = score | Coincidencia perceptual casi perfecta, no confirmada a nivel de píxeles. |
| 🟦 `REDIMENSIONADO` | = score | La misma imagen guardada a otra resolución / compresión / formato. |
| 🟧 `RECORTE` | = score | Una imagen parece un encuadre parcial (recorte) de la otra. |
| 🟦 `MISMO CONTENIDO PROBABLE` | = score | Vídeo: los fotogramas muestreados coinciden (nunca es una certeza). |
| 🟡 `MUY SIMILAR` | = score | Probable variante (resolución/compresión/formato, ligeros ajustes). |
| 🟠 `SIMILAR` | = score | Misma escena u objeto con diferencias mayores; revísala en detalle. |
| 🔴 `DIFERENTE` | — | No es un duplicado (no se muestra como grupo). |

El *score* es la similitud calculada por los algoritmos, **no una verdad
matemática**: preferimos decir "posiblemente similar" a afirmar un duplicado
que no lo es.

### Pestaña "Renombrar por fecha"

Una segunda pestaña, independiente del análisis de duplicados, para **ordenar
la nomenclatura** de una biblioteca de fotos/vídeos:

- Escanea una carpeta **y sus subcarpetas**.
- Propone para cada archivo un nombre `AAAAMMDD_HHMMSS.ext`, con la fecha
  tomada de (en orden): **EXIF DateTimeOriginal → DateTimeDigitized →
  DateTime → metadatos de vídeo (ffprobe) → fecha de modificación del
  archivo** (siempre disponible, es el respaldo).
- **Vista previa en tabla** (carpeta · nombre actual · nombre nuevo · fecha ·
  origen de la fecha) con casilla por fila para incluir/excluir.
- Formato configurable (patrón `strftime`), prefijo opcional (p. ej. `IMG_`),
  incluir vídeos, extensión en minúsculas, `.jpeg`→`.jpg`.
- Los archivos **no se mueven de carpeta**: solo cambia el nombre.
- **Nunca sobrescribe** un archivo existente — si dos fotos comparten segundo
  (o el nombre ya está ocupado), se añade `_2`, `_3`… de forma determinista y
  por carpeta.
- Renombrado en **dos fases** (nombre temporal → final) para que intercambios
  y ciclos sean seguros; si algo falla, se revierte.
- Todo queda en el **historial** y hay **"Deshacer último renombrado"**.

---

## Instalación

Requiere **Python 3.11+** (probado con 3.14).

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### Formatos de imagen

De serie (vía Pillow): JPG/JPEG, PNG, WEBP, GIF, BMP, TIFF/TIF.

Opcionales (se activan solos si están instalados):

```bash
pip install pillow-heif        # HEIC / HEIF
pip install pillow-avif-plugin # AVIF
```

Si un formato no tiene decodificador, el archivo se sigue escaneando y
hasheando (la detección de duplicados **exactos** por SHA-256 no necesita
decodificar); solo se pierde la miniatura, los metadatos y la comparación de
píxeles / perceptual de ese archivo.

### FFmpeg (para el análisis de vídeo)

Formatos: MP4, M4V, MOV, AVI, MKV, WMV, WEBM, MPEG/MPG, 3GP, TS/M2TS, FLV…

1. **Recomendado**: instala FFmpeg del sistema y déjalo en el PATH.
   - Windows: `winget install Gyan.FFmpeg` (o [gyan.dev](https://www.gyan.dev/ffmpeg/builds/))
   - macOS: `brew install ffmpeg`
   - Linux: `sudo apt install ffmpeg` / `sudo dnf install ffmpeg`
2. O define la ruta manualmente en **Configuración → Escaneo → Ruta de FFmpeg**
   (acepta el ejecutable o su carpeta).
3. O, como atajo sin configurar nada: `pip install imageio-ffmpeg` (binario
   embebido, ~30 MB) — la app lo detecta automáticamente.

Sin FFmpeg, el análisis de vídeo se omite (la app lo avisa) pero los vídeos
**byte-idénticos** se siguen detectando por SHA-256.

### Configuración (Configuración → pestaña Escaneo)

- Analizar imágenes / **detectar pixel-por-pixel idénticas** (ambas activas por
  defecto).
- **Analizar imágenes similares** (motor combinado) — desactivado por defecto
  porque decodifica todas las imágenes.
  - **Sensibilidad de detección**: *Baja* (solo duplicados muy claros),
    *Media* (+ imágenes muy similares), *Alta* (+ variantes, recortes y
    modificaciones), *Personalizada*.
  - En *Personalizada*: editar las tres **bandas de score**, los **pesos** de
    las 5 señales, y activar la **detección de recortes**.
  - **Detección avanzada mediante IA** (embeddings CLIP) — opcional.
- **Analizar vídeos** (desactivado por defecto) + **Ruta de FFmpeg** +
  **Comprobar FFmpeg**.
- **Usar caché en disco** y su ruta.
- Seguir enlaces simbólicos, tamaño mínimo, extensiones y carpetas a excluir,
  nº de *workers*, tema.
- **Modo de eliminación por defecto** (pestaña General): papelera del sistema
  (recomendado) o permanente. La confirmación es **siempre** obligatoria.

---

## Ejecución

```bash
python main.py
# o, con una carpeta inicial:
python main.py "/ruta/a/mis fotos"
```

---

## Tests

```bash
pip install pytest
pytest -q
```

Los tests de interfaz se ejecutan sin pantalla:

```bash
QT_QPA_PLATFORM=offscreen pytest -q
```

**169 tests**. Los de vídeo se **saltan automáticamente** si no hay FFmpeg; los
de IA, si no hay backend de embeddings.

- **Fase 1** — SHA-256 vs `hashlib`, idénticos / distintos / vacíos, rutas
  Unicode, cancelación, `partial_signature`; escaneo recursivo, exclusiones,
  tamaño mínimo, ciclos de symlink, permisos; agrupación (grupos de N, orden,
  banderas de seguridad); pipeline (copias byte-idénticas agrupan; colisión de
  tamaño sin coincidencia real NO; pausa/reanudación; cancelación → parcial;
  archivo ilegible no aborta; orientación EXIF); `RunController`; humo de GUI.
- **Fase 2** — `test_perceptual.py` (hashes 64-bit, idénticas iguales,
  redimensionada cercana, distintas lejanas, corrupta→None, BK-tree,
  `classify_similarity` no mezcla categorías); `test_image_analyzer.py`
  (PNG/BMP = mismo digest, RGB vs RGBA opaco, orientación EXIF normalizada,
  corrupta→None, `difference_image`); `test_database.py` (upsert/get, validez,
  persistencia, purga, DB corrupta degrada sin excepción);
  `test_analysis_phase2.py` (PNG+BMP → `PIXEL_IDENTICAL`; byte-idénticas siguen
  `FILE_IDENTICAL`; copia redimensionada = similar solo con perceptual activo;
  incremental reutiliza caché; caché se invalida al cambiar el archivo;
  **la pausa bloquea la fase de decodificación**).
- **Fase 3** — `test_deletion.py` (permanente borra; papelera **mueve** a una
  papelera falsa; archivo inexistente/cambiado se omite; carpeta nunca se
  borra; preview marca grupos vacíos + avisos; cancelación corta el lote;
  rutas Unicode; error de permisos registrado; el historial registra los
  fallos); `test_history.py` (persistencia, clear, degradación);
  `test_export.py` (CSV, JSON round-trip, HTML autocontenido y escapado);
  resolución de grupos tras borrado; flujo de borrado en la UI.
- **Fase 4** — `test_ffmpeg.py` (detección no lanza, fallback ante ruta
  inválida, ruta de fichero / carpeta); `test_videos.py` (metadatos;
  hashes de fotogramas + comparación: recomprimido ≥90 %, distinto <80 %,
  sin solape → `None`; serialización; byte-idénticos → `FILE_IDENTICAL`;
  recomprimido → "mismo contenido probable"; no relacionados no agrupan;
  **byte-idéntico + recomprimido en un grupo de 3**; sin FFmpeg no rompe;
  incremental reutiliza la caché de vídeo).
- **Tema / accesibilidad** — `test_theme.py`: cambiar de tema **invierte** los
  colores de texto (también en widgets anidados ya creados); el texto de
  cuerpo, atenuado y deshabilitado **cumple contraste WCAG** en ambos temas
  (se calcula el ratio y falla si baja del umbral).
- **Recomendación** — `test_recommendation.py`: prefiere mayor resolución /
  bitrate / archivo más antiguo; penaliza nombres "copia" y carpetas de
  backup; confianza baja en grupos byte-idénticos sin señal.
- **i18n** — `test_i18n.py`: español es identidad; inglés traduce lo conocido
  y deja pasar lo desconocido (nunca vacío).
- **Fase 6 (similitud inteligente)** — `test_color_hist.py` (firma, brillo
  tolerado, paleta distinta = baja); `test_similarity_engine.py` (score
  combinado, pesos normalizados y respetados, ascenso a `RESIZED`, bandas,
  presets); `test_crop_detect.py` (recorte real detectado con región, no
  relacionados = None, recorte casi de marco completo ≠ recorte);
  `test_similarity_phase6.py` (**req. 57**: dos atardeceres distintos NO se
  agrupan; resize→`RESIZED`; crop→`RECORTE` solo en Alta; brillo/blur siguen
  detectándose; sensibilidad baja más estricta; toda coincidencia lleva
  etiqueta + confianza; IA opcional inofensiva sin backend).
- **Renombrar por fecha** — `test_renamer.py`: EXIF vs fecha de modificación;
  formato y prefijo; sufijos deterministas al chocar (mismo segundo o archivo
  ya existente); subcarpetas numeradas por separado; **intercambio A↔B seguro**
  (renombrado en dos fases); **nunca sobrescribe** (rollback); **deshacer**;
  rutas Unicode; filas desactivadas se saltan.

---

## Instalar como paquete (opcional)

```bash
pip install .            # instala el comando `pixmatch`
pip install ".[video]"   # + imageio-ffmpeg
pixmatch
```

---

## Generar un ejecutable

**PyInstaller**, one-folder, con FFmpeg embebido si `imageio-ffmpeg` está
instalado. Ejecuta el script de tu plataforma **desde la raíz del proyecto**:

```bash
packaging/build_linux.sh          # -> dist/PixMatch/PixMatch
packaging/build_macos.sh          # -> dist/PixMatch.app  (genera .icns)
packaging\build_windows.bat       # -> dist\PixMatch\PixMatch.exe
```

O directamente: `pyinstaller packaging/PixMatch.spec --noconfirm`.

Notas:
- El build de Linux está probado (`dist/PixMatch/PixMatch
  --version` arranca; FFmpeg queda empaquetado). Windows/macOS deben
  compilarse en su propia plataforma (no hay cross-compilación).
- macOS: app sin firmar; primera ejecución con clic derecho → Abrir, o
  `xattr -dr com.apple.quarantine dist/PixMatch.app`.
- El ejecutable pesa ~270 MB (PySide6 + FFmpeg).

---

## Rendimiento

Benchmark reproducible: `N=3000 python scripts/benchmark.py`.

Medido en el equipo de desarrollo (imágenes sintéticas 800×600, ~todos los
núcleos):

| Escenario | 3 000 imágenes | pico RSS |
|---|---|---|
| Detección exacta (SHA-256 + pixel) | ~4 s (~1,3 ms/img) | ~210 MB |
| + hash perceptual | ~8,5 s (~2,8 ms/img) | ~212 MB |
| Reanálisis **incremental** | ~0,1 s | — |

* El **pico de RSS se mantiene plano** al crecer N: hashes en *streaming*,
  imágenes decodificadas de una en una y liberadas, caché de miniaturas LRU
  (máx. 400). Sin fuga de memoria en la UI navegando cientos de grupos.
* Extrapolación lineal: **100 000 imágenes ≈ 2 min** (exacto) / **≈ 5 min**
  (+perceptual); luego el incremental es de segundos.
* Fotos reales (12 MP) decodifican más lento por imagen; la arquitectura
  (paralela + streaming + caché) es la misma.

---

## Arquitectura y decisiones técnicas

```
app/
  main.py                 punto de entrada (CLI --version/--help, logging, QApplication)
  config.py               AppConfig, persistida en JSON en la carpeta de config del SO
  i18n.py                 tr() + tabla ES->EN (español = idioma fuente)
  core/                   lógica pura, sin Qt (fácil de testear)
    scanner.py            escaneo recursivo iterativo; FileKind, ScanStats, ScanOptions
    hashing.py            hash_file (SHA-256 en streaming, cancelable), partial_signature
    metadata.py           lectura de metadatos de imagen y orientación EXIF (solo lectura)
    similarity.py         MatchCategory (7), classify_*, category_confidence
    perceptual.py         pHash / dHash / aHash / bHash, Hamming, BK-tree
    color_hist.py         firma de color HS (64 bins) + intersección
    image_features.py     compute_signatures(): todas las firmas en 1 decode
    similarity_engine.py  combined_score() ponderado + motivos + sensibilidad
    crop_detect.py        detect_crop(): NCC sobre rejilla, score + región
    embeddings.py         backend CLIP opcional (open_clip), encode/decode
    image_analyzer.py     pixel_digest, pixels_equal, difference_image
    ffmpeg.py             detect() (config / PATH / imageio-ffmpeg), install_hint
    video_analyzer.py     probe_video, extract_frame_hashes, compare_frame_hashes
    duplicate_groups.py   FileRecord, Decision, DuplicateGroup, AnalysisResult,
                          build_groups (capas pixel -> sha -> perceptual -> vídeo)
    analysis.py           run_analysis(): scan -> SHA-256 -> píxeles -> perceptual -> vídeo -> grupos
    recommendation.py     recommend_keep(): KEEP_SCORE ponderado, NO vinculante
    deletion_manager.py   build_preview + DeletionManager (papelera / permanente)
    renamer.py            renombrado por fecha (EXIF/mtime, sin sobrescribir, undo)
    export.py             export_csv / export_json / export_html
  database/               models.py + database.py (FileCacheDB) + history.py (OperationHistory)
  workers/
    analysis_worker.py    AnalysisWorker + AnalysisController (ciclo de vida del QThread)
    deletion_worker.py    DeletionRunner (hilo de fondo, señales en cola)
    rename_worker.py      RenamePreviewRunner + RenameApplyRunner
  ui/                     PySide6
    main_window.py (QTabWidget), duplicate_view.py, rename_view.py,
    image_viewer.py, advanced_compare.py, video_viewer.py,
    deletion_dialog.py, settings_dialog.py, theme.py
    widgets/file_card.py, widgets/thumbnail_loader.py
  utils/
    paths.py, logging_setup.py, control.py, file_utils.py, thumbnail_cache.py
packaging/    PixMatch.spec + build_{linux,macos,windows}.* + resources/icon.*
scripts/      benchmark.py
tests/
```

**Por qué estas decisiones:**

- **PySide6 (Qt)** — pedido en el enunciado; da temas, `QThread`, widgets
  maduros y multiplataforma real. Licencia LGPL.
- **Núcleo sin Qt** — `app/core/*` no importa PySide6. El pipeline
  (`run_analysis`) usa *callbacks*, y el `AnalysisWorker` los traduce a
  señales Qt. Así el 80% de la lógica se prueba sin arrancar una GUI.
- **Tema por `QPalette` + contraste AA** — los colores base van en una paleta
  clara/oscura (no en `QWidget { color }`, que no se limpia al cambiar de
  hoja). El texto atenuado usa un rol propio (`PlaceholderText`), separado del
  color de borde, para poder tener bordes sutiles sin sacrificar legibilidad;
  un test calcula el ratio WCAG y falla si baja de 4.5:1.
- **i18n ligero** — `tr()` con tabla ES→EN en memoria; el español es el
  idioma fuente (las claves son las cadenas en español), así una cadena sin
  traducir nunca sale vacía. Los combos guardan la clave canónica como
  `itemData` para que la lógica no dependa del idioma. Sin `gettext`/`.po`
  para no añadir toolchain de compilación de traducciones.
- **KEEP_SCORE ponderado** — la recomendación combina resolución, tamaño,
  formato, bitrate, metadatos, antigüedad, nombre y carpeta; se muestra con
  un % de confianza derivado del margen y **se limita** en grupos byte-
  idénticos sin señal (ahí "cuál conservar" es una preferencia, no calidad).
  Nunca preselecciona nada para borrar.
- **Escaneo iterativo con pila explícita** — árboles muy profundos no
  provocan `RecursionError`. Se registran `(st_dev, st_ino)` visitados para
  que ni siquiera seguir symlinks a propósito pueda crear un bucle infinito.
- **Tamaño → SHA-256, nunca solo tamaño** — el tamaño solo descarta trabajo;
  la evidencia de duplicado exacto es siempre el hash criptográfico completo.
  `partial_signature` (cabecera+cola+tamaño) es una optimización adicional y
  jamás se usa por sí sola para declarar un duplicado.
- **`FILE_IDENTICAL` ≠ `PIXEL_IDENTICAL` ≠ "similar"** — el código distingue
  "mismos bytes" (SHA-256), "misma imagen decodificada" (hash de píxeles) y
  "se parece" (hash perceptual). Un grupo solo es `ARCHIVO IDÉNTICO` si además
  de los píxeles coinciden los SHA-256. Una coincidencia perceptual perfecta
  que no se confirma a nivel de píxeles es `VISUALMENTE IDÉNTICO`, nunca
  `PIXEL IDÉNTICO`. Hay tests que fijan cada frontera.
- **Hash perceptual propio, sin numpy ni `imagehash`** — pHash necesita un DCT
  8×8; hacerlo a mano cuesta ~10k multiplicaciones por imagen (nada frente a
  decodificar) y evita arrastrar numpy/scipy al empaquetado. Un solo decode
  por imagen produce los tres hashes. Cambiar a numpy más adelante es un
  cambio local a `perceptual.py`.
- **Comparación de píxeles sin O(n²)** — en vez de comparar cada par, se
  calcula un SHA-256 de los píxeles normalizados (orientación EXIF aplicada en
  memoria, todo a RGBA) y se agrupa por igualdad de ese digest. Cada imagen se
  abre una vez y se libera enseguida; solo se decodifican los candidatos cuyas
  dimensiones coinciden con las de otra imagen.
- **Caché SQLite en un solo hilo** — `run_analysis` abre la base de datos en el
  hilo del worker, lee todo al principio y escribe todo al final; los hilos del
  pool nunca tocan SQLite. Si el fichero está corrupto, `FileCacheDB` degrada a
  no-op y el análisis continúa sin caché. La validez de una fila se decide por
  (tamaño, mtime); los valores `None` nunca se dan por buenos.
- **Similitud = motor combinado, no un solo algoritmo** — el score es una
  media **ponderada y configurable** de 5 señales (pHash, dHash, aHash,
  histograma de color, hash de composición 16×16). El histograma de color y el
  hash de composición son los que evitan el falso positivo clásico ("dos
  atardeceres distintos"); un test lo fija. La BK-tree solo hace el
  *prefiltrado* barato de pares candidatos; la decisión la toma el motor.
- **`RESIZED` y `RECORTE` como categorías propias** — no todo lo "similar" es
  lo mismo: una reducción de resolución (`RESIZED_DUPLICATE`) y un encuadre
  parcial (`CROPPED_SIMILAR`, detectado por correlación normalizada sobre una
  rejilla 32×32) se etiquetan aparte. El recorte solo se busca en sensibilidad
  Alta y sobre un conjunto acotado de pares.
- **Sensibilidad como presets** — Baja/Media/Alta traducen a bandas de score y
  a si se buscan recortes; Personalizada expone bandas, pesos y recortes. Los
  porcentajes **no son verdad matemática**: representan lo que calculan los
  algoritmos, y ante la duda se prefiere "posiblemente similar".
- **Embeddings visuales (CLIP) aislados y opcionales** — `core/embeddings.py`
  carga el modelo de forma perezosa; sin el extra `[ai]` instalado,
  `available()` es False y el resto del programa funciona igual. Cuando está,
  actúa como **voto de confirmación/rechazo** sobre aristas dudosas (rescata un
  borderline con "misma escena", descarta una coincidencia de hash con "escena
  distinta"). Se descartó hacerlo obligatorio por el peso de torch.
- **Vídeo: FFmpeg por subproceso**, como pide el enunciado (detectar,
  configurar ruta). `imageio-ffmpeg` es un *fallback opcional* (binario
  embebido). Se descartó **PyAV** (wheel grande, duplica libav). Los
  fotogramas se canalizan como PNG por *stdout* (sin ficheros temporales) y se
  comparan por pHash. Dos vídeos **nunca** se llaman "idénticos" a partir de
  unos fotogramas: el máximo veredicto es "mismo contenido probable".
- **Grupos de vídeo con dos tipos de arista** — un `union-find` une los vídeos
  por SHA-256 idéntico **y** por fotogramas cercanos, así el original, su copia
  exacta y su recompresión caen en un solo grupo cuya categoría es la del
  enlace más débil.
- **Tema por `QPalette`, no por `QWidget { color }`** — la regla de color en el
  stylesheet no se limpia al cambiar de hoja (dejaba texto blanco sobre fondo
  claro). Ahora los colores base van en una `QPalette` clara/oscura +
  `Fusion` + re-*polish* de los widgets; el stylesheet solo lleva estructura.
- **Hashing en paralelo** con `ThreadPoolExecutor` — la lectura de disco y
  `hashlib` liberan el GIL, así que varios hilos aceleran de verdad. El
  número de *workers* es configurable. La sumisión de tareas está acotada
  (no se encolan 100.000 futuros de golpe).
- **Pausa/cancelación cooperativa** (`RunController`) — sin matar hilos a la
  fuerza: el trabajo consulta `checkpoint()` con frecuencia, que bloquea
  mientras está en pausa y devuelve `False` al cancelar. Una cancelación
  devuelve un resultado **parcial** claramente marcado.
- **Miniaturas** — nunca se cargan imágenes a resolución completa en la lista;
  se generan miniaturas PNG con caché en memoria (LRU) y en disco (clave =
  ruta + tamaño + mtime), y se renderizan **fuera del hilo de la UI** con un
  `QThreadPool`. La orientación EXIF se normaliza solo para mostrar.
- **Rutas largas de Windows** — `file_utils.extended_path` antepone `\\?\`
  antes de abrir/`stat` archivos (hashing, metadatos, escaneo, caché).
- **Eliminación segura** — `Send2Trash` (papelera nativa en los tres SO) es el
  camino por defecto; la eliminación permanente es un modo explícito. Cada
  archivo se re-verifica justo antes de borrarlo (existe, es archivo, no ha
  cambiado de tamaño); lo dudoso se **omite y se informa**, nunca se fuerza.
  Todo intento — con éxito o no — se escribe en `OperationHistory`
  (SQLite propio, separado de la caché). Si `Send2Trash` falta en tiempo de
  ejecución, la app **no borra**: informa y sugiere instalarla.
- **Borrado fuera del hilo de UI** — `DeletionRunner` usa un
  `threading.Thread` (no `moveToThread`) y sus señales llegan a la UI por
  conexión en cola; así una unidad de red lenta o un lote grande no congela la
  ventana, y no hay riesgo de tocar widgets desde otro hilo.
- **El estado "read-only por defecto" se mantiene** — en la pestaña de
  duplicados todo *marca* decisiones; los únicos puntos que escriben en el
  disco del usuario son `DeletionManager` (borrar) y `renamer` (renombrar), y
  ambos exigen un diálogo de confirmación. Fuera de eso solo se escriben la
  config, el log, la caché y el historial, cada uno en su carpeta del sistema.
- **Renombrado en dos fases** — `renamer.apply_renames()` renombra primero
  todo a un nombre temporal único y luego al definitivo, así un intercambio
  `A.jpg`↔`B.jpg` o cualquier ciclo es seguro. Antes de cada paso comprueba que
  el destino no exista (nunca sobrescribe) y revierte si algo falla. Cada
  operación va al historial con `action="rename"` y `undo_renames()` la
  invierte. La fecha se toma de EXIF y, solo si no hay, de `st_mtime` (que
  siempre existe) — nunca se inventa una fecha.

**Sustituciones respecto al enunciado (documentadas):**

- `utils/logging.py` → `utils/logging_setup.py` para no ensombrecer el módulo
  `logging` de la librería estándar.
- El escaneo vive de momento dentro de `core/analysis.py` (como primera etapa
  del pipeline) en lugar de un `workers/scan_worker.py` separado; se separará si
  en fases futuras se necesita lanzarlo aislado.
- `Send2Trash` en lugar de reimplementar la papelera a mano (spec XDG en Linux,
  `IFileOperation` en Windows, Finder en macOS): la librería es MIT y sin
  dependencias transitivas.
- `database/cache.py` del enunciado no existe como tal: la lógica de caché
  está en `core/analysis.py` + `database/`. `utils/logging.py` es
  `logging_setup.py`. Todo lo demás del árbol propuesto está creado.
- `imageio-ffmpeg` es **opcional** (fallback): lo primario es el FFmpeg del
  sistema, como pide el enunciado.

---

## Licencia

Este proyecto se publica bajo la **licencia MIT** (ver [`LICENSE`](LICENSE)).

### Dependencias de terceros

| Paquete | Licencia | Notas |
|---|---|---|
| **PySide6 / Qt** | LGPL v3 | Enlace dinámico. Para distribuir **binarios** hay que permitir sustituir las librerías Qt (el empaquetado *one-folder* de PyInstaller ya lo permite) e incluir el texto de la LGPL. |
| **Pillow** | MIT-CMU (HPND) | Permisiva. |
| **Send2Trash** | BSD | Permisiva. |
| `imageio-ffmpeg` *(opcional)* | BSD, pero **empaqueta un binario de FFmpeg compilado con `--enable-gpl`** | El **código fuente** en GitHub no se ve afectado. Pero **no distribuyas un ejecutable que incluya ese FFmpeg** salvo bajo GPL: para releases binarias, usa el FFmpeg del sistema o un build LGPL y no empaquetes `imageio-ffmpeg`. |
| `open-clip-torch`, `torch` *(opcional, extra `[ai]`)* | MIT / BSD | Los pesos del modelo se descargan en tiempo de ejecución. |

**En resumen:** publicar el **código fuente** con licencia MIT no tiene ningún
problema. Al generar **ejecutables para distribuir**, ten en cuenta la LGPL de
Qt y, si empaquetas `imageio-ffmpeg`, la GPL de ese FFmpeg.
