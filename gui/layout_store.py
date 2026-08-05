"""Persistenz des Fenster- und Dock-Layouts (global in QSettings)."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtWidgets import QMainWindow

_ORG = "HRouting"
_APP = "HRouting"

_GEOMETRY_KEY = "layout/geometry"
_WINDOW_STATE_KEY = "layout/window_state"
_WORKSPACE_STATE_KEY = "layout/workspace/{workspace_id}"
_LAST_WORKSPACE_KEY = "layout/last_workspace"


def settings() -> QSettings:
    return QSettings(_ORG, _APP)


def save_geometry(window: QMainWindow) -> None:
    store = settings()
    store.setValue(_GEOMETRY_KEY, window.saveGeometry())
    store.setValue(_WINDOW_STATE_KEY, window.saveState())


def restore_geometry(window: QMainWindow) -> bool:
    store = settings()
    geometry = store.value(_GEOMETRY_KEY)
    state = store.value(_WINDOW_STATE_KEY)
    restored = False
    if isinstance(geometry, QByteArray) and not geometry.isEmpty():
        restored = window.restoreGeometry(geometry) or restored
    if isinstance(state, QByteArray) and not state.isEmpty():
        restored = window.restoreState(state) or restored
    return restored


def save_workspace_state(window: QMainWindow, workspace_id: str) -> None:
    settings().setValue(
        _WORKSPACE_STATE_KEY.format(workspace_id=workspace_id), window.saveState()
    )


def restore_workspace_state(window: QMainWindow, workspace_id: str) -> bool:
    state = settings().value(_WORKSPACE_STATE_KEY.format(workspace_id=workspace_id))
    if isinstance(state, QByteArray) and not state.isEmpty():
        return window.restoreState(state)
    return False


def save_last_workspace(workspace_id: str) -> None:
    settings().setValue(_LAST_WORKSPACE_KEY, workspace_id)


def last_workspace(default: str = "") -> str:
    return str(settings().value(_LAST_WORKSPACE_KEY, default) or default)


def reset_layout() -> None:
    store = settings()
    for key in store.allKeys():
        if key.startswith("layout/"):
            store.remove(key)
