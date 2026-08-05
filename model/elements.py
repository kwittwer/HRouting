"""Typisierte Element-Objekte des HRouting-Datenmodells.

Ein Element bündelt zwei Datenquellen des .hrp-Formats:

* ``data``  – der Eintrag aus ``params.<PARAMS_KEY>[element_id]`` (Konfiguration)
* ``geom``  – die Einträge aller id-basierten ``canvas``-Maps (Geometrie)

Beide werden bewusst als rohe Dicts gehalten, damit das Laden/Speichern
verlustfrei bleibt (unbekannte Felder überleben einen Roundtrip). Der
typisierte Zugriff erfolgt über Properties.
"""

from __future__ import annotations

from typing import Any, ClassVar

from .layers import LayerId

#: canvas-Maps, die für jedes Element existieren können
COMMON_GEOM_KEYS: tuple[str, ...] = (
    "label_positions",
    "label_font_sizes",
    "label_visible",
)


def _param(key: str, default: Any = None) -> property:
    """Property auf einen Schlüssel in ``data`` (params-Eintrag)."""

    def getter(self: "Element") -> Any:
        return self.data.get(key, default)

    def setter(self: "Element", value: Any) -> None:
        self.data[key] = value

    return property(getter, setter, doc=f"params-Feld '{key}'")


def _geom(key: str, default: Any = None) -> property:
    """Property auf eine id-basierte canvas-Map."""

    def getter(self: "Element") -> Any:
        return self.geom.get(key, default)

    def setter(self: "Element", value: Any) -> None:
        if value is None:
            self.geom.pop(key, None)
        else:
            self.geom[key] = value

    return property(getter, setter, doc=f"canvas-Map '{key}'")


class Element:
    """Basisklasse aller Projektelemente."""

    #: ID-Präfix, z. B. ``HK``
    PREFIX: ClassVar[str] = ""
    #: Schlüssel im ``params``-Abschnitt
    PARAMS_KEY: ClassVar[str] = ""
    #: Name des ID-Feldes innerhalb des params-Eintrags
    ID_FIELD: ClassVar[str] = ""
    #: Gewerk/Layer für Selektionsfilter
    LAYER: ClassVar[LayerId] = LayerId.ANNOTATION
    #: id-basierte canvas-Maps, die zu diesem Elementtyp gehören
    GEOM_KEYS: ClassVar[tuple[str, ...]] = ()
    #: Anzeigename für Navigator-Kategorien
    CATEGORY_LABEL: ClassVar[str] = ""

    def __init__(
        self,
        element_id: str,
        data: dict | None = None,
        geom: dict | None = None,
    ) -> None:
        self.id = element_id
        self.data: dict[str, Any] = dict(data or {})
        self.geom: dict[str, Any] = dict(geom or {})

    # -- gemeinsame Felder ------------------------------------------------
    name = _param("name", "")
    color = _param("color", "")
    floor_plan_id = _param("floor_plan_id", "")
    visible = _param("visible", True)
    label_visible = _param("label_visible", True)
    label_size = _param("label_size", 12.0)

    label_pos = _geom("label_positions")
    label_font_size = _geom("label_font_sizes")

    # -- Serialisierung ---------------------------------------------------
    @classmethod
    def create(cls, element_id: str, **fields: Any) -> "Element":
        """Erzeugt ein neues Element inklusive gesetztem ID-Feld."""
        data: dict[str, Any] = dict(fields)
        if cls.ID_FIELD:
            data[cls.ID_FIELD] = element_id
        return cls(element_id, data)

    @classmethod
    def all_geom_keys(cls) -> tuple[str, ...]:
        return cls.GEOM_KEYS + COMMON_GEOM_KEYS

    def to_params(self) -> dict:
        out = dict(self.data)
        if self.ID_FIELD and self.ID_FIELD in out:
            out[self.ID_FIELD] = self.id
        return out

    def to_geom(self) -> dict:
        return {k: v for k, v in self.geom.items() if v is not None}

    def __repr__(self) -> str:  # pragma: no cover - Debughilfe
        return f"<{type(self).__name__} {self.id} {self.name!r}>"


