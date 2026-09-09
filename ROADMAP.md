# Arquitectura y decisiones técnicas

Documento de referencia para quien quiera entender o contribuir al proyecto.
Para *qué hace* la aplicación y cómo usarla, ver el [README](README.md).

---

## Principios de diseño

1. **Ningún cambio silencioso en los archivos del usuario.** El análisis de
   duplicados es de solo lectura. Borrar y renombrar exigen confirmación
   explícita, quedan en un historial y el renombrado se puede deshacer.
2. **Nunca "duplicado" sin explicar de qué tipo.** Cada coincidencia lleva una
   etiqueta específica (archivo idéntico / pixel idéntico / redimensionado /
   recorte / muy similar / …) y, para las similitudes, un porcentaje que
   representa lo que calculan los algoritmos, no una verdad matemática.
3. **Nunca agrupar por nombre, tamaño o resolución solos.** Siempre hay
   evidencia real (hash criptográfico, píxeles decodificados, hashes
   perceptuales, fotogramas).
4. **La interfaz nunca se congela.** Todo el trabajo pesado va en hilos; la UI
   solo recibe señales.
5. **Robustez.** Un archivo corrupto, bloqueado o sin permisos se registra y el
   análisis continúa. Los errores técnicos van a un log rotativo, no a la
   interfaz (`Configuración → Logs`).

---

## Mapa del código

```
app/
  main.py               entry point: --version/--help, logging, QApplication, icono, HiDPI
  config.py             AppConfig (JSON persistente en la carpeta de config del SO)
  i18n.py               tr() + tabla ES→EN (el español es el idioma fuente)

  core/                  lógica pura, sin Qt — se prueba sin arrancar la GUI
    scanner.py           escaneo recursivo iterativo; FileKind, ScanStats, ScanOptions
    hashing.py           hash_file (SHA-256 en streaming, cancelable), partial_signature
    metadata.py          read_image_info (EXIF, cámara, fecha), file_timestamps
    similarity.py        MatchCategory (7 categorías + colores), classify_*, category_confidence
    perceptual.py        pHash / dHash / aHash / bHash (composición 16×16), BK-tree
    color_hist.py        firma de color Hue-Saturación (64 bins) + intersección de histogramas
    image_features.py    compute_signatures(): todas las firmas de una imagen en un solo decode
    similarity_engine.py combined_score() ponderado + motivos + ascensos de categoría;
                         SimilarityBands; perfiles de sensibilidad (presets + personalizado)
    crop_detect.py       detect_crop(): correlación normalizada sobre rejilla, score + región
    embeddings.py        backend CLIP opcional (open_clip), carga perezosa, encode/decode
    image_analyzer.py    pixel_digest, pixels_equal, difference_image
    ffmpeg.py            detect() (ruta configurada / PATH / imageio-ffmpeg), install_hint
    video_analyzer.py    probe_video, extract_frame_hashes, compare_frame_hashes
    duplicate_groups.py  FileRecord, Decision, DuplicateGroup, AnalysisResult,
                         build_groups (capas: pixel → sha → similitud/recorte → vídeo)
    analysis.py          run_analysis(): pipeline escalonado + callbacks + caché
    recommendation.py    recommend_keep(): sugerencia NO vinculante (KEEP_SCORE ponderado)
    deletion_manager.py  build_preview / build_preview_for, DeletionManager (papelera / permanente)
    renamer.py           renombrado por fecha (EXIF / mtime), sin sobrescribir, dos fases, undo
    export.py            export_csv / export_json / export_html

  database/
    models.py            esquema SQL + CachedFile
    database.py          FileCacheDB (una conexión, lecturas al inicio, escrituras al final)
    history.py           OperationHistory (auditoría de borrados/renombrados, SQLite propio)

  workers/                adaptadores Qt: hilo de fondo + señales en cola
    analysis_worker.py   AnalysisWorker + AnalysisController (QThread)
    deletion_worker.py   DeletionRunner (threading.Thread)
    rename_worker.py     RenamePreviewRunner + RenameApplyRunner

  ui/                     PySide6
    main_window.py        QTabWidget: «Duplicados» + «Renombrar por fecha»; toolbar, atajos
    duplicate_view.py     navegador de grupos (filtros / orden / búsqueda / lado a lado)
    rename_view.py        tabla de vista previa de renombrado + aplicar + deshacer
    image_viewer.py       visor ampliado: zoom / pan, ◀▶ por el grupo, decisiones
    advanced_compare.py   "Comparar en detalle": Normal / Lado a lado / Diferencia / Superposición
    video_viewer.py       VideoCompareDialog (fotograma + metadatos + score)
    deletion_dialog.py    DeletionConfirmDialog + HistoryDialog
    settings_dialog.py    General / Escaneo / Exclusiones / Logs
    theme.py              QPalette claro/oscuro (contraste AA) + QSS de estructura
    widgets/
      file_card.py         tarjeta de archivo (miniatura + datos + decisión + acciones)
      thumbnail_loader.py  miniaturas fuera del hilo de UI (QThreadPool)

  utils/
    paths.py              carpetas de config/cache/data/logs por plataforma
    logging_setup.py      log rotativo
    control.py            RunController (pausa / reanudar / cancelar cooperativo)
    file_utils.py         tamaños legibles, rutas largas de Windows (\\?\), abrir archivo/carpeta
    thumbnail_cache.py    caché de miniaturas: memoria (LRU) + disco; imágenes y fotograma de vídeo

packaging/   PixMatch.spec (PyInstaller) + build_{linux,macos,windows}.* + resources/icon.*
scripts/     benchmark.py
tests/       ~170 tests con pytest (los de vídeo/IA se saltan si falta el binario/backend)
```

