from __future__ import annotations

import re

import pytest

from test_cable_laying_exports import app, window, _document, _write_pdf
from gui.cable_line_preview import CableLineSample
from gui.main_window import MainWindow, _PdfContext
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter


STYLES = [("#d12345", 2.0, "solid"), ("#147b39", 4.0, "dash"),
          ("#2367c9", 6.0, "dot"), ("#a524ad", 10.0, "dashdot")]


def _styled_document():
    document = _document()
    for cable, (color, width, style) in zip(document.elements["elec_cables"].values(), STYLES):
        cable.color = color
        cable.data.update(stroke_width=width, line_style=style)
    return document


def _legacy_window(document):
    legacy = MainWindow()
    for cable_id, cable in document.elements["elec_cables"].items():
        panel = legacy._create_elec_cable_panel(cable_id, name=cable.name)
        panel.from_dict(cable.data)
        legacy.canvas.set_elec_cable_line_style(cable_id, cable.data["line_style"])
    return legacy


def _assert_samples(pdf, styles, resolution=150):
    drawings = [drawing for page in pdf for drawing in page.get_drawings()]
    for color, width, style in styles:
        rgb = tuple(int(color[index:index + 2], 16) / 255 for index in (1, 3, 5))
        matches = [drawing for drawing in drawings
                   if drawing.get("color") == pytest.approx(rgb, abs=0.002)
                   and drawing["width"] == pytest.approx(width * 72 / resolution, abs=0.01)]
        assert matches, (color, width, style)
        for drawing in matches:
            assert (drawing["dashes"] == "[] 0") == (style == "solid")
            assert drawing["rect"].width >= 12 * width * 72 / resolution - 0.1
            pattern = {"solid": [], "dash": [4, 2], "dot": [1, 2], "dashdot": [4, 2, 1, 2]}[style]
            actual = [float(value) / drawing["width"] for value in
                      re.findall(r"[\d.]+", drawing["dashes"].split("]")[0])]
            assert actual == pytest.approx(pattern, abs=0.01)


def test_general_pdf_samples_keep_duplicate_cable_identity(window, tmp_path):
    window._set_document(_styled_document())
    for cable in window._document.elements["elec_cables"].values():
        cable.name = "Duplicate"
    rows = window._collect_pdf_electro_rows()[1]
    assert all(len(row) == 6 and isinstance(row[0], CableLineSample) for row in rows)
    assert [(row[0].color, row[0].stroke_width, row[0].line_style) for row in rows] == STYLES
    with _write_pdf(window, tmp_path / "general.pdf", page={"type": "elektro", "table_sections": ["el_kabel"]}) as pdf:
        _assert_samples(pdf, STYLES)


@pytest.mark.parametrize("section,indices", [("el_ap_connections", [0, 1, 2, 3]),
                                             ("el_rooms", [0, 1, 2, 3]),
                                             ("el_uv", [0]), ("el_up_distribution", [0, 1, 2]),
                                             ("schaltplan_stromkreise", [0])])
def test_individual_cable_assignments_have_samples(window, tmp_path, section, indices):
    window._set_document(_styled_document())
    with _write_pdf(window, tmp_path / f"{section}.pdf", page={"type": "elektro", "table_sections": [section]}) as pdf:
        _assert_samples(pdf, [STYLES[index] for index in indices])


def test_room_filtered_pdf_samples(window, tmp_path):
    window._set_document(_styled_document())
    with _write_pdf(window, tmp_path / "room.pdf", page={"type": "elektro_room", "room_ids": ["ER-1"]}) as pdf:
        _assert_samples(pdf, STYLES[:2])
        colors = [drawing.get("color") for page in pdf for drawing in page.get_drawings()]
        for color, _, _ in STYLES[2:]:
            rgb = tuple(int(color[index:index + 2], 16) / 255 for index in (1, 3, 5))
            assert not any(value == pytest.approx(rgb, abs=0.002) for value in colors if value)


