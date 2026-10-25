import math

import pytest

from logic.heating_curve_analysis import analyze, circuit_heat_demand, default_config, heat_demand, range_values, validate_config
from logic.heating_calc import calc_circuit


def circuit(**changes):
    return dict(id="HK-1", name="Raum", distributor="HKV", area_m2=12.0,
                route_m=80.0, total_m=100.0, room_temp=20.0, spacing_mm=150.0,
                diameter_mm=16.0, floor_covering="Fliesen / Keramik", **changes)


def config():
    result = default_config()
    result.update(supply=[30, 40, 5], spread=[3, 5, 2], outdoor=[-12, 15, 27])
    result["circuits"] = {"HK-1": {"heat_load_w": 500}}
    return result


def reference_settings(**changes):
    return {"load_mode": "reference_specific", "specific_heat_load_wm2": 50,
            "reference_room_temp": 20, "reference_outdoor_temp": -13, **changes}


def test_reference_specific_demand():
    room = circuit()
    settings = reference_settings()
    assert circuit_heat_demand(room, settings, -13, -12, 15) == 600
    assert circuit_heat_demand(room, settings, 0, -12, 15) == pytest.approx(12 * 30.3030303)
    room["room_temp"] = 24
    assert circuit_heat_demand(room, settings, 0, -12, 15) == pytest.approx(600 * 24 / 33)
    assert circuit_heat_demand(room, settings, 15, -12, 15) == 0
    settings["reference_area_m2"] = 20
    assert circuit_heat_demand(room, settings, -9, -12, 15) == 1000


def test_reference_missing_zero_and_validation():
    settings = config()
    settings["circuits"]["HK-1"] = reference_settings(specific_heat_load_wm2=None)
    assert analyze([circuit()], settings, 35, 30, -12)["curve"][0]["status"] == "Heizlast unbekannt"
    settings["circuits"]["HK-1"]["specific_heat_load_wm2"] = 0
    assert analyze([circuit()], settings, 35, 30, -12)["curve"][0]["status"] == "OK"
    for changes in ({"reference_outdoor_temp": 20}, {"reference_area_m2": 0},
                    {"specific_heat_load_wm2": -1}, {"reference_room_temp": math.nan}):
        settings["circuits"]["HK-1"] = reference_settings(**changes)
        with pytest.raises(ValueError):
            validate_config(settings, -12)


def test_mixed_modes_use_per_circuit_demand():
    settings = config()
    settings["outdoor"] = [0, 0, 1]
    settings["circuits"]["HK-2"] = reference_settings()
    second = circuit()
    second["id"] = "HK-2"
    result = analyze([circuit(), second], settings, 35, 30, -12)
    assert result["details"][0]["demand_w"] == pytest.approx(500 * 15 / 27)
    assert result["details"][1]["demand_w"] == pytest.approx(600 * 20 / 33)
    assert result["details"][1]["reference_area_m2"] == 12
    assert result["curve"][0]["demand_w"] == pytest.approx(500 * 15 / 27 + 600 * 20 / 33)


def test_reference_area_tracks_geometry_unless_overridden():
    room = circuit()
    settings = reference_settings()
    room["area_m2"] = 6
    assert circuit_heat_demand(room, settings, -13, -12, 15) == 300
    settings["reference_area_m2"] = 12
    assert circuit_heat_demand(room, settings, -13, -12, 15) == 600
    room["area_m2"] = 0
    settings["reference_area_m2"] = None
    assert circuit_heat_demand(room, settings, -13, -12, 15) is None


def test_reference_schema_roundtrip_and_semantic_errors():
    import json
    from pathlib import Path
    from model.document import Document
    from validate_hrp import validate_schema, validate_semantic

    root = Path(__file__).resolve().parents[1]
    raw = json.loads((root / "examples/minimal.hrp").read_text(encoding="utf-8"))
    settings = config()
    settings["circuits"] = {key: reference_settings(reference_area_m2=12)
                            for key in raw["params"].get("circuits", {})}
    raw["params"]["heating_curve_analysis"] = settings
    schema = json.loads((root / "hrp_schema.json").read_text(encoding="utf-8"))
    assert settings["circuits"]
    assert not validate_schema(raw, schema)
    assert not validate_semantic(raw)[0]
    assert Document.from_dict(raw).to_dict()["params"]["heating_curve_analysis"] == settings
    specific = next(iter(settings["circuits"].values()))
    specific["reference_outdoor_temp"] = 20
    assert any("Referenz" in error for error in validate_semantic(raw)[0])
    specific["reference_area_m2"] = 0
    assert validate_schema(raw, schema)


