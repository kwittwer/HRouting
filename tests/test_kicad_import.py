from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from logic.kicad_import import (
    KiCadApGroupCandidate,
    KiCadCableCandidate,
    KiCadScanResult,
    KiCadSheetPinRef,
    build_kicad_bus_cable_key,
    build_import_preview,
    build_kicad_cable_key,
    build_textfield_candidate_from_scan,
    scan_kicad_project,
    suggest_ap_matches,
    suggest_cable_from_candidates,
)


KICAD_ROOT = ROOT / "examples" / "KiCAD" / "Elektroplanung.kicad_sch"
KICAD_HWR = ROOT / "examples" / "KiCAD" / "HWR.kicad_sch"


def test_scan_kicad_project_extracts_root_sheet_pin_candidates():
    result = scan_kicad_project(KICAD_ROOT)

    assert result.project_uuid == "400d403a-4cdf-4a53-9f0d-9df9db2933a3"
    assert "Flur{3x1_5}" in result.candidates
    assert "Zuleitung_Haus{5x10}" in result.candidates
    assert "WP{2xCAT6}" in result.candidates


def test_scan_kicad_project_aggregates_same_pin_name_across_sheets():
    result = scan_kicad_project(KICAD_ROOT)

    candidate = result.candidates["Flur{3x1_5}"]
    sheet_names = {ref.sheet_name for ref in candidate.pin_refs}
    directions = {ref.pin_direction for ref in candidate.pin_refs}

    assert candidate.base_name == "Flur"
    assert candidate.spec_raw == "3x1_5"
    assert candidate.normalized_spec == "3x1,5"
    assert candidate.spec_kind == "count_x_spec"
    assert {"UV HWR", "Flur_Ankleide"}.issubset(sheet_names)
    assert {"input", "output"}.issubset(directions)


def test_scan_kicad_project_collects_matching_child_sheet_labels():
    result = scan_kicad_project(KICAD_ROOT)

    candidate = result.candidates["Flur{3x1_5}"]

    assert "Flur.L" in candidate.local_labels
    assert "Flur.N" in candidate.local_labels
    assert "{3x1_5}" in candidate.local_labels


def test_scan_kicad_project_detects_token_list_specs():
    result = scan_kicad_project(KICAD_ROOT)

    candidate = result.candidates["RS485_Powermeter{A,B,GND}"]

    assert candidate.base_name == "RS485_Powermeter"
    assert candidate.spec_raw == "A,B,GND"
    assert candidate.normalized_spec == "A,B,GND"
    assert candidate.spec_kind == "token_list"


def test_scan_kicad_project_includes_spec_labels_as_candidates():
    result = scan_kicad_project(KICAD_HWR)

    assert "HWR2{5x1_5}" in result.candidates
    candidate = result.candidates["HWR2{5x1_5}"]
    assert candidate.base_name == "HWR2"
    assert candidate.spec_raw == "5x1_5"
    assert any(ref.pin_direction == "label" for ref in candidate.pin_refs)


def test_suggest_ap_matches_marks_unique_exact_match():
    result = scan_kicad_project(KICAD_ROOT)
    candidate = result.candidates["Flur{3x1_5}"]

    matches = suggest_ap_matches(
        candidate,
        [
            {"id": "AP-1", "name": "Flur", "floor_plan_id": "grundriss-1"},
            {"id": "AP-2", "name": "Wohnzimmer", "floor_plan_id": "grundriss-1"},
        ],
    )

    assert len(matches) == 1
    assert matches[0].point_id == "AP-1"
    assert matches[0].reason == "exakter Name"


def test_suggest_ap_matches_can_be_ambiguous_for_room_like_names():
    result = scan_kicad_project(KICAD_ROOT)
    candidate = result.candidates["Flur{3x1_5}"]

    matches = suggest_ap_matches(
        candidate,
        [
            {"id": "AP-1", "name": "Steckdose Flur", "floor_plan_id": "grundriss-1"},
            {"id": "AP-2", "name": "Licht Flur", "floor_plan_id": "grundriss-1"},
        ],
    )

    assert len(matches) == 2
    assert {match.point_id for match in matches} == {"AP-1", "AP-2"}
    assert matches[0].score == matches[1].score


