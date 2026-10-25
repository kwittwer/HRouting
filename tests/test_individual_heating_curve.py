import pytest

from logic.heating_calc import FLOOR_COVERINGS, calc_specific_heat_output
from logic.heating_curve_analysis import individual_curve_steps, required_supply_temperature


def circuit(circuit_id="HK-1", room=20):
    return {"id": circuit_id, "name": circuit_id, "room_temp": room, "area_m2": 12,
            "spacing_mm": 150, "floor_covering": "Fliesen / Keramik"}


def reference(load=50):
    return {"specific_heat_load_wm2": load, "reference_room_temp": 20, "reference_outdoor_temp": -13}


def calculate(circuits, settings, spread=5):
    steps = individual_curve_steps(circuits, settings, [-13, 15, 7], spread, 15)
    while True:
        try:
            next(steps)
        except StopIteration as finished:
            return finished.value


@pytest.mark.parametrize("room,spread,load", [(20, 5, 50), (24, 7, 80), (18, 3, 15)])
def test_inverse_covers_load_to_tenth_degree(room, spread, load):
    supply = required_supply_temperature(circuit(room=room), load, spread)
    assert supply is not None
    def output(value):
        return calc_specific_heat_output(value, value - spread, room, 15, FLOOR_COVERINGS["Fliesen / Keramik"])
    assert output(supply) >= load
    assert output(supply - 0.1) < load


def test_independent_curves_and_reference_scaling():
    result = calculate([circuit(), circuit("HK-2", 24), circuit("HK-3")],
                       {"HK-1": reference(), "HK-2": reference(80)})
    assert result["HK-1"][2]["q_needed"] == pytest.approx(50 * 19 / 33)
    assert result["HK-2"][2]["q_needed"] == pytest.approx(80 * 23 / 33)
    assert result["HK-2"][2]["supply"] > result["HK-1"][2]["supply"]
    assert result["HK-3"][0]["status"] == "Referenzwerte fehlen"
    assert result["HK-1"][-1]["status"] == "Heizung aus"
    assert result["HK-1"][-1]["supply"] is None


def test_zero_high_invalid_and_spread():
    assert calculate([circuit()], {"HK-1": reference(0)})["HK-1"][0]["status"] == "Kein Heizbedarf"
    assert calculate([circuit()], {"HK-1": reference(10000)})["HK-1"][0]["supply"] is None
    assert calculate([circuit()], {"HK-1": reference(-1)})["HK-1"][0]["status"].endswith("ungueltig")
    for spread in (0, -1, float("nan")):
        with pytest.raises(ValueError):
            calculate([circuit()], {"HK-1": reference()}, spread)