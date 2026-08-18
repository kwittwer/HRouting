"""Logische Layer (Gewerke) eines HRouting-Projekts.

Die Layer steuern in der neuen UI, welche Elemente in einem Workspace
selektierbar sind. Sichtbarkeit bleibt davon unberührt.
"""

from __future__ import annotations

from enum import Enum


class LayerId(str, Enum):
    FLOORPLAN = "floorplan"
    HEATING = "heating"
    ELECTRICAL = "electrical"
    FURNITURE = "furniture"
    ANNOTATION = "annotation"
    EXPORT = "export"

    @property
    def label(self) -> str:
        return _LAYER_LABELS[self]


_LAYER_LABELS: dict[LayerId, str] = {
    LayerId.FLOORPLAN: "Grundriss",
    LayerId.HEATING: "Heizung",
    LayerId.ELECTRICAL: "Elektro",
    LayerId.FURNITURE: "Einrichtung",
    LayerId.ANNOTATION: "Annotationen",
    LayerId.EXPORT: "Export & Layout",
}


#: ID-Präfix -> Layer
PREFIX_LAYERS: dict[str, LayerId] = {
    "grundriss": LayerId.FLOORPLAN,
    "einrichtung": LayerId.FURNITURE,
    "HK": LayerId.HEATING,
    "HKV": LayerId.HEATING,
    "HKVL": LayerId.HEATING,
    "AP": LayerId.ELECTRICAL,
    "ER": LayerId.ELECTRICAL,
    "EK": LayerId.ELECTRICAL,
    "TEXT": LayerId.ANNOTATION,
    "ANL": LayerId.ANNOTATION,
    "ANR": LayerId.ANNOTATION,
    "ANP": LayerId.ANNOTATION,
    "ANC": LayerId.ANNOTATION,
    "ANE": LayerId.ANNOTATION,
    "ANPG": LayerId.ANNOTATION,
    "MSRD": LayerId.ANNOTATION,
    "MSRA": LayerId.ANNOTATION,
}


def layer_of_prefix(prefix: str) -> LayerId | None:
    return PREFIX_LAYERS.get(prefix)


def layer_of_id(element_id: str) -> LayerId | None:
    """Leitet den Layer aus einer Element-ID wie ``HK-3`` oder ``AP-12`` ab."""
    if not element_id:
        return None
    prefix = element_id.rsplit("-", 1)[0] if "-" in element_id else element_id
    return PREFIX_LAYERS.get(prefix)