class Circuit(Element):
    """Heizkreis (HK)."""

    PREFIX = "HK"
    PARAMS_KEY = "circuits"
    ID_FIELD = "circuit_id"
    LAYER = LayerId.HEATING
    CATEGORY_LABEL = "Heizkreise"
    GEOM_KEYS = (
        "polygons",
        "start_points",
        "manual_routes",
        "route_wall_dist_px",
        "route_line_dist_px",
        "supply_lines",
        "supply_hkv",
    )

    diameter = _param("diameter", 16.0)
    spacing = _param("spacing", 150.0)
    wall_dist = _param("wall_dist", 200.0)
    room_temp = _param("room_temp", 20.0)
    floor_covering = _param("floor_covering", "")
    distributor = _param("distributor", "")

    polygon = _geom("polygons")
    start_point = _geom("start_points")
    route = _geom("manual_routes")
    route_wall_dist_px = _geom("route_wall_dist_px")
    route_line_dist_px = _geom("route_line_dist_px")
    supply_line = _geom("supply_lines")
    hkv_id = _geom("supply_hkv")


class ElecPoint(Element):
    """Elektro-Anschlusspunkt (AP)."""

    PREFIX = "AP"
    PARAMS_KEY = "elec_points"
    ID_FIELD = "point_id"
    LAYER = LayerId.ELECTRICAL
    CATEGORY_LABEL = "Anschlusspunkte"
    GEOM_KEYS = (
        "elec_points",
        "elec_point_size_px",
        "elec_point_position",
        "elec_point_height",
        "elec_point_notes",
        "elec_point_smarthome_device",
        "elec_point_smarthome_device_color",
        "elec_visible",
    )

    width = _param("width", 30.0)
    height = _param("height", 30.0)
    icon_path = _param("icon_path", "")
    builtin_symbol = _param("builtin_symbol", "")
    position = _param("position", "")
    height_from_floor = _param("height_from_floor", 0.0)
    smarthome_device = _param("smarthome_device", "")
    smarthome_device_color = _param("smarthome_device_color", "")
    note = _param("note", "")
    ap_type = _param("ap_type", "standard")

    pos = _geom("elec_points")
    size_px = _geom("elec_point_size_px")


class ElecRoom(Element):
    """Elektro-Raum (ER)."""

    PREFIX = "ER"
    PARAMS_KEY = "elec_rooms"
    ID_FIELD = "room_id"
    LAYER = LayerId.ELECTRICAL
    CATEGORY_LABEL = "Räume"
    GEOM_KEYS = ("elec_rooms", "elec_room_polygons", "elec_room_visible")

    @property
    def polygon(self) -> list | None:
        return self.geom.get("elec_rooms") or self.geom.get("elec_room_polygons")

    @polygon.setter
    def polygon(self, value: list | None) -> None:
        if value is None:
            self.geom.pop("elec_rooms", None)
            self.geom.pop("elec_room_polygons", None)
        else:
            self.geom["elec_rooms"] = value


class ElecCable(Element):
    """Elektro-Kabel (EK)."""

    PREFIX = "EK"
    PARAMS_KEY = "elec_cables"
    ID_FIELD = "cable_id"
    LAYER = LayerId.ELECTRICAL
    CATEGORY_LABEL = "Kabel"
    GEOM_KEYS = (
        "elec_cables",
        "elec_cable_notes",
        "elec_cable_stroke_width",
        "elec_cable_type_text",
        "elec_cable_type_label_visible",
        "cable_start_ap",
        "cable_end_ap",
        "elec_visible",
    )

    cable_type = _param("type", "")
    comment = _param("comment", "")
    start_ap = _param("start_ap", "")
    end_ap = _param("end_ap", "")

    path = _geom("elec_cables")


