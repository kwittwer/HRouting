"""Active AppWindow export rows and real QPdfWriter output (no renderer mocks)."""

from __future__ import annotations

import copy
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings  # noqa: E402
from PySide6.QtGui import QFontDatabase, QPageLayout, QPageSize, QPainter, QPdfWriter  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from gui.app_window import AppWindow  # noqa: E402
from model.cable_laying_location import (  # noqa: E402
    aggregate_cable_laying_locations,
    format_cable_laying_location,
    normalize_cable_laying_location,
)
from model.computed import cable_length_details  # noqa: E402
from model.document import Document  # noqa: E402
from storage.hrp_io import load_document  # noqa: E402


CUSTOM = 'Kabelkanal; "Südflügel" – außen / über Tür\nPrüfung: ÄÖÜ ß Ω'
HIDDEN = "INACTIVE_CUSTOM_MUST_NOT_APPEAR"
OUTSIDE = "FILTER_ONLY_LOCATION"


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    # Windows' offscreen Qt plugin may expose no system fonts at all. Register
    # real fonts (not a painter mock) so the resulting PDF has actual glyphs.
    font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("arial.ttf", "arialbd.ttf", "seguisym.ttf"):
        path = font_dir / name
        if path.exists():
            assert QFontDatabase.addApplicationFont(str(path)) >= 0
    assert QFontDatabase.families(), "Real fonts required for PDF content assertions"
    return instance


@pytest.fixture
def window(app, monkeypatch):
    # Only modal UI and persisted settings are replaced. Document, docks,
    # row collectors, canvas rendering and Qt PDF drawing are all real.
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kwargs: default)
    monkeypatch.setattr(QSettings, "setValue", lambda *args: None)

    def unexpected_modal(*args, **kwargs):
        raise AssertionError("Unexpected modal message during export test")

    for name in ("critical", "warning", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, unexpected_modal)
    instance = AppWindow()
    try:
        yield instance
    finally:
        instance._dirty = False
        instance.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


def _document(*, custom=CUSTOM, locations=True):
    points = {
        "AP-1": {"name": "Shared", "room_id": "ER-1", "ap_type": "uv",
                 "uv_config": {"rows": 1, "modules_per_row": 12, "slots": [
                     {"row": 1, "slot": 1, "device_type": "LS", "assignment": "EK-1", "note": "UV note"}
                 ]}},
        "AP-2": {"name": "Shared", "room_id": "ER-1", "ap_type": "up_distribution",
                 "up_distribution_config": {
                     "incoming_cable_id": "EK-1", "outgoing_cable_ids": ["EK-2", "EK-3"],
                     "mappings": [
                         {"from_conductor": "L1", "to_cable_id": "EK-2", "to_conductor": "L1", "note": "Mapping note"},
                         {"from_conductor": "N", "to_cable_id": "EK-3", "to_conductor": "N", "note": "Second mapping"},
                     ], "note": "Distribution note",
                 }},
        "AP-3": {"name": "Shared", "room_id": "ER-2"},
        "AP-4": {"name": "Far", "room_id": "ER-2"},
    }
    endpoints = [("AP-1", "AP-2"), ("AP-2", "AP-3"), ("AP-3", "AP-4"), ("AP-4", "AP-4")]
    values = [
        {"locations": ["wall", "floor"], "custom_enabled": True, "custom_text": custom},
        {"locations": ["ceiling", "wall"], "custom_enabled": False, "custom_text": HIDDEN},
        None,  # old cable: property absent, not merely an explicitly empty value
        {"locations": [], "custom_enabled": True, "custom_text": OUTSIDE},
    ]
    cables = {}
    for i, (start, end) in enumerate(endpoints, 1):
        cables[f"EK-{i}"] = {
            "cable_id": f"EK-{i}", "floor_plan_id": "grundriss-1", "name": "Duplicate",
            "type": "NYM-J 3x1,5" if i < 4 else "OUTSIDE-TYPE",
            "start_ap": start, "end_ap": end, "comment": f"Cable note {i}",
            "start_length_surcharge_m": i * 0.1, "end_length_surcharge_m": i * 0.2,
            "label_visible": False,
        }
        if locations and values[i - 1] is not None:
            cables[f"EK-{i}"]["laying_location"] = values[i - 1]
    for pid, point in points.items():
        point.update(point_id=pid, floor_plan_id="grundriss-1", builtin_symbol="Steckdose",
                     height_from_floor=300.0, note=f"AP note {pid}")
    return Document.from_dict({
        "canvas": {
            "mm_per_px": 10.0,
            "floor_plans": [{"fp_id": "grundriss-1", "mm_per_px": 10.0, "visible": True}],
            "elec_points": {"AP-1": [20, 50], "AP-2": [120, 50], "AP-3": [420, 50], "AP-4": [620, 50]},
            "elec_rooms": {"ER-1": [[0, 0], [200, 0], [200, 200], [0, 200]],
                           "ER-2": [[300, 0], [800, 0], [800, 200], [300, 200]]},
            "elec_cables": {f"EK-{i}": [[i * 100, 50], [i * 100 + i * 80, 50]] for i in range(1, 5)},
            "cable_start_ap": {f"EK-{i}": start for i, (start, end) in enumerate(endpoints, 1)},
            "cable_end_ap": {f"EK-{i}": end for i, (start, end) in enumerate(endpoints, 1)},
        },
        "params": {
            "floorplans": {"grundriss-1": {"name": "EG", "file_path": ""}},
            "elec_points": points, "elec_cables": cables,
            "elec_rooms": {"ER-1": {"room_id": "ER-1", "name": "Near", "floor_plan_id": "grundriss-1"},
                           "ER-2": {"room_id": "ER-2", "name": "Far room", "floor_plan_id": "grundriss-1"}},
        },
    })


