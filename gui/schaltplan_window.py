"""Schaltplan-Vorschaufenster für HRouting.

QMainWindow mit drei Tabs:
  1. UV-Innenschaltplan  – visuelle DIN-Rail-Darstellung
  2. Stromkreisplan      – Tabelle Slot → Kabel → Verbraucher
  3. Hierarchieübersicht – Baum HAK → Zähler → UV → sub-UV
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import (
    QColor, QFont, QPageLayout, QPainter, QPen, QBrush,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtPrintSupport import QPrinter

from logic.schaltplan_generator import (
    build_uv_hierarchy,
    get_uv_circuits,
    render_hierarchy_overview,
    render_stromkreisplan,
    render_uv_innenschaltplan,
    SUPPLY_TYPES,
)


class _ZoomView(QGraphicsView):
    """QGraphicsView mit Mausrad-Zoom und Mittelklick-Pan."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._panning = False
        self._pan_start = None
        self._hbar_start = 0
        self._vbar_start = 0
        self._zoom_min = 0.05
        self._zoom_max = 20.0
        self._zoom_step = 1.18

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        factor = self._zoom_step if delta > 0 else 1.0 / self._zoom_step
        cur = float(self.transform().m11())
        target = cur * factor
        if target < self._zoom_min:
            factor = self._zoom_min / cur if cur > 0 else 1.0
        elif target > self._zoom_max:
            factor = self._zoom_max / cur if cur > 0 else 1.0
        self.scale(factor, factor)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = event.position().toPoint()
            self._hbar_start = self.horizontalScrollBar().value()
            self._vbar_start = self.verticalScrollBar().value()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning and self._pan_start is not None:
            delta = event.position().toPoint() - self._pan_start
            self.horizontalScrollBar().setValue(self._hbar_start - delta.x())
            self.verticalScrollBar().setValue(self._vbar_start - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self._pan_start = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SchaltplanWindow(QMainWindow):
    """Vorschaufenster für Schaltpläne."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Schaltplan")
        self.resize(1100, 720)

        self._ap_nodes: dict = {}
        self._cable_edges: dict = {}
        self._room_map: dict[str, str] = {}

        self._build_ui()

    # ── UI-Aufbau ──────────────────────────────────────────────────────── #

    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Toolbar
        tb = QToolBar()
        tb.setMovable(False)

        # UV-Auswahl (für Tab 1 + 2)
        self._lbl_uv = QLabel("  UV:")
        self._cmb_uv = QComboBox()
        self._cmb_uv.setMinimumWidth(200)
        self._cmb_uv.currentIndexChanged.connect(self._on_uv_changed)
        tb.addWidget(self._lbl_uv)
        tb.addWidget(self._cmb_uv)
        tb.addSeparator()

        # Zoom
        self._btn_zoom_out = QPushButton("－")
        self._btn_zoom_out.setFixedWidth(28)
        self._btn_zoom_in = QPushButton("＋")
        self._btn_zoom_in.setFixedWidth(28)
        self._btn_zoom_reset = QPushButton("100%")
        self._btn_fit = QPushButton("Einpassen")
        self._lbl_zoom = QLabel("100%")
        self._lbl_zoom.setMinimumWidth(44)
        self._lbl_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for w in (self._btn_zoom_out, self._btn_zoom_in,
                  self._btn_zoom_reset, self._lbl_zoom, self._btn_fit):
            tb.addWidget(w)
        tb.addSeparator()

        # Export
        self._btn_export = QPushButton("📄 Als PDF…")
        tb.addWidget(self._btn_export)

        self.addToolBar(tb)

        # Tabs
        self._tabs = QTabWidget()
        root.addWidget(self._tabs)

        self._scene_uv = QGraphicsScene(self)
        self._view_uv = _ZoomView()
        self._view_uv.setScene(self._scene_uv)
        self._tabs.addTab(self._view_uv, "📋 UV-Innenschaltplan")

        self._scene_circ = QGraphicsScene(self)
        self._view_circ = _ZoomView()
        self._view_circ.setScene(self._scene_circ)
        self._tabs.addTab(self._view_circ, "⚡ Stromkreisplan")

        self._scene_hier = QGraphicsScene(self)
        self._view_hier = _ZoomView()
        self._view_hier.setScene(self._scene_hier)
        self._tabs.addTab(self._view_hier, "🌐 Hierarchieübersicht")
        self._tabs.currentChanged.connect(self._on_tab_changed)

        self.setCentralWidget(central)

        # Signale
        self._btn_zoom_in.clicked.connect(self._zoom_in)
        self._btn_zoom_out.clicked.connect(self._zoom_out)
        self._btn_zoom_reset.clicked.connect(self._zoom_reset)
        self._btn_fit.clicked.connect(self._fit)
        self._btn_export.clicked.connect(self._export_pdf)
        self._update_zoom_label()

    # ── Daten übergeben ────────────────────────────────────────────────── #

    def set_data(
        self,
        ap_nodes: dict,
        cable_edges: dict,
        room_map: dict[str, str] | None = None,
    ) -> None:
        """Neue Projektdaten laden und alle Ansichten neu rendern."""
        self._ap_nodes = dict(ap_nodes or {})
        self._cable_edges = dict(cable_edges or {})
        self._room_map = dict(room_map or {})

        self._refresh_uv_combobox()
        self._render_hierarchy()
        self._render_current_tab()

    # ── UV-Combobox ────────────────────────────────────────────────────── #

    def _refresh_uv_combobox(self):
        prev = self._cmb_uv.currentData()
        self._cmb_uv.blockSignals(True)
        self._cmb_uv.clear()

        uv_items = [
            (pid, node)
            for pid, node in self._ap_nodes.items()
            if str(getattr(node, "ap_type", "") or "").strip() == "uv"
        ]
        uv_items.sort(key=lambda v: str(getattr(v[1], "name", v[0]) or v[0]).lower())

        if not uv_items:
            self._cmb_uv.addItem("(keine UV vorhanden)", None)
        else:
            for pid, node in uv_items:
                name = str(getattr(node, "name", pid) or pid)
                self._cmb_uv.addItem(f"{name} [{pid}]", pid)

        # Vorherige Auswahl wiederherstellen
        if prev:
            idx = self._cmb_uv.findData(prev)
            if idx >= 0:
                self._cmb_uv.setCurrentIndex(idx)

        self._cmb_uv.blockSignals(False)

    def _current_uv_id(self) -> str | None:
        data = self._cmb_uv.currentData()
        return str(data) if data else None

    # ── Render ────────────────────────────────────────────────────────── #

    def _render_current_tab(self):
        tab = self._tabs.currentIndex()
        if tab == 0:
            self._render_uv_innenschaltplan()
        elif tab == 1:
            self._render_stromkreisplan()
        # Tab 2 (Hierarchie) wird separat via _render_hierarchy() aktuell gehalten

    def _render_uv_innenschaltplan(self):
        uv_id = self._current_uv_id()
        self._scene_uv.clear()
        if uv_id is None:
            t = self._scene_uv.addSimpleText(
                "Keine UV ausgewählt.\nLege einen AP mit Typ 'Unterverteilung (UV)' an.")
            t.setBrush(QBrush(QColor("#888888")))
            return

        node = self._ap_nodes.get(uv_id)
        if node is None:
            return
        uv_cfg = dict(getattr(node, "uv_config", None) or {})
        ap_name = str(getattr(node, "name", uv_id) or uv_id)
        render_uv_innenschaltplan(self._scene_uv, uv_cfg, ap_name)

    def _render_stromkreisplan(self):
        uv_id = self._current_uv_id()
        self._scene_circ.clear()
        if uv_id is None:
            t = self._scene_circ.addSimpleText("Keine UV ausgewählt.")
            t.setBrush(QBrush(QColor("#888888")))
            return

        node = self._ap_nodes.get(uv_id)
        uv_name = str(getattr(node, "name", uv_id) or uv_id) if node else uv_id
        circuits = get_uv_circuits(
            uv_id, self._ap_nodes, self._cable_edges, self._room_map
        )
        render_stromkreisplan(self._scene_circ, uv_id, circuits, uv_name)

    def _render_hierarchy(self):
        hierarchy = build_uv_hierarchy(self._ap_nodes, self._cable_edges)
        render_hierarchy_overview(self._scene_hier, hierarchy, self._ap_nodes)

    # ── Zoom ──────────────────────────────────────────────────────────── #

    def _active_view(self) -> _ZoomView:
        views = [self._view_uv, self._view_circ, self._view_hier]
        return views[self._tabs.currentIndex()]

    def _zoom_in(self):
        v = self._active_view()
        v.scale(1.18, 1.18)
        self._update_zoom_label()

    def _zoom_out(self):
        v = self._active_view()
        v.scale(1 / 1.18, 1 / 1.18)
        self._update_zoom_label()

    def _zoom_reset(self):
        v = self._active_view()
        v.resetTransform()
        self._update_zoom_label()

    def _fit(self):
        v = self._active_view()
        rect = v.scene().itemsBoundingRect()
        if not rect.isNull() and rect.width() > 0 and rect.height() > 0:
            v.fitInView(rect.adjusted(-20, -20, 20, 20),
                        Qt.AspectRatioMode.KeepAspectRatio)
        self._update_zoom_label()

    def _update_zoom_label(self):
        v = self._active_view()
        pct = int(round(float(v.transform().m11()) * 100.0))
        self._lbl_zoom.setText(f"{pct}%")

    # ── Events ────────────────────────────────────────────────────────── #

    def _on_tab_changed(self, index: int):
        self._render_current_tab()
        self._update_zoom_label()

    def _on_uv_changed(self, _index: int):
        tab = self._tabs.currentIndex()
        if tab == 0:
            self._render_uv_innenschaltplan()
        elif tab == 1:
            self._render_stromkreisplan()

    # ── PDF-Export ─────────────────────────────────────────────────────── #

    def _export_pdf(self):
        tab = self._tabs.currentIndex()
        tab_name = ["UV-Innenschaltplan", "Stromkreisplan", "Hierarchieuebersicht"][tab]
        default_name = f"schaltplan_{tab_name}.pdf"

        path, _ = QFileDialog.getSaveFileName(
            self, "Schaltplan als PDF exportieren", default_name, "PDF (*.pdf)"
        )
        if not path:
            return

        printer = QPrinter(QPrinter.Resolution.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)
        printer.setPageOrientation(QPageLayout.Orientation.Landscape)
        printer.setPageMargins(
            QMarginsF(12, 10, 12, 10), QPageLayout.Unit.Millimeter
        )

        painter = QPainter()
        if not painter.begin(printer):
            QMessageBox.warning(self, "PDF-Fehler",
                                "PDF konnte nicht erstellt werden.")
            return

        scene = self._active_view().scene()
        src = scene.itemsBoundingRect().adjusted(-10, -10, 10, 10)
        if src.isNull() or src.width() <= 0 or src.height() <= 0:
            painter.end()
            QMessageBox.information(self, "PDF-Export", "Nichts zu exportieren.")
            return

        page = QRectF(printer.pageRect(QPrinter.Unit.DevicePixel))
        # Skalierung: Inhalt auf Seite einpassen
        scale = min(page.width() / src.width(), page.height() / src.height())
        x_off = (page.width() - src.width() * scale) / 2
        y_off = (page.height() - src.height() * scale) / 2
        painter.translate(x_off, y_off)
        painter.scale(scale, scale)

        scene.render(painter, QRectF(), src)
        painter.end()

        QMessageBox.information(self, "PDF-Export",
                                f"Schaltplan wurde exportiert:\n{path}")
