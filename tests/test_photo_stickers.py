"""Documentation sticker contracts and real, measurable QPdfWriter output."""

from copy import deepcopy
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from gui.pdf_ap_stickers import (
    documentation_sticker_pages, estimate_documentation_pages, prepare_documentation_stickers, sticker_footprint,
)
from model.document import Document


def _document():
    return Document.from_dict({
        "canvas": {"elec_points": {"AP-1": [10, 10], "AP-2": [200, 10]}},
        "params": {
            "elec_points": {
                "AP-1": {"point_id": "AP-1", "name": "Socket", "room_id": "ER-1",
                         "height_from_floor": 300, "position": "Wand"},
                "AP-2": {"point_id": "AP-2", "name": "Other", "room_id": "ER-2"},
            },
            "elec_rooms": {"ER-1": {"room_id": "ER-1", "name": "Kitchen"},
                           "ER-2": {"room_id": "ER-2", "name": "Hall"}},
        },
    })


def _resolve(point):
    return point.room_id


def test_prepare_uses_registered_markers_without_mutation():
    document = _document()
    document.settings["photo_documentation"] = {
        "dictionary": {"marker_size": 5},
        "markers": {
            "MARK-1": {"id": "MARK-1", "point_id": "AP-1", "aruco_id": 0, "side_mm": 80},
            "MARK-2": {"id": "MARK-2", "point_id": "AP-2", "aruco_id": 1, "side_mm": 80},
            "MARK-3": {"id": "MARK-3", "surface_id": "SURF-1", "aruco_id": 2, "side_mm": 120},
            "MARK-4": {"id": "MARK-4", "surface_id": "SURF-2", "aruco_id": 3, "side_mm": 120},
        },
        "surfaces": {"SURF-1": {"room_id": "ER-1", "status": "current", "name": "Wall 1"},
                     "SURF-2": {"room_id": "ER-1", "status": "stale"}},
    }
    before = deepcopy(document.to_dict())
    stickers = prepare_documentation_stickers(document, _resolve, "ER-1")
    assert [entry["marker_id"] for entry in stickers] == ["MARK-1", "MARK-3"]
    assert [entry["side_mm"] for entry in stickers] == [80, 120]
    assert stickers[0]["ap"].height_mm == 300
    assert stickers[0]["ap"].room == "Kitchen"
    assert len(prepare_documentation_stickers(document, _resolve, "ER-1", False)) == 1
    assert document.to_dict() == before
    assert stickers[0]["template"] is None and stickers[0]["anchor_offset_mm"] is None
    assert documentation_sticker_pages(stickers) == [[stickers[0], stickers[1]]]
    assert documentation_sticker_pages([]) == [[]]


def test_newest_registration_wins_regardless_of_key_sorted_registry():
    document = _document()
    template = {"template": {"diameter_mm": 68}, "anchor_offset_mm": [71, 0], "print_version": 2}
    markers = {
        "MARK-1": {"id": "MARK-1", "point_id": "AP-1", "aruco_id": 0, "side_mm": 80},
        "MARK-296": {"id": "MARK-296", "point_id": "AP-1", "aruco_id": 295, "side_mm": 56, **template},
        "MARK-76": {"id": "MARK-76", "point_id": "AP-2", "aruco_id": 75, "side_mm": 80},
        "MARK-413": {"id": "MARK-413", "point_id": "AP-2", "aruco_id": 412, "side_mm": 56, **template},
        "MARK-167": {"id": "MARK-167", "surface_id": "SURF-1", "aruco_id": 166, "side_mm": 120},
        "MARK-470": {"id": "MARK-470", "surface_id": "SURF-1", "aruco_id": 469, "side_mm": 100},
        "MARK-471": {"id": "MARK-471", "surface_id": "SURF-1", "aruco_id": 470, "side_mm": 100},
    }
    document.settings["photo_documentation"] = {
        "dictionary": {"marker_size": 5},
        "markers": dict(sorted(markers.items())),
        "surfaces": {"SURF-1": {"room_id": "ER-1", "status": "current", "name": "Wall 1"}},
    }
    stickers = prepare_documentation_stickers(document, _resolve)
    # APs sort by room name (Hall before Kitchen), references follow.
    assert [entry["marker_id"] for entry in stickers] == ["MARK-413", "MARK-296", "MARK-470", "MARK-471"]
    assert all(entry["template"] for entry in stickers[:2])


