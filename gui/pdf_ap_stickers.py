from dataclasses import dataclass
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QTextLayout, QTextOption

from logic.photo_markers import TEMPLATE_MARGIN_MM, quiet_zone_mm

# Conservative A4 landscape printable area of the export writer (3.5 mm margins) in mm.
SHEET_WIDTH_MM = 288.0
SHEET_HEIGHT_MM = 200.0
SHEET_FOOTER_MM = 8.0
STICKER_GAP_MM = 1.0
STICKER_HEADER_MM = 3.0


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


def _marker_number(marker_id) -> int:
    tail = str(marker_id).rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else -1


def _registration_rank(item) -> tuple:
    marker_id, marker = item
    version = marker.get("print_version", 1)
    return (version if isinstance(version, int) and not isinstance(version, bool) else 1, _marker_number(marker_id))


def prepare_documentation_stickers(document, resolve_room, room_id="", include_references=True) -> list[dict]:
    """Collect saved registrations only; printing never allocates marker identities.

    Saved registries may be key-sorted, so the newest registration is chosen by
    print version and numeric id, never by dictionary order.
    """
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
    marker_size = (config.get("dictionary") or {}).get("marker_size", 5)

    def entry(marker_id, marker, **payload):
        side = marker.get("side_mm")
        if (not isinstance(side, (int, float)) or isinstance(side, bool)
                or not math.isfinite(side) or side <= 0):
            raise ValueError(f"{marker_id}: side_mm muss positiv und endlich sein. Messmarker neu registrieren.")
        if marker.get("id") != marker_id or type(marker.get("aruco_id")) is not int or marker["aruco_id"] < 0:
            raise ValueError(f"{marker_id}: ungueltige Registrierung. Fotodokumentation pruefen.")
        template = marker.get("template")
        offset = marker.get("anchor_offset_mm")
        if template is not None:
            diameter = (template or {}).get("diameter_mm") if isinstance(template, dict) else None
            if (not isinstance(diameter, (int, float)) or isinstance(diameter, bool)
                    or not math.isfinite(diameter) or diameter <= 0 or not isinstance(offset, (list, tuple))
                    or len(offset) != 2 or not all(isinstance(value, (int, float)) and math.isfinite(value)
                                                    for value in offset)):
                raise ValueError(f"{marker_id}: Bohrschablone unvollstaendig registriert. Messmarker neu registrieren.")
        return {"marker_id": marker_id, "side_mm": float(side), "marker_size": marker_size,
                "template": {"diameter_mm": float(template["diameter_mm"])} if template else None,
                "anchor_offset_mm": [float(offset[0]), float(offset[1])] if template else None, **payload}

    for ap in aps:
        matches = [(key, marker) for key, marker in registry.items()
                   if isinstance(marker, dict) and marker.get("point_id") == ap.point_id]
        if not matches:
            raise ValueError(f"{ap.point_id}: Messmarker fehlt. Fotodokumentation vorbereiten und Projekt speichern.")
        marker_id, marker = max(matches, key=_registration_rank)
        result.append(entry(marker_id, marker, kind="ap", ap=ap, name=ap.name, room=ap.room))
    if include_references:
        rooms = document.elements["elec_rooms"]
        surfaces = config.get("surfaces", {})
        by_surface = {}
        for marker_id, marker in registry.items():
            if not isinstance(marker, dict) or marker.get("point_id"):
                continue
            surface = surfaces.get(marker.get("surface_id"))
            if not isinstance(surface, dict) or surface.get("status") != "current":
                continue
            assigned_room = surface.get("room_id")
            if assigned_room not in rooms or (room_id and assigned_room != room_id):
                continue
            by_surface.setdefault(marker["surface_id"], []).append((marker_id, marker))
        for surface_id, candidates in by_surface.items():
            # A resized reference supersedes older sizes; equal-size extra references stay printable.
            newest_side = max(candidates, key=_registration_rank)[1].get("side_mm")
            surface = surfaces[surface_id]
            for marker_id, marker in sorted(candidates, key=_registration_rank):
                if marker.get("side_mm") != newest_side:
                    continue
                result.append(entry(marker_id, marker, kind="reference", ap=None,
                                    name=str(surface.get("name") or marker_id),
                                    room=str(rooms[surface["room_id"]].name or surface["room_id"])))
    if result and not config.get("dictionary"):
        raise ValueError("Messmarker-Dictionary fehlt. Fotodokumentation vorbereiten und Projekt vor dem Druck speichern.")
    return result


