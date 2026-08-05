"""Schaltplan-Generator für HRouting.

Erzeugt aus ApNode/CableEdge-Daten:
  - UV-Hierarchiebaum
  - Stromkreisliste pro UV
  - QGraphicsScene-Render für UV-Innenschaltplan
  - QGraphicsScene-Render für Stromkreisplan
  - QGraphicsScene-Render für Hierarchieübersicht
"""
from __future__ import annotations

from typing import Any

# ── Konstanten ─────────────────────────────────────────────────────────── #

#: AP-Typen, die als "Versorgungsknoten" gelten
SUPPLY_TYPES: frozenset[str] = frozenset({"hak", "zaehler", "uv"})

UV_COLORS: dict[str, str] = {
    "":                    "#aaaaaa",
    "Reserve":             "#888888",
    "Hauptschalter":       "#c0392b",
    "LS":                  "#1553b5",
    "LS 3-polig":          "#0d3d8a",
    "FI":                  "#b85d10",
    "FI 4-polig":          "#8a3a00",
    "FI/LS":               "#6b22bf",
    "Überspannungsschutz": "#9b0000",
    "Motorschutz":         "#1a7a3a",
    "Schütz":              "#007070",
    "Zeitschalter":        "#5a5a00",
    "Klemme":              "#9a7000",
    "Steckdose UV":        "#2c6e49",
    "Freitext":            "#444444",
}

UV_SHORT: dict[str, str] = {
    "":                    "",
    "Reserve":             "Res",
    "Hauptschalter":       "HS",
    "LS":                  "LS",
    "LS 3-polig":          "LS3",
    "FI":                  "FI",
    "FI 4-polig":          "FI4",
    "FI/LS":               "FI/L",
    "Überspannungsschutz": "ÜSS",
    "Motorschutz":         "MOT",
    "Schütz":              "SCH",
    "Zeitschalter":        "Zeit",
    "Klemme":              "KL",
    "Steckdose UV":        "SD",
    "Freitext":            "...",
}

# Phasenfarben
PHASE_COLORS: dict[str, str] = {
    "L1": "#e53935",
    "L2": "#43a047",
    "L3": "#1e88e5",
    "N":  "#1565c0",
    "PE": "#8bc34a",
}

_3P_COLORS = {"L1": "#e53935", "L2": "#43a047", "L3": "#1e88e5"}
_3PN_COLORS = {"L1": "#e53935", "L2": "#43a047", "L3": "#1e88e5", "N": "#1565c0"}


# ── Datenschicht ────────────────────────────────────────────────────────── #

def build_uv_hierarchy(
    ap_nodes: dict[str, Any],
    cable_edges: dict[str, Any],
) -> list[dict]:
    """Baut einen Wald aus Versorgungsknoten (hak, zaehler, uv).

    Jeder Knoten hat:
      ap_id, name, ap_type, uv_config, hak_config, zaehler_config, children

    Kabel, die zwei Versorgungsknoten verbinden, bestimmen die Eltern-Kind-
    Relation: start_ap → end_ap (start ist Elternteil).
    """
    supply_ids = {
        pid for pid, node in ap_nodes.items()
        if str(getattr(node, "ap_type", None) or "").strip() in SUPPLY_TYPES
    }

    # Kanten zwischen Versorgungsknoten ermitteln
    parent_of: dict[str, str] = {}  # child_id → parent_id
    for edge in cable_edges.values():
        s = str(getattr(edge, "start_ap_id", "") or "").strip()
        e = str(getattr(edge, "end_ap_id", "") or "").strip()
        if s in supply_ids and e in supply_ids and s != e:
            if e not in parent_of:
                parent_of[e] = s

    # Knoten-Dicts erstellen
    def _make_node(ap_id: str) -> dict:
        node = ap_nodes.get(ap_id)
        if node is None:
            return {"ap_id": ap_id, "name": ap_id, "ap_type": "uv",
                    "uv_config": {}, "hak_config": {}, "zaehler_config": {},
                    "children": []}
        return {
            "ap_id": ap_id,
            "name": str(getattr(node, "name", ap_id) or ap_id),
            "ap_type": str(getattr(node, "ap_type", "uv") or "uv"),
            "uv_config": dict(getattr(node, "uv_config", None) or {}),
            "hak_config": dict(getattr(node, "hak_config", None) or {}),
            "zaehler_config": dict(getattr(node, "zaehler_config", None) or {}),
            "color": str(getattr(node, "color", "#4fc3f7") or "#4fc3f7"),
            "children": [],
        }

    nodes_dict = {pid: _make_node(pid) for pid in supply_ids}

    # Baum aufbauen
    for child_id, parent_id in parent_of.items():
        if parent_id in nodes_dict and child_id in nodes_dict:
            nodes_dict[parent_id]["children"].append(nodes_dict[child_id])

    # Wurzeln = Knoten ohne Elternteil
    child_ids = set(parent_of.keys())
    roots = [n for pid, n in nodes_dict.items() if pid not in child_ids]

    # Alphabetisch sortieren für stabile Reihenfolge
    def _sort_tree(node: dict) -> None:
        node["children"].sort(key=lambda c: c["name"].lower())
        for c in node["children"]:
            _sort_tree(c)

    roots.sort(key=lambda n: n["name"].lower())
    for r in roots:
        _sort_tree(r)

    return roots


