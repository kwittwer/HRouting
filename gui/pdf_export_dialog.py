import copy
import uuid

from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QAbstractItemView,
)


class SourceRectPreview(QWidget):
    rectChanged = Signal(object)  # QRectF

    def __init__(self, svg_w: float, svg_h: float, canvas=None, parent=None):
        super().__init__(parent)
        self._svg_w = max(1.0, float(svg_w))
        self._svg_h = max(1.0, float(svg_h))
        self._canvas = canvas
        self._snapshot: QPixmap | None = None
        self._rect: QRectF | None = None
        self._drag_start: QPointF | None = None
        self._drag_current: QPointF | None = None
        self._selection_enabled = True
        self.setMinimumHeight(210)

    def refresh_snapshot(self):
        self._snapshot = None
        if self._canvas and self._canvas.width() > 2 and self._canvas.height() > 2:
            pm = self._canvas.grab()
            if not pm.isNull():
                self._snapshot = pm
        self.update()

    def set_selection_enabled(self, enabled: bool):
        self._selection_enabled = bool(enabled)
        self.update()

    def set_source_rect(self, rect: QRectF | None):
        if rect is None:
            self._rect = None
        else:
            nr = QRectF(rect).normalized()
            if nr.width() <= 0 or nr.height() <= 0:
                self._rect = None
            else:
                self._rect = nr
        self.update()

    def _content_rect(self) -> QRectF:
        m = 10.0
        area = QRectF(m, m, max(1.0, self.width() - 2 * m), max(1.0, self.height() - 2 * m))
        src_aspect = self._svg_w / self._svg_h if self._svg_h > 0 else 1.0
        area_aspect = area.width() / area.height() if area.height() > 0 else 1.0
        if area_aspect > src_aspect:
            w = area.height() * src_aspect
            x = area.x() + (area.width() - w) / 2.0
            return QRectF(x, area.y(), w, area.height())
        h = area.width() / src_aspect
        y = area.y() + (area.height() - h) / 2.0
        return QRectF(area.x(), y, area.width(), h)

    def _widget_to_svg(self, p: QPointF) -> QPointF:
        cr = self._content_rect()
        if cr.width() <= 0 or cr.height() <= 0:
            return QPointF(0.0, 0.0)
        x = min(max(p.x(), cr.left()), cr.right())
        y = min(max(p.y(), cr.top()), cr.bottom())
        sx = (x - cr.left()) / cr.width() * self._svg_w
        sy = (y - cr.top()) / cr.height() * self._svg_h
        sx = min(max(sx, 0.0), self._svg_w)
        sy = min(max(sy, 0.0), self._svg_h)
        return QPointF(sx, sy)

    def _svg_to_widget_rect(self, rect: QRectF) -> QRectF:
        cr = self._content_rect()
        if cr.width() <= 0 or cr.height() <= 0 or self._svg_w <= 0 or self._svg_h <= 0:
            return QRectF()
        x = cr.left() + (rect.x() / self._svg_w) * cr.width()
        y = cr.top() + (rect.y() / self._svg_h) * cr.height()
        w = (rect.width() / self._svg_w) * cr.width()
        h = (rect.height() / self._svg_h) * cr.height()
        return QRectF(x, y, w, h)

    def mousePressEvent(self, event):
        if not self._selection_enabled or event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        p = QPointF(event.position())
        self._drag_start = self._widget_to_svg(p)
        self._drag_current = QPointF(self._drag_start)
        self.update()

    def mouseMoveEvent(self, event):
        if not self._selection_enabled or self._drag_start is None:
            return super().mouseMoveEvent(event)
        self._drag_current = self._widget_to_svg(QPointF(event.position()))
        self.update()

    def mouseReleaseEvent(self, event):
        if not self._selection_enabled or event.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(event)
        if self._drag_start is not None and self._drag_current is not None:
            rect = QRectF(self._drag_start, self._drag_current).normalized()
            if rect.width() >= 1.0 and rect.height() >= 1.0:
                self._rect = rect
                self.rectChanged.emit(QRectF(rect))
        self._drag_start = None
        self._drag_current = None
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.fillRect(self.rect(), QColor("#1f1f1f"))
        cr = self._content_rect()
        if self._snapshot and not self._snapshot.isNull():
            painter.drawPixmap(cr, self._snapshot, QRectF(self._snapshot.rect()))
        else:
            painter.fillRect(cr, QColor("#2d2d2d"))
        painter.setPen(QPen(QColor("#666666"), 1.0))
        painter.drawRect(cr)

        frame = self._rect
        if self._drag_start is not None and self._drag_current is not None:
            frame = QRectF(self._drag_start, self._drag_current).normalized()

        if frame and frame.width() > 0 and frame.height() > 0:
            wr = self._svg_to_widget_rect(frame)
            painter.setBrush(QBrush(QColor(0, 230, 118, 50)))
            painter.setPen(QPen(QColor("#00e676"), 2.0, Qt.DashLine))
            painter.drawRect(wr)

        hint = "Im Vorschaubereich ziehen, um den Export-Rahmen festzulegen"
        if not self._selection_enabled:
            hint = "Aktiviere 'Eigenen Export-Rahmen nutzen', um im Vorschaubereich zu ziehen"
        painter.setPen(QPen(QColor("#d0d0d0")))
        painter.drawText(QRectF(10, self.height() - 26, self.width() - 20, 18), Qt.AlignLeft, hint)
        if self._snapshot is None:
            painter.drawText(QRectF(10, 10, self.width() - 20, 18), Qt.AlignLeft, "Keine Planvorschau verfügbar")


