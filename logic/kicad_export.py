"""KiCad schematic (.kicad_sch) export for HRouting electrical data."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple
from uuid import uuid4


def _uid() -> str:
    return str(uuid4())


def _fmt(value: float) -> str:
    # KiCad schematic coordinates are decimal numbers. Keep output compact.
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text if text else "0"


def _escape(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("\r", "")
        .replace("\n", "\\n")
        .replace('"', "'")
    )


def _normalize_kbl_token(value: str) -> str:
    return str(value or "").replace(",", "_").replace(".", "_").strip()


def _normalize_kbl_endpoint_name(value: str) -> str:
    return (
        str(value or "")
        .replace(":", "_")
        .replace("{", "_")
        .replace("}", "_")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )


class KiCadExporter:
    """Convert HRouting project data to KiCad schematic format."""

    ROOM_GRID_COLUMNS = 3
    ROOM_BLOCK_WIDTH = 150.0
    ROOM_BLOCK_HEIGHT = 95.0
    AP_GRID_COLUMNS = 2
    AP_CELL_WIDTH = 70.0
    AP_CELL_HEIGHT = 22.0
    AP_BLOCK_X0 = 30.0
    AP_BLOCK_Y0 = 45.0

    CABLE_LABEL_COLUMNS = 2
    CABLE_LABEL_CELL_WIDTH = 50.0
    CABLE_LABEL_CELL_HEIGHT = 4.0
    CABLE_LABEL_OFFSET_X = 2.0
    CABLE_LABEL_OFFSET_Y = 2.0

    def __init__(self, project_dict: Dict[str, Any]):
        self.project = project_dict or {}
        self.canvas = self.project.get("canvas", {}) or {}
        self.params = self.project.get("params", {}) or {}

    def export_to_file(self, output_path: str) -> bool:
        content = self._build_schematic()
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        return True

    def _to_kicad_xy(self, px: List[float]) -> Tuple[float, float]:
        mm_per_px = float(self.canvas.get("mm_per_px", 1.0) or 1.0)
        x = float(px[0]) * mm_per_px / 10.0 + 20.0
        y = float(px[1]) * mm_per_px / 10.0 + 20.0
        return x, y

    def _textbox_size(self, content: str) -> Tuple[float, float]:
        lines = [line for line in str(content or "").split("\n") if line] or [""]
        longest = max(len(line) for line in lines)
        width = max(30.0, 2.3 + longest * 1.7)
        height = max(10.0, 3.0 + len(lines) * 2.8)
        return width, height

    def _room_name_lookup(self) -> Dict[str, str]:
        lookup: Dict[str, str] = {}
        for room_id, room_data in (self.params.get("elec_rooms", {}) or {}).items():
            if not isinstance(room_data, dict):
                continue
            room_name = str(room_data.get("name", "") or "").strip() or room_id
            lookup[str(room_id)] = room_name
        return lookup

    def _group_aps_by_room(self) -> List[Tuple[str, List[Tuple[str, dict]]]]:
        elec_points = self.params.get("elec_points", {}) or {}
        point_positions = self.canvas.get("elec_points", {}) or {}
        room_names = self._room_name_lookup()
        grouped: Dict[str, List[Tuple[str, dict]]] = {}

        for ap_id, pdata in elec_points.items():
            if not bool(pdata.get("visible", True)):
                continue
            if ap_id not in point_positions:
                continue

            room_id = str(pdata.get("room_id", "") or "").strip()
            if room_id:
                room_name = room_names.get(room_id, room_id)
            else:
                room_name = "Ohne Raum"

            grouped.setdefault(room_name, []).append((ap_id, pdata))

        result: List[Tuple[str, List[Tuple[str, dict]]]] = []
        for room_name in sorted(grouped):
            entries = grouped[room_name]
            entries.sort(key=lambda item: str(item[1].get("name", "") or item[0]).casefold())
            result.append((room_name, entries))
        return result

    def _build_ap_text(self) -> Tuple[List[str], Dict[str, Tuple[float, float]], Dict[str, float]]:
        room_groups = self._group_aps_by_room()

        lines: List[str] = []
        ap_positions: Dict[str, Tuple[float, float]] = {}
        ap_heights: Dict[str, float] = {}

        for room_index, (room_name, aps) in enumerate(room_groups):
            room_col = room_index % self.ROOM_GRID_COLUMNS
            room_row = room_index // self.ROOM_GRID_COLUMNS
            room_x = self.AP_BLOCK_X0 + room_col * self.ROOM_BLOCK_WIDTH
            room_y = self.AP_BLOCK_Y0 + room_row * self.ROOM_BLOCK_HEIGHT

            lines.extend(
                [
                    f'  (text "ROOM: {_escape(room_name)}"',
                    f"    (at {_fmt(room_x)} {_fmt(room_y)} 0)",
                    "    (effects (font (size 1.27 1.27)) (justify left))",
                    f"    (uuid {_uid()})",
                    "  )",
                ]
            )

            for ap_index, (ap_id, pdata) in enumerate(aps):
                cell_col = ap_index % self.AP_GRID_COLUMNS
                cell_row = ap_index // self.AP_GRID_COLUMNS
                x = room_x + cell_col * self.AP_CELL_WIDTH
                y = room_y + 6.0 + cell_row * self.AP_CELL_HEIGHT

                ap_name = str(pdata.get("name", "") or "").strip() or ap_id
                ap_symbol = str(pdata.get("builtin_symbol", "") or "").strip() or "Unbekannt"
                height_from_floor = pdata.get("height_from_floor", 0.0)

                content = "\n".join(
                    [
                        f"HRP:AP_ID: {ap_id}",
                        f"AP_NAME: {ap_name}",
                        f"AP_SYMBOL: {ap_symbol}",
                        f"HEIGHT_FROM_FLOOR_MM: {height_from_floor}",
                    ]
                )
                width, height = self._textbox_size(content)
                ap_positions[ap_id] = (x, y)
                ap_heights[ap_id] = height

                lines.extend(
                    [
                        f'  (text_box "{_escape(content)}"',
                        "    (exclude_from_sim no)",
                        f"    (at {_fmt(x)} {_fmt(y)} 0)",
                        f"    (size {_fmt(width)} {_fmt(height)})",
                        "    (margins 0.9525 0.9525 0.9525 0.9525)",
                        "    (stroke (width 0) (type solid) (color 0 0 0 1))",
                        "    (fill (type none))",
                        "    (effects (font (size 1.27 1.27) (color 0 0 0 1)) (justify left top))",
                        f"    (uuid {_uid()})",
                        "  )",
                    ]
                )

        return lines, ap_positions, ap_heights

    def _build_net_labels(
        self,
        ap_positions: Dict[str, Tuple[float, float]],
        ap_heights: Dict[str, float],
    ) -> List[str]:
        labels: List[str] = []
        cable_polylines = self.canvas.get("elec_cables", {}) or {}
        cable_start_map = self.canvas.get("cable_start_ap", {}) or {}
        cable_end_map = self.canvas.get("cable_end_ap", {}) or {}
        point_positions = self.canvas.get("elec_points", {}) or {}
        elec_cables = self.params.get("elec_cables", {}) or {}
        elec_points = self.params.get("elec_points", {}) or {}
        cables_by_start: Dict[str, List[Tuple[str, str]]] = {}

        for cid, polyline in cable_polylines.items():
            pts = polyline if isinstance(polyline, list) else []
            cable_data = elec_cables.get(cid, {}) or {}
            start_ap = str(
                cable_data.get("start_ap", "") or cable_start_map.get(cid, "") or ""
            ).strip()
            end_ap = str(
                cable_data.get("end_ap", "") or cable_end_map.get(cid, "") or ""
            ).strip()
            cable_type = str(cable_data.get("type", "") or "").strip()
            normalized_type = _normalize_kbl_token(cable_type)

            if len(pts) < 2:
                if start_ap in point_positions and end_ap in point_positions:
                    pts = [point_positions[start_ap], point_positions[end_ap]]

            if len(pts) < 2:
                continue

            start_name = str((elec_points.get(start_ap, {}) or {}).get("name", "") or "").strip()
            end_name = str((elec_points.get(end_ap, {}) or {}).get("name", "") or "").strip()
            start_token = _normalize_kbl_endpoint_name(start_name or start_ap or "UNSET")
            end_token = _normalize_kbl_endpoint_name(end_name or end_ap or "UNSET")
            label = f"KBL_{start_token}:{end_token}{{{normalized_type}}}"

            group_key = start_ap or "UNSET"
            cables_by_start.setdefault(group_key, []).append((cid, label))

        for start_ap in sorted(cables_by_start):
            entries = sorted(cables_by_start[start_ap], key=lambda item: item[0])

            if start_ap in ap_positions:
                anchor_x, anchor_y = ap_positions[start_ap]
                anchor_x = anchor_x + self.CABLE_LABEL_OFFSET_X
                anchor_y = anchor_y + ap_heights.get(start_ap, 10.0) + self.CABLE_LABEL_OFFSET_Y
            else:
                anchor_x = self.AP_BLOCK_X0
                anchor_y = self.AP_BLOCK_Y0 + 340.0 + len(labels) * self.CABLE_LABEL_CELL_HEIGHT

            for index, (_, label) in enumerate(entries):
                col = index % self.CABLE_LABEL_COLUMNS
                row = index // self.CABLE_LABEL_COLUMNS
                lx = anchor_x + col * self.CABLE_LABEL_CELL_WIDTH
                ly = anchor_y + row * self.CABLE_LABEL_CELL_HEIGHT
                labels.extend(
                    [
                        "  (label \"%s\"" % _escape(label),
                        f"    (at {_fmt(lx)} {_fmt(ly)} 0)",
                        "    (effects (font (size 1.0 1.0)) (justify left))",
                        f"    (uuid {_uid()})",
                        "  )",
                    ]
                )

        return labels

    def _build_power_labels(self) -> List[str]:
        labels = ["L1", "L2", "L3", "N", "PE"]
        x0 = 15.0
        y0 = 15.0
        dy = 4.0
        lines: List[str] = []
        for i, label in enumerate(labels):
            y = y0 + i * dy
            lines.extend(
                [
                    "  (global_label \"%s\"" % label,
                    f"    (at {_fmt(x0)} {_fmt(y)} 0)",
                    "    (shape input)",
                    "    (effects (font (size 1.27 1.27)))",
                    f"    (uuid {_uid()})",
                    "  )",
                ]
            )
        return lines

    def _build_schematic(self) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        title = str(self.params.get("project_name", "HRouting Export") or "HRouting Export")

        parts: List[str] = [
            "(kicad_sch",
            "  (version 20230121)",
            '  (generator "HRouting")',
            f"  (uuid {_uid()})",
            '  (paper "A3")',
            "  (title_block",
            f'    (title "{_escape(title)}")',
            '    (company "HRouting")',
            f'    (comment 1 "Export: {now}")',
            "  )",
            "  (lib_symbols)",
        ]

        parts.extend(self._build_power_labels())
        ap_lines, ap_positions, ap_heights = self._build_ap_text()
        parts.extend(ap_lines)
        parts.extend(self._build_net_labels(ap_positions, ap_heights))

        parts.append(")")
        return "\n".join(parts) + "\n"


def export_project_to_kicad(project_dict: Dict[str, Any], output_path: str) -> Tuple[bool, str]:
    """Export wrapper for KiCad schematic generation."""
    try:
        exporter = KiCadExporter(project_dict)
        exporter.export_to_file(output_path)
        return True, f"Erfolgreich exportiert zu: {output_path}"
    except Exception as exc:
        return False, f"Export fehlgeschlagen: {exc}"