def test_room_plan_labels_hidden_cables_with_leaders(window, tmp_path):
    window._set_document(_document())
    for cable in window._document.elements["elec_cables"].values():
        cable.name = "Duplicate"
    original_labels = dict(window.canvas._label_visible)
    with _write_pdf(window, tmp_path / "room-labels.pdf",
                    page={"type": "elektro_room", "room_ids": ["ER-1"]}) as pdf:
        assert len(pdf) == 4
        plan_text = pdf[0].get_text()
        assert "Duplicate (EK-1)" in plan_text
        assert "Duplicate (EK-2)" in plan_text
        assert "Shared (AP-1)" in plan_text
        assert "Shared (AP-2)" in plan_text
        assert "AP-3" not in plan_text
        assert "EK-3" not in plan_text
        leaders = [drawing for drawing in pdf[0].get_drawings()
               if drawing["fill_opacity"] == pytest.approx(130 / 255, abs=0.01)]
        assert len(leaders) >= 2
        assert all(len(drawing["items"]) > 1 for drawing in leaders)
        assert "Duplicate (EK-1)" in pdf[2].get_text()
        assert "Duplicate (EK-2)" in pdf[2].get_text()
        assert "Unterputz-Verteilung" in pdf[3].get_text()
        assert "Second mapping" not in pdf[3].get_text()
    assert window.canvas._label_visible == original_labels


def test_room_plan_skips_outside_or_disabled_cable_labels(window, tmp_path):
    window._set_document(_document())
    for cable in window._document.elements["elec_cables"].values():
        cable.name = "Duplicate"
    window.canvas._elec_cables["EK-2"] = [QPointF(900, 900), QPointF(1000, 900)]
    with _write_pdf(window, tmp_path / "room-clipped.pdf",
                    page={"type": "elektro_room", "room_ids": ["ER-1"]}) as pdf:
        assert "Duplicate (EK-1)" in pdf[0].get_text()
        assert "Duplicate (EK-2)" not in pdf[0].get_text()
        assert "Duplicate (EK-2)" in pdf[2].get_text()

    with _write_pdf(window, tmp_path / "room-hidden.pdf",
                    page={"type": "elektro_room", "room_ids": ["ER-1"],
                          "element_visibility": {"kv": False}}) as pdf:
        assert "Duplicate (EK-1)" not in pdf[0].get_text()
        assert "Duplicate (EK-1)" in pdf[2].get_text()


def test_room_label_colors_persist_alpha_values(window, monkeypatch):
    store = {}

    class SettingsStore:
        def value(self, key, default=None):
            return store.get(key, default)

        def setValue(self, key, value):
            store[key] = value

    monkeypatch.setattr(window, "_settings", SettingsStore)
    colors = {"background": "#70402010", "leader": "#60506070", "text": "#ff123456"}
    window._save_pdf_room_label_colors(colors)
    assert window._pdf_room_label_colors() == colors
    style = {"stroke_width": 2.8, "line_style": "dashdot"}
    window._save_pdf_room_label_style(style)
    assert window._pdf_room_label_style() == style
    ap_colors = {"background": "#7000ff00", "leader": "#60ff00ff", "text": "#ff001122"}
    ap_style = {"stroke_width": 3.4, "line_style": "dot"}
    window._save_pdf_ap_label_colors(ap_colors)
    window._save_pdf_ap_label_style(ap_style)
    assert window._pdf_ap_label_colors() == ap_colors
    assert window._pdf_ap_label_style() == ap_style


def _write_context_pdf(path, callback, resolution=150):
    writer = QPdfWriter(str(path))
    writer.setResolution(resolution)
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setPageOrientation(QPageLayout.Landscape)
    painter = QPainter(writer)
    assert painter.isActive()
    context = _PdfContext.__new__(_PdfContext)
    context.printer = writer
    context.painter = painter
    context.dpi = resolution
    context._dry_run = False
    context._pr = QRectF(writer.pageLayout().paintRectPixels(resolution))
    context._page_num = 1
    context._toc = []
    try:
        callback(context)
    finally:
        painter.end()
    fitz = pytest.importorskip("fitz")
    return fitz.open(path)