def test_default_sheet_packing_four_templates_or_two_references():
    from logic.photo_markers import template_anchor_offset_mm

    ap = {"marker_id": "AP", "side_mm": 56.0, "template": {"diameter_mm": 68.0},
          "anchor_offset_mm": template_anchor_offset_mm(56.0)}
    reference = {"marker_id": "REF", "side_mm": 100.0}
    assert sticker_footprint(ap) == pytest.approx((142.0, 95.0))
    assert sticker_footprint(reference)[0] == pytest.approx(136.57, abs=0.01)
    assert [len(page) for page in documentation_sticker_pages([ap] * 9)] == [4, 4, 1]
    assert [len(page) for page in documentation_sticker_pages([reference] * 3)] == [2, 1]
    assert estimate_documentation_pages(4, 0) == 1
    assert estimate_documentation_pages(5, 0) == 2
    assert estimate_documentation_pages(0, 0) == 1
    assert estimate_documentation_pages(13, 6) == 7
    with pytest.raises(ValueError, match="A4 quer"):
        documentation_sticker_pages([{"marker_id": "BIG", "side_mm": 250.0}])


def test_prepare_missing_marker_is_actionable():
    document = _document()
    with pytest.raises(ValueError, match="speichern"):
        prepare_documentation_stickers(document, _resolve)
    document.settings["photo_documentation"] = {"dictionary": {"marker_size": 5}, "markers": {}}
    with pytest.raises(ValueError, match=r"AP-\d+.*speichern"):
        prepare_documentation_stickers(document, _resolve)


def test_empty_room_filter_does_not_require_marker_generation():
    document = _document()
    before = deepcopy(document.to_dict())
    assert prepare_documentation_stickers(document, _resolve, "ER-999") == []
    assert document.to_dict() == before
    document.settings["photo_documentation"] = {"dictionary": {}, "markers": {}}
    assert prepare_documentation_stickers(document, _resolve, "ER-999") == []


@pytest.fixture(scope="module")
def app():
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    instance = QApplication.instance() or QApplication([])
    for name in ("arial.ttf", "arialbd.ttf"):
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if font.exists():
            assert QFontDatabase.addApplicationFont(str(font)) >= 0
    assert QFontDatabase.families()
    return instance


def test_dialog_documentation_controls_persist_and_count_pages(app):
    from gui.pdf_export_dialog import PdfExportConfigDialog

    dialog = PdfExportConfigDialog(
        pages=[], floor_plans=[], svg_size=(0, 0), elec_rooms=[("ER-1", "Kitchen")],
        sticker_room_counts={"": 13, "ER-1": 1}, sticker_reference_counts={"": 6, "ER-1": 2},
    )
    assert dialog.cb_documentation_mode.isHidden()
    dialog.btn_add_ap_stickers.click()
    try:
        assert not dialog.cb_documentation_mode.isChecked()
        assert dialog.cb_include_references.isChecked()
        assert not dialog.cb_include_references.isEnabled()
        assert not dialog.cb_documentation_mode.isHidden()
        assert dialog.le_page_count.text() == "3"
        dialog.cb_documentation_mode.setChecked(True)
        assert dialog.cb_include_references.isEnabled()
        assert dialog.get_pages()[0]["documentation_mode"] is True
        assert dialog.le_page_count.text() == str(estimate_documentation_pages(13, 6) + 1)
        dialog.cb_sticker_room.setCurrentIndex(dialog.cb_sticker_room.findData("ER-1"))
        assert dialog.le_page_count.text() == str(estimate_documentation_pages(1, 2) + 1)
        dialog.cb_include_references.setChecked(False)
        assert dialog.get_pages()[0]["include_references"] is False
        assert dialog.le_page_count.text() == "2"
        pages = dialog.get_pages()
        dialog.btn_add_plan.click()
        assert dialog.cb_documentation_mode.isHidden()
        assert dialog.cb_include_references.isHidden()
        restored = PdfExportConfigDialog(pages, [], (0, 0))
        try:
            assert restored.cb_documentation_mode.isChecked()
            assert not restored.cb_include_references.isChecked()
        finally:
            restored.deleteLater()
    finally:
        dialog.deleteLater()