def test_build_import_preview_marks_create_update_and_unchanged_states():
    result = scan_kicad_project(KICAD_ROOT)
    flur_key = build_kicad_cable_key(result.project_uuid, result.candidates["Flur{3x1_5}"])
    wp_key = build_kicad_cable_key(result.project_uuid, result.candidates["WP{2xCAT6}"])

    previews = build_import_preview(
        result,
        existing_cables=[
            {
                "id": "EK-1",
                "name": "Altname",
                "type": "alt",
                "kicad_cable_key": flur_key,
            },
            {
                "id": "EK-2",
                "name": "WP",
                "type": "2xCAT6",
                "kicad_cable_key": wp_key,
            },
        ],
        elec_points=[
            {"id": "AP-1", "name": "Steckdose Flur", "floor_plan_id": "grundriss-1"},
            {"id": "AP-2", "name": "Licht Flur", "floor_plan_id": "grundriss-1"},
            {"id": "AP-3", "name": "WP", "floor_plan_id": "grundriss-1"},
        ],
    )

    by_key = {preview.candidate_key: preview for preview in previews}

    assert by_key["Flur{3x1_5}"].status == "update"
    assert by_key["Flur{3x1_5}"].existing_cable_id == "EK-1"
    assert by_key["Flur{3x1_5}"].ap_match_status == "ambiguous"
    assert any(diff.field == "name" and diff.changed for diff in by_key["Flur{3x1_5}"].diffs)
    assert any(diff.field == "type" and diff.changed for diff in by_key["Flur{3x1_5}"].diffs)

    assert by_key["WP{2xCAT6}"].status == "unchanged"
    assert by_key["WP{2xCAT6}"].ap_match_status == "matched"
    assert all(not diff.changed for diff in by_key["WP{2xCAT6}"].diffs)

    assert by_key["Zuleitung_Haus{5x10}"].status == "create"


def test_build_import_preview_matches_ap_group_with_ap_prefix_normalization(tmp_path):
    scan_result = KiCadScanResult(root_path=tmp_path / "dummy.kicad_sch", project_uuid="proj-1")
    scan_result.ap_group_candidates["AP_Herdanschluss"] = KiCadApGroupCandidate(
        key="AP_Herdanschluss",
        group_name="AP_Herdanschluss",
        group_uuid="group-1",
        frame_uuid="frame-1",
    )

    previews = build_import_preview(
        scan_result,
        existing_cables=[],
        elec_points=[
            {"id": "AP-77", "name": "Herdanschluss", "floor_plan_id": "grundriss-1"},
        ],
    )

    preview = next(p for p in previews if p.candidate_key == "AP_Herdanschluss")
    assert preview.cable_name == "Herdanschluss"
    assert preview.ap_import_action == "AP wiederverwenden"
    assert preview.ap_match_status == "matched"
    assert preview.ap_matches[0].point_id == "AP-77"


def test_scan_kicad_project_recurses_into_nested_child_sheets(tmp_path):
        root = tmp_path / "root.kicad_sch"
        child = tmp_path / "child.kicad_sch"
        grand = tmp_path / "grand.kicad_sch"

        root.write_text(
                """(kicad_sch
    (uuid \"root-project\")
    (sheet
        (uuid \"sheet-child\")
        (property \"Sheetname\" \"Child\")
        (property \"Sheetfile\" \"child.kicad_sch\")
        (pin \"RootFeed{3x1_5}\" input (uuid \"pin-root\"))
    )
)""",
                encoding="utf-8",
        )
        child.write_text(
                """(kicad_sch
    (uuid \"child-doc\")
    (label \"RootFeed.L\")
    (sheet
        (uuid \"sheet-grand\")
        (property \"Sheetname\" \"Grand\")
        (property \"Sheetfile\" \"grand.kicad_sch\")
        (pin \"GrandCircuit{5x1_5}\" output (uuid \"pin-grand\"))
    )
)""",
                encoding="utf-8",
        )
        grand.write_text(
                """(kicad_sch
    (uuid \"grand-doc\")
    (label \"GrandCircuit.PE\")
)""",
                encoding="utf-8",
        )

        result = scan_kicad_project(root)

        assert "GrandCircuit{5x1_5}" in result.candidates
        nested = result.candidates["GrandCircuit{5x1_5}"]
        assert nested.pin_refs[0].hierarchy_path == ("Child", "Grand")
        assert "GrandCircuit.PE" in nested.local_labels

        root_feed = result.candidates["RootFeed{3x1_5}"]
        assert root_feed.pin_refs[0].hierarchy_path == ("Child",)
        assert "RootFeed.L" in root_feed.local_labels


