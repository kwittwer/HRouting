from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QTextLayout, QTextOption


@dataclass(frozen=True)
class ApSticker:
    point_id: str
    name: str
    room: str
    position: str
    height_mm: float
    symbol: str
    icon_path: str
    cables: tuple[str, ...]


def collect_ap_stickers(document, resolve_room, room_id="") -> list[ApSticker]:
    rooms = document.elements["elec_rooms"]
    connections = {point_id: [] for point_id in document.elements["elec_points"]}
    for cable_id, cable in document.elements["elec_cables"].items():
        endpoints = {
            str(cable.start_ap or cable.geom.get("cable_start_ap") or ""),
            str(cable.end_ap or cable.geom.get("cable_end_ap") or ""),
        }
        text = f"{cable_id}: {cable.name or cable_id} / {cable.cable_type or '-'}"
        for endpoint in endpoints:
            if endpoint in connections:
                connections[endpoint].append(text)
    stickers = []
    for point_id, point in document.elements["elec_points"].items():
        assigned_room = resolve_room(point)
        if room_id and assigned_room != room_id:
            continue
        room = rooms.get(assigned_room)
        stickers.append(ApSticker(
            point_id=point_id,
            name=str(point.name or point_id),
            room=str(room.name or assigned_room) if room is not None else "Ohne Raum",
            position=str(point.position or "-"),
            height_mm=float(point.height_from_floor or 0.0),
            symbol=str(point.builtin_symbol or ""),
            icon_path=str(point.icon_path or ""),
            cables=tuple(connections[point_id]),
        ))
    stickers.sort(key=lambda sticker: (
        sticker.room.casefold(),
        int(sticker.point_id.rsplit("-", 1)[-1]),
    ))
    return stickers


def _draw_text(painter, rect, text, *, bold=False, size=8.0, centered=False):
    text = text.replace("\n", "\u2028")
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    if centered:
        option.setAlignment(Qt.AlignHCenter)
    font = QFont("Arial")
    font.setBold(bold)
    layout = QTextLayout()
    for _attempt in range(80):
        font.setPointSizeF(size)
        layout = QTextLayout(text, font, painter.device())
        layout.setTextOption(option)
        layout.beginLayout()
        height = 0.0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(rect.width())
            line.setPosition(QPointF(0, height))
            height += line.height()
        layout.endLayout()
        if height <= rect.height():
            break
        size *= 0.92
    painter.setPen(Qt.black)
    layout.draw(painter, rect.topLeft())


def draw_ap_sticker_sheet(painter, writer, stickers):
    from gui.parameter_panel import BUILTIN_SYMBOLS

    scale = writer.resolution() / 25.4
    page = QRectF(writer.pageLayout().paintRectPixels(writer.resolution()))
    origin_x = page.x() + (page.width() - 240 * scale) / 2
    origin_y = page.y() + (page.height() - 180 * scale) / 2
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    for index, sticker in enumerate(stickers):
        left = origin_x + (index % 4) * 60 * scale
        top = origin_y + (index // 4) * 60 * scale

        def rect(x, y, width, height):
            return QRectF(left + x * scale, top + y * scale, width * scale, height * scale)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#b0b0b0"), 0.15 * scale))
        painter.drawRect(rect(0, 0, 60, 60))
        painter.drawEllipse(rect(0, 0, 60, 60))
        painter.setPen(QPen(QColor("#888888"), 0.15 * scale, Qt.DashLine))
        painter.drawLine(rect(0, 30, 60, 0).topLeft(), rect(0, 30, 60, 0).topRight())
        painter.drawLine(rect(30, 0, 0, 60).topLeft(), rect(30, 0, 0, 60).bottomLeft())
        painter.setPen(QPen(Qt.black, 0.2 * scale))
        painter.drawLine(rect(28.5, 30, 3, 0).topLeft(), rect(28.5, 30, 3, 0).topRight())
        painter.drawLine(rect(30, 28.5, 0, 3).topLeft(), rect(30, 28.5, 0, 3).bottomLeft())
        _draw_text(painter, rect(3, 3, 54, 12),
               f"{sticker.point_id}\n{sticker.name}\nRaum: {sticker.room}", bold=True, centered=True)
        icon = QPixmap(sticker.icon_path or BUILTIN_SYMBOLS.get(sticker.symbol, ""))
        if icon.isNull() and sticker.symbol:
            icon = QPixmap(BUILTIN_SYMBOLS.get(sticker.symbol, ""))
        if not icon.isNull():
            target = rect(21, 21, 18, 18)
            fitted = icon.size().scaled(target.size().toSize(), Qt.KeepAspectRatio)
            target = QRectF(target.center().x() - fitted.width() / 2,
                            target.center().y() - fitted.height() / 2,
                            fitted.width(), fitted.height())
            painter.drawPixmap(target, icon, QRectF(icon.rect()))
        _draw_text(painter, rect(3, 15, 54, 6),
                   f"{sticker.position} / H\u00f6he: {sticker.height_mm / 10:g} cm")
        _draw_text(painter, rect(3, 40, 54, 17),
                   "Kabel:\n" + ("\n".join(sticker.cables) or "Keine Kabel angeschlossen"),
                   size=7.0)
    painter.restore()