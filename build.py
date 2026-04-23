"""
Build-Script für HRouting
=========================
Inkrementiert automatisch die Patch-Version in main.py,
baut dann mit PyInstaller eine einzelne .exe mit Versionssuffix.

Aufruf:
    python build.py
"""

import re
import subprocess
import sys
import shutil
from pathlib import Path

import markdown

ROOT = Path(__file__).parent
MAIN_PY = ROOT / "main.py"
DIST = ROOT / "dist"
ICON = ROOT / "assets" / "icon.svg"
WIKI = ROOT / "Wiki"


# ---------------------------------------------------------------------------
# 1. Version in main.py lesen & ggf. inkrementieren oder manuell setzen
# ---------------------------------------------------------------------------
def bump_version() -> str:
    text = MAIN_PY.read_text(encoding="utf-8")
    m = re.search(r'VERSION\s*=\s*"(\d+\.\d+\.\d+)"', text)
    if not m:
        print("FEHLER: VERSION = \"x.y.z\" nicht in main.py gefunden!")
        sys.exit(1)

    old_ver = m.group(1)
    major, minor, patch = old_ver.split(".")
    auto_ver = f"{major}.{minor}.{int(patch) + 1}"

    print(f"Aktuelle Version: {old_ver}")
    print(f"  [Enter] → automatisch inkrement: {auto_ver}")
    print(f"  [x.y.z] → manuelle Version eingeben")
    choice = input("Version: ").strip()

    if choice == "":
        new_ver = auto_ver
    elif re.fullmatch(r"\d+\.\d+\.\d+", choice):
        new_ver = choice
    else:
        print(f"FEHLER: Ungültiges Format '{choice}' – erwartet: x.y.z")
        sys.exit(1)

    new_text = text.replace(f'VERSION = "{old_ver}"', f'VERSION = "{new_ver}"')
    MAIN_PY.write_text(new_text, encoding="utf-8")
    print(f"Version: {old_ver} → {new_ver}")
    return new_ver


# ---------------------------------------------------------------------------
# 2. Splash Screen neu generieren (mit aktueller Version + Build-Datum)
# ---------------------------------------------------------------------------
def regenerate_splash():
    splash_script = ROOT / "generate_splash.py"
    if not splash_script.exists():
        print("WARNUNG: generate_splash.py nicht gefunden, überspringe Splash.")
        return
    print("Splash Screen generieren …")
    result = subprocess.run([sys.executable, str(splash_script)], cwd=str(ROOT))
    if result.returncode != 0:
        print("WARNUNG: Splash-Generierung fehlgeschlagen.")


