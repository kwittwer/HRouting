#!/usr/bin/env python3
# HRouting – Fußbodenheizung und Kabel Planer
# Copyright (C) 2026 Konrad-Fabian Wittwer
#
# Standalone MCP-Server (stdio) für die Copilot CLI.
# Arbeitet direkt auf .hrp-Dateien ohne die GUI.
#
# Nutzung:
#   copilot --additional-mcp-config @.copilot/mcp-config.json
#   copilot -p "Welche Heizkreise gibt es?" --additional-mcp-config @.copilot/mcp-config.json

"""
Standalone MCP-Server für HRouting (stdio-Transport).

Dieser Server kann von der GitHub Copilot CLI direkt gestartet werden
und arbeitet auf .hrp-Projektdateien ohne laufende GUI. Er bietet
Lese- und Schreibzugriff auf HRouting-Projekte.

Konfiguration in ~/.copilot/mcp-config.json oder per --additional-mcp-config.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import re
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("hrouting.mcp-stdio")

BASE_DIR = Path(__file__).parent

# ── Projekt-State ──────────────────────────────────────────────────

class ProjectState:
    """Hält den Zustand eines geladenen .hrp-Projekts."""

    def __init__(self):
        self.path: Path | None = None
        self.project_id: str | None = None
        self.data: dict = self._empty_project()
        self._dirty = False
        self._revision_version = 0
        self._source_disk_hash = ""
        self._source_mtime_utc = ""
        self._transactions: list[dict] = []
        self._last_validation_report: dict | None = None

    @property
    def dirty(self) -> bool:
        return self._dirty

    @dirty.setter
    def dirty(self, value: bool):
        self._dirty = value
        if value:
            self._revision_version += 1
        if value and self.path:
            self.save()
            logger.debug("Auto-save: %s", self.path)

    @staticmethod
    def _stable_hash(data: dict) -> str:
        canonical = json.dumps(
            data,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _disk_hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _path_project_id(path: Path) -> str:
        base = str(path.resolve()).replace("\\", "/")
        digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
        return f"project-{digest}"

    def _update_source_state(self):
        if not self.path or not self.path.exists():
            self._source_disk_hash = ""
            self._source_mtime_utc = ""
            return
        self._source_disk_hash = self._disk_hash(self.path)
        mtime = datetime.fromtimestamp(self.path.stat().st_mtime, tz=UTC)
        self._source_mtime_utc = mtime.isoformat()

    def get_revision(self) -> dict:
        return {
            "project_id": self.project_id,
            "revision": {
                "hash": self._stable_hash(self.data),
                "version": self._revision_version,
                "mtime_utc": self._source_mtime_utc,
                "dirty": self._dirty,
            },
            "source_revision": {
                "disk_hash": self._source_disk_hash,
                "mtime_utc": self._source_mtime_utc,
            },
        }

    def assert_in_sync(self, expected_revision: str = "") -> dict:
        if not self.path:
            return {
                "in_sync": True,
                "current_revision": self.get_revision().get("revision", {}),
                "expected_revision": expected_revision or None,
            }

        if not self.path.exists():
            return {
                "in_sync": False,
                "drift_reason": "PROJECT_FILE_MISSING",
                "current_revision": self.get_revision().get("revision", {}),
                "expected_revision": expected_revision or None,
            }

        current_disk_hash = self._disk_hash(self.path)
        current = self.get_revision().get("revision", {})

        if expected_revision and current.get("hash") != expected_revision:
            return {
                "in_sync": False,
                "drift_reason": "REVISION_MISMATCH",
                "current_revision": current,
                "expected_revision": expected_revision,
            }

        if self._source_disk_hash and current_disk_hash != self._source_disk_hash:
            return {
                "in_sync": False,
                "drift_reason": "DISK_CHANGED_OUTSIDE_SESSION",
                "current_revision": current,
                "expected_revision": expected_revision or None,
            }

        return {
            "in_sync": True,
            "current_revision": current,
            "expected_revision": expected_revision or None,
        }

    def push_transaction(
        self,
        *,
        before_data: dict,
        delta: dict,
        before_revision: str,
        after_revision: str,
    ) -> str:
        tx_id = f"tx-{len(self._transactions) + 1}"
        self._transactions.append({
            "transaction_id": tx_id,
            "before_data": before_data,
            "delta": delta,
            "before_revision": before_revision,
            "after_revision": after_revision,
            "created_at": datetime.now(tz=UTC).isoformat(),
        })
        return tx_id

    def rollback_transaction(self, transaction_id: str = "") -> dict:
        if not self._transactions:
            return {"error": "TRANSACTION_NOT_FOUND"}

        tx = None
        if transaction_id:
            for candidate in self._transactions:
                if candidate.get("transaction_id") == transaction_id:
                    tx = candidate
                    break
            if tx is None:
                return {"error": "TRANSACTION_NOT_FOUND", "transaction_id": transaction_id}
        else:
            tx = self._transactions[-1]

        self.data = copy.deepcopy(tx.get("before_data", self._empty_project()))
        self.dirty = True

        return {
            "rolled_back": True,
            "transaction_id": tx.get("transaction_id"),
            "restored_revision": self.get_revision().get("revision", {}),
            "reverted_delta": tx.get("delta", {}),
        }

    @staticmethod
    def _empty_project() -> dict:
        return {
            "svg_path": "",
            "canvas": {
                "view_scale": 1.0, "view_offset": [0, 0],
                "bg_color": "#2b2b2b",
                "grid_visible": True, "grid_spacing_mm": 100,
                "grid_color": [255, 255, 255, 30],
                "snap_angle": 0, "export_frame": None,
                "measure_color": "#ffdd00", "mm_per_px": 1.0,
                "ref_line": None, "ref_line_colors": {},
                "ref_line_visible": {}, "polygons": {},
                "start_points": {}, "manual_routes": {},
                "route_wall_dist_px": {}, "route_line_dist_px": {},
                "supply_lines": {}, "elec_points": {},
                "elec_point_size_px": {}, "elec_point_position": {},
                "elec_point_height": {}, "elec_point_notes": {},
                "elec_point_smarthome_device": {},
                "elec_point_smarthome_device_color": {},
                "elec_rooms": {}, "elec_room_visible": {},
                "elec_cables": {}, "elec_cable_notes": {},
                "cable_start_ap": {}, "cable_end_ap": {},
                "elec_visible": {}, "hkv_points": {},
                "hkv_size_px": {}, "hkv_visible": {},
                "supply_hkv": {}, "hkv_lines": {},
                "hkv_line_start": {}, "hkv_line_end": {},
                "hkv_line_visible": {}, "label_positions": {},
                "label_font_sizes": {}, "label_visible": {},
                "text_annotations": {}, "floor_plans": [],
            },
            "params": {
                "t_supply": 35.0, "t_return": 30.0,
                "t_norm_outdoor": -12.0,
                "elec_cable_defaults": {},
                "bom": {
                    "version": 1,
                    "sections": {
                        "hk_bom_rows": True,
                        "cable_bom_rows": True,
                        "ap_bom_rows": True,
                        "hkv_line_bom_rows": True,
                        "uv_device_bom_rows": True,
                        "uv_busbar_bom_rows": True,
                        "custom_bom_rows": True,
                    },
                    "rounding": {"length_m": 1, "quantity": 1},
                    "last_generated_at": "",
                    "item_catalog": {},
                    "custom_items": [],
                },
                "floorplans_order": [], "floorplans": {},
                "furniture": {}, "circuits": {},
                "elec_points": {}, "elec_rooms": {},
                "elec_cables": {}, "hkv_points": {},
                "hkv_lines": {}, "text_annotations": {},
            },
            "pdf_export_pages": [],
        }

    def load(self, path: str) -> dict:
        """Lädt ein .hrp-Projekt."""
        p = Path(path)
        if not p.exists():
            return {"error": f"Datei nicht gefunden: {path}"}
        try:
            self.data = json.loads(p.read_text(encoding="utf-8"))
            self.path = p
            self.project_id = self._path_project_id(p)
            self._dirty = False
            self._revision_version += 1
            self._update_source_state()
            return {
                "status": "ok",
                "path": str(p),
                "project_id": self.project_id,
                "revision": self.get_revision().get("revision", {}),
                "info": self._summary(),
            }
        except Exception as e:
            return {"error": f"Fehler beim Laden: {e}"}

    def save(self, path: str = "") -> dict:
        """Speichert das Projekt."""
        target = Path(path) if path else self.path
        if not target:
            return {"error": "Kein Dateipfad angegeben."}
        try:
            target.write_text(
                json.dumps(self.data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            self.path = target
            if not self.project_id:
                self.project_id = self._path_project_id(target)
            self._dirty = False
            self._update_source_state()
            return {
                "status": "ok",
                "path": str(target),
                "project_id": self.project_id,
                "revision": self.get_revision().get("revision", {}),
            }
        except Exception as e:
            return {"error": f"Fehler beim Speichern: {e}"}

    def _summary(self) -> dict:
        p = self.data.get("params", {})
        c = self.data.get("canvas", {})
        return {
            "circuits": len(p.get("circuits", {})),
            "elec_points": len(p.get("elec_points", {})),
            "floor_plans": len(p.get("floorplans", {})),
            "elec_rooms": len(p.get("elec_rooms", {})),
            "elec_cables": len(p.get("elec_cables", {})),
            "hkv_count": len(p.get("hkv_points", {})),
            "mm_per_px": c.get("mm_per_px", 1.0),
        }

    def _next_id(self, prefix: str, collection: dict) -> str:
        """Erzeugt die nächste freie ID (z.B. HK-3)."""
        existing = [
            int(k.split("-")[1]) for k in collection
            if k.startswith(prefix + "-") and k.split("-")[1].isdigit()
        ]
        n = max(existing, default=0) + 1
        return f"{prefix}-{n}"


# Globaler State
_state = ProjectState()


def _create_stdio_mcp():
    """Erstellt den FastMCP-Server mit stdio-Transport."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "HRouting",
        instructions=(
            "HRouting MCP Server – Fußbodenheizungs- und Elektroplanung. "
            "Arbeitet auf .hrp-Projektdateien. "
            "Nutze open_project() zum Laden und get_project_summary() als Einstieg. "
            "Koordinaten sind in Canvas-Pixeln. Einheiten: mm."
        ),
    )

    def _norm_text(value: str) -> str:
        if value is None:
            return ""
        v = unicodedata.normalize("NFKD", value)
        v = "".join(ch for ch in v if not unicodedata.combining(ch))
        v = v.lower().strip()
        v = re.sub(r"\s+", " ", v)
        v = re.sub(r"[^a-z0-9 ]+", "", v)
        return v

    def _cable_endpoint_position(cable_id: str, side: str) -> list[float] | None:
        poly = _state.data.get("canvas", {}).get("elec_cables", {}).get(cable_id, [])
        if not poly:
            return None
        return poly[0] if side == "start" else poly[-1]

    def _collect_candidate_points_by_name(name_value: str, normalized: bool = False) -> list[dict]:
        points = _state.data.get("params", {}).get("elec_points", {})
        target = _norm_text(name_value) if normalized else name_value
        result: list[dict] = []
        for pid, pdata in points.items():
            pname = pdata.get("name", "")
            cmp_name = _norm_text(pname) if normalized else pname
            if cmp_name == target:
                result.append({"point_id": pid, "name": pname})
        return sorted(result, key=lambda x: x["point_id"])

    def _candidate_from_geometry(cable_id: str, side: str, max_distance_px: float = 2500.0) -> list[dict]:
        ep = _cable_endpoint_position(cable_id, side)
        if ep is None:
            return []
        ex, ey = ep[0], ep[1]
        points_pos = _state.data.get("canvas", {}).get("elec_points", {})
        candidates: list[dict] = []
        for pid, pos in points_pos.items():
            if not isinstance(pos, list) or len(pos) < 2:
                continue
            dist = math.dist([ex, ey], [float(pos[0]), float(pos[1])])
            confidence = max(0.0, 1.0 - (dist / max_distance_px))
            if confidence <= 0:
                continue
            candidates.append({
                "point_id": pid,
                "confidence": round(confidence, 4),
                "reason": "nearest_geometry",
                "strategy": "nearest_geometry",
                "distance_px": round(dist, 2),
            })
        candidates.sort(key=lambda c: (-c["confidence"], c["point_id"]))
        return candidates[:8]

    def _resolve_endpoint_candidates(
        cable_id: str,
        side: str,
        value,
        strategy: list[str] | None = None,
        max_distance_px: float = 2500.0,
        max_candidates: int = 8,
    ) -> list[dict]:
        strategies = strategy or [
            "exact_id",
            "exact_name",
            "normalized_name",
            "nearest_geometry",
        ]
        points = _state.data.get("params", {}).get("elec_points", {})
        merged: dict[str, dict] = {}

        def _upsert(candidate: dict):
            pid = candidate["point_id"]
            existing = merged.get(pid)
            if existing is None or candidate["confidence"] > existing["confidence"]:
                merged[pid] = candidate

        str_value = value if isinstance(value, str) else ""

        if "exact_id" in strategies and str_value and str_value in points:
            _upsert({
                "point_id": str_value,
                "confidence": 1.0,
                "reason": "exact_id",
                "strategy": "exact_id",
            })

        if "exact_name" in strategies and str_value:
            exact = _collect_candidate_points_by_name(str_value, normalized=False)
            conf = 0.96 if len(exact) == 1 else 0.65
            for item in exact:
                _upsert({
                    "point_id": item["point_id"],
                    "confidence": conf,
                    "reason": "exact_name",
                    "strategy": "exact_name",
                })

        if "normalized_name" in strategies and str_value:
            normalized = _collect_candidate_points_by_name(str_value, normalized=True)
            conf = 0.9 if len(normalized) == 1 else 0.6
            for item in normalized:
                _upsert({
                    "point_id": item["point_id"],
                    "confidence": conf,
                    "reason": "normalized_name",
                    "strategy": "normalized_name",
                })

        if "nearest_geometry" in strategies:
            for item in _candidate_from_geometry(
                cable_id,
                side,
                max_distance_px=max_distance_px,
            ):
                _upsert(item)

        ranked = sorted(merged.values(), key=lambda c: (-c["confidence"], c["point_id"]))
        return ranked[:max_candidates]

    def _collect_reference_issues(
        scope: str = "elec_cables",
        include_resolvable: bool = True,
    ) -> list[dict]:
        issues: list[dict] = []
        if scope not in ("elec_cables", "all"):
            return issues

        params = _state.data.get("params", {})
        canvas = _state.data.get("canvas", {})
        points = params.get("elec_points", {})
        cables = params.get("elec_cables", {})
        start_map = canvas.get("cable_start_ap", {})
        end_map = canvas.get("cable_end_ap", {})

        for cable_id in sorted(cables.keys()):
            for side, cmap, field in (
                ("start", start_map, "start_ap_id"),
                ("end", end_map, "end_ap_id"),
            ):
                value = cmap.get(cable_id, "")
                reason = None
                severity = "error"

                if value is None or value == "":
                    reason = "missing"
                    severity = "warning"
                elif not isinstance(value, str):
                    reason = "invalid_type"
                elif value in points:
                    continue
                else:
                    same_name = _collect_candidate_points_by_name(value, normalized=False)
                    same_norm = _collect_candidate_points_by_name(value, normalized=True)
                    if len(same_name) == 1 or len(same_norm) == 1:
                        reason = "name_instead_of_id"
                    elif len(same_name) > 1 or len(same_norm) > 1:
                        reason = "ambiguous_name"
                    else:
                        reason = "not_found"

                candidates = []
                if include_resolvable:
                    candidates = _resolve_endpoint_candidates(cable_id, side, value)

                issues.append({
                    "issue_id": f"ref-{cable_id}-{side}",
                    "category": "ref",
                    "entity_type": "elec_cable",
                    "entity_id": cable_id,
                    "cable_id": cable_id,
                    "side": side,
                    "field": field,
                    "value": value,
                    "reason": reason,
                    "severity": severity,
                    "resolvable_candidates": candidates,
                })

        return issues

    def _build_endpoint_delta(mappings: list[dict], strict: bool = True) -> dict:
        params = _state.data.get("params", {})
        canvas = _state.data.get("canvas", {})
        cables = params.get("elec_cables", {})
        points = params.get("elec_points", {})
        start_map = canvas.setdefault("cable_start_ap", {})
        end_map = canvas.setdefault("cable_end_ap", {})

        conflicts: list[dict] = []
        operations: list[dict] = []
        seen: dict[tuple[str, str], str] = {}

        for idx, mapping in enumerate(mappings):
            cable_id = mapping.get("cable_id", "")
            side = mapping.get("side", "")
            target = mapping.get("target_point_id", "")

            if side not in ("start", "end"):
                conflicts.append({"index": idx, "error": "INVALID_SIDE", "mapping": mapping})
                continue

            if cable_id not in cables:
                conflicts.append({"index": idx, "error": "CABLE_NOT_FOUND", "mapping": mapping})
                continue

            if target and target not in points:
                conflicts.append({"index": idx, "error": "INVALID_TARGET_AP", "mapping": mapping})
                continue

            key = (cable_id, side)
            if key in seen and seen[key] != target:
                conflicts.append({
                    "index": idx,
                    "error": "CONFLICTING_MAPPING",
                    "mapping": mapping,
                    "previous_target": seen[key],
                })
                continue
            seen[key] = target

            cmap = start_map if side == "start" else end_map
            before = cmap.get(cable_id, "")
            after = target
            if before == after:
                continue

            operations.append({
                "op": "remove" if after == "" else "replace",
                "path": f"/canvas/{'cable_start_ap' if side == 'start' else 'cable_end_ap'}/{cable_id}",
                "before": before,
                "after": after,
                "cable_id": cable_id,
                "side": side,
            })

        applicable = len(operations) > 0 and (not strict or len(conflicts) == 0)
        return {
            "applicable": applicable,
            "strict": strict,
            "operations": operations,
            "conflicts": conflicts,
        }

    def _default_bom_metadata() -> dict:
        return {
            "version": 1,
            "sections": {
                "hk_bom_rows": True,
                "cable_bom_rows": True,
                "ap_bom_rows": True,
                "hkv_line_bom_rows": True,
                "uv_device_bom_rows": True,
                "uv_busbar_bom_rows": True,
                "custom_bom_rows": True,
            },
            "rounding": {
                "length_m": 1,
                "quantity": 1,
            },
            "last_generated_at": "",
            "item_catalog": {},
            "custom_items": [],
        }

    def _sanitize_bom_metadata(value) -> dict:
        default = _default_bom_metadata()
        if not isinstance(value, dict):
            return default

        sections = dict(default["sections"])
        for key, section_value in value.get("sections", {}).items():
            sections[str(key)] = bool(section_value)

        rounding = dict(default["rounding"])
        for key, rounding_value in value.get("rounding", {}).items():
            try:
                rounding[str(key)] = max(0, min(6, int(rounding_value)))
            except Exception:
                continue

        try:
            version = max(1, int(value.get("version", default["version"])))
        except Exception:
            version = default["version"]

        item_catalog: dict[str, dict] = {}
        raw_catalog = value.get("item_catalog", {})
        if isinstance(raw_catalog, dict):
            for raw_key, raw_value in raw_catalog.items():
                key = str(raw_key or "").strip()
                if not key:
                    continue
                rv = raw_value if isinstance(raw_value, dict) else {}
                item_catalog[key] = {
                    "manufacturer": str(rv.get("manufacturer", "") or "").strip(),
                    "article_number": str(rv.get("article_number", "") or "").strip(),
                    "description_override": str(rv.get("description_override", "") or "").strip(),
                    "note": str(rv.get("note", "") or "").strip(),
                }

        custom_items: list[dict] = []
        raw_custom_items = value.get("custom_items", [])
        if isinstance(raw_custom_items, list):
            for index, raw_item in enumerate(raw_custom_items, start=1):
                if not isinstance(raw_item, dict):
                    continue
                custom_id = str(raw_item.get("custom_id", "") or "").strip() or f"BOM-{index}"
                section_key = str(raw_item.get("section_key", "custom_bom_rows") or "custom_bom_rows").strip()
                category = str(raw_item.get("category", "Manuell") or "Manuell").strip()
                item_type = str(raw_item.get("item_type", "custom") or "custom").strip()
                item_key = str(raw_item.get("key", custom_id) or custom_id).strip()
                description = str(raw_item.get("description", "") or "").strip()
                unit = str(raw_item.get("unit", "Stk") or "Stk").strip()
                try:
                    quantity = float(raw_item.get("quantity", 0.0) or 0.0)
                except Exception:
                    quantity = 0.0
                custom_items.append({
                    "custom_id": custom_id,
                    "section_key": section_key,
                    "category": category,
                    "item_type": item_type,
                    "key": item_key,
                    "description": description,
                    "unit": unit,
                    "quantity": quantity,
                    "manufacturer": str(raw_item.get("manufacturer", "") or "").strip(),
                    "article_number": str(raw_item.get("article_number", "") or "").strip(),
                    "note": str(raw_item.get("note", "") or "").strip(),
                })

        return {
            "version": version,
            "sections": sections,
            "rounding": rounding,
            "last_generated_at": str(value.get("last_generated_at", "") or "").strip(),
            "item_catalog": item_catalog,
            "custom_items": custom_items,
        }

    def _bom_catalog_key(item_type: str, key: str) -> str:
        return f"{str(item_type or '').strip()}|{str(key or '').strip()}"

    def _apply_bom_metadata(rows_by_section: dict[str, list[dict]], metadata: dict) -> dict[str, list[dict]]:
        catalog = metadata.get("item_catalog", {}) if isinstance(metadata, dict) else {}
        custom_items = metadata.get("custom_items", []) if isinstance(metadata, dict) else []

        enriched: dict[str, list[dict]] = {
            section: [dict(row) for row in rows]
            for section, rows in rows_by_section.items()
        }
        enriched.setdefault("custom_bom_rows", [])

        for section, rows in enriched.items():
            for row in rows:
                row.setdefault("manufacturer", "")
                row.setdefault("article_number", "")
                entry = {}
                if isinstance(catalog, dict):
                    item_type = str(row.get("item_type", "") or "")
                    item_key = str(row.get("key", "") or "")
                    entry = catalog.get(_bom_catalog_key(item_type, item_key), {})
                if isinstance(entry, dict):
                    manufacturer = str(entry.get("manufacturer", "") or "").strip()
                    article_number = str(entry.get("article_number", "") or "").strip()
                    description_override = str(entry.get("description_override", "") or "").strip()
                    if manufacturer:
                        row["manufacturer"] = manufacturer
                    if article_number:
                        row["article_number"] = article_number
                    if description_override:
                        row["description"] = description_override

        if isinstance(custom_items, list):
            for item in custom_items:
                if not isinstance(item, dict):
                    continue
                section_key = str(item.get("section_key", "custom_bom_rows") or "custom_bom_rows").strip()
                section_key = section_key or "custom_bom_rows"
                enriched.setdefault(section_key, [])
                enriched[section_key].append({
                    "category": str(item.get("category", "Manuell") or "Manuell").strip(),
                    "item_type": str(item.get("item_type", "custom") or "custom").strip(),
                    "key": str(item.get("key", item.get("custom_id", "")) or "").strip(),
                    "description": str(item.get("description", "") or "").strip(),
                    "unit": str(item.get("unit", "Stk") or "Stk").strip(),
                    "quantity": float(item.get("quantity", 0.0) or 0.0),
                    "manufacturer": str(item.get("manufacturer", "") or "").strip(),
                    "article_number": str(item.get("article_number", "") or "").strip(),
                    "note": str(item.get("note", "") or "").strip(),
                    "custom_id": str(item.get("custom_id", "") or "").strip(),
                })

        return enriched

    def _next_custom_bom_id(custom_items: list[dict]) -> str:
        existing: set[int] = set()
        for item in custom_items:
            if not isinstance(item, dict):
                continue
            value = str(item.get("custom_id", "") or "").strip()
            if not value.startswith("BOM-"):
                continue
            suffix = value.split("-", 1)[1]
            if suffix.isdigit():
                existing.add(int(suffix))
        n = 1
        while n in existing:
            n += 1
        return f"BOM-{n}"

    def _polyline_length_m(points: list, mm_per_px: float) -> float:
        if not isinstance(points, list) or len(points) < 2:
            return 0.0
        length_px = 0.0
        for idx in range(1, len(points)):
            x0, y0 = points[idx - 1]
            x1, y1 = points[idx]
            length_px += math.hypot(float(x1) - float(x0), float(y1) - float(y0))
        return length_px * mm_per_px / 1000.0

    def _safe_te_count(te_start: int, te_end: int) -> int:
        if te_end < te_start:
            te_start, te_end = te_end, te_start
        return max(0, (te_end - te_start + 1))

    def _collect_bom_data() -> dict:
        params = _state.data.get("params", {})
        canvas = _state.data.get("canvas", {})
        mm_per_px = float(canvas.get("mm_per_px", 1.0) or 1.0)

        hk_by_diameter: dict[float, float] = {}
        for circuit_id, cdata in params.get("circuits", {}).items():
            diameter = float(cdata.get("diameter", 0.0) or 0.0)
            route_m = _polyline_length_m(canvas.get("manual_routes", {}).get(circuit_id, []), mm_per_px)
            supply_m = _polyline_length_m(canvas.get("supply_lines", {}).get(circuit_id, []), mm_per_px)
            hk_by_diameter[diameter] = hk_by_diameter.get(diameter, 0.0) + route_m + supply_m

        hk_bom_rows = [
            {
                "category": "Heizrohr",
                "item_type": "heating_pipe",
                "key": f"{diameter:.1f}",
                "description": f"Heizrohr {diameter:.1f} mm",
                "unit": "m",
                "quantity": quantity,
                "meta": {"diameter_mm": diameter},
            }
            for diameter, quantity in sorted(hk_by_diameter.items())
        ]

        cable_by_type: dict[str, float] = {}
        for cable_id, cdata in params.get("elec_cables", {}).items():
            cable_type = str(cdata.get("cable_type", cdata.get("name", cable_id)) or "").strip() or "(unbekannt)"
            length_m = _polyline_length_m(canvas.get("elec_cables", {}).get(cable_id, []), mm_per_px)
            cable_by_type[cable_type] = cable_by_type.get(cable_type, 0.0) + length_m

        cable_bom_rows = [
            {
                "category": "Elektro-Kabel",
                "item_type": "elec_cable",
                "key": cable_type,
                "description": cable_type,
                "unit": "m",
                "quantity": quantity,
                "meta": {"cable_type": cable_type},
            }
            for cable_type, quantity in sorted(cable_by_type.items(), key=lambda kv: kv[0].lower())
        ]

        ap_by_type: dict[str, int] = {}
        for point_id, pdata in params.get("elec_points", {}).items():
            ap_type = str(
                pdata.get("builtin_symbol")
                or pdata.get("ap_type")
                or pdata.get("name")
                or point_id
            ).strip() or "(kein Typ)"
            ap_by_type[ap_type] = ap_by_type.get(ap_type, 0) + 1

        ap_bom_rows = [
            {
                "category": "Anschlusspunkte",
                "item_type": "ap",
                "key": ap_type,
                "description": ap_type,
                "unit": "Stk",
                "quantity": quantity,
                "meta": {"ap_type": ap_type},
            }
            for ap_type, quantity in sorted(ap_by_type.items(), key=lambda kv: kv[0].lower())
        ]

        hkv_line_by_type: dict[str, float] = {}
        for line_id, ldata in params.get("hkv_lines", {}).items():
            line_type = str(ldata.get("line_type") or ldata.get("name") or line_id).strip() or "(unbekannt)"
            length_m = _polyline_length_m(canvas.get("hkv_lines", {}).get(line_id, []), mm_per_px)
            hkv_line_by_type[line_type] = hkv_line_by_type.get(line_type, 0.0) + length_m

        hkv_line_bom_rows = [
            {
                "category": "HKV-Leitungen",
                "item_type": "hkv_line",
                "key": line_type,
                "description": line_type,
                "unit": "m",
                "quantity": quantity,
                "meta": {"line_type": line_type},
            }
            for line_type, quantity in sorted(hkv_line_by_type.items(), key=lambda kv: kv[0].lower())
        ]

        uv_device_summary: dict[tuple[str, str, str, str], dict] = {}
        uv_busbar_summary: dict[tuple[str, int, int], dict] = {}
        for point_id, pdata in params.get("elec_points", {}).items():
            uv_config = pdata.get("uv_config") or {}
            if not isinstance(uv_config, dict):
                continue
            uv_name = str(pdata.get("name") or point_id).strip() or point_id

            for slot in uv_config.get("slots", []) or []:
                if not isinstance(slot, dict):
                    continue
                device_type = str(slot.get("device_type", "") or "").strip() or "(leer)"
                te_size = max(1, int(slot.get("te_size", 1) or 1))
                manufacturer = str(slot.get("manufacturer", "") or "").strip()
                article_number = str(slot.get("article_number", "") or "").strip()
                key = (uv_name, device_type, manufacturer, article_number)
                if key not in uv_device_summary:
                    uv_device_summary[key] = {
                        "category": "UV-Geräte",
                        "item_type": "uv_device",
                        "key": device_type,
                        "uv": uv_name,
                        "description": device_type,
                        "unit": "Stk",
                        "quantity": 0,
                        "te_total": 0,
                        "manufacturer": manufacturer,
                        "article_number": article_number,
                    }
                uv_device_summary[key]["quantity"] += 1
                uv_device_summary[key]["te_total"] += te_size

            for busbar in uv_config.get("busbars", []) or []:
                if not isinstance(busbar, dict):
                    continue
                phase = str(busbar.get("phase", "") or "").strip()
                if not phase:
                    continue
                te_start = max(1, int(busbar.get("te_start", 1) or 1))
                te_end = max(1, int(busbar.get("te_end", 1) or 1))
                if te_end < te_start:
                    te_start, te_end = te_end, te_start
                te_count = _safe_te_count(te_start, te_end)
                key = (phase, te_start, te_end)
                if key not in uv_busbar_summary:
                    uv_busbar_summary[key] = {
                        "category": "UV-Phasenschienen",
                        "item_type": "uv_busbar",
                        "phase": phase,
                        "description": f"Phasenschiene {phase}",
                        "te_start": te_start,
                        "te_end": te_end,
                        "unit": "TE",
                        "quantity": 0,
                        "uv_count": 0,
                        "uv_names": set(),
                    }
                uv_busbar_summary[key]["quantity"] += te_count
                uv_busbar_summary[key]["uv_names"].add(uv_name)
                uv_busbar_summary[key]["uv_count"] = len(uv_busbar_summary[key]["uv_names"])

        uv_device_bom_rows = sorted(
            uv_device_summary.values(),
            key=lambda r: (str(r.get("uv", "")).lower(), str(r.get("description", "")).lower()),
        )
        uv_busbar_bom_rows = []
        for row in sorted(
            uv_busbar_summary.values(),
            key=lambda r: (
                str(r.get("phase", "")).lower(),
                int(r.get("te_start", 0) or 0),
                int(r.get("te_end", 0) or 0),
            ),
        ):
            row_copy = dict(row)
            row_copy["uv_names"] = ", ".join(sorted(row.get("uv_names", set())))
            uv_busbar_bom_rows.append(row_copy)

        rows_by_section = {
            "hk_bom_rows": hk_bom_rows,
            "cable_bom_rows": cable_bom_rows,
            "ap_bom_rows": ap_bom_rows,
            "hkv_line_bom_rows": hkv_line_bom_rows,
            "uv_device_bom_rows": uv_device_bom_rows,
            "uv_busbar_bom_rows": uv_busbar_bom_rows,
            "custom_bom_rows": [],
        }

        metadata = _sanitize_bom_metadata(params.get("bom"))
        rows_by_section = _apply_bom_metadata(rows_by_section, metadata)

        return {
            "rows_by_section": rows_by_section,
            "totals": {
                section: round(
                    sum(float(row.get("quantity", 0.0) or 0.0) for row in rows),
                    3,
                )
                for section, rows in rows_by_section.items()
            },
            "item_counts": {section: len(rows) for section, rows in rows_by_section.items()},
        }

    # ── Projekt-Management ────────────────────────────────────────

    @mcp.tool()
    def open_project(path: str = "", project_id: str = "") -> dict:
        """HRP-Projektdatei öffnen/laden oder aktives Projekt auswählen.

        Args:
            path: Pfad zur .hrp-Datei (absolut oder relativ)
            project_id: Bereits geladene Projekt-ID.
        """
        if path:
            return _state.load(path)
        if project_id:
            if _state.project_id == project_id:
                return {
                    "status": "ok",
                    "project_id": _state.project_id,
                    "revision": _state.get_revision().get("revision", {}),
                    "info": _state._summary(),
                }
            return {"error": "PROJECT_NOT_FOUND", "project_id": project_id}
        return {"error": "AMBIGUOUS_PROJECT_REF", "message": "Bitte path oder project_id angeben."}

    @mcp.tool()
    def save_project(path: str = "") -> dict:
        """Aktuelles Projekt speichern.

        Args:
            path: Dateipfad (.hrp). Leer = aktueller Projektpfad.
        """
        return _state.save(path)

    @mcp.tool()
    def reload_project_from_disk(project_id: str = "", force: bool = False) -> dict:
        """Projekt aus Datei neu laden und Session-Änderungen verwerfen.

        Args:
            project_id: Zu ladende Projekt-ID (leer = aktives Projekt)
            force: True erzwingt Reload trotz ungespeicherter Änderungen.
        """
        if not _state.path:
            return {"error": "PROJECT_NOT_OPEN"}
        if project_id and _state.project_id != project_id:
            return {"error": "PROJECT_NOT_FOUND", "project_id": project_id}
        if _state.dirty and not force:
            return {"error": "UNSAVED_CHANGES", "project_id": _state.project_id}

        had_local_changes = bool(_state.dirty)
        old_rev = _state.get_revision().get("revision", {})
        load_result = _state.load(str(_state.path))
        if load_result.get("error"):
            return load_result

        return {
            "project_id": _state.project_id,
            "old_revision": old_rev,
            "new_revision": _state.get_revision().get("revision", {}),
            "discarded_local_changes": had_local_changes,
            "status": "reloaded",
        }

    @mcp.tool()
    def get_project_revision(project_id: str = "") -> dict:
        """Aktuelle Projekt-Revision (hash/version/mtime) abrufen."""
        if project_id and _state.project_id != project_id:
            return {"error": "PROJECT_NOT_FOUND", "project_id": project_id}
        if not _state.project_id:
            return {"error": "PROJECT_NOT_OPEN"}
        return _state.get_revision()

    @mcp.tool()
    def assert_project_in_sync(project_id: str = "", expected_revision: str = "") -> dict:
        """Prüft, ob Session und Datei synchron sind (inkl. optionaler Revision)."""
        if project_id and _state.project_id != project_id:
            return {"error": "PROJECT_NOT_FOUND", "project_id": project_id}
        if not _state.project_id:
            return {"error": "PROJECT_NOT_OPEN"}
        return _state.assert_in_sync(expected_revision=expected_revision)

    @mcp.tool()
    def get_project_summary() -> dict:
        """Projektübersicht: Anzahl Elemente, Parameter, Maßstab."""
        p = _state.data.get("params", {})
        c = _state.data.get("canvas", {})
        return {
            "project_file": str(_state.path) if _state.path else None,
            "dirty": _state.dirty,
            "floor_plans": list(p.get("floorplans", {}).keys()),
            "circuit_count": len(p.get("circuits", {})),
            "circuit_ids": list(p.get("circuits", {}).keys()),
            "elec_point_count": len(p.get("elec_points", {})),
            "elec_point_ids": list(p.get("elec_points", {}).keys()),
            "elec_room_count": len(p.get("elec_rooms", {})),
            "elec_cable_count": len(p.get("elec_cables", {})),
            "hkv_count": len(p.get("hkv_points", {})),
            "t_supply": p.get("t_supply", 35.0),
            "t_return": p.get("t_return", 30.0),
            "t_norm_outdoor": p.get("t_norm_outdoor", -12.0),
            "mm_per_px": c.get("mm_per_px", 1.0),
        }

    @mcp.tool()
    def get_project_json() -> dict:
        """Vollständiges Projekt als JSON zurückgeben."""
        return _state.data

    @mcp.tool()
    def get_bom_metadata() -> dict:
        """Persistierte Stücklisten-Metadaten lesen (params.bom)."""
        meta = _state.data.setdefault("params", {}).get("bom")
        return _sanitize_bom_metadata(meta)

    @mcp.tool()
    def set_bom_metadata(metadata: dict, merge: bool = True) -> dict:
        """Stücklisten-Metadaten setzen (persistiert in params.bom)."""
        params = _state.data.setdefault("params", {})
        current = _sanitize_bom_metadata(params.get("bom"))
        base = copy.deepcopy(current) if merge else _default_bom_metadata()
        if isinstance(metadata, dict):
            for key, value in metadata.items():
                if key in ("sections", "rounding", "item_catalog") and isinstance(value, dict):
                    merged = dict(base.get(key, {}))
                    merged.update(value)
                    base[key] = merged
                elif key == "custom_items" and isinstance(value, list):
                    base[key] = value
                else:
                    base[key] = value
        params["bom"] = _sanitize_bom_metadata(base)
        _state.dirty = True
        return {
            "status": "ok",
            "bom": params["bom"],
        }

    @mcp.tool()
    def upsert_bom_catalog_entry(
        item_type: str,
        key: str,
        manufacturer: str = "",
        article_number: str = "",
        description_override: str = "",
        note: str = "",
    ) -> dict:
        """Hersteller-/Artikeldaten für eine BOM-Position setzen."""
        params = _state.data.setdefault("params", {})
        metadata = _sanitize_bom_metadata(params.get("bom"))
        catalog = dict(metadata.get("item_catalog", {}))
        catalog_key = _bom_catalog_key(item_type, key)
        if not catalog_key or catalog_key == "|":
            return {"error": "INVALID_ITEM_KEY"}
        catalog[catalog_key] = {
            "manufacturer": str(manufacturer or "").strip(),
            "article_number": str(article_number or "").strip(),
            "description_override": str(description_override or "").strip(),
            "note": str(note or "").strip(),
        }
        metadata["item_catalog"] = catalog
        params["bom"] = _sanitize_bom_metadata(metadata)
        _state.dirty = True
        return {
            "status": "ok",
            "catalog_key": catalog_key,
            "entry": catalog[catalog_key],
        }

    @mcp.tool()
    def add_manual_bom_item(
        category: str,
        description: str,
        unit: str,
        quantity: float,
        manufacturer: str = "",
        article_number: str = "",
        note: str = "",
        section_key: str = "custom_bom_rows",
        custom_id: str = "",
    ) -> dict:
        """Manuelle Stücklistenposition hinzufügen/überschreiben."""
        params = _state.data.setdefault("params", {})
        metadata = _sanitize_bom_metadata(params.get("bom"))
        items = list(metadata.get("custom_items", []))
        item_id = str(custom_id or "").strip() or _next_custom_bom_id(items)

        new_item = {
            "custom_id": item_id,
            "section_key": str(section_key or "custom_bom_rows").strip() or "custom_bom_rows",
            "category": str(category or "Manuell").strip() or "Manuell",
            "item_type": "custom",
            "key": item_id,
            "description": str(description or "").strip(),
            "unit": str(unit or "Stk").strip() or "Stk",
            "quantity": float(quantity or 0.0),
            "manufacturer": str(manufacturer or "").strip(),
            "article_number": str(article_number or "").strip(),
            "note": str(note or "").strip(),
        }

        replaced = False
        for idx, existing in enumerate(items):
            if str(existing.get("custom_id", "") or "").strip() == item_id:
                items[idx] = new_item
                replaced = True
                break
        if not replaced:
            items.append(new_item)

        metadata["custom_items"] = items
        params["bom"] = _sanitize_bom_metadata(metadata)
        _state.dirty = True
        return {
            "status": "ok",
            "action": "updated" if replaced else "created",
            "item": new_item,
        }

    @mcp.tool()
    def remove_manual_bom_item(custom_id: str) -> dict:
        """Manuelle Stücklistenposition löschen."""
        item_id = str(custom_id or "").strip()
        if not item_id:
            return {"error": "INVALID_CUSTOM_ID"}
        params = _state.data.setdefault("params", {})
        metadata = _sanitize_bom_metadata(params.get("bom"))
        items = list(metadata.get("custom_items", []))
        before = len(items)
        items = [
            item for item in items
            if str(item.get("custom_id", "") or "").strip() != item_id
        ]
        if len(items) == before:
            return {"error": "CUSTOM_ITEM_NOT_FOUND", "custom_id": item_id}

        metadata["custom_items"] = items
        params["bom"] = _sanitize_bom_metadata(metadata)
        _state.dirty = True
        return {"status": "ok", "deleted": item_id}

    @mcp.tool()
    def get_bom_summary() -> dict:
        """Aggregierte Stückliste inkl. UV-Geräte und UV-Phasenschienen."""
        bom = _collect_bom_data()
        metadata = _sanitize_bom_metadata(_state.data.setdefault("params", {}).get("bom"))
        generated_at = datetime.now(tz=UTC).isoformat()
        return {
            "generated_at": generated_at,
            "metadata": metadata,
            "summary": bom,
        }

    @mcp.tool()
    def export_bom_report(format: str = "json") -> dict:
        """Stücklisten-Report als JSON oder Markdown erzeugen."""
        payload = get_bom_summary()
        if payload.get("error"):
            return payload

        if format == "json":
            return {
                "format": "json",
                "generated_at": payload.get("generated_at", ""),
                "report": payload,
            }

        if format == "md":
            summary = payload.get("summary", {})
            totals = summary.get("totals", {})
            item_counts = summary.get("item_counts", {})
            lines = [
                "# Stückliste",
                "",
                f"- generated_at: {payload.get('generated_at', '')}",
                f"- sections: {len(summary.get('rows_by_section', {}))}",
            ]
            for section in [
                "hk_bom_rows",
                "cable_bom_rows",
                "ap_bom_rows",
                "hkv_line_bom_rows",
                "uv_device_bom_rows",
                "uv_busbar_bom_rows",
                "custom_bom_rows",
            ]:
                lines.append(
                    f"- {section}: items={item_counts.get(section, 0)}, total={totals.get(section, 0)}"
                )
            return {
                "format": "md",
                "generated_at": payload.get("generated_at", ""),
                "report": "\n".join(lines),
                "summary": summary,
            }

        return {"error": "UNSUPPORTED_FORMAT", "format": format}

    # ── Lese-Tools ────────────────────────────────────────────────

    @mcp.tool()
    def list_circuits() -> list[dict]:
        """Liste aller Heizkreise mit Parametern."""
        p = _state.data.get("params", {})
        c = _state.data.get("canvas", {})
        result = []
        for cid, cdata in p.get("circuits", {}).items():
            entry = dict(cdata)
            poly = c.get("polygons", {}).get(cid, [])
            entry["polygon_points"] = len(poly)
            entry["has_polygon"] = len(poly) >= 3
            route = c.get("manual_routes", {}).get(cid, [])
            entry["has_route"] = len(route) >= 2
            result.append(entry)
        return result

    @mcp.tool()
    def list_elec_points() -> list[dict]:
        """Liste aller Elektro-Anschlusspunkte."""
        p = _state.data.get("params", {})
        c = _state.data.get("canvas", {})
        result = []
        for pid, pdata in p.get("elec_points", {}).items():
            entry = dict(pdata)
            pos = c.get("elec_points", {}).get(pid)
            entry["canvas_position"] = list(pos) if pos else None
            result.append(entry)
        return result

    @mcp.tool()
    def list_hkvs() -> list[dict]:
        """Liste aller Heizkreisverteiler."""
        p = _state.data.get("params", {})
        c = _state.data.get("canvas", {})
        result = []
        for hid, hdata in p.get("hkv_points", {}).items():
            entry = dict(hdata)
            pos = c.get("hkv_points", {}).get(hid)
            entry["canvas_position"] = list(pos) if pos else None
            result.append(entry)
        return result

    @mcp.tool()
    def list_elec_rooms() -> list[dict]:
        """Liste aller Elektro-Räume."""
        return list(_state.data.get("params", {}).get("elec_rooms", {}).values())

    @mcp.tool()
    def list_elec_cables() -> list[dict]:
        """Liste aller Elektro-Kabel."""
        p = _state.data.get("params", {})
        c = _state.data.get("canvas", {})
        result = []
        for eid, edata in p.get("elec_cables", {}).items():
            entry = dict(edata)
            entry["start_ap"] = c.get("cable_start_ap", {}).get(eid, "")
            entry["end_ap"] = c.get("cable_end_ap", {}).get(eid, "")
            result.append(entry)
        return result

    @mcp.tool()
    def list_floor_plans() -> list[dict]:
        """Liste aller Grundriss-Layer."""
        return _state.data.get("canvas", {}).get("floor_plans", [])

    @mcp.tool()
    def list_texts() -> list[dict]:
        """Liste aller Text-Annotationen."""
        return list(_state.data.get("params", {}).get("text_annotations", {}).values())

    # ── Heizkreis-Tools ───────────────────────────────────────────

    @mcp.tool()
    def add_circuit(
        name: str,
        polygon: list[list[float]],
        floor_plan_id: str = "",
        color: str = "#2a9d8f",
        diameter: float = 16.0,
        spacing: float = 150.0,
        wall_dist: float = 200.0,
        room_temp: float = 20.0,
        floor_covering: str = "Fliesen / Keramik",
    ) -> dict:
        """Neuen Heizkreis mit Raumpolygon hinzufügen.

        Args:
            name: Anzeigename (z.B. 'Wohnzimmer', 'Badezimmer')
            polygon: Raumpolygon [[x1,y1], [x2,y2], ...] in Canvas-Pixeln (min. 3 Punkte)
            floor_plan_id: Grundriss-ID (z.B. 'grundriss-1')
            color: Hex-Farbe (#rrggbb)
            diameter: Rohrdurchmesser in mm
            spacing: Verlegeabstand in mm
            wall_dist: Wandabstand in mm
            room_temp: Raumtemperatur °C
            floor_covering: Bodenbelag
        """
        if len(polygon) < 3:
            return {"error": "Polygon muss mindestens 3 Punkte haben."}

        circuits = _state.data["params"].setdefault("circuits", {})
        cid = _state._next_id("HK", circuits)

        circuits[cid] = {
            "circuit_id": cid,
            "floor_plan_id": floor_plan_id,
            "name": name,
            "color": color,
            "diameter": diameter,
            "spacing": spacing,
            "wall_dist": wall_dist,
            "visible": True,
            "label_visible": True,
            "label_size": 12.0,
            "room_temp": room_temp,
            "floor_covering": floor_covering,
            "distributor": "",
        }
        _state.data["canvas"].setdefault("polygons", {})[cid] = polygon
        _state.data["canvas"].setdefault("start_points", {})[cid] = polygon[0]
        _state.dirty = True
        return {"circuit_id": cid, "status": "ok", "name": name}

    @mcp.tool()
    def modify_circuit(
        circuit_id: str,
        name: str | None = None,
        color: str | None = None,
        diameter: float | None = None,
        spacing: float | None = None,
        wall_dist: float | None = None,
        room_temp: float | None = None,
        floor_covering: str | None = None,
    ) -> dict:
        """Heizkreis-Parameter ändern.

        Args:
            circuit_id: Heizkreis-ID (z.B. 'HK-1')
        """
        circuits = _state.data["params"].get("circuits", {})
        if circuit_id not in circuits:
            return {"error": f"Heizkreis '{circuit_id}' nicht gefunden."}
        c = circuits[circuit_id]
        if name is not None: c["name"] = name
        if color is not None: c["color"] = color
        if diameter is not None: c["diameter"] = diameter
        if spacing is not None: c["spacing"] = spacing
        if wall_dist is not None: c["wall_dist"] = wall_dist
        if room_temp is not None: c["room_temp"] = room_temp
        if floor_covering is not None: c["floor_covering"] = floor_covering
        _state.dirty = True
        return {"circuit_id": circuit_id, "status": "modified"}

    @mcp.tool()
    def delete_circuit(circuit_id: str) -> dict:
        """Heizkreis löschen.

        Args:
            circuit_id: ID (z.B. 'HK-1')
        """
        circuits = _state.data["params"].get("circuits", {})
        if circuit_id not in circuits:
            return {"error": f"Heizkreis '{circuit_id}' nicht gefunden."}
        del circuits[circuit_id]
        canvas = _state.data["canvas"]
        for key in ["polygons", "start_points", "manual_routes",
                     "route_wall_dist_px", "route_line_dist_px", "supply_lines"]:
            canvas.get(key, {}).pop(circuit_id, None)
        _state.dirty = True
        return {"circuit_id": circuit_id, "status": "deleted"}

    @mcp.tool()
    def set_circuit_polygon(circuit_id: str, polygon: list[list[float]]) -> dict:
        """Raumpolygon eines Heizkreises setzen/ersetzen.

        Args:
            circuit_id: Heizkreis-ID
            polygon: Neues Polygon [[x,y], ...] in Canvas-Pixeln
        """
        if circuit_id not in _state.data["params"].get("circuits", {}):
            return {"error": f"Heizkreis '{circuit_id}' nicht gefunden."}
        if len(polygon) < 3:
            return {"error": "Mindestens 3 Punkte nötig."}
        _state.data["canvas"]["polygons"][circuit_id] = polygon
        _state.dirty = True
        return {"circuit_id": circuit_id, "status": "ok"}

    # ── Elektro-Tools ─────────────────────────────────────────────

    @mcp.tool()
    def add_elec_point(
        name: str,
        x: float,
        y: float,
        floor_plan_id: str = "",
        color: str = "#4fc3f7",
        builtin_symbol: str = "Steckdose",
        width: float = 30.0,
        height: float = 30.0,
        position: str = "Wand",
        height_from_floor: float = 30.0,
        note: str = "",
    ) -> dict:
        """Elektro-Anschlusspunkt platzieren.

        Args:
            name: Anzeigename
            x: X-Position in Canvas-Pixeln
            y: Y-Position in Canvas-Pixeln
        """
        points = _state.data["params"].setdefault("elec_points", {})
        pid = _state._next_id("AP", points)

        points[pid] = {
            "point_id": pid,
            "floor_plan_id": floor_plan_id,
            "name": name,
            "color": color,
            "width": width,
            "height": height,
            "icon_path": "",
            "builtin_symbol": builtin_symbol,
            "visible": True,
            "label_visible": True,
            "label_size": 12.0,
            "position": position,
            "height_from_floor": height_from_floor,
            "smarthome_device": "",
            "smarthome_device_color": "",
            "note": note,
        }
        _state.data["canvas"].setdefault("elec_points", {})[pid] = [x, y]
        _state.dirty = True
        return {"point_id": pid, "status": "ok"}

    @mcp.tool()
    def modify_elec_point(
        point_id: str,
        name: str | None = None,
        x: float | None = None,
        y: float | None = None,
        color: str | None = None,
        builtin_symbol: str | None = None,
        note: str | None = None,
    ) -> dict:
        """Anschlusspunkt-Parameter ändern."""
        pts = _state.data["params"].get("elec_points", {})
        if point_id not in pts:
            return {"error": f"AP '{point_id}' nicht gefunden."}
        p = pts[point_id]
        if name is not None: p["name"] = name
        if color is not None: p["color"] = color
        if builtin_symbol is not None: p["builtin_symbol"] = builtin_symbol
        if note is not None: p["note"] = note
        if x is not None or y is not None:
            pos = _state.data["canvas"].get("elec_points", {}).get(point_id, [0, 0])
            if x is not None: pos[0] = x
            if y is not None: pos[1] = y
            _state.data["canvas"]["elec_points"][point_id] = pos
        _state.dirty = True
        return {"point_id": point_id, "status": "modified"}

    @mcp.tool()
    def delete_elec_point(point_id: str) -> dict:
        """Anschlusspunkt löschen."""
        pts = _state.data["params"].get("elec_points", {})
        if point_id not in pts:
            return {"error": f"AP '{point_id}' nicht gefunden."}
        del pts[point_id]
        _state.data["canvas"].get("elec_points", {}).pop(point_id, None)
        _state.data["canvas"].get("elec_point_size_px", {}).pop(point_id, None)
        _state.dirty = True
        return {"point_id": point_id, "status": "deleted"}

    # ── Heizungsparameter ─────────────────────────────────────────

    @mcp.tool()
    def set_heating_params(
        t_supply: float | None = None,
        t_return: float | None = None,
        t_norm_outdoor: float | None = None,
    ) -> dict:
        """Globale Heizungsparameter ändern.

        Args:
            t_supply: Vorlauftemperatur °C (20–90)
            t_return: Rücklauftemperatur °C (15–80)
            t_norm_outdoor: Normaußentemperatur °C (-30 bis 5)
        """
        p = _state.data["params"]
        if t_supply is not None:
            p["t_supply"] = max(20, min(90, t_supply))
        if t_return is not None:
            p["t_return"] = max(15, min(80, t_return))
        if t_norm_outdoor is not None:
            p["t_norm_outdoor"] = max(-30, min(5, t_norm_outdoor))
        _state.dirty = True
        return {
            "status": "ok",
            "t_supply": p["t_supply"],
            "t_return": p["t_return"],
            "t_norm_outdoor": p["t_norm_outdoor"],
        }

    # ── Heizlast-Berechnung ───────────────────────────────────────

    @mcp.tool()
    def calculate_heating(circuit_id: str) -> dict:
        """Heizlast für einen Heizkreis berechnen (DIN EN 1264).

        Args:
            circuit_id: Heizkreis-ID (z.B. 'HK-1')
        """
        params = _state.data["params"]
        circuits = params.get("circuits", {})
        canvas = _state.data["canvas"]

        if circuit_id not in circuits:
            return {"error": f"Heizkreis '{circuit_id}' nicht gefunden."}

        circ = circuits[circuit_id]
        poly = canvas.get("polygons", {}).get(circuit_id, [])
        if len(poly) < 3:
            return {"error": "Kein gültiges Polygon vorhanden."}

        try:
            from logic.heating_calc import calc_circuit
        except ImportError:
            return {"error": "heating_calc Modul nicht verfügbar."}

        mm_per_px = canvas.get("mm_per_px", 1.0)
        # Fläche berechnen (Shoelace)
        n = len(poly)
        area_px = abs(sum(
            poly[i][0] * poly[(i+1) % n][1] - poly[(i+1) % n][0] * poly[i][1]
            for i in range(n)
        )) / 2.0
        area_m2 = area_px * (mm_per_px ** 2) / 1e6

        # Rohrlänge schätzen
        spacing_m = circ.get("spacing", 150) / 1000
        pipe_length = area_m2 / spacing_m if spacing_m > 0 else 0

        result = calc_circuit(
            t_supply=params.get("t_supply", 35.0),
            t_return=params.get("t_return", 30.0),
            room_temp=circ.get("room_temp", 20.0),
            spacing_mm=circ.get("spacing", 150),
            diameter_mm=circ.get("diameter", 16),
            area_m2=area_m2,
            pipe_length_m=pipe_length,
            floor_covering=circ.get("floor_covering", "Fliesen / Keramik"),
        )
        result["circuit_id"] = circuit_id
        result["area_m2"] = round(area_m2, 2)
        result["pipe_length_m"] = round(pipe_length, 1)
        return result

    @mcp.tool()
    def calculate_all_circuits() -> list[dict]:
        """Heizlast aller Heizkreise berechnen."""
        results = []
        for cid in _state.data["params"].get("circuits", {}):
            results.append(calculate_heating(cid))
        return results

    # ── Validierung ───────────────────────────────────────────────

    @mcp.tool()
    def validate_project(categories: list[str] | None = None) -> dict:
        """Projekt validieren (kategoriespezifisch).

        Args:
            categories: z.B. ['schema'], ['ref'] oder ['schema','ref'].
                        Leer/None = ['schema','ref'].
        """
        selected = categories or ["schema", "ref"]
        unsupported = [c for c in selected if c not in ("schema", "ref")]
        if unsupported:
            return {"error": "UNKNOWN_CATEGORY", "categories": unsupported}

        by_category: dict[str, list] = {}
        errors: list = []
        warnings: list = []

        if "schema" in selected:
            try:
                import jsonschema
                schema_path = BASE_DIR / "hrp_schema.json"
                if not schema_path.exists():
                    schema_errors = [{"path": "", "message": "Schema nicht gefunden."}]
                else:
                    schema = json.loads(schema_path.read_text(encoding="utf-8"))
                    validator = jsonschema.Draft202012Validator(schema)
                    schema_errors = [
                        {
                            "path": "/".join(str(s) for s in e.absolute_path),
                            "message": e.message,
                        }
                        for e in validator.iter_errors(_state.data)
                    ]
                by_category["schema"] = schema_errors
                errors.extend(schema_errors)
            except ImportError:
                return {"error": "jsonschema-Paket nicht installiert."}
            except Exception as e:
                by_category["schema"] = [{"path": "", "message": str(e)}]
                errors.append({"path": "", "message": str(e)})

        if "ref" in selected:
            ref_issues = _collect_reference_issues(scope="all", include_resolvable=False)
            by_category["ref"] = ref_issues
            for issue in ref_issues:
                if issue.get("severity") == "warning":
                    warnings.append(issue)
                else:
                    errors.append(issue)

        report = {
            "valid": len(errors) == 0,
            "categories": selected,
            "issues_count": {
                "total": sum(len(v) for v in by_category.values()),
                **{k: len(v) for k, v in by_category.items()},
            },
            "issues_by_category": by_category,
            "errors": errors,
            "warnings": warnings,
        }
        _state._last_validation_report = report
        return report

    @mcp.tool()
    def get_reference_issues(
        scope: str = "elec_cables",
        include_resolvable: bool = True,
    ) -> dict:
        """Fokussierte Referenz-Issues für Kabel-Endpunkte abrufen."""
        if scope not in ("elec_cables", "all"):
            return {"error": "UNSUPPORTED_SCOPE", "scope": scope}
        issues = _collect_reference_issues(
            scope=scope,
            include_resolvable=include_resolvable,
        )
        return {
            "scope": scope,
            "include_resolvable": include_resolvable,
            "issues": issues,
            "count": len(issues),
        }

    @mcp.tool()
    def suggest_endpoint_mapping(
        cable_id: str,
        side: str,
        strategy: list[str] | None = None,
    ) -> dict:
        """Kandidaten für start/end AP-Mapping eines Kabels vorschlagen."""
        if side not in ("start", "end"):
            return {"error": "INVALID_SIDE", "side": side}
        cables = _state.data.get("params", {}).get("elec_cables", {})
        if cable_id not in cables:
            return {"error": "CABLE_NOT_FOUND", "cable_id": cable_id}

        cmap_key = "cable_start_ap" if side == "start" else "cable_end_ap"
        current_value = _state.data.get("canvas", {}).get(cmap_key, {}).get(cable_id, "")
        candidates = _resolve_endpoint_candidates(
            cable_id,
            side,
            current_value,
            strategy=strategy,
        )
        return {
            "cable_id": cable_id,
            "side": side,
            "value": current_value,
            "strategy": strategy or ["exact_id", "exact_name", "normalized_name", "nearest_geometry"],
            "candidates": candidates,
        }

    @mcp.tool()
    def bulk_suggest_endpoint_mappings(
        missing_or_invalid: bool = True,
        confidence_threshold: float = 0.8,
        strategy: list[str] | None = None,
    ) -> dict:
        """Bulk-Vorschläge für fehlende/ungültige Kabel-Endpunkte erzeugen."""
        issues = _collect_reference_issues(scope="elec_cables", include_resolvable=False)
        suggestions = []
        skipped = []

        for issue in issues:
            if missing_or_invalid and issue.get("reason") not in (
                "missing", "not_found", "name_instead_of_id", "ambiguous_name", "invalid_type"
            ):
                continue
            cable_id = issue.get("cable_id", "")
            side = issue.get("side", "")
            val = issue.get("value")

            candidates = _resolve_endpoint_candidates(
                cable_id,
                side,
                val,
                strategy=strategy,
            )
            if not candidates:
                skipped.append({"issue_id": issue.get("issue_id"), "reason": "NO_CANDIDATES"})
                continue

            top = candidates[0]
            second = candidates[1] if len(candidates) > 1 else None
            ambiguous = second is not None and abs(top["confidence"] - second["confidence"]) < 0.05

            if top["confidence"] >= confidence_threshold and not ambiguous:
                suggestions.append({
                    "cable_id": cable_id,
                    "side": side,
                    "target_point_id": top["point_id"],
                    "source_issue_id": issue.get("issue_id"),
                    "confidence": top["confidence"],
                    "reason": top.get("reason", ""),
                    "strategy": top.get("strategy", ""),
                })
            else:
                skipped.append({
                    "issue_id": issue.get("issue_id"),
                    "reason": "LOW_CONFIDENCE" if not ambiguous else "AMBIGUOUS_TOP_CANDIDATE",
                    "top_confidence": top["confidence"],
                })

        suggestions.sort(key=lambda x: (x["cable_id"], x["side"], -x["confidence"]))
        return {
            "confidence_threshold": confidence_threshold,
            "strategy": strategy or ["exact_id", "exact_name", "normalized_name", "nearest_geometry"],
            "suggestions": suggestions,
            "skipped": skipped,
            "count": len(suggestions),
        }

    @mcp.tool()
    def preview_endpoint_patch(mappings: list[dict], strict: bool = True) -> dict:
        """Patch auf Kabel-Endpunkte simulieren (Diff/Konflikte/Impact)."""
        current_revision = _state.get_revision().get("revision", {}).get("hash", "")
        delta = _build_endpoint_delta(mappings, strict=strict)

        simulated = copy.deepcopy(_state.data)
        for op in delta["operations"]:
            side_map = "cable_start_ap" if op.get("side") == "start" else "cable_end_ap"
            cmap = simulated.setdefault("canvas", {}).setdefault(side_map, {})
            cable_id = op.get("cable_id", "")
            after = op.get("after", "")
            if after:
                cmap[cable_id] = after
            else:
                cmap.pop(cable_id, None)

        after_revision = _state._stable_hash(simulated)
        changed_cables = sorted({op.get("cable_id", "") for op in delta["operations"]})
        preview_id = hashlib.sha1(
            json.dumps({"rev": current_revision, "mappings": mappings}, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]

        return {
            "preview_id": preview_id,
            "conflicts": delta["conflicts"],
            "impact": {
                "changed_operations": len(delta["operations"]),
                "changed_cables": len(changed_cables),
                "cable_ids": changed_cables,
            },
            "delta": {
                "before_revision": current_revision,
                "after_revision": after_revision,
                "operations": delta["operations"],
            },
            "applicable": delta["applicable"],
            "strict": strict,
        }

    @mcp.tool()
    def apply_endpoint_patch(
        mappings: list[dict],
        mode: str = "transactional",
        strict: bool = True,
        expected_revision: str = "",
    ) -> dict:
        """Patch transaktional anwenden und maschinenlesbares Delta zurückgeben."""
        if mode != "transactional":
            return {"error": "UNSUPPORTED_MODE", "mode": mode}

        sync = _state.assert_in_sync(expected_revision=expected_revision)
        if not sync.get("in_sync", False):
            return {"error": sync.get("drift_reason", "REVISION_MISMATCH"), "sync": sync}

        preview = preview_endpoint_patch(mappings=mappings, strict=strict)
        if strict and preview.get("conflicts"):
            return {
                "error": "CONFLICTS_PRESENT",
                "conflicts": preview.get("conflicts", []),
                "delta": preview.get("delta", {}),
            }

        operations = preview.get("delta", {}).get("operations", [])
        if not operations:
            return {
                "transaction_id": None,
                "committed": False,
                "message": "NO_CHANGES",
                "delta": preview.get("delta", {}),
            }

        before_data = copy.deepcopy(_state.data)
        before_revision = _state.get_revision().get("revision", {}).get("hash", "")

        for op in operations:
            side = op.get("side")
            side_map = "cable_start_ap" if side == "start" else "cable_end_ap"
            cmap = _state.data.setdefault("canvas", {}).setdefault(side_map, {})
            cable_id = op.get("cable_id", "")
            after = op.get("after", "")
            if after:
                cmap[cable_id] = after
            else:
                cmap.pop(cable_id, None)

        _state.dirty = True
        after_revision = _state.get_revision().get("revision", {}).get("hash", "")
        delta = {
            "before_revision": before_revision,
            "after_revision": after_revision,
            "operations": operations,
        }

        transaction_id = _state.push_transaction(
            before_data=before_data,
            delta=delta,
            before_revision=before_revision,
            after_revision=after_revision,
        )

        return {
            "transaction_id": transaction_id,
            "committed": True,
            "new_revision": _state.get_revision().get("revision", {}),
            "delta": delta,
            "changed_entities": {
                "elec_cables": sorted({op.get("cable_id", "") for op in operations}),
            },
        }

    @mcp.tool()
    def rollback_last_patch(transaction_id: str = "") -> dict:
        """Letzte (oder angegebene) transaktionale Änderung zurückrollen."""
        return _state.rollback_transaction(transaction_id=transaction_id)

    @mcp.tool()
    def list_cables(
        missing_start: bool = False,
        missing_end: bool = False,
        invalid_ref: bool = False,
    ) -> dict:
        """Kabel mit optionalem Referenz-Filter auflisten."""
        params = _state.data.get("params", {})
        canvas = _state.data.get("canvas", {})
        points = params.get("elec_points", {})
        cables = params.get("elec_cables", {})
        start_map = canvas.get("cable_start_ap", {})
        end_map = canvas.get("cable_end_ap", {})

        result = []
        for cable_id, cdata in sorted(cables.items()):
            start_val = start_map.get(cable_id, "")
            end_val = end_map.get(cable_id, "")
            is_missing_start = start_val == ""
            is_missing_end = end_val == ""
            is_invalid_ref = (
                (start_val != "" and start_val not in points)
                or (end_val != "" and end_val not in points)
            )

            if missing_start and not is_missing_start:
                continue
            if missing_end and not is_missing_end:
                continue
            if invalid_ref and not is_invalid_ref:
                continue

            result.append({
                "cable_id": cable_id,
                "name": cdata.get("name", cable_id),
                "start_ap": start_val,
                "end_ap": end_val,
                "missing_start": is_missing_start,
                "missing_end": is_missing_end,
                "invalid_ref": is_invalid_ref,
            })

        return {
            "count": len(result),
            "cables": result,
        }

    @mcp.tool()
    def bulk_clear_invalid_endpoints(
        missing_start: bool = False,
        missing_end: bool = False,
        invalid_ref: bool = True,
        dry_run: bool = False,
    ) -> dict:
        """Ungültige Endpunkt-Referenzen in Bulk leeren."""
        cable_data = list_cables(
            missing_start=missing_start,
            missing_end=missing_end,
            invalid_ref=invalid_ref,
        ).get("cables", [])
        points = _state.data.get("params", {}).get("elec_points", {})
        mappings = []
        for c in cable_data:
            if c.get("start_ap") and c.get("start_ap") not in points:
                mappings.append({
                    "cable_id": c["cable_id"],
                    "side": "start",
                    "target_point_id": "",
                    "source_issue_id": f"clear-{c['cable_id']}-start",
                    "confidence": 1.0,
                })
            if c.get("end_ap") and c.get("end_ap") not in points:
                mappings.append({
                    "cable_id": c["cable_id"],
                    "side": "end",
                    "target_point_id": "",
                    "source_issue_id": f"clear-{c['cable_id']}-end",
                    "confidence": 1.0,
                })

        if dry_run:
            return preview_endpoint_patch(mappings=mappings, strict=False)

        return apply_endpoint_patch(mappings=mappings, mode="transactional", strict=False)

    @mcp.tool()
    def bulk_apply_resolver(
        strategy: list[str] | None = None,
        threshold: float = 0.8,
        dry_run: bool = True,
        strict: bool = True,
    ) -> dict:
        """Resolver in Bulk anwenden (optional Dry-Run)."""
        suggested = bulk_suggest_endpoint_mappings(
            missing_or_invalid=True,
            confidence_threshold=threshold,
            strategy=strategy,
        )
        mappings = suggested.get("suggestions", [])
        if dry_run:
            preview = preview_endpoint_patch(mappings=mappings, strict=strict)
            return {
                "dry_run": True,
                "proposed": len(mappings),
                "preview": preview,
                "skipped": suggested.get("skipped", []),
            }

        applied = apply_endpoint_patch(
            mappings=mappings,
            mode="transactional",
            strict=strict,
        )
        return {
            "dry_run": False,
            "proposed": len(mappings),
            "applied": applied,
            "skipped": suggested.get("skipped", []),
        }

    @mcp.tool()
    def export_validation_report(
        categories: list[str] | None = None,
        format: str = "json",
        include_before_after: bool = True,
    ) -> dict:
        """Validierungsreport exportieren (json/md)."""
        report = validate_project(categories=categories or ["ref"])
        if report.get("error"):
            return report

        revision = _state.get_revision().get("revision", {})
        payload = {
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "project_id": _state.project_id,
            "revision": revision,
            "report": report,
        }

        if include_before_after and _state._transactions:
            last_tx = _state._transactions[-1]
            payload["before_after"] = {
                "before_revision": last_tx.get("before_revision"),
                "after_revision": last_tx.get("after_revision"),
                "transaction_id": last_tx.get("transaction_id"),
            }

        if format == "json":
            return {
                "format": "json",
                "summary": report.get("issues_count", {}),
                "generated_at": payload["generated_at"],
                "report": payload,
            }

        if format == "md":
            summary = report.get("issues_count", {})
            md = [
                "# Validation Report",
                "",
                f"- project_id: {_state.project_id}",
                f"- generated_at: {payload['generated_at']}",
                f"- valid: {report.get('valid')}",
                f"- total issues: {summary.get('total', 0)}",
            ]
            for cat, count in summary.items():
                if cat == "total":
                    continue
                md.append(f"- {cat}: {count}")
            if include_before_after and payload.get("before_after"):
                ba = payload["before_after"]
                md.extend([
                    "",
                    "## Before/After",
                    f"- transaction_id: {ba.get('transaction_id')}",
                    f"- before_revision: {ba.get('before_revision')}",
                    f"- after_revision: {ba.get('after_revision')}",
                ])
            return {
                "format": "md",
                "summary": summary,
                "generated_at": payload["generated_at"],
                "report": "\n".join(md),
            }

        return {"error": "UNSUPPORTED_FORMAT", "format": format}

    # ── Grundriss-Tools ───────────────────────────────────────────

    @mcp.tool()
    def add_floor_plan(
        name: str,
        file_path: str = "",
        offset_x: float = 0,
        offset_y: float = 0,
        opacity: float = 1.0,
    ) -> dict:
        """Neuen Grundriss-Layer hinzufügen.

        Args:
            name: Anzeigename (z.B. 'Erdgeschoss')
            file_path: Pfad zum Bild (relativ zum Projekt)
        """
        fps = _state.data["params"].setdefault("floorplans", {})
        fp_id = _state._next_id("grundriss", fps)

        fps[fp_id] = {
            "fp_id": fp_id,
            "name": name,
            "file_path": file_path,
        }

        order = _state.data["params"].setdefault("floorplans_order", [])
        order.append(fp_id)

        floor_plans = _state.data["canvas"].setdefault("floor_plans", [])
        floor_plans.append({
            "fp_id": fp_id,
            "offset_x": offset_x,
            "offset_y": offset_y,
            "rotation": 0.0,
            "opacity": opacity,
            "visible": True,
            "mm_per_px": 1.0,
            "ref_length_mm": 1000.0,
            "fixed_width_mm": 0.0,
            "fixed_height_mm": 0.0,
            "polygon_color": "#8d99ae",
            "ref_line": [],
            "polygon": [],
        })
        _state.dirty = True
        return {"floor_plan_id": fp_id, "status": "ok", "name": name}

    # ── Text-Tools ────────────────────────────────────────────────

    @mcp.tool()
    def add_text(
        text: str,
        x: float,
        y: float,
        color: str = "#ffffff",
        font_size: float = 14.0,
    ) -> dict:
        """Text-Annotation hinzufügen.

        Args:
            text: Anzuzeigender Text
            x: X-Position in Canvas-Pixeln
            y: Y-Position in Canvas-Pixeln
        """
        annotations = _state.data["params"].setdefault("text_annotations", {})
        tid = _state._next_id("TEXT", annotations)
        annotations[tid] = {
            "text_id": tid,
            "text": text,
            "color": color,
            "font_size": font_size,
            "visible": True,
        }
        _state.data["canvas"].setdefault("text_annotations", {})[tid] = [x, y]
        _state.dirty = True
        return {"text_id": tid, "status": "ok"}

    @mcp.tool()
    def delete_text(text_id: str) -> dict:
        """Text-Annotation löschen."""
        annotations = _state.data["params"].get("text_annotations", {})
        if text_id not in annotations:
            return {"error": f"Text '{text_id}' nicht gefunden."}
        del annotations[text_id]
        _state.data["canvas"].get("text_annotations", {}).pop(text_id, None)
        _state.dirty = True
        return {"text_id": text_id, "status": "deleted"}

    # ── Resources ─────────────────────────────────────────────────

    @mcp.resource("hrp://schema")
    def get_schema() -> str:
        """JSON-Schema für .hrp-Projektdateien."""
        schema_path = BASE_DIR / "hrp_schema.json"
        if schema_path.exists():
            return schema_path.read_text(encoding="utf-8")
        return '{"error": "Schema nicht gefunden"}'

    @mcp.resource("hrp://instructions")
    def get_instructions() -> str:
        """Agenten-Anleitung."""
        doc_path = BASE_DIR / ".github" / "copilot-instructions.md"
        if doc_path.exists():
            return doc_path.read_text(encoding="utf-8")
        return "# Anleitung nicht gefunden"

    return mcp


# ── Entry Point ────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stderr,  # Logs auf stderr, stdio für MCP
    )

    # Auto-load: .hrp-Datei als Argument
    if len(sys.argv) > 1 and sys.argv[1].endswith(".hrp"):
        result = _state.load(sys.argv[1])
        logger.info(f"Projekt geladen: {result}")

    mcp = _create_stdio_mcp()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
