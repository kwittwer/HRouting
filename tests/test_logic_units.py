"""Unit tests for heating calculations, SVG parsing and HRP validation."""

from __future__ import annotations

import json
from pathlib import Path

from logic.heating_calc import (
    FLOOR_COVERINGS,
    calc_circuit,
    calc_specific_heat_output,
)
from logic.hrp_import import import_selected_elements, resolve_import_selection, selection_key
from model.document import Document
from model.elements import Circuit, ElecCable, ElecPoint, FloorPlan, Hkv, HkvLine
from logic.svg_parser import parse_svg_dimensions
from validate_hrp import validate_schema, validate_semantic

ROOT = Path(__file__).resolve().parents[1]


def test_heating_specific_output_reference_range():
    r_lambda = FLOOR_COVERINGS["Fliesen / Keramik"]
    q_wm2 = calc_specific_heat_output(
        t_supply=35.0,
        t_return=30.0,
        t_room=20.0,
        spacing_cm=15.0,
        r_lambda_b=r_lambda,
    )

    # Reference run is around 46.1 W/m2 for this setup.
    assert 40.0 <= q_wm2 <= 52.0


def test_calc_circuit_uses_total_pipe_length_for_pressure_drop():
    r_lambda = FLOOR_COVERINGS["Fliesen / Keramik"]
    short = calc_circuit(
        t_supply=35.0,
        t_return=30.0,
        t_room=20.0,
        spacing_cm=15.0,
        r_lambda_b=r_lambda,
        area_m2=12.0,
        pipe_length_m=80.0,
        outer_diameter_mm=16.0,
        total_pipe_length_m=90.0,
    )
    long = calc_circuit(
        t_supply=35.0,
        t_return=30.0,
        t_room=20.0,
        spacing_cm=15.0,
        r_lambda_b=r_lambda,
        area_m2=12.0,
        pipe_length_m=80.0,
        outer_diameter_mm=16.0,
        total_pipe_length_m=130.0,
    )

    assert long["pressure_drop_mbar"] > short["pressure_drop_mbar"]


def test_parse_svg_dimensions_supports_units_and_viewbox(tmp_path):
    svg = tmp_path / "sample.svg"
    svg.write_text(
        '<svg width="210mm" height="297mm" viewBox="0 0 1000 2000"></svg>',
        encoding="utf-8",
    )

    parsed = parse_svg_dimensions(str(svg))
    assert parsed["width"] is not None
    assert parsed["height"] is not None
    assert parsed["viewBox"] == {
        "min_x": 0.0,
        "min_y": 0.0,
        "width": 1000.0,
        "height": 2000.0,
    }


def test_validate_hrp_minimal_example_passes_schema_and_semantic():
    data = json.loads((ROOT / "examples" / "minimal.hrp").read_text(encoding="utf-8"))
    schema = json.loads((ROOT / "hrp_schema.json").read_text(encoding="utf-8"))

    schema_errors = validate_schema(data, schema)
    semantic_errors, semantic_warnings = validate_semantic(data)

    assert schema_errors == []
    assert semantic_errors == []
    assert semantic_warnings == []


def test_validate_hrp_detects_invalid_ap_reference():
    data = json.loads((ROOT / "examples" / "minimal.hrp").read_text(encoding="utf-8"))
    data["params"]["elec_cables"]["EK-1"]["start_ap"] = "AP-999"

    errors, _warnings = validate_semantic(data)

    assert any("elec_cables.EK-1.start_ap" in msg for msg in errors)


def test_resolve_import_selection_auto_includes_dependencies():
    source = Document.from_dict(
        {
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                "hkv_points": {"HKV-1": [10, 10]},
                "elec_points": {"AP-1": [20, 20], "AP-2": [40, 40]},
                "elec_cables": {"EK-1": [[20, 20], [40, 40]]},
                "cable_start_ap": {"EK-1": "AP-1"},
                "cable_end_ap": {"EK-1": "AP-2"},
                "polygons": {"HK-1": [[0, 0], [100, 0], [100, 100]]},
                "supply_hkv": {"HK-1": "HKV-1"},
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG", "file_path": ""}},
                "hkv_points": {"HKV-1": {"hkv_id": "HKV-1", "name": "Verteiler", "floor_plan_id": "grundriss-1"}},
                "elec_points": {
                    "AP-1": {"point_id": "AP-1", "name": "Dose 1", "floor_plan_id": "grundriss-1"},
                    "AP-2": {"point_id": "AP-2", "name": "Dose 2", "floor_plan_id": "grundriss-1"},
                },
                "elec_cables": {
                    "EK-1": {
                        "cable_id": "EK-1",
                        "name": "Kabel 1",
                        "floor_plan_id": "grundriss-1",
                        "start_ap": "AP-1",
                        "end_ap": "AP-2",
                    }
                },
                "circuits": {
                    "HK-1": {
                        "circuit_id": "HK-1",
                        "name": "Wohnen",
                        "floor_plan_id": "grundriss-1",
                    }
                },
            },
        }
    )

    selection = resolve_import_selection(
        source,
        [selection_key(ElecCable, "EK-1"), selection_key(Circuit, "HK-1")],
    )

    assert selection.selected_keys == (
        selection_key(ElecCable, "EK-1"),
        selection_key(Circuit, "HK-1"),
    )
    assert selection_key(FloorPlan, "grundriss-1") in selection.auto_included_keys
    assert selection_key(Hkv, "HKV-1") in selection.auto_included_keys
    assert selection_key(ElecPoint, "AP-1") in selection.auto_included_keys
    assert selection_key(ElecPoint, "AP-2") in selection.auto_included_keys