# ---------------------------------------------------------------------------
# 3. PyInstaller aufrufen
# ---------------------------------------------------------------------------
def build_exe(version: str) -> Path:
    exe_name = f"HRouting_{version}"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", exe_name,
        # Nur benötigte PySide6-Module — spart ~200 MB
        "--hidden-import", "PySide6.QtCore",
        "--hidden-import", "PySide6.QtGui",
        "--hidden-import", "PySide6.QtWidgets",
        "--hidden-import", "PySide6.QtPrintSupport",
        "--hidden-import", "PySide6.QtSvg",
        "--hidden-import", "PySide6.QtNetwork",
        # Nicht benötigte Module ausschließen
        "--exclude-module", "PySide6.Qt3DAnimation",
        "--exclude-module", "PySide6.Qt3DCore",
        "--exclude-module", "PySide6.Qt3DExtras",
        "--exclude-module", "PySide6.Qt3DInput",
        "--exclude-module", "PySide6.Qt3DLogic",
        "--exclude-module", "PySide6.Qt3DRender",
        "--exclude-module", "PySide6.QtWebEngine",
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtWebEngineQuick",
        "--exclude-module", "PySide6.QtWebEngineWidgets",
        "--exclude-module", "PySide6.QtWebView",
        "--exclude-module", "PySide6.QtMultimedia",
        "--exclude-module", "PySide6.QtMultimediaWidgets",
        "--exclude-module", "PySide6.QtQuick",
        "--exclude-module", "PySide6.QtQuick3D",
        "--exclude-module", "PySide6.QtQml",
        "--exclude-module", "PySide6.QtCharts",
        "--exclude-module", "PySide6.QtDataVisualization",
        "--exclude-module", "PySide6.QtGraphs",
        "--exclude-module", "PySide6.QtGraphsWidgets",
        "--exclude-module", "PySide6.QtBluetooth",
        "--exclude-module", "PySide6.QtNfc",
        "--exclude-module", "PySide6.QtSensors",
        "--exclude-module", "PySide6.QtSerialBus",
        "--exclude-module", "PySide6.QtSerialPort",
        "--exclude-module", "PySide6.QtSpatialAudio",
        "--exclude-module", "PySide6.QtRemoteObjects",
        "--exclude-module", "PySide6.QtLocation",
        "--exclude-module", "PySide6.QtPositioning",
        "--exclude-module", "PySide6.QtHttpServer",
        "--exclude-module", "PySide6.QtPdf",
        "--exclude-module", "PySide6.QtPdfWidgets",
        "--exclude-module", "PySide6.QtSql",
        "--exclude-module", "PySide6.QtTest",
        "--exclude-module", "PySide6.QtDesigner",
        "--exclude-module", "PySide6.QtHelp",
        "--exclude-module", "PySide6.QtOpenGL",
        "--exclude-module", "PySide6.QtOpenGLWidgets",
        # Lokale Module
        "--hidden-import", "gui.main_window",
        "--hidden-import", "gui.canvas_widget",
        "--hidden-import", "gui.parameter_panel",
        "--hidden-import", "logic.svg_parser",
        "--hidden-import", "logic.heating_calc",
        # Daten einbetten
        "--add-data", f"assets{';' if sys.platform == 'win32' else ':'}assets",
    ]

    # Icon (SVG kann nicht direkt als Windows-Icon, nur wenn .ico vorhanden)
    ico = ROOT / "assets" / "icon.ico"
    if ico.exists():
        cmd += ["--icon", str(ico)]

    cmd.append(str(MAIN_PY))

    print(f"\n{'='*60}")
    print(f"Baue {exe_name}.exe …")
    print(f"{'='*60}\n")

    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"\nFEHLER: PyInstaller beendet mit Code {result.returncode}")
        sys.exit(result.returncode)

    exe_path = DIST / f"{exe_name}.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"\n✓ Fertig: {exe_path}  ({size_mb:.1f} MB)")
    else:
        print(f"\nWARNUNG: {exe_path} nicht gefunden!")

    # Aufräumen: .spec und build-Ordner entfernen
    spec = ROOT / f"{exe_name}.spec"
    build_dir = ROOT / "build"
    if spec.exists():
        spec.unlink()
    if build_dir.exists():
        shutil.rmtree(build_dir)

    return exe_path


# ---------------------------------------------------------------------------
# 4. Wiki → PDF (mit PySide6 QPrinter)
# ---------------------------------------------------------------------------
_WIKI_CSS = """
body {
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 5.5pt;
    line-height: 1.5;
    color: #222;
    max-width: 100%;
    margin: 0;
    padding: 0;
}
h1 { font-size: 10pt; margin-top: 14pt; border-bottom: 1px solid #333; padding-bottom: 2pt; page-break-after: avoid; }
h2 { font-size: 7.5pt; margin-top: 10pt; color: #1a5276; page-break-after: avoid; }
h3 { font-size: 6pt; margin-top: 7pt; color: #2e4053; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 4pt 0; }
th, td { border: 1px solid #bbb; padding: 2pt 4pt; text-align: left; }
th { background: #eaf2f8; font-weight: bold; }
code { background: #f4f4f4; padding: 1pt 2pt; border-radius: 2pt; font-size: 5pt; }
pre { background: #f4f4f4; padding: 4pt; border-radius: 2pt; font-size: 4.75pt; overflow-x: auto; }
blockquote { border-left: 3px solid #2e86c1; margin: 8pt 0; padding: 4pt 12pt; color: #555; background: #f9f9f9; }
hr { border: none; border-top: 1px solid #ccc; margin: 16pt 0; }
.page-break { page-break-before: always; }
"""

# Order of Wiki files for the PDF
_WIKI_ORDER = [
    "README.md",
    "01-Erste-Schritte.md",
    "02-Grundriss-und-Massstab.md",
    "03-Heizkreise.md",
    "04-Elektroplanung.md",
    "05-Heizkreisverteiler.md",
    "06-Ansicht-und-Raster.md",
    "07-Projekt-und-Export.md",
    "08-Tastatur-und-Maus.md",
    "09-Berechnungen.md",
]


def build_wiki_pdf(version: str) -> Path | None:
    """Convert all Wiki Markdown files into a single PDF."""
    if not WIKI.is_dir():
        print("WARNUNG: Wiki/-Ordner nicht gefunden, überspringe PDF.")
        return None

    DIST.mkdir(exist_ok=True)
    pdf_path = DIST / f"HRouting_{version}_Wiki.pdf"

    # Collect & convert Markdown → HTML
    md_ext = ["tables", "fenced_code", "toc", "sane_lists"]
    sections: list[str] = []

    for fname in _WIKI_ORDER:
        fpath = WIKI / fname
        if not fpath.exists():
            continue
        md_text = fpath.read_text(encoding="utf-8")
        html_section = markdown.markdown(md_text, extensions=md_ext)
        sections.append(html_section)

    if not sections:
        print("WARNUNG: Keine Wiki-Dateien gefunden.")
        return None

    # Join with page breaks between chapters
    body_html = '<div class="page-break"></div>\n'.join(sections)
    full_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>{_WIKI_CSS}</style>
