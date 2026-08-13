# HRouting – Fußbodenheizung und Kabel Planer
# Copyright (C) 2026 Konrad-Fabian Wittwer
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import sys
import ctypes
import re
from pathlib import Path
from typing import Any, cast

VERSION = "0.2.1"

# Windows: AppUserModelID muss VOR allen Qt-Imports gesetzt werden,
# damit die Taskleiste das App-Icon statt des Python-Icons zeigt.
_APP_ID = "HRouting.FBH.Planer"
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_APP_ID)
except Exception:
    pass

# PyInstaller: gebundelte Resourcen liegen in _MEIPASS
BASE_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))


def _parse_windows_open_command_exe(command: str) -> Path | None:
    command = (command or "").strip()
    if not command:
        return None
    if command.startswith('"'):
        end = command.find('"', 1)
        if end > 1:
            return Path(command[1:end])
    return Path(command.split()[0]) if command.split() else None


def _parse_hrouting_version_from_name(exe_name: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"HRouting_(\d+)\.(\d+)\.(\d+)\.exe", exe_name, re.IGNORECASE)
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
    )


def _should_update_association(current_exe: Path, registered_exe: Path | None) -> bool:
    if registered_exe is None:
        return True

    try:
        registered_resolved = registered_exe.resolve()
    except Exception:
        registered_resolved = registered_exe

    if str(registered_resolved).lower() == str(current_exe).lower():
        return False

    current_ver = _parse_hrouting_version_from_name(current_exe.name)
    registered_ver = _parse_hrouting_version_from_name(registered_resolved.name)
    if current_ver and registered_ver:
        return registered_ver < current_ver

    if "hrouting_" in registered_resolved.name.lower() and not registered_resolved.exists():
        return True

    return False


def _refresh_hrp_association_if_outdated():
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return

    try:
        import winreg
    except Exception:
        return

    extension = ".hrp"
    prog_id = "HRouting.Project"
    file_desc = "HRouting Projekt"

    try:
        current_exe = Path(sys.executable).resolve()
    except Exception:
        return

    registered_command = ""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Classes\{prog_id}\shell\open\command",
            0,
            winreg.KEY_READ,
        ) as key:
            registered_command, _ = winreg.QueryValueEx(key, "")
    except FileNotFoundError:
        pass
    except OSError:
        return

    registered_exe = _parse_windows_open_command_exe(registered_command)
    if not _should_update_association(current_exe, registered_exe):
        return

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{extension}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, prog_id)
            winreg.SetValueEx(key, "PerceivedType", 0, winreg.REG_SZ, "document")

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{extension}\OpenWithProgids") as key:
            winreg.SetValueEx(key, prog_id, 0, winreg.REG_SZ, "")

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, file_desc)

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id}\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{current_exe}" "%1"')

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id}\DefaultIcon") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{current_exe}",0')

        try:
            SHCNE_ASSOCCHANGED = 0x08000000
            SHCNF_IDLIST = 0x0000
            ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
        except Exception:
            pass
    except OSError:
        pass