def get_uv_circuits(
    uv_ap_id: str,
    ap_nodes: dict[str, Any],
    cable_edges: dict[str, Any],
    room_map: dict[str, str] | None = None,
) -> list[dict]:
    """Gibt die Stromkreise einer UV zurück.

    Jeder Eintrag: {row, slot, device_type, spec, label, assignment,
                    cable_id, end_ap_id, end_ap_name, end_ap_room}
    """
    node = ap_nodes.get(uv_ap_id)
    if node is None:
        return []

    uv_cfg = dict(getattr(node, "uv_config", None) or {})
    slots_list: list[dict] = uv_cfg.get("slots", []) or []

    # cable → end_ap lookup
    cable_to_end: dict[str, str] = {}
    for edge in cable_edges.values():
        cid = str(getattr(edge, "cable_id", "") or "").strip()
        end = str(getattr(edge, "end_ap_id", "") or "").strip()
        start = str(getattr(edge, "start_ap_id", "") or "").strip()
        if cid:
            cable_to_end[cid] = end
            # auch umgekehrt – Kabelrichtung ist nicht semantisch fixiert
            if not end:
                cable_to_end[cid] = start

    room_map = room_map or {}
    result: list[dict] = []
    for slot in slots_list:
        device_type = str(slot.get("device_type", "") or "").strip()
        if not device_type:
            continue
        assignment = str(slot.get("assignment", "") or "").strip()
        end_ap_id = ""
        end_ap_name = ""
        end_ap_room = ""
        if assignment:
            end_ap_id = cable_to_end.get(assignment, "")
            if end_ap_id:
                end_node = ap_nodes.get(end_ap_id)
                if end_node:
                    end_ap_name = str(getattr(end_node, "name", end_ap_id) or end_ap_id)
                    end_ap_room = room_map.get(end_ap_id, "")

        result.append({
            "row": int(slot.get("row", 1) or 1),
            "slot": int(slot.get("slot", 1) or 1),
            "device_type": device_type,
            "spec": str(slot.get("spec", "") or ""),
            "label": str(slot.get("label", "") or ""),
            "assignment": assignment,
            "note": str(slot.get("note", "") or ""),
            "cable_id": assignment,
            "end_ap_id": end_ap_id,
            "end_ap_name": end_ap_name,
            "end_ap_room": end_ap_room,
        })

    result.sort(key=lambda r: (r["row"], r["slot"]))
    return result


# ── QGraphicsScene-Renderer ─────────────────────────────────────────────── #