def test_ranges_and_load_model():
    assert range_values([1, 1.3, 0.1]) == [1, 1.1, 1.2, 1.3]
    assert range_values([1, 1.25, 0.1]) == [1, 1.1, 1.2]
    for bounds in ([1, 2, 0], [2, 1, 1], [0, math.inf, 1], [20, 90, 1e-30]):
        with pytest.raises(ValueError):
            range_values(bounds)
    assert heat_demand(500, -12, -12, 15) == 500
    assert heat_demand(500, 15, -12, 15) == 0
    assert heat_demand(500, -39, -12, 15) == 1000


def test_reference_and_lowest_supply():
    result = analyze([circuit()], config(), 35, 30, -12)
    reference = calc_circuit(35, 30, 20, 15, 0.01, 12, 40, 16, 100)
    assert result["baseline"]["HK-1"]["power_w"] == pytest.approx(reference["power_w"])
    assert result["curve"][0]["supply"] == 35
    assert result["curve"][0]["spread"] == 5
    assert result["curve"][1]["status"] == "Heizung aus"
    assert result["details"][0]["coverage_percent"] >= 100
    assert result["details"][1]["power_w"] == 0
    same = next(row for row in result["points"] if row["supply"] == 35 and row["spread"] == 5)
    assert same["delta_power_w"] == 0


@pytest.mark.parametrize("hydraulic_limit", [None, 2.0])
def test_outdoor_sweep_finds_lowest_common_feasible_supply(hydraulic_limit):
    settings = config()
    settings.update(supply=[25, 45, 1], spread=[3, 7, 2], outdoor=[-13, 15, 7],
                    max_flow=hydraulic_limit)
    settings["circuits"] = {"HK-1": reference_settings(),
                            "HK-2": reference_settings(specific_heat_load_wm2=65)}
    second = circuit()
    second.update(id="HK-2", room_temp=24)
    result = analyze([circuit(), second], settings, 35, 30, -12)
    assert [row["outdoor"] for row in result["curve"]] == [-13, -6, 1, 8, 15]
    required = []
    for row in result["curve"]:
        if row["status"] == "Heizung aus":
            assert row["supply"] is None
            continue
        details = [detail for detail in result["details"] if detail["outdoor"] == row["outdoor"]]
        demands = {detail["id"]: detail["demand_w"] for detail in details}
        feasible = []
        for supply in range(25, 46):
            for spread in (3, 5, 7):
                points = [point for point in result["points"]
                          if point["supply"] == supply and point["spread"] == spread]
                if all(point["status"] == "OK" and point["power_w"] + 1e-9 >= demands[point["id"]]
                       for point in points):
                    feasible.append(supply)
        if feasible:
            assert row["status"] == "OK"
            assert row["supply"] == min(feasible)
            assert all(detail["power_w"] + 1e-9 >= detail["demand_w"] for detail in details)
            required.append(row["supply"])
        else:
            assert row["supply"] is None
            assert row["status"] in ("Hydrauliklimit", "Bedarf nicht gedeckt")
    assert required
    if hydraulic_limit is None:
        assert required == sorted(required, reverse=True)


def test_missing_zero_and_limits():
    settings = config()
    settings["circuits"] = {}
    assert analyze([circuit()], settings, 35, 30, -12)["curve"][0]["status"] == "Heizlast unbekannt"
    settings["circuits"] = {"HK-1": {"heat_load_w": 0}}
    assert analyze([circuit()], settings, 35, 30, -12)["curve"][0]["status"] == "OK"
    settings["max_flow"] = 0
    assert analyze([circuit()], settings, 35, 30, -12)["curve"][0]["status"] == "Hydrauliklimit"


