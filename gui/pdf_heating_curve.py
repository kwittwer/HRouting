"""Snapshot-based heating curve data and vector PDF rendering."""

from copy import deepcopy

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPen, QTextLayout, QTextOption
from PySide6.QtWidgets import QApplication

from gui.heating_curve_analysis_window import PlotView, heating_curve_color
from logic.heating_curve_analysis import default_config, individual_curve_steps
from model.computed import heating_length_overview


def prepare_heating_curve_pages(document, pages, cancelled=lambda: False):
    curve_pages = [page for page in pages if page.get("type") == "heating_curve" and page.get("enabled", True)]
    if not curve_pages:
        return {}
    config = default_config(float(document.settings.get("t_norm_outdoor", -12)))
    config.update(deepcopy(document.settings.get("heating_curve_analysis", {})))
    all_rows, supply, return_temp = heating_length_overview(document)
    selected_ids = {key for page in curve_pages for key in page.get("circuit_ids", [])}
    rows = deepcopy([row for row in all_rows if row["id"] in selected_ids])
    results, error = {}, ""
    try:
        steps = individual_curve_steps(rows, config["circuits"], config["outdoor"], supply - return_temp, config["heating_limit"])
        count = 0
        while True:
            try:
                next(steps)
                count += 1
                if count % 100 == 0:
                    QApplication.processEvents()
                    if cancelled():
                        steps.close()
                        raise InterruptedError("PDF-Export abgebrochen.")
            except StopIteration as finished:
                results = finished.value
                break
    except (ValueError, TypeError, KeyError, OverflowError) as problem:
        error = str(problem)
    colors = {row["id"]: heating_curve_color(index) for index, row in enumerate(all_rows)}
    names = {row["id"]: str(row["name"]) for row in all_rows}
    payloads = {}
    for page in curve_pages:
        series, entries = [], []
        for key in dict.fromkeys(page.get("circuit_ids", [])):
            color = colors.get(key, "#747474")
            label = f"{key} {names[key]}" if key in names else f"{key}: Heizkreis nicht vorhanden"
            curve = results.get(key, [])
            if curve:
                series.append((label, color, [(point["outdoor"], point["supply"]) for point in curve]))
                statuses = sorted({point["status"] for point in curve if point["status"] not in ("OK", "Heizung aus")})
                if statuses:
                    label += " | " + "; ".join(statuses)
                elif not any(point["supply"] is not None for point in curve):
                    label += " | Heizung aus"
            elif key in names:
                label += " | " + (error or "Keine Kurvenpunkte")
            entries.append((label, color))
        payloads[page["id"]] = {
            "series": series, "entries": entries,
            "note": error or ("Keine Heizkreise ausgewaehlt." if not entries else ""),
            "outdoor": config["outdoor"], "spread": supply - return_temp,
            "heating_limit": config["heating_limit"],
        }
    return payloads


def curve_page_layout(painter, content_rect, payload):
    font = QFont("Arial", 9)
    metrics = QFontMetricsF(font, painter.device())
    line = metrics.height()
    chart = QRectF(content_rect.x(), content_rect.y() + 3 * line,
                   content_rect.width(), content_rect.height() * 0.57)
    legend_top = chart.bottom() + line * 0.4
    available = max(line * 2, content_rect.bottom() - legend_top)
    column_width = content_rect.width() / 2
    marker_width = line * 1.6
    chunks, current, y_offset = [], [], 0.0
    entries = []
    max_lines = max(1, int((available - line * 0.35) / metrics.lineSpacing()))
    for name, color in payload["entries"]:
        text_layout = QTextLayout(name, font, painter.device())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        text_layout.setTextOption(option)
        text_layout.beginLayout()
        lines = []
        while True:
            text_line = text_layout.createLine()
            if not text_line.isValid():
                break
            text_line.setLineWidth(column_width - marker_width - line)
            lines.append(name[text_line.textStart():text_line.textStart() + text_line.textLength()])
        text_layout.endLayout()
        for offset in range(0, max(1, len(lines)), max_lines):
            entries.append(("\n".join(lines[offset:offset + max_lines]), color))
    for offset in range(0, len(entries), 2):
        pair = entries[offset:offset + 2]
        heights = [max(line, metrics.boundingRect(QRectF(0, 0, column_width - marker_width - line, 100000),
                    Qt.TextWordWrap, name).height()) for name, _ in pair]
        row_height = max(heights) + line * 0.35
        if current and y_offset + row_height > available:
            chunks.append(current)
            current, y_offset = [], 0.0
        for column, ((name, color), height) in enumerate(zip(pair, heights)):
            current.append((name, color, QRectF(content_rect.x() + column * column_width,
                                               legend_top + y_offset, column_width, height)))
        y_offset += row_height
    chunks.append(current)
    return chart, chunks


def draw_heating_curve_sheet(painter, content_rect, payload, chart, legend):
    painter.save()
    plot = None
    try:
        painter.setPen(Qt.black)
        font = QFont("Arial", 9)
        painter.setFont(font)
        line = QFontMetricsF(font, painter.device()).height()
        outside = payload["outdoor"]
        info = (f"Aussen: {outside[0]:g} bis {outside[1]:g} C, Schritt {outside[2]:g} K | "
                f"Projektspreizung: {payload['spread']:g} K | Heizgrenze: {payload['heating_limit']:g} C")
        painter.drawText(QRectF(content_rect.x(), content_rect.y(), content_rect.width(), line * 1.3), Qt.AlignLeft, info)
        painter.drawText(QRectF(content_rect.x(), content_rect.y() + line * 1.3, content_rect.width(), line * 1.3),
                         Qt.AlignLeft, "Stationaeres Modell, Genauigkeit 0.1 C; keine hydraulische oder sicherheitstechnische Freigabe.")
        plot = PlotView()
        plot.scene().setFont(QFont("Arial", 11))
        plot.set_series(payload["series"], show_legend=False)
        plot.scene().setBackgroundBrush(Qt.white)
        for item in plot.scene().items():
            if hasattr(item, "setDefaultTextColor"):
                item.setDefaultTextColor(Qt.black)
        plot.scene().render(painter, chart, plot.sceneRect(), Qt.KeepAspectRatio)
        if payload["note"]:
            painter.setPen(Qt.black)
            painter.drawText(chart.adjusted(line, line, -line, -line), Qt.AlignCenter | Qt.TextWordWrap, payload["note"])
        marker_width = line * 1.6
        for name, color, rect in legend:
            painter.setPen(QPen(QColor(color), 2.5))
            painter.drawLine(rect.x(), rect.y() + line * 0.5, rect.x() + marker_width * 0.7, rect.y() + line * 0.5)
            painter.setPen(Qt.black)
            painter.drawText(rect.adjusted(marker_width, 0, -line, 0), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, name)
    finally:
        if plot is not None:
            plot.close()
        painter.restore()