@pytest.fixture(scope="module")
def registered():
    pytest.importorskip("cv2")
    from logic.photo_markers import add_reference_marker, ensure_markers
    from model.photo_documentation import default_config, ensure_surfaces

    document = _document()
    document.get("ER-1").polygon = [[0, 0], [100, 0], [100, 100], [0, 100]]
    config = ensure_surfaces(document, default_config())
    ensure_markers(document, config)
    add_reference_marker(config, "SURF-1")
    document.settings["photo_documentation"] = config
    return document


@pytest.mark.parametrize("landscape", [True, False])
def test_real_pdf_markers_dimensions_and_control_line(app, registered, tmp_path, landscape):
    fitz = pytest.importorskip("fitz")
    import numpy as np
    from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter
    from gui.pdf_ap_stickers import draw_documentation_sticker_sheet
    from logic.photo_markers import detect_markers

    config = registered.settings["photo_documentation"]
    before = deepcopy(registered.to_dict())
    stickers = prepare_documentation_stickers(registered, _resolve, "ER-1")
    assert [sticker["side_mm"] for sticker in stickers] == [56, 100]
    assert stickers[0]["template"] == {"diameter_mm": 68}
    assert stickers[1]["template"] is None
    path = tmp_path / f"documentation-{landscape}.pdf"
    writer = QPdfWriter(str(path))
    writer.setResolution(300)
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setPageOrientation(QPageLayout.Landscape if landscape else QPageLayout.Portrait)
    painter = QPainter(writer)
    assert painter.isActive()
    try:
        pages = documentation_sticker_pages(stickers)
        if not landscape:
            with pytest.raises(ValueError, match="A4 quer"):
                draw_documentation_sticker_sheet(painter, writer, pages[0], config)
            return
        assert len(pages) == 1
        draw_documentation_sticker_sheet(painter, writer, pages[0], config)
    finally:
        painter.end()
    assert registered.to_dict() == before
    with fitz.open(path) as pdf:
        assert pdf.page_count == 1
        page = pdf[0]
        raster = page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72), colorspace=fitz.csGRAY)
        image = np.frombuffer(raster.samples, dtype=np.uint8).reshape(raster.height, raster.width)
        detections = sorted(detect_markers(image, config), key=lambda item: item["marker_id"])
        assert [item["marker_id"] for item in detections] == [sticker["marker_id"] for sticker in stickers]
        text = "".join(page.get_text().split())
        assert all(value in text for value in ("100%", "100mm", "OBEN", "AP-1", "Socket", "Kitchen", "30cm",
                                                "Referenz", "Bohrmitte"))
        control_lines = [item for drawing in page.get_drawings() for item in drawing["items"]
                         if item[0] == "l" and abs(item[2].x - item[1].x - 100 * 72 / 25.4) < 0.1
                         and abs(item[2].y - item[1].y) < 0.1]
        assert len(control_lines) == 1
        circles = [drawing["rect"] for drawing in page.get_drawings()
                   if abs(drawing["rect"].width * 25.4 / 72 - 68) < 0.05 and abs(drawing["rect"].height - drawing["rect"].width) < 0.05]
        assert len(circles) == 1
        for detection, sticker in zip(detections, stickers):
            assert not detection["warnings"]
            corners = np.asarray(detection["corners"])
            side = np.linalg.norm(corners[1] - corners[0]) * 25.4 / 150
            assert side == pytest.approx(sticker["side_mm"], abs=0.4)
            marker_rect = fitz.Rect(corners[0, 0] * 72 / 150, corners[0, 1] * 72 / 150,
                                    corners[2, 0] * 72 / 150, corners[2, 1] * 72 / 150)
            assert all(not marker_rect.intersects(fitz.Rect(word[:4])) for word in page.get_text("words"))
            if sticker["template"]:
                centre = corners.mean(axis=0) * 25.4 / 150
                drill = np.array([(circles[0].x0 + circles[0].x1) / 2, (circles[0].y0 + circles[0].y1) / 2]) * 25.4 / 72
                assert drill - centre == pytest.approx(sticker["anchor_offset_mm"], abs=0.4)
        assert all(page.rect.contains(fitz.Rect(word[:4])) for word in page.get_text("words"))


