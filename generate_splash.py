"""Splash-Screen PNG generieren mit Version, Build-Datum, Author & Copyright."""
import sys
import datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter, QColor, QFont, QLinearGradient, QPen
from PySide6.QtCore import Qt, QRectF
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).parent

app = QApplication(sys.argv)
renderer = QSvgRenderer(str(ROOT / "assets" / "icon.svg"))

# Version aus main.py lesen
main_text = (ROOT / "main.py").read_text(encoding="utf-8")
ver = main_text.split('VERSION = "')[1].split('"')[0]
build_date = datetime.datetime.now().strftime("%d.%m.%Y")
year = datetime.datetime.now().year

W, H = 480, 320
splash = QImage(W, H, QImage.Format_ARGB32)
splash.fill(Qt.transparent)
p = QPainter(splash)
p.setRenderHint(QPainter.Antialiasing)

# Hintergrund
grad = QLinearGradient(0, 0, 0, H)
grad.setColorAt(0, QColor("#1e3a5f"))
grad.setColorAt(1, QColor("#0d1f33"))
p.setBrush(grad)
p.setPen(QPen(QColor("#2a9d8f"), 3))
p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 18, 18)

# Icon
renderer.render(p, QRectF(30, 40, 120, 120))

# Titel
p.setPen(QColor("white"))
p.setFont(QFont("Arial", 28, QFont.Bold))
p.drawText(QRectF(170, 40, 290, 50), Qt.AlignLeft | Qt.AlignVCenter, "HRouting")

# Untertitel
p.setFont(QFont("Arial", 12))
p.setPen(QColor("#a0c4e8"))
p.drawText(QRectF(170, 95, 290, 22), Qt.AlignLeft | Qt.AlignVCenter,
           "Fußbodenheizung und Kabel Planer")

# Version + Build-Datum
p.setFont(QFont("Arial", 10))
p.setPen(QColor("#80a0b8"))
p.drawText(QRectF(170, 122, 290, 20), Qt.AlignLeft | Qt.AlignVCenter,
           f"Version {ver}  \u2022  Build {build_date}")

# Lade-Text
p.drawText(QRectF(170, 175, 290, 20), Qt.AlignLeft | Qt.AlignVCenter,
           "Anwendung wird geladen \u2026")

# Ladebalken
bar_x, bar_y, bar_w, bar_h = 170, 205, 270, 8
p.setPen(QPen(QColor("#2a9d8f"), 1))
p.setBrush(Qt.NoBrush)
p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 4, 4)
p.setBrush(QColor("#2a9d8f"))
p.setPen(Qt.NoPen)
p.drawRoundedRect(QRectF(bar_x + 1, bar_y + 1, bar_w * 0.6, bar_h - 2), 3, 3)

# Footer: Author + Copyright
p.setFont(QFont("Arial", 8))
p.setPen(QColor("#607080"))
p.drawText(QRectF(20, H - 40, W - 40, 16), Qt.AlignCenter,
           f"\u00a9 {year} Konrad-Fabian Wittwer  \u2022  GPL v3")

p.end()
splash.save(str(ROOT / "assets" / "splash.png"))
print(f"splash.png erzeugt (v{ver}, Build {build_date})")
