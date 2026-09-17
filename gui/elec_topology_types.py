from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ApNode:
    point_id: str
    name: str
    room: str
    ap_type: str
    has_distributor_function: bool
    is_connected: bool
    color: str
    icon_path: str
    builtin_symbol: str
    width_px: float
    height_px: float
    room_id: str = ""
    width_mm: float = 30.0
    height_mm: float = 30.0
    visible: bool = True
    label_visible: bool = True
    label_size: float = 12.0
    position: str = "Wand"
    height_from_floor: float = 0.0
    smarthome_device: str = ""
    smarthome_device_color: str = ""
    note: str = ""
    uv_config: dict | None = None
    up_distribution_config: dict | None = None
    hak_config: dict | None = None
    zaehler_config: dict | None = None


@dataclass
class CableEdge:
    cable_id: str
    name: str
    cable_type: str
    length_m: float
    color: str
    stroke_width_px: float
    line_style: str = "solid"
    start_ap_id: str = ""
    end_ap_id: str = ""
    visible: bool = True
    label_visible: bool = True
    type_label_visible: bool = False
    label_size: float = 12.0
    comment: str = ""