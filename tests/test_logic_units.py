"""Unit tests for heating calculations, SVG parsing and HRP validation."""

from __future__ import annotations

import json
from pathlib import Path

from logic.heating_calc import (
    FLOOR_COVERINGS,
    calc_circuit,
    calc_specific_heat_output,
)
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