def test_scan_kicad_project_collects_hierarchical_labels_from_nested_sheets(tmp_path):
        root = tmp_path / "root.kicad_sch"
        child = tmp_path / "child.kicad_sch"
        grand = tmp_path / "grand.kicad_sch"

        root.write_text(
                """(kicad_sch
    (uuid \"root-project\")
    (sheet
        (uuid \"sheet-child\")
        (property \"Sheetname\" \"Child\")
        (property \"Sheetfile\" \"child.kicad_sch\")
    )
)""",
                encoding="utf-8",
        )
        child.write_text(
                """(kicad_sch
    (uuid \"child-doc\")
    (hierarchical_label \"ChildBus{3x1_5}\"
        (shape input)
        (at 10 10 0)
        (uuid \"label-child\")
    )
    (sheet
        (uuid \"sheet-grand\")
        (property \"Sheetname\" \"Grand\")
        (property \"Sheetfile\" \"grand.kicad_sch\")
    )
)""",
                encoding="utf-8",
        )
        grand.write_text(
                """(kicad_sch
    (uuid \"grand-doc\")
    (hierarchical_label \"GrandBus{5x1_5}\"
        (shape output)
        (at 20 20 180)
        (uuid \"label-grand\")
    )
)""",
                encoding="utf-8",
        )

        result = scan_kicad_project(root)

        assert "ChildBus{3x1_5}" in result.candidates
        assert "GrandBus{5x1_5}" in result.candidates
        child_candidate = result.candidates["ChildBus{3x1_5}"]
        grand_candidate = result.candidates["GrandBus{5x1_5}"]
        assert child_candidate.pin_refs[0].pin_direction == "hierarchical_label"
        assert child_candidate.pin_refs[0].hierarchy_path == ("Child",)
        assert grand_candidate.pin_refs[0].pin_direction == "hierarchical_label"
        assert grand_candidate.pin_refs[0].hierarchy_path == ("Child", "Grand")


def test_suggest_cable_from_candidates_prefers_matching_sheet_context():
    first_candidate = KiCadCableCandidate(
        key="candidate-1",
        base_name="Flur Licht",
        pin_name_raw="Flur Licht{3x1_5}",
        spec_raw="3x1_5",
        normalized_spec="3x1,5",
        spec_kind="count_x_spec",
        pin_refs=[
            KiCadSheetPinRef(
                sheet_uuid="sheet-1",
                sheet_name="Wohnen",
                sheet_file="wohnen.kicad_sch",
                pin_uuid="pin-1",
                pin_name_raw="Flur Licht{3x1_5}",
                pin_direction="input",
                hierarchy_path=("Wohnen",),
            )
        ],
    )
    second_candidate = KiCadCableCandidate(
        key="candidate-2",
        base_name="Flur Licht",
        pin_name_raw="Flur Licht AP{5x1_5}",
        spec_raw="5x1_5",
        normalized_spec="5x1,5",
        spec_kind="count_x_spec",
        pin_refs=[
            KiCadSheetPinRef(
                sheet_uuid="sheet-2",
                sheet_name="Ankleide",
                sheet_file="ankleide.kicad_sch",
                pin_uuid="pin-2",
                pin_name_raw="Flur Licht AP{5x1_5}",
                pin_direction="input",
                hierarchy_path=("Ankleide",),
            )
        ],
    )

    selected, matched_spec = suggest_cable_from_candidates(
        ap_name="Flur Licht",
        room="",
        candidates={
            first_candidate.key: first_candidate,
            second_candidate.key: second_candidate,
        },
        preferred_sheet_name="Ankleide",
    )

    assert selected is second_candidate
    assert matched_spec == "5x1,5"


