"""Andockbare Fenster der neuen HRouting-Oberfläche."""

from .navigator_dock import NavigatorDock
from .tools_dock import ToolsDock
from .properties_dock import PropertiesDock
from .log_dock import LogDock

from .overview_dock import ProjectOverviewDock

__all__ = ["NavigatorDock", "ToolsDock", "PropertiesDock", "LogDock", "ProjectOverviewDock"]
