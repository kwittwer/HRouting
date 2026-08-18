"""Bindung der Canvas-Datencontainer an das :class:`~model.document.Document`.

Diese Tabelle ist die Brücke aus Phase A: Sie beschreibt deklarativ, welches
Canvas-Attribut auf welche Stelle im Dokument abgebildet wird. ``bind_canvas``
ersetzt die betreffenden Attribute durch Proxy-Views, sodass der Canvas
weiterhin mit gewohnter dict-Syntax arbeitet, die Daten aber im Dokument liegen.

Die Umstellung erfolgt gestaffelt (Plan A4.1 – A4.7). ``STAGES`` gibt an,
welche Gruppe zu welcher Stufe gehört; ``bind_canvas`` kann damit schrittweise
aktiviert und getestet werden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .document import Document
from .elements import (
        AngleMeasurement,
        AnnotationCircle,
        AnnotationEllipse,
        AnnotationLine,
        AnnotationPolyline,
        AnnotationRectangle,
        AnnotationPolygon,
    Circuit,
        DistanceMeasurement,
    ElecCable,
    ElecPoint,
    ElecRoom,
    Element,
    FloorPlan,
    Hkv,
    HkvLine,
    TextAnnotation,
)
from .views import (
    POINT,
    POINT_LIST,
    RAW,
    SIZE,
    DocumentMapView,
    FloorPlanLayerView,
    NestedEntryView,
    NestedViewMapView,
    ParamsMapView,
)

#: Alle Elementtypen, die Labels und Sichtbarkeit besitzen
_LABELLED = (Circuit, ElecPoint, ElecRoom, ElecCable, Hkv, HkvLine, TextAnnotation)


@dataclass(frozen=True)
class Binding:
    """Beschreibt die Abbildung eines Canvas-Attributs auf das Dokument."""

    attr: str
    """Attributname im Canvas, z. B. ``_polygons``."""

    stage: str
    """Stufe der gestaffelten Umstellung (``A4.1`` … ``A4.7``)."""

    kind: str = "geom"
    """``geom`` | ``nested`` | ``params``"""

    geom_key: str = ""
    """Schlüssel der canvas-Map bzw. des verschachtelten Eintrags."""

    field_name: str = ""
    """Feld innerhalb eines verschachtelten Eintrags oder in ``params``."""

    element_cls: type[Element] | None = None
    element_classes: tuple[type[Element], ...] = ()
    converters: tuple[Callable[[Any], Any], Callable[[Any], Any]] = RAW
    default: Any = None
    has_default: bool = False


BINDINGS: tuple[Binding, ...] = (
    # -- A4.1  Text-Annotationen ----------------------------------------
    Binding("_text_annotations", "A4.1", "nested", "text_annotations", "pos",
            element_cls=TextAnnotation, converters=POINT),
    Binding("_text_contents", "A4.1", "nested", "text_annotations", "content",
            element_cls=TextAnnotation, default=""),
    Binding("_text_font_sizes", "A4.1", "nested", "text_annotations", "font_size",
            element_cls=TextAnnotation, default=14.0),
    Binding("_text_colors", "A4.1", "nested", "text_annotations", "color",
            element_cls=TextAnnotation, default="#ffffff"),
    Binding("_text_comments", "A4.1", "nested", "text_annotations", "comment",
            element_cls=TextAnnotation, default=""),
    Binding("_text_visible", "A4.1", "nested", "text_annotations", "visible",
            element_cls=TextAnnotation, default=True),

    # -- A4.2  Punkt-Elemente -------------------------------------------
    Binding("_elec_points", "A4.2", "geom", "elec_points",
            element_cls=ElecPoint, converters=POINT),
    Binding("_elec_point_size_px", "A4.2", "geom", "elec_point_size_px",
            element_cls=ElecPoint, converters=SIZE),
    Binding("_hkv_points", "A4.2", "geom", "hkv_points",
            element_cls=Hkv, converters=POINT),
    Binding("_hkv_size_px", "A4.2", "geom", "hkv_size_px",
            element_cls=Hkv, converters=SIZE),

    # -- A4.3  Metadaten, Labels, Sichtbarkeit ---------------------------
    Binding("_elec_point_position", "A4.3", "geom", "elec_point_position",
            element_cls=ElecPoint, default="", has_default=True),
    Binding("_elec_point_height", "A4.3", "geom", "elec_point_height",
            element_cls=ElecPoint, default=0.0, has_default=True),
    Binding("_elec_point_notes", "A4.3", "geom", "elec_point_notes",
            element_cls=ElecPoint, default="", has_default=True),
    Binding("_elec_point_smarthome_device", "A4.3", "geom",
            "elec_point_smarthome_device", element_cls=ElecPoint,
            default="", has_default=True),
    Binding("_elec_point_smarthome_device_color", "A4.3", "geom",
            "elec_point_smarthome_device_color", element_cls=ElecPoint,
            default="", has_default=True),
    Binding("_label_positions", "A4.3", "geom", "label_positions",
            element_cls=None, converters=POINT),
    Binding("_label_font_sizes", "A4.3", "params", field_name="label_size",
            element_classes=_LABELLED, default=12.0),
    Binding("_label_visible", "A4.3", "params", field_name="label_visible",
            element_classes=_LABELLED, default=True),
    Binding("_circuit_visible", "A4.3", "params", field_name="visible",
            element_classes=(Circuit,), default=True),
    Binding("_elec_room_visible", "A4.3", "params", field_name="visible",
            element_classes=(ElecRoom,), default=True),
    Binding("_elec_visible", "A4.3", "params", field_name="visible",
            element_classes=(ElecPoint, ElecCable), default=True),
    Binding("_hkv_visible", "A4.3", "params", field_name="visible",
            element_classes=(Hkv,), default=True),
    Binding("_hkv_line_visible", "A4.3", "params", field_name="visible",
            element_classes=(HkvLine,), default=True),

    # -- A4.4  Kabel und Leitungen ---------------------------------------
    Binding("_elec_cables", "A4.4", "geom", "elec_cables",
            element_cls=ElecCable, converters=POINT_LIST),
    Binding("_elec_cable_notes", "A4.4", "geom", "elec_cable_notes",
            element_cls=ElecCable, default="", has_default=True),
    Binding("_elec_cable_stroke_width", "A4.4", "geom", "elec_cable_stroke_width",
            element_cls=ElecCable, default=2.0, has_default=True),
    Binding("_elec_cable_type_text", "A4.4", "geom", "elec_cable_type_text",
            element_cls=ElecCable, default="", has_default=True),
    Binding("_elec_cable_type_label_visible", "A4.4", "geom",
            "elec_cable_type_label_visible", element_cls=ElecCable,
            default=False, has_default=True),
    Binding("_cable_start_ap", "A4.4", "geom", "cable_start_ap",
            element_cls=ElecCable, default="", has_default=True),
    Binding("_cable_end_ap", "A4.4", "geom", "cable_end_ap",
            element_cls=ElecCable, default="", has_default=True),
    Binding("_hkv_lines", "A4.4", "geom", "hkv_lines",
            element_cls=HkvLine, converters=POINT_LIST),
    Binding("_hkv_line_start", "A4.4", "geom", "hkv_line_start",
            element_cls=HkvLine, default="", has_default=True),
    Binding("_hkv_line_end", "A4.4", "geom", "hkv_line_end",
            element_cls=HkvLine, default="", has_default=True),
    Binding("_supply_lines", "A4.4", "geom", "supply_lines",
            element_cls=Circuit, converters=POINT_LIST),
    Binding("_supply_hkv", "A4.4", "geom", "supply_hkv",
            element_cls=Circuit, default="", has_default=True),

    # -- A4.5  Polygone und Routen ---------------------------------------
    Binding("_polygons", "A4.5", "geom", "polygons",
            element_cls=Circuit, converters=POINT_LIST),
    Binding("_start_points", "A4.5", "geom", "start_points",
            element_cls=Circuit, converters=POINT),
    Binding("_manual_routes", "A4.5", "geom", "manual_routes",
            element_cls=Circuit, converters=POINT_LIST),
    Binding("_route_wall_dist_px", "A4.5", "geom", "route_wall_dist_px",
            element_cls=Circuit, default=0.0, has_default=True),
    Binding("_route_line_dist_px", "A4.5", "geom", "route_line_dist_px",
            element_cls=Circuit, default=0.0, has_default=True),
    Binding("_elec_room_polygons", "A4.5", "geom", "elec_rooms",
            element_cls=ElecRoom, converters=POINT_LIST),

    # -- A4.6  Referenzlinien je Grundriss ---------------------------------
    Binding("_ref_line_visible", "A4.6", "geom", "ref_line_visible",
            element_cls=FloorPlan, default=True, has_default=True),
    Binding("_ref_line_colors", "A4.6", "geom", "ref_line_colors",
            element_cls=FloorPlan, default="#ffdd00", has_default=True),

    # -- A4.7  Hilfslinien je Grundriss -----------------------------------
    Binding("_floor_helper_lines", "A4.7", "nested_view",
            geom_key="floor_helper_lines", converters=POINT_LIST),
    Binding("_floor_helper_line_visible", "A4.7", "nested_view",
            geom_key="floor_helper_line_visible", converters=RAW),
    Binding("_helper_label_positions", "A4.7", "nested_view",
            geom_key="helper_label_positions", converters=RAW),

    # -- A4.8  Persistente Vermessung ------------------------------------
    Binding("_persisted_distance_measurements", "A4.8", "geom", "distance_measurements",
            element_cls=DistanceMeasurement, converters=POINT_LIST),
    Binding("_persisted_distance_label_positions", "A4.8", "geom", "distance_label_positions",
            element_cls=DistanceMeasurement, converters=POINT),
    Binding("_persisted_angle_measurements", "A4.8", "geom", "angle_measurements",
            element_cls=AngleMeasurement, converters=POINT_LIST),
    Binding("_persisted_angle_label_positions", "A4.8", "geom", "angle_label_positions",
            element_cls=AngleMeasurement, converters=POINT),

    # -- A4.9  Zeichnungs-Annotations ------------------------------------
    Binding("_annotation_lines", "A4.9", "geom", "annotation_lines",
            element_cls=AnnotationLine, converters=RAW),
    Binding("_annotation_rectangles", "A4.9", "geom", "annotation_rectangles",
            element_cls=AnnotationRectangle, converters=RAW),
    Binding("_annotation_polylines", "A4.9", "geom", "annotation_polylines",
            element_cls=AnnotationPolyline, converters=RAW),
    Binding("_annotation_circles", "A4.9", "geom", "annotation_circles",
            element_cls=AnnotationCircle, converters=RAW),
    Binding("_annotation_ellipses", "A4.9", "geom", "annotation_ellipses",
            element_cls=AnnotationEllipse, converters=RAW),
    Binding("_annotation_polygons", "A4.9", "geom", "annotation_polygons",
            element_cls=AnnotationPolygon, converters=RAW),
)

#: Reihenfolge der Umstellungsstufen
STAGES: tuple[str, ...] = ("A4.1", "A4.2", "A4.3", "A4.4", "A4.5", "A4.6", "A4.7", "A4.8", "A4.9")

#: ``document.view``-Schlüssel, die bereits über Views gebunden sind
BOUND_VIEW_KEYS: frozenset[str] = frozenset(
    binding.geom_key for binding in BINDINGS if binding.kind == "nested_view"
)


def bindings_for(stages: tuple[str, ...] | None = None) -> list[Binding]:
    """Liefert alle Bindungen der angegebenen Stufen (Standard: alle)."""
    if stages is None:
        return list(BINDINGS)
    allowed = set(stages)
    return [binding for binding in BINDINGS if binding.stage in allowed]


def build_view(
    document: Document,
    binding: Binding,
    on_change: Callable[[str], None] | None = None,
):
    """Erzeugt die zur Bindung passende Proxy-View."""
    if binding.kind == "nested_view":
        return NestedViewMapView(
            document, binding.geom_key, binding.converters, on_change
        )
    if binding.kind == "params":
        return ParamsMapView(
            document,
            binding.field_name,
            binding.element_classes,
            binding.converters,
            binding.default,
            on_change,
        )
    if binding.kind == "nested":
        assert binding.element_cls is not None
        return NestedEntryView(
            document,
            binding.geom_key,
            binding.field_name,
            binding.element_cls,
            binding.converters,
            binding.default,
            on_change,
        )
    assert binding.element_cls is not None
    return DocumentMapView(
        document,
        binding.geom_key,
        binding.element_cls,
        binding.converters,
        binding.default,
        binding.has_default,
        on_change,
    )


def bind_floor_plans(
    canvas: Any,
    document: Document,
    on_change: Callable[[str], None] | None = None,
) -> None:
    """Ersetzt die Grundriss-Layer durch dokumentgebundene Proxies (A4.6).

    Bilddaten (``renderer``, ``pixmap``, ``size``) werden aus den bisherigen
    Layern übernommen, da sie nicht Teil des Projektformats sind.
    """
    existing = getattr(canvas, "_floor_plans", {}) or {}
    bound: dict[str, FloorPlanLayerView] = {}

    for fp_id, element in {**document.floorplans, **document.furniture}.items():
        view = FloorPlanLayerView(element, on_change)
        previous = existing.get(fp_id)
        if previous is not None:
            view.renderer = getattr(previous, "renderer", None)
            view.pixmap = getattr(previous, "pixmap", None)
            view.size = getattr(previous, "size", (100.0, 100.0))
        bound[fp_id] = view

    canvas._floor_plans = bound

    order = [fid for fid in document.floorplan_order if fid in bound]
    order += [fid for fid in bound if fid not in order]
    canvas._floor_plan_order = order


def bind_canvas(
    canvas: Any,
    document: Document,
    stages: tuple[str, ...] | None = None,
    on_change: Callable[[str], None] | None = None,
) -> None:
    """Ersetzt die Canvas-Datencontainer durch Views auf das Dokument.

    ``_label_positions`` ist ein Sonderfall: Labels existieren für alle
    Elementtypen, daher wird dort über sämtliche Container hinweg gesucht.
    """
    for binding in bindings_for(stages):
        if binding.attr == "_label_positions":
            view = _MultiTypeGeomView(document, "label_positions", _LABELLED, POINT)
        else:
            view = build_view(document, binding, on_change)
        setattr(canvas, binding.attr, view)

    if stages is None or "A4.6" in stages:
        bind_floor_plans(canvas, document, on_change)


class _MultiTypeGeomView(DocumentMapView):
    """Geometrie-View, die mehrere Elementtypen gleichzeitig bedient."""

    __slots__ = ("_element_classes",)

    def __init__(self, document, geom_key, element_classes, converters=RAW):
        super().__init__(document, geom_key, element_classes[0], converters)
        object.__setattr__(self, "_element_classes", element_classes)

    @property
    def _bucket(self):  # type: ignore[override]
        merged: dict[str, Element] = {}
        for element_cls in self._element_classes:
            merged.update(self._document.container(element_cls))
        return merged