def test_each_circuit_must_be_covered_and_inputs_unchanged():
    settings = config()
    settings["circuits"]["HK-2"] = {"heat_load_w": 100000}
    second = circuit()
    second["id"] = "HK-2"
    assert analyze([circuit(), second], settings, 35, 30, -12)["curve"][0]["supply"] is None
    assert settings["circuits"]["HK-2"]["heat_load_w"] == 100000


def test_missing_geometry_and_invalid_return():
    incomplete = circuit()
    incomplete["route_m"] = 0
    assert analyze([incomplete], config(), 35, 30, -12)["curve"][0]["supply"] is None
    settings = config()
    settings["supply"] = [24, 24, 1]
    settings["spread"] = [5, 5, 1]
    result = analyze([circuit()], settings, 20, 20, -12)
    assert result["points"][0]["status"] == "Ruecklauf <= Raumtemperatur"
    assert result["points"][0]["percent_power_w"] is None


def test_config_roundtrip_and_validation():
    import json
    from pathlib import Path
    from model.document import Document
    from validate_hrp import validate_schema, validate_semantic

    root = Path(__file__).resolve().parents[1]
    raw = json.loads((root / "examples/minimal.hrp").read_text(encoding="utf-8"))
    settings = config()
    settings["circuits"] = {key: {"heat_load_w": 500} for key in raw["params"].get("circuits", {})}
    raw["params"]["heating_curve_analysis"] = settings
    schema = json.loads((root / "hrp_schema.json").read_text(encoding="utf-8"))
    assert not validate_schema(raw, schema)
    assert not validate_semantic(raw)[0]
    assert Document.from_dict(raw).to_dict()["params"]["heating_curve_analysis"] == settings
    settings["circuits"]["HK-99999"] = {"heat_load_w": 0}
    assert any("HK-99999" in error for error in validate_semantic(raw)[0])
    settings["spread"] = [0, 10, 1]
    assert any("Spreizung" in error for error in validate_semantic(raw)[0])


def test_power_and_hydraulics_change_with_temperature():
    result = analyze([circuit()], config(), 35, 30, -12)
    rows = {(row["supply"], row["spread"]): row for row in result["points"]}
    assert rows[40, 5]["power_w"] > rows[35, 5]["power_w"]
    assert rows[40, 5]["pressure_drop_mbar"] > rows[35, 5]["pressure_drop_mbar"]
    assert rows[35, 3]["volume_flow_lmin"] > rows[35, 5]["volume_flow_lmin"]
    assert rows[35, 3]["power_w"] > rows[35, 5]["power_w"]


def test_floorplan_scale_and_double_pipe_lengths():
    from model.computed import heating_length_overview
    from model.document import Document

    raw = {"canvas": {"mm_per_px": 1, "floor_plans": [{"fp_id": "grundriss-1", "mm_per_px": 100}],
           "polygons": {"HK-1": [[0, 0], [40, 0], [40, 30], [0, 30]]},
           "manual_routes": {"HK-1": [[0, 0], [400, 0]]},
           "supply_lines": {"HK-1": [[0, 0], [100, 0]]}},
           "params": {"floorplans": {"grundriss-1": {"name": "EG"}},
           "circuits": {"HK-1": {"floor_plan_id": "grundriss-1", "spacing": 150,
                        "diameter": 16, "room_temp": 20, "floor_covering": "Fliesen / Keramik"}}}}
    doc = Document.from_dict(raw)
    before = doc.to_dict()
    rows, supply, return_temp = heating_length_overview(doc)
    assert rows[0]["area_m2"] == 12
    assert rows[0]["route_m"] == 80
    assert rows[0]["supply_m"] == 20
    assert rows[0]["total_m"] == 100
    result = analyze(rows, config(), supply, return_temp, -12)
    assert result["baseline"]["HK-1"]["pressure_drop_mbar"] == pytest.approx(rows[0]["pressure_drop_mbar"])
    assert doc.to_dict() == before


def test_empty_project_and_workload_guard():
    assert analyze([], config(), 35, 30, -12)["curve"][0]["status"] == "Keine Heizkreise"
    settings = config()
    settings.update(supply=[20, 90, 0.01], spread=[1, 10, 0.01])
    with pytest.raises(ValueError, match="Raster"):
        analyze([circuit()], settings, 35, 30, -12)