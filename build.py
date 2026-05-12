"""
Build-Script für HRouting
=========================
Automatisierter Build-Prozess:
  1. Version in main.py inkrementieren (oder manuell setzen)
  2. Splash Screen neu generieren
  3. PyInstaller → einzelne .exe kompilieren
  4. Wiki → PDF konvertieren
        5. WiX Toolset Installer bauen (WiX v7/v4 oder v3)

Aufruf:
    python build.py

Output:
    dist/HRouting_x.y.z.exe         (Standalone-EXE)
    dist/HRouting_x.y.z_Wiki.pdf    (Dokumentation)
    dist/setup_HRouting_x.y.z.msi   (WiX Installer)
"""

import re
import subprocess
import sys
import shutil
import os
import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
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
def _read_current_version() -> str:
    text = MAIN_PY.read_text(encoding="utf-8")
    m = re.search(r'VERSION\s*=\s*"(\d+\.\d+\.\d+)"', text)
    if not m:
        print("FEHLER: VERSION = \"x.y.z\" nicht in main.py gefunden!")
        sys.exit(1)

    return m.group(1)


def _write_version(old_ver: str, new_ver: str) -> None:
    text = MAIN_PY.read_text(encoding="utf-8")
    new_text = text.replace(f'VERSION = "{old_ver}"', f'VERSION = "{new_ver}"')
    MAIN_PY.write_text(new_text, encoding="utf-8")


def set_version(new_ver: str) -> str:
    if not re.fullmatch(r"\d+\.\d+\.\d+", new_ver):
        print(f"FEHLER: Ungültiges Format '{new_ver}' – erwartet: x.y.z")
        sys.exit(1)

    old_ver = _read_current_version()
    if old_ver != new_ver:
        _write_version(old_ver, new_ver)
        print(f"Version: {old_ver} → {new_ver}")
    else:
        print(f"Version unverändert: {new_ver}")
    return new_ver


def bump_version(interactive: bool = True) -> str:
    old_ver = _read_current_version()
    major, minor, patch = old_ver.split(".")
    auto_ver = f"{major}.{minor}.{int(patch) + 1}"

    if not interactive:
        new_ver = auto_ver
        _write_version(old_ver, new_ver)
        print(f"Version: {old_ver} → {new_ver}")
        return new_ver

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

    _write_version(old_ver, new_ver)
    print(f"Version: {old_ver} → {new_ver}")
    return new_ver


