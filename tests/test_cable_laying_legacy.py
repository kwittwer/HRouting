"""Real legacy editor, CSV, PDF and registered MCP regression coverage."""
from __future__ import annotations

import asyncio
import copy
import csv
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, QPointF, QRectF, QSettings, Qt
from PySide6.QtGui import QFontDatabase, QPainter, QPdfWriter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QTableWidget

from gui import main_window
from gui.parameter_panel import ElektroCablePanel
from model.cable_laying_location import format_cable_laying_location, normalize_cable_laying_location

FLOOR = {"locations": ["floor"], "custom_enabled": False, "custom_text": "retained"}
WALL = {"locations": ["wall"], "custom_enabled": True, "custom_text": 'Kanal; "Süd"\nTrasse'}


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    for name in ("arial.ttf", "arialbd.ttf"):
        path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    return app


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    settings.setFallbacksEnabled(False)
    monkeypatch.setattr(main_window, "_SETTINGS", settings)
    monkeypatch.setattr(main_window.MainWindow, "_auto_load_last_project", lambda self: None)
    w = main_window.MainWindow()
    yield w
    w._dirty_debounce_timer.stop()
    w._dirty = False
    w.deleteLater()
    app.processEvents()


def _populate(w):
    start = w._create_elec_point_panel("AP-1", name="UV")
    end = w._create_elec_point_panel("AP-2", name="UP")
    start.set_ap_type("uv")
    end.set_ap_type("up_distribution")
    w.canvas._elec_points.update({"AP-1": QPointF(0, 0), "AP-2": QPointF(100, 0)})
    for cid, length, location in (("KV-1", 1000, FLOOR), ("KV-2", 2000, WALL)):
        panel = w._create_elec_cable_panel(cid, name="same")
        panel.set_type_text("5x1,5")
        panel.set_laying_location(location)
        w.canvas._elec_cables[cid] = [QPointF(0, 0), QPointF(length, 0)]
        w.canvas._cable_start_ap[cid] = "AP-1"
        w.canvas._cable_end_ap[cid] = "AP-2"
        w._update_cable_ap_labels(cid)
    w._elec_cable_counter = 2
    start.set_uv_config({"rows": 1, "modules_per_row": 12, "slots": [
        {"row": 1, "slot": 1, "device_type": "LS", "assignment": "KV-2"},
        {"row": 1, "slot": 2, "device_type": "LS", "assignment": "UV → UP"},
    ]})
    end.set_up_distribution_config({"incoming_cable_id": "KV-1", "outgoing_cable_ids": ["KV-2"],
                                    "mappings": [{"from_conductor": "L", "to_cable_id": "KV-2", "to_conductor": "L"}]})
    return w._collect_export_data()


