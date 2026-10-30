from dataclasses import dataclass
import math

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


def prepare_documentation_stickers(document, resolve_room, room_id="", include_references=True) -> list[dict]:
    """Collect saved registrations only; printing never allocates marker identities."""
    aps = collect_ap_stickers(document, resolve_room, room_id)
    config = document.settings.get("photo_documentation")
    if not isinstance(config, dict):
        if not aps:
            return []
        raise ValueError("Messmarker fehlen. Fotodokumentation vorbereiten und Projekt vor dem Druck speichern.")
    registry = config.get("markers", {})
    if not isinstance(registry, dict):
        raise ValueError("Messmarker-Register ist ungueltig. Fotodokumentation pruefen und Projekt speichern.")
    result = []

    def entry(marker_id, marker, **payload):
        side = marker.get("side_mm")
        if (not isinstance(side, (int, float)) or isinstance(side, bool)
                or not math.isfinite(side) or side <= 0):
            raise ValueError(f"{marker_id}: side_mm muss positiv und endlich sein. Messmarker neu registrieren.")
        if marker.get("id") != marker_id or type(marker.get("aruco_id")) is not int or marker["aruco_id"] < 0:
            raise ValueError(f"{marker_id}: ungueltige Registrierung. Fotodokumentation pruefen.")
        return {"marker_id": marker_id, "side_mm": float(side), **payload}

    for ap in aps:
        matches = [(key, marker) for key, marker in registry.items()
                   if isinstance(marker, dict) and marker.get("point_id") == ap.point_id]
        if not matches:
            raise ValueError(f"{ap.point_id}: Messmarker fehlt. Fotodokumentation vorbereiten und Projekt speichern.")
        marker_id, marker = matches[-1]
        result.append(entry(marker_id, marker, kind="ap", ap=ap, name=ap.name, room=ap.room))
    if include_references:
        rooms = document.elements["elec_rooms"]
        surfaces = config.get("surfaces", {})
        for marker_id, marker in registry.items():
            if not isinstance(marker, dict) or marker.get("point_id"):
                continue
            surface = surfaces.get(marker.get("surface_id"))
            if not isinstance(surface, dict) or surface.get("status") != "current":
                continue
            assigned_room = surface.get("room_id")
            if assigned_room not in rooms or (room_id and assigned_room != room_id):
                continue
            result.append(entry(marker_id, marker, kind="reference", ap=None,
                                name=str(surface.get("name") or marker_id),
                                room=str(rooms[assigned_room].name or assigned_room)))
    if result and not config.get("dictionary"):
        raise ValueError("Messmarker-Dictionary fehlt. Fotodokumentation vorbereiten und Projekt vor dem Druck speichern.")
    return result


def documentation_sticker_pages(stickers) -> list[list[dict]]:
    """One registered AP/reference per sheet, including a printable empty sheet."""
    return [[sticker] for sticker in stickers] or [[]]