def test_build_textfield_candidate_from_scan_uses_floor_plan_name_as_sheet_hint():
    first_candidate = KiCadCableCandidate(
        key="candidate-1",
        base_name="Flur Licht",
        pin_name_raw="Flur Licht{3x1_5}",
        spec_raw="3x1_5",
        normalized_spec="3x1,5",
        spec_kind="count_x_spec",
        pin_refs=[
            KiCadSheetPinRef(
                sheet_uuid="sheet-1",
                sheet_name="Wohnen",
                sheet_file="wohnen.kicad_sch",
                pin_uuid="pin-1",
                pin_name_raw="Flur Licht{3x1_5}",
                pin_direction="input",
                hierarchy_path=("Wohnen",),
            )
        ],
    )
    second_candidate = KiCadCableCandidate(
        key="candidate-2",
        base_name="Flur Licht",
        pin_name_raw="Flur Licht AP{5x1_5}",
        spec_raw="5x1_5",
        normalized_spec="5x1,5",
        spec_kind="count_x_spec",
        pin_refs=[
            KiCadSheetPinRef(
                sheet_uuid="sheet-2",
                sheet_name="Ankleide",
                sheet_file="ankleide.kicad_sch",
                pin_uuid="pin-2",
                pin_name_raw="Flur Licht AP{5x1_5}",
                pin_direction="input",
                hierarchy_path=("Ankleide",),
            )
        ],
    )

    textfield_candidate = build_textfield_candidate_from_scan(
        text_id="TEXT-1",
        content="AP_NAME: Flur Licht",
        candidates={
            first_candidate.key: first_candidate,
            second_candidate.key: second_candidate,
        },
        floor_plan_name="Ankleide",
    )

    assert textfield_candidate is not None
    assert textfield_candidate.best_matched_candidate is second_candidate
    assert textfield_candidate.matched_spec == "5x1,5"


def test_scan_kicad_project_collects_ap_group_candidates_from_group_prefix():
    result = scan_kicad_project(KICAD_HWR)

    ap_candidates = list(result.ap_group_candidates.values())
    assert ap_candidates, "expected at least one AP_ group candidate"
    names = {candidate.group_name for candidate in ap_candidates}
    assert "AP_1" in names


def test_scan_kicad_project_ap1_group_contains_expected_bus_overlaps():
    result = scan_kicad_project(KICAD_HWR)

    ap1 = None
    for candidate in result.ap_group_candidates.values():
        if candidate.group_name == "AP_1":
            ap1 = candidate
            break

    assert ap1 is not None
    hit_uuids = {bus.uuid for bus in ap1.bus_hits}
    assert "4cd5369a-ca39-4bcf-ba8a-257cc9153321" in hit_uuids
    assert "66f63aa4-fb92-4c51-9c47-439693eb87d5" in hit_uuids


def test_scan_kicad_project_extracts_case_insensitive_kbl_bus_candidates(tmp_path):
    root = tmp_path / "kbl_case_test.kicad_sch"
    root.write_text(
        """(kicad_sch
    (uuid \"project-kbl\")
    (rectangle (start 0 0) (end 10 10) (uuid \"rect-a\"))
    (rectangle (start 90 0) (end 100 10) (uuid \"rect-b\"))
    (bus
        (pts (xy 5 5) (xy 95 5))
        (uuid \"bus-1\")
    )
    (group \"AP_1\" (uuid \"group-ap-1\") (members \"rect-a\"))
    (group \"AP_2\" (uuid \"group-ap-2\") (members \"rect-b\"))
    (group \"kbl_MainFeed{5x10}\" (uuid \"group-kbl\") (members \"bus-1\"))
)""",
        encoding="utf-8",
    )

    result = scan_kicad_project(root)

    assert len(result.kbl_bus_candidates) == 1
    candidate = next(iter(result.kbl_bus_candidates.values()))
    assert candidate.group_name_raw == "kbl_MainFeed{5x10}"
    assert candidate.base_name == "MainFeed"
    assert candidate.spec_raw == "5x10"
    assert candidate.normalized_spec == "5x10"
    assert candidate.points[0] == (5.0, 5.0)
    assert candidate.points[-1] == (95.0, 5.0)
    assert build_kicad_bus_cable_key(result.project_uuid, candidate) == "project-kbl::kbl_bus::group-kbl::bus-1"


