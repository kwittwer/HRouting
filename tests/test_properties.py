"""Tests für Phase B: schema-getriebene Eigenschaften-Editoren."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from model.computed import polygon_area_px2, polyline_length_px  # noqa: E402
from model.document import Document  # noqa: E402
from model.elements import Circuit, ElecPoint, TextAnnotation  # noqa: E402
from model.field_access import (  # noqa: E402
    apply_display_value,
    display_value,
    get_field,
    set_field,
)
from model.schema import (  # noqa: E402
    SCHEMAS,
    FieldKind,
    groups_of,
    schema_for,
)
from storage.hrp_io import load_raw  # noqa: E402

EXAMPLE = ROOT / "examples" / "minimal.hrp"


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture()
def document() -> Document:
    return Document.from_dict(load_raw(EXAMPLE))


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_every_schema_has_fields():
    for schema in SCHEMAS:
        assert schema.fields, f"{schema.title} hat keine Felder"


def test_schema_lookup_by_element(document):
    circuit = document.elements["circuits"]["HK-1"]
    schema = schema_for(circuit)
    assert schema is not None
    assert schema.element_cls is Circuit


def test_choice_options_resolve():
    schema = schema_for(Circuit)
    covering = next(f for f in schema.fields if f.key == "floor_covering")
    options = covering.resolve_options()
    assert options
    assert "Fliesen / Keramik" in options


def test_groups_preserve_order():
    schema = schema_for(Circuit)
    groups = [name for name, _ in groups_of(schema)]
    assert groups[0] == "Allgemein"
    assert "Verlegung" in groups


def test_scale_converts_mm_to_cm():
    schema = schema_for(Circuit)
    spacing = next(f for f in schema.fields if f.key == "spacing")
    assert spacing.to_display(150.0) == 15.0
    assert spacing.to_storage(15.0) == 150.0


# ---------------------------------------------------------------------------
# Feldzugriff
# ---------------------------------------------------------------------------


def test_field_access_reads_params(document):
    circuit = document.elements["circuits"]["HK-1"]
    spec = next(f for f in schema_for(circuit).fields if f.key == "name")
    assert get_field(circuit, spec) == "Wohnzimmer"


def test_field_access_writes_params(document):
    circuit = document.elements["circuits"]["HK-1"]
    spec = next(f for f in schema_for(circuit).fields if f.key == "name")
    set_field(circuit, spec, "Neuer Name")
    assert document.to_dict()["params"]["circuits"]["HK-1"]["name"] == "Neuer Name"


def test_display_value_applies_scale(document):
    circuit = document.elements["circuits"]["HK-1"]
    spec = next(f for f in schema_for(circuit).fields if f.key == "spacing")
    assert display_value(circuit, spec) == 15.0

    apply_display_value(circuit, spec, 20.0)
    assert document.to_dict()["params"]["circuits"]["HK-1"]["spacing"] == 200.0


def test_text_fields_write_into_nested_entry(document):
    text = document.elements["text_annotations"]["TEXT-1"]
    spec = next(f for f in schema_for(text).fields if f.key == "content")
    set_field(text, spec, "Geändert")
    entry = document.to_dict()["canvas"]["text_annotations"]["TEXT-1"]
    assert entry["content"] == "Geändert"
    assert entry["pos"] == [300, 50]  # Position unangetastet


def test_floorplan_fields_write_into_layer(document):
    fp_id = document.floorplan_order[0]
    floor = document.floorplans[fp_id]
    spec = next(f for f in schema_for(floor).fields if f.key == "rotation")
    set_field(floor, spec, 45.0)

    saved = document.to_dict()
    entry = next(e for e in saved["canvas"]["floor_plans"] if e["fp_id"] == fp_id)
    assert entry["rotation"] == 45.0
    assert saved["params"]["floorplans"][fp_id]["rotation"] == 45.0


# ---------------------------------------------------------------------------
# Berechnungen
# ---------------------------------------------------------------------------


def test_polygon_area_of_square():
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    assert polygon_area_px2(square) == 100.0


def test_polyline_length():
    line = [(0.0, 0.0), (3.0, 4.0)]
    assert polyline_length_px(line) == 5.0
    assert polyline_length_px(line, closed=True) == 5.0  # nur 2 Punkte


def test_circuit_computed_values(document):
    from model.computed import computed_values  # noqa: PLC0415

    circuit = document.elements["circuits"]["HK-1"]
    values = computed_values(document, circuit)
    # 400x300 px bei 25 mm/px -> 10m x 7.5m = 75 m²
    assert values["area_m2"].startswith("75.00")
    assert values["power_w"].endswith("W")
    assert values["volume_flow_lmin"].endswith("l/min")


def test_cable_computed_shows_ap_names(document):
    from model.computed import computed_values  # noqa: PLC0415

    cable = document.elements["elec_cables"]["EK-1"]
    values = computed_values(document, cable)
    assert values["start_ap_name"] == "Steckdose Wohnzimmer"
    assert values["end_ap_name"] == "Steckdose 2 Wohnzimmer"


# ---------------------------------------------------------------------------
# Widgets und Editor
# ---------------------------------------------------------------------------


def test_widget_created_for_every_field_kind(app):
    from gui.properties.field_widgets import create_field_widget  # noqa: PLC0415
    from model.schema import FieldSpec  # noqa: PLC0415

    for kind in FieldKind:
        spec = FieldSpec("k", "L", kind, options=("a", "b"))
        widget = create_field_widget(spec)
        assert widget is not None
        widget.deleteLater()


def test_editor_builds_all_fields(app, document):
    from gui.properties import GenericElementEditor  # noqa: PLC0415

    circuit = document.elements["circuits"]["HK-1"]
    schema = schema_for(circuit)
    editor = GenericElementEditor(document, circuit, schema)
    try:
        assert set(editor._widgets) == {f.key for f in schema.fields}
        assert editor._widgets["name"].value() == "Wohnzimmer"
        assert editor._widgets["spacing"].value() == 15.0  # cm
    finally:
        editor.deleteLater()


def test_editor_writes_change_to_document(app, document):
    from gui.properties import GenericElementEditor  # noqa: PLC0415

    circuit = document.elements["circuits"]["HK-1"]
    editor = GenericElementEditor(document, circuit, schema_for(circuit))
    try:
        editor._on_field_changed("room_temp", 24.0)
        assert document.to_dict()["params"]["circuits"]["HK-1"]["room_temp"] == 24.0
    finally:
        editor.deleteLater()


def test_editor_silent_update_does_not_emit(app, document):
    from gui.properties import GenericElementEditor  # noqa: PLC0415

    circuit = document.elements["circuits"]["HK-1"]
    editor = GenericElementEditor(document, circuit, schema_for(circuit))
    try:
        seen: list[tuple] = []
        editor.field_changed.connect(lambda *args: seen.append(args))
        editor.refresh()
        assert seen == []
    finally:
        editor.deleteLater()


def test_editors_exist_for_all_element_types(app, document):
    """Jeder Elementtyp im Beispielprojekt muss einen Editor bekommen."""
    from gui.properties import GenericElementEditor  # noqa: PLC0415

    checked = 0
    for element in document.all_elements():
        schema = schema_for(element)
        if schema is None:
            continue
        editor = GenericElementEditor(document, element, schema)
        try:
            assert editor._widgets
            checked += 1
        finally:
            editor.deleteLater()
    assert checked >= 6


def test_global_settings_editor(app, document):
    from gui.properties import GlobalSettingsEditor  # noqa: PLC0415

    editor = GlobalSettingsEditor(document)
    try:
        assert editor._widgets["t_supply"].value() == 35.0
        editor._on_changed("t_supply", 40.0)
        assert document.settings["t_supply"] == 40.0
    finally:
        editor.deleteLater()


def test_properties_dock_shows_element(app, document):
    from gui.docks.properties_dock import PropertiesDock  # noqa: PLC0415

    dock = PropertiesDock()
    try:
        dock.set_document(document)
        dock.show_element("HK-1")
        assert "HK-1" in dock._editors
        assert dock._current_id == "HK-1"

        dock.forget_element("HK-1")
        assert "HK-1" not in dock._editors
    finally:
        dock.deleteLater()


def test_properties_dock_handles_unknown_element(app, document):
    from gui.docks.properties_dock import PropertiesDock  # noqa: PLC0415

    dock = PropertiesDock()
    try:
        dock.set_document(document)
        dock.show_element("gibt-es-nicht")
        assert dock._current_id == ""
    finally:
        dock.deleteLater()


# ---------------------------------------------------------------------------
# AP-Typ-Konfiguration (B9)
# ---------------------------------------------------------------------------


def test_action_visibility_depends_on_ap_type():
    from model.schema import ELEC_POINT_SCHEMA  # noqa: PLC0415

    uv_action = next(a for a in ELEC_POINT_SCHEMA.actions if a.id == "configure_uv")
    assert uv_action.is_visible_for({"ap_type": "uv"})
    assert not uv_action.is_visible_for({"ap_type": "standard"})

    place_action = next(a for a in ELEC_POINT_SCHEMA.actions if a.id == "place")
    assert place_action.is_visible_for({})  # ohne Bedingung immer sichtbar


def test_editor_toggles_config_buttons(app, document):
    from gui.properties import GenericElementEditor  # noqa: PLC0415

    point = document.elements["elec_points"]["AP-1"]
    editor = GenericElementEditor(document, point, schema_for(point))
    try:
        def visible(action_id: str) -> bool:
            button = editor._action_buttons[action_id]
            return button.isVisibleTo(button.parentWidget())

        editor._on_field_changed("ap_type", "uv")
        assert visible("configure_uv")
        assert not visible("configure_hak")

        editor._on_field_changed("ap_type", "zaehler")
        assert visible("configure_zaehler")
        assert not visible("configure_uv")
    finally:
        editor.deleteLater()


def test_hak_dialog_roundtrip(app):
    from gui.properties.config_dialogs import HakConfigDialog  # noqa: PLC0415

    dialog = HakConfigDialog({"incoming_voltage": "230V", "main_fuse_a": "35"})
    try:
        config = dialog.get_config()
        assert config == {"incoming_voltage": "230V", "main_fuse_a": "35"}
    finally:
        dialog.deleteLater()


def test_hak_dialog_uses_defaults(app):
    from gui.properties.config_dialogs import HakConfigDialog  # noqa: PLC0415

    dialog = HakConfigDialog(None)
    try:
        assert dialog.get_config() == {"incoming_voltage": "400V", "main_fuse_a": "63"}
    finally:
        dialog.deleteLater()


def test_zaehler_dialog_roundtrip(app):
    from gui.properties.config_dialogs import ZaehlerConfigDialog  # noqa: PLC0415

    dialog = ZaehlerConfigDialog({"meter_id": "12345", "phases": "1-phasig"})
    try:
        assert dialog.get_config() == {"meter_id": "12345", "phases": "1-phasig"}
    finally:
        dialog.deleteLater()


def test_uv_and_up_dialogs_are_constructible(app, document):
    """Die großen Dialoge werden aus parameter_panel.py wiederverwendet."""
    from gui.parameter_panel import UpDistributionDialog, UvConfigDialog  # noqa: PLC0415

    uv = UvConfigDialog(config={}, cable_choices=["Kabel A", "Kabel B"])
    try:
        config = uv.get_config()
        assert "slots" in config and "busbars" in config
    finally:
        uv.deleteLater()

    up = UpDistributionDialog(config={}, cable_choices=[("EK-1", "Kabel A")])
    try:
        config = up.get_config()
        assert "mappings" in config
    finally:
        up.deleteLater()


def test_configs_persist_in_document(app, document, tmp_path):
    """AP-Konfigurationen müssen Speichern und Laden überstehen."""
    from storage.hrp_io import load_document, save_document  # noqa: PLC0415

    point = document.elements["elec_points"]["AP-1"]
    point.data["ap_type"] = "uv"
    point.data["uv_config"] = {
        "rows": 2,
        "modules_per_row": 12,
        "slots": [{"row": 1, "slot": 1, "device_type": "FI", "te_size": 4}],
        "busbars": [{"phase": "L1", "color": "#e53935", "te_start": 1, "te_end": 4}],
    }
    point.data["hak_config"] = {"incoming_voltage": "400V", "main_fuse_a": "63"}

    target = tmp_path / "configs.hrp"
    save_document(document, target)
    reloaded = load_document(target)

    stored = reloaded.to_dict()["params"]["elec_points"]["AP-1"]
    assert stored["ap_type"] == "uv"
    assert stored["uv_config"]["rows"] == 2
    assert stored["uv_config"]["busbars"][0]["phase"] == "L1"
    assert stored["hak_config"]["main_fuse_a"] == "63"


def test_app_window_stores_config(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        assert window.open_project_file(EXAMPLE)
        window._store_config("AP-1", "zaehler_config", {"meter_id": "X1"})
        stored = window._document.to_dict()["params"]["elec_points"]["AP-1"]
        assert stored["zaehler_config"] == {"meter_id": "X1"}
        assert window._dirty is True
    finally:
        window.deleteLater()


def test_cable_choice_helpers(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw: default
    )
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)

    from gui.app_window import AppWindow  # noqa: PLC0415

    window = AppWindow()
    try:
        assert window.open_project_file(EXAMPLE)
        names = window._cable_names()
        pairs = window._cable_id_name_pairs()
        assert names  # Beispielprojekt hat ein Kabel
        assert pairs[0][0] == "EK-1"
    finally:
        window.deleteLater()


# ---------------------------------------------------------------------------
# Vollständigkeit: Felder, Spiegelung, dynamische Auswahl
# ---------------------------------------------------------------------------


def test_cable_schema_has_all_panel_fields():
    """Die Kabel-Felder der alten Oberfläche müssen vollständig sein."""
    schema = schema_for(__import__("model.elements", fromlist=["ElecCable"]).ElecCable)
    keys = {f.key for f in schema.fields}
    expected = {
        "name",
        "color",
        "visible",
        "label_visible",
        "label_size",
        "type",
        "type_label_visible",
        "stroke_width",
        "comment",
    }
    assert expected <= keys


def test_mirrored_fields_write_to_canvas_map(document):
    """Felder, die der Canvas aus canvas-Maps liest, müssen dort landen."""
    cable = document.elements["elec_cables"]["EK-1"]
    schema = schema_for(cable)

    stroke = next(f for f in schema.fields if f.key == "stroke_width")
    set_field(cable, stroke, 4.5)
    label = next(f for f in schema.fields if f.key == "type_label_visible")
    set_field(cable, label, True)

    saved = document.to_dict()
    assert saved["params"]["elec_cables"]["EK-1"]["stroke_width"] == 4.5
    assert saved["canvas"]["elec_cable_stroke_width"]["EK-1"] == 4.5
    assert saved["canvas"]["elec_cable_type_label_visible"]["EK-1"] is True


def test_mirrored_fields_are_visible_in_canvas(app, document):
    """Eine Änderung muss ohne Neuladen im Canvas ankommen."""
    from gui.canvas_widget import CanvasWidget  # noqa: PLC0415

    canvas = CanvasWidget()
    try:
        canvas.set_document(document)
        cable = document.elements["elec_cables"]["EK-1"]
        schema = schema_for(cable)
        set_field(cable, next(f for f in schema.fields if f.key == "stroke_width"), 6.0)
        assert canvas._elec_cable_stroke_width["EK-1"] == 6.0
    finally:
        canvas.deleteLater()


def test_mirrored_read_falls_back_to_canvas_map(document):
    """Werte, die nur in der canvas-Map stehen, müssen gelesen werden."""
    cable = document.elements["elec_cables"]["EK-1"]
    cable.data.pop("stroke_width", None)
    cable.geom["elec_cable_stroke_width"] = 3.5

    spec = next(f for f in schema_for(cable).fields if f.key == "stroke_width")
    assert get_field(cable, spec) == 3.5


def test_elec_point_position_accepts_free_text(document):
    """Beliebige Positionsangaben müssen möglich sein (früher 'Freitext')."""
    point = document.elements["elec_points"]["AP-1"]
    schema = schema_for(point)
    spec = next(f for f in schema.fields if f.key == "position")
    assert spec.kind is FieldKind.EDITABLE_CHOICE

    set_field(point, spec, "Trockenbauwand")
    saved = document.to_dict()
    assert saved["params"]["elec_points"]["AP-1"]["position"] == "Trockenbauwand"
    assert saved["canvas"]["elec_point_position"]["AP-1"] == "Trockenbauwand"


def test_distributor_options_come_from_document(document):
    """Die Verteilerauswahl muss die HKV des Projekts anbieten."""
    circuit = document.elements["circuits"]["HK-1"]
    spec = next(f for f in schema_for(circuit).fields if f.key == "distributor")
    assert spec.document_options is not None

    options = spec.resolve_options(document)
    assert options[0] == ""  # leere Auswahl möglich
    assert "Verteiler EG" in options


def test_editor_refreshes_dynamic_options(app, document):
    """Neue Verteiler müssen nach einem Refresh auswählbar sein."""
    from gui.properties import GenericElementEditor  # noqa: PLC0415
    from model.elements import Hkv  # noqa: PLC0415

    circuit = document.elements["circuits"]["HK-1"]
    editor = GenericElementEditor(document, circuit, schema_for(circuit))
    try:
        combo = editor._widgets["distributor"]._combo
        before = combo.count()

        document.add(Hkv.create("HKV-2", name="Verteiler OG"))
        editor.refresh()

        assert combo.count() == before + 1
    finally:
        editor.deleteLater()


def test_action_disabled_without_geometry(app, document):
    """Bearbeiten-Aktionen brauchen vorhandene Geometrie."""
    from gui.properties import GenericElementEditor  # noqa: PLC0415

    circuit = document.elements["circuits"]["HK-1"]
    editor = GenericElementEditor(document, circuit, schema_for(circuit))
    try:
        # HK-1 hat ein Polygon, aber keinen Rohrverlauf
        assert editor._action_buttons["edit_polygon"].isEnabled()
        assert not editor._action_buttons["edit_route"].isEnabled()
        # Zeichnen ist immer möglich
        assert editor._action_buttons["draw_route"].isEnabled()
    finally:
        editor.deleteLater()


def test_action_enabled_after_geometry_added(app, document):
    from gui.properties import GenericElementEditor  # noqa: PLC0415

    circuit = document.elements["circuits"]["HK-1"]
    editor = GenericElementEditor(document, circuit, schema_for(circuit))
    try:
        assert not editor._action_buttons["edit_route"].isEnabled()
        circuit.geom["manual_routes"] = [[0, 0], [10, 10]]
        editor.refresh()
        assert editor._action_buttons["edit_route"].isEnabled()
    finally:
        editor.deleteLater()


def test_all_editable_fields_survive_roundtrip(app, document, tmp_path):
    """Jedes Feld jedes Typs muss Speichern und Laden überstehen."""
    from storage.hrp_io import load_document, save_document  # noqa: PLC0415

    changed: dict[tuple[str, str], object] = {}
    for element in list(document.all_elements()):
        schema = schema_for(element)
        if schema is None:
            continue
        for spec in schema.fields:
            if spec.kind is FieldKind.BOOL:
                value = not bool(get_field(element, spec))
            elif spec.kind is FieldKind.NUMBER:
                value = 7.0 * spec.scale
            elif spec.kind in (FieldKind.COLOR,):
                value = "#123456"
            elif spec.kind is FieldKind.FILE:
                continue  # Pfade werden beim Speichern umgeschrieben
            else:
                options = spec.resolve_options(document)
                value = options[-1] if options else f"wert-{spec.key}"
            set_field(element, spec, value)
            changed[(element.id, spec.key)] = value

    target = tmp_path / "all_fields.hrp"
    save_document(document, target)
    reloaded = load_document(target)

    for (element_id, key), expected in changed.items():
        element = reloaded.get(element_id)
        assert element is not None, element_id
        spec = next(f for f in schema_for(element).fields if f.key == key)
        assert get_field(element, spec) == expected, f"{element_id}.{key}"