def documentation_sticker_pages(stickers) -> list[list[dict]]:
    """Pack registered AP/reference stickers onto A4 landscape sheets; empty input prints one sheet."""
    return [[sticker for sticker, _x, _y in page]
            for page in _place(stickers, SHEET_WIDTH_MM, SHEET_HEIGHT_MM - SHEET_FOOTER_MM)]


def estimate_documentation_pages(ap_count: int, reference_count: int) -> int:
    """Sheet count for not yet registered stickers at the default sizes."""
    from logic.photo_markers import (
        AP_MARKER_SIDE_MM, REFERENCE_MARKER_SIDE_MM, TEMPLATE_DIAMETER_MM, template_anchor_offset_mm,
    )

    stickers = [{"marker_id": "AP", "side_mm": AP_MARKER_SIDE_MM, "template": {"diameter_mm": TEMPLATE_DIAMETER_MM},
                 "anchor_offset_mm": template_anchor_offset_mm(AP_MARKER_SIDE_MM)}] * max(0, int(ap_count))
    stickers += [{"marker_id": "REF", "side_mm": REFERENCE_MARKER_SIDE_MM}] * max(0, int(reference_count))
    return len(documentation_sticker_pages(stickers))


def sticker_footprint(sticker) -> tuple[float, float]:
    """Outer sticker size in mm including cut margin, derived from the registered marker."""
    side = float(sticker["side_mm"])
    quiet = quiet_zone_mm(side, sticker.get("marker_size", 5))
    outer = side + 2 * quiet
    template = sticker.get("template")
    if template:
        diameter = float(template["diameter_mm"])
        return (outer + 2 * TEMPLATE_MARGIN_MM + diameter,
                STICKER_HEADER_MM + max(outer, diameter + 2 * TEMPLATE_MARGIN_MM) + 20)
    return outer + 8, STICKER_HEADER_MM + outer + 18


def _place(stickers, width_mm, height_mm) -> list[list[tuple]]:
    pages, page, x, y, row_height = [], [], 0.0, 0.0, 0.0
    for sticker in stickers:
        width, height = sticker_footprint(sticker)
        if width > width_mm or height > height_mm:
            raise ValueError(f"{sticker['marker_id']}: Messaufkleber passt nicht masshaltig auf A4 quer.")
        if x + width > width_mm + 1e-6:
            x, y, row_height = 0.0, y + row_height + STICKER_GAP_MM, 0.0
        if y + height > height_mm + 1e-6:
            pages.append(page)
            page, x, y, row_height = [], 0.0, 0.0, 0.0
        page.append((sticker, x, y))
        x += width + STICKER_GAP_MM
        row_height = max(row_height, height)
    pages.append(page)
    return pages