def test_build_import_preview_maps_kbl_bus_endpoints_to_ap_names(tmp_path):
    root = tmp_path / "kbl_preview_test.kicad_sch"
    root.write_text(
        """(kicad_sch
    (uuid \"project-kbl-preview\")
    (rectangle (start 0 0) (end 10 10) (uuid \"rect-a\"))
    (rectangle (start 90 0) (end 100 10) (uuid \"rect-b\"))
    (bus
        (pts (xy 5 5) (xy 95 5))
        (uuid \"bus-1\")
    )
    (group \"AP_1\" (uuid \"group-ap-1\") (members \"rect-a\"))
    (group \"AP_2\" (uuid \"group-ap-2\") (members \"rect-b\"))
    (group \"KBL_MainFeed{5x10}\" (uuid \"group-kbl\") (members \"bus-1\"))
)""",
        encoding="utf-8",
    )

    result = scan_kicad_project(root)
    previews = build_import_preview(
        result,
        existing_cables=[],
        elec_points=[
            {"id": "AP-1", "name": "AP_1", "floor_plan_id": "grundriss-1"},
            {"id": "AP-2", "name": "AP_2", "floor_plan_id": "grundriss-1"},
        ],
    )

    kbl_previews = [preview for preview in previews if preview.source == "kbl_bus"]
    assert len(kbl_previews) == 1
    preview = kbl_previews[0]
    assert preview.cable_name == "MainFeed"
    assert preview.cable_type == "5x10"
    assert preview.ap_match_status == "matched"
    assert preview.start_ap_group == "AP_1"
    assert preview.end_ap_group == "AP_2"
    assert preview.start_ap_status == "matched"
    assert preview.end_ap_status == "matched"
    assert preview.start_ap_id == "AP-1"
    assert preview.end_ap_id == "AP-2"


def test_build_import_preview_maps_kbl_bus_endpoints_with_margin_tolerance(tmp_path):
    root = tmp_path / "kbl_preview_margin_test.kicad_sch"
    root.write_text(
        """(kicad_sch
    (uuid "project-kbl-margin")
    (rectangle (start 0 0) (end 10 10) (uuid "rect-a"))
    (rectangle (start 90 0) (end 100 10) (uuid "rect-b"))
    (bus
        (pts (xy -2 5) (xy 102 5))
        (uuid "bus-1")
    )
    (group "AP_1" (uuid "group-ap-1") (members "rect-a"))
    (group "AP_2" (uuid "group-ap-2") (members "rect-b"))
    (group "KBL_MainFeed{5x10}" (uuid "group-kbl") (members "bus-1"))
)""",
        encoding="utf-8",
    )

    result = scan_kicad_project(root)
    previews = build_import_preview(
        result,
        existing_cables=[],
        elec_points=[
            {"id": "AP-1", "name": "AP_1", "floor_plan_id": "grundriss-1"},
            {"id": "AP-2", "name": "AP_2", "floor_plan_id": "grundriss-1"},
        ],
    )

    kbl_previews = [preview for preview in previews if preview.source == "kbl_bus"]
    assert len(kbl_previews) == 1
    preview = kbl_previews[0]
    assert preview.start_ap_status == "matched"
    assert preview.end_ap_status == "matched"
    assert preview.start_ap_id == "AP-1"
    assert preview.end_ap_id == "AP-2"