def _compact(text):
    return re.sub(r"\s+", "", text)


def _write_pdf(window, path, *, page=None, table=None, resolution=150):
    writer = QPdfWriter(str(path))
    writer.setResolution(resolution)
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setPageOrientation(QPageLayout.Landscape)
    painter = QPainter(writer)
    assert painter.isActive()
    window._pdf_prepare_footer()
    saved = window._save_all_visibility()
    try:
        if table is not None:
            window._draw_pdf_table(painter, writer, **table)
        else:
            data = window._collect_export_data()
            ap_rows, cable_rows = window._collect_pdf_electro_rows()
            window._apply_page_visibility(page)
            window._render_pdf_export_page(
                painter, writer, page, data["hk_rows"], data["t_supply"], data["t_return"],
                ap_rows, cable_rows, data,
            )
        window._pdf_finalize_footer(painter, writer)
    finally:
        painter.end()
        window._restore_all_visibility(saved)
    assert path.read_bytes().startswith(b"%PDF-")
    fitz = pytest.importorskip("fitz", reason="PDF cell/content verification requires PyMuPDF")
    return fitz.open(path)


def test_all_export_rows_keep_per_cable_metadata_with_duplicate_names(window):
    window._set_document(_document())
    data = window._collect_export_data()
    rows = {row["id"]: row for row in data["kv_rows"]}
    assert rows["EK-1"]["name"] == rows["EK-2"]["name"]
    assert rows["EK-1"]["length_m"] != rows["EK-2"]["length_m"]
    for cid, cable in window._document.elements["elec_cables"].items():
        assert rows[cid]["laying_location"] == cable.laying_location
        assert rows[cid]["laying_location_text"] == format_cable_laying_location(cable.laying_location)
        assert rows[cid]["length_m"] == cable_length_details(window._document, cable)["length_m"]
    connections = [conn for values in data["ap_cables"].values() for conn in values]
    assert len(connections) == 8
    assert len(data["room_ap_connections"]) == 8
    for conn in connections + data["room_ap_connections"]:
        source = rows[conn["cable_id"]]
        assert conn["laying_location"] == source["laying_location"]
        assert conn["laying_location_text"] == source["laying_location_text"]
        assert conn["length_m"] == source["length_m"]
        assert conn["cable_note"] == source["comment"]
        assert conn["ap_id"] in {source["start_ap_id"], source["end_ap_id"]}
    general_rows = window._collect_pdf_electro_rows()[1]
    assert len(general_rows) == 4
    assert all(len(row) == 6 for row in general_rows)
    assert sorted(row[5] for row in general_rows) == sorted(row["laying_location_text"] for row in rows.values())
    aps, cables, _, room_rows = window._collect_pdf_elektro_room_rows(["ER-1"])
    assert aps == {"AP-1", "AP-2"}
    assert cables == {"EK-1", "EK-2"}
    assert {row[5] for row in room_rows} == {rows["EK-1"]["laying_location_text"], rows["EK-2"]["laying_location_text"]}
    assert all(HIDDEN not in row["laying_location_text"] for row in rows.values())
    assert rows["EK-2"]["laying_location"]["custom_text"] == HIDDEN