def test_panel_real_interactions_pending_commit_roundtrip(app):
    panel = ElektroCablePanel("KV-1", defaults={"laying_location": WALL})
    widget = panel.laying_location_widget
    assert panel.get_parameters()["laying_location"] == normalize_cable_laying_location(None)
    assert not panel.chk_label_visible.isChecked()
    emitted = []
    panel.laying_location_changed.connect(lambda cid, value: emitted.append((cid, value)))
    panel.set_laying_location(FLOOR)
    assert emitted == []
    panel.show()
    app.processEvents()
    for check in (widget._checks["wall"], widget._checks["ceiling"], widget._custom_check):
        QTest.mouseClick(check, Qt.LeftButton, pos=QPoint(8, check.height() // 2))
    widget._custom_edit.setFocus()
    widget._custom_edit.selectAll()
    QTest.keyClicks(widget._custom_edit, "Route pending")
    assert panel.has_pending_edit()
    count = len(emitted)
    data = panel.to_dict()
    assert len(emitted) == count + 1
    assert data["laying_location"]["locations"] == ["floor", "wall", "ceiling"]
    assert data["laying_location"]["custom_text"] == "Route pending"
    QTest.mouseClick(widget._custom_check, Qt.LeftButton, pos=QPoint(8, widget._custom_check.height() // 2))
    assert not widget._custom_edit.isEnabled()
    assert panel.to_dict()["laying_location"]["custom_text"] == "Route pending"
    assert "Route pending" not in format_cable_laying_location(panel.to_dict()["laying_location"])
    clone = ElektroCablePanel("KV-2")
    clone.from_dict(panel.to_dict())
    assert clone.to_dict()["laying_location"] == panel.to_dict()["laying_location"]
    clone.set_laying_location({})
    assert panel.to_dict()["laying_location"]["locations"]
    panel.close()
    clone.close()


def test_legacy_callback_dirty_duplicate_and_new_defaults(window):
    source = window._create_elec_cable_panel("KV-1")
    window._elec_cable_counter = 1
    window._dirty = False
    source.laying_location_widget._checks["floor"].setChecked(True)
    assert window._dirty
    assert window.param_panel.to_dict()["elec_cables"]["KV-1"]["laying_location"]["locations"] == ["floor"]
    duplicate_id = window._duplicate_elec_cable("KV-1")
    duplicate = window.param_panel.elec_cable_panels[duplicate_id]
    assert duplicate.get_parameters()["laying_location"] == source.get_parameters()["laying_location"]
    assert not duplicate.chk_label_visible.isChecked()
    duplicate.set_laying_location(WALL)
    assert source.get_parameters()["laying_location"]["locations"] == ["floor"]
    new = window._create_elec_cable_panel("KV-3")
    assert new.get_parameters()["laying_location"] == normalize_cable_laying_location(None)


def test_legacy_rows_id_collisions_material_union_and_distribution(window):
    data = _populate(window)
    assert data["kv_sum"]["5x1,5"] == pytest.approx(3)
    assert {row["cable_id"]: row["length_m"] for row in data["room_ap_connections"]} == {"KV-1": 1, "KV-2": 2}
    assert all(row["laying_location"] == (FLOOR if row["cable_id"] == "KV-1" else WALL) for row in data["room_ap_connections"])
    assert data["uv_rows"][0]["laying_location"] == WALL
    # Duplicate auto names are ambiguous: never attach one cable's metadata arbitrarily.
    assert data["uv_rows"][1]["laying_location_text"] == "–"
    assert data["uv_data"][0]["slots"][0]["laying_location"] == WALL
    up = data["up_distribution_rows"][0]
    assert up["incoming_laying_location"] == FLOOR
    assert up["to_cable_laying_location"] == WALL
    assert up["outgoing_laying_location_text"] == format_cable_laying_location(WALL)
    bom = data["cable_bom_rows"]
    assert len(bom) == 1 and bom[0]["quantity"] == 3
    assert bom[0]["laying_location_text"] == 'Auf dem Boden, In der Wand, Kanal; "Süd"\nTrasse'

def test_legacy_undo_redo_restores_location_and_reconnects_dirty(window, app):
    panel = window._create_elec_cable_panel("KV-1")
    window._elec_cable_counter = 1
    window._flush_pending_dirty()
    window._push_undo()
    panel.laying_location_widget._checks["floor"].setChecked(True)
    window._flush_pending_dirty()
    window._undo()
    app.processEvents()
    panel = window.param_panel.elec_cable_panels["KV-1"]
    assert panel.get_parameters()["laying_location"]["locations"] == []
    window._redo()
    app.processEvents()
    panel = window.param_panel.elec_cable_panels["KV-1"]
    assert panel.get_parameters()["laying_location"]["locations"] == ["floor"]
    window._dirty = False
    panel.laying_location_widget._checks["wall"].setChecked(True)
    assert window._dirty


def test_legacy_csv_all_sections_roundtrip_quotes_and_newlines(window, tmp_path, monkeypatch):
    data = _populate(window)
    path = tmp_path / "lengths.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), "CSV"))
    window._save_lengths_csv(data["hk_rows"], {}, data["kv_rows"], data["kv_sum"], data["hkv_sum"],
                             data["ap_cables"], data["hl_rows"], data["hl_sum"], data["ap_type_counts"],
                             data["room_ap_connections"], data["uv_rows"], data["up_distribution_rows"], data)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    for section in ("Elektro - Kabelverbindungen", "Elektro - Summe pro Leitungstyp", "Elektro - Anschlusspunkte",
                    "Elektro - Räume mit AP und Kabelzielen", "Elektro - Unterverteilungen",
                    "Elektro - Verteilung in Unterputzdose", "Stückliste - Zusammenfassung"):
        idx = rows.index([section])
        headers = rows[idx + 1]
        assert any("Verlegeort" in cell for cell in headers)
        section_rows = []
        for row in rows[idx + 2:]:
            if not row:
                break
            assert len(row) == len(headers)
            section_rows.append(row)
        assert any(format_cable_laying_location(WALL) in cell for row in section_rows for cell in row)
    idx = rows.index(["Elektro - Summe pro Leitungstyp"])
    assert rows[idx + 2][1] == "3.00"


def test_legacy_inventory_dialog_location_columns(window, monkeypatch):
    _populate(window)
    captured = []
    def inspect(dialog):
        for table in dialog.findChildren(QTableWidget):
            headers = [table.horizontalHeaderItem(i).text() for i in range(table.columnCount())]
            if any("Verlegeort" in header for header in headers):
                captured.append(headers)
        return 0
    monkeypatch.setattr(QDialog, "exec", inspect)
    window._export_lengths()
    assert len(captured) >= 8  # cables, totals, UV, UP, BOM, APs, rooms


def _tool(mcp, name):
    return mcp._tool_manager.get_tool(name).fn


def test_http_mcp_registered_add_modify_lists_and_summary(window):
    pytest.importorskip("mcp")
    from mcp_server import _create_mcp
    server = _create_mcp(window, SimpleNamespace(invoke=lambda fn: fn()))
    created = _tool(server, "add_elec_cable")("ignored", [[0, 0], [1000, 0]], laying_location=WALL)
    cid = created["cable_id"]
    assert created["laying_location"] == WALL
    assert _tool(server, "modify_elec_cable")(cid, comment="only comment")["params"]["laying_location"] == WALL
    assert _tool(server, "list_elec_cables")()[0]["laying_location_text"] == format_cable_laying_location(WALL)
    assert _tool(server, "list_cables")()["cables"][0]["laying_location"] == WALL
    summary = _tool(server, "get_cable_length_summary")()
    assert summary["by_type"] == {"5x1,5": 1.0}
    assert summary["laying_location_by_type"]["5x1,5"]["laying_location_text"] == format_cable_laying_location(WALL)
    assert _tool(server, "get_project_summary")()["cable_laying_location_by_type"]["5x1,5"]["laying_location"]["locations"] == ["wall"]
    schema = asyncio.run(server.list_tools())
    assert "laying_location" in next(t for t in schema if t.name == "add_elec_cable").inputSchema["properties"]
    # Same type, different locations: one unchanged length sum and a union.
    _tool(server, "add_elec_cable")("ignored", [[0, 0], [2000, 0]], laying_location=FLOOR)
    summary = _tool(server, "get_cable_length_summary")()
    assert summary["by_type"] == {"5x1,5": 3.0}
    assert summary["laying_location_by_type"]["5x1,5"]["laying_location"]["locations"] == ["floor", "wall"]
    assert _tool(server, "modify_elec_cable")(cid, laying_location={})["params"]["laying_location"] == normalize_cable_laying_location(None)
    empty = _tool(server, "add_elec_cable")("ignored", [[0, 0], [1000, 0]])
    assert empty["laying_location"] == normalize_cable_laying_location(None)


def test_stdio_registered_lists_bom_union_without_new_write_tools(monkeypatch):
    pytest.importorskip("mcp")
    import mcp_server_stdio as stdio
    state = stdio.ProjectState()
    state.data["canvas"]["mm_per_px"] = 1
    state.data["canvas"]["elec_cables"] = {"EK-1": [[0, 0], [1000, 0]], "EK-2": [[0, 0], [2000, 0]]}
    state.data["params"]["elec_cables"] = {
        "EK-1": {"type": "5x1,5", "name": "same", "laying_location": copy.deepcopy(FLOOR)},
        "EK-2": {"type": "5x1,5", "name": "same", "laying_location": copy.deepcopy(WALL)},
        "EK-3": {"type": "3x1,5"},
    }
    monkeypatch.setattr(stdio, "_state", state)
    server = stdio._create_stdio_mcp()
    before = copy.deepcopy(state.data)
    rows = _tool(server, "list_elec_cables")()
    assert rows[0]["laying_location"] == FLOOR
    assert rows[2]["laying_location_text"] == "–"
    rows[0]["laying_location"]["locations"].clear()
    assert state.data == before
    assert _tool(server, "list_cables")()["cables"][1]["laying_location"] == WALL
    bom = _tool(server, "get_bom_summary")()["summary"]["rows_by_section"]["cable_bom_rows"]
    combined = next(row for row in bom if row["key"] == "5x1,5")
    assert combined["quantity"] == 3
    assert combined["laying_location"]["locations"] == ["floor", "wall"]
    assert combined["laying_location_text"] == 'Auf dem Boden, In der Wand, Kanal; "Süd"\nTrasse'
    assert server._tool_manager.get_tool("add_elec_cable") is None
    assert server._tool_manager.get_tool("modify_elec_cable") is None


def _pdf_context(writer, painter):
    ctx = main_window._PdfContext.__new__(main_window._PdfContext)
    ctx.printer, ctx.painter, ctx.dpi = writer, painter, writer.resolution()
    rect = writer.pageLayout().paintRectPixels(writer.resolution())
    ctx._pr = QRectF(0, 0, rect.width(), rect.height())
    ctx._dry_run, ctx._page_num, ctx._toc = False, 1, []
    return ctx


def test_legacy_real_pdf_inventory_sections(window, tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    data = _populate(window)
    monkeypatch.setattr(window, "_render_plan_to_painter", lambda *args, **kwargs: None)
    path = tmp_path / "inventory.pdf"
    writer = QPdfWriter(str(path))
    writer.setResolution(150)
    painter = QPainter(writer)
    ctx = _pdf_context(writer, painter)
    captured = []
    draw = ctx.draw_table
    def checked(page, y, headers, rows, **kwargs):
        assert all(len(row) == len(headers) for row in rows)
        captured.append(headers)
        return draw(page, y, headers, rows, **kwargs)
    ctx.draw_table = checked
    try:
        window._pdf_elektro_page(ctx, data, table_sections=[
            "el_kabel", "el_ap_connections", "el_rooms", "el_uv", "el_up_distribution", "el_bom"
        ])
        ctx.new_page()
        window._pdf_uv_page(ctx, data)
    finally:
        painter.end()
    assert sum(any("Verlegeort" in header for header in headers) for headers in captured) == 8
    with fitz.open(path) as pdf:
        text = "\n".join(page.get_text() for page in pdf)
        assert "Auf dem Boden" in text
        assert "Kanal;" in text and '"Süd"' in text and "Trasse" in text
        assert len(pdf) >= 9


def test_legacy_pdf_wraps_unbroken_text_across_pages(app, tmp_path):
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "legacy.pdf"
    writer = QPdfWriter(str(path))
    writer.setResolution(150)
    painter = QPainter(writer)
    # Avoid the native QPrinter COM dependency while using the actual legacy renderer.
    ctx = _pdf_context(writer, painter)
    headers = ["AP", "Kabel", "Typ", "Gerät", "Farbe", "Notiz", "Länge", "Verlegeort"]
    text = "BEGIN " + ("UnbrokenRoute" * 300) + " FINISH"
    try:
        ctx.draw_table(ctx.page_rect(), ctx.mm(10), headers, [["AP", "K", "5x1,5", "", "", "", "3", text]])
    finally:
        painter.end()
    with fitz.open(path) as pdf:
        assert len(pdf) > 1
        extracted = "".join(page.get_text() for page in pdf)
        assert "BEGIN" in extracted and "FINISH" in extracted
        assert extracted.count("Verlegeort") == len(pdf)
        # Isolate the location column: repeated headers must not interrupt a
        # word split by a page boundary when reconstructing the original text.
        location_x = (ctx.mm(4) + (ctx._pr.width() - ctx.mm(8)) * 7 / 8) * 72 / ctx.dpi
        body = "".join(
            span["text"] for page in pdf for block in page.get_text("dict")["blocks"]
            for line in block.get("lines", []) for span in line["spans"]
            if span["bbox"][0] >= location_x - 1 and span["text"] != "Verlegeort"
        )
        compact = "".join(body.split())
        assert compact.count("UnbrokenRoute") == 300