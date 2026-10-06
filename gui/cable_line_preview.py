"""Cable line samples shared by overview tables and PDF exports."""

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPen


class CableLineSample(str):
    def __new__(cls, text, color="#ff9800", stroke_width=2.0, line_style="solid"):
        instance = super().__new__(cls, text)
        instance.color = str(color or "#ff9800")
        instance.stroke_width = max(0.5, min(10.0, float(stroke_width)))
        instance.line_style = str(line_style or "solid")
        return instance


def draw_cable_line_sample(painter: QPainter, rect: QRectF, sample: CableLineSample) -> None:
    styles = {
        "solid": Qt.SolidLine,
        "dash": Qt.DashLine,
        "dot": Qt.DotLine,
        "dashdot": Qt.DashDotLine,
    }
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(sample.color), sample.stroke_width, styles.get(sample.line_style, Qt.SolidLine))
    pen.setCapStyle(Qt.FlatCap)
    painter.setPen(pen)
    painter.drawLine(rect.left(), rect.center().y(), rect.right(), rect.center().y())
    painter.restore()