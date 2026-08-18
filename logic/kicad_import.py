"""KiCad schematic import helpers for cable discovery.

The first implementation step focuses on read-only discovery of hierarchical
sheet pins in KiCad 10 `.kicad_sch` files. These sheet pins carry the cable
identity in the provided example project, so they are the most stable anchor
for later import and re-sync flows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Iterable


_COUNT_X_SPEC_RE = re.compile(r"^(?P<count>\d+)x(?P<rest>.+)$")
_KBL_ENDPOINT_MARGIN_PX = 8.0
_KBL_ENDPOINT_DISTANCE_GAP_PX = 5.0


@dataclass(frozen=True)
class KiCadSheetPinRef:
    sheet_uuid: str
    sheet_name: str
    sheet_file: str
    pin_uuid: str
    pin_name_raw: str
    pin_direction: str
    hierarchy_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class KiCadImportWarning:
    code: str
    message: str
    source_path: str = ""


@dataclass(frozen=True)
class KiCadTextFieldMetadata:
    """Parsed AP-import metadata from HRouting text annotation."""
    text_id: str
    ap_name: str
    room: str = ""
    floor_plan_id: str = ""


@dataclass
class KiCadTextFieldCandidate:
    """Cable candidate derived from a text field annotation."""
    key: str
    source_metadata: KiCadTextFieldMetadata
    cable_name: str  # ap_name (directly from text field)
    matched_spec: str = ""  # auto-matched from nearby KiCad candidates
    best_matched_candidate: KiCadCableCandidate | None = None  # best match by name


@dataclass(frozen=True)
class KiCadRectFrame:
    uuid: str
    sheet_name: str
    sheet_file: str
    hierarchy_path: tuple[str, ...]
    x_min: float
    y_min: float
    x_max: float
    y_max: float


@dataclass(frozen=True)
class KiCadBusSegment:
    uuid: str
    sheet_name: str
    sheet_file: str
    hierarchy_path: tuple[str, ...]
    points: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class KiCadGroup:
    uuid: str
    name: str
    sheet_name: str
    sheet_file: str
    hierarchy_path: tuple[str, ...]
    members: tuple[str, ...]


@dataclass
class KiCadApGroupCandidate:
    key: str
    group_name: str
    group_uuid: str
    frame_uuid: str
    frame_bounds: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    sheet_file: str = ""
    hierarchy_path: tuple[str, ...] = ()
    bus_hits: list[KiCadBusSegment] = field(default_factory=list)


@dataclass
class KiCadCableCandidate:
    key: str
    base_name: str
    pin_name_raw: str
    spec_raw: str
    normalized_spec: str
    spec_kind: str
    pin_refs: list[KiCadSheetPinRef] = field(default_factory=list)
    local_labels: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class KiCadBusCableCandidate:
    key: str
    group_uuid: str
    group_name_raw: str
    base_name: str
    spec_raw: str
    normalized_spec: str
    spec_kind: str
    bus_uuid: str
    sheet_name: str
    sheet_file: str
    hierarchy_path: tuple[str, ...]
    points: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class KiCadApMatch:
    point_id: str
    point_name: str
    floor_plan_id: str
    score: int
    reason: str


@dataclass(frozen=True)
class KiCadFieldDiff:
    field: str
    current_value: str
    imported_value: str
    changed: bool


@dataclass
class KiCadImportPreview:
    candidate_key: str
    sync_key: str
    cable_name: str
    cable_type: str
    status: str
    source: str = "sheet_pin"  # 'sheet_pin' or 'text_field'
    existing_cable_id: str = ""
    existing_name: str = ""
    existing_type: str = ""
    ap_import_action: str = ""
    ap_match_status: str = "unmatched"
    ap_matches: list[KiCadApMatch] = field(default_factory=list)
    start_ap_group: str = ""
    end_ap_group: str = ""
    start_ap_id: str = ""
    end_ap_id: str = ""
    start_ap_status: str = ""
    end_ap_status: str = ""
    start_ap_diagnostic: str = ""
    end_ap_diagnostic: str = ""
    diffs: list[KiCadFieldDiff] = field(default_factory=list)


@dataclass
class KiCadScanResult:
    root_path: Path
    project_uuid: str
    candidates: dict[str, KiCadCableCandidate] = field(default_factory=dict)
    kbl_bus_candidates: dict[str, KiCadBusCableCandidate] = field(default_factory=dict)
    textfield_candidates: dict[str, KiCadTextFieldCandidate] = field(default_factory=dict)
    ap_group_candidates: dict[str, KiCadApGroupCandidate] = field(default_factory=dict)
    rectangles: dict[str, KiCadRectFrame] = field(default_factory=dict)
    bus_segments: dict[str, KiCadBusSegment] = field(default_factory=dict)
    groups: dict[str, KiCadGroup] = field(default_factory=dict)
    warnings: list[KiCadImportWarning] = field(default_factory=list)


def build_import_preview(
    scan_result: KiCadScanResult,
    existing_cables: Iterable[dict[str, Any]],
    elec_points: Iterable[dict[str, Any]],
) -> list[KiCadImportPreview]:
    existing_by_sync_key: dict[str, dict[str, Any]] = {}
    for cable in existing_cables:
        sync_key = str(cable.get("kicad_cable_key", "") or "").strip()
        if sync_key:
            existing_by_sync_key[sync_key] = cable

    previews: list[KiCadImportPreview] = []
    
    # Process KiCad sheet pin candidates
    for candidate in scan_result.candidates.values():
        if _is_kbl_candidate_name(candidate.pin_name_raw):
            continue
        sync_key = build_kicad_cable_key(scan_result.project_uuid, candidate)
        existing = existing_by_sync_key.get(sync_key)
        cable_name = candidate.base_name or candidate.pin_name_raw
        cable_type = candidate.normalized_spec or candidate.spec_raw
        ap_matches = suggest_ap_matches(candidate, elec_points)
        ap_match_status = ap_match_summary(ap_matches)
        ap_action = "AP-Auswahl prüfen"
        if ap_match_status == "matched":
            ap_action = "AP wiederverwenden"
        elif ap_match_status == "unmatched":
            ap_action = "AP neu anlegen"

        if existing is None:
            previews.append(
                KiCadImportPreview(
                    candidate_key=candidate.key,
                    sync_key=sync_key,
                    cable_name=cable_name,
                    cable_type=cable_type,
                    status="create",
                    source="sheet_pin",
                    ap_import_action=ap_action,
                    ap_match_status=ap_match_status,
                    ap_matches=ap_matches,
                    diffs=[
                        KiCadFieldDiff("name", "", cable_name, bool(cable_name)),
                        KiCadFieldDiff("type", "", cable_type, bool(cable_type)),
                    ],
                )
            )
            continue

        diffs = [
            KiCadFieldDiff(
                "name",
                str(existing.get("name", "") or ""),
                cable_name,
                str(existing.get("name", "") or "") != cable_name,
            ),
            KiCadFieldDiff(
                "type",
                str(existing.get("type", "") or ""),
                cable_type,
                str(existing.get("type", "") or "") != cable_type,
            ),
        ]
        changed = any(diff.changed for diff in diffs)
        previews.append(
            KiCadImportPreview(
                candidate_key=candidate.key,
                sync_key=sync_key,
                cable_name=cable_name,
                cable_type=cable_type,
                status="update" if changed else "unchanged",
                source="sheet_pin",
                existing_cable_id=str(existing.get("id", "") or ""),
                existing_name=str(existing.get("name", "") or ""),
                existing_type=str(existing.get("type", "") or ""),
                ap_import_action=ap_action,
                ap_match_status=ap_match_status,
                ap_matches=ap_matches,
                diffs=diffs,
            )
        )

    # Process KBL_* hierarchical label / pin-name candidates
    for candidate in scan_result.candidates.values():
        if not _is_kbl_candidate_name(candidate.pin_name_raw):
            continue
        if not str(candidate.spec_raw or "").strip():
            continue

        sync_key = build_kicad_cable_key(scan_result.project_uuid, candidate)
        existing = existing_by_sync_key.get(sync_key)
        cable_name = _strip_kbl_prefix(candidate.base_name or candidate.pin_name_raw)
        cable_type = candidate.normalized_spec or candidate.spec_raw

        (
            start_ap_id,
            start_ap_status,
            start_ap_matches,
            end_ap_id,
            end_ap_status,
            end_ap_matches,
        ) = _resolve_kbl_label_endpoints(candidate, scan_result, elec_points)
        ap_matches = _merge_unique_ap_matches(start_ap_matches, end_ap_matches)
        start_ap_diagnostic = _build_kbl_endpoint_diagnostic(
            side="Start",
            source="kbl_label",
            group_name="",
            group_status="",
            ap_status=start_ap_status,
            matches=start_ap_matches,
        )
        end_ap_diagnostic = _build_kbl_endpoint_diagnostic(
            side="Ziel",
            source="kbl_label",
            group_name="",
            group_status="",
            ap_status=end_ap_status,
            matches=end_ap_matches,
        )

        if start_ap_status == "matched":
            ap_match_status = "ambiguous"
            ap_action = "Start/Ziel-AP prüfen"
        elif start_ap_status == "ambiguous":
            ap_match_status = "ambiguous"
            ap_action = "Start/Ziel-AP prüfen"
        else:
            ap_match_status = "unmatched"
            ap_action = "Start/Ziel-AP prüfen"

        if existing is None:
            previews.append(
                KiCadImportPreview(
                    candidate_key=candidate.key,
                    sync_key=sync_key,
                    cable_name=cable_name,
                    cable_type=cable_type,
                    status="create",
                    source="kbl_label",
                    ap_import_action=ap_action,
                    ap_match_status=ap_match_status,
                    ap_matches=ap_matches,
                    start_ap_group="",
                    end_ap_group="",
                    start_ap_id=start_ap_id,
                    end_ap_id=end_ap_id,
                    start_ap_status=start_ap_status,
                    end_ap_status=end_ap_status,
                    start_ap_diagnostic=start_ap_diagnostic,
                    end_ap_diagnostic=end_ap_diagnostic,
                    diffs=[
                        KiCadFieldDiff("name", "", cable_name, bool(cable_name)),
                        KiCadFieldDiff("type", "", cable_type, bool(cable_type)),
                    ],
                )
            )
            continue

        diffs = [
            KiCadFieldDiff(
                "name",
                str(existing.get("name", "") or ""),
                cable_name,
                str(existing.get("name", "") or "") != cable_name,
            ),
            KiCadFieldDiff(
                "type",
                str(existing.get("type", "") or ""),
                cable_type,
                str(existing.get("type", "") or "") != cable_type,
            ),
        ]
        changed = any(diff.changed for diff in diffs)
        previews.append(
            KiCadImportPreview(
                candidate_key=candidate.key,
                sync_key=sync_key,
                cable_name=cable_name,
                cable_type=cable_type,
                status="update" if changed else "unchanged",
                source="kbl_label",
                existing_cable_id=str(existing.get("id", "") or ""),
                existing_name=str(existing.get("name", "") or ""),
                existing_type=str(existing.get("type", "") or ""),
                ap_import_action=ap_action,
                ap_match_status=ap_match_status,
                ap_matches=ap_matches,
                start_ap_group="",
                end_ap_group="",
                start_ap_id=start_ap_id,
                end_ap_id=end_ap_id,
                start_ap_status=start_ap_status,
                end_ap_status=end_ap_status,
                start_ap_diagnostic=start_ap_diagnostic,
                end_ap_diagnostic=end_ap_diagnostic,
                diffs=diffs,
            )
        )

    # Process KBL_* bus group candidates
    for candidate in scan_result.kbl_bus_candidates.values():
        sync_key = build_kicad_bus_cable_key(scan_result.project_uuid, candidate)
        existing = existing_by_sync_key.get(sync_key)
        cable_name = candidate.base_name or candidate.group_name_raw
        cable_type = candidate.normalized_spec or candidate.spec_raw
        start_ap_group, start_ap_group_status = _resolve_kbl_endpoint_group(
            scan_result,
            candidate.sheet_file,
            candidate.hierarchy_path,
            candidate.points[0],
        )
        end_ap_group, end_ap_group_status = _resolve_kbl_endpoint_group(
            scan_result,
            candidate.sheet_file,
            candidate.hierarchy_path,
            candidate.points[-1],
        )
        start_ap_id, start_ap_status, start_ap_matches = _resolve_kbl_endpoint_ap(start_ap_group, elec_points)
        end_ap_id, end_ap_status, end_ap_matches = _resolve_kbl_endpoint_ap(end_ap_group, elec_points)
        ap_matches = _merge_unique_ap_matches(start_ap_matches, end_ap_matches)
        start_ap_diagnostic = _build_kbl_endpoint_diagnostic(
            side="Start",
            source="kbl_bus",
            group_name=start_ap_group,
            group_status=start_ap_group_status,
            ap_status=start_ap_status,
            matches=start_ap_matches,
        )
        end_ap_diagnostic = _build_kbl_endpoint_diagnostic(
            side="Ziel",
            source="kbl_bus",
            group_name=end_ap_group,
            group_status=end_ap_group_status,
            ap_status=end_ap_status,
            matches=end_ap_matches,
        )

        if start_ap_status == "matched" and end_ap_status == "matched":
            ap_match_status = "matched"
            ap_action = "AP wiederverwenden"
        elif "ambiguous" in (start_ap_status, end_ap_status):
            ap_match_status = "ambiguous"
            ap_action = "Start/Ziel-AP prüfen"
        else:
            ap_match_status = "unmatched"
            ap_action = "Start/Ziel-AP prüfen"

        if existing is None:
            previews.append(
                KiCadImportPreview(
                    candidate_key=candidate.key,
                    sync_key=sync_key,
                    cable_name=cable_name,
                    cable_type=cable_type,
                    status="create",
                    source="kbl_bus",
                    ap_import_action=ap_action,
                    ap_match_status=ap_match_status,
                    ap_matches=ap_matches,
                    start_ap_group=start_ap_group,
                    end_ap_group=end_ap_group,
                    start_ap_id=start_ap_id,
                    end_ap_id=end_ap_id,
                    start_ap_status=start_ap_status,
                    end_ap_status=end_ap_status,
                    start_ap_diagnostic=start_ap_diagnostic,
                    end_ap_diagnostic=end_ap_diagnostic,
                    diffs=[
                        KiCadFieldDiff("name", "", cable_name, bool(cable_name)),
                        KiCadFieldDiff("type", "", cable_type, bool(cable_type)),
                    ],
                )
            )
            continue

        diffs = [
            KiCadFieldDiff(
                "name",
                str(existing.get("name", "") or ""),
                cable_name,
                str(existing.get("name", "") or "") != cable_name,
            ),
            KiCadFieldDiff(
                "type",
                str(existing.get("type", "") or ""),
                cable_type,
                str(existing.get("type", "") or "") != cable_type,
            ),
        ]
        changed = any(diff.changed for diff in diffs)
        previews.append(
            KiCadImportPreview(
                candidate_key=candidate.key,
                sync_key=sync_key,
                cable_name=cable_name,
                cable_type=cable_type,
                status="update" if changed else "unchanged",
                source="kbl_bus",
                existing_cable_id=str(existing.get("id", "") or ""),
                existing_name=str(existing.get("name", "") or ""),
                existing_type=str(existing.get("type", "") or ""),
                ap_import_action=ap_action,
                ap_match_status=ap_match_status,
                ap_matches=ap_matches,
                start_ap_group=start_ap_group,
                end_ap_group=end_ap_group,
                start_ap_id=start_ap_id,
                end_ap_id=end_ap_id,
                start_ap_status=start_ap_status,
                end_ap_status=end_ap_status,
                start_ap_diagnostic=start_ap_diagnostic,
                end_ap_diagnostic=end_ap_diagnostic,
                diffs=diffs,
            )
        )

    # Process text field candidates
    point_names = {
        str(point.get("name", "") or "").strip().casefold()
        for point in elec_points
        if str(point.get("name", "") or "").strip()
    }
    for tf_candidate in scan_result.textfield_candidates.values():
        sync_key = f"{scan_result.project_uuid}::text_field::{tf_candidate.cable_name}"
        existing = existing_by_sync_key.get(sync_key)
        cable_name = tf_candidate.cable_name
        cable_type = tf_candidate.matched_spec
        ap_action = (
            "AP wiederverwenden"
            if str(cable_name or "").strip().casefold() in point_names
            else "AP neu anlegen"
        )
        
        # Try to find AP matches
        ap_matches = suggest_ap_matches_by_name(cable_name, elec_points)
        ap_match_status = ap_match_summary(ap_matches)

        if existing is None:
            previews.append(
                KiCadImportPreview(
                    candidate_key=tf_candidate.key,
                    sync_key=sync_key,
                    cable_name=cable_name,
                    cable_type=cable_type,
                    status="create",
                    source="text_field",
                    ap_import_action=ap_action,
                    ap_match_status=ap_match_status,
                    ap_matches=ap_matches,
                    diffs=[
                        KiCadFieldDiff("name", "", cable_name, bool(cable_name)),
                        KiCadFieldDiff("type", "", cable_type, bool(cable_type)),
                    ],
                )
            )
        else:
            diffs = [
                KiCadFieldDiff(
                    "name",
                    str(existing.get("name", "") or ""),
                    cable_name,
                    str(existing.get("name", "") or "") != cable_name,
                ),
                KiCadFieldDiff(
                    "type",
                    str(existing.get("type", "") or ""),
                    cable_type,
                    str(existing.get("type", "") or "") != cable_type,
                ),
            ]
            changed = any(diff.changed for diff in diffs)
            previews.append(
                KiCadImportPreview(
                    candidate_key=tf_candidate.key,
                    sync_key=sync_key,
                    cable_name=cable_name,
                    cable_type=cable_type,
                    status="update" if changed else "unchanged",
                    source="text_field",
                    existing_cable_id=str(existing.get("id", "") or ""),
                    existing_name=str(existing.get("name", "") or ""),
                    existing_type=str(existing.get("type", "") or ""),
                    ap_import_action=ap_action,
                    ap_match_status=ap_match_status,
                    ap_matches=ap_matches,
                    diffs=diffs,
                )
            )

    # Process AP group candidates (AP_ prefix from KiCad groups)
    point_ap_names = {
        _normalize_ap_import_name(str(point.get("name", "") or "")).casefold()
        for point in elec_points
        if str(point.get("name", "") or "").strip()
    }
    for ap_candidate in scan_result.ap_group_candidates.values():
        sync_key = f"{scan_result.project_uuid}::ap_group::{ap_candidate.group_name}"
        existing = existing_by_sync_key.get(sync_key)
        cable_name = _normalize_ap_import_name(ap_candidate.group_name)
        cable_type = ""
        ap_action = (
            "AP wiederverwenden"
            if str(cable_name or "").strip().casefold() in point_ap_names
            else "AP neu anlegen"
        )

        ap_matches = suggest_ap_matches_by_name(cable_name, elec_points)
        ap_match_status = ap_match_summary(ap_matches)

        if existing is None:
            previews.append(
                KiCadImportPreview(
                    candidate_key=ap_candidate.key,
                    sync_key=sync_key,
                    cable_name=cable_name,
                    cable_type=cable_type,
                    status="create",
                    source="ap_group",
                    ap_import_action=ap_action,
                    ap_match_status=ap_match_status,
                    ap_matches=ap_matches,
                    diffs=[
                        KiCadFieldDiff("name", "", cable_name, bool(cable_name)),
                        KiCadFieldDiff("type", "", cable_type, bool(cable_type)),
                    ],
                )
            )
        else:
            diffs = [
                KiCadFieldDiff(
                    "name",
                    str(existing.get("name", "") or ""),
                    cable_name,
                    str(existing.get("name", "") or "") != cable_name,
                ),
                KiCadFieldDiff(
                    "type",
                    str(existing.get("type", "") or ""),
                    cable_type,
                    str(existing.get("type", "") or "") != cable_type,
                ),
            ]
            changed = any(diff.changed for diff in diffs)
            previews.append(
                KiCadImportPreview(
                    candidate_key=ap_candidate.key,
                    sync_key=sync_key,
                    cable_name=cable_name,
                    cable_type=cable_type,
                    status="update" if changed else "unchanged",
                    source="ap_group",
                    existing_cable_id=str(existing.get("id", "") or ""),
                    existing_name=str(existing.get("name", "") or ""),
                    existing_type=str(existing.get("type", "") or ""),
                    ap_import_action=ap_action,
                    ap_match_status=ap_match_status,
                    ap_matches=ap_matches,
                    diffs=diffs,
                )
            )

    previews.sort(key=lambda preview: (preview.status, preview.cable_name.lower(), preview.candidate_key.lower()))
    return previews


def suggest_ap_matches(
    candidate: KiCadCableCandidate,
    elec_points: Iterable[dict[str, Any]],
) -> list[KiCadApMatch]:
    candidate_name = candidate.base_name or candidate.pin_name_raw
    return suggest_ap_matches_by_name(candidate_name, elec_points)


def suggest_ap_matches_by_name(
    cable_name: str,
    elec_points: Iterable[dict[str, Any]],
) -> list[KiCadApMatch]:
    """Match cable name (from any source) against available APs."""
    candidate_norm = _normalize_match_text(cable_name)
    candidate_tokens = _match_tokens(cable_name)
    matches: list[KiCadApMatch] = []

    for point in elec_points:
        point_id = str(point.get("id", "") or point.get("point_id", "") or "").strip()
        point_name = str(point.get("name", "") or "").strip()
        floor_plan_id = str(point.get("floor_plan_id", "") or "").strip()
        if not point_id:
            continue

        point_norm = _normalize_match_text(point_name or point_id)
        point_tokens = _match_tokens(point_name or point_id)
        score = 0
        reason = ""

        if point_norm and point_norm == candidate_norm:
            score = 100
            reason = "exakter Name"
        elif candidate_norm and point_norm and candidate_norm in point_norm:
            score = 78
            reason = "Kabelname in AP-Name"
        elif candidate_norm and point_norm and point_norm in candidate_norm:
            score = 72
            reason = "AP-Name in Kabelname"
        else:
            overlap = len(candidate_tokens & point_tokens)
            if overlap:
                score = min(69, 45 + overlap * 12)
                reason = "Token-Überlappung"

        if score < 55:
            continue

        matches.append(
            KiCadApMatch(
                point_id=point_id,
                point_name=point_name or point_id,
                floor_plan_id=floor_plan_id,
                score=score,
                reason=reason,
            )
        )

    matches.sort(key=lambda match: (-match.score, match.point_name.lower(), match.point_id.lower()))
    return matches


def ap_match_summary(matches: list[KiCadApMatch]) -> str:
    if not matches:
        return "unmatched"
    if len(matches) == 1:
        return "matched"
    if matches[0].score >= 90 and matches[0].score - matches[1].score >= 15:
        return "matched"
    return "ambiguous"


def preferred_kicad_pin_ref(candidate: KiCadCableCandidate) -> KiCadSheetPinRef | None:
    for ref in candidate.pin_refs:
        if ref.pin_direction == "output":
            return ref
    return candidate.pin_refs[0] if candidate.pin_refs else None


def build_kicad_cable_key(project_uuid: str, candidate: KiCadCableCandidate) -> str:
    preferred = preferred_kicad_pin_ref(candidate)
    if preferred is None:
        return f"{project_uuid}::{candidate.pin_name_raw}"
    return f"{project_uuid}::{preferred.sheet_uuid}::{candidate.pin_name_raw}"


def build_kicad_bus_cable_key(project_uuid: str, candidate: KiCadBusCableCandidate) -> str:
    return f"{project_uuid}::kbl_bus::{candidate.group_uuid}::{candidate.bus_uuid}"


def scan_kicad_project(root_path: str | Path) -> KiCadScanResult:
    """Scan a KiCad root schematic and aggregate cable candidates by pin name."""
    root = Path(root_path).resolve()
    root_expr = _load_schematic(root)
    project_uuid = _node_uuid(root_expr)
    result = KiCadScanResult(root_path=root, project_uuid=project_uuid)

    _scan_schematic_recursive(
        expr=root_expr,
        current_path=root,
        result=result,
        hierarchy_path=(),
        recursion_stack=(root,),
    )
    _build_ap_group_candidates(result)
    _build_kbl_bus_candidates(result)
    return result


def _scan_schematic_recursive(
    expr: list,
    current_path: Path,
    result: KiCadScanResult,
    hierarchy_path: tuple[str, ...],
    recursion_stack: tuple[Path, ...],
) -> None:
    current_dir = current_path.parent
    current_sheet_name = hierarchy_path[-1] if hierarchy_path else current_path.stem
    current_sheet_uuid = _node_uuid(expr)

    for rect in _children(expr, "rectangle"):
        rect_uuid = _node_uuid(rect)
        bounds = _rectangle_bounds(rect)
        if rect_uuid and bounds is not None:
            result.rectangles[rect_uuid] = KiCadRectFrame(
                uuid=rect_uuid,
                sheet_name=current_sheet_name,
                sheet_file=current_path.name,
                hierarchy_path=hierarchy_path,
                x_min=bounds[0],
                y_min=bounds[1],
                x_max=bounds[2],
                y_max=bounds[3],
            )

    for bus in _children(expr, "bus"):
        bus_uuid = _node_uuid(bus)
        points = _extract_bus_points(bus)
        if bus_uuid and len(points) >= 2:
            result.bus_segments[bus_uuid] = KiCadBusSegment(
                uuid=bus_uuid,
                sheet_name=current_sheet_name,
                sheet_file=current_path.name,
                hierarchy_path=hierarchy_path,
                points=tuple(points),
            )

    for group in _children(expr, "group"):
        group_uuid = _node_uuid(group)
        if not group_uuid:
            continue
        name = _string_at(group, 1)
        members = _group_members(group)
        result.groups[group_uuid] = KiCadGroup(
            uuid=group_uuid,
            name=name,
            sheet_name=current_sheet_name,
            sheet_file=current_path.name,
            hierarchy_path=hierarchy_path,
            members=tuple(members),
        )

    for label_head in ("hierarchical_label", "global_label", "label"):
        for label_node in _children(expr, label_head):
            label_name_raw = _string_at(label_node, 1)
            if not label_name_raw:
                continue
            label_uuid = _node_uuid(label_node)
            parsed = _parse_pin_name(label_name_raw)

            # Plain labels can be very noisy; only import them as cable
            # candidates when they include a typed spec in braces.
            if label_head != "hierarchical_label" and not parsed["spec_raw"]:
                continue

            candidate = result.candidates.get(label_name_raw)
            if candidate is None:
                candidate = KiCadCableCandidate(
                    key=label_name_raw,
                    base_name=parsed["base_name"],
                    pin_name_raw=label_name_raw,
                    spec_raw=parsed["spec_raw"],
                    normalized_spec=parsed["normalized_spec"],
                    spec_kind=parsed["spec_kind"],
                )
                result.candidates[label_name_raw] = candidate

            candidate.pin_refs.append(
                KiCadSheetPinRef(
                    sheet_uuid=current_sheet_uuid,
                    sheet_name=current_sheet_name,
                    sheet_file=current_path.name,
                    pin_uuid=label_uuid,
                    pin_name_raw=label_name_raw,
                    pin_direction=label_head,
                    hierarchy_path=hierarchy_path,
                )
            )

    for sheet in _children(expr, "sheet"):
        sheet_name = _sheet_property(sheet, "Sheetname")
        sheet_file = _sheet_property(sheet, "Sheetfile")
        sheet_uuid = _node_uuid(sheet)
        hierarchy_entry = sheet_name or Path(sheet_file).stem or sheet_uuid
        child_hierarchy = (*hierarchy_path, hierarchy_entry)
        if not sheet_file:
            result.warnings.append(
                KiCadImportWarning(
                    code="sheetfile-missing",
                    message=f"Sheet '{sheet_name or sheet_uuid}' hat kein Sheetfile.",
                    source_path=str(current_path),
                )
            )
            continue

        child_path = (current_dir / sheet_file).resolve()
        child_labels = _load_label_set(child_path, result.warnings, recursion_stack)

        for pin in _children(sheet, "pin"):
            pin_name_raw = _string_at(pin, 1)
            if not pin_name_raw:
                continue
            pin_direction = _string_at(pin, 2)
            pin_uuid = _node_uuid(pin)
            parsed = _parse_pin_name(pin_name_raw)

            candidate = result.candidates.get(pin_name_raw)
            if candidate is None:
                candidate = KiCadCableCandidate(
                    key=pin_name_raw,
                    base_name=parsed["base_name"],
                    pin_name_raw=pin_name_raw,
                    spec_raw=parsed["spec_raw"],
                    normalized_spec=parsed["normalized_spec"],
                    spec_kind=parsed["spec_kind"],
                )
                result.candidates[pin_name_raw] = candidate

            candidate.pin_refs.append(
                KiCadSheetPinRef(
                    sheet_uuid=sheet_uuid,
                    sheet_name=sheet_name,
                    sheet_file=sheet_file,
                    pin_uuid=pin_uuid,
                    pin_name_raw=pin_name_raw,
                    pin_direction=pin_direction,
                    hierarchy_path=child_hierarchy,
                )
            )
            candidate.local_labels.update(
                _matching_labels(
                    child_labels,
                    base_name=parsed["base_name"],
                    spec_raw=parsed["spec_raw"],
                )
            )

        if child_path in recursion_stack:
            result.warnings.append(
                KiCadImportWarning(
                    code="sheet-recursion-detected",
                    message=f"Rekursive Sheet-Einbindung erkannt: {child_path.name}",
                    source_path=str(child_path),
                )
            )
            continue
        if not child_path.exists():
            continue

        child_expr = _load_schematic(child_path)
        _scan_schematic_recursive(
            expr=child_expr,
            current_path=child_path,
            result=result,
            hierarchy_path=child_hierarchy,
            recursion_stack=(*recursion_stack, child_path),
        )


def _parse_pin_name(raw: str) -> dict[str, str]:
    text = str(raw or "").strip()
    if not text:
        return {
            "base_name": "",
            "spec_raw": "",
            "normalized_spec": "",
            "spec_kind": "empty",
        }

    base_name = text
    spec_raw = ""
    if text.endswith("}") and "{" in text:
        brace = text.rfind("{")
        base_name = text[:brace].strip()
        spec_raw = text[brace + 1:-1].strip()

    normalized_spec = spec_raw.replace("_", ",")
    if not spec_raw:
        spec_kind = "plain"
    elif "," in spec_raw:
        spec_kind = "token_list"
    elif _COUNT_X_SPEC_RE.match(spec_raw):
        spec_kind = "count_x_spec"
    else:
        spec_kind = "freeform"

    return {
        "base_name": base_name,
        "spec_raw": spec_raw,
        "normalized_spec": normalized_spec,
        "spec_kind": spec_kind,
    }


def _matching_labels(labels: Iterable[str], base_name: str, spec_raw: str) -> set[str]:
    matched: set[str] = set()
    spec_label = f"{{{spec_raw}}}" if spec_raw else ""
    prefix = f"{base_name}." if base_name else ""
    for label in labels:
        if prefix and label.startswith(prefix):
            matched.add(label)
        elif spec_label and label == spec_label:
            matched.add(label)
    return matched


def _normalize_match_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _normalize_ap_import_name(value: str) -> str:
    text = str(value or "").strip()
    if text[:3].upper() == "AP_":
        rest = text[3:]
        # Keep AP_ for technical ID-style names like AP_1 / AP_1_NEU.
        if rest[:1].isdigit():
            return text
        return rest
    return text


def _match_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", str(value or "").lower())
        if token and len(token) >= 2
    }


def _load_label_set(
    path: Path,
    warnings: list[KiCadImportWarning],
    recursion_stack: tuple[Path, ...],
) -> set[str]:
    if not path.exists():
        warnings.append(
            KiCadImportWarning(
                code="sheetfile-not-found",
                message=f"Child-Sheet fehlt: {path.name}",
                source_path=str(path),
            )
        )
        return set()

    if path in recursion_stack:
        return set()

    expr = _load_schematic(path)
    labels: set[str] = set()
    for head in ("label", "global_label", "hierarchical_label"):
        for node in _children(expr, head):
            label = _string_at(node, 1)
            if label:
                labels.add(label)

    next_stack = (*recursion_stack, path)
    for sheet in _children(expr, "sheet"):
        sheet_file = _sheet_property(sheet, "Sheetfile")
        if not sheet_file:
            continue
        child_path = (path.parent / sheet_file).resolve()
        labels.update(_load_label_set(child_path, warnings, next_stack))
    return labels


def _load_schematic(path: Path) -> list:
    return _parse_sexpr(path.read_text(encoding="utf-8"))


def _parse_sexpr(text: str) -> list:
    tokens = list(_tokenize(text))
    stack: list[list] = []
    current: list = []

    for token in tokens:
        if token == "(":
            stack.append(current)
            current = []
            continue
        if token == ")":
            if not stack:
                raise ValueError("Unbalancierte KiCad-Klammern.")
            completed = current
            current = stack.pop()
            current.append(completed)
            continue
        current.append(token)

    if stack:
        raise ValueError("Unvollständiger KiCad-Ausdruck.")
    if len(current) != 1 or not isinstance(current[0], list):
        raise ValueError("Unerwartete KiCad-Dateistruktur.")
    return current[0]


def _tokenize(text: str) -> Iterable[str]:
    index = 0
    size = len(text)
    while index < size:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char in "()":
            yield char
            index += 1
            continue
        if char == '"':
            index += 1
            chars: list[str] = []
            while index < size:
                char = text[index]
                if char == "\\" and index + 1 < size:
                    chars.append(text[index + 1])
                    index += 2
                    continue
                if char == '"':
                    index += 1
                    break
                chars.append(char)
                index += 1
            yield "".join(chars)
            continue

        start = index
        while index < size and not text[index].isspace() and text[index] not in '()':
            index += 1
        yield text[start:index]


def _children(node: list, head: str) -> list[list]:
    return [child for child in node if isinstance(child, list) and _string_at(child, 0) == head]


def _string_at(node: list, index: int) -> str:
    if index < len(node) and isinstance(node[index], str):
        return node[index]
    return ""


def _sheet_property(sheet: list, key: str) -> str:
    for prop in _children(sheet, "property"):
        if _string_at(prop, 1) == key:
            return _string_at(prop, 2)
    return ""


def _node_uuid(node: list) -> str:
    for child in _children(node, "uuid"):
        return _string_at(child, 1)
    return ""


def _parse_xy(node: list) -> tuple[float, float] | None:
    if len(node) < 3:
        return None
    try:
        return float(node[1]), float(node[2])
    except (TypeError, ValueError):
        return None


def _rectangle_bounds(node: list) -> tuple[float, float, float, float] | None:
    start = None
    end = None
    start_nodes = _children(node, "start")
    end_nodes = _children(node, "end")
    if start_nodes:
        start = _parse_xy(start_nodes[0])
    if end_nodes:
        end = _parse_xy(end_nodes[0])
    if start is None or end is None:
        return None
    x1, y1 = start
    x2, y2 = end
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def _extract_bus_points(node: list) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    pts_nodes = _children(node, "pts")
    if not pts_nodes:
        return points
    for xy in _children(pts_nodes[0], "xy"):
        parsed = _parse_xy(xy)
        if parsed is not None:
            points.append(parsed)
    return points


def _group_members(node: list) -> list[str]:
    members: list[str] = []
    member_nodes = _children(node, "members")
    if not member_nodes:
        return members
    raw = member_nodes[0]
    for entry in raw[1:]:
        if isinstance(entry, str) and entry:
            members.append(entry)
    return members


def _is_ap_group_name(name: str) -> bool:
    return str(name or "").strip().upper().startswith("AP_")


def _canonical_ap_group_name(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    upper = text.upper()
    match = re.search(r"(?:^|_)AP_(?:[A-Z0-9]+_)*(\d+)$", upper)
    if match:
        return f"AP_{match.group(1)}"
    return text


def _is_kbl_group_name(name: str) -> bool:
    return str(name or "").strip().upper().startswith("KBL_")


def _is_kbl_candidate_name(name: str) -> bool:
    return str(name or "").strip().upper().startswith("KBL_")


def _strip_kbl_prefix(name: str) -> str:
    raw = str(name or "").strip()
    if raw[:4].upper() == "KBL_":
        return raw[4:]
    return raw


def _parse_kbl_group_name(name: str) -> dict[str, str]:
    raw = str(name or "").strip()
    if not raw:
        return {
            "group_name_raw": "",
            "base_name": "",
            "spec_raw": "",
            "normalized_spec": "",
            "spec_kind": "empty",
        }
    stripped = raw[4:] if raw[:4].upper() == "KBL_" else raw
    parsed = _parse_pin_name(stripped)
    return {
        "group_name_raw": raw,
        "base_name": parsed["base_name"],
        "spec_raw": parsed["spec_raw"],
        "normalized_spec": parsed["normalized_spec"],
        "spec_kind": parsed["spec_kind"],
    }


def _build_ap_group_candidates(result: KiCadScanResult) -> None:
    result.ap_group_candidates.clear()

    for group in result.groups.values():
        if not _is_ap_group_name(group.name):
            continue

        frame = None
        for member_id in group.members:
            maybe_frame = result.rectangles.get(member_id)
            if maybe_frame is not None:
                frame = maybe_frame
                break

        if frame is None:
            result.warnings.append(
                KiCadImportWarning(
                    code="ap-group-missing-frame",
                    message=f"AP-Gruppe '{group.name}' hat keinen Rechteck-Rahmen als Group-Member.",
                    source_path=str(result.root_path),
                )
            )
            continue

        bus_hits: list[KiCadBusSegment] = []
        frame_rect = (frame.x_min, frame.y_min, frame.x_max, frame.y_max)
        for bus in result.bus_segments.values():
            # Only compare geometry in the same schematic page context.
            if bus.sheet_file != frame.sheet_file or bus.hierarchy_path != frame.hierarchy_path:
                continue
            if _polyline_intersects_rect(bus.points, frame_rect):
                bus_hits.append(bus)

        key = f"group::{group.uuid}"
        result.ap_group_candidates[key] = KiCadApGroupCandidate(
            key=key,
            group_name=_canonical_ap_group_name(group.name),
            group_uuid=group.uuid,
            frame_uuid=frame.uuid,
            sheet_file=frame.sheet_file,
            hierarchy_path=frame.hierarchy_path,
            frame_bounds=frame_rect,
            bus_hits=bus_hits,
        )


def _build_kbl_bus_candidates(result: KiCadScanResult) -> None:
    result.kbl_bus_candidates.clear()

    for group in result.groups.values():
        if not _is_kbl_group_name(group.name):
            continue

        parsed = _parse_kbl_group_name(group.name)
        if not parsed["spec_raw"]:
            result.warnings.append(
                KiCadImportWarning(
                    code="kbl-group-missing-spec",
                    message=(
                        f"KBL-Gruppe '{group.name}' hat keine Typdefinition {{...}} "
                        f"und wird übersprungen."
                    ),
                    source_path=str(result.root_path),
                )
            )
            continue
        members: list[KiCadBusSegment] = []
        for member_id in group.members:
            bus = result.bus_segments.get(member_id)
            if bus is None:
                continue
            if bus.sheet_file != group.sheet_file or bus.hierarchy_path != group.hierarchy_path:
                continue
            members.append(bus)

        if not members:
            result.warnings.append(
                KiCadImportWarning(
                    code="kbl-group-missing-bus",
                    message=f"KBL-Gruppe '{group.name}' enthält keine Bus-Member auf derselben Seite.",
                    source_path=str(result.root_path),
                )
            )
            continue

        for bus in members:
            key = f"kbl_bus::{group.uuid}::{bus.uuid}"
            result.kbl_bus_candidates[key] = KiCadBusCableCandidate(
                key=key,
                group_uuid=group.uuid,
                group_name_raw=parsed["group_name_raw"],
                base_name=parsed["base_name"],
                spec_raw=parsed["spec_raw"],
                normalized_spec=parsed["normalized_spec"],
                spec_kind=parsed["spec_kind"],
                bus_uuid=bus.uuid,
                sheet_name=bus.sheet_name,
                sheet_file=bus.sheet_file,
                hierarchy_path=bus.hierarchy_path,
                points=bus.points,
            )


def _resolve_kbl_endpoint_group(
    scan_result: KiCadScanResult,
    sheet_file: str,
    hierarchy_path: tuple[str, ...],
    point: tuple[float, float],
) -> tuple[str, str]:
    exact_hits: list[str] = []
    nearby: list[tuple[str, float]] = []
    for ap_candidate in scan_result.ap_group_candidates.values():
        if ap_candidate.sheet_file != sheet_file or ap_candidate.hierarchy_path != hierarchy_path:
            continue
        if _point_in_rect(point, ap_candidate.frame_bounds):
            exact_hits.append(ap_candidate.group_name)
            nearby.append((ap_candidate.group_name, 0.0))
            continue

        distance = _point_to_rect_distance(point, ap_candidate.frame_bounds)
        if distance <= _KBL_ENDPOINT_MARGIN_PX:
            nearby.append((ap_candidate.group_name, distance))

    unique_exact = sorted(set(exact_hits))
    if len(unique_exact) == 1:
        return unique_exact[0], "matched"
    if len(unique_exact) > 1:
        return "", "ambiguous"

    if not nearby:
        return "", "unmatched"

    best_by_name: dict[str, float] = {}
    for name, dist in nearby:
        prev = best_by_name.get(name)
        if prev is None or dist < prev:
            best_by_name[name] = dist

    ranked = sorted(best_by_name.items(), key=lambda item: (item[1], item[0].lower()))
    if len(ranked) == 1:
        return ranked[0][0], "matched"
    if ranked[1][1] - ranked[0][1] >= _KBL_ENDPOINT_DISTANCE_GAP_PX:
        return ranked[0][0], "matched"
    return "", "ambiguous"


def _resolve_kbl_endpoint_ap(
    group_name: str,
    elec_points: Iterable[dict[str, Any]],
) -> tuple[str, str, list[KiCadApMatch]]:
    if not group_name:
        return "", "unmatched", []

    target_forms = _kbl_ap_name_variants(group_name)
    matches: list[KiCadApMatch] = []
    for point in elec_points:
        point_id = str(point.get("id", "") or point.get("point_id", "") or "").strip()
        point_name = str(point.get("name", "") or "").strip()
        floor_plan_id = str(point.get("floor_plan_id", "") or "").strip()
        if not point_id:
            continue
        point_norm = _normalize_match_text(point_name)
        if point_norm not in target_forms:
            continue
        matches.append(
            KiCadApMatch(
                point_id=point_id,
                point_name=point_name or point_id,
                floor_plan_id=floor_plan_id,
                score=100,
                reason="AP-Group Endpoint",
            )
        )

    match_id, status = _resolve_ap_match_status(matches)
    if status != "unmatched":
        return match_id, status, matches

    # Fallback: fuzzy name matching (handles small naming variants).
    fuzzy = suggest_ap_matches_by_name(_strip_ap_prefix(group_name), elec_points)
    match_id, status = _resolve_ap_match_status(fuzzy)
    return match_id, status, fuzzy


def _resolve_kbl_label_endpoint_ap(
    cable_name: str,
    elec_points: Iterable[dict[str, Any]],
) -> tuple[str, str, list[KiCadApMatch]]:
    base = str(cable_name or "").strip()
    if not base:
        return "", "unmatched", []

    target_forms = {
        _normalize_match_text(base),
        _normalize_match_text(f"AP_{base}"),
        _normalize_match_text(_strip_ap_prefix(base)),
    }
    target_forms.discard("")
    matches: list[KiCadApMatch] = []
    for point in elec_points:
        point_id = str(point.get("id", "") or point.get("point_id", "") or "").strip()
        point_name = str(point.get("name", "") or "").strip()
        floor_plan_id = str(point.get("floor_plan_id", "") or "").strip()
        if not point_id:
            continue
        point_norm = _normalize_match_text(point_name)
        if point_norm not in target_forms:
            continue
        matches.append(
            KiCadApMatch(
                point_id=point_id,
                point_name=point_name or point_id,
                floor_plan_id=floor_plan_id,
                score=100,
                reason="KBL Label Endpoint",
            )
        )

    match_id, status = _resolve_ap_match_status(matches)
    if status != "unmatched":
        return match_id, status, matches

    fuzzy = suggest_ap_matches_by_name(_strip_ap_prefix(base), elec_points)
    match_id, status = _resolve_ap_match_status(fuzzy)
    return match_id, status, fuzzy


def _resolve_kbl_label_endpoints(
    candidate: KiCadCableCandidate,
    scan_result: KiCadScanResult,
    elec_points: Iterable[dict[str, Any]],
) -> tuple[str, str, list[KiCadApMatch], str, str, list[KiCadApMatch]]:
    primary_name = _strip_kbl_prefix(candidate.base_name or candidate.pin_name_raw)
    start_ap_id, start_ap_status, start_ap_matches = _resolve_kbl_label_endpoint_ap(
        primary_name,
        elec_points,
    )

    local_end_id, local_end_status, local_end_matches = _resolve_kbl_label_local_group_counterpart(
        candidate,
        primary_name,
        scan_result,
        elec_points,
        start_ap_id,
        start_ap_matches,
    )
    if local_end_status == "matched":
        return (
            start_ap_id,
            start_ap_status,
            start_ap_matches,
            local_end_id,
            local_end_status,
            local_end_matches,
        )

    hint_names = _collect_kbl_hierarchy_endpoint_hints(candidate)
    hint_matches = _resolve_kbl_related_end_matches(
        related_names=hint_names,
        elec_points=elec_points,
        start_ap_id=start_ap_id,
        start_ap_matches=start_ap_matches,
    )
    if hint_matches:
        numbered_hint = _pick_numbered_neighbor_match(primary_name, hint_matches)
        if numbered_hint is not None:
            return start_ap_id, start_ap_status, start_ap_matches, numbered_hint.point_id, "matched", [numbered_hint]
        end_ap_id, end_ap_status = _resolve_ap_match_status(hint_matches)
        return start_ap_id, start_ap_status, start_ap_matches, end_ap_id, end_ap_status, hint_matches

    related_names = _collect_kbl_related_counterpart_names(candidate, scan_result)
    if not related_names:
        return start_ap_id, start_ap_status, start_ap_matches, "", "unmatched", []

    end_matches = _resolve_kbl_related_end_matches(
        related_names=related_names,
        elec_points=elec_points,
        start_ap_id=start_ap_id,
        start_ap_matches=start_ap_matches,
    )
    numbered_fallback = _pick_numbered_neighbor_match(primary_name, end_matches)
    if numbered_fallback is not None:
        return (
            start_ap_id,
            start_ap_status,
            start_ap_matches,
            numbered_fallback.point_id,
            "matched",
            [numbered_fallback],
        )
    end_ap_id, end_ap_status = _resolve_ap_match_status(end_matches)
    return start_ap_id, start_ap_status, start_ap_matches, end_ap_id, end_ap_status, end_matches


def _resolve_kbl_related_end_matches(
    related_names: list[str],
    elec_points: Iterable[dict[str, Any]],
    start_ap_id: str,
    start_ap_matches: list[KiCadApMatch],
) -> list[KiCadApMatch]:
    if not related_names:
        return []

    start_semantic = ""
    for match in start_ap_matches:
        if match.point_id == start_ap_id:
            start_semantic = _ap_semantic_key(match.point_name)
            break
    if not start_semantic and start_ap_matches:
        start_semantic = _ap_semantic_key(start_ap_matches[0].point_name)

    end_matches_by_semantic: dict[str, KiCadApMatch] = {}
    seen_point_ids: set[str] = set()
    for related_name in related_names:
        _rid, status, matches = _resolve_kbl_label_endpoint_ap(related_name, elec_points)
        if status == "unmatched":
            continue
        for match in matches:
            if match.point_id == start_ap_id or match.point_id in seen_point_ids:
                continue
            semantic_key = _ap_semantic_key(match.point_name)
            if start_semantic and semantic_key == start_semantic:
                continue
            seen_point_ids.add(match.point_id)
            current = end_matches_by_semantic.get(semantic_key)
            if current is None or _prefer_ap_alias_variant(match, current):
                end_matches_by_semantic[semantic_key] = match

    end_matches = list(end_matches_by_semantic.values())
    end_matches.sort(key=lambda item: (-item.score, item.point_name.lower(), item.point_id.lower()))
    return end_matches


def _collect_kbl_related_counterpart_names(
    candidate: KiCadCableCandidate,
    scan_result: KiCadScanResult,
) -> list[str]:
    primary_norm = _normalize_match_text(_strip_kbl_prefix(candidate.base_name or candidate.pin_name_raw))
    own_refs = {
        (str(ref.sheet_file or ""), tuple(ref.hierarchy_path or ()))
        for ref in candidate.pin_refs
    }

    names: set[str] = set()
    for other in scan_result.candidates.values():
        if other.key == candidate.key:
            continue
        if _is_kbl_candidate_name(other.pin_name_raw):
            continue

        other_name = str(other.base_name or other.pin_name_raw or "").strip()
        if not other_name:
            continue
        if _normalize_match_text(other_name) == primary_norm:
            continue

        related = False
        for ref in other.pin_refs:
            ref_loc = (str(ref.sheet_file or ""), tuple(ref.hierarchy_path or ()))
            if _is_hierarchy_location_related(ref_loc, own_refs):
                related = True
                break
        if related:
            names.add(other_name)

    return sorted(names, key=lambda value: value.lower())


def _collect_kbl_hierarchy_endpoint_hints(candidate: KiCadCableCandidate) -> list[str]:
    hints: list[str] = []

    for ref in candidate.pin_refs:
        sheet_stem = Path(str(ref.sheet_file or "")).stem.strip()
        if sheet_stem and sheet_stem.casefold() not in {"kueche", "küche"}:
            hints.append(sheet_stem)
        for part in ref.hierarchy_path:
            token = str(part or "").strip()
            if token:
                hints.append(token)

    # Prioritize UV/verteiler-like endpoints first for cross-sheet connectors.
    unique: list[str] = []
    seen: set[str] = set()
    for hint in hints:
        norm = _normalize_match_text(hint)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        unique.append(hint)

    unique.sort(
        key=lambda value: (
            0 if "uv" in _normalize_match_text(value) else 1,
            0 if "spk" in _normalize_match_text(value) else 1,
            value.lower(),
        )
    )
    return unique


def _resolve_kbl_label_local_group_counterpart(
    candidate: KiCadCableCandidate,
    primary_name: str,
    scan_result: KiCadScanResult,
    elec_points: Iterable[dict[str, Any]],
    start_ap_id: str,
    start_ap_matches: list[KiCadApMatch],
) -> tuple[str, str, list[KiCadApMatch]]:
    primary_family, primary_index = _name_family_index(primary_name)
    if primary_index is None:
        return "", "unmatched", []

    locations = {
        (str(ref.sheet_file or ""), tuple(ref.hierarchy_path or ()))
        for ref in candidate.pin_refs
    }

    start_semantic = ""
    for match in start_ap_matches:
        if match.point_id == start_ap_id:
            start_semantic = _ap_semantic_key(match.point_name)
            break

    local_matches: list[tuple[int, str, KiCadApMatch]] = []
    for ap_group in scan_result.ap_group_candidates.values():
        group_loc = (str(ap_group.sheet_file or ""), tuple(ap_group.hierarchy_path or ()))
        if group_loc not in locations:
            continue

        group_name = _strip_ap_prefix(ap_group.group_name)
        group_family, group_index = _name_family_index(group_name)
        if group_index is None:
            continue
        if not _families_related(primary_family, group_family):
            continue

        group_ap_id, group_status, group_ap_matches = _resolve_kbl_endpoint_ap(ap_group.group_name, elec_points)
        if group_status != "matched" or not group_ap_matches or not group_ap_id:
            continue

        chosen = next((m for m in group_ap_matches if m.point_id == group_ap_id), group_ap_matches[0])
        semantic = _ap_semantic_key(chosen.point_name)
        if semantic and start_semantic and semantic == start_semantic:
            continue
        local_matches.append((group_index, ap_group.group_name, chosen))

    if not local_matches:
        return "", "unmatched", []

    # Favor the nearest lower numbered neighbor to the KBL index.
    local_matches.sort(
        key=lambda item: (
            abs(item[0] - primary_index),
            item[0],
            item[1].lower(),
        )
    )
    lower = [entry for entry in local_matches if entry[0] < primary_index]
    if not lower:
        return "", "unmatched", []

    best = lower[0][2]
    return best.point_id, "matched", [best]


def _name_family_index(value: str) -> tuple[str, int | None]:
    text = str(value or "").strip()
    if not text:
        return "", None

    # Split trailing numeric endpoint index, e.g. Kueche3 -> (kueche, 3).
    match = re.match(r"^(.*?)(\d+)$", text)
    if not match:
        return _normalize_match_text(text), None

    family_raw = str(match.group(1) or "")
    index_raw = str(match.group(2) or "")
    family = _normalize_match_text(family_raw)
    try:
        index = int(index_raw)
    except ValueError:
        return family, None
    return family, index


def _families_related(left: str, right: str) -> bool:
    a = str(left or "").strip()
    b = str(right or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    return a.endswith(b) or b.endswith(a)


def _pick_numbered_neighbor_match(
    primary_name: str,
    matches: list[KiCadApMatch],
) -> KiCadApMatch | None:
    if not matches:
        return None

    primary_family, primary_index = _name_family_index(primary_name)
    if primary_index is None:
        return None

    ranked: list[tuple[int, int, int, str, KiCadApMatch]] = []
    for match in matches:
        family, index = _name_family_index(_strip_ap_prefix(match.point_name))
        if index is None:
            continue
        if not _families_related(primary_family, family):
            continue
        if index >= primary_index:
            continue
        ranked.append(
            (
                abs(index - primary_index),
                index,
                str(match.point_name or "").lower(),
                match,
            )
        )

    if not ranked:
        return None

    ranked.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return ranked[0][3]


def _is_hierarchy_location_related(
    location: tuple[str, tuple[str, ...]],
    candidate_locations: set[tuple[str, tuple[str, ...]]],
) -> bool:
    sheet_file, path = location
    for cand_sheet, cand_path in candidate_locations:
        if sheet_file == cand_sheet:
            return True
        if _is_hierarchy_prefix(path, cand_path) or _is_hierarchy_prefix(cand_path, path):
            return True
    return False


def _is_hierarchy_prefix(prefix: tuple[str, ...], full: tuple[str, ...]) -> bool:
    if len(prefix) > len(full):
        return False
    return full[: len(prefix)] == prefix


def _resolve_ap_match_status(matches: list[KiCadApMatch]) -> tuple[str, str]:
    if not matches:
        return "", "unmatched"

    semantic_keys = {_ap_semantic_key(match.point_name) for match in matches if _ap_semantic_key(match.point_name)}
    if len(semantic_keys) == 1:
        ordered = sorted(matches, key=lambda item: (0 if str(item.point_name).upper().startswith("AP_") else 1, item.point_name.lower(), item.point_id.lower()))
        return ordered[0].point_id, "matched"

    summary = ap_match_summary(matches)
    if summary == "matched":
        return matches[0].point_id, "matched"
    return "", "ambiguous"


def _strip_ap_prefix(value: str) -> str:
    text = str(value or "").strip()
    if text[:3].upper() == "AP_":
        return text[3:]
    return text


def _ap_semantic_key(value: str) -> str:
    return _normalize_match_text(_strip_ap_prefix(value))


def _prefer_ap_alias_variant(candidate: KiCadApMatch, current: KiCadApMatch) -> bool:
    candidate_is_prefixed = str(candidate.point_name or "").upper().startswith("AP_")
    current_is_prefixed = str(current.point_name or "").upper().startswith("AP_")
    if candidate_is_prefixed != current_is_prefixed:
        return candidate_is_prefixed
    if candidate.score != current.score:
        return candidate.score > current.score
    return str(candidate.point_name or "").lower() < str(current.point_name or "").lower()


def _kbl_ap_name_variants(group_name: str) -> set[str]:
    raw = str(group_name or "").strip()
    stripped = _strip_ap_prefix(raw)
    variants = {
        _normalize_match_text(raw),
        _normalize_match_text(stripped),
        _normalize_match_text(f"AP_{stripped}"),
    }
    variants.discard("")
    return variants


def _point_to_rect_distance(
    point: tuple[float, float],
    rect: tuple[float, float, float, float],
) -> float:
    px, py = point
    x_min, y_min, x_max, y_max = rect
    dx = max(x_min - px, 0.0, px - x_max)
    dy = max(y_min - py, 0.0, py - y_max)
    return (dx * dx + dy * dy) ** 0.5


def _build_kbl_endpoint_diagnostic(
    side: str,
    source: str,
    group_name: str,
    group_status: str,
    ap_status: str,
    matches: list[KiCadApMatch],
) -> str:
    parts: list[str] = [f"{side}-Erkennung"]

    if source == "kbl_bus":
        geom_text = {
            "matched": "Geom-Treffer: eindeutiger AP-Frame",
            "ambiguous": "Geom-Treffer: mehrere AP-Frames",
            "unmatched": "Geom-Treffer: kein AP-Frame in Reichweite",
        }.get(group_status or "", "Geom-Treffer: unbekannt")
        if group_name:
            geom_text += f" ({group_name})"
        parts.append(geom_text)
    elif source == "kbl_label":
        parts.append("Quelle: KBL-Label (ohne direkte Bus-Endpunktgeometrie)")

    ap_text = {
        "matched": "AP-Match: eindeutig",
        "ambiguous": "AP-Match: mehrdeutig",
        "unmatched": "AP-Match: kein Treffer",
    }.get(ap_status or "", "AP-Match: unbekannt")
    parts.append(ap_text)

    if matches:
        top = matches[0]
        parts.append(
            f"Top-Kandidat: {top.point_name} ({top.reason}, Score {top.score}, Kandidaten {len(matches)})"
        )
    elif source == "kbl_label" and side.casefold().startswith("ziel"):
        parts.append("Hinweis: Ziel-AP kann aus reinem KBL-Label nicht automatisch abgeleitet werden")

    return " | ".join(parts)


def _merge_unique_ap_matches(
    first: list[KiCadApMatch],
    second: list[KiCadApMatch],
) -> list[KiCadApMatch]:
    merged: dict[str, KiCadApMatch] = {}
    for match in (*first, *second):
        merged[match.point_id] = match
    values = list(merged.values())
    values.sort(key=lambda item: (-item.score, item.point_name.lower(), item.point_id.lower()))
    return values


def _polyline_intersects_rect(
    points: tuple[tuple[float, float], ...],
    rect: tuple[float, float, float, float],
) -> bool:
    if len(points) < 2:
        return False
    for index in range(len(points) - 1):
        if _segment_intersects_rect(points[index], points[index + 1], rect):
            return True
    return False


def _segment_intersects_rect(
    a: tuple[float, float],
    b: tuple[float, float],
    rect: tuple[float, float, float, float],
) -> bool:
    x_min, y_min, x_max, y_max = rect
    x1, y1 = a
    x2, y2 = b

    if _point_in_rect(a, rect) or _point_in_rect(b, rect):
        return True

    rect_edges = (
        ((x_min, y_min), (x_max, y_min)),
        ((x_max, y_min), (x_max, y_max)),
        ((x_max, y_max), (x_min, y_max)),
        ((x_min, y_max), (x_min, y_min)),
    )
    for c, d in rect_edges:
        if _segments_intersect((x1, y1), (x2, y2), c, d):
            return True
    return False


def _point_in_rect(point: tuple[float, float], rect: tuple[float, float, float, float]) -> bool:
    x, y = point
    x_min, y_min, x_max, y_max = rect
    return x_min <= x <= x_max and y_min <= y <= y_max


def _segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    def orient(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    def on_segment(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> bool:
        return (
            min(p[0], r[0]) <= q[0] <= max(p[0], r[0])
            and min(p[1], r[1]) <= q[1] <= max(p[1], r[1])
        )

    o1 = orient(a, b, c)
    o2 = orient(a, b, d)
    o3 = orient(c, d, a)
    o4 = orient(c, d, b)

    if (o1 > 0 > o2 or o1 < 0 < o2) and (o3 > 0 > o4 or o3 < 0 < o4):
        return True

    if o1 == 0 and on_segment(a, c, b):
        return True
    if o2 == 0 and on_segment(a, d, b):
        return True
    if o3 == 0 and on_segment(c, a, d):
        return True
    if o4 == 0 and on_segment(c, b, d):
        return True
    return False


def parse_textfield_metadata(content: str) -> KiCadTextFieldMetadata | None:
    """Parse AP_NAME and optional ROOM from text field content.
    
    Format (case-insensitive):
    AP_NAME: Flur
    ROOM: Wohnzimmer
    """
    lines = (line.strip() for line in (content or "").split("\n"))
    ap_name = ""
    room = ""
    
    for line in lines:
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key_norm = key.strip().upper()
        val_norm = value.strip()
        
        if key_norm == "AP_NAME":
            ap_name = val_norm
        elif key_norm == "ROOM":
            room = val_norm
    
    if not ap_name:
        return None
    
    return KiCadTextFieldMetadata(
        text_id="",  # set by caller
        ap_name=ap_name,
        room=room,
    )


def suggest_cable_from_candidates(
    ap_name: str,
    room: str,
    candidates: dict[str, KiCadCableCandidate],
    preferred_sheet_name: str = "",
) -> tuple[KiCadCableCandidate | None, str]:
    """Find best matching KiCad candidate by AP name, room, and sheet context.
    
    Scoring:
    - Name matching: exact (100), substring (80/75), token overlap (40-70)
    - Room boost: +5 if room name appears in base name
    - Sheet context: +10 if preferred_sheet_name matches any pin_ref.sheet_name
    
    Returns (best_candidate, matched_spec) or (None, "").
    """
    ap_norm = _normalize_match_text(ap_name)
    room_norm = _normalize_match_text(room)
    ap_tokens = _match_tokens(ap_name)
    room_tokens = _match_tokens(room)
    preferred_sheet_norm = _normalize_match_text(preferred_sheet_name) if preferred_sheet_name else ""
    
    best_candidate: KiCadCableCandidate | None = None
    best_score = 0
    
    for candidate in candidates.values():
        base_norm = _normalize_match_text(candidate.base_name)
        base_tokens = _match_tokens(candidate.base_name)
        
        score = 0
        
        # Exact match on base_name
        if base_norm and base_norm == ap_norm:
            score = 100
        # Substring match
        elif base_norm and ap_norm and base_norm in ap_norm:
            score = 80
        elif base_norm and ap_norm and ap_norm in base_norm:
            score = 75
        # Token overlap
        else:
            overlap = len(ap_tokens & base_tokens)
            if overlap:
                score = min(70, 40 + overlap * 15)
        
        # Room as secondary signal (small boost if room name matches)
        if room_norm and base_norm and room_norm in base_norm:
            score += 5
        
        # Sheet context boost: prefer candidates from the same sheet
        if preferred_sheet_norm:
            for pin_ref in candidate.pin_refs:
                pin_sheet_norm = _normalize_match_text(pin_ref.sheet_name)
                if pin_sheet_norm and pin_sheet_norm == preferred_sheet_norm:
                    score += 10
                    break  # Only add boost once per candidate
        
        if score > best_score:
            best_score = score
            best_candidate = candidate
    
    matched_spec = best_candidate.normalized_spec if best_candidate else ""
    return best_candidate, matched_spec


def build_textfield_candidate_from_scan(
    text_id: str,
    content: str,
    candidates: dict[str, KiCadCableCandidate],
    floor_plan_name: str = "",
) -> KiCadTextFieldCandidate | None:
    """Convert a text field annotation into a cable candidate.
    
    Automatically matches nearest KiCad candidate by name similarity and sheet context.
    """
    metadata = parse_textfield_metadata(content)
    if not metadata:
        return None

    metadata = KiCadTextFieldMetadata(
        text_id=text_id,
        ap_name=metadata.ap_name,
        room=metadata.room,
        floor_plan_id=metadata.floor_plan_id,
    )
    best_candidate, matched_spec = suggest_cable_from_candidates(
        ap_name=metadata.ap_name,
        room=metadata.room,
        candidates=candidates,
        preferred_sheet_name=floor_plan_name,
    )
    
    return KiCadTextFieldCandidate(
        key=text_id,
        source_metadata=metadata,
        cable_name=metadata.ap_name,
        matched_spec=matched_spec,
        best_matched_candidate=best_candidate,
    )