---

## El pipeline de análisis (escalonado)

`core/analysis.py :: run_analysis()` — cada etapa cara solo se ejecuta sobre
los candidatos que la anterior no descartó, y todo es *cache-aware*:

1. **Escaneo** — recorre carpeta + subcarpetas. Ignora symlinks de directorio
   (anti-ciclos, con control por `(dev, inode)`). Unicode y espacios nativos.
2. **Agrupación previa por tamaño** — solo una optimización; el tamaño nunca
   define un duplicado.
3. **SHA-256** — hash completo en streaming de los archivos que comparten
   tamaño → `ARCHIVO IDÉNTICO`.
4. **Comparación de píxeles** — decodifica los candidatos con **dimensiones
   coincidentes** (orientación EXIF normalizada en memoria), SHA-256 de los
   píxeles → `PIXEL IDÉNTICO` (distinto de `ARCHIVO IDÉNTICO`).
5. **Firmas de imagen** (opt-in) — pHash + dHash + aHash + bHash + histograma
   de color, en un solo decode por imagen.
6. **Embeddings visuales** (opt-in, extra `[ai]`) — CLIP, como voto de
   confirmación/rechazo en aristas dudosas.
7. **Detección de recortes** (solo sensibilidad Alta) — sobre pares con aspecto
   distinto y paleta parecida.
8. **Vídeo** (opt-in, necesita FFmpeg) — hash de archivo, metadatos por
   ffprobe/`ffmpeg -i`, y muestreo de 5 fotogramas + pHash.
9. **Agrupación** — `build_groups()` combina las capas con union-find.

La caché SQLite guarda todas las firmas por archivo; un reanálisis solo
recalcula lo que cambió de tamaño o fecha de modificación.

---

## Motor de similitud (`similarity_engine.py`)

`combined_score(a, b, weights)` = media ponderada de 5 señales, con pesos
configurables:

| Señal | Peso por defecto | Qué capta |
|---|---|---|
| pHash (DCT 32→8) | 40 % | estructura de frecuencias |
| dHash | 20 % | gradientes / bordes |
| aHash | 10 % | tono general |
| histograma de color HS | 10 % | paleta (ignora brillo) |
| bHash (bloques 16×16) | 20 % | composición espacial |

El histograma de color y el bHash son los que **evitan el falso positivo
clásico**: dos fotos distintas con colores parecidos (p. ej. dos atardeceres)
tienen pHash cercano pero composición y/o distribución de color distintas.

Bandas de score configurables → categoría; por debajo de la banda "similar" no
se agrupa. Presets de **sensibilidad** Baja / Media / Alta (Alta también activa
la detección de recortes); "Personalizada" expone bandas, pesos y recortes.

El motor también devuelve los **motivos** ("Se agruparon porque: ✓ …") y puede
*ascender* la categoría a `REDIMENSIONADO` (misma imagen, otra resolución) o
`RECORTE`.

---

## Decisiones técnicas

### Núcleo sin Qt
`app/core/*` no importa PySide6. El pipeline usa *callbacks*; los `*Worker`
los traducen a señales Qt. Así ~80 % de la lógica se prueba sin GUI.

### Hashes perceptuales propios, sin numpy ni `imagehash`
pHash necesita un DCT 8×8: hacerlo a mano cuesta ~10 k multiplicaciones por
imagen (nada frente a decodificar) y evita arrastrar numpy/scipy al
empaquetado. Un solo decode produce las 4 hashes + el histograma de color.
Cambiar a numpy más adelante sería un cambio local a `perceptual.py` /
`image_features.py`.

### Comparación de píxeles sin O(n²)
En vez de comparar cada par, se calcula un SHA-256 de los píxeles normalizados
(orientación EXIF en memoria, todo a RGBA) y se agrupa por igualdad de ese
digest. Cada imagen se abre una vez y se libera; solo se decodifican los
candidatos con dimensiones coincidentes.

### Tema por `QPalette`, no por `QWidget { color }`
La regla de color en el stylesheet de Qt no se limpia al cambiar de hoja
(dejaba texto blanco sobre fondo claro). Los colores base van en una paleta
clara/oscura + `Fusion` + re-*polish* de los widgets; el stylesheet solo lleva
estructura y resuelve colores con `palette(...)`. El texto atenuado usa un rol
propio, separado del color de borde, para poder tener bordes sutiles sin
sacrificar el contraste (un test calcula el ratio WCAG y falla si baja de
4.5 : 1).