def draw_documentation_sticker_sheet(painter, writer, stickers, config) -> None:
    """Draw one sheet from documentation_sticker_pages at registered physical sizes."""
    scale = writer.resolution() / 25.4
    page = QRectF(writer.pageLayout().paintRectPixels(writer.resolution()))
    if not stickers:
        _draw_text(painter, page, "Keine Anschlusspunkte oder Referenzmarker ausgewaehlt.", centered=True)
        return
    if page.width() / scale + 1e-6 < SHEET_WIDTH_MM or page.height() / scale + 1e-6 < SHEET_HEIGHT_MM:
        raise ValueError("Messaufkleber passen nicht masshaltig auf diese Seite. A4 quer verwenden.")
    placed = _place(stickers, SHEET_WIDTH_MM, SHEET_HEIGHT_MM - SHEET_FOOTER_MM)
    if len(placed) != 1:
        raise ValueError("Zu viele Messaufkleber fuer eine Seite; documentation_sticker_pages verwenden.")
    left = page.x() + (page.width() - SHEET_WIDTH_MM * scale) / 2
    top = page.y() + (page.height() - SHEET_HEIGHT_MM * scale) / 2
    painter.save()
    try:
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        for sticker, x, y in placed[0]:
            _draw_documentation_sticker(painter, scale, left + x * scale, top + y * scale, sticker, config)
        footer_top = SHEET_HEIGHT_MM - SHEET_FOOTER_MM + 1
        control_left = (SHEET_WIDTH_MM - 100) / 2
        _draw_text(painter, QRectF(left, top + footer_top * scale, SHEET_WIDTH_MM * scale, 3.5 * scale),
                   "Kontrollstrecke 100 mm. Nur bei 100 % / Originalgroesse drucken, keine Seitenanpassung, nachmessen.",
                   size=7, centered=True)
        painter.setPen(QPen(Qt.black, 0.25 * scale))
        base = top + (footer_top + 5.5) * scale
        painter.drawLine(QPointF(left + control_left * scale, base), QPointF(left + (control_left + 100) * scale, base))
        for endpoint in (control_left, control_left + 100):
            painter.drawLine(QPointF(left + endpoint * scale, base - 1.5 * scale),
                             QPointF(left + endpoint * scale, base + 1.5 * scale))
    finally:
        painter.restore()