def test_import_selected_elements_rewrites_ids_and_references():
    source = Document.from_dict(
        {
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1", "visible": True, "mm_per_px": 25.0}],
                "hkv_points": {"HKV-1": [10, 10]},
                "hkv_lines": {"HKVL-1": [[10, 10], [20, 10]]},
                "hkv_line_start": {"HKVL-1": "HKV-1"},
                "hkv_line_end": {"HKVL-1": "HKV-1"},
                "elec_points": {"AP-1": [20, 20], "AP-2": [40, 40]},
                "elec_cables": {"EK-1": [[20, 20], [40, 40]]},
                "cable_start_ap": {"EK-1": "AP-1"},
                "cable_end_ap": {"EK-1": "AP-2"},
                "polygons": {"HK-1": [[0, 0], [100, 0], [100, 100]]},
                "supply_hkv": {"HK-1": "HKV-1"},
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG", "file_path": ""}},
                "floorplans_order": ["grundriss-1"],
                "hkv_points": {"HKV-1": {"hkv_id": "HKV-1", "name": "Verteiler", "floor_plan_id": "grundriss-1"}},
                "hkv_lines": {
                    "HKVL-1": {
                        "line_id": "HKVL-1",
                        "name": "Linie",
                        "floor_plan_id": "grundriss-1",
                        "start_hkv": "HKV-1",
                        "end_hkv": "HKV-1",
                    }
                },
                "elec_points": {
                    "AP-1": {"point_id": "AP-1", "name": "Dose 1", "floor_plan_id": "grundriss-1"},
                    "AP-2": {"point_id": "AP-2", "name": "Dose 2", "floor_plan_id": "grundriss-1"},
                },
                "elec_cables": {
                    "EK-1": {
                        "cable_id": "EK-1",
                        "name": "Kabel 1",
                        "floor_plan_id": "grundriss-1",
                        "start_ap": "AP-1",
                        "end_ap": "AP-2",
                    }
                },
                "circuits": {
                    "HK-1": {
                        "circuit_id": "HK-1",
                        "name": "Wohnen",
                        "floor_plan_id": "grundriss-1",
                    }
                },
            },
        }
    )
    target = Document.from_dict(
        {
            "canvas": {
                "floor_plans": [{"fp_id": "grundriss-1", "visible": True}],
                "elec_points": {"AP-1": [1, 1]},
            },
            "params": {
                "floorplans": {"grundriss-1": {"name": "Bestand", "file_path": ""}},
                "floorplans_order": ["grundriss-1"],
                "elec_points": {"AP-1": {"point_id": "AP-1", "name": "Alt", "floor_plan_id": "grundriss-1"}},
            },
        }
    )

    result = import_selected_elements(
        source,
        target,
        [selection_key(ElecCable, "EK-1"), selection_key(Circuit, "HK-1"), selection_key(HkvLine, "HKVL-1")],
    )

    floor_id = result.id_map[selection_key(FloorPlan, "grundriss-1")]
    hkv_id = result.id_map[selection_key(Hkv, "HKV-1")]
    ap1_id = result.id_map[selection_key(ElecPoint, "AP-1")]
    ap2_id = result.id_map[selection_key(ElecPoint, "AP-2")]
    cable_id = result.id_map[selection_key(ElecCable, "EK-1")]
    circuit_id = result.id_map[selection_key(Circuit, "HK-1")]
    hkv_line_id = result.id_map[selection_key(HkvLine, "HKVL-1")]

    assert floor_id == "grundriss-2"
    assert target.floorplan_order == ["grundriss-1", "grundriss-2"]

    imported_cable = target.elements["elec_cables"][cable_id]
    assert imported_cable.floor_plan_id == floor_id
    assert imported_cable.start_ap == ap1_id
    assert imported_cable.end_ap == ap2_id
    assert imported_cable.geom["cable_start_ap"] == ap1_id
    assert imported_cable.geom["cable_end_ap"] == ap2_id

    imported_circuit = target.elements["circuits"][circuit_id]
    assert imported_circuit.floor_plan_id == floor_id
    assert imported_circuit.hkv_id == hkv_id

    imported_hkv_line = target.elements["hkv_lines"][hkv_line_id]
    assert imported_hkv_line.floor_plan_id == floor_id
    assert imported_hkv_line.start_hkv == hkv_id
    assert imported_hkv_line.end_hkv == hkv_id
    assert imported_hkv_line.geom["hkv_line_start"] == hkv_id
    assert imported_hkv_line.geom["hkv_line_end"] == hkv_id