def test_bom_and_separate_summaries_union_active_locations_without_recounting(window):
    window._set_document(_document(locations=False))
    baseline = window._collect_export_data()
    window._set_document(_document())
    data = window._collect_export_data()
    assert data["kv_sum"] == baseline["kv_sum"]
    assert [row["length_m"] for row in data["kv_rows"]] == [row["length_m"] for row in baseline["kv_rows"]]
    assert len(data["cable_bom_rows"]) == 2
    without_location = lambda rows: [{k: v for k, v in row.items() if k != "laying_location_text"} for row in rows]
    assert without_location(data["cable_bom_rows"]) == without_location(baseline["cable_bom_rows"])
    for key in ("ap_bom_rows", "hkv_line_bom_rows", "uv_device_bom_rows", "uv_busbar_bom_rows"):
        assert data[key] == baseline[key]
    bom = next(row for row in data["cable_bom_rows"] if row["key"] == "NYM-J 3x1,5")
    expected = aggregate_cable_laying_locations(row["laying_location"] for row in data["kv_rows"] if row["type"] == bom["key"])
    assert bom["laying_location_text"] == expected
    assert bom["quantity"] == pytest.approx(data["kv_sum"][bom["key"]])
    assert expected.count("In der Wand") == 1
    assert all(label in expected for label in ("Auf dem Boden", "In der Wand", "In der Decke", CUSTOM))
    assert HIDDEN not in expected
    assert data["kv_laying_location_by_type"][bom["key"]] == expected


def test_distributor_rows_resolve_actual_input_output_and_mapping_ids(window):
    window._set_document(_document())
    data = window._collect_export_data()
    rows = {row["id"]: row for row in data["kv_rows"]}
    uv = data["uv_rows"][0]
    assert uv["cable_id"] == "EK-1"
    assert uv["laying_location"] == rows["EK-1"]["laying_location"]
    assert uv["laying_location_text"] == rows["EK-1"]["laying_location_text"]
    assert data["uv_data"][0]["slots"][0]["laying_location_text"] == uv["laying_location_text"]
    assert len(data["up_distribution_rows"]) == 2
    for up in data["up_distribution_rows"]:
        assert up["incoming_laying_location_text"] == rows["EK-1"]["laying_location_text"]
        assert up["to_cable_laying_location_text"] == rows[up["to_cable_id"]]["laying_location_text"]
        assert up["to_cable_laying_location"] == rows[up["to_cable_id"]]["laying_location"]
        assert up["outgoing_laying_location_text"] == rows["EK-2"]["laying_location_text"]
        assert up["distribution_note"] == "Distribution note"
        assert {item["cable_id"] for item in up["outgoing_cable_locations"]} == {"EK-2", "EK-3"}
        for item in up["outgoing_cable_locations"]:
            assert item["laying_location_text"] == rows[item["cable_id"]]["laying_location_text"]
    point = window._document.get("AP-2")
    point.data["up_distribution_config"]["mappings"] = []
    up = window._collect_up_distribution_rows()[0]
    assert up["to_cable_laying_location_text"] == "–"
    assert up["incoming_laying_location_text"] == rows["EK-1"]["laying_location_text"]
    assert up["outgoing_laying_location_text"] == rows["EK-2"]["laying_location_text"]


