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
from pathlib import Path

VERSION = "0.1.3"

# Windows: AppUserModelID muss VOR allen Qt-Imports gesetzt werden,
# damit die Taskleiste das App-Icon statt des Python-Icons zeigt.
_APP_ID = "HRouting.FBH.Planer"
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_APP_ID)
except Exception:
    pass

# PyInstaller: gebundelte Resourcen liegen in _MEIPASS
BASE_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))


def main():
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
        splash = QSplashScreen(pixmap, Qt.WindowStaysOnTopHint)
        splash.show()
        app.processEvents()

    # --- Jetzt die schweren Imports (MainWindow, Canvas, etc.) ---
    from gui.main_window import MainWindow

    window = MainWindow()

    # Open project file passed as command-line argument (file association)
    if len(sys.argv) > 1:
        project_file = Path(sys.argv[1])
        if project_file.exists() and project_file.suffix in ('.hrp', '.json'):
            window._project_path = project_file
            window._load_project(project_file)

    if ico_path.exists():
        window.setWindowIcon(QIcon(str(ico_path)))
    elif svg_path.exists():
        window.setWindowIcon(QIcon(str(svg_path)))
    window.show()

    if splash:
        splash.finish(window)

    sys.exit(app.exec())

if __name__ == "__main__":
    main()