def test_build_import_preview_maps_kbl_bus_endpoint_when_ap_prefix_differs(tmp_path):
    root = tmp_path / "kbl_preview_name_norm_test.kicad_sch"
    root.write_text(
        """(kicad_sch
    (uuid "project-kbl-name")
    (rectangle (start 0 0) (end 10 10) (uuid "rect-a"))
    (rectangle (start 90 0) (end 100 10) (uuid "rect-b"))
    (bus
        (pts (xy 5 5) (xy 95 5))
        (uuid "bus-1")
    )
    (group "AP_Herdanschluss" (uuid "group-ap-1") (members "rect-a"))
    (group "AP_Backofen" (uuid "group-ap-2") (members "rect-b"))
    (group "KBL_Kueche{5x2_5}" (uuid "group-kbl") (members "bus-1"))
)""",
        encoding="utf-8",
    )

    result = scan_kicad_project(root)
    previews = build_import_preview(
        result,
        existing_cables=[],
        elec_points=[
            {"id": "AP-1", "name": "Herdanschluss", "floor_plan_id": "grundriss-1"},
            {"id": "AP-2", "name": "Backofen", "floor_plan_id": "grundriss-1"},
        ],
    )

    kbl_previews = [preview for preview in previews if preview.source == "kbl_bus"]
    assert len(kbl_previews) == 1
    preview = kbl_previews[0]
    assert preview.start_ap_status == "matched"
    assert preview.end_ap_status == "matched"
    assert preview.start_ap_id == "AP-1"
    assert preview.end_ap_id == "AP-2"


def test_build_import_preview_resolves_kbl_label_counterpart_across_hierarchy(tmp_path):
    root = tmp_path / "kbl_label_hierarchy_test.kicad_sch"
    child = tmp_path / "child.kicad_sch"
    grand = tmp_path / "grand.kicad_sch"

    root.write_text(
        """(kicad_sch
    (uuid "project-kbl-hier")
    (sheet
        (uuid "sheet-child")
        (property "Sheetname" "Child")
        (property "Sheetfile" "child.kicad_sch")
        (pin "KBL_Backofen{3x1_5}" output (uuid "pin-kbl"))
    )
)""",
        encoding="utf-8",
    )

    child.write_text(
        """(kicad_sch
    (uuid "child-doc")
    (hierarchical_label "KBL_Backofen{3x1_5}"
        (shape output)
        (at 10 10 180)
        (uuid "label-kbl")
    )
    (sheet
        (uuid "sheet-grand")
        (property "Sheetname" "Grand")
        (property "Sheetfile" "grand.kicad_sch")
        (pin "Herdanschluss{5x2_5}" input (uuid "pin-herd"))
    )
)""",
        encoding="utf-8",
    )

    grand.write_text(
        """(kicad_sch
    (uuid "grand-doc")
    (hierarchical_label "Herdanschluss{5x2_5}"
        (shape input)
        (at 20 20 0)
        (uuid "label-herd")
    )
)""",
        encoding="utf-8",
    )

    result = scan_kicad_project(root)
    previews = build_import_preview(
        result,
        existing_cables=[],
        elec_points=[
            {"id": "AP-1", "name": "Backofen", "floor_plan_id": "grundriss-1"},
            {"id": "AP-2", "name": "Herdanschluss", "floor_plan_id": "grundriss-1"},
        ],
    )

    kbl_label_previews = [preview for preview in previews if preview.source == "kbl_label"]
    assert len(kbl_label_previews) == 1
    preview = kbl_label_previews[0]
    assert preview.cable_name == "Backofen"
    assert preview.start_ap_status == "matched"
    assert preview.end_ap_status == "matched"
    assert preview.start_ap_id == "AP-1"
    assert preview.end_ap_id == "AP-2"


