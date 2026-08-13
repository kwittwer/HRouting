from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Iterable

from model.document import Document
from model.elements import Circuit, ElecCable, ElecPoint, ElecRoom, Element, FloorPlan, Furniture, Hkv, HkvLine, TextAnnotation


SUPPORTED_ROOT_TYPES: tuple[type[Element], ...] = (
    FloorPlan,
    Furniture,
    Circuit,
    Hkv,
    HkvLine,
    ElecPoint,
    ElecRoom,
    ElecCable,
    TextAnnotation,
)

_IMPORT_ORDER: tuple[type[Element], ...] = (
    FloorPlan,
    Furniture,
    Hkv,
    Circuit,
    ElecPoint,
    ElecRoom,
    ElecCable,
    HkvLine,
    TextAnnotation,
)


@dataclass(frozen=True)
class HrpImportCandidate:
    key: str
    element_id: str
    element_type: type[Element]
    category_label: str
    name: str
    floor_plan_id: str


@dataclass(frozen=True)
class HrpImportSelection:
    selected_keys: tuple[str, ...]
    auto_included_keys: tuple[str, ...]
    ordered_keys: tuple[str, ...]


@dataclass(frozen=True)
class HrpImportResult:
    selected_keys: tuple[str, ...]
    auto_included_keys: tuple[str, ...]
    imported_keys: tuple[str, ...]
    id_map: dict[str, str]


def selection_key(element_type: type[Element], element_id: str) -> str:
    return f"{element_type.PARAMS_KEY}:{element_id}"


def iter_import_candidates(document: Document) -> list[HrpImportCandidate]:
    candidates: list[HrpImportCandidate] = []
    for element_type in SUPPORTED_ROOT_TYPES:
        for element in _elements_of_type(document, element_type):
            candidates.append(
                HrpImportCandidate(
                    key=selection_key(element_type, element.id),
                    element_id=element.id,
                    element_type=element_type,
                    category_label=element_type.CATEGORY_LABEL,
                    name=str(getattr(element, "name", "") or element.id),
                    floor_plan_id=_element_floorplan_ref(element),
                )
            )
    return candidates


def resolve_import_selection(document: Document, selected_keys: Iterable[str]) -> HrpImportSelection:
    index = _candidate_index(document)
    direct = [key for key in selected_keys if key in index]
    closure: set[str] = set()
    for key in direct:
        _collect_with_dependencies(document, index, key, closure)
    ordered = tuple(sorted(closure, key=lambda key: _sort_key(index[key][0], key)))
    direct_set = set(direct)
    auto_included = tuple(key for key in ordered if key not in direct_set)
    return HrpImportSelection(
        selected_keys=tuple(direct),
        auto_included_keys=auto_included,
        ordered_keys=ordered,
    )


def import_selected_elements(
    source: Document,
    target: Document,
    selected_keys: Iterable[str],
) -> HrpImportResult:
    index = _candidate_index(source)
    selection = resolve_import_selection(source, selected_keys)
    id_map: dict[str, str] = {}

    for key in selection.ordered_keys:
        element_type, _source_element = index[key]
        id_map[key] = target.new_id(element_type)

    for key in selection.ordered_keys:
        element_type, source_element = index[key]
        imported = _clone_element(source_element, id_map)
        imported.id = id_map[key]
        if imported.ID_FIELD:
            imported.data[imported.ID_FIELD] = imported.id
        if isinstance(imported, FloorPlan):
            imported.layer["fp_id"] = imported.id
        target.add(imported)
        if isinstance(imported, FloorPlan) and not isinstance(imported, Furniture):
            target.floorplan_order.append(imported.id)

    return HrpImportResult(
        selected_keys=selection.selected_keys,
        auto_included_keys=selection.auto_included_keys,
        imported_keys=selection.ordered_keys,
        id_map=id_map,
    )


def _candidate_index(document: Document) -> dict[str, tuple[type[Element], Element]]:
    index: dict[str, tuple[type[Element], Element]] = {}
    for element_type in SUPPORTED_ROOT_TYPES:
        for element in _elements_of_type(document, element_type):
            index[selection_key(element_type, element.id)] = (element_type, element)
    return index


def _elements_of_type(document: Document, element_type: type[Element]) -> list[Element]:
    if element_type is FloorPlan:
        return list(document.floorplans.values())
    if element_type is Furniture:
        return list(document.furniture.values())
    return list(document.container(element_type).values())


def _sort_key(element_type: type[Element], key: str) -> tuple[int, str]:
    try:
        return _IMPORT_ORDER.index(element_type), key
    except ValueError:
        return len(_IMPORT_ORDER), key


