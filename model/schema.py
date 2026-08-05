"""Deklarative Feldbeschreibung der Elementtypen.

Grundlage für die modellgetriebenen Eigenschaften-Editoren: Statt für jeden
Elementtyp ein eigenes Panel zu pflegen, beschreibt dieses Modul, welche Felder
ein Typ besitzt und wie sie dargestellt werden. Die GUI erzeugt daraus
automatisch die passenden Widgets.

Einheiten-Hinweis: Das Dateiformat speichert Längen in Millimetern, die
Oberfläche zeigt sie in Zentimetern. ``FieldSpec.scale`` regelt die Umrechnung
(``scale=10`` → Anzeige = Wert / 10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .elements import (
    Circuit,
    ElecCable,
    ElecPoint,
    ElecRoom,
    Element,
    FloorPlan,
    Furniture,
    Hkv,
    HkvLine,
    TextAnnotation,
)


class FieldKind(str, Enum):
    """Darstellungsform eines Feldes."""

    TEXT = "text"
    MULTILINE = "multiline"
    NUMBER = "number"
    BOOL = "bool"
    COLOR = "color"
    CHOICE = "choice"
    EDITABLE_CHOICE = "editable_choice"
    FILE = "file"
    READONLY = "readonly"


@dataclass(frozen=True)
class FieldSpec:
    """Beschreibung eines editierbaren Feldes."""

    key: str
    label: str
    kind: FieldKind = FieldKind.TEXT

    # Numerische Felder
    minimum: float = 0.0
    maximum: float = 999_999.0
    step: float = 1.0
    decimals: int = 2
    #: Faktor zwischen gespeichertem Wert und Anzeige (10 = mm -> cm)
    scale: float = 1.0

    unit: str = ""
    default: Any = None
    #: Auswahlmöglichkeiten; entweder Liste oder Callable für dynamische Werte
    options: tuple[str, ...] | Callable[[], tuple[str, ...]] = ()
    tooltip: str = ""
    #: Feld nur anzeigen, wenn ``depends_on`` diesen Wert hat: (key, wert)
    depends_on: tuple[str, Any] | None = None
    #: Gruppenüberschrift im Formular
    group: str = ""
    file_filter: str = ""

    def resolve_options(self) -> tuple[str, ...]:
        if callable(self.options):
            return tuple(self.options())
        return tuple(self.options)

    def to_display(self, value: Any) -> Any:
        """Gespeicherter Wert -> Anzeigewert."""
        if self.kind is FieldKind.NUMBER and self.scale != 1.0:
            try:
                return float(value) / self.scale
            except (TypeError, ValueError):
                return value
        return value

    def to_storage(self, value: Any) -> Any:
        """Anzeigewert -> gespeicherter Wert."""
        if self.kind is FieldKind.NUMBER and self.scale != 1.0:
            try:
                return float(value) * self.scale
            except (TypeError, ValueError):
                return value
        return value


@dataclass(frozen=True)
class ActionSpec:
    """Eine Schaltfläche im Eigenschaften-Editor."""

    id: str
    label: str
    tooltip: str = ""
    #: Aktion ist nur sinnvoll, wenn das Element Geometrie besitzt
    requires_geometry: bool = False
    destructive: bool = False
    #: Nur anzeigen, wenn das Feld ``key`` einen der Werte hat: (key, werte)
    depends_on: tuple[str, tuple[Any, ...]] | None = None

    def is_visible_for(self, values: dict[str, Any]) -> bool:
        """Prüft, ob die Aktion beim aktuellen Feldzustand angezeigt wird."""
        if self.depends_on is None:
            return True
        key, allowed = self.depends_on
        return values.get(key) in allowed


@dataclass(frozen=True)
class ElementSchema:
    """Alle Felder und Aktionen eines Elementtyps."""

    element_cls: type[Element]
    title: str
    fields: tuple[FieldSpec, ...] = ()
    actions: tuple[ActionSpec, ...] = ()
    #: Berechnete Anzeigewerte (Schlüssel -> Beschriftung)
    computed: tuple[tuple[str, str], ...] = ()


# ---------------------------------------------------------------------------
# Dynamische Optionslisten
# ---------------------------------------------------------------------------


def _floor_covering_options() -> tuple[str, ...]:
    from logic.heating_calc import FLOOR_COVERINGS  # noqa: PLC0415

    return tuple(FLOOR_COVERINGS.keys())


def _builtin_symbol_options() -> tuple[str, ...]:
    try:
        from gui.parameter_panel import BUILTIN_SYMBOLS  # noqa: PLC0415

        return tuple(BUILTIN_SYMBOLS.keys())
    except Exception:  # pragma: no cover - Symbolordner fehlt
        return ("(kein Symbol)",)


AP_POSITIONS = ("Wand", "Decke", "Boden", "Freitext")
AP_TYPES = ("standard", "uv", "up_distribution", "hak", "zaehler")
SMARTHOME_DEVICES = ("", "Shelly", "Sonoff ZBMINIR2")
SMARTHOME_COLORS = ("", "weiß", "schwarz")
CABLE_TYPES = ("3x1,5", "5x1,5", "3x2,5", "5x2,5", "NYM-J 3x1,5", "NYM-J 5x2,5")


# ---------------------------------------------------------------------------
# Gemeinsame Felder
# ---------------------------------------------------------------------------


def _common_fields(default_color: str) -> tuple[FieldSpec, ...]:
    return (
        FieldSpec("name", "Name", FieldKind.TEXT, group="Allgemein"),
        FieldSpec("color", "Farbe", FieldKind.COLOR, default=default_color,
                  group="Allgemein"),
        FieldSpec("visible", "Sichtbar", FieldKind.BOOL, default=True,
                  group="Allgemein"),
    )


def _label_fields() -> tuple[FieldSpec, ...]:
    return (
        FieldSpec("label_visible", "Beschriftung anzeigen", FieldKind.BOOL,
                  default=True, group="Beschriftung"),
        FieldSpec("label_size", "Schriftgröße", FieldKind.NUMBER, minimum=0.1,
                  maximum=999.0, step=1.0, decimals=1, unit="pt", default=12.0,
                  group="Beschriftung"),
    )


# ---------------------------------------------------------------------------
# Schemata je Elementtyp
# ---------------------------------------------------------------------------

CIRCUIT_SCHEMA = ElementSchema(
    element_cls=Circuit,
    title="Heizkreis",
    fields=(
        *_common_fields("#2a9d8f"),
        FieldSpec("diameter", "Rohrdurchmesser", FieldKind.NUMBER, minimum=0.1,
                  maximum=9999.0, step=0.05, decimals=2, scale=10.0, unit="cm",
                  default=16.0, group="Verlegung"),
        FieldSpec("spacing", "Verlegeabstand", FieldKind.NUMBER, minimum=0.1,
                  maximum=9999.0, step=0.5, decimals=1, scale=10.0, unit="cm",
                  default=150.0, group="Verlegung"),
        FieldSpec("wall_dist", "Randabstand", FieldKind.NUMBER, minimum=0.1,
                  maximum=9999.0, step=0.5, decimals=1, scale=10.0, unit="cm",
                  default=200.0, group="Verlegung"),
        FieldSpec("room_temp", "Soll-Raumtemperatur", FieldKind.NUMBER,
                  minimum=-50.0, maximum=200.0, step=0.5, decimals=1, unit="°C",
                  default=20.0, group="Auslegung"),
        FieldSpec("floor_covering", "Fußbodenbelag", FieldKind.CHOICE,
                  options=_floor_covering_options, group="Auslegung"),
        FieldSpec("distributor", "Heizkreisverteiler", FieldKind.TEXT,
                  group="Auslegung"),
        *_label_fields(),
    ),
    actions=(
        ActionSpec("draw_polygon", "Raum zeichnen",
                   "Raumpolygon neu zeichnen"),
        ActionSpec("edit_polygon", "Raum bearbeiten",
                   "Polygonpunkte verschieben", requires_geometry=True),
        ActionSpec("draw_route", "Rohrverlauf zeichnen"),
        ActionSpec("edit_route", "Rohrverlauf bearbeiten",
                   requires_geometry=True),
        ActionSpec("draw_supply", "Versorgungsleitung zeichnen"),
        ActionSpec("edit_supply", "Versorgungsleitung bearbeiten",
                   requires_geometry=True),
        ActionSpec("delete", "Löschen", destructive=True),
    ),
    computed=(
        ("area_m2", "Fläche"),
        ("perimeter_m", "Umfang"),
        ("pipe_length_m", "Rohrlänge"),
        ("supply_length_m", "Zuleitung"),
        ("total_length_m", "Gesamtlänge"),
        ("power_w", "Heizleistung"),
        ("q_wm2", "Spez. Leistung"),
        ("volume_flow_lmin", "Volumenstrom"),
        ("pressure_drop_mbar", "Druckverlust"),
    ),
)


ELEC_POINT_SCHEMA = ElementSchema(
    element_cls=ElecPoint,
    title="Anschlusspunkt",
    fields=(
        *_common_fields("#4fc3f7"),
        FieldSpec("builtin_symbol", "Symbol", FieldKind.CHOICE,
                  options=_builtin_symbol_options, group="Darstellung"),
        FieldSpec("icon_path", "Eigenes Bild", FieldKind.FILE,
                  file_filter="Bilder (*.png *.jpg *.jpeg *.svg)",
                  group="Darstellung"),
        FieldSpec("width", "Breite", FieldKind.NUMBER, minimum=0.1,
                  maximum=9999.0, step=0.5, decimals=1, scale=10.0, unit="cm",
                  default=30.0, group="Darstellung"),
        FieldSpec("height", "Höhe", FieldKind.NUMBER, minimum=0.1,
                  maximum=9999.0, step=0.5, decimals=1, scale=10.0, unit="cm",
                  default=30.0, group="Darstellung"),
        FieldSpec("ap_type", "AP-Typ", FieldKind.CHOICE, options=AP_TYPES,
                  default="standard", group="Elektro"),
        FieldSpec("position", "Position", FieldKind.CHOICE,
                  options=AP_POSITIONS, default="Wand", group="Elektro"),
        FieldSpec("height_from_floor", "Höhe über Boden", FieldKind.NUMBER,
                  minimum=0.0, maximum=9999.0, step=1.0, decimals=1,
                  scale=10.0, unit="cm", default=0.0, group="Elektro"),
        FieldSpec("smarthome_device", "Unterputz-Gerät",
                  FieldKind.EDITABLE_CHOICE, options=SMARTHOME_DEVICES,
                  group="Elektro"),
        FieldSpec("smarthome_device_color", "Gerätefarbe",
                  FieldKind.EDITABLE_CHOICE, options=SMARTHOME_COLORS,
                  group="Elektro"),
        FieldSpec("note", "Notiz", FieldKind.MULTILINE, group="Notiz"),
        *_label_fields(),
    ),
    actions=(
        ActionSpec("place", "Neu platzieren"),
        ActionSpec("duplicate", "Duplizieren"),
        ActionSpec("configure_uv", "Unterverteilung planen…",
                   "Reihen, Module und Phasenschienen konfigurieren",
                   depends_on=("ap_type", ("uv",))),
        ActionSpec("configure_up", "Verteilung in Dose…",
                   "Aderzuordnung in der Unterputzdose",
                   depends_on=("ap_type", ("up_distribution",))),
        ActionSpec("configure_hak", "HAK konfigurieren…",
                   depends_on=("ap_type", ("hak",))),
        ActionSpec("configure_zaehler", "Zähler konfigurieren…",
                   depends_on=("ap_type", ("zaehler",))),
        ActionSpec("delete", "Löschen", destructive=True),
    ),
)


ELEC_ROOM_SCHEMA = ElementSchema(
    element_cls=ElecRoom,
    title="Elektro-Raum",
    fields=(
        *_common_fields("#43aa8b"),
        *_label_fields(),
    ),
    actions=(
        ActionSpec("draw_polygon", "Raum zeichnen"),
        ActionSpec("edit_polygon", "Raum bearbeiten", requires_geometry=True),
        ActionSpec("delete", "Löschen", destructive=True),
    ),
    computed=(("area_m2", "Fläche"),),
)


ELEC_CABLE_SCHEMA = ElementSchema(
    element_cls=ElecCable,
    title="Elektro-Kabel",
    fields=(
        *_common_fields("#ff9800"),
        FieldSpec("type", "Kabeltyp", FieldKind.EDITABLE_CHOICE,
                  options=CABLE_TYPES, default="5x1,5", group="Kabel"),
        FieldSpec("stroke_width", "Strichstärke", FieldKind.NUMBER,
                  minimum=0.5, maximum=10.0, step=0.5, decimals=1, unit="px",
                  default=2.0, group="Darstellung"),
        FieldSpec("comment", "Kommentar", FieldKind.MULTILINE, group="Notiz"),
        *_label_fields(),
    ),
    actions=(
        ActionSpec("draw_cable", "Kabel zeichnen"),
        ActionSpec("edit_cable", "Kabel bearbeiten", requires_geometry=True),
        ActionSpec("duplicate", "Duplizieren"),
        ActionSpec("delete", "Löschen", destructive=True),
    ),
    computed=(
        ("length_m", "Länge"),
        ("start_ap_name", "Start"),
        ("end_ap_name", "Ziel"),
    ),
)


HKV_SCHEMA = ElementSchema(
    element_cls=Hkv,
    title="Heizkreisverteiler",
    fields=(
        *_common_fields("#e53935"),
        FieldSpec("width", "Breite", FieldKind.NUMBER, minimum=0.1,
                  maximum=9999.0, step=1.0, decimals=1, scale=10.0, unit="cm",
                  default=50.0, group="Darstellung"),
        FieldSpec("height", "Höhe", FieldKind.NUMBER, minimum=0.1,
                  maximum=9999.0, step=1.0, decimals=1, scale=10.0, unit="cm",
                  default=50.0, group="Darstellung"),
        FieldSpec("icon_path", "Symbol", FieldKind.FILE,
                  file_filter="Bilder (*.png *.jpg *.jpeg *.svg)",
                  group="Darstellung"),
        *_label_fields(),
    ),
    actions=(
        ActionSpec("place", "Neu platzieren"),
        ActionSpec("delete", "Löschen", destructive=True),
    ),
)


HKV_LINE_SCHEMA = ElementSchema(
    element_cls=HkvLine,
    title="HKV-Leitung",
    fields=(
        *_common_fields("#9c27b0"),
        *_label_fields(),
    ),
    actions=(
        ActionSpec("draw_line", "Leitung zeichnen"),
        ActionSpec("edit_line", "Leitung bearbeiten", requires_geometry=True),
        ActionSpec("delete", "Löschen", destructive=True),
    ),
    computed=(("length_m", "Länge"),),
)


TEXT_SCHEMA = ElementSchema(
    element_cls=TextAnnotation,
    title="Text",
    fields=(
        FieldSpec("name", "Name", FieldKind.TEXT, group="Allgemein"),
        FieldSpec("content", "Inhalt", FieldKind.MULTILINE, group="Allgemein"),
        FieldSpec("color", "Farbe", FieldKind.COLOR, default="#ffffff",
                  group="Darstellung"),
        FieldSpec("font_size", "Schriftgröße", FieldKind.NUMBER, minimum=1.0,
                  maximum=999.0, step=1.0, decimals=1, unit="pt", default=14.0,
                  group="Darstellung"),
        FieldSpec("visible", "Sichtbar", FieldKind.BOOL, default=True,
                  group="Allgemein"),
        FieldSpec("comment", "Kommentar", FieldKind.MULTILINE, group="Notiz"),
    ),
    actions=(
        ActionSpec("place", "Neu platzieren"),
        ActionSpec("delete", "Löschen", destructive=True),
    ),
)


FLOOR_PLAN_SCHEMA = ElementSchema(
    element_cls=FloorPlan,
    title="Grundriss",
    fields=(
        FieldSpec("name", "Name", FieldKind.TEXT, group="Allgemein"),
        FieldSpec("visible", "Sichtbar", FieldKind.BOOL, default=True,
                  group="Allgemein"),
        FieldSpec("file_path", "Bilddatei", FieldKind.FILE,
                  file_filter="Bilder (*.png *.jpg *.jpeg *.svg)",
                  group="Darstellung"),
        FieldSpec("opacity", "Deckkraft", FieldKind.NUMBER, minimum=0.0,
                  maximum=1.0, step=0.05, decimals=2, default=1.0,
                  group="Darstellung"),
        FieldSpec("polygon_color", "Umrissfarbe", FieldKind.COLOR,
                  default="#8d99ae", group="Darstellung"),
        FieldSpec("offset_x", "Versatz X", FieldKind.NUMBER, minimum=-999_999.0,
                  maximum=999_999.0, step=1.0, decimals=1, unit="px",
                  default=0.0, group="Lage"),
        FieldSpec("offset_y", "Versatz Y", FieldKind.NUMBER, minimum=-999_999.0,
                  maximum=999_999.0, step=1.0, decimals=1, unit="px",
                  default=0.0, group="Lage"),
        FieldSpec("rotation", "Drehung", FieldKind.NUMBER, minimum=-360.0,
                  maximum=360.0, step=1.0, decimals=1, unit="°", default=0.0,
                  group="Lage"),
        FieldSpec("ref_length_mm", "Referenzlänge", FieldKind.NUMBER,
                  minimum=0.0, maximum=999_999.0, step=100.0, decimals=1,
                  unit="mm", default=5000.0, group="Maßstab"),
        FieldSpec("ref_line_visible", "Referenzlinie anzeigen", FieldKind.BOOL,
                  default=True, group="Maßstab"),
        FieldSpec("ref_line_color", "Referenzlinienfarbe", FieldKind.COLOR,
                  default="#ffdd00", group="Maßstab"),
    ),
    actions=(
        ActionSpec("choose_image", "Bild wählen…"),
        ActionSpec("draw_ref_line", "Referenzlinie zeichnen"),
        ActionSpec("move", "Verschieben"),
        ActionSpec("rotate", "Drehen"),
        ActionSpec("draw_polygon", "Umriss zeichnen"),
        ActionSpec("delete", "Löschen", destructive=True),
    ),
    computed=(("mm_per_px", "Maßstab"),),
)


FURNITURE_SCHEMA = ElementSchema(
    element_cls=Furniture,
    title="Einrichtung",
    fields=(
        FieldSpec("name", "Name", FieldKind.TEXT, group="Allgemein"),
        FieldSpec("visible", "Sichtbar", FieldKind.BOOL, default=True,
                  group="Allgemein"),
        FieldSpec("polygon_color", "Farbe", FieldKind.COLOR, default="#8d99ae",
                  group="Darstellung"),
        FieldSpec("opacity", "Deckkraft", FieldKind.NUMBER, minimum=0.0,
                  maximum=1.0, step=0.05, decimals=2, default=1.0,
                  group="Darstellung"),
        FieldSpec("fixed_width_mm", "Breite", FieldKind.NUMBER, minimum=0.0,
                  maximum=999_999.0, step=10.0, decimals=1, scale=10.0,
                  unit="cm", default=0.0, group="Abmessungen"),
        FieldSpec("fixed_height_mm", "Höhe", FieldKind.NUMBER, minimum=0.0,
                  maximum=999_999.0, step=10.0, decimals=1, scale=10.0,
                  unit="cm", default=0.0, group="Abmessungen"),
        FieldSpec("offset_x", "Versatz X", FieldKind.NUMBER, minimum=-999_999.0,
                  maximum=999_999.0, step=1.0, decimals=1, unit="px",
                  default=0.0, group="Lage"),
        FieldSpec("offset_y", "Versatz Y", FieldKind.NUMBER, minimum=-999_999.0,
                  maximum=999_999.0, step=1.0, decimals=1, unit="px",
                  default=0.0, group="Lage"),
        FieldSpec("rotation", "Drehung", FieldKind.NUMBER, minimum=-360.0,
                  maximum=360.0, step=1.0, decimals=1, unit="°", default=0.0,
                  group="Lage"),
    ),
    actions=(
        ActionSpec("draw_polygon", "Umriss zeichnen"),
        ActionSpec("move", "Verschieben"),
        ActionSpec("delete", "Löschen", destructive=True),
    ),
)


#: Globale Projektparameter (angezeigt, wenn nichts selektiert ist)
GLOBAL_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("t_supply", "Vorlauftemperatur", FieldKind.NUMBER, minimum=-50.0,
              maximum=200.0, step=0.5, decimals=1, unit="°C", default=35.0,
              group="Heizung"),
    FieldSpec("t_return", "Rücklauftemperatur", FieldKind.NUMBER, minimum=-50.0,
              maximum=200.0, step=0.5, decimals=1, unit="°C", default=30.0,
              group="Heizung"),
    FieldSpec("t_norm_outdoor", "Normaußentemperatur", FieldKind.NUMBER,
              minimum=-50.0, maximum=200.0, step=0.5, decimals=1, unit="°C",
              default=-12.0, group="Heizung"),
)


SCHEMAS: tuple[ElementSchema, ...] = (
    CIRCUIT_SCHEMA,
    ELEC_POINT_SCHEMA,
    ELEC_ROOM_SCHEMA,
    ELEC_CABLE_SCHEMA,
    HKV_SCHEMA,
    HKV_LINE_SCHEMA,
    TEXT_SCHEMA,
    FLOOR_PLAN_SCHEMA,
    FURNITURE_SCHEMA,
)

_SCHEMA_BY_CLASS: dict[type[Element], ElementSchema] = {
    schema.element_cls: schema for schema in SCHEMAS
}


def schema_for(element: Element | type[Element]) -> ElementSchema | None:
    """Liefert das Schema für ein Element oder eine Elementklasse."""
    element_cls = element if isinstance(element, type) else type(element)
    return _SCHEMA_BY_CLASS.get(element_cls)


def groups_of(schema: ElementSchema) -> list[tuple[str, list[FieldSpec]]]:
    """Gruppiert die Felder eines Schemas in Reihenfolge ihres Auftretens."""
    ordered: list[tuple[str, list[FieldSpec]]] = []
    index: dict[str, list[FieldSpec]] = {}
    for spec in schema.fields:
        group = spec.group or "Allgemein"
        if group not in index:
            index[group] = []
            ordered.append((group, index[group]))
        index[group].append(spec)
    return ordered