@pytest.mark.parametrize("side_mm", [80, 120])
@pytest.mark.parametrize("landscape", [True, False])
def test_pdf_vector_geometry_and_long_text_without_cv(app, tmp_path, monkeypatch, side_mm, landscape):
    fitz = pytest.importorskip("fitz")
    np = pytest.importorskip("numpy")
    from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter
    from gui.pdf_ap_stickers import ApSticker, draw_documentation_sticker_sheet
    from logic import photo_markers

    modules = np.full((7, 7), 255, dtype=np.uint8)
    modules[0, :] = modules[-1, :] = modules[:, 0] = modules[:, -1] = 0
    modules[2:5, 2:5] = 0
    monkeypatch.setattr(photo_markers, "marker_image",
                        lambda config, marker_id, pixels: np.repeat(np.repeat(modules, 2, axis=0), 2, axis=1))
    ap = ApSticker("AP-1", "LongOutletName " * 24, "Kitchen", "Wand", 300, "", "",
                   tuple(f"EK-{number}: CableConnection{number} NYM-J 3x1,5" for number in range(1, 41)))
    sticker = {"ap": ap, "marker_id": "MARK-1", "side_mm": side_mm}
    config = {"dictionary": {"marker_size": 5},
              "markers": {"MARK-1": {"aruco_id": 0, "side_mm": side_mm}}}
    path = tmp_path / f"geometry-{side_mm}-{landscape}.pdf"
    writer = QPdfWriter(str(path))
    writer.setResolution(254)
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setPageOrientation(QPageLayout.Landscape if landscape else QPageLayout.Portrait)
    painter = QPainter(writer)
    try:
        if not landscape:
            with pytest.raises(ValueError, match="A4 quer"):
                draw_documentation_sticker_sheet(painter, writer, [sticker], config)
            return
        draw_documentation_sticker_sheet(painter, writer, [sticker], config)
    finally:
        painter.end()
    with fitz.open(path) as pdf:
        page = pdf[0]
        black_rectangles = [drawing["rect"] for drawing in page.get_drawings()
                            if drawing["fill"] == (0, 0, 0)]
        marker_rect = fitz.Rect(black_rectangles[0])
        for rectangle in black_rectangles[1:]:
            marker_rect.include_rect(rectangle)
        assert marker_rect.width * 25.4 / 72 == pytest.approx(side_mm, abs=0.01)
        assert marker_rect.height * 25.4 / 72 == pytest.approx(side_mm, abs=0.01)
        assert all(not marker_rect.intersects(fitz.Rect(word[:4])) for word in page.get_text("words"))
        assert all(page.rect.contains(fitz.Rect(word[:4])) for word in page.get_text("words"))
        compact = "".join(page.get_text().split())
        assert compact.count("LongOutletName") == 24
        assert all(f"CableConnection{number}" in compact for number in range(1, 41))
        raster = page.get_pixmap(colorspace=fitz.csGRAY)
        image = np.frombuffer(raster.samples, dtype=np.uint8).reshape(raster.height, raster.width)
        gap = (side_mm / 7 / 2) * 72 / 25.4
        left, top, right, bottom = map(round, (marker_rect.x0, marker_rect.y0, marker_rect.x1, marker_rect.y1))
        assert np.all(image[top:bottom, round(left - gap)] == 255)
        assert np.all(image[top:bottom, round(right + gap)] == 255)
        assert np.all(image[round(top - gap), left:right] == 255)
        assert np.all(image[round(bottom + gap), left:right] == 255)
        control_lines = [item for drawing in page.get_drawings() for item in drawing["items"]
                         if item[0] == "l" and abs(item[2].x - item[1].x - 100 * 72 / 25.4) < 0.1
                         and abs(item[2].y - item[1].y) < 0.1]
        assert len(control_lines) == 1