import copy
import uuid

from PySide6.QtCore import Qt
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

        self.btn_full_plan = QPushButton("Voller Plan")
        self.btn_full_plan.clicked.connect(self._set_full_plan_frame)
        self.btn_draw_on_canvas = QPushButton("⬚ Auf Canvas zeichnen…")
        self.btn_draw_on_canvas.setToolTip(
            "Dialog ausblenden und Rahmen direkt auf dem Canvas aufziehen"
        )
        self.btn_draw_on_canvas.clicked.connect(self._on_draw_on_canvas)
        self.btn_draw_on_canvas.setEnabled(self._canvas is not None)
        frame_btns = QWidget()
        frame_btns_layout = QHBoxLayout(frame_btns)
        frame_btns_layout.setContentsMargins(0, 0, 0, 0)
        frame_btns_layout.addWidget(self.btn_full_plan)
        frame_btns_layout.addWidget(self.btn_draw_on_canvas)
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
                else:
                    self.sb_x.setValue(0.0)
                    self.sb_y.setValue(0.0)
                    self.sb_w.setValue(max(1.0, self._svg_w))
                    self.sb_h.setValue(max(1.0, self._svg_h))

                self._set_frame_controls_enabled(has_custom)
        finally:
            self._block_updates = False

    def _set_frame_controls_enabled(self, enabled: bool):
        self.sb_x.setEnabled(enabled)
        self.sb_y.setEnabled(enabled)
        self.sb_w.setEnabled(enabled)
        self.sb_h.setEnabled(enabled)

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
            else:
                p["source_rect"] = None

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

    def _on_draw_on_canvas(self):
        if not self._canvas:
            return
        self.cb_custom_frame.setChecked(True)
        self._set_frame_controls_enabled(True)
        self._canvas.export_frame_drawn.connect(self._on_canvas_frame_received)
        self.hide()
        # Raise main window and give canvas focus so mouse events work
        main_win = self._canvas.window()
        main_win.raise_()
        main_win.activateWindow()
        self._canvas.setFocus()
        self._canvas.start_draw_export_frame()
        # Connect abort handler AFTER start – so the mode_changed emitted
        # inside start_draw_export_frame() does not immediately re-open the dialog
        self._canvas.mode_changed.connect(self._on_canvas_draw_aborted)

    def _on_canvas_draw_aborted(self):
        """Called when canvas mode changes away from DRAW_EXPORT_FRAME (ESC / right-click)."""
        from gui.canvas_widget import ToolMode
        if self._canvas and self._canvas._mode != ToolMode.DRAW_EXPORT_FRAME:
            try:
                self._canvas.mode_changed.disconnect(self._on_canvas_draw_aborted)
            except RuntimeError:
                pass
            try:
                self._canvas.export_frame_drawn.disconnect(self._on_canvas_frame_received)
            except RuntimeError:
                pass
            if not self.isVisible():
                self.show()
                self.raise_()
                self.activateWindow()

    def _on_canvas_frame_received(self, rect):
        try:
            self._canvas.export_frame_drawn.disconnect(self._on_canvas_frame_received)
        except RuntimeError:
            pass
        try:
            self._canvas.mode_changed.disconnect(self._on_canvas_draw_aborted)
        except RuntimeError:
            pass
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
        self.show()
        self.raise_()
        self.activateWindow()

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