def test_build_import_preview_prefers_uv_spk_counterpart_for_kbl_label(tmp_path):
    root = tmp_path / "kbl_label_uv_spk_test.kicad_sch"
    child = tmp_path / "child.kicad_sch"
    uv_sheet = tmp_path / "UV_SPK.kicad_sch"

    root.write_text(
        """(kicad_sch
    (uuid "project-kbl-uvspk")
    (sheet
        (uuid "sheet-child")
        (property "Sheetname" "Speisekammer")
        (property "Sheetfile" "child.kicad_sch")
        (pin "KBL_Backofen{3x1_5}" output (uuid "pin-kbl"))
    )
)""",
        encoding="utf-8",
    )

    child.write_text(
        """(kicad_sch
    (uuid "child-doc")
    (sheet
        (uuid "sheet-uv")
        (property "Sheetname" "UV_SPK")
        (property "Sheetfile" "UV_SPK.kicad_sch")
        (pin "KBL_Backofen{3x1_5}" output (uuid "pin-kbl-child"))
    )
    (hierarchical_label "KBL_Backofen{3x1_5}"
        (shape output)
        (at 10 10 180)
        (uuid "label-kbl")
    )
    (hierarchical_label "Herdanschluss{5x2_5}"
        (shape input)
        (at 20 20 0)
        (uuid "label-herd")
    )
)""",
        encoding="utf-8",
    )

    uv_sheet.write_text(
        """(kicad_sch
    (uuid "uv-doc")
    (hierarchical_label "KBL_Backofen{3x1_5}"
        (shape output)
        (at 30 30 180)
        (uuid "label-kbl-uv")
    )
)""",
        encoding="utf-8",
    )

    result = scan_kicad_project(root)
    previews = build_import_preview(
        result,
        existing_cables=[],
        elec_points=[
            {"id": "AP-1", "name": "Backofen", "floor_plan_id": "grundriss-1"},
            {"id": "AP-2", "name": "Herdanschluss", "floor_plan_id": "grundriss-1"},
            {"id": "AP-3", "name": "AP_UV_SPK", "floor_plan_id": "grundriss-1"},
            {"id": "AP-4", "name": "UV_spk", "floor_plan_id": "grundriss-1"},
        ],
    )

    preview = next(p for p in previews if p.source == "kbl_label")
    assert preview.start_ap_status == "matched"
    assert preview.start_ap_id == "AP-1"
    assert preview.end_ap_status == "matched"
    assert preview.end_ap_id == "AP-3"


def test_build_import_preview_resolves_kbl_kueche3_between_sd2_and_sd3(tmp_path):
    root = tmp_path / "kbl_kueche3_test.kicad_sch"
    root.write_text(
        """(kicad_sch
    (uuid "project-kbl-kueche3")
    (label "KBL_Küche3{3x1_5}"
        (at 10 10 0)
        (uuid "label-kbl-kueche3")
    )
    (label "SD_Küche{3x1_5}"
        (at 20 20 0)
        (uuid "label-sd-kueche")
    )
)""",
        encoding="utf-8",
    )

    result = scan_kicad_project(root)
    previews = build_import_preview(
        result,
        existing_cables=[],
        elec_points=[
            {"id": "AP-1", "name": "AP_SD_Küche2", "floor_plan_id": "grundriss-1"},
            {"id": "AP-2", "name": "AP_SD_Küche3", "floor_plan_id": "grundriss-1"},
            {"id": "AP-3", "name": "SD_Küche2", "floor_plan_id": "grundriss-1"},
            {"id": "AP-4", "name": "SD_Küche3", "floor_plan_id": "grundriss-1"},
        ],
    )

    preview = next(p for p in previews if p.source == "kbl_label" and "Küche3" in p.candidate_key)
    assert preview.start_ap_status == "matched"
    assert preview.start_ap_id == "AP-2"
    assert preview.end_ap_status == "matched"
    assert preview.end_ap_id == "AP-1"


def test_build_import_preview_prefers_local_sd_group_neighbor_for_kbl_kueche3(tmp_path):
    root = tmp_path / "kbl_kueche3_local_groups.kicad_sch"
    root.write_text(
        """(kicad_sch
    (uuid "project-kbl-kueche3-local")
    (label "KBL_Küche3{3x1_5}" (at 10 10 0) (uuid "label-kbl"))
    (rectangle (start 0 0) (end 10 10) (uuid "rect-1"))
    (rectangle (start 20 0) (end 30 10) (uuid "rect-2"))
    (rectangle (start 40 0) (end 50 10) (uuid "rect-3"))
    (rectangle (start 60 0) (end 70 10) (uuid "rect-4"))
    (group "AP_SD_Küche1" (uuid "group-1") (members "rect-1"))
    (group "AP_SD_Küche2" (uuid "group-2") (members "rect-2"))
    (group "AP_SD_Küche3" (uuid "group-3") (members "rect-3"))
    (group "AP_SD_Küche4" (uuid "group-4") (members "rect-4"))
)""",
        encoding="utf-8",
    )

    result = scan_kicad_project(root)
    previews = build_import_preview(
        result,
        existing_cables=[],
        elec_points=[
            {"id": "AP-1", "name": "SD_Küche2", "floor_plan_id": "grundriss-1"},
            {"id": "AP-2", "name": "SD_Küche3", "floor_plan_id": "grundriss-1"},
            {"id": "AP-3", "name": "SD_Küche4", "floor_plan_id": "grundriss-1"},
        ],
    )

    preview = next(p for p in previews if p.source == "kbl_label" and p.candidate_key == "KBL_Küche3{3x1_5}")
    assert preview.start_ap_status == "matched"
    assert preview.start_ap_id == "AP-2"
    assert preview.end_ap_status == "matched"
    assert preview.end_ap_id == "AP-1"