def draw_documentation_sticker_sheet(painter, writer, stickers, config) -> None:
    """Draw one page from documentation_sticker_pages at its registered size."""
    if len(stickers) > 1:
        raise ValueError("Ein Messaufkleber pro Seite; documentation_sticker_pages fuer Seitenplanung verwenden.")
    scale = writer.resolution() / 25.4
    page = QRectF(writer.pageLayout().paintRectPixels(writer.resolution()))
    if not stickers:
        _draw_text(painter, page, "Keine Anschlusspunkte oder Referenzmarker ausgewaehlt.", centered=True)
        return
    from logic.photo_markers import marker_image

    sticker = stickers[0]
    marker_id = sticker["marker_id"]
    marker = config.get("markers", {}).get(marker_id)
    if not isinstance(marker, dict) or sticker["side_mm"] != marker.get("side_mm"):
        raise ValueError(f"{marker_id}: Registrierung fehlt oder Druckmass hat sich geaendert. Export neu vorbereiten.")
    side = sticker["side_mm"]
    if not isinstance(side, (int, float)) or isinstance(side, bool) or not math.isfinite(side) or side <= 0:
        raise ValueError(f"{marker_id}: side_mm muss positiv und endlich sein.")
    cells = config["dictionary"]["marker_size"] + 2
    modules = marker_image(config, marker_id, pixels=cells * 2)[::2, ::2]
    quiet = max(5.0, side / cells)
    outer = side + quiet * 2
    width_mm, height_mm = page.width() / scale, page.height() / scale
    horizontal = width_mm >= outer + 8 + 65 + 8
    width = min(width_mm - 8, outer + 108) if horizontal else max(outer, min(width_mm - 8, 170))
    body_height = outer if horizontal else outer + 66
    height = 12 + body_height + 18
    if width + 8 > width_mm or height + 4 > height_mm:
        raise ValueError(f"{marker_id}: Messaufkleber passt nicht masshaltig auf diese Seite. A4 quer verwenden.")
    left = page.x() + (page.width() - width * scale) / 2
    top = page.y() + (page.height() - height * scale) / 2

    def rect(x, y, width, height):
        return QRectF(left + x * scale, top + y * scale, width * scale, height * scale)

    def line(start_x, start_y, end_x, end_y):
        painter.drawLine(QPointF(left + start_x * scale, top + start_y * scale),
                         QPointF(left + end_x * scale, top + end_y * scale))

    ap = sticker.get("ap")
    title = (f"{ap.point_id} / {ap.name} / Raum: {ap.room}" if ap is not None
             else f"Referenz / {sticker['name']} / Raum: {sticker['room']}")
    details = f"{marker_id} / ArUco {marker['aruco_id']}\nSchwarze Markerkante: {side:g} mm\nBezug: Markermitte (seitliche Kreuze)\n"
    if ap is not None:
        details += (f"{ap.position} / H\u00f6he: {ap.height_mm / 10:g} cm ueber FFB\n"
                    "Kabel:\n" + ("\n".join(ap.cables) or "Keine Kabel angeschlossen"))
    else:
        details += "Flaechenreferenz / Lage und Ausrichtung vor Ort einmessen."
    painter.save()
    try:
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.fillRect(rect(0, 0, width, height), Qt.white)
        painter.setPen(QPen(Qt.black, 0.25 * scale))
        line(3, 9, 3, 1)
        line(3, 1, 1, 3)
        line(3, 1, 5, 3)
        _draw_text(painter, rect(8, 0, width - 8, 11), "OBEN / " + title, bold=True, size=11)
        marker_left = 0 if horizontal else (width - outer) / 2
        marker_top = 12
        painter.fillRect(rect(marker_left, marker_top, outer, outer), Qt.white)
        module_side = side / cells
        for row in range(cells):
            for column in range(cells):
                if modules[row, column] == 0:
                    painter.fillRect(rect(marker_left + quiet + column * module_side,
                                          marker_top + quiet + row * module_side,
                                          module_side, module_side), Qt.black)
        center_y = marker_top + outer / 2
        painter.setPen(QPen(Qt.black, 0.25 * scale))
        for center_x in (marker_left - 2, marker_left + outer + 2):
            line(center_x - 1, center_y, center_x + 1, center_y)
            line(center_x, center_y - 1, center_x, center_y + 1)
        if horizontal:
            text_rect = rect(outer + 8, 12, width - outer - 8, outer)
        else:
            text_rect = rect(0, 12 + outer + 6, width, 60)
        _draw_text(painter, text_rect, details, size=10)
        control_top = 12 + body_height
        control_left = (width - 100) / 2
        _draw_text(painter, rect(0, control_top, width, 5), "Kontrollstrecke: 100 mm", centered=True)
        painter.setPen(QPen(Qt.black, 0.25 * scale))
        line(control_left, control_top + 8, control_left + 100, control_top + 8)
        for endpoint in (control_left, control_left + 100):
            line(endpoint, control_top + 6, endpoint, control_top + 10)
        _draw_text(painter, rect(0, control_top + 11, width, 7),
                   "Nur bei 100 % / Originalgroesse drucken. Keine Seitenanpassung. Kontrollstrecke nachmessen.",
                   size=8, centered=True)
    finally:
        painter.restore()


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