def render_uv_innenschaltplan(scene, uv_config: dict, ap_name: str = "") -> None:
    """Zeichnet den UV-Innenschaltplan in die übergebene QGraphicsScene.

    Der Maßstab ist so gewählt, dass die gesamte UV bei üblicher Fenstergröße
    gut sichtbar ist (24 px/TE, automatische Zeilenhöhe).
    """
    from PySide6.QtCore import Qt, QRectF
    from PySide6.QtGui import (
        QBrush, QColor, QFont, QPen, QPainterPath,
    )
    from PySide6.QtWidgets import (
        QGraphicsRectItem, QGraphicsSimpleTextItem,
        QGraphicsLineItem, QGraphicsPathItem,
    )

    scene.clear()

    rows = int(uv_config.get("rows", 0) or 0)
    mpr = int(uv_config.get("modules_per_row", 0) or 0)
    slots_list: list[dict] = uv_config.get("slots", []) or []
    busbars_list: list[dict] = uv_config.get("busbars", []) or []

    if rows < 1 or mpr < 1:
        t = scene.addSimpleText("Keine UV-Konfiguration vorhanden.")
        t.setBrush(QBrush(QColor("#888888")))
        return

    # Layout-Konstanten (px)
    TE_W = 24.0
    HEADER_H = 28.0
    TE_NUM_H = 16.0
    SLOT_H = 70.0
    RAIL_H = 10.0
    BB_H = 10.0  # height per busbar band
    BOTTOM_H = 22.0  # row bottom label area (assignment)
    ROW_GAP = 18.0
    LEFT_MARGIN = 36.0
    SCENE_PAD = 20.0

    # Busbars normalisieren (te_ranges → separate Einträge)
    flat_bb: list[dict] = []
    for bb in busbars_list:
        phase = str(bb.get("phase", "") or "")
        color = str(bb.get("color", "#888888") or "#888888")
        if "te_ranges" in bb:
            for rng in (bb["te_ranges"] or []):
                if isinstance(rng, (list, tuple)) and len(rng) == 2:
                    flat_bb.append({"phase": phase, "color": color,
                                    "te_start": int(rng[0]), "te_end": int(rng[1])})
        else:
            flat_bb.append({"phase": phase, "color": color,
                             "te_start": int(bb.get("te_start", 1) or 1),
                             "te_end": int(bb.get("te_end", 1) or 1)})

    # Slot-Map
    slot_map: dict[tuple[int, int], dict] = {}
    for s in slots_list:
        try:
            slot_map[(int(s["row"]), int(s["slot"]))] = s
        except (KeyError, TypeError, ValueError):
            pass

    row_total_h = TE_NUM_H + SLOT_H + RAIL_H + BB_H + BOTTOM_H

    # ── Header ────────────────────────────────────────────────────────── #
    total_w = LEFT_MARGIN + mpr * TE_W
    hdr = QGraphicsRectItem(QRectF(0, 0, total_w, HEADER_H))
    hdr.setBrush(QBrush(QColor("#d0d8e8")))
    hdr.setPen(QPen(QColor("#555555"), 1.0))
    scene.addItem(hdr)

    lbl_name = scene.addSimpleText(ap_name or "UV-Schaltplan")
    lbl_name.setFont(QFont("Arial", 10, QFont.Weight.Bold))
    lbl_name.setBrush(QBrush(QColor("#222222")))
    lbl_name.setPos(8, (HEADER_H - lbl_name.boundingRect().height()) / 2)

    sub = f"  {rows}×{mpr} TE"
    preset = str(uv_config.get("preset", "") or "")
    if preset:
        sub += f"  |  {preset}"
    lbl_sub = scene.addSimpleText(sub)
    lbl_sub.setFont(QFont("Arial", 7))
    lbl_sub.setBrush(QBrush(QColor("#555555")))
    lbl_sub.setPos(8, HEADER_H - lbl_sub.boundingRect().height() - 3)

    # ── Reihen ────────────────────────────────────────────────────────── #
    te_global_offset = 0
    for row_idx in range(rows):
        row_no = row_idx + 1
        ry = HEADER_H + ROW_GAP + row_idx * (row_total_h + ROW_GAP)
        rx = LEFT_MARGIN

        # Row label
        rl = scene.addSimpleText(f"R{row_no}")
        rl.setFont(QFont("Arial", 8, QFont.Weight.Bold))
        rl.setBrush(QBrush(QColor("#555555")))
        br = rl.boundingRect()
        rl.setPos(LEFT_MARGIN - br.width() - 4,
                  ry + TE_NUM_H + (SLOT_H - br.height()) / 2)

        # TE-Nummern
        font_te = QFont("Arial", 5)
        for te in range(1, mpr + 1):
            tx = rx + (te - 1) * TE_W
            t = scene.addSimpleText(str(te_global_offset + te))
            t.setFont(font_te)
            t.setBrush(QBrush(QColor("#888888")))
            br = t.boundingRect()
            t.setPos(tx + (TE_W - br.width()) / 2, ry + (TE_NUM_H - br.height()) / 2)

        # Slots zeichnen
        te = 1
        while te <= mpr:
            sx = rx + (te - 1) * TE_W
            sy = ry + TE_NUM_H
            slot_data = slot_map.get((row_no, te))
            ts = max(1, int((slot_data or {}).get("te_size", 1) or 1))
            ts = min(ts, mpr - te + 1)
            sw = ts * TE_W
            device_type = str((slot_data or {}).get("device_type", "") or "").strip()
            color_hex = UV_COLORS.get(device_type, UV_COLORS[""])

            if slot_data and device_type:
                box = QGraphicsRectItem(QRectF(sx + 1.5, sy, sw - 3.0, SLOT_H))
                box.setBrush(QBrush(QColor(color_hex)))
                box.setPen(QPen(QColor("#222222"), 1.0))
                scene.addItem(box)

                short = UV_SHORT.get(device_type, device_type[:4])
                t_type = scene.addSimpleText(short)
                t_type.setFont(QFont("Arial", 6, QFont.Weight.Bold))
                t_type.setBrush(QBrush(QColor("#ffffff")))
                br = t_type.boundingRect()
                t_type.setPos(sx + (sw - br.width()) / 2, sy + 4)

                spec = str(slot_data.get("spec", "") or "").strip()
                if spec:
                    t_spec = scene.addSimpleText(spec)
                    t_spec.setFont(QFont("Arial", 5))
                    t_spec.setBrush(QBrush(QColor("#ffe08a")))
                    br = t_spec.boundingRect()
                    t_spec.setPos(sx + (sw - br.width()) / 2, sy + SLOT_H * 0.38)

                label = str(slot_data.get("label", "") or "").strip()
                if label:
                    t_lbl = scene.addSimpleText(label)
                    t_lbl.setFont(QFont("Arial", 5))
                    t_lbl.setBrush(QBrush(QColor("#eeeeee")))
                    br = t_lbl.boundingRect()
                    y_off = SLOT_H * (0.62 if spec else 0.52)
                    t_lbl.setPos(sx + (sw - br.width()) / 2, sy + y_off)

                # DIN-Symbol-Akzentlinie
                _draw_device_symbol(scene, sx + 1.5, sy, sw - 3.0, SLOT_H, device_type)

            else:
                box = QGraphicsRectItem(QRectF(sx + 1.5, sy, sw - 3.0, SLOT_H))
                box.setBrush(QBrush(QColor("#e8e8e8")))
                box.setPen(QPen(QColor("#cccccc"), 0.8))
                scene.addItem(box)

            te += ts

        # DIN-Schiene
        rail_y = ry + TE_NUM_H + SLOT_H
        rail = QGraphicsRectItem(QRectF(rx, rail_y, mpr * TE_W, RAIL_H))
        rail.setBrush(QBrush(QColor("#999999")))
        rail.setPen(QPen(QColor("#777777"), 0.8))
        scene.addItem(rail)
        # Rastkerben
        pen_notch = QPen(QColor("#bbbbbb"), 0.8)
        for te in range(0, mpr + 1, 2):
            nx = rx + te * TE_W
            line = scene.addLine(nx, rail_y + 2, nx, rail_y + RAIL_H - 2, pen_notch)

        # Busbar-Phasenbänder
        bb_y = rail_y + RAIL_H
        row_te_s_g = te_global_offset + 1
        row_te_e_g = te_global_offset + mpr
        _draw_busbars(scene, flat_bb, rx, bb_y, BB_H, TE_W,
                      row_te_s_g, row_te_e_g)

        # Abgangs-Labels (assignment) unter den Slots
        lbl_y = bb_y + BB_H + 2
        te = 1
        while te <= mpr:
            sx = rx + (te - 1) * TE_W
            slot_data = slot_map.get((row_no, te))
            ts = max(1, int((slot_data or {}).get("te_size", 1) or 1))
            ts = min(ts, mpr - te + 1)
            sw = ts * TE_W
            if slot_data:
                asgn = str(slot_data.get("assignment", "") or "").strip()
                if asgn:
                    t_a = scene.addSimpleText(asgn)
                    t_a.setFont(QFont("Arial", 4))
                    t_a.setBrush(QBrush(QColor("#333333")))
                    br = t_a.boundingRect()
                    t_a.setPos(sx + (sw - br.width()) / 2, lbl_y)
            te += ts

        te_global_offset += mpr

    # Szenenrahmen anpassen
    total_h = (HEADER_H + ROW_GAP
               + rows * (row_total_h + ROW_GAP)
               + SCENE_PAD)
    scene.setSceneRect(QRectF(-SCENE_PAD, -SCENE_PAD,
                               total_w + 2 * SCENE_PAD,
                               total_h + 2 * SCENE_PAD))


