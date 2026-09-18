"""Deterministic choice refresh regressions, without timing thresholds."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtTest import QSignalSpy  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.properties.field_widgets import ChoiceFieldWidget  # noqa: E402
from model.schema import FieldKind, FieldSpec  # noqa: E402


OPTIONS = ("", ("AP-1", "Socket (AP-1)"), ("AP-2", "Light (AP-2)"))


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture()
def make_choice(app):
    widgets = []

    def create(options=OPTIONS, *, editable=False):
        kind = FieldKind.EDITABLE_CHOICE if editable else FieldKind.CHOICE
        widget = ChoiceFieldWidget(
            FieldSpec("start_ap", "Start-AP", kind),
            editable=editable,
            options=options,
        )
        widgets.append(widget)
        return widget

    yield create
    for widget in widgets:
        widget.deleteLater()


def _entries(widget):
    combo = widget._combo
    return tuple((combo.itemData(i), combo.itemText(i)) for i in range(combo.count()))


def _watch(widget):
    model = widget._combo.model()
    return (
        QSignalSpy(model.rowsInserted),
        QSignalSpy(model.rowsRemoved),
        QSignalSpy(widget.value_changed),
    )


@pytest.mark.parametrize("editable", [False, True])
@pytest.mark.parametrize("options", [(), ("", "a", "b"), OPTIONS])
def test_unchanged_entries_do_not_rebuild(make_choice, editable, options):
    widget = make_choice(options, editable=editable)
    if options:
        widget.set_value(widget._combo.itemData(widget._combo.count() - 1))
    entries = _entries(widget)
    current = widget.value()
    index = widget._combo.currentIndex()
    inserted, removed, changed = _watch(widget)

    for _ in range(3):
        # Fresh tuples, and strings represented as equivalent (value, label) pairs.
        widget.set_options(tuple((value, label) for value, label in entries))

    assert _entries(widget) == entries
    assert widget.value() == current
    assert widget._combo.currentIndex() == index
    assert inserted.count() == removed.count() == changed.count() == 0


def test_two_146_entry_endpoint_lists_refresh_without_model_changes(make_choice):
    options = ("",) + tuple(
        (f"AP-{i}", f"Endpoint {i} (AP-{i})") for i in range(1, 146)
    )
    widgets = [make_choice(options), make_choice(options)]
    for widget, selected in zip(widgets, ("AP-1", "AP-145")):
        widget.set_value(selected)
    watchers = [_watch(widget) for widget in widgets]

    for _ in range(3):
        fresh_options = ("",) + tuple(
            (f"AP-{i}", f"Endpoint {i} (AP-{i})") for i in range(1, 146)
        )
        for widget in widgets:
            widget.set_options(fresh_options)

    assert [widget.value() for widget in widgets] == ["AP-1", "AP-145"]
    assert [widget._combo.count() for widget in widgets] == [146, 146]
    for inserted, removed, changed in watchers:
        assert inserted.count() == removed.count() == changed.count() == 0


@pytest.mark.parametrize(
    ("options", "value", "label"),
    [
        (("", OPTIONS[1], ("AP-2", "Renamed light")), "AP-2", "Renamed light"),
        (OPTIONS + (("AP-3", "New endpoint"),), "AP-2", "Light (AP-2)"),
        (OPTIONS[:2], "", ""),
        ((OPTIONS[2], OPTIONS[0], OPTIONS[1]), "AP-2", "Light (AP-2)"),
        (("", OPTIONS[1], ("AP-3", "Light (AP-2)")), "", ""),
        ((), "", ""),
    ],
    ids=["rename", "addition", "removal", "reorder", "stored-value-change", "empty"],
)
def test_changed_lists_rebuild_silently_and_preserve_valid_selection(
    make_choice, options, value, label
):
    widget = make_choice()
    widget.set_value("AP-2")
    inserted, removed, changed = _watch(widget)

    widget.set_options(options)

    expected = tuple(
        option if isinstance(option, tuple) else (option, option)
        for option in options
    )
    assert _entries(widget) == expected
    assert widget.value() == value
    assert widget._combo.currentText() == label
    assert removed.count() > 0
    assert inserted.count() == len(options)
    assert changed.count() == 0
    assert widget._updating is False

    inserted, removed, changed = _watch(widget)
    widget.set_options(options)
    assert inserted.count() == removed.count() == changed.count() == 0


@pytest.mark.parametrize("text", ["Unlisted draft endpoint", "Light (AP-2)", ""])
@pytest.mark.parametrize("changed_options", [False, True])
def test_editable_text_survives_silent_refresh(make_choice, text, changed_options):
    widget = make_choice(editable=True)
    widget._combo.setEditText(text)
    inserted, removed, changed = _watch(widget)
    options = OPTIONS + (("AP-3", "New endpoint"),) if changed_options else OPTIONS

    widget.set_options(options)

    assert widget.value() == text
    assert widget._combo.currentText() == text
    assert changed.count() == 0
    if changed_options:
        assert inserted.count() == len(options)
        assert removed.count() > 0
    else:
        assert inserted.count() == removed.count() == 0


@pytest.mark.parametrize("selected", ["AP-deleted", "AP-2"])
def test_source_refresh_removes_inserted_stale_choices(make_choice, selected):
    widget = make_choice()
    widget.set_value("AP-deleted")
    assert _entries(widget)[-1] == ("AP-deleted", "AP-deleted")
    widget.set_value(selected)
    inserted, removed, changed = _watch(widget)

    widget.set_options(OPTIONS)

    assert _entries(widget) == (("", ""), OPTIONS[1], OPTIONS[2])
    assert widget.value() == ("AP-2" if selected == "AP-2" else "")
    assert inserted.count() == len(OPTIONS)
    assert removed.count() > 0
    assert changed.count() == 0


def test_inserted_choice_matching_requested_entries_needs_no_rebuild(make_choice):
    widget = make_choice()
    widget.set_value("AP-legacy")
    inserted, removed, changed = _watch(widget)

    widget.set_options(OPTIONS + ("AP-legacy",))

    assert widget.value() == "AP-legacy"
    assert inserted.count() == removed.count() == changed.count() == 0


@pytest.mark.parametrize("part", ["label", "value"])
def test_actual_entries_are_checked_not_cached_source_options(make_choice, part):
    widget = make_choice()
    widget.set_value("AP-2")
    if part == "label":
        widget._combo.setItemText(1, "Outdated label")
    else:
        widget._combo.setItemData(1, "AP-outdated")
    inserted, removed, changed = _watch(widget)

    widget.set_options(OPTIONS)

    assert _entries(widget) == (("", ""), OPTIONS[1], OPTIONS[2])
    assert widget.value() == "AP-2"
    assert inserted.count() == len(OPTIONS)
    assert removed.count() > 0
    assert changed.count() == 0


@pytest.mark.parametrize("updating", [False, True])
@pytest.mark.parametrize("changed_options", [False, True])
def test_refresh_restores_previous_updating_flag(make_choice, updating, changed_options):
    widget = make_choice()
    widget._updating = updating
    changed = QSignalSpy(widget.value_changed)
    options = OPTIONS + (("AP-3", "New endpoint"),) if changed_options else OPTIONS

    widget.set_options(options)

    assert widget._updating is updating
    assert changed.count() == 0
    widget.set_value("AP-2")
    assert changed.count() == (0 if updating else 1)


@pytest.mark.parametrize("updating", [False, True])
def test_refresh_restores_previous_updating_flag_on_error(make_choice, monkeypatch, updating):
    widget = make_choice()
    widget._updating = updating

    def fail(options):
        assert widget._updating is True
        raise RuntimeError("options update failed")

    monkeypatch.setattr(widget, "_set_combo_options", fail)
    with pytest.raises(RuntimeError, match="options update failed"):
        widget.set_options(OPTIONS + (("AP-3", "New endpoint"),))

    assert widget._updating is updating