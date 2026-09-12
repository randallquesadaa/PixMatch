"""Minimal in-process translation.

Spanish is the source language: the keys in :data:`_EN` are the exact Spanish
strings used in the code. ``tr("…")`` returns the English string when the
language is ``"en"`` and the key is known, otherwise the original text
unchanged (so an untranslated string is never blank).

Switching language at runtime updates dynamically-built text immediately;
static widget labels created once take effect on the next launch (the Settings
dialog says so).
"""

from __future__ import annotations

_lang = "es"

_EN: dict[str, str] = {
    # --- window / toolbar / menu ---
    "📁  Seleccionar carpeta": "📁  Choose folder",
    "▶  Analizar": "▶  Analyze",
    "↻  Análisis completo": "↻  Full analysis",
    "⏸  Pausar": "⏸  Pause",
    "▶  Reanudar": "▶  Resume",
    "✖  Cancelar": "✖  Cancel",
    "⚙  Configuración": "⚙  Settings",
    "🌓  Tema": "🌓  Theme",
    "&Archivo": "&File",
    "A&yuda": "&Help",
    "Exportar resultados": "Export results",
    "CSV…": "CSV…",
    "JSON…": "JSON…",
    "Informe HTML…": "HTML report…",
    "Salir": "Quit",
    "Historial de operaciones…": "Operation history…",
    "Abrir carpeta de logs": "Open logs folder",
    "Acerca de": "About",
    "Ninguna carpeta seleccionada": "No folder selected",
    # --- welcome ---
    "Herramienta de solo lectura para encontrar imágenes y videos duplicados.\n"
    "Nunca se borra ni se modifica nada sin tu confirmación explícita.": "Read-only tool to find duplicate images and videos.\n"
    "Nothing is ever deleted or modified without your explicit confirmation.",
    "⬇  Arrastra una carpeta aquí para comenzar": "⬇  Drop a folder here to start",
    "Seleccionar carpeta": "Choose folder",
    # --- progress ---
    "Analizando…": "Analyzing…",
    "En pausa": "Paused",
    "Cancelando…": "Cancelling…",
    "Escaneando carpetas…": "Scanning folders…",
    "Calculando SHA-256…": "Computing SHA-256…",
    "Comparando píxeles…": "Comparing pixels…",
    "Calculando hashes perceptuales…": "Computing perceptual hashes…",
    "Leyendo metadatos de vídeo…": "Reading video metadata…",
    "Comparando fotogramas de vídeo…": "Comparing video frames…",
    "Agrupando duplicados…": "Grouping duplicates…",
    "Finalizando…": "Finishing…",
    "Pausar": "Pause",
    "Reanudar": "Resume",
    "Cancelar": "Cancel",
    # --- dashboard ---
    "Resultados": "Results",
    "Ver duplicados": "View duplicates",
    "Ver idénticos": "View identical",
    "Ver similares": "View similar",
    "Ver imágenes": "View images",
    "Ver videos": "View videos",
    "Analizar de nuevo": "Analyze again",
    "Archivos analizados": "Files analyzed",
    "Imágenes": "Images",
    "Videos": "Videos",
    "Otros archivos": "Other files",
    "Carpetas analizadas": "Folders scanned",
    "Tamaño total": "Total size",
    "Grupos de duplicados": "Duplicate groups",
    "Copias redundantes (dejando 1 por grupo)": "Redundant copies (keeping 1 per group)",
    "Tiempo de análisis": "Analysis time",
    "Eliminados en esta sesión": "Deleted this session",
    # --- duplicate view ---
    "Filtro": "Filter",
    "Ordenar por": "Sort by",
    "Buscar por nombre, extensión o ruta…": "Search by name, extension or path…",
    "Todas": "All",
    "Archivo idéntico": "File identical",
    "Pixel idéntico": "Pixel identical",
    "Redimensionados": "Resized",
    "Recortes": "Crops",
    "Similares": "Similar",
    "Sin revisar": "Unreviewed",
    "Revisados": "Reviewed",
    "Marcados para eliminar": "Marked for deletion",
    "Resueltos": "Resolved",
    "Ahorro potencial": "Potential savings",
    "Nº de duplicados": "Number of duplicates",
    "Tamaño": "Size",
    "Similitud": "Similarity",
    "Nombre": "Name",
    "Ruta": "Path",
    "Mantener seleccionados": "Keep selected",
    "Eliminar no seleccionados": "Delete unselected",
    "Mantener todos": "Keep all",
    "Ignorar grupo": "Ignore group",
    "Quitar decisiones": "Clear decisions",
    "🔍 Comparar en detalle": "🔍 Compare in detail",
    "Comparar en detalle": "Compare in detail",
    "Se agruparon porque:": "Grouped because:",
    "Relación:": "Relation:",
    "Sensibilidad de detección": "Detection sensitivity",
    "Baja": "Low",
    "Media": "Medium",
    "Alta": "High",
    "Personalizada": "Custom",
    "Normal": "Normal",
    "Lado a lado": "Side by side",
    "Diferencia": "Difference",
    "Superposición": "Overlay",
    "🎬 Comparar vídeos": "🎬 Compare videos",
    "🗑  Eliminar archivos marcados…": "🗑  Delete marked files…",
    "Nada marcado para eliminar.": "Nothing marked for deletion.",
    "◀ Anterior": "◀ Previous",
    "Siguiente ▶": "Next ▶",
    "Mantener este archivo": "Keep this file",
    "Abrir archivo": "Open file",
    "Abrir carpeta": "Open folder",
    "Copiar ruta": "Copy path",
    "Sin decidir": "Undecided",
    "Sin grupos que mostrar con el filtro actual": "No groups match the current filter",
    # --- deletion ---
    "⚠️  Confirmar eliminación": "⚠️  Confirm deletion",
    "Enviar a la papelera del sistema (recomendado, recuperable)": "Move to the system recycle bin (recommended, recoverable)",
    "Confirmar eliminación": "Confirm deletion",
    "Enviar a la papelera": "Move to recycle bin",
    "Eliminar permanentemente": "Delete permanently",
    "Archivos que se eliminarán:": "Files to be deleted:",
    "Avisos:": "Warnings:",
    "Eliminando": "Deleting",
    "Eliminando archivos…": "Deleting files…",
    "Eliminación finalizada": "Deletion finished",
    "Eliminación finalizada con avisos": "Deletion finished with warnings",
    # --- settings ---
    "Configuración": "Settings",
    "General": "General",
    "Escaneo": "Scanning",
    "Exclusiones": "Exclusions",
    "Logs": "Logs",
    "Tema": "Theme",
    "Idioma": "Language",
    "Guardar": "Save",
    "Comprobar FFmpeg": "Check FFmpeg",
    "Analizar imágenes": "Analyze images",
    "Analizar vídeos (hash de archivo + comparación de fotogramas, necesita FFmpeg)": "Analyze videos (file hash + frame comparison, needs FFmpeg)",
    "Umbral de similitud": "Similarity threshold",
    "El idioma se aplica del todo al reiniciar la aplicación.": "The language fully applies after restarting the app.",
    # --- import / organise tab ---
    "📥  Importar y organizar": "📥  Import & organise",
    "🔎  Analizar": "🔎  Analyze",
    "📁  Origen": "📁  Source",
    "📁  Biblioteca": "📁  Library",
    "Incluir vídeos": "Include videos",
    "Origen:": "Source:",
    "Biblioteca:": "Library:",
    "Formato:": "Format:",
    "Sin ubicación:": "No location:",
    "Sin ubicación": "No location",
    "Mostrar solo los nuevos": "Show only new files",
    "📥  Mover a la biblioteca…": "📥  Move to the library…",
    "↩  Deshacer última importación": "↩  Undo last import",
    "Confirmar importación": "Confirm import",
    "Mover": "Move",
    "Importación finalizada": "Import finished",
    "Importación con avisos": "Import finished with warnings",
    "Origen": "Source",
    "País": "Country",
    "Carpeta destino": "Destination folder",
    "Nombre destino": "Destination name",
    "Estado": "Status",
    # --- common buttons / labels ---
    "Cerrar": "Close",
    "Actualizar": "Refresh",
    "Aceptar": "OK",
    "Ajustar a ventana": "Fit to window",
    "Tamaño original": "Original size",
}

_TABLES = {"en": _EN}


def set_language(lang: str) -> None:
    global _lang
    _lang = (lang or "es").lower()


def current_language() -> str:
    return _lang


def tr(text: str) -> str:
    if _lang == "es":
        return text
    return _TABLES.get(_lang, {}).get(text, text)
