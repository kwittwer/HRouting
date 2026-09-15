from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from logic.kicad_export import KiCadExporter


def _sample_project() -> dict:
    return {
        "canvas": {
            "mm_per_px": 1.0,
            "elec_points": {
                "AP-1": [10.0, 20.0],
                "AP-2": [40.0, 60.0],
            },
            "elec_cables": {
                "EK-1": [[10.0, 20.0], [20.0, 25.0], [40.0, 60.0]],
            },
            "cable_start_ap": {"EK-1": "AP-1"},
            "cable_end_ap": {"EK-1": "AP-2"},
        },
        "params": {
            "project_name": "KiCad Export Test",
            "elec_points": {
                "AP-1": {
                    "name": "Steckdose Küche",
                    "builtin_symbol": "Steckdose",
                    "height_from_floor": 300.0,
                    "visible": True,
                },
                "AP-2": {
                    "name": "Licht Flur",
                    "builtin_symbol": "Leuchte",
                    "height_from_floor": 2100.0,
                    "visible": True,
                },
            },
            "elec_cables": {
                "EK-1": {
                    "name": "Zuleitung Küche",
                    "type": "5x1,5",
                    "start_ap": "AP-1",
                    "end_ap": "AP-2",
                }
            },
        },
    }


def _room_grid_project() -> dict:
    return {
        "canvas": {
            "mm_per_px": 1.0,
            "elec_points": {
                "AP-1": [10.0, 20.0],
                "AP-2": [40.0, 60.0],
                "AP-3": [70.0, 90.0],
            },
            "elec_cables": {
                "EK-1": [[10.0, 20.0], [20.0, 20.0]],
                "EK-2": [[10.0, 20.0], [20.0, 30.0]],
            },
            "cable_start_ap": {"EK-1": "AP-1", "EK-2": "AP-1"},
            "cable_end_ap": {"EK-1": "AP-2", "EK-2": "AP-3"},
        },
        "params": {
            "project_name": "KiCad Room Grid",
            "elec_rooms": {
                "ER-1": {"name": "Bad"},
                "ER-2": {"name": "Kueche"},
            },
            "elec_points": {
                "AP-1": {
                    "name": "Kueche Steckdose",
                    "builtin_symbol": "Steckdose",
                    "height_from_floor": 300.0,
                    "visible": True,
                    "room_id": "ER-2",
                },
                "AP-2": {
                    "name": "Bad Licht",
                    "builtin_symbol": "Leuchte",
                    "height_from_floor": 2100.0,
                    "visible": True,
                    "room_id": "ER-1",
                },
                "AP-3": {
                    "name": "Bad Steckdose",
                    "builtin_symbol": "Steckdose",
                    "height_from_floor": 300.0,
                    "visible": True,
                    "room_id": "ER-1",
                },
            },
            "elec_cables": {
                "EK-1": {"type": "5x1,5", "start_ap": "AP-1", "end_ap": "AP-2"},
                "EK-2": {"type": "3x1,5", "start_ap": "AP-1", "end_ap": "AP-3"},
            },
        },
    }


def test_kicad_export_writes_aps_as_text_boxes():
    content = KiCadExporter(_sample_project())._build_schematic()

    assert '(text_box "HRP:AP_ID: AP-1\\nAP_NAME: Steckdose Küche\\nAP_SYMBOL: Steckdose\\nHEIGHT_FROM_FLOOR_MM: 300.0"' in content
    assert '(text_box "HRP:AP_ID: AP-2\\nAP_NAME: Licht Flur\\nAP_SYMBOL: Leuchte\\nHEIGHT_FROM_FLOOR_MM: 2100.0"' in content
    assert '(justify left top)' in content
    assert '(size ' in content
    assert '(symbol' not in content


def test_kicad_export_writes_cables_as_net_labels_with_normalized_kbl_label():
    content = KiCadExporter(_sample_project())._build_schematic()

    assert '(label "KBL_Steckdose Küche:Licht Flur{5x1_5}"' in content
    assert 'KBL_Steckdose Küche:Licht Flur{5x1_5}' in content
    assert '(effects (font (size 1.0 1.0)) (justify left))' in content
    assert '(bus' not in content
    assert '(wire' not in content


def test_kicad_export_normalizes_dot_and_comma_in_cable_type():
    project = _sample_project()
    project["params"]["elec_cables"]["EK-1"]["type"] = "J-Y(ST)Y 2x2x0.8, geschirmt"

    content = KiCadExporter(project)._build_schematic()

    assert 'KBL_Steckdose Küche:Licht Flur{J-Y(ST)Y 2x2x0_8_ geschirmt}' in content


def test_kicad_export_sanitizes_endpoint_delimiters_in_kbl_label():
    project = _sample_project()
    project["params"]["elec_points"]["AP-1"]["name"] = "AP: Küche{Nord}"
    project["params"]["elec_points"]["AP-2"]["name"] = "Licht} Flur"

    content = KiCadExporter(project)._build_schematic()

    assert 'KBL_AP_ Küche_Nord_:Licht_ Flur{5x1_5}' in content


def test_kicad_export_arranges_aps_by_room_and_cables_near_start_ap_grid():
    content = KiCadExporter(_room_grid_project())._build_schematic()

    assert '(text "ROOM: Bad"' in content
    assert '(text "ROOM: Kueche"' in content
    assert '(text_box "HRP:AP_ID: AP-2\\nAP_NAME: Bad Licht' in content
    assert '(at 30 51 0)' in content
    assert '(text_box "HRP:AP_ID: AP-1\\nAP_NAME: Kueche Steckdose' in content
    assert '(at 180 51 0)' in content

    assert '(label "KBL_Kueche Steckdose:Bad Licht{5x1_5}"' in content
    assert '(at 182 67.2 0)' in content
    assert '(label "KBL_Kueche Steckdose:Bad Steckdose{3x1_5}"' in content
    assert '(at 232 67.2 0)' in content