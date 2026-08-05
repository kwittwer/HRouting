"""Werkzeug-Registry für die neue UI.

Jedes Werkzeug wird deklarativ beschrieben und einem Layer (Gewerk)
zugeordnet. Die Workspace-Definitionen greifen darauf zurück, statt
Toolbars hart zu verdrahten. ``tool_mode`` verweist per Name auf
``gui.canvas_widget.ToolMode``, damit dieses Modul Qt-frei bleibt.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from model.layers import LayerId


@dataclass(frozen=True)
class ToolSpec:
    id: str
    label: str
    layer: LayerId
    tool_mode: str = "NONE"
    shortcut: str = ""
    icon: str = ""
    tooltip: str = ""
    checkable: bool = True
    #: Werkzeug benötigt ein selektiertes Element als Ziel
    needs_selection: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)


TOOLS: tuple[ToolSpec, ...] = (
    # --- Grundriss ----------------------------------------------------
    ToolSpec("fp.select", "Auswählen", LayerId.FLOORPLAN, "NONE", "Esc"),
    ToolSpec("fp.ref_line", "Referenzlinie", LayerId.FLOORPLAN, "DRAW_REF", "R",
             tooltip="Maßstab über eine bekannte Länge kalibrieren"),
    ToolSpec("fp.move", "Grundriss verschieben", LayerId.FLOORPLAN, "MOVE_FLOOR_PLAN", "M"),
    ToolSpec("fp.rotate", "Grundriss drehen", LayerId.FLOORPLAN, "ROTATE_FLOOR_PLAN"),
    ToolSpec("fp.polygon", "Grundriss-Umriss", LayerId.FLOORPLAN, "DRAW_POLY"),

    # --- Heizung ------------------------------------------------------
    ToolSpec("hk.select", "Auswählen", LayerId.HEATING, "NONE", "Esc"),
    ToolSpec("hk.polygon", "Heizkreis zeichnen", LayerId.HEATING, "DRAW_POLY", "H",
             needs_selection=True),
    ToolSpec("hk.edit_polygon", "Heizkreis bearbeiten", LayerId.HEATING, "EDIT_POLYGON",
             needs_selection=True),
    ToolSpec("hk.route", "Rohrverlauf zeichnen", LayerId.HEATING, "DRAW_ROUTE",
             needs_selection=True),
    ToolSpec("hk.edit_route", "Rohrverlauf bearbeiten", LayerId.HEATING, "EDIT_ROUTE",
             needs_selection=True),
    ToolSpec("hk.supply", "Versorgungsleitung", LayerId.HEATING, "DRAW_SUPPLY_LINE",
             needs_selection=True),
    ToolSpec("hk.edit_supply", "Versorgungsleitung bearbeiten", LayerId.HEATING,
             "EDIT_SUPPLY_LINE", needs_selection=True),
    ToolSpec("hkv.place", "HKV platzieren", LayerId.HEATING, "PLACE_HKV"),
    ToolSpec("hkv.line", "HKV-Leitung zeichnen", LayerId.HEATING, "DRAW_HKV_LINE"),
    ToolSpec("hkv.edit_line", "HKV-Leitung bearbeiten", LayerId.HEATING, "EDIT_HKV_LINE"),

    # --- Elektro ------------------------------------------------------
    ToolSpec("ap.select", "Auswählen", LayerId.ELECTRICAL, "NONE", "Esc"),
    ToolSpec("ap.place", "Anschlusspunkt setzen", LayerId.ELECTRICAL, "PLACE_ELEC_POINT", "E"),
    ToolSpec("er.polygon", "Elektro-Raum zeichnen", LayerId.ELECTRICAL, "DRAW_POLY",
             needs_selection=True),
    ToolSpec("ek.draw", "Kabel zeichnen", LayerId.ELECTRICAL, "DRAW_ELEC_CABLE", "K"),
    ToolSpec("ek.edit", "Kabel bearbeiten", LayerId.ELECTRICAL, "EDIT_ELEC_CABLE"),

    # --- Einrichtung --------------------------------------------------
    ToolSpec("furn.select", "Auswählen", LayerId.FURNITURE, "NONE", "Esc"),
    ToolSpec("furn.polygon", "Möbel zeichnen", LayerId.FURNITURE, "DRAW_FURNITURE_POLY"),
    ToolSpec("furn.move", "Möbel verschieben", LayerId.FURNITURE, "MOVE_FLOOR_PLAN"),

    # --- Vermessung & Annotation --------------------------------------
    ToolSpec("ann.select", "Auswählen", LayerId.ANNOTATION, "NONE", "Esc"),
    ToolSpec("ann.measure", "Distanz messen", LayerId.ANNOTATION, "MEASURE", "D"),
    ToolSpec("ann.measure_angle", "Winkel messen", LayerId.ANNOTATION, "MEASURE_ANGLE"),
    ToolSpec("ann.helper", "Hilfslinie zeichnen", LayerId.ANNOTATION, "DRAW_HELPER_LINE"),
    ToolSpec("ann.edit_helper", "Hilfslinie bearbeiten", LayerId.ANNOTATION, "EDIT_HELPER_LINE"),
    ToolSpec("ann.text", "Text platzieren", LayerId.ANNOTATION, "PLACE_TEXT", "T"),

    # --- Export & Layout ----------------------------------------------
    ToolSpec("exp.select", "Auswählen", LayerId.EXPORT, "NONE", "Esc"),
    ToolSpec("exp.frame", "Exportrahmen zeichnen", LayerId.EXPORT, "DRAW_EXPORT_FRAME"),
)


TOOLS_BY_ID: dict[str, ToolSpec] = {tool.id: tool for tool in TOOLS}


def tools_for_layer(layer: LayerId) -> list[ToolSpec]:
    return [tool for tool in TOOLS if tool.layer is layer]


def tools_for_ids(tool_ids: tuple[str, ...]) -> list[ToolSpec]:
    return [TOOLS_BY_ID[tid] for tid in tool_ids if tid in TOOLS_BY_ID]
