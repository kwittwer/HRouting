import copy
import uuid

from PySide6.QtCore import Qt, QRectF
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
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
        self._connect_canvas_signals()
        self._load_pages_into_tree()
        self._select_first_item()

    def _connect_canvas_signals(self):
        if not self._canvas:
            return
        sig = getattr(self._canvas, "export_frame_drawn", None)
        if sig is not None:
            sig.connect(self._on_canvas_export_frame_drawn)

    def _canvas_export_frame(self) -> QRectF | None:
        if not self._canvas:
            return None
        getter = getattr(self._canvas, "get_export_frame", None)
        if getter is None:
            return None
        rect = getter()
        if rect is None:
            return None
        nr = QRectF(rect).normalized()
        if nr.width() <= 0 or nr.height() <= 0:
            return None
        return nr

    def _apply_rect_to_controls(self, rect: QRectF):
        nr = QRectF(rect).normalized()
        self._block_updates = True
        try:
            self.cb_custom_frame.setChecked(True)
            self.sb_x.setValue(float(nr.x()))
            self.sb_y.setValue(float(nr.y()))
            self.sb_w.setValue(max(0.1, float(nr.width())))
            self.sb_h.setValue(max(0.1, float(nr.height())))
        finally:
            self._block_updates = False
        self._set_frame_controls_enabled(True)
        self._on_frame_changed()

    def _on_canvas_export_frame_drawn(self, rect):
        if rect is None:
            return
        nr = QRectF(rect).normalized()
        if nr.width() <= 0 or nr.height() <= 0:
            return
        self._apply_rect_to_controls(nr)

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

        self.preview_hint = QLabel("Export-Rahmen wird aus dem Plan übernommen.")
        self.preview_hint.setWordWrap(True)
        plan_form.addRow("Export-Rahmen", self.preview_hint)

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

                # Use canvas export frame
                canvas_rect = self._canvas_export_frame()
                if canvas_rect is not None:
                    rect = [
                        float(canvas_rect.x()),
                        float(canvas_rect.y()),
                        float(canvas_rect.width()),
                        float(canvas_rect.height()),
                    ]
                    page["source_rect"] = rect
                    self._store_current_page(page)
                else:
                    page["source_rect"] = None
                    self._store_current_page(page)
        finally:
            self._block_updates = False


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