### Caché e historial SQLite en un solo hilo
`run_analysis` abre la BD en el hilo del worker, lee todo al principio y
escribe todo al final; los hilos del pool nunca tocan SQLite. Si el fichero
está corrupto, `FileCacheDB` degrada a no-op y el análisis sigue sin caché. La
validez de una fila se decide por `(tamaño, mtime)`; los valores `None` nunca
se dan por buenos.

### Borrado y renombrado fuera del hilo de UI
`DeletionRunner` / `RenameApplyRunner` usan un `threading.Thread` (no
`moveToThread`) y sus señales llegan a la UI por conexión en cola. Un disco de
red lento o un lote grande no congela la ventana ni toca widgets desde otro
hilo.

### Eliminación segura
Modo **papelera** (`Send2Trash`, nativo en Windows/macOS/Linux) por defecto;
la eliminación permanente es un modo aparte y explícito. Si `Send2Trash` falta,
la app **no borra**: informa. Cada archivo se re-verifica justo antes de
borrarlo (existe, es archivo regular, tamaño sin cambios); lo dudoso se omite y
se informa. Todo va al historial.

### Renombrado en dos fases
`renamer.apply_renames()` renombra primero todo a un nombre temporal único y
luego al definitivo, así un intercambio `A↔B` o cualquier ciclo es seguro.
Comprueba que el destino no exista (nunca sobrescribe; añade sufijo `_2`, `_3`…
determinista y por carpeta) y revierte si algo falla. La fecha se toma de EXIF
y, solo si no hay, de `st_mtime` — nunca se inventa. `undo_renames()` invierte
la operación.

### Vídeo: FFmpeg por subproceso
`subprocess` a `ffmpeg`/`ffprobe` del sistema (detectado, con ruta
configurable). Se descartó **PyAV** (wheel grande, duplica libav).
`imageio-ffmpeg` es una **dependencia opcional** de conveniencia: si el usuario
no tiene FFmpeg en el PATH y lo instala, el análisis de vídeo funciona sin
configurar nada. Dos vídeos nunca se llaman "idénticos" a partir de unos
fotogramas: el máximo veredicto es "mismo contenido probable".

### i18n ligero
`tr()` con tabla ES→EN en memoria; el español es el idioma fuente (las claves
*son* las cadenas en español), así una cadena sin traducir nunca sale vacía.
Los combos guardan la clave canónica en `itemData` para que la lógica no
dependa del idioma. Sin `gettext`/`.po` para no añadir toolchain de compilación.

---

## Límites de escala y rendimiento

Benchmark: `N=3000 python scripts/benchmark.py`. En el equipo de desarrollo
(imágenes sintéticas 800×600, todos los núcleos):

| Escenario | 3 000 imágenes | pico RSS |
|---|---|---|
| Detección exacta (SHA-256 + pixel) | ~4 s | ~210 MB |
| + motor de similitud combinado | ~8,5 s | ~215 MB |
| Reanálisis incremental | ~0,1 s | — |

El **pico de RSS se mantiene plano** al crecer N: hashes en *streaming*,
imágenes decodificadas de una en una y liberadas, caché de miniaturas LRU
(máx. 400). Sin fuga de memoria en la UI navegando cientos de grupos.
Extrapolación lineal: 100 000 imágenes ≈ 2 min (exacto) / ≈ 5 min
(+ similitud); luego el incremental es de segundos.

---

## Limitaciones conocidas

- **Grupos solapados/anidados para imágenes.** Una familia de variantes
  (original + copia exacta + reducida + editada + recorte) puede quedar en 2-3
  grupos que comparten el original *por referencia* en vez de un único grupo.
  Cada grupo es coherente y etiquetado. (Para vídeo ya está resuelto: la capa
  de vídeo fusiona byte-idéntico + recomprimido en un grupo.)
- **Cobertura de traducción EN parcial.** El mecanismo está completo; faltan
  cadenas dinámicas y algunos diálogos. Se amplía añadiendo entradas a
  `app/i18n.py::_EN`.
- **`use_gpu`** en la configuración no tiene efecto (no hay ruta GPU; el flag
  se deja para el futuro).
- **`FileCacheDB.prune_missing()`** existe pero `run_analysis` no la llama
  automáticamente (riesgo si el *root* analizado es una subcarpeta). Falta
  decidir la política o exponer un "limpiar caché".
- **Empaquetado Windows/macOS.** Los scripts están listos y el build de Linux
  probado; el build real en Windows/macOS hay que ejecutarlo en su plataforma
  (no hay cross-compilación).

---

## Ideas para más adelante

- Grafo de similitud único totalmente fusionado (resuelve la limitación de
  arriba).
- `database/prune`: limpiar filas de la caché cuyos archivos ya no existen.
- Export que combine el estado actual + el historial de operaciones.
- i18n completo + más idiomas.
- Recorte proporcional (mismo aspecto) además del recorte con cambio de aspecto.
