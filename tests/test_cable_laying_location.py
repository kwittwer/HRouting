"""Cable laying metadata: pure helpers, persistence and real Qt interactions."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from model.cable_laying_location import (
    CABLE_LAYING_OPTIONS,
    aggregate_cable_laying_locations,
    cable_laying_location_labels,
    format_cable_laying_location,
    normalize_cable_laying_location,
)
from model.document import Document
from model.elements import ElecCable
from model.field_access import get_field, set_field
from model.schema import ELEC_CABLE_SCHEMA, FieldKind
from storage.hrp_io import load_document, save_document
from validate_hrp import validate_schema, validate_semantic

EMPTY = {"locations": [], "custom_enabled": False, "custom_text": ""}


def location(locations=(), *, enabled=False, text=""):
    return {"locations": list(locations), "custom_enabled": enabled, "custom_text": text}


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def spec():
    return next(field for field in ELEC_CABLE_SCHEMA.fields if field.key == "laying_location")


@pytest.fixture
def document():
    return Document.from_dict({
        "svg_path": "",
        "canvas": {"elec_cables": {"EK-1": [[0, 0], [100, 0]]}},
        "params": {"elec_cables": {"EK-1": {
            "cable_id": "EK-1", "name": "Kabel", "type": "3x1,5",
            "label_visible": False, "future_metadata": {"untouched": [1, 2]},
        }}},
        "pdf_export_pages": [],
    })


@pytest.fixture
def widget(app, spec):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget
    from gui.properties.field_widgets import MultiSelectWithTextFieldWidget, create_field_widget

    host = QWidget()
    layout = QVBoxLayout(host)
    field = create_field_widget(spec, host)
    assert isinstance(field, MultiSelectWithTextFieldWidget)
    layout.addWidget(field)
    button = QPushButton("Fokusziel", host)
    layout.addWidget(button)
    host.show()
    host.activateWindow()
    app.processEvents()
    yield field, button
    host.close()
    host.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def click(box):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    QTest.mouseClick(box, Qt.LeftButton, pos=QPoint(8, box.height() // 2))


def type_text(edit, text):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.mouseClick(edit, Qt.LeftButton)
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(edit, text)


@pytest.mark.parametrize("value", [None, "floor", [], 5, False, {}])
def test_normalization_defaults_are_fresh(value):
    first = normalize_cable_laying_location(value)
    second = normalize_cable_laying_location(value)
    assert first == second == EMPTY
    assert first is not second and first["locations"] is not second["locations"]
    first["locations"].append("floor")
    assert second == EMPTY


def test_normalization_orders_deduplicates_and_keeps_exact_text():
    raw = location(["ceiling", "floor", "wall", "floor", "unknown", None, []],
                   enabled=True, text='  Schächte; "Nord"\nSüd  ')
    before = copy.deepcopy(raw)
    result = normalize_cable_laying_location(raw)
    assert result == location(["floor", "wall", "ceiling"], enabled=True, text=raw["custom_text"])
    result["locations"].clear()
    assert raw == before


@pytest.mark.parametrize("raw", [
    {"locations": "floor", "custom_enabled": "false", "custom_text": 3},
    {"locations": None, "custom_enabled": [], "custom_text": None},
])
def test_normalization_ignores_wrong_types(raw):
    assert normalize_cable_laying_location(raw) == EMPTY


def test_formatter_active_labels_and_empty_custom():
    assert CABLE_LAYING_OPTIONS == (
        ("floor", "Auf dem Boden"), ("wall", "In der Wand"), ("ceiling", "In der Decke"),
    )
    value = location(["ceiling", "floor", "wall"], enabled=True, text="  Kabelkanal  ")
    assert cable_laying_location_labels(value) == ["Auf dem Boden", "In der Wand", "In der Decke", "Kabelkanal"]
    assert format_cable_laying_location(value) == "Auf dem Boden, In der Wand, In der Decke, Kabelkanal"
    assert format_cable_laying_location(location(enabled=True, text=" \n\t")) == "Sonstiges"
    assert format_cable_laying_location(location(text="hidden")) == "–"
    assert format_cable_laying_location(None, empty="") == ""


def test_aggregation_consumes_value_iterator_not_cables():
    values = iter([
        location(["ceiling"], enabled=True, text="Kanal"),
        location(["floor", "wall"], enabled=True, text="Kanal"),
        location(enabled=True), location(text="ignored"),
        location(enabled=True, text="In der Wand"),
    ])
    assert aggregate_cable_laying_locations(values) == "Auf dem Boden, In der Wand, In der Decke, Kanal, Sonstiges"
    assert list(values) == []
    assert aggregate_cable_laying_locations(iter(()), empty="") == ""


def test_model_bypasses_shared_param_mutable_defaults():
    first, second = ElecCable.create("EK-1"), ElecCable.create("EK-2")
    assert first.label_visible is second.label_visible is False
    first.laying_location["locations"].append("floor")
    assert first.laying_location == second.laying_location == EMPTY
    raw = location(["wall", "floor", "floor"], text="retained")
    first.laying_location = raw
    raw["locations"].clear()
    raw["custom_text"] = "changed"
    assert first.laying_location == location(["floor", "wall"], text="retained")
    readback = first.laying_location
    readback["locations"].clear()
    assert first.laying_location["locations"] == ["floor", "wall"]
    assert second.laying_location == EMPTY


def test_model_constructor_and_serialization_isolate_nested_location():
    raw = {"cable_id": "EK-1", "laying_location": location(["floor"], text="saved")}
    cable = ElecCable("EK-1", raw)
    raw["laying_location"]["locations"].clear()
    assert cable.laying_location["locations"] == ["floor"]
    duplicate = ElecCable("EK-2", cable.to_params())
    duplicate.data["laying_location"]["locations"].append("wall")
    assert cable.laying_location["locations"] == ["floor"]
    exported = cable.to_params()
    exported["laying_location"]["locations"].clear()
    assert cable.laying_location["locations"] == ["floor"]


def test_field_spec_and_access_normalize_without_canvas_map(spec):
    assert spec.kind is FieldKind.MULTISELECT_WITH_TEXT
    assert spec.label == "Verlegeort" and spec.group == "Kabel"
    assert spec.resolve_options() == CABLE_LAYING_OPTIONS
    cable = ElecCable("EK-1")
    assert get_field(cable, spec) == EMPTY
    assert "laying_location" not in cable.data
    raw = location(["ceiling", "floor", "floor"])
    set_field(cable, spec, raw)
    raw["locations"].clear()
    assert get_field(cable, spec) == location(["floor", "ceiling"])
    assert cable.geom == {}


def test_old_document_is_not_rewritten(document, spec):
    before = copy.deepcopy(document.to_dict())
    cable = document.elements["elec_cables"]["EK-1"]
    assert cable.laying_location == get_field(cable, spec) == EMPTY
    assert document.to_dict() == before
    assert "laying_location" not in cable.data


def test_existing_example_location_defaults_do_not_rewrite_raw_params(spec):
    from storage.hrp_io import load_raw

    document = Document.from_dict(load_raw(ROOT / "examples" / "minimal.hrp"))
    before = copy.deepcopy(document.to_dict())
    for cable in document.elements["elec_cables"].values():
        assert get_field(cable, spec) == EMPTY
    assert document.to_dict() == before


def test_getter_does_not_repair_or_remove_unknown_raw_metadata(spec):
    raw_value = {"locations": ["wall", "wall", "legacy"],
                 "custom_text": "retained", "future_member": {"nested": [1]}}
    cable = ElecCable("EK-1", {"laying_location": raw_value})
    before = copy.deepcopy(cable.data)
    assert get_field(cable, spec) == location(["wall"], text="retained")
    assert cable.data == before
    assert cable.to_params()["laying_location"] == raw_value


def test_real_hrp_roundtrip_validates_and_retains_inactive_text(document, tmp_path):
    pytest.importorskip("jsonschema")
    cable = document.elements["elec_cables"]["EK-1"]
    cable.laying_location = location(["ceiling", "wall"], text=' Schächte; "Süd"\nNord ')
    target = tmp_path / "location.hrp"
    save_document(document, target)
    raw = json.loads(target.read_text(encoding="utf-8"))
    schema = json.loads((ROOT / "hrp_schema.json").read_text(encoding="utf-8"))
    assert validate_schema(raw, schema) == []
    assert validate_semantic(raw)[0] == []
    loaded = load_document(target).elements["elec_cables"]["EK-1"]
    assert loaded.laying_location == cable.laying_location
    assert loaded.data["future_metadata"] == {"untouched": [1, 2]}
    assert format_cable_laying_location(loaded.laying_location) == "In der Wand, In der Decke"
    assert loaded.label_visible is False


@pytest.mark.parametrize("value", [
    None, [], "floor", {"locations": "floor"}, {"locations": ["roof"]},
    {"locations": ["floor", "floor"]}, {"custom_enabled": "true"},
    {"custom_enabled": 1}, {"custom_text": 42},
])
def test_hrp_schema_rejects_wrong_location_types(document, value):
    pytest.importorskip("jsonschema")
    raw = document.to_dict()
    raw["params"]["elec_cables"]["EK-1"]["laying_location"] = value
    schema = json.loads((ROOT / "hrp_schema.json").read_text(encoding="utf-8"))
    assert any("laying_location" in error for error in validate_schema(raw, schema))


@pytest.mark.parametrize("value", [{}, EMPTY, location(["floor", "wall", "ceiling"], enabled=True)])
def test_hrp_schema_accepts_optional_location(document, value):
    pytest.importorskip("jsonschema")
    raw = document.to_dict()
    schema = json.loads((ROOT / "hrp_schema.json").read_text(encoding="utf-8"))
    assert validate_schema(raw, schema) == []
    raw["params"]["elec_cables"]["EK-1"]["laying_location"] = value
    assert validate_schema(raw, schema) == []


def test_hrp_schema_location_contract_and_defaults():
    schema = json.loads((ROOT / "hrp_schema.json").read_text(encoding="utf-8"))
    cable_schema = schema["$defs"]["ParamElecCable"]
    assert "laying_location" not in cable_schema["required"]
    ref = cable_schema["properties"]["laying_location"]["$ref"]
    fields = schema["$defs"][ref.rsplit("/", 1)[-1]]["properties"]
    assert fields["locations"]["default"] == []
    assert fields["locations"]["uniqueItems"] is True
    assert fields["custom_enabled"]["default"] is False
    assert fields["custom_text"]["default"] == ""


def test_widget_checkbox_interactions_commit_full_fresh_objects(widget):
    field, _button = widget
    seen = []
    field.value_changed.connect(lambda *args: seen.append(args))
    assert field.value() == EMPTY and not field._custom_edit.isEnabled()
    for code, label in CABLE_LAYING_OPTIONS:
        assert field._checks[code].text() == label
        click(field._checks[code])
    click(field._custom_check)
    assert field._custom_check.text() == "Sonstiges"
    assert field._custom_edit.isEnabled()
    assert len(seen) == 4
    assert seen[-1] == ("laying_location", location(["floor", "wall", "ceiling"], enabled=True))
    seen[-1][1]["locations"].clear()
    assert field.value()["locations"] == ["floor", "wall", "ceiling"]


def test_widget_programmatic_updates_emit_no_signals(widget):
    field, _button = widget
    from PySide6.QtTest import QSignalSpy

    spies = [QSignalSpy(field.value_changed), QSignalSpy(field._custom_check.toggled),
             QSignalSpy(field._custom_edit.textChanged)]
    spies.extend(QSignalSpy(box.toggled) for box in field._checks.values())
    raw = location(["wall"], enabled=True, text="Schacht")
    field.update_silently(raw)
    field.set_value(location(["floor"], text="retained"))
    assert all(spy.count() == 0 for spy in spies)
    assert field.value() == location(["floor"], text="retained")
    assert raw == location(["wall"], enabled=True, text="Schacht")


def test_widget_disabled_custom_retains_text(widget):
    field, _button = widget
    field.update_silently(location(enabled=True, text="Unter dem Podest"))
    click(field._custom_check)
    assert not field._custom_edit.isEnabled()
    assert field.value() == location(text="Unter dem Podest")
    assert format_cable_laying_location(field.value()) == "–"
    click(field._custom_check)
    assert field._custom_edit.text() == "Unter dem Podest"


def test_widget_typing_commits_on_enter_and_focus_out_only(widget, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    field, button = widget
    field.update_silently(location(enabled=True))
    seen = []
    field.value_changed.connect(lambda *args: seen.append(args))
    type_text(field._custom_edit, "Trasse")
    assert field.has_pending_edit() and seen == []
    QTest.qWait(450)  # No idle timer may write this compound field.
    assert seen == []
    QTest.keyClick(field._custom_edit, Qt.Key_Return)
    assert len(seen) == 1 and not field.has_pending_edit()
    type_text(field._custom_edit, "Podest")
    assert len(seen) == 1
    QTest.mouseClick(button, Qt.LeftButton)
    app.processEvents()
    assert len(seen) == 2 and seen[-1][1]["custom_text"] == "Podest"
    field.commit_pending_edit()
    assert len(seen) == 2


def test_widget_external_refresh_preserves_pending_focus_and_cursor(widget):
    field, _button = widget
    field.update_silently(location(["wall"], enabled=True, text="old"))
    seen = []
    field.value_changed.connect(lambda *args: seen.append(args))
    type_text(field._custom_edit, "typing")
    cursor = field._custom_edit.cursorPosition()
    assert field._custom_edit.hasFocus()
    field.update_silently(location(["wall"], enabled=True, text="external"))
    assert field._custom_edit.text() == "typing"
    assert field._custom_edit.cursorPosition() == cursor
    assert field.has_pending_edit() and seen == []
    field.commit_pending_edit()
    assert len(seen) == 1 and seen[0][1]["custom_text"] == "typing"


def test_widget_unchecking_custom_commits_pending_text_without_reset(widget):
    field, _button = widget
    field.update_silently(location(enabled=True))
    seen = []
    field.value_changed.connect(lambda *args: seen.append(args))
    type_text(field._custom_edit, "saved text")
    click(field._custom_check)
    assert field.value() == location(text="saved text")
    assert not field.has_pending_edit()
    assert seen[-1][1] == location(text="saved text")


def test_widget_returning_to_committed_text_clears_pending_without_signal(widget):
    field, _button = widget
    field.update_silently(location(enabled=True, text="same"))
    seen = []
    field.value_changed.connect(lambda *args: seen.append(args))
    type_text(field._custom_edit, "different")
    assert field.has_pending_edit()
    type_text(field._custom_edit, "same")
    assert not field.has_pending_edit()
    field.commit_pending_edit()
    assert seen == []


def test_editor_undo_snapshot_refresh_and_explicit_flush(app, document, spec):
    from PySide6.QtCore import QCoreApplication, QEvent
    from gui.properties.generic_editor import GenericElementEditor
    from model.schema import ElementSchema

    cable = document.elements["elec_cables"]["EK-1"]
    cable.laying_location = location(enabled=True, text="before")
    editor = GenericElementEditor(document, cable, ElementSchema(
        element_cls=ElecCable, title="Kabel", fields=(spec,),
    ))
    snapshots, changes = [], []
    editor.pre_change.connect(lambda: snapshots.append(copy.deepcopy(document.snapshot())))
    editor.field_changed.connect(lambda *args: changes.append(args))
    editor.show()
    editor.activateWindow()
    app.processEvents()
    try:
        field = editor._widgets["laying_location"]
        type_text(field._custom_edit, "pending")
        assert editor.has_pending_edit() and changes == []
        editor.refresh()
        assert field._custom_edit.text() == "pending"
        assert changes == [] and cable.laying_location["custom_text"] == "before"
        editor.commit_pending_edit()
        assert len(changes) == len(snapshots) == 1
        assert snapshots[0]["params"]["elec_cables"]["EK-1"]["laying_location"]["custom_text"] == "before"
        assert cable.laying_location["custom_text"] == "pending"
        assert not editor.has_pending_edit()
        editor.commit_pending_edit()
        assert len(changes) == 1
        after = copy.deepcopy(document.snapshot())
        document.restore(snapshots[0])
        assert document.elements["elec_cables"]["EK-1"].laying_location["custom_text"] == "before"
        document.restore(after)
        assert document.elements["elec_cables"]["EK-1"].laying_location["custom_text"] == "pending"
    finally:
        editor.close()
        editor.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_full_cable_editor_immediate_checkbox_writes_and_silent_refresh(app, document):
    from gui.properties.generic_editor import GenericElementEditor

    cable = document.elements["elec_cables"]["EK-1"]
    editor = GenericElementEditor(document, cable, ELEC_CABLE_SCHEMA)
    snapshots, changes = [], []
    editor.pre_change.connect(lambda: snapshots.append(copy.deepcopy(cable.to_params())))
    editor.field_changed.connect(lambda *args: changes.append(args))
    try:
        editor.show()
        app.processEvents()
        field = editor._widgets["laying_location"]
        click(field._checks["floor"])
        assert cable.laying_location == location(["floor"])
        assert "laying_location" not in snapshots[0]
        assert changes == [("EK-1", "laying_location", location(["floor"]))]
        editor.refresh()
        assert len(changes) == 1
        assert cable.label_visible is False
    finally:
        editor.close()
        editor.deleteLater()


def test_focus_switch_between_cables_commits_to_original_only(app, document, spec):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    from gui.properties.generic_editor import GenericElementEditor
    from model.schema import ElementSchema

    first = document.elements["elec_cables"]["EK-1"]
    second = document.add(ElecCable.create("EK-2"))
    first.laying_location = location(enabled=True)
    second.laying_location = location(enabled=True, text="second")
    host = QWidget()
    layout = QVBoxLayout(host)
    schema = ElementSchema(element_cls=ElecCable, title="Kabel", fields=(spec,))
    editors = [GenericElementEditor(document, cable, schema, host) for cable in (first, second)]
    for editor in editors:
        layout.addWidget(editor)
    changes = []
    for editor in editors:
        editor.field_changed.connect(lambda *args: changes.append(args))
    host.show()
    host.activateWindow()
    app.processEvents()
    try:
        first_field = editors[0]._widgets["laying_location"]
        second_field = editors[1]._widgets["laying_location"]
        type_text(first_field._custom_edit, "first draft")
        editors[0].refresh()
        assert first_field._custom_edit.text() == "first draft"
        QTest.mouseClick(second_field._custom_edit, Qt.LeftButton)
        app.processEvents()
        assert first.laying_location["custom_text"] == "first draft"
        assert second.laying_location["custom_text"] == "second"
        assert changes == [("EK-1", "laying_location", location(enabled=True, text="first draft"))]
        editors[0].refresh()
        assert not editors[0].has_pending_edit()
        assert first_field._custom_edit.text() == "first draft"
    finally:
        host.close()
        host.deleteLater()


def test_location_is_not_added_to_batch_whitelist():
    from gui.docks.properties_dock import PropertiesDock

    assert "laying_location" not in PropertiesDock._MULTI_EDITABLE_KEYS