def test_uv_name_compatibility_never_guesses_between_duplicate_names(window):
    window._set_document(_document())
    cable1, cable2 = window._document.get("EK-1"), window._document.get("EK-2")
    assert cable1.name == cable2.name
    assert window._uv_slot_cable_fields("AP-1", {"assignment": cable1.name})["cable_id"] == "EK-1"
    # Both cables connect to AP-2: a legacy name alone is ambiguous.
    ambiguous = window._uv_slot_cable_fields("AP-2", {"assignment": cable1.name})
    assert ambiguous["cable_id"] == ""
    assert ambiguous["laying_location_text"] == "–"
    assert window._uv_slot_cable_fields("AP-2", {"assignment": cable1.name, "cable_id": "EK-2"})["laying_location"] == cable2.laying_location
    assert window._uv_slot_cable_fields("AP-1", {"assignment": "unrelated circuit"})["laying_location_text"] == "–"


def test_old_projects_and_unresolved_endpoints_export_empty_locations(window):
    old = _document(locations=False)
    window._set_document(old)
    for cable in old.elements["elec_cables"].values():
        assert "laying_location" not in cable.data
    data = window._collect_export_data()
    assert all(row["laying_location_text"] == "–" for row in data["kv_rows"])
    assert all(row["laying_location"] == normalize_cable_laying_location(None) for row in data["kv_rows"])
    assert all(row["laying_location_text"] == "–" for row in data["cable_bom_rows"])
    # Missing AP definitions must not cause an attribute access on None.
    old.get("EK-1").start_ap = "AP-999"
    old.get("EK-1").end_ap = "AP-998"
    row = window._collect_export_data()["kv_rows"][0]
    assert row["start_ap"] == "AP-999" and row["end_ap"] == "AP-998"
    assert any(row[2:4] == ["AP-999", "AP-998"] for row in window._collect_pdf_electro_rows()[1])
    # Load an actual checked-in pre-feature project too; do not rewrite it.
    window._set_document(load_document(ROOT / "examples" / "minimal.hrp"))
    data = window._collect_export_data()
    assert all(row["laying_location_text"] == "–" for row in data["kv_rows"])


@pytest.mark.parametrize("section,title", [
    ("el_kabel", "Elektro – Kabelverbindungen"),
    ("el_ap_connections", "Anschlusspunkte – Kabelzuordnung"),
    ("el_rooms", "AP-Zuordnung nach Räumen"),
    ("el_uv", "Unterverteilungen (UV)"),
    ("el_up_distribution", "Unterputz-Verteilungen"),
    ("el_bom", "Stückliste"),
    ("schaltplan_stromkreise", "Schaltplan – Stromkreise"),
])
def test_real_pdf_sections_contain_headers_and_complete_location_cells(window, tmp_path, section, title):
    window._set_document(_document())
    with _write_pdf(window, tmp_path / f"{section}.pdf", page={"type": "elektro", "title": "Plan", "table_sections": [section]}) as pdf:
        table_pages = [page for page in pdf if _compact(title) in _compact(page.get_text())]
        assert table_pages, "Expected table title missing from real PDF"
        text = "\n".join(page.get_text() for page in table_pages)
        assert "Verlegeort" in _compact(text)
        assert _compact(CUSTOM) in _compact(text)
        assert _compact("Auf dem Boden") in _compact(text)
        assert _compact("In der Wand") in _compact(text)
        assert HIDDEN not in text
        if section not in {"el_uv", "schaltplan_stromkreise"}:
            assert _compact("In der Decke") in _compact(text)
        if section == "el_up_distribution":
            assert "EK-2" in text and "EK-3" in text
            assert "VerlegeortZuleitung" in _compact(text)
            assert "VerlegeortAbgänge" in _compact(text)
        if section == "el_bom":
            assert "NYM-J 3x1,5" in text
            assert "Anschlusspunkte" in text
        assert all("Seite" in page.get_text() for page in pdf)


