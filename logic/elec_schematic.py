from __future__ import annotations

from typing import Any


def default_elec_schematic() -> dict[str, Any]:
    return {
        "version": 1,
        "devices": [],
        "nets": [
            {"net_id": "NET-L1", "name": "L1", "potential": "L1", "class": "power", "color": "#e53935"},
            {"net_id": "NET-L2", "name": "L2", "potential": "L2", "class": "power", "color": "#43a047"},
            {"net_id": "NET-L3", "name": "L3", "potential": "L3", "class": "power", "color": "#1e88e5"},
            {"net_id": "NET-N", "name": "N", "potential": "N", "class": "power", "color": "#1565c0"},
            {"net_id": "NET-PE", "name": "PE", "potential": "PE", "class": "power", "color": "#8bc34a"},
        ],
        "connections": [],
        "pages": [
            {"page_id": "PWR-1", "page_type": "einspeisung", "title": "Einspeisung", "order": 10},
            {"page_id": "UV-1", "page_type": "uv_intern", "title": "UV intern", "order": 20},
            {"page_id": "END-1", "page_type": "endstromkreise", "title": "Endstromkreise", "order": 30},
            {"page_id": "TERM-1", "page_type": "klemmenplan", "title": "Klemmenplan", "order": 40},
        ],
    }


def sanitize_elec_schematic(raw: Any) -> dict[str, Any]:
    base = default_elec_schematic()
    if not isinstance(raw, dict):
        return base

    out = dict(base)
    try:
        out["version"] = int(raw.get("version", 1) or 1)
    except (TypeError, ValueError):
        out["version"] = 1

    for key in ("devices", "nets", "connections", "pages"):
        value = raw.get(key)
        if isinstance(value, list):
            out[key] = [v for v in value if isinstance(v, dict)]

    if not out["nets"]:
        out["nets"] = list(base["nets"])
    if not out["pages"]:
        out["pages"] = list(base["pages"])

    return out


def infer_elec_schematic_from_legacy(params: dict[str, Any]) -> dict[str, Any]:
    """Build a first schematic model from legacy elec points/cables/uv_config data."""
    model = default_elec_schematic()

    elec_points = params.get("elec_points", {}) if isinstance(params, dict) else {}
    elec_cables = params.get("elec_cables", {}) if isinstance(params, dict) else {}

    if not isinstance(elec_points, dict):
        elec_points = {}
    if not isinstance(elec_cables, dict):
        elec_cables = {}

    # AP -> device
    for point_id, pdata in elec_points.items():
        if not isinstance(pdata, dict):
            continue
        ap_type = str(pdata.get("ap_type", "standard") or "standard")
        name = str(pdata.get("name", point_id) or point_id)
        symbol = str(pdata.get("builtin_symbol", "") or "")
        device_id = f"DEV-{point_id}"

        terminals: list[dict[str, Any]] = []
        symbol_lower = symbol.lower()
        if "steckdose" in symbol_lower:
            terminals = [
                {"terminal_id": "L", "name": "L", "role": "power", "potential_hint": "L1", "order": 10},
                {"terminal_id": "N", "name": "N", "role": "power", "potential_hint": "N", "order": 20},
                {"terminal_id": "PE", "name": "PE", "role": "pe", "potential_hint": "PE", "order": 30},
            ]
        elif "licht" in symbol_lower or "leuchte" in symbol_lower:
            terminals = [
                {"terminal_id": "L", "name": "L", "role": "power", "potential_hint": "L1", "order": 10},
                {"terminal_id": "N", "name": "N", "role": "power", "potential_hint": "N", "order": 20},
                {"terminal_id": "PE", "name": "PE", "role": "pe", "potential_hint": "PE", "order": 30},
            ]
        elif ap_type == "uv":
            terminals = [
                {"terminal_id": "L-IN", "name": "L-IN", "role": "power", "potential_hint": "L1", "order": 10},
                {"terminal_id": "N-IN", "name": "N-IN", "role": "n", "potential_hint": "N", "order": 20},
                {"terminal_id": "PE-IN", "name": "PE-IN", "role": "pe", "potential_hint": "PE", "order": 30},
            ]
            uv_cfg = pdata.get("uv_config") if isinstance(pdata.get("uv_config"), dict) else {}
            slots = uv_cfg.get("slots", []) if isinstance(uv_cfg, dict) else []
            if isinstance(slots, list):
                for slot in slots:
                    if not isinstance(slot, dict):
                        continue
                    row = int(slot.get("row", 1) or 1)
                    col = int(slot.get("slot", 1) or 1)
                    dev_type = str(slot.get("device_type", "") or "").strip()
                    if not dev_type:
                        continue
                    sfx = f"R{row}-S{col}"
                    terminals.append(
                        {
                            "terminal_id": f"{sfx}-OUT",
                            "name": f"{sfx}-OUT",
                            "role": "power",
                            "potential_hint": "L1",
                            "order": 1000 + row * 100 + col,
                            "meta": {
                                "slot_device_type": dev_type,
                                "assignment": str(slot.get("assignment", "") or ""),
                            },
                        }
                    )
        else:
            terminals = [
                {"terminal_id": "T1", "name": "T1", "role": "signal", "order": 10},
                {"terminal_id": "T2", "name": "T2", "role": "signal", "order": 20},
            ]

        model["devices"].append(
            {
                "device_id": device_id,
                "tag": str(point_id),
                "name": name,
                "family": "distribution" if ap_type == "uv" else "field_device",
                "type": ap_type,
                "symbol_key": symbol,
                "location": str(pdata.get("floor_plan_id", "") or ""),
                "terminals": terminals,
                "properties": {
                    "source_ap_id": str(point_id),
                    "position": str(pdata.get("position", "") or ""),
                    "height_from_floor": pdata.get("height_from_floor", 0.0),
                },
            }
        )

    # Cable -> net + connections (first terminal fallback)
    device_for_ap = {f"DEV-{pid}": f"DEV-{pid}" for pid in elec_points.keys()}

    def _first_terminal(device: dict[str, Any]) -> str:
        terms = device.get("terminals") if isinstance(device, dict) else []
        if isinstance(terms, list) and terms:
            term = terms[0]
            if isinstance(term, dict):
                return str(term.get("terminal_id", "T1") or "T1")
        return "T1"

    device_map = {str(d.get("device_id", "")): d for d in model["devices"] if isinstance(d, dict)}

    for cable_id, cdata in elec_cables.items():
        if not isinstance(cdata, dict):
            continue
        net_id = f"NET-{cable_id}"
        model["nets"].append(
            {
                "net_id": net_id,
                "name": str(cdata.get("name", cable_id) or cable_id),
                "potential": "UNKNOWN",
                "class": "power",
                "color": "#9e9e9e",
                "properties": {"source_cable_id": str(cable_id), "type": str(cdata.get("type", "") or "")},
            }
        )

        start_ap = str(cdata.get("start_ap", "") or "").strip()
        end_ap = str(cdata.get("end_ap", "") or "").strip()

        for ap_id, side in ((start_ap, "start"), (end_ap, "end")):
            if not ap_id:
                continue
            dev_id = f"DEV-{ap_id}"
            dev = device_map.get(dev_id)
            if not dev:
                continue
            model["connections"].append(
                {
                    "conn_id": f"CONN-{cable_id}-{side}",
                    "from": {"device_id": dev_id, "terminal_id": _first_terminal(dev)},
                    "to_net": {"net_id": net_id},
                    "wire_spec": str(cdata.get("type", "") or ""),
                    "properties": {"source_cable_id": str(cable_id)},
                }
            )

    return sanitize_elec_schematic(model)