def _collect_with_dependencies(
    document: Document,
    index: dict[str, tuple[type[Element], Element]],
    key: str,
    closure: set[str],
) -> None:
    if key in closure:
        return
    closure.add(key)
    element_type, element = index[key]
    for dependency_key in _dependency_keys(document, index, element_type, element):
        _collect_with_dependencies(document, index, dependency_key, closure)


def _dependency_keys(
    document: Document,
    index: dict[str, tuple[type[Element], Element]],
    element_type: type[Element],
    element: Element,
) -> list[str]:
    dependencies: list[str] = []
    floorplan_ref = _element_floorplan_ref(element)
    if floorplan_ref:
        _append_existing_key(index, dependencies, FloorPlan, floorplan_ref)

    if element_type is Furniture:
        parent_ref = str(element.data.get("parent_fp_id", "") or "")
        if parent_ref:
            _append_existing_key(index, dependencies, FloorPlan, parent_ref)

    if element_type is Circuit:
        hkv_ref = str(element.hkv_id or "")
        if hkv_ref:
            _append_existing_key(index, dependencies, Hkv, hkv_ref)

    if element_type is ElecCable:
        start_ap = str(element.start_ap or "")
        end_ap = str(element.end_ap or "")
        if start_ap:
            _append_existing_key(index, dependencies, ElecPoint, start_ap)
        if end_ap:
            _append_existing_key(index, dependencies, ElecPoint, end_ap)

    if element_type is HkvLine:
        start_hkv = str(element.start_hkv or "")
        end_hkv = str(element.end_hkv or "")
        if start_hkv:
            _append_existing_key(index, dependencies, Hkv, start_hkv)
        if end_hkv:
            _append_existing_key(index, dependencies, Hkv, end_hkv)

    return dependencies


def _append_existing_key(
    index: dict[str, tuple[type[Element], Element]],
    dependencies: list[str],
    element_type: type[Element],
    element_id: str,
) -> None:
    candidate_key = selection_key(element_type, element_id)
    if candidate_key in index:
        dependencies.append(candidate_key)


def _element_floorplan_ref(element: Element) -> str:
    return str(getattr(element, "floor_plan_id", "") or "")


def _clone_element(element: Element, id_map: dict[str, str]) -> Element:
    new_id = id_map[selection_key(type(element), element.id)]
    data = copy.deepcopy(element.to_params())
    geom = copy.deepcopy(element.to_geom())

    if element.ID_FIELD:
        data[element.ID_FIELD] = new_id
    data = _rewrite_data_refs(element, data, id_map)
    geom = _rewrite_geom_refs(element, geom, id_map)

    if isinstance(element, FloorPlan):
        layer = copy.deepcopy(element.layer)
        layer["fp_id"] = new_id
        return type(element)(new_id, data, geom, layer)
    return type(element)(new_id, data, geom)


def _rewrite_data_refs(element: Element, data: dict, id_map: dict[str, str]) -> dict:
    if isinstance(element, Furniture):
        parent = str(data.get("parent_fp_id", "") or "")
        if parent:
            data["parent_fp_id"] = id_map.get(selection_key(FloorPlan, parent), parent)

    floorplan_ref = str(data.get("floor_plan_id", "") or "")
    if floorplan_ref:
        data["floor_plan_id"] = id_map.get(selection_key(FloorPlan, floorplan_ref), floorplan_ref)

    if isinstance(element, ElecCable):
        for field_name in ("start_ap", "end_ap"):
            ref = str(data.get(field_name, "") or "")
            if ref:
                data[field_name] = id_map.get(selection_key(ElecPoint, ref), ref)

    if isinstance(element, HkvLine):
        for field_name in ("start_hkv", "end_hkv"):
            ref = str(data.get(field_name, "") or "")
            if ref:
                data[field_name] = id_map.get(selection_key(Hkv, ref), ref)

    return data


def _rewrite_geom_refs(element: Element, geom: dict, id_map: dict[str, str]) -> dict:
    if isinstance(element, Circuit):
        hkv_ref = str(geom.get("supply_hkv", "") or "")
        if hkv_ref:
            geom["supply_hkv"] = id_map.get(selection_key(Hkv, hkv_ref), hkv_ref)

    if isinstance(element, ElecCable):
        for field_name in ("cable_start_ap", "cable_end_ap"):
            ref = str(geom.get(field_name, "") or "")
            if ref:
                geom[field_name] = id_map.get(selection_key(ElecPoint, ref), ref)

    if isinstance(element, HkvLine):
        for field_name in ("hkv_line_start", "hkv_line_end"):
            ref = str(geom.get(field_name, "") or "")
            if ref:
                geom[field_name] = id_map.get(selection_key(Hkv, ref), ref)

    return geom