def _draw_device_symbol(scene, x: float, y: float, w: float, h: float,
                         device_type: str) -> None:
    """Zeichnet ein vereinfachtes DIN-Symbol-Akzent-Element."""
    from PySide6.QtGui import QPen, QColor, QPainterPath
    from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsLineItem

    cx = x + w / 2
    mid_y = y + h * 0.22

    if device_type in ("LS", "LS 3-polig"):
        # Diagonallinie im Kästchen
        pen = QPen(QColor("#ffffff"), 1.2)
        line = scene.addLine(x + w * 0.25, y + h * 0.08,
                              x + w * 0.75, y + h * 0.24, pen)

    elif device_type in ("FI", "FI 4-polig"):
        # Kreis
        pen = QPen(QColor("#ffffff"), 1.0)
        r = min(w, h * 0.2) * 0.45
        scene.addEllipse(cx - r, mid_y - r, 2 * r, 2 * r, pen)

    elif device_type == "FI/LS":
        # Diagonale + kleiner Kreis
        pen = QPen(QColor("#ffffff"), 1.0)
        scene.addLine(x + w * 0.2, y + h * 0.08,
                       x + w * 0.6, y + h * 0.22, pen)
        r = min(w, h * 0.2) * 0.35
        scene.addEllipse(x + w * 0.6 - r, mid_y - r, 2 * r, 2 * r, pen)

    elif device_type == "Hauptschalter":
        # Vertikale Linie mit O/I-Andeutung
        pen = QPen(QColor("#ffffff"), 1.2)
        scene.addLine(cx, y + h * 0.06, cx, y + h * 0.22, pen)


