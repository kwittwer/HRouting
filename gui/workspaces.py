"""Workspace-Definitionen für die Tabs des Hauptfensters.

Ein Workspace bündelt:

* die im Tab angebotenen Werkzeuge,
* die Layer, deren Elemente selektierbar sind (Sichtbarkeit bleibt unberührt),
* die standardmäßig eingeblendeten Docks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from model.layers import LayerId

from .tool_registry import ToolSpec, tools_for_ids, tools_for_layer


class DockId:
    NAVIGATOR = "navigator"
    PROPERTIES = "properties"
    TOOLS = "tools"
    RESULTS = "results"
    SCHEMA = "schema"
    SCHALTPLAN = "schaltplan"
    LOG = "log"
    OVERVIEW_GENERAL = "overview_general"
    OVERVIEW_HEATING = "overview_heating"
    OVERVIEW_ELECTRO = "overview_electro"
    # Backward compatibility alias for old single overview dock id.
    OVERVIEW = OVERVIEW_HEATING


#: Docks, die in jedem Workspace verfügbar sind
BASE_DOCKS: tuple[str, ...] = (DockId.NAVIGATOR, DockId.PROPERTIES, DockId.TOOLS)


@dataclass(frozen=True)
class WorkspaceDefinition:
    id: str
    label: str
    layer: LayerId
    #: zusätzlich selektierbare Layer (der eigene Layer ist immer dabei)
    extra_selectable: tuple[LayerId, ...] = ()
    tool_ids: tuple[str, ...] = ()
    default_docks: tuple[str, ...] = BASE_DOCKS
    icon: str = ""

    @property
    def selectable_layers(self) -> set[LayerId]:
        return {self.layer, *self.extra_selectable}

    @property
    def tools(self) -> list[ToolSpec]:
        if self.tool_ids:
            return tools_for_ids(self.tool_ids)
        return tools_for_layer(self.layer)


WORKSPACES: tuple[WorkspaceDefinition, ...] = (
    WorkspaceDefinition(
        id="floorplan",
        label="Grundriss",
        layer=LayerId.FLOORPLAN,
    ),
    WorkspaceDefinition(
        id="heating",
        label="Heizung",
        layer=LayerId.HEATING,
        default_docks=(
            BASE_DOCKS
            + (
                DockId.RESULTS,
                DockId.OVERVIEW_GENERAL,
                DockId.OVERVIEW_HEATING,
                DockId.OVERVIEW_ELECTRO,
            )
        ),
    ),
    WorkspaceDefinition(
        id="electrical",
        label="Elektro",
        layer=LayerId.ELECTRICAL,
        default_docks=BASE_DOCKS + (DockId.SCHEMA,),
    ),
    WorkspaceDefinition(
        id="furniture",
        label="Einrichtung",
        layer=LayerId.FURNITURE,
    ),
    WorkspaceDefinition(
        id="annotation",
        label="Vermessung",
        layer=LayerId.ANNOTATION,
    ),
    WorkspaceDefinition(
        id="export",
        label="Export & Layout",
        layer=LayerId.EXPORT,
        extra_selectable=(LayerId.ANNOTATION,),
        default_docks=BASE_DOCKS,
    ),
)

WORKSPACES_BY_ID: dict[str, WorkspaceDefinition] = {w.id: w for w in WORKSPACES}

DEFAULT_WORKSPACE_ID = WORKSPACES[0].id


def workspace(workspace_id: str) -> WorkspaceDefinition:
    return WORKSPACES_BY_ID.get(workspace_id, WORKSPACES[0])