class Hkv(Element):
    """Heizkreisverteiler (HKV)."""

    PREFIX = "HKV"
    PARAMS_KEY = "hkv_points"
    ID_FIELD = "hkv_id"
    LAYER = LayerId.HEATING
    CATEGORY_LABEL = "Verteiler"
    GEOM_KEYS = ("hkv_points", "hkv_size_px", "hkv_visible")

    width = _param("width", 50.0)
    height = _param("height", 50.0)
    icon_path = _param("icon_path", "")

    pos = _geom("hkv_points")
    size_px = _geom("hkv_size_px")


class HkvLine(Element):
    """HKV-Verbindungsleitung (HKVL)."""

    PREFIX = "HKVL"
    PARAMS_KEY = "hkv_lines"
    ID_FIELD = "line_id"
    LAYER = LayerId.HEATING
    CATEGORY_LABEL = "HKV-Leitungen"
    GEOM_KEYS = ("hkv_lines", "hkv_line_start", "hkv_line_end", "hkv_line_visible")

    start_hkv = _param("start_hkv", "")
    end_hkv = _param("end_hkv", "")

    path = _geom("hkv_lines")


class TextAnnotation(Element):
    """Text-Annotation (TEXT)."""

    PREFIX = "TEXT"
    PARAMS_KEY = "text_annotations"
    ID_FIELD = "text_id"
    LAYER = LayerId.ANNOTATION
    CATEGORY_LABEL = "Texte"
    GEOM_KEYS = ("text_annotations",)

    @property
    def _entry(self) -> dict:
        entry = self.geom.get("text_annotations")
        return entry if isinstance(entry, dict) else {}

    @property
    def pos(self) -> list | None:
        entry = self.geom.get("text_annotations")
        if isinstance(entry, dict):
            return entry.get("pos")
        return entry

    @property
    def content(self) -> str:
        return self._entry.get("content", self.data.get("content", ""))

    @property
    def font_size(self) -> float:
        return self._entry.get("font_size", self.data.get("font_size", 14.0))


class FloorPlan(Element):
    """Grundriss-Layer.

    Besonderheit: Die Geometrie steht nicht in id-basierten Maps, sondern
    als Eintrag in der Liste ``canvas.floor_plans`` (``self.layer``).
    """

    PREFIX = "grundriss"
    PARAMS_KEY = "floorplans"
    ID_FIELD = ""
    LAYER = LayerId.FLOORPLAN
    CATEGORY_LABEL = "Grundrisse"
    GEOM_KEYS = ("ref_line_colors", "ref_line_visible")

    def __init__(
        self,
        element_id: str,
        data: dict | None = None,
        geom: dict | None = None,
        layer: dict | None = None,
    ) -> None:
        super().__init__(element_id, data, geom)
        #: Eintrag aus ``canvas.floor_plans``
        self.layer: dict[str, Any] = dict(layer or {})

    file_path = _param("file_path", "")
    polygon_color = _param("polygon_color", "#8d99ae")

    @property
    def mm_per_px(self) -> float:
        return float(self.layer.get("mm_per_px", 1.0))

    @property
    def opacity(self) -> float:
        return float(self.layer.get("opacity", self.data.get("opacity", 1.0)))

    @property
    def rotation(self) -> float:
        return float(self.layer.get("rotation", self.data.get("rotation", 0.0)))

    @property
    def offset(self) -> tuple[float, float]:
        return (
            float(self.layer.get("offset_x", 0.0)),
            float(self.layer.get("offset_y", 0.0)),
        )

    def to_layer(self) -> dict:
        out = dict(self.layer)
        out["fp_id"] = self.id
        return out


class Furniture(FloorPlan):
    """Einrichtungs-Objekt – technisch ein Floor-Plan-Layer."""

    PREFIX = "einrichtung"
    PARAMS_KEY = "furniture"
    LAYER = LayerId.FURNITURE
    CATEGORY_LABEL = "Einrichtung"


#: Reihenfolge bestimmt Navigator-Kategorien und Ladeschritte
ELEMENT_TYPES: tuple[type[Element], ...] = (
    Circuit,
    Hkv,
    HkvLine,
    ElecPoint,
    ElecRoom,
    ElecCable,
    TextAnnotation,
)