def _draw_busbars(scene, flat_bb: list[dict], rx: float, bb_y: float,
                   bb_h: float, te_w: float,
                   row_te_s_g: int, row_te_e_g: int) -> None:
    """Zeichnet Phasenband-Streifen für eine Reihe."""
    from PySide6.QtGui import QBrush, QColor, QFont, QPen
    from PySide6.QtCore import Qt, QRectF

    font_bb = QFont("Arial", 4, QFont.Weight.Bold)

    for bb in flat_bb:
        bb_te_s = int(bb.get("te_start", 1) or 1)
        bb_te_e = int(bb.get("te_end", 1) or 1)
        vis_s = max(bb_te_s, row_te_s_g)
        vis_e = min(bb_te_e, row_te_e_g)
        if vis_s > vis_e:
            continue

        bb_phase = str(bb.get("phase", "") or "")
        bb_color = str(bb.get("color", "#888888") or "#888888")

        if bb_phase in ("L1/L2/L3", "3~N", "3~N4"):
            # Einzelne TE einfärben
            colors_map = _3P_COLORS if bb_phase == "L1/L2/L3" else _3PN_COLORS
            cycle = (
                ["L1", "L2", "L3"] if bb_phase == "L1/L2/L3"
                else ["L1", "L2", "L3", "N"] if bb_phase == "3~N"
                else None  # handled below
            )
            for te_g in range(vis_s, vis_e + 1):
                local_te = te_g - row_te_s_g  # 0-based
                te_bx = rx + local_te * te_w
                if bb_phase == "3~N4":
                    idx = te_g - bb_te_s
                    if idx < 3:
                        ph = ("L1", "L2", "L3")[idx]
                    elif idx == 3:
                        ph = "N"
                    else:
                        ph = ("L1", "L2", "L3")[(idx - 4) % 3]
                    c = _3PN_COLORS.get(ph, "#888888")
                else:
                    ph = cycle[(te_g - bb_te_s) % len(cycle)]
                    c = colors_map.get(ph, "#888888")

                rect = scene.addRect(QRectF(te_bx, bb_y, te_w, bb_h))
                rect.setBrush(QBrush(QColor(c)))
                rect.setPen(QPen(QColor(c), 0))
                t = scene.addSimpleText(ph)
                t.setFont(font_bb)
                t.setBrush(QBrush(QColor("#ffffff")))
                br = t.boundingRect()
                t.setPos(te_bx + (te_w - br.width()) / 2,
                          bb_y + (bb_h - br.height()) / 2)
        else:
            local_s = vis_s - row_te_s_g
            local_e = vis_e - row_te_s_g
            bx = rx + local_s * te_w
            bw = (local_e - local_s + 1) * te_w
            rect = scene.addRect(QRectF(bx, bb_y, bw, bb_h))
            rect.setBrush(QBrush(QColor(bb_color)))
            rect.setPen(QPen(QColor(bb_color), 0))
            if bb_phase:
                t = scene.addSimpleText(bb_phase)
                t.setFont(font_bb)
                t.setBrush(QBrush(QColor("#ffffff")))
                br = t.boundingRect()
                t.setPos(bx + (bw - br.width()) / 2,
                          bb_y + (bb_h - br.height()) / 2)