</head><body>{body_html}</body></html>"""

    # Render to PDF via PySide6 (headless)
    print(f"\nWiki-PDF generieren: {pdf_path.name} …")

    from datetime import date
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import (
        QTextDocument, QFont, QPageSize, QPainter,
        QAbstractTextDocumentLayout,
    )
    from PySide6.QtPrintSupport import QPrinter
    from PySide6.QtCore import QMarginsF, QSizeF, QRectF, Qt

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    # ── Printer setup ──────────────────────────────────────────────
    printer = QPrinter(QPrinter.HighResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(str(pdf_path))
    printer.setPageSize(QPageSize(QPageSize.A4))
    # Margins in mm: left=20, top=20, right=15, bottom=20
    printer.setPageMargins(QMarginsF(20, 20, 15, 20))

    dpi = printer.resolution()  # 1200 for HighResolution

    # Page rect in device pixels and in points
    page_rect_px = printer.pageRect(QPrinter.DevicePixel)
    page_rect_pt = printer.pageRect(QPrinter.Point)
    scale = dpi / 72.0  # device-pixels per point

    footer_height_pt = 25.0  # ~9 mm in points
    content_height_pt = page_rect_pt.height() - footer_height_pt

    # ── Build QTextDocument in POINT coordinates ───────────────────
    doc = QTextDocument()
    doc.setDefaultFont(QFont("Segoe UI", 6))
    doc.setHtml(full_html)
    # Page size in points so font sizes (pt) are correct
    doc.setPageSize(QSizeF(page_rect_pt.width(), content_height_pt))

    total_pages = doc.pageCount()
    today_str = date.today().strftime("%d.%m.%Y")

    # ── Paint pages manually (content + footer) ───────────────────
    painter = QPainter()
    if not painter.begin(printer):
        print("FEHLER: Kann PDF-Painter nicht starten.")
        return None

    ctx = QAbstractTextDocumentLayout.PaintContext()

    for page_idx in range(total_pages):
        if page_idx > 0:
            printer.newPage()

        # ── Draw document content for this page ────────────────
        painter.save()
        # Scale from point-space to device-pixel-space
        painter.scale(scale, scale)
        # Clip to content area (in points)
        content_rect = QRectF(0, 0, page_rect_pt.width(), content_height_pt)
        painter.setClipRect(content_rect)
        # Translate to show current page
        painter.translate(0, -page_idx * content_height_pt)
        doc.documentLayout().draw(painter, ctx)
        painter.restore()

        # ── Draw footer: left = version+date, right = page number
        painter.save()
        painter.scale(scale, scale)
        footer_font = QFont("Segoe UI", 4)
        painter.setFont(footer_font)
        footer_y = content_height_pt + 8  # small gap

        left_text = f"HRouting v{version}  |  {today_str}"
        right_text = f"Seite {page_idx + 1} / {total_pages}"

        painter.drawText(
            QRectF(0, footer_y, page_rect_pt.width(), footer_height_pt),
            Qt.AlignLeft | Qt.AlignTop,
            left_text,
        )
        painter.drawText(
            QRectF(0, footer_y, page_rect_pt.width(), footer_height_pt),
            Qt.AlignRight | Qt.AlignTop,
            right_text,
        )
        painter.restore()

    painter.end()

    if pdf_path.exists():
        size_kb = pdf_path.stat().st_size / 1024
        print(f"✓ Wiki-PDF: {pdf_path}  ({size_kb:.0f} KB)")
    else:
        print("WARNUNG: Wiki-PDF wurde nicht erstellt.")

    return pdf_path


# ---------------------------------------------------------------------------
# 5. Dateiassoziation .hrp registrieren (optional)
# ---------------------------------------------------------------------------
def register_filetype(exe_path: Path):
    """Register .hrp file association with the built EXE."""
    if not exe_path.exists():
        return
    reg_script = ROOT / "register_filetype.py"
    if not reg_script.exists():
        return
    print(f"\n.hrp-Dateiassoziation registrieren …")
    result = subprocess.run(
        [sys.executable, str(reg_script), "install", str(exe_path)],
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        print("WARNUNG: Dateiassoziation konnte nicht registriert werden.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    ver = bump_version()
    regenerate_splash()
    exe = build_exe(ver)
    build_wiki_pdf(ver)
    register_filetype(exe)