def _github_request(url: str, token: str, method: str = "GET", data: bytes | None = None,
                    content_type: str = "application/json") -> tuple[int, dict]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if data is not None:
        headers["Content-Type"] = content_type

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8") if resp.length != 0 else "{}"
            return resp.status, json.loads(body or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        parsed = {}
        if body:
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = {"message": body}
        return exc.code, parsed


def upload_msi_to_github_release(
    version: str,
    installer_path: Path,
    repo: str,
    token: str,
    tag: str | None = None,
    release_name: str | None = None,
) -> None:
    if not installer_path.exists():
        print(f"FEHLER: MSI nicht gefunden: {installer_path}")
        sys.exit(1)

    tag_name = tag or f"v{version}"
    rel_name = release_name or f"HRouting v{version}"
    api_base = f"https://api.github.com/repos/{repo}"

    print(f"\nGitHub Release vorbereiten: {repo} / {tag_name}")
    status, release_data = _github_request(
        f"{api_base}/releases/tags/{urllib.parse.quote(tag_name, safe='')}",
        token,
    )

    if status == 404:
        payload = {
            "tag_name": tag_name,
            "name": rel_name,
            "draft": False,
            "prerelease": False,
            "generate_release_notes": True,
        }
        status, release_data = _github_request(
            f"{api_base}/releases",
            token,
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
        )
        if status not in (200, 201):
            print(f"FEHLER: Release konnte nicht erstellt werden ({status}): {release_data}")
            sys.exit(1)
    elif status not in (200, 201):
        print(f"FEHLER: Release konnte nicht geladen werden ({status}): {release_data}")
        sys.exit(1)

    release_id = release_data.get("id")
    upload_url_tpl = release_data.get("upload_url", "")
    upload_url = upload_url_tpl.split("{")[0] if upload_url_tpl else ""
    if not release_id or not upload_url:
        print("FEHLER: Ungültige Release-Antwort von GitHub.")
        sys.exit(1)

    asset_name = installer_path.name
    for asset in release_data.get("assets", []):
        if asset.get("name") == asset_name and asset.get("id"):
            del_status, del_resp = _github_request(
                f"{api_base}/releases/assets/{asset['id']}",
                token,
                method="DELETE",
            )
            if del_status not in (200, 204):
                print(f"FEHLER: Bestehendes Asset konnte nicht gelöscht werden ({del_status}): {del_resp}")
                sys.exit(1)

    with installer_path.open("rb") as f:
        msi_data = f.read()

    upload_target = f"{upload_url}?name={urllib.parse.quote(asset_name)}"
    up_status, up_resp = _github_request(
        upload_target,
        token,
        method="POST",
        data=msi_data,
        content_type="application/x-msi",
    )
    if up_status not in (200, 201):
        print(f"FEHLER: MSI-Upload fehlgeschlagen ({up_status}): {up_resp}")
        sys.exit(1)

    print(f"✓ MSI als Release-Asset hochgeladen: {asset_name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HRouting Build-Script")
    parser.add_argument("--version", help="Setzt explizite Version x.y.z in main.py")
    parser.add_argument("--no-bump", action="store_true", help="Verwendet aktuelle Version ohne Änderung")
    parser.add_argument("--non-interactive", action="store_true", help="Kein Prompt; Auto-Inkrement")
    parser.add_argument("--github-release", action="store_true", help="MSI als GitHub Release Asset hochladen")
    parser.add_argument("--github-repo", help="Repo owner/name (default: env GITHUB_REPOSITORY)")
    parser.add_argument("--github-token", help="GitHub Token (default: env GITHUB_TOKEN)")
    parser.add_argument("--release-tag", help="Release Tag (default: v<version>)")
    parser.add_argument("--release-name", help="Release Name (default: HRouting v<version>)")
    return parser.parse_args()


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
        "--hidden-import", "mcp_server",
        "--hidden-import", "validate_hrp",
        # MCP-Server-Abhängigkeiten (optional zur Laufzeit)
        "--hidden-import", "mcp",
        "--hidden-import", "mcp.server",
        "--hidden-import", "mcp.server.fastmcp",
        "--hidden-import", "uvicorn",
        "--hidden-import", "uvicorn.config",
        "--hidden-import", "uvicorn.main",
        "--hidden-import", "uvicorn.protocols",
        "--hidden-import", "uvicorn.protocols.http",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.http.h11_impl",
        "--hidden-import", "uvicorn.protocols.http.httptools_impl",
        "--hidden-import", "uvicorn.protocols.websockets",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "uvicorn.lifespan.off",
        "--hidden-import", "uvicorn.loops",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "httptools",
        "--hidden-import", "h11",
        "--hidden-import", "starlette",
        "--hidden-import", "starlette.applications",
        "--hidden-import", "starlette.routing",
        "--hidden-import", "starlette.responses",
        "--hidden-import", "starlette.requests",
        "--hidden-import", "starlette.middleware",
        "--hidden-import", "anyio",
        "--hidden-import", "anyio._backends",
        "--hidden-import", "anyio._backends._asyncio",
        "--hidden-import", "sniffio",
        "--hidden-import", "pydantic",
        "--hidden-import", "pydantic_settings",
        "--hidden-import", "sse_starlette",
        "--hidden-import", "jsonschema",
        "--hidden-import", "jsonschema.validators",
        "--hidden-import", "jsonschema._format",
        # Daten einbetten
        "--add-data", f"assets{';' if sys.platform == 'win32' else ':'}assets",
        "--add-data", f"icons{';' if sys.platform == 'win32' else ':'}icons",
        "--add-data", f"hrp_schema.json{';' if sys.platform == 'win32' else ':'}.",
        "--add-data", f".github{';' if sys.platform == 'win32' else ':'}.github",
        "--add-data", f"validate_hrp.py{';' if sys.platform == 'win32' else ':'}.",
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
# 5. WiX Toolset Installer bauen
# ---------------------------------------------------------------------------
def build_installer(version: str, exe_path: Path) -> Path | None:
    """
    Build a WiX Toolset MSI installer around the PyInstaller EXE.

    Unterstützt WiX v7/v4 (wix build) und WiX v3 (candle/light).
    """
    wxs_script = ROOT / "HRouting.wxs"
    if not wxs_script.exists():
        print(f"\nFEHLER: {wxs_script} nicht gefunden!")
        return None

    if not exe_path.exists():
        print(f"\nFEHLER: PyInstaller EXE nicht gefunden: {exe_path}")
        return None

    wiki_pdf = DIST / f"HRouting_{version}_Wiki.pdf"
    include_wiki = "1" if wiki_pdf.exists() else "0"
    installer_path = DIST / f"setup_HRouting_{version}.msi"

    print(f"\n{'='*60}")
    print("Baue Windows Installer (WiX Toolset) …")
    print(f"  Version: {version}")
    print(f"  Quelle: {exe_path.name}")
    print(f"  Wiki-PDF: {'ja' if include_wiki == '1' else 'nein'}")
    print(f"{'='*60}\n")

    wix_v4_cmd = [
        "wix",
        "build",
        "-nologo",
        "-arch", "x64",
        "-d", f"Version={version}",
        "-d", f"ExeName={exe_path.name}",
        "-d", f"WikiPdfName={wiki_pdf.name}",
        "-d", f"IncludeWiki={include_wiki}",
        "-o", str(installer_path),
        str(wxs_script),
    ]

    used_tool = None
    result = None
    wix_requires_eula = False
    wix_build_cmd = list(wix_v4_cmd)

    try:
        probe = subprocess.run(
            ["wix", "--version"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        if probe.returncode == 0:
            version_text = (probe.stdout or probe.stderr or "").strip()
            m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", version_text)
            if m and int(m.group(1)) >= 7:
                wix_requires_eula = True
                wix_build_cmd += ["-acceptEula", "wix7"]

            used_tool = "WiX v7" if wix_requires_eula else "WiX v4"
            result = subprocess.run(wix_build_cmd, cwd=str(ROOT))

            if result.returncode != 0 and wix_requires_eula:
                accept_result = subprocess.run(
                    ["wix", "eula", "accept", "wix7"],
                    cwd=str(ROOT),
                )
                if accept_result.returncode == 0:
                    print("Hinweis: WiX v7 EULA wurde akzeptiert – starte Build erneut …")
                    result = subprocess.run(wix_build_cmd, cwd=str(ROOT))
    except FileNotFoundError:
        pass

    if used_tool is None:
        try:
            candle_probe = subprocess.run(
                ["candle", "-?"],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
            )
            light_probe = subprocess.run(
                ["light", "-?"],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
            )

            if candle_probe.returncode == 0 and light_probe.returncode == 0:
                used_tool = "WiX v3"
                wixobj = DIST / f"HRouting_{version}.wixobj"
                candle_cmd = [
                    "candle",
                    "-nologo",
                    "-arch", "x64",
                    f"-dVersion={version}",
                    f"-dExeName={exe_path.name}",
                    f"-dWikiPdfName={wiki_pdf.name}",
                    f"-dIncludeWiki={include_wiki}",
                    "-out", str(wixobj),
                    str(wxs_script),
                ]
                candle_result = subprocess.run(candle_cmd, cwd=str(ROOT))
                if candle_result.returncode != 0:
                    print(f"\nFEHLER: candle beendet mit Code {candle_result.returncode}")
                    return None

                light_cmd = [
                    "light",
                    "-nologo",
                    "-sice:ICE61",
                    "-out", str(installer_path),
                    str(wixobj),
                ]
                result = subprocess.run(light_cmd, cwd=str(ROOT))
                if wixobj.exists():
                    wixobj.unlink()
        except FileNotFoundError:
            pass

    if used_tool is None or result is None:
        print("\n⚠️  WARNUNG: WiX Toolset nicht gefunden.")
        print("   Installer wird NICHT erstellt.")
        print("\n   Bitte installieren Sie WiX Toolset:")
        print("   - WiX v4: https://wixtoolset.org/releases/")
        print("   - oder WiX v3: https://github.com/wixtoolset/wix3/releases")
        return None

    if result.returncode != 0:
        print(f"\nFEHLER: {used_tool} Build fehlgeschlagen mit Code {result.returncode}")
        return None

    if installer_path.exists():
        size_mb = installer_path.stat().st_size / (1024 * 1024)
        print(f"\n✓ Installer fertig: {installer_path}")
        print(f"  Tool: {used_tool}")
        print(f"  Größe: {size_mb:.1f} MB")
        return installer_path

    print(f"\nWARNUNG: Installer {installer_path} nicht gefunden!")
    return None


# ---------------------------------------------------------------------------
# 6. Dateiassoziation .hrp registrieren (optional)
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
    args = parse_args()

    if args.version and args.no_bump:
        print("FEHLER: --version und --no-bump können nicht kombiniert werden.")
        sys.exit(2)

    if args.version:
        ver = set_version(args.version)
    elif args.no_bump:
        ver = _read_current_version()
        print(f"Version unverändert: {ver}")
    else:
        ver = bump_version(interactive=not args.non_interactive)

    regenerate_splash()
    exe = build_exe(ver)
    build_wiki_pdf(ver)
    installer_result = build_installer(ver, exe)

    if args.github_release:
        if installer_result is None:
            print("FEHLER: Kein Installer erzeugt – Upload übersprungen.")
            sys.exit(1)

        repo = args.github_repo or os.environ.get("GITHUB_REPOSITORY", "")
        token = args.github_token or os.environ.get("GITHUB_TOKEN", "")
        if not repo:
            print("FEHLER: GitHub-Repo fehlt. Nutze --github-repo oder GITHUB_REPOSITORY.")
            sys.exit(1)
        if not token:
            print("FEHLER: GitHub-Token fehlt. Nutze --github-token oder GITHUB_TOKEN.")
            sys.exit(1)

        upload_msi_to_github_release(
            version=ver,
            installer_path=installer_result,
            repo=repo,
            token=token,
            tag=args.release_tag,
            release_name=args.release_name,
        )
    
    # Only register filetype for the PyInstaller EXE, not the installer
    # (The installer handles file association via Registry integration)
    # register_filetype(exe)