def render_stromkreisplan(
    scene,
    uv_ap_id: str,
    circuits: list[dict],
    uv_name: str = "",
) -> None:
    """Zeichnet den Stromkreisplan als Tabelle in die QGraphicsScene."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QBrush, QColor, QFont, QPen

    scene.clear()

    COLS = ["Reihe", "Sl.", "Gerät", "Kennz.", "Bezeichnung", "Kabel", "Verbraucher", "Raum"]
    COL_W = [50.0, 35.0, 80.0, 80.0, 120.0, 80.0, 160.0, 100.0]
    ROW_H = 22.0
    HEADER_H = 28.0
    PAD_X = 6.0
    SCENE_PAD = 16.0

    total_w = sum(COL_W)

    # Titel
    title_text = f"Stromkreisplan – {uv_name}" if uv_name else "Stromkreisplan"
    t = scene.addSimpleText(title_text)
    t.setFont(QFont("Arial", 11, QFont.Weight.Bold))
    t.setBrush(QBrush(QColor("#222222")))
    t.setPos(0, 0)
    title_h = t.boundingRect().height() + 10

    y = title_h

    # Spaltenüberschriften
    x = 0.0
    hdr_bg = scene.addRect(QRectF(0, y, total_w, HEADER_H))
    hdr_bg.setBrush(QBrush(QColor("#1553b5")))
    hdr_bg.setPen(QPen(QColor("#1553b5"), 0))
    for i, col in enumerate(COLS):
        t = scene.addSimpleText(col)
        t.setFont(QFont("Arial", 7, QFont.Weight.Bold))
        t.setBrush(QBrush(QColor("#ffffff")))
        br = t.boundingRect()
        t.setPos(x + PAD_X, y + (HEADER_H - br.height()) / 2)
        x += COL_W[i]

    # Trennlinien Überschriften
    x = 0.0
    for cw in COL_W:
        x += cw
        scene.addLine(x, y, x, y + HEADER_H, QPen(QColor("#ffffff"), 0.5))

    y += HEADER_H

    if not circuits:
        t = scene.addSimpleText("Keine Stromkreise konfiguriert.")
        t.setFont(QFont("Arial", 8))
        t.setBrush(QBrush(QColor("#888888")))
        t.setPos(PAD_X, y + 8)
        scene.setSceneRect(QRectF(-SCENE_PAD, -SCENE_PAD,
                                   total_w + 2 * SCENE_PAD,
                                   y + 60 + 2 * SCENE_PAD))
        return

    font_cell = QFont("Arial", 7)
    pen_grid = QPen(QColor("#dddddd"), 0.5)
    pen_dark = QPen(QColor("#222222"), 0)

    for row_idx, circ in enumerate(circuits):
        row_bg_color = "#f7f9ff" if row_idx % 2 == 0 else "#ffffff"
        bg = scene.addRect(QRectF(0, y, total_w, ROW_H))
        bg.setBrush(QBrush(QColor(row_bg_color)))
        bg.setPen(QPen(QColor(row_bg_color), 0))

        cells = [
            str(circ.get("row", "")),
            str(circ.get("slot", "")),
            circ.get("device_type", ""),
            circ.get("spec", ""),
            circ.get("label", ""),
            circ.get("cable_id", ""),
            circ.get("end_ap_name", ""),
            circ.get("end_ap_room", ""),
        ]
        x = 0.0
        for i, cell in enumerate(cells):
            t = scene.addSimpleText(str(cell))
            t.setFont(font_cell)
            t.setBrush(QBrush(QColor("#222222")))
            br = t.boundingRect()
            t.setPos(x + PAD_X, y + (ROW_H - br.height()) / 2)
            x += COL_W[i]

        # Trennlinien vertikal
        x = 0.0
        for cw in COL_W:
            x += cw
            scene.addLine(x, y, x, y + ROW_H, pen_grid)

        scene.addLine(0, y + ROW_H, total_w, y + ROW_H, pen_grid)
        y += ROW_H

    scene.setSceneRect(QRectF(-SCENE_PAD, -SCENE_PAD,
                               total_w + 2 * SCENE_PAD,
                               y + 2 * SCENE_PAD))


def render_hierarchy_overview(
    scene,
    hierarchy: list[dict],
    ap_nodes: dict[str, Any] | None = None,
) -> None:
    """Zeichnet den UV-Hierarchiebaum in die QGraphicsScene."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QBrush, QColor, QFont, QPen

    scene.clear()

    if not hierarchy:
        t = scene.addSimpleText("Keine Versorgungsknoten vorhanden.\n"
                                "Lege einen AP mit Typ HAK, Zähler oder UV an.")
        t.setBrush(QBrush(QColor("#888888")))
        scene.setSceneRect(QRectF(-20, -20, 400, 100))
        return

    NODE_W = 140.0
    NODE_H = 52.0
    H_GAP = 60.0   # horizontal gap between sibling nodes
    V_GAP = 80.0   # vertical gap between levels
    SCENE_PAD = 32.0

    AP_TYPE_COLORS: dict[str, tuple[str, str]] = {
        "hak":     ("#c0392b", "#ffffff"),
        "zaehler": ("#7b3fb0", "#ffffff"),
        "uv":      ("#1553b5", "#ffffff"),
    }

    # Breitenberechnung für jeden Knoten
    def _subtree_width(node: dict) -> float:
        if not node["children"]:
            return NODE_W
        children_w = sum(_subtree_width(c) for c in node["children"])
        spacing = H_GAP * (len(node["children"]) - 1)
        return max(NODE_W, children_w + spacing)

    # Knoten rekursiv platzieren und zeichnen
    def _place_and_draw(node: dict, cx: float, cy: float) -> None:
        ap_type = node.get("ap_type", "uv")
        bg_color, fg_color = AP_TYPE_COLORS.get(ap_type, ("#1553b5", "#ffffff"))

        uv_cfg = node.get("uv_config") or {}
        rows = int(uv_cfg.get("rows", 0) or 0)
        mpr = int(uv_cfg.get("modules_per_row", 0) or 0)

        # Hintergrund
        box = scene.addRect(QRectF(cx - NODE_W / 2, cy, NODE_W, NODE_H))
        box.setBrush(QBrush(QColor(bg_color)))
        box.setPen(QPen(QColor("#333333"), 1.2))

        # AP-Typ-Baderl oben links
        type_labels = {"hak": "HAK", "zaehler": "ZÄH", "uv": "UV"}
        type_short = type_labels.get(ap_type, ap_type.upper()[:3])
        badge = scene.addRect(QRectF(cx - NODE_W / 2, cy, 28, 14))
        badge.setBrush(QBrush(QColor("#333333")))
        badge.setPen(QPen(QColor("#333333"), 0))
        t_badge = scene.addSimpleText(type_short)
        t_badge.setFont(QFont("Arial", 5, QFont.Weight.Bold))
        t_badge.setBrush(QBrush(QColor("#ffffff")))
        t_badge.setPos(cx - NODE_W / 2 + 2, cy + 1)

        # Name
        name = node.get("name", "?")
        t_name = scene.addSimpleText(name)
        t_name.setFont(QFont("Arial", 8, QFont.Weight.Bold))
        t_name.setBrush(QBrush(QColor(fg_color)))
        br = t_name.boundingRect()
        t_name.setPos(cx - br.width() / 2, cy + 16)

        # Konfigurationsinfo
        if ap_type == "uv" and rows > 0 and mpr > 0:
            info = f"{rows}×{mpr} TE"
            t_info = scene.addSimpleText(info)
            t_info.setFont(QFont("Arial", 6))
            t_info.setBrush(QBrush(QColor(fg_color)))
            br = t_info.boundingRect()
            t_info.setPos(cx - br.width() / 2, cy + NODE_H - br.height() - 4)
        elif ap_type == "hak":
            hak_cfg = node.get("hak_config") or {}
            v = str(hak_cfg.get("incoming_voltage", "") or "")
            a = str(hak_cfg.get("main_fuse_a", "") or "")
            if v or a:
                info = " / ".join(x for x in [v, a + "A" if a else ""] if x)
                t_info = scene.addSimpleText(info)
                t_info.setFont(QFont("Arial", 6))
                t_info.setBrush(QBrush(QColor(fg_color)))
                br = t_info.boundingRect()
                t_info.setPos(cx - br.width() / 2, cy + NODE_H - br.height() - 4)
        elif ap_type == "zaehler":
            z_cfg = node.get("zaehler_config") or {}
            mid = str(z_cfg.get("meter_id", "") or "")
            if mid:
                t_mid = scene.addSimpleText(f"Nr: {mid}")
                t_mid.setFont(QFont("Arial", 6))
                t_mid.setBrush(QBrush(QColor(fg_color)))
                br = t_mid.boundingRect()
                t_mid.setPos(cx - br.width() / 2, cy + NODE_H - br.height() - 4)

        node["_cx"] = cx
        node["_cy"] = cy

        # Kinder platzieren
        if node["children"]:
            child_y = cy + NODE_H + V_GAP
            total_children_w = sum(_subtree_width(c) for c in node["children"])
            spacing = H_GAP * (len(node["children"]) - 1)
            total_span = total_children_w + spacing
            child_x = cx - total_span / 2

            for child in node["children"]:
                child_w = _subtree_width(child)
                child_cx = child_x + child_w / 2
                _place_and_draw(child, child_cx, child_y)

                # Verbindungslinie Eltern → Kind
                pen_line = QPen(QColor("#555555"), 1.5)
                scene.addLine(
                    cx, cy + NODE_H,
                    child_cx, child_y,
                    pen_line,
                )
                child_x += child_w + H_GAP

    # Mehrere Wurzeln nebeneinander
    total_root_w = sum(_subtree_width(r) for r in hierarchy)
    spacing = H_GAP * (len(hierarchy) - 1)
    total_span = total_root_w + spacing
    rx = -total_span / 2

    for root in hierarchy:
        rw = _subtree_width(root)
        root_cx = rx + rw / 2
        _place_and_draw(root, root_cx, 0)
        rx += rw + H_GAP

    bounds = scene.itemsBoundingRect()
    scene.setSceneRect(bounds.adjusted(-SCENE_PAD, -SCENE_PAD,
                                        SCENE_PAD, SCENE_PAD))