def main():
    _refresh_hrp_association_if_outdated()

    # Parameter-Parsing: --mcp (HTTP) und --mcpstdio (Stdio)
    _enable_mcp_http = "--mcp" in sys.argv
    _enable_mcp_stdio = "--mcpstdio" in sys.argv
    # Oberfläche: neue Dock-/Workspace-UI ist Standard.
    _use_legacy_ui = "--legacy" in sys.argv
    if _use_legacy_ui:
        print("WARNUNG: --legacy ist entfernt. Starte neue UI.")

    # Argumente vor QApplication herausfiltern
    if _enable_mcp_http:
        sys.argv.remove("--mcp")
    if _enable_mcp_stdio:
        sys.argv.remove("--mcpstdio")
    for _flag in ("--legacy", "--new-ui"):
        while _flag in sys.argv:
            sys.argv.remove(_flag)

    # --- Schnellstart: nur minimale Qt-Imports für Splash ---
    from PySide6.QtWidgets import QApplication, QSplashScreen
    from PySide6.QtGui import QPixmap, QIcon
    from PySide6.QtCore import Qt

    app = QApplication(sys.argv)
    app.setApplicationName("HRouting − Fußbodenheizung und Kabel Planer")
    app.setApplicationVersion(VERSION)

    # App icon (.ico für Fenster + Taskleiste)
    ico_path = BASE_DIR / "assets" / "icon.ico"
    svg_path = BASE_DIR / "assets" / "icon.svg"
    if ico_path.exists():
        app.setWindowIcon(QIcon(str(ico_path)))
    elif svg_path.exists():
        app.setWindowIcon(QIcon(str(svg_path)))

    # Splash Screen
    splash_path = BASE_DIR / "assets" / "splash.png"
    splash = None
    if splash_path.exists():
        pixmap = QPixmap(str(splash_path))
        splash = QSplashScreen(pixmap, Qt.WindowType.WindowStaysOnTopHint)
        splash.show()
        app.processEvents()

    # --- Jetzt die schweren Imports (MainWindow, Canvas, etc.) ---
    from PySide6.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QLabel
    from PySide6.QtGui import QFont, QColor
    from PySide6.QtCore import Signal, QObject
    import logging as _logging

    # --- MCP Log-Fenster (wird nur bei --mcp verwendet) ---
    class _McpLogHandler(_logging.Handler, QObject):
        log_signal = Signal(str, str)  # (nachricht, level)

        def __init__(self):
            _logging.Handler.__init__(self)
            QObject.__init__(self)

        def emit(self, record):
            try:
                msg = self.format(record)
                self.log_signal.emit(msg, record.levelname)
            except Exception:
                pass

    class McpLogWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("HRouting – MCP Server Log")
            self.resize(750, 420)
            ico_path = BASE_DIR / "assets" / "icon.ico"
            svg_path = BASE_DIR / "assets" / "icon.svg"
            if ico_path.exists():
                from PySide6.QtGui import QIcon
                self.setWindowIcon(QIcon(str(ico_path)))
            elif svg_path.exists():
                from PySide6.QtGui import QIcon
                self.setWindowIcon(QIcon(str(svg_path)))

            layout = QVBoxLayout(self)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(6)

            url_label = QLabel(
                "<b>MCP Server läuft:</b> "
                "<a href='http://127.0.0.1:3274/mcp'>http://127.0.0.1:3274/mcp</a>"
            )
            url_label.setOpenExternalLinks(True)
            layout.addWidget(url_label)

            self._text = QTextEdit()
            self._text.setReadOnly(True)
            self._text.setFont(QFont("Courier New", 9))
            self._text.setStyleSheet(
                "QTextEdit { background:#1e1e1e; color:#d4d4d4; border:none; }"
            )
            layout.addWidget(self._text)

            self._handler = _McpLogHandler()
            self._handler.setFormatter(_logging.Formatter(
                "%(asctime)s  [%(levelname)-8s]  %(message)s",
                datefmt="%H:%M:%S",
            ))
            self._handler.log_signal.connect(self._append)
            _logging.getLogger("hrouting.mcp").addHandler(self._handler)
            _logging.getLogger("hrouting.mcp").setLevel(_logging.DEBUG)

        def _append(self, msg: str, level: str):
            colors = {
                "DEBUG":    "#9cdcfe",
                "INFO":     "#d4d4d4",
                "WARNING":  "#dcdcaa",
                "ERROR":    "#f44747",
                "CRITICAL": "#f44747",
            }
            color = colors.get(level, "#d4d4d4")
            import html
            self._text.append(
                f'<span style="color:{color}">{html.escape(msg)}</span>'
            )

        def closeEvent(self, event):
            _logging.getLogger("hrouting.mcp").removeHandler(self._handler)
            super().closeEvent(event)

    from gui.app_window import AppWindow
    window = AppWindow()

    # Open project file passed as command-line argument (file association)
    project_file = next(
        (
            Path(arg)
            for arg in sys.argv[1:]
            if not arg.startswith("-") and Path(arg).suffix.lower() in (".hrp", ".json")
        ),
        None,
    )
    if project_file is not None and project_file.exists():
        window.open_project_file(project_file)

    if ico_path.exists():
        window.setWindowIcon(QIcon(str(ico_path)))
    elif svg_path.exists():
        window.setWindowIcon(QIcon(str(svg_path)))

    # --- MCP-Server starten (nur mit --mcp oder --mcpstdio) ---
    _mcp_log_window = None
    _mcp_stdio_process = None
    
    if _enable_mcp_http:
        _mcp_log_window = McpLogWindow()
        _mcp_log_window.show()
        try:
            from mcp_server import start_mcp_server
            start_mcp_server(cast(Any, window))
        except Exception as e:
            import traceback
            _mcp_log_window._append(
                f"FEHLER beim Starten des MCP-Servers: {e}\n"
                + traceback.format_exc(),
                "ERROR",
            )
    
    if _enable_mcp_stdio:
        _mcp_log_window = McpLogWindow()
        _mcp_log_window._append("Starten des MCP Stdio-Servers...", "INFO")
        _mcp_log_window.show()
        try:
            import subprocess
            server_path = BASE_DIR / "mcp_server_stdio.py"
            _mcp_stdio_process = subprocess.Popen(
                [str(Path(sys.executable)), str(server_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            _mcp_log_window._append(
                f"MCP Stdio-Server läuft (PID: {_mcp_stdio_process.pid})",
                "INFO",
            )
        except Exception as e:
            import traceback
            _mcp_log_window._append(
                f"FEHLER beim Starten des MCP Stdio-Servers: {e}\n"
                + traceback.format_exc(),
                "ERROR",
            )

    # Log-Fenster schließen wenn Hauptfenster geschlossen wird
    if _mcp_log_window:
        _orig_close = window.closeEvent

        def _close_with_log(event):
            if _mcp_stdio_process:
                _mcp_stdio_process.terminate()
            _mcp_log_window.close()
            _orig_close(event)

        window.closeEvent = _close_with_log

    window.show()

    if splash:
        splash.finish(window)

    sys.exit(app.exec())

if __name__ == "__main__":
    main()