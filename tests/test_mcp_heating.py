import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from model.elements import Circuit, FloorPlan, Hkv


@pytest.fixture
def heating_window(monkeypatch):
    pytest.importorskip("mcp")
    from gui.app_window import AppWindow
    from mcp_server import _create_mcp

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, **kw: default)
    monkeypatch.setattr(QSettings, "setValue", lambda self, key, value: None)
    window = AppWindow()
    floor = FloorPlan("grundriss-1", layer={"mm_per_px": 10.0})
    window._document.add(floor)
    window._document.floorplan_order.append(floor.id)
    for index in range(1, 16):
        circuit = Circuit.create(
            f"HK-{index}", name=f"Circuit {index}", floor_plan_id=floor.id,
            diameter=16.0, spacing=100.0, wall_dist=200.0, room_temp=20.0,
            floor_covering="Fliesen / Keramik",
        )
        circuit.polygon = [[0, 0], [400, 0], [400, 500], [0, 500]]
        circuit.route = [[0, 0], [1000, 0]]
        circuit.supply_line = [[0, 0], [0, 100]]
        window._document.add(circuit)
    window.canvas.set_document(window._document)
    server = _create_mcp(window, SimpleNamespace(invoke=lambda fn: fn()))
    yield window, server
    window.deleteLater()
    app.processEvents()


def tool(server, name):
    return server._tool_manager._tools[name].fn


def test_modify_all_circuit_diameters_in_mm(heating_window):
    window, server = heating_window
    before = window._document.to_dict()
    for circuit_id in window._document.elements["circuits"]:
        result = tool(server, "modify_circuit")(circuit_id, diameter=17.0)
        assert result["status"] == "modified"
        assert result["params"]["diameter"] == 17.0
    assert all(row["diameter"] == 17.0 for row in tool(server, "list_circuits")())
    assert window._dirty
    window._undo()
    assert window._document.elements["circuits"]["HK-15"].diameter == 16.0
    assert before["params"]["circuits"]["HK-1"]["diameter"] == 16.0


def test_calculate_heating_uses_circuit_floor_scale(heating_window):
    window, server = heating_window
    before = window._document.to_dict()
    result = tool(server, "calculate_heating")("HK-1")
    assert result["area_m2"] == 20.0
    assert result["pipe_length_m"] == 20.0
    assert result["supply_length_m"] == 2.0
    assert result["total_pipe_length_m"] == 22.0
    assert result["power_w"] > 0
    assert result["pressure_drop_mbar"] > 0
    results = tool(server, "calculate_all_circuits")()
    assert len(results) == 15
    assert results[0] == result
    summary = tool(server, "get_heating_load_summary")()
    assert summary["circuit_count"] == 15
    assert summary["total_power_w"] == pytest.approx(result["power_w"] * 15, abs=1.0)
    assert summary["total_pipe_length_m"] == 330.0
    assert all("error" not in row for row in summary["circuits"])
    assert window._document.to_dict() == before


def test_modify_invalid_covering_does_not_partially_update(heating_window):
    window, server = heating_window
    result = tool(server, "modify_circuit")("HK-1", diameter=17.0, floor_covering="invalid")
    assert "error" in result
    assert window._document.elements["circuits"]["HK-1"].diameter == 16.0


def test_missing_circuit_returns_error(heating_window):
    _, server = heating_window
    assert "error" in tool(server, "modify_circuit")("HK-999", diameter=17.0)
    assert "error" in tool(server, "calculate_heating")("HK-999")


@pytest.mark.parametrize("connection", ["id", "name"])
def test_balance_matches_heating_without_mutating_document(heating_window, connection):
    window, server = heating_window
    document = window._document
    distributor = Hkv.create("HKV-1", name="Test distributor")
    document.add(distributor)
    for circuit_id in ("HK-1", "HK-2"):
        circuit = document.elements["circuits"][circuit_id]
        circuit.diameter = 17.0
        if connection == "id":
            circuit.hkv_id = distributor.id
        else:
            circuit.distributor = distributor.name
    document.elements["circuits"]["HK-2"].route = [[0, 0], [2000, 0]]
    before = document.to_dict()
    result = tool(server, "balance_hkv_circuits")(distributor.id)
    assert result["connected_circuit_count"] == 2
    for row in result["circuits"]:
        expected = tool(server, "calculate_heating")(row["circuit_id"])
        for key in ("power_w", "volume_flow_lmin", "pressure_drop_mbar", "area_m2", "pipe_length_m"):
            assert row[key] == expected[key]
        assert row["throttle_mbar"] == pytest.approx(
            result["max_pressure_drop_mbar"] - row["pressure_drop_mbar"], abs=0.1
        )
    assert result["circuits"][0]["throttle_mbar"] > 0
    assert result["circuits"][1]["throttle_mbar"] == 0
    assert document.to_dict() == before