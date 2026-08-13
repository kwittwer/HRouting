"""Zentrales Projektdokument von HRouting.

``Document`` hält den kompletten Projektinhalt Qt-frei und verlustfrei:
Unbekannte Felder aus einer .hrp-Datei überleben Laden + Speichern.
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Iterable, Iterator

from .elements import (
    COMMON_GEOM_KEYS,
    ELEMENT_TYPES,
    Element,
    FloorPlan,
    Furniture,
)
from .ids import IdAllocator
from .layers import LayerId


class Emitter:
    """Minimaler, Qt-freier Signal-Ersatz."""

    def __init__(self) -> None:
        self._slots: list[Callable[..., Any]] = []

    def connect(self, slot: Callable[..., Any]) -> None:
        if slot not in self._slots:
            self._slots.append(slot)

    def disconnect(self, slot: Callable[..., Any]) -> None:
        if slot in self._slots:
            self._slots.remove(slot)

    def emit(self, *args: Any) -> None:
        for slot in list(self._slots):
            slot(*args)


#: params-Schlüssel, die keine Elementcontainer sind
_PARAMS_NON_ELEMENT_KEYS = {"floorplans_order"}

#: canvas-Schlüssel, die separat behandelt werden
_CANVAS_STRUCTURAL_KEYS = {"floor_plans", "floor_plan_order"}
_TOP_LEVEL_KNOWN_KEYS = {"svg_path", "canvas", "params", "pdf_export_pages", "format_version"}


def _all_geom_keys() -> set[str]:
    keys: set[str] = set(COMMON_GEOM_KEYS)
    for cls in ELEMENT_TYPES:
        keys.update(cls.GEOM_KEYS)
    keys.update(FloorPlan.GEOM_KEYS)
    return keys


class Document:
    """Projektdokument mit typisiertem Zugriff auf alle Elemente."""

    def __init__(self) -> None:
        self.svg_path: str = ""
        self.pdf_export_pages: list = []
        self.format_version: int | None = None
        self.top_level_extras: dict[str, Any] = {}

        #: globale Heizungs-/Projektparameter und sonstige params-Skalare
        self.settings: dict[str, Any] = {
            "t_supply": 35.0,
            "t_return": 30.0,
            "t_norm_outdoor": -12.0,
        }
        #: globale canvas-Einstellungen (Zoom, Raster, Hilfslinien, …)
        self.view: dict[str, Any] = {}

        self.floorplans: dict[str, FloorPlan] = {}
        self.furniture: dict[str, Furniture] = {}
        self.floorplan_order: list[str] = []

        #: PARAMS_KEY -> {element_id: Element}
        self.elements: dict[str, dict[str, Element]] = {
            cls.PARAMS_KEY: {} for cls in ELEMENT_TYPES
        }

        #: verwaiste canvas-Einträge ohne zugehöriges Element
        self.canvas_orphans: dict[str, dict[str, Any]] = {}
        #: beim Laden vorhandene id-basierte canvas-Maps (auch leere)
        self.canvas_geom_keys_seen: list[str] = []

        self.ids = IdAllocator()
        self._active_floorplan_id: str = ""

        # Signale
        self.element_added = Emitter()      # (element_id)
        self.element_removed = Emitter()    # (element_id)
        self.element_changed = Emitter()    # (element_id)
        self.structure_changed = Emitter()  # ()
        self.active_floorplan_changed = Emitter()  # (fp_id)

    # ------------------------------------------------------------------
    # Zugriff
    # ------------------------------------------------------------------
    @property
    def active_floorplan_id(self) -> str:
        if self._active_floorplan_id in self.floorplans:
            return self._active_floorplan_id
        return self.floorplan_order[0] if self.floorplan_order else ""

    @active_floorplan_id.setter
    def active_floorplan_id(self, fp_id: str) -> None:
        if fp_id == self._active_floorplan_id:
            return
        self._active_floorplan_id = fp_id
        self.active_floorplan_changed.emit(fp_id)

    def container(self, cls: type[Element]) -> dict[str, Element]:
        if cls in (FloorPlan,):
            return self.floorplans  # type: ignore[return-value]
        if cls is Furniture:
            return self.furniture  # type: ignore[return-value]
        return self.elements[cls.PARAMS_KEY]

    def all_elements(self) -> Iterator[Element]:
        yield from self.floorplans.values()
        yield from self.furniture.values()
        for bucket in self.elements.values():
            yield from bucket.values()

    def get(self, element_id: str) -> Element | None:
        for element in self.all_elements():
            if element.id == element_id:
                return element
        return None

    def elements_of(
        self,
        cls: type[Element],
        floor_plan_id: str | None = None,
    ) -> list[Element]:
        items = list(self.container(cls).values())
        if floor_plan_id:
            items = [e for e in items if e.floor_plan_id == floor_plan_id]
        return items

    def layers_in_use(self, floor_plan_id: str | None = None) -> set[LayerId]:
        """Layer, zu denen tatsächlich Elemente existieren.

        Basis für den Navigator, der leere Kategorien ausblendet.
        """
        used: set[LayerId] = set()
        for cls in ELEMENT_TYPES:
            if self.elements_of(cls, floor_plan_id):
                used.add(cls.LAYER)
        if self.furniture:
            used.add(LayerId.FURNITURE)
        if self.floorplans:
            used.add(LayerId.FLOORPLAN)
        return used

    def add(self, element: Element) -> Element:
        self.container(type(element))[element.id] = element
        self.ids.observe(element.id)
        self.element_added.emit(element.id)
        return element

    def remove(self, element_id: str) -> Element | None:
        for bucket in (self.floorplans, self.furniture, *self.elements.values()):
            if element_id in bucket:
                element = bucket.pop(element_id)
                if element_id in self.floorplan_order:
                    self.floorplan_order.remove(element_id)
                self.element_removed.emit(element_id)
                return element
        return None

    def new_id(self, cls: type[Element]) -> str:
        return self.ids.next_id(cls.PREFIX)

    def is_visible(self, element_id: str) -> bool:
        """Liest die Sichtbarkeit eines Elements oder Grundrisses."""
        if element_id in self.floorplans:
            floor = self.floorplans[element_id]
            layer_visible = floor.layer.get("visible")
            if layer_visible is not None:
                return bool(layer_visible)
            return bool(floor.data.get("visible", True))

        element = self.get(element_id)
        if element is None:
            return True
        if element.visible is not None:
            return bool(element.visible)
        return True

    def set_visible(self, element_id: str, visible: bool) -> bool:
        """Setzt die Sichtbarkeit eines Elements und synchronisiert Geometriemaps."""
        visible = bool(visible)
        if element_id in self.floorplans:
            floor = self.floorplans[element_id]
            floor.data["visible"] = visible
            floor.layer["visible"] = visible
            self.element_changed.emit(element_id)
            return True

        element = self.get(element_id)
        if element is None:
            return False

        element.visible = visible
        geom = element.geom
        if "elec_visible" in geom:
            geom["elec_visible"] = visible
        if "hkv_visible" in geom:
            geom["hkv_visible"] = visible
        if "hkv_line_visible" in geom:
            geom["hkv_line_visible"] = visible
        if "elec_room_visible" in geom:
            geom["elec_room_visible"] = visible
        if "text_annotations" in geom and isinstance(geom["text_annotations"], dict):
            geom["text_annotations"]["visible"] = visible

        self.element_changed.emit(element_id)
        return True

    # ------------------------------------------------------------------
    # Serialisierung
    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, raw: dict) -> "Document":
        doc = cls()
        raw = copy.deepcopy(raw or {})
        canvas = raw.get("canvas") or {}
        params = raw.get("params") or {}

        doc.svg_path = raw.get("svg_path", "")
        doc.pdf_export_pages = raw.get("pdf_export_pages", []) or []
        try:
            doc.format_version = int(raw["format_version"]) if "format_version" in raw else None
        except (TypeError, ValueError):
            doc.format_version = None
        doc.top_level_extras = {
            k: v for k, v in raw.items() if k not in _TOP_LEVEL_KNOWN_KEYS
        }

        geom_keys = _all_geom_keys()

        # --- Grundrisse & Einrichtung -----------------------------------
        layer_entries = {
            entry.get("fp_id", ""): entry
            for entry in (canvas.get("floor_plans") or [])
            if isinstance(entry, dict)
        }
        fp_geom = {
            key: canvas.get(key) or {}
            for key in FloorPlan.GEOM_KEYS
        }

        def _build_layerlike(klass, params_key: str) -> None:
            for fp_id, data in (params.get(params_key) or {}).items():
                geom = {
                    key: values[fp_id]
                    for key, values in fp_geom.items()
                    if isinstance(values, dict) and fp_id in values
                }
                element = klass(fp_id, data, geom, layer_entries.pop(fp_id, {}))
                doc.container(klass)[fp_id] = element
                doc.ids.observe(fp_id)

        _build_layerlike(FloorPlan, FloorPlan.PARAMS_KEY)
        _build_layerlike(Furniture, Furniture.PARAMS_KEY)

        order = list(params.get("floorplans_order") or canvas.get("floor_plan_order") or [])
        doc.floorplan_order = [f for f in order if f in doc.floorplans]
        doc.floorplan_order += [f for f in doc.floorplans if f not in doc.floorplan_order]

        # verbleibende Layer-Einträge ohne params-Definition bewahren
        if layer_entries:
            doc.canvas_orphans["floor_plans"] = layer_entries

        # --- übrige Elemente --------------------------------------------
        consumed: dict[str, set[str]] = {}
        for element_cls in ELEMENT_TYPES:
            bucket = doc.elements[element_cls.PARAMS_KEY]
            for element_id, data in (params.get(element_cls.PARAMS_KEY) or {}).items():
                geom: dict[str, Any] = {}
                for key in element_cls.all_geom_keys():
                    values = canvas.get(key)
                    if isinstance(values, dict) and element_id in values:
                        geom[key] = values[element_id]
                        consumed.setdefault(key, set()).add(element_id)
                bucket[element_id] = element_cls(element_id, data, geom)
                doc.ids.observe(element_id)

        # --- verwaiste canvas-Einträge ----------------------------------
        for key in geom_keys:
            values = canvas.get(key)
            if not isinstance(values, dict):
                continue
            doc.canvas_geom_keys_seen.append(key)
            taken = consumed.get(key, set())
            if key in FloorPlan.GEOM_KEYS:
                taken = taken | set(doc.floorplans) | set(doc.furniture)
            rest = {k: v for k, v in values.items() if k not in taken}
            if rest:
                doc.canvas_orphans[key] = rest

        # --- globale Einstellungen --------------------------------------
        element_param_keys = {c.PARAMS_KEY for c in ELEMENT_TYPES} | {
            FloorPlan.PARAMS_KEY,
            Furniture.PARAMS_KEY,
        }
        doc.settings = {
            k: v
            for k, v in params.items()
            if k not in element_param_keys and k not in _PARAMS_NON_ELEMENT_KEYS
        }
        doc.view = {
            k: v
            for k, v in canvas.items()
            if k not in geom_keys and k not in _CANVAS_STRUCTURAL_KEYS
        }

        if doc.floorplan_order:
            doc._active_floorplan_id = doc.floorplan_order[0]
        return doc

    def to_dict(self) -> dict:
        canvas: dict[str, Any] = dict(copy.deepcopy(self.view))
        params: dict[str, Any] = dict(copy.deepcopy(self.settings))
        for key in self.canvas_geom_keys_seen:
            canvas.setdefault(key, {})

        def _merge_geom(element: Element) -> None:
            for key, value in element.to_geom().items():
                canvas.setdefault(key, {})[element.id] = value

        # Grundrisse & Einrichtung
        params[FloorPlan.PARAMS_KEY] = {
            fp_id: fp.to_params() for fp_id, fp in self.floorplans.items()
        }
        params[Furniture.PARAMS_KEY] = {
            fid: f.to_params() for fid, f in self.furniture.items()
        }
        params["floorplans_order"] = list(self.floorplan_order)

        layer_list: list[dict] = []
        for fp_id in self.floorplan_order:
            if fp_id in self.floorplans:
                layer_list.append(self.floorplans[fp_id].to_layer())
        for fp_id, fp in self.floorplans.items():
            if fp_id not in self.floorplan_order:
                layer_list.append(fp.to_layer())
        for furn in self.furniture.values():
            layer_list.append(furn.to_layer())
        layer_list += list(self.canvas_orphans.get("floor_plans", {}).values())
        canvas["floor_plans"] = layer_list

        for fp in (*self.floorplans.values(), *self.furniture.values()):
            _merge_geom(fp)

        # übrige Elemente
        for element_cls in ELEMENT_TYPES:
            bucket = self.elements[element_cls.PARAMS_KEY]
            params[element_cls.PARAMS_KEY] = {
                eid: element.to_params() for eid, element in bucket.items()
            }
            for element in bucket.values():
                _merge_geom(element)

        # verwaiste Einträge zurückschreiben
        for key, values in self.canvas_orphans.items():
            if key == "floor_plans":
                continue
            canvas.setdefault(key, {}).update(values)

        result = {
            "svg_path": self.svg_path,
            "canvas": canvas,
            "params": params,
            "pdf_export_pages": copy.deepcopy(self.pdf_export_pages),
        }
        if self.format_version is not None:
            result["format_version"] = int(self.format_version)
        result.update(copy.deepcopy(self.top_level_extras))
        return result

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        """Tiefe Kopie für Undo/Redo."""
        return self.to_dict()

    def restore(self, snapshot: dict) -> None:
        other = Document.from_dict(snapshot)
        self.__dict__.update(
            {
                k: v
                for k, v in other.__dict__.items()
                if not isinstance(v, Emitter)
            }
        )
        self.structure_changed.emit()