@pytest.mark.parametrize("page", [
    {"type": "elektro_room", "title": "Selected", "room_ids": ["ER-1"]},
    {"type": "uv", "title": "UV details", "uv_ap_id": "AP-1"},
])
def test_filtered_room_and_uv_real_pdfs_include_only_related_locations(window, tmp_path, page):
    window._set_document(_document())
    with _write_pdf(window, tmp_path / f"{page['type']}.pdf", page=page) as pdf:
        text = "\n".join(p.get_text() for p in pdf)
        assert "Verlegeort" in _compact(text)
        assert _compact(CUSTOM) in _compact(text)
        assert OUTSIDE not in text
        assert HIDDEN not in text
        assert pdf.page_count >= 2


@pytest.mark.parametrize("resolution", [96, 150, 300])
def test_wide_pdf_wraps_header_and_entire_multi_page_cell_without_truncation(window, tmp_path, resolution):
    # One cell exceeds several pages, including an unbroken word and Unicode.
    markers = [f'LOCATION_{i:04d};"ÄΩß"' for i in range(450)]
    long_text = "\n".join(markers) + "\n" + "UNBROKEN" * 300 + "\nFINAL_LOCATION_SENTINEL"
    table = {
        "title": "Long location table", "headers": ["ID", "Name", "3", "4", "5", "6", "7", "Verlegeort vollständige Beschreibung"],
        "rows": [["EK-1", "Cable", "", "", "", "", "", long_text],
                 ["EK-2", "Next row", "", "", "", "", "", "NEXT_ROW_SENTINEL"]],
        "col_widths": [1, 1, 1, 1, 1, 1, 1, 1.5], "wrap_columns": {7},
    }
    with _write_pdf(window, tmp_path / f"long-{resolution}.pdf", table=table, resolution=resolution) as pdf:
        assert pdf.page_count > 2
        all_text = "\n".join(page.get_text() for page in pdf)
        compact = _compact(all_text)
        for marker in markers:
            assert _compact(marker) in compact
            assert compact.count(_compact(marker)) == 1
        assert "FINAL_LOCATION_SENTINEL" in compact
        assert "NEXT_ROW_SENTINEL" in compact
        # Read the location column only, excluding repeat headers and footers.
        location_text = []
        for index, page in enumerate(pdf):
            words = page.get_text("words")
            heading = [word for word in words if word[4] == "Verlegeort"]
            assert heading
            assert "vollständigeBeschreibung" in _compact(page.get_text())
            header_bottom = max(word[3] for word in words if word[4] in {"Verlegeort", "vollständige", "Beschreibung"})
            footer_top = min(word[1] for word in words if word[4] in {"Datum:", "Seite"})
            for word in words:
                if word[0] >= heading[0][0] - 1 and word[1] > header_bottom and word[3] < footer_top:
                    location_text.append(word[4])
            assert f"Seite {index + 1}" in page.get_text()
            if index:
                assert "Fortsetzung" in page.get_text()
        assert "UNBROKEN" * 300 in _compact("".join(location_text))


def test_export_rows_follow_duplicate_edit_undo_and_redo(window):
    window._set_document(_document())
    original = copy.deepcopy(window._document.get("EK-1").laying_location)
    clone_id = window._duplicate_element("EK-1")
    assert clone_id
    clone = window._document.get(clone_id)
    assert clone.laying_location == original
    window._push_undo()
    clone.laying_location = {"locations": ["ceiling"], "custom_enabled": False, "custom_text": "retained"}
    window._on_property_changed(clone_id, "laying_location", clone.laying_location)
    row = next(row for row in window._collect_export_data()["kv_rows"] if row["id"] == clone_id)
    assert row["laying_location_text"] == "In der Decke"
    assert window._document.get("EK-1").laying_location == original
    window._undo()
    row = next(row for row in window._collect_export_data()["kv_rows"] if row["id"] == clone_id)
    assert row["laying_location"] == original
    window._redo()
    row = next(row for row in window._collect_export_data()["kv_rows"] if row["id"] == clone_id)
    assert row["laying_location_text"] == "In der Decke"


