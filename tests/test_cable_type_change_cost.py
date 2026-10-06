"""Kabeltyp-Änderung darf nicht das halbe Projekt neu aufbauen.

Die Tests prüfen Aufrufzahlen statt Laufzeiten, damit sie auf jeder Maschine
deterministisch sind.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui import app_window, layout_store  # noqa: E402
from gui.properties.field_widgets import ChoiceFieldWidget  # noqa: E402
from model.document import Document  # noqa: E402
from model.schema import FieldKind, FieldSpec  # noqa: E402


AP_COUNT = 40
CABLE_COUNT = 40


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _project_dict() -> dict:
    elec_points: dict[str, list[float]] = {}
    point_params: dict[str, dict] = {}
    for index in range(1, AP_COUNT + 1):
        pid = f"AP-{index}"
        elec_points[pid] = [100.0 + index * 10.0, 100.0]
        point_params[pid] = {"name": pid, "floor_plan_id": "grundriss-1"}

    elec_cables: dict[str, list[list[float]]] = {}
    cable_params: dict[str, dict] = {}
    start_ap: dict[str, str] = {}
    end_ap: dict[str, str] = {}
    for index in range(1, CABLE_COUNT + 1):
        cid = f"EK-{index}"
        start = f"AP-{index}"
        end = f"AP-{(index % AP_COUNT) + 1}"
        elec_cables[cid] = [elec_points[start], [250.0, 200.0], elec_points[end]]
        cable_params[cid] = {
            "floor_plan_id": "grundriss-1",
            "type": "5x1,5",
            "color": "#ff9800",
            "stroke_width": 2.0,
            "line_style": "solid",
        }
        start_ap[cid] = start
        end_ap[cid] = end

    return {
        "canvas": {
            "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
            "elec_points": elec_points,
            "elec_cables": elec_cables,
            "cable_start_ap": start_ap,
            "cable_end_ap": end_ap,
        },
        "params": {
            "floorplans": {"grundriss-1": {"name": "EG", "file_path": ""}},
            "elec_points": point_params,
            "elec_cables": cable_params,
        },
    }


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    store.setFallbacksEnabled(False)
    monkeypatch.setattr(layout_store, "settings", lambda: store)
    monkeypatch.setattr(app_window.AppWindow, "_auto_load_last_project", lambda self: None)
    w = app_window.AppWindow()
    w._set_document(Document.from_dict(_project_dict()))
    w.topology.hide()
    yield w
    w.deleteLater()


def test_type_change_takes_single_snapshot_and_no_hidden_schema_rebuild(window, monkeypatch):
    document = window._document
    snapshot = Mock(wraps=document.snapshot)
    monkeypatch.setattr(document, "snapshot", snapshot)
    build = Mock(wraps=window._build_schema_data)
    monkeypatch.setattr(window, "_build_schema_data", build)
    undo_before = len(window._undo_stack)

    window._push_undo()
    document.elements["elec_cables"]["EK-1"].data["type"] = "5x2,5"
    window._on_property_changed("EK-1", "type", "5x2,5")

    assert snapshot.call_count == 1
    assert len(window._undo_stack) == undo_before + 1
    # Topologie und Schaltplan sind unsichtbar: kein Schema-Neuaufbau.
    build.assert_not_called()
    assert window._schema_refresh_pending


def test_hidden_schema_rebuild_is_caught_up_when_topology_is_shown(window):
    window._document.elements["elec_cables"]["EK-1"].data["type"] = "5x2,5"
    window._on_property_changed("EK-1", "type", "5x2,5")
    assert window._schema_refresh_pending

    window._show_topology_dock()

    assert not window._schema_refresh_pending
    assert len(window.topology._cable_edges) == CABLE_COUNT


def test_unchanged_style_propagation_emits_no_element_signals(window):
    cables = window._document.elements["elec_cables"]
    window._ensure_cable_type_style_profile("5x1,5")
    # Erster Durchlauf gleicht alle Kabel an das Profil an.
    window._apply_cable_type_style_to_all(
        "5x1,5", color="#ff9800", stroke_width=2.0, line_style="solid"
    )

    seen: list[str] = []
    window._document.element_changed.connect(seen.append)
    changed = window._apply_cable_type_style_to_all(
        "5x1,5", color="#ff9800", stroke_width=2.0, line_style="solid"
    )

    assert changed == []
    assert seen == []
    assert len(cables) == CABLE_COUNT


@pytest.mark.parametrize("key,value,column", [
    ("color", "#12ab34", 6),
    ("stroke_width", 4.5, 7),
    ("line_style", "dashdot", 8),
])
def test_overview_style_changes_match_properties_and_support_undo(window, key, value, column):
    from model.field_access import apply_display_value
    from model.schema import ELEC_CABLE_SCHEMA

    document = window._document
    spec = next(field for field in ELEC_CABLE_SCHEMA.fields if field.key == key)
    cable = document.get("EK-1")
    original = cable.data[key]
    window._push_undo()
    apply_display_value(cable, spec, value)
    window._on_property_changed("EK-1", key, value)
    expected = [dict(item.data) for item in document.elements["elec_cables"].values()]
    window._undo()
    dock = window.overview_electro_cables
    dock.refresh_now()
    table = dock._elec_cable_table
    row = next(index for index in range(table.rowCount())
               if table.item(index, 0).text() == document.get("EK-1").name)
    undo_before = len(window._undo_stack)
    editor = table.cellWidget(row, column)
    if key == "stroke_width":
        editor._spin.setValue(value)
    elif key == "line_style":
        editor._combo.setCurrentIndex(editor._combo.findData(value))
    else:
        editor._emit(value)
    assert len(window._undo_stack) == undo_before + 1
    assert [dict(item.data) for item in document.elements["elec_cables"].values()] == expected
    if key == "color":
        assert all(window.canvas._color_map[cid].name() == value for cid in document.elements["elec_cables"])
    else:
        canvas_values = getattr(window.canvas, f"_elec_cable_{key}")
        assert all(canvas_values[cid] == value for cid in document.elements["elec_cables"])
    dock.refresh_now()
    assert str(value) in table.item(row, 9).toolTip()
    window._undo()
    assert document.get("EK-1").data[key] == original
    window._redo()
    assert document.get("EK-1").data[key] == value


def test_editable_choice_commits_once_instead_of_per_keystroke(app):
    widget = ChoiceFieldWidget(
        FieldSpec("type", "Kabeltyp", FieldKind.EDITABLE_CHOICE),
        editable=True,
        options=("5x1,5", "5x2,5"),
    )
    try:
        emitted: list[object] = []
        widget.value_changed.connect(lambda _key, value: emitted.append(value))

        for length in range(1, len("5x2,5") + 1):
            widget._combo.setEditText("5x2,5"[:length])
        assert emitted == []
        assert widget.has_pending_edit()

        widget.commit_pending_edit()

        assert emitted == ["5x2,5"]
        assert not widget.has_pending_edit()

        # Fokusverlust ohne neue Eingabe schreibt nicht erneut.
        widget.commit_pending_edit()
        assert emitted == ["5x2,5"]
    finally:
        widget.deleteLater()