def test_build_import_preview_prefers_numbered_neighbor_in_generic_series(tmp_path):
    root = tmp_path / "kbl_zimmer_series.kicad_sch"
    root.write_text(
        """(kicad_sch
    (uuid "project-kbl-zimmer-series")
    (label "KBL_Zimmer3{3x1_5}" (at 10 10 0) (uuid "label-kbl-z3"))
    (label "Zimmer{3x1_5}" (at 20 20 0) (uuid "label-zimmer"))
)""",
        encoding="utf-8",
    )

    result = scan_kicad_project(root)
    previews = build_import_preview(
        result,
        existing_cables=[],
        elec_points=[
            {"id": "AP-1", "name": "AP_Zimmer1", "floor_plan_id": "grundriss-1"},
            {"id": "AP-2", "name": "AP_Zimmer2", "floor_plan_id": "grundriss-1"},
            {"id": "AP-3", "name": "AP_Zimmer3", "floor_plan_id": "grundriss-1"},
            {"id": "AP-4", "name": "AP_Zimmer4", "floor_plan_id": "grundriss-1"},
        ],
    )

    preview = next(p for p in previews if p.source == "kbl_label" and p.candidate_key == "KBL_Zimmer3{3x1_5}")
    assert preview.start_ap_status == "matched"
    assert preview.start_ap_id == "AP-3"
    assert preview.end_ap_status == "matched"
    assert preview.end_ap_id == "AP-2"


def test_build_import_preview_does_not_create_reverse_pair_for_lowest_index(tmp_path):
    root = tmp_path / "kbl_lowest_index_no_reverse.kicad_sch"
    root.write_text(
        """(kicad_sch
    (uuid "project-kbl-lowest")
    (label "KBL_Küche1{3x1_5}" (at 10 10 0) (uuid "label-kbl-1"))
    (label "KBL_Küche2{3x1_5}" (at 20 20 0) (uuid "label-kbl-2"))
    (label "SD_Küche{3x1_5}" (at 30 30 0) (uuid "label-sd"))
    (rectangle (start 0 0) (end 10 10) (uuid "rect-1"))
    (rectangle (start 20 0) (end 30 10) (uuid "rect-2"))
    (group "AP_SD_Küche1" (uuid "group-1") (members "rect-1"))
    (group "AP_SD_Küche2" (uuid "group-2") (members "rect-2"))
)""",
        encoding="utf-8",
    )

    result = scan_kicad_project(root)
    previews = build_import_preview(
        result,
        existing_cables=[],
        elec_points=[
            {"id": "AP-1", "name": "SD_Küche1", "floor_plan_id": "grundriss-1"},
            {"id": "AP-2", "name": "SD_Küche2", "floor_plan_id": "grundriss-1"},
        ],
    )

    by_key = {p.candidate_key: p for p in previews if p.source == "kbl_label"}
    p1 = by_key["KBL_Küche1{3x1_5}"]
    p2 = by_key["KBL_Küche2{3x1_5}"]

    assert p1.start_ap_id == "AP-1"
    assert p1.start_ap_status == "matched"
    assert p1.end_ap_id == ""
    assert p1.end_ap_status == "unmatched"

    assert p2.start_ap_id == "AP-2"
    assert p2.start_ap_status == "matched"
    assert p2.end_ap_id == "AP-1"
    assert p2.end_ap_status == "matched"