class PdfExportConfigDialog(QDialog):
    def __init__(
        self,
        pages: list[dict],
        floor_plans: list[tuple[str, str]],
        svg_size: tuple[float, float],
        canvas=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("PDF-Export konfigurieren")
        self.resize(960, 620)

        self._pages = copy.deepcopy(pages)
        self._floor_plans = list(floor_plans)
        self._svg_w = float(svg_size[0] if svg_size else 0.0)
        self._svg_h = float(svg_size[1] if svg_size else 0.0)
        self._block_updates = False
        self._canvas = canvas

        self._build_ui()
        self._load_pages_into_tree()
        self._select_first_item()

    def _build_ui(self):
        root = QVBoxLayout(self)

        content = QHBoxLayout()
        root.addLayout(content, stretch=1)

        left = QVBoxLayout()
        content.addLayout(left, stretch=1)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Seite", "Typ"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.tree.setDefaultDropAction(Qt.MoveAction)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        left.addWidget(self.tree, stretch=1)

        left_btns = QHBoxLayout()
        self.btn_add_plan = QPushButton("Planseite einfügen")
        self.btn_add_plan.clicked.connect(self._on_add_plan_page)
        self.btn_remove = QPushButton("Seite entfernen")
        self.btn_remove.clicked.connect(self._on_remove_selected)
        left_btns.addWidget(self.btn_add_plan)
        left_btns.addWidget(self.btn_remove)
        left.addLayout(left_btns)

        right_wrap = QWidget()
        right = QVBoxLayout(right_wrap)
        content.addWidget(right_wrap, stretch=1)

        self.form = QFormLayout()
        right.addLayout(self.form)

        self.le_title = QLineEdit()
        self.le_title.textChanged.connect(self._on_title_changed)
        self.form.addRow("Überschrift", self.le_title)

        self.cb_enabled = QCheckBox("Seite exportieren")
        self.cb_enabled.toggled.connect(self._on_enabled_changed)
        self.form.addRow("Aktiv", self.cb_enabled)

        self.plan_group = QGroupBox("Plan-Einstellungen")
        right.addWidget(self.plan_group)

        plan_form = QFormLayout(self.plan_group)

        self.cb_floor_plan = QComboBox()
        self.cb_floor_plan.addItem("Alle Grundrisse", None)
        for fid, name in self._floor_plans:
            label = name.strip() or fid
            self.cb_floor_plan.addItem(label, fid)
        self.cb_floor_plan.currentIndexChanged.connect(self._on_floor_plan_changed)
        plan_form.addRow("Planquelle", self.cb_floor_plan)

        self.cb_show_background = QCheckBox("Grundrisse anzeigen")
        self.cb_show_background.toggled.connect(self._on_plan_options_changed)
        self.cb_show_heating = QCheckBox("Heizung anzeigen")
        self.cb_show_heating.toggled.connect(self._on_plan_options_changed)
        self.cb_show_elektro = QCheckBox("Elektro anzeigen")
        self.cb_show_elektro.toggled.connect(self._on_plan_options_changed)

        vis_wrap = QWidget()
        vis_layout = QVBoxLayout(vis_wrap)
        vis_layout.setContentsMargins(0, 0, 0, 0)
        vis_layout.addWidget(self.cb_show_background)
        vis_layout.addWidget(self.cb_show_heating)
        vis_layout.addWidget(self.cb_show_elektro)
        plan_form.addRow("Sichtbarkeit", vis_wrap)

        self.cb_custom_frame = QCheckBox("Eigenen Export-Rahmen nutzen")
        self.cb_custom_frame.toggled.connect(self._on_custom_frame_toggled)
        plan_form.addRow("Rahmen", self.cb_custom_frame)

        frame_grid = QGridLayout()
        self.sb_x = self._make_spinbox()
        self.sb_y = self._make_spinbox()
        self.sb_w = self._make_spinbox(minimum=0.1)
        self.sb_h = self._make_spinbox(minimum=0.1)

        for sb in (self.sb_x, self.sb_y, self.sb_w, self.sb_h):
            sb.valueChanged.connect(self._on_frame_changed)

        frame_grid.addWidget(QLabel("X"), 0, 0)
        frame_grid.addWidget(self.sb_x, 0, 1)
        frame_grid.addWidget(QLabel("Y"), 0, 2)
        frame_grid.addWidget(self.sb_y, 0, 3)
        frame_grid.addWidget(QLabel("Breite"), 1, 0)
        frame_grid.addWidget(self.sb_w, 1, 1)
        frame_grid.addWidget(QLabel("Höhe"), 1, 2)
        frame_grid.addWidget(self.sb_h, 1, 3)

        frame_widget = QWidget()
        frame_widget.setLayout(frame_grid)
        plan_form.addRow("Koordinaten", frame_widget)

        self.preview = SourceRectPreview(self._svg_w, self._svg_h, canvas=self._canvas)
        self.preview.rectChanged.connect(self._on_preview_rect_changed)
        plan_form.addRow("Vorschau", self.preview)

        self.btn_full_plan = QPushButton("Voller Plan")
        self.btn_full_plan.clicked.connect(self._set_full_plan_frame)
        frame_btns = QWidget()
        frame_btns_layout = QHBoxLayout(frame_btns)
        frame_btns_layout.setContentsMargins(0, 0, 0, 0)
        frame_btns_layout.addWidget(self.btn_full_plan)
        plan_form.addRow("", frame_btns)

        self.lbl_non_plan = QLabel(
            "Diese Seite enthält keinen frei konfigurierbaren Planbereich."
        )
        self.lbl_non_plan.setWordWrap(True)
        right.addWidget(self.lbl_non_plan)

        right.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _make_spinbox(minimum: float = -1_000_000.0) -> QDoubleSpinBox:
        sb = QDoubleSpinBox()
        sb.setDecimals(1)
        sb.setRange(minimum, 1_000_000.0)
        sb.setSingleStep(5.0)
        return sb

    def _page_type_label(self, page: dict) -> str:
        ptype = page.get("type", "plan")
        return {
            "plan": "Plan",
            "lengths": "Rohrlängen",
            "hydraulics": "Hydraulik",
            "elektro": "Elektro",
        }.get(ptype, ptype)

    def _load_pages_into_tree(self):
        self.tree.clear()
        for page in self._pages:
            item = QTreeWidgetItem(self.tree)
            item.setText(0, page.get("title", "Seite"))
            item.setText(1, self._page_type_label(page))
            item.setFlags(
                item.flags()
                | Qt.ItemIsUserCheckable
                | Qt.ItemIsEditable
                | Qt.ItemIsDragEnabled
                | Qt.ItemIsDropEnabled
                | Qt.ItemIsSelectable
                | Qt.ItemIsEnabled
            )
            enabled = bool(page.get("enabled", True))
            item.setCheckState(0, Qt.Checked if enabled else Qt.Unchecked)
            item.setData(0, Qt.UserRole, copy.deepcopy(page))

    def _select_first_item(self):
        if self.tree.topLevelItemCount() > 0:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def _current_item(self) -> QTreeWidgetItem | None:
        return self.tree.currentItem()

    def _current_page(self) -> dict | None:
        item = self._current_item()
        if not item:
            return None
        page = item.data(0, Qt.UserRole)
        return copy.deepcopy(page) if isinstance(page, dict) else None

    def _store_current_page(self, page: dict):
        item = self._current_item()
        if not item:
            return
        page = copy.deepcopy(page)
        item.setData(0, Qt.UserRole, page)
        item.setText(0, page.get("title", "Seite"))
        item.setText(1, self._page_type_label(page))
        item.setCheckState(0, Qt.Checked if page.get("enabled", True) else Qt.Unchecked)

    def _on_tree_item_changed(self, item: QTreeWidgetItem, column: int):
        if self._block_updates:
            return
        if column != 0:
            return
        page = item.data(0, Qt.UserRole)
        if not isinstance(page, dict):
            return
        page = copy.deepcopy(page)
        title = (item.text(0) or "").strip() or "Seite"
        page["title"] = title
        if item.text(0) != title:
            self._block_updates = True
            item.setText(0, title)
            self._block_updates = False
        page["enabled"] = item.checkState(0) == Qt.Checked
        item.setData(0, Qt.UserRole, page)
        if item is self._current_item():
            self._block_updates = True
            self.le_title.setText(title)
            self.cb_enabled.setChecked(page["enabled"])
            self._block_updates = False

    def _on_tree_selection_changed(self):
        self._load_editor_from_current()

    def _load_editor_from_current(self):
        page = self._current_page()
        has_item = page is not None
        self.preview.refresh_snapshot()

        self._block_updates = True
        try:
            if not has_item:
                self.le_title.clear()
                self.cb_enabled.setChecked(False)
                self.plan_group.setVisible(False)
                self.lbl_non_plan.setVisible(True)
                self.btn_remove.setEnabled(False)
                return

            self.btn_remove.setEnabled(True)
            self.le_title.setText(page.get("title", ""))
            self.cb_enabled.setChecked(bool(page.get("enabled", True)))

            ptype = page.get("type", "plan")
            is_plan_like = ptype in ("plan", "elektro")
            self.plan_group.setVisible(is_plan_like)
            self.lbl_non_plan.setVisible(not is_plan_like)

            if is_plan_like:
                floor_plan_id = page.get("floor_plan_id")
                idx = self.cb_floor_plan.findData(floor_plan_id)
                self.cb_floor_plan.setCurrentIndex(max(0, idx))

                self.cb_show_background.setChecked(bool(page.get("show_background", True)))
                self.cb_show_heating.setChecked(bool(page.get("show_heating", True)))
                self.cb_show_elektro.setChecked(bool(page.get("show_elektro", True)))

                rect = page.get("source_rect")
                has_custom = bool(rect and len(rect) == 4)
                self.cb_custom_frame.setChecked(has_custom)

                if has_custom:
                    self.sb_x.setValue(float(rect[0]))
                    self.sb_y.setValue(float(rect[1]))
                    self.sb_w.setValue(max(0.1, float(rect[2])))
                    self.sb_h.setValue(max(0.1, float(rect[3])))
                    self.preview.set_source_rect(
                        QRectF(
                            float(rect[0]),
                            float(rect[1]),
                            max(0.1, float(rect[2])),
                            max(0.1, float(rect[3])),
                        )
                    )
                else:
                    self.sb_x.setValue(0.0)
                    self.sb_y.setValue(0.0)
                    self.sb_w.setValue(max(1.0, self._svg_w))
                    self.sb_h.setValue(max(1.0, self._svg_h))
                    self.preview.set_source_rect(None)

                self._set_frame_controls_enabled(has_custom)
        finally:
            self._block_updates = False

    def _set_frame_controls_enabled(self, enabled: bool):
        self.sb_x.setEnabled(enabled)
        self.sb_y.setEnabled(enabled)
        self.sb_w.setEnabled(enabled)
        self.sb_h.setEnabled(enabled)
        self.preview.set_selection_enabled(enabled)

    def _update_current_page(self, updater):
        if self._block_updates:
            return
        page = self._current_page()
        if not page:
            return
        updater(page)
        self._store_current_page(page)

    def _on_title_changed(self, text: str):
        self._update_current_page(lambda p: p.__setitem__("title", text.strip() or "Seite"))

    def _on_enabled_changed(self, checked: bool):
        self._update_current_page(lambda p: p.__setitem__("enabled", bool(checked)))

    def _on_floor_plan_changed(self, _index: int):
        self._update_current_page(
            lambda p: p.__setitem__("floor_plan_id", self.cb_floor_plan.currentData())
        )

    def _on_plan_options_changed(self):
        def updater(p: dict):
            p["show_background"] = bool(self.cb_show_background.isChecked())
            p["show_heating"] = bool(self.cb_show_heating.isChecked())
            p["show_elektro"] = bool(self.cb_show_elektro.isChecked())

        self._update_current_page(updater)

    def _on_custom_frame_toggled(self, checked: bool):
        self._set_frame_controls_enabled(bool(checked))

        def updater(p: dict):
            if checked:
                p["source_rect"] = [
                    float(self.sb_x.value()),
                    float(self.sb_y.value()),
                    float(self.sb_w.value()),
                    float(self.sb_h.value()),
                ]
                self.preview.set_source_rect(
                    QRectF(
                        float(self.sb_x.value()),
                        float(self.sb_y.value()),
                        float(self.sb_w.value()),
                        float(self.sb_h.value()),
                    ).normalized()
                )
            else:
                p["source_rect"] = None
                self.preview.set_source_rect(None)

        self._update_current_page(updater)

    def _on_frame_changed(self):
        if not self.cb_custom_frame.isChecked():
            return

        def updater(p: dict):
            p["source_rect"] = [
                float(self.sb_x.value()),
                float(self.sb_y.value()),
                float(self.sb_w.value()),
                float(self.sb_h.value()),
            ]

        self._update_current_page(updater)

        self.preview.set_source_rect(
            QRectF(
                float(self.sb_x.value()),
                float(self.sb_y.value()),
                float(self.sb_w.value()),
                float(self.sb_h.value()),
            ).normalized()
        )

    def _on_preview_rect_changed(self, rect):
        self._block_updates = True
        try:
            self.cb_custom_frame.setChecked(True)
            self.sb_x.setValue(float(rect.x()))
            self.sb_y.setValue(float(rect.y()))
            self.sb_w.setValue(max(0.1, float(rect.width())))
            self.sb_h.setValue(max(0.1, float(rect.height())))
        finally:
            self._block_updates = False
        self._set_frame_controls_enabled(True)
        self._on_frame_changed()

    def _set_full_plan_frame(self):
        self._block_updates = True
        try:
            self.cb_custom_frame.setChecked(True)
            self.sb_x.setValue(0.0)
            self.sb_y.setValue(0.0)
            self.sb_w.setValue(max(1.0, self._svg_w))
            self.sb_h.setValue(max(1.0, self._svg_h))
        finally:
            self._block_updates = False
        self._set_frame_controls_enabled(True)
        self._on_frame_changed()

    def _on_add_plan_page(self):
        page = {
            "id": f"plan-{uuid.uuid4().hex[:8]}",
            "type": "plan",
            "title": "Neue Planseite",
            "enabled": True,
            "show_background": True,
            "show_heating": True,
            "show_elektro": True,
            "floor_plan_id": None,
            "source_rect": None,
        }
        item = QTreeWidgetItem(self.tree)
        item.setText(0, page["title"])
        item.setText(1, self._page_type_label(page))
        item.setFlags(
            item.flags()
            | Qt.ItemIsUserCheckable
            | Qt.ItemIsEditable
            | Qt.ItemIsDragEnabled
            | Qt.ItemIsDropEnabled
            | Qt.ItemIsSelectable
            | Qt.ItemIsEnabled
        )
        item.setCheckState(0, Qt.Checked)
        item.setData(0, Qt.UserRole, page)
        self.tree.setCurrentItem(item)

    def _on_remove_selected(self):
        item = self._current_item()
        if not item:
            return
        idx = self.tree.indexOfTopLevelItem(item)
        self.tree.takeTopLevelItem(idx)
        if self.tree.topLevelItemCount() > 0:
            self.tree.setCurrentItem(self.tree.topLevelItem(max(0, idx - 1)))
        else:
            self._load_editor_from_current()

    def get_pages(self) -> list[dict]:
        pages: list[dict] = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            page = item.data(0, Qt.UserRole)
            if not isinstance(page, dict):
                continue
            p = copy.deepcopy(page)
            p["enabled"] = item.checkState(0) == Qt.Checked
            p["title"] = (p.get("title", "") or "Seite").strip()
            if not p["title"]:
                p["title"] = "Seite"
            pages.append(p)
        return pages