def _draw_documentation_sticker(painter, scale, left, top, sticker, config) -> None:
    from logic.photo_markers import marker_image

    marker_id = sticker["marker_id"]
    marker = config.get("markers", {}).get(marker_id)
    if (not isinstance(marker, dict) or sticker["side_mm"] != marker.get("side_mm")
            or bool(sticker.get("template")) != bool(marker.get("template"))
            or (sticker.get("template") and (
                list(sticker["anchor_offset_mm"]) != [float(v) for v in marker.get("anchor_offset_mm", [])]
                or sticker["template"]["diameter_mm"] != marker["template"].get("diameter_mm")))):
        raise ValueError(f"{marker_id}: Registrierung fehlt oder Druckmass hat sich geaendert. Export neu vorbereiten.")
    side = sticker["side_mm"]
    cells = config["dictionary"]["marker_size"] + 2
    modules = marker_image(config, marker_id, pixels=cells * 2)[::2, ::2]
    quiet = quiet_zone_mm(side, config["dictionary"]["marker_size"])
    outer = side + quiet * 2
    width, height = sticker_footprint(sticker)
    template = sticker.get("template")

    def rect(x, y, w, h):
        return QRectF(left + x * scale, top + y * scale, w * scale, h * scale)

    def line(start_x, start_y, end_x, end_y):
        painter.drawLine(QPointF(left + start_x * scale, top + start_y * scale),
                         QPointF(left + end_x * scale, top + end_y * scale))

    ap = sticker.get("ap")
    painter.fillRect(rect(0, 0, width, height), Qt.white)
    painter.setPen(QPen(QColor("#b0b0b0"), 0.15 * scale, Qt.DashLine))
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(rect(0, 0, width, height))
    painter.setPen(QPen(Qt.black, 0.25 * scale))
    line(1.5, 2.8, 1.5, 0.4)
    line(1.5, 0.4, 0.6, 1.3)
    line(1.5, 0.4, 2.4, 1.3)
    header = f"OBEN / {marker_id} / ArUco {marker['aruco_id']} / Kante {side:g} mm"
    if template:
        block = max(outer, template["diameter_mm"] + 2 * TEMPLATE_MARGIN_MM)
        marker_left, marker_top = 0.0, STICKER_HEADER_MM + (block - outer) / 2
        offset = sticker["anchor_offset_mm"]
        expected = [outer / 2 + TEMPLATE_MARGIN_MM + template["diameter_mm"] / 2, 0.0]
        if any(abs(offset[axis] - expected[axis]) > 1e-6 for axis in (0, 1)):
            raise ValueError(f"{marker_id}: registrierter Ankerversatz passt nicht zum Drucklayout.")
        header += f" / Bohrmitte {offset[0]:g} mm rechts der Markermitte = AP-Bezug"
    else:
        block = outer
        marker_left, marker_top = 4.0, STICKER_HEADER_MM
    _draw_text(painter, rect(4, 0, width - 4, STICKER_HEADER_MM), header, size=6)
    module_side = side / cells
    for row in range(cells):
        for column in range(cells):
            if modules[row, column] == 0:
                painter.fillRect(rect(marker_left + quiet + column * module_side,
                                      marker_top + quiet + row * module_side,
                                      module_side, module_side), Qt.black)
    center_y = STICKER_HEADER_MM + block / 2
    painter.setPen(QPen(Qt.black, 0.25 * scale))
    if template:
        diameter = template["diameter_mm"]
        center_x = marker_left + outer / 2 + offset[0]
        zone_left = outer + TEMPLATE_MARGIN_MM
        painter.setPen(QPen(Qt.black, 0.25 * scale))
        painter.drawEllipse(rect(center_x - diameter / 2, center_y - diameter / 2, diameter, diameter))
        painter.drawEllipse(rect(center_x - 1, center_y - 1, 2, 2))
        painter.setPen(QPen(QColor("#777777"), 0.15 * scale, Qt.DashLine))
        painter.drawEllipse(rect(center_x - 30, center_y - 30, 60, 60))
        painter.setPen(QPen(Qt.black, 0.2 * scale))
        line(zone_left, center_y, width, center_y)
        line(center_x, STICKER_HEADER_MM, center_x, STICKER_HEADER_MM + block)
        title = f"{ap.point_id}" if ap is not None else marker_id
        _draw_text(painter, rect(center_x - 26, center_y - 30, 52, 9), title, bold=True, size=12, centered=True)
        if ap is not None:
            _draw_text(painter, rect(center_x - 29, center_y - 20, 58, 16),
                       f"{ap.name}\nRaum: {ap.room}", size=7.5, centered=True)
            _draw_text(painter, rect(center_x - 29, center_y + 4, 58, 14),
                       f"{ap.position} / H\u00f6he: {ap.height_mm / 10:g} cm ueber FFB", size=7.5, centered=True)
        _draw_text(painter, rect(center_x - 29, center_y + 19, 58, 9),
                   f"Bohrmitte = AP-Bezug / Dose \u00d8{diameter:g} mm", size=6, centered=True)
        details = ("Kabel: " + ("; ".join(ap.cables) if ap is not None and ap.cables else "Keine Kabel angeschlossen")
                   + "\nMarker flach auf derselben Ebene kleben, Bohrmitte am Montageort, Ausrichtung frei.")
        _draw_text(painter, rect(1, STICKER_HEADER_MM + block + 1, width - 2, 18), details, size=7)
    else:
        for cross_x in (2.0, marker_left + outer + 2.0):
            line(cross_x - 1, center_y, cross_x + 1, center_y)
            line(cross_x, center_y - 1, cross_x, center_y + 1)
        title = (f"{ap.point_id} / {ap.name} / Raum: {ap.room}" if ap is not None
                 else f"Referenz / {sticker['name']} / Raum: {sticker['room']}")
        details = "Bezug: Markermitte (seitliche Kreuze)\n"
        if ap is not None:
            details += (f"{ap.position} / H\u00f6he: {ap.height_mm / 10:g} cm ueber FFB\n"
                        "Kabel: " + ("; ".join(ap.cables) or "Keine Kabel angeschlossen"))
        else:
            details += "Flaechenreferenz / Lage und Ausrichtung vor Ort einmessen."
        _draw_text(painter, rect(1, STICKER_HEADER_MM + outer + 0.5, width - 2, 5), title, bold=True, size=8)
        _draw_text(painter, rect(1, STICKER_HEADER_MM + outer + 6, width - 2, 11.5), details, size=7)


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