@pytest.mark.parametrize("renderer", ["active", "legacy"])
@pytest.mark.parametrize("resolution", [72, 150, 300])
def test_samples_survive_pagination_and_resolution(window, tmp_path, renderer, resolution):
    window._set_document(_styled_document())
    rows = window._collect_pdf_electro_rows()[1] * 30
    headers = ["Name", "Typ", "Start", "Ende", "Length", "Location"]
    path = tmp_path / f"{renderer}-{resolution}.pdf"
    if renderer == "active":
        pdf = _write_pdf(window, path, resolution=resolution,
                         table={"title": "Cable samples", "headers": headers, "rows": rows})
    else:
        pdf = _write_context_pdf(path, lambda context: context.draw_table(
            context.page_rect(), 0, headers, rows), resolution)
    with pdf:
        assert len(pdf) > 1
        _assert_samples(pdf, STYLES, resolution)
        for color, _, _ in STYLES:
            rgb = tuple(int(color[index:index + 2], 16) / 255 for index in (1, 3, 5))
            assert sum(drawing.get("color") == pytest.approx(rgb, abs=0.002)
                       for page in pdf for drawing in page.get_drawings()) == 30


@pytest.mark.parametrize("section,indices", [("el_kabel", [0, 1, 2, 3]),
                                             ("el_ap_connections", [0, 1, 2, 3]),
                                             ("el_rooms", [0, 1, 2, 3]),
                                             ("el_up_distribution", [0, 1, 2])])
def test_legacy_pdf_row_producers(window, tmp_path, section, indices):
    window._set_document(_styled_document())
    data = window._collect_export_data()
    legacy = _legacy_window(window._document)
    try:
        with _write_context_pdf(tmp_path / f"legacy-{section}.pdf", lambda context:
                                legacy._pdf_elektro_page(context, data,
                                                         table_sections=[section])) as pdf:
            _assert_samples(pdf, [STYLES[index] for index in indices])
    finally:
        legacy.deleteLater()


def test_legacy_compact_uv_assignment_table(window, tmp_path):
    window._set_document(_styled_document())
    uv = window._collect_export_data()["uv_data"][0]
    uv = MainWindow._pdf_uv_samples(window, uv)
    with _write_context_pdf(tmp_path / "compact-uv.pdf", lambda context:
                            context.draw_uv_schematic(context.page_rect(), 0, uv)) as pdf:
        _assert_samples(pdf, STYLES[:1])


def test_direct_uv_exports_include_samples_in_each_assignment_table(window, tmp_path):
    window._set_document(_styled_document())
    data = window._collect_export_data()
    with _write_pdf(window, tmp_path / "active-uv.pdf", page={"type": "uv", "uv_ap_id": "AP-1"}) as pdf:
        _assert_samples(pdf, STYLES[:1])
    legacy = _legacy_window(window._document)
    try:
        with _write_context_pdf(tmp_path / "legacy-uv.pdf", lambda context:
                                legacy._pdf_uv_page(context, data, uv_ap_id="AP-1")) as pdf:
            _assert_samples(pdf, STYLES[:1])
            rgb = tuple(int(STYLES[0][0][index:index + 2], 16) / 255 for index in (1, 3, 5))
            assert sum(drawing.get("color") == pytest.approx(rgb, abs=0.002)
                       for page in pdf for drawing in page.get_drawings()) == 2
    finally:
        legacy.deleteLater()


@pytest.mark.parametrize("renderer", ["active", "legacy"])
def test_sample_bands_do_not_overlap_cable_text(window, tmp_path, renderer):
    window._set_document(_styled_document())
    rows = [[window._pdf_cable_sample(f"EK-{index}", f"Sample{index}")] for index in range(1, 5)]
    path = tmp_path / f"bands-{renderer}.pdf"
    if renderer == "active":
        pdf = _write_pdf(window, path, table={"title": "Samples", "headers": ["Cable"], "rows": rows})
    else:
        pdf = _write_context_pdf(path, lambda context: context.draw_table(context.page_rect(), 0, ["Cable"], rows))
    with pdf:
        page = pdf[0]
        for index, (color, _, _) in enumerate(STYLES, 1):
            rgb = tuple(int(color[position:position + 2], 16) / 255 for position in (1, 3, 5))
            sample = next(drawing for drawing in page.get_drawings()
                          if drawing.get("color") == pytest.approx(rgb, abs=0.002))
            text = page.search_for(f"Sample{index}")
            assert len(text) == 1
            assert text[0].y1 <= sample["rect"].y0 - sample["width"] / 2