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
    return value.replace('"', "'")


class KiCadExporter:
    """Convert HRouting project data to KiCad schematic format."""

    SYMBOL_MAP = {
        "Steckdose": ("Connector:Conn_01x03_Pin", "X", "Steckdose"),
        "Schalter": ("Switch:SW_SPST", "S", "Schalter"),
        "Taster": ("Switch:SW_Push", "S", "Taster"),
        "Leuchte": ("Device:Lamp", "L", "Leuchte"),
        "Motor": ("Device:Motor", "M", "Motor"),
        "Relais": ("Relay:Relay_SPST", "K", "Relais"),
        "Schuetz": ("Relay:Relay_SPST", "K", "Schuetz"),
        "Schütz": ("Relay:Relay_SPST", "K", "Schuetz"),
    }

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

    def _build_symbols(self) -> List[str]:
        elec_points = self.params.get("elec_points", {}) or {}
        point_positions = self.canvas.get("elec_points", {}) or {}

        lines: List[str] = []
        used_prefix: Dict[str, int] = {}

        for ap_id, pdata in elec_points.items():
            if not bool(pdata.get("visible", True)):
                continue
            if ap_id not in point_positions:
                continue

            symbol_name = str(pdata.get("builtin_symbol", "") or "").strip()
            lib_id, prefix, default_value = self.SYMBOL_MAP.get(
                symbol_name, ("Connector:Conn_01x02_Pin", "X", symbol_name or "Anschluss")
            )
            used_prefix[prefix] = used_prefix.get(prefix, 0) + 1
            ref = f"{prefix}{used_prefix[prefix]}"

            x, y = self._to_kicad_xy(point_positions[ap_id])
            value = str(pdata.get("name", "") or "").strip() or default_value

            lines.extend(
                [
                    "  (symbol",
                    f'    (lib_id "{lib_id}")',
                    f"    (at {_fmt(x)} {_fmt(y)} 0)",
                    "    (unit 1)",
                    "    (in_bom yes)",
                    "    (on_board yes)",
                    f"    (uuid {_uid()})",
                    "    (property \"Reference\" \"%s\"" % _escape(ref),
                    f"      (at {_fmt(x)} {_fmt(y - 2.54)} 0)",
                    "      (effects (font (size 1.27 1.27)))",
                    "    )",
                    "    (property \"Value\" \"%s\"" % _escape(value),
                    f"      (at {_fmt(x)} {_fmt(y + 2.54)} 0)",
                    "      (effects (font (size 1.27 1.27)))",
                    "    )",
                    "    (property \"Footprint\" \"\" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))",
                    "    (property \"Datasheet\" \"\" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))",
                    "  )",
                ]
            )

            # Add a local label so nets are readable after export.
            lines.extend(
                [
                    "  (label \"%s\"" % _escape(ap_id),
                    f"    (at {_fmt(x + 4.0)} {_fmt(y)} 0)",
                    "    (effects (font (size 1.27 1.27)))",
                    f"    (uuid {_uid()})",
                    "  )",
                ]
            )

        return lines

    def _build_wires(self) -> List[str]:
        wires: List[str] = []
        cable_polylines = self.canvas.get("elec_cables", {}) or {}
        cable_start_map = self.canvas.get("cable_start_ap", {}) or {}
        cable_end_map = self.canvas.get("cable_end_ap", {}) or {}
        point_positions = self.canvas.get("elec_points", {}) or {}

        for cid, polyline in cable_polylines.items():
            pts = polyline if isinstance(polyline, list) else []
            if len(pts) < 2:
                start_ap = str(cable_start_map.get(cid, "") or "").strip()
                end_ap = str(cable_end_map.get(cid, "") or "").strip()
                if start_ap in point_positions and end_ap in point_positions:
                    pts = [point_positions[start_ap], point_positions[end_ap]]

            if len(pts) < 2:
                continue

            for a, b in zip(pts[:-1], pts[1:]):
                ax, ay = self._to_kicad_xy(a)
                bx, by = self._to_kicad_xy(b)
                wires.extend(
                    [
                        "  (wire",
                        f"    (pts (xy {_fmt(ax)} {_fmt(ay)}) (xy {_fmt(bx)} {_fmt(by)}))",
                        "    (stroke (width 0) (type default))",
                        f"    (uuid {_uid()})",
                        "  )",
                    ]
                )

            # Add a net label near the first cable point.
            fx, fy = self._to_kicad_xy(pts[0])
            wires.extend(
                [
                    "  (label \"%s\"" % _escape(cid),
                    f"    (at {_fmt(fx)} {_fmt(fy - 1.8)} 0)",
                    "    (effects (font (size 1.0 1.0)))",
                    f"    (uuid {_uid()})",
                    "  )",
                ]
            )

        return wires

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
        parts.extend(self._build_symbols())
        parts.extend(self._build_wires())

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