def test_export_flushes_unfinished_custom_input(window, app):
    from PySide6.QtTest import QTest

    window._set_document(_document())
    window.properties.show_element("EK-1")
    window.show()
    widget = window.properties._editors["EK-1"]._widgets["laying_location"]
    app.processEvents()
    widget._custom_edit.setFocus()
    widget._custom_edit.selectAll()
    QTest.keyClicks(widget._custom_edit, "Pending export route")
    assert widget.has_pending_edit()
    assert window._document.get("EK-1").laying_location["custom_text"] == CUSTOM
    data = window._collect_export_data()
    row = next(row for row in data["kv_rows"] if row["id"] == "EK-1")
    assert row["laying_location"]["custom_text"] == "Pending export route"
    assert not widget.has_pending_edit()


def test_hrp_element_import_keeps_independent_location():
    from logic.hrp_import import import_selected_elements, selection_key
    from model.elements import ElecCable

    source = _document()
    target = Document()
    result = import_selected_elements(source, target, [selection_key(ElecCable, "EK-1")])
    imported = target.get(result.id_map[selection_key(ElecCable, "EK-1")])
    original = copy.deepcopy(source.get("EK-1").laying_location)
    assert imported.laying_location == original
    imported.laying_location = {"locations": ["ceiling"]}
    assert source.get("EK-1").laying_location == original


def test_kicad_reimport_preserves_user_location(window):
    from logic.kicad_import import KiCadScanResult, KiCadTextFieldCandidate, KiCadTextFieldMetadata
    from model.elements import TextAnnotation

    window._set_document(_document())
    text = TextAnnotation.create("TEXT-1", floor_plan_id="grundriss-1", content="AP_NAME: Light")
    text.geom["text_annotations"] = {"pos": [40, 80], "content": "AP_NAME: Light", "font_size": 14}
    window._document.add(text)
    scan = KiCadScanResult(root_path=ROOT, project_uuid="location-test")
    scan.textfield_candidates[text.id] = KiCadTextFieldCandidate(
        key=text.id,
        source_metadata=KiCadTextFieldMetadata(text_id=text.id, ap_name="Light", room="", floor_plan_id="grundriss-1"),
        cable_name="Light", matched_spec="3x1,5", best_matched_candidate=None,
    )
    summary = window._apply_kicad_cable_import(scan, [text.id])
    assert summary["created"] == 1
    imported = next(cable for cable in window._document.elements["elec_cables"].values()
                    if cable.data.get("kicad_project_uuid") == "location-test" or
                    cable.data.get("kicad_cable_key", "").startswith("location-test::"))
    assert imported.laying_location == normalize_cable_laying_location(None)
    assert imported.label_visible is False
    original = {"locations": ["wall"], "custom_enabled": True, "custom_text": CUSTOM}
    imported.laying_location = original
    summary = window._apply_kicad_cable_import(scan, [text.id])
    assert summary["created"] == 0 and summary["updated"] == 1
    assert imported.laying_location == original


def test_production_export_keeps_long_custom_text_page_count_and_visibility(window, tmp_path, monkeypatch):
    markers = [f'CUSTOM_LINE_{i:04d};"ÄΩß"' for i in range(200)]
    custom = "\n".join(markers) + "\nCUSTOM_FINAL_SENTINEL"
    window._set_document(_document(custom=custom))
    before = window._save_all_visibility()
    location_before = copy.deepcopy(window._document.get("EK-1").laying_location)
    path = tmp_path / "production.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(path), "PDF (*.pdf)"))
    pages = window._normalize_pdf_export_pages([
        {"type": "elektro", "title": "Production", "enabled": True, "table_sections": ["el_kabel"]}
    ])
    window._continue_export_pdf(pages, window._normalize_pdf_export_meta({}, pages))
    assert window._save_all_visibility() == before
    assert window._document.get("EK-1").laying_location == location_before
    fitz = pytest.importorskip("fitz")
    with fitz.open(path) as pdf:
        assert pdf.page_count > 3
        assert window._pdf_export_meta["page_count"] == str(pdf.page_count)
        text = _compact("\n".join(page.get_text() for page in pdf))
        assert "CUSTOM_FINAL_SENTINEL" in text
        for marker in markers:
            assert text.count(_compact(marker)) == 1
        for index, page in enumerate(pdf):
            assert f"Seite {index + 1}" in page.get_text()