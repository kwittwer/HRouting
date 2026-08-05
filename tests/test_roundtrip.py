"""Golden-Master-Tests: .hrp laden -> Document -> speichern muss verlustfrei sein."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.document import Document  # noqa: E402
from storage.hrp_io import load_raw, save_raw  # noqa: E402
from storage.migration import migrate_raw  # noqa: E402

EXAMPLES = sorted((ROOT / "examples").glob("*.hrp"))


def _normalize(value):
    """Tupel/Listen angleichen, damit JSON-Roundtrips vergleichbar sind."""
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_document_roundtrip_is_lossless(example: Path):
    raw = load_raw(example)
    doc = Document.from_dict(raw)
    result = doc.to_dict()
    assert _normalize(result) == _normalize(raw)


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_document_roundtrip_is_stable(example: Path):
    """Zweiter Durchlauf darf nichts mehr verändern."""
    doc = Document.from_dict(load_raw(example))
    first = doc.to_dict()
    second = Document.from_dict(first).to_dict()
    assert _normalize(first) == _normalize(second)


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_saved_file_is_valid(example: Path, tmp_path: Path):
    from validate_hrp import (  # noqa: PLC0415
        _load_hrp,
        _load_schema,
        validate_schema,
        validate_semantic,
    )

    doc = Document.from_dict(load_raw(example))
    target = tmp_path / example.name
    save_raw(doc.to_dict(), target)

    data = _load_hrp(target)
    schema = _load_schema(ROOT / "hrp_schema.json")
    errors = validate_schema(data, schema)
    semantic_errors, _warnings = validate_semantic(data)
    assert not errors, errors
    assert not semantic_errors, semantic_errors


def test_migration_drops_legacy_ui_state():
    raw = migrate_raw({"params": {"_ui_state": {"main_window": {}}, "circuits": {}}})
    assert "_ui_state" not in raw["params"]


def test_migration_moves_global_helper_lines():
    raw = migrate_raw(
        {
            "canvas": {
                "global_helper_lines": {"H-1": [[0, 0], [1, 1]]},
                "floor_plans": [{"fp_id": "grundriss-1"}],
            }
        }
    )
    assert "global_helper_lines" not in raw["canvas"]
    assert raw["canvas"]["floor_helper_lines"]["grundriss-1"]["H-1"] == [[0, 0], [1, 1]]


def test_id_allocator_continues_after_existing_ids():
    from model.ids import IdAllocator  # noqa: PLC0415

    alloc = IdAllocator()
    alloc.observe_all(["HK-1", "HK-7", "AP-3"])
    assert alloc.next_id("HK") == "HK-8"
    assert alloc.next_id("AP") == "AP-4"
    assert alloc.next_id("EK") == "EK-1"


def test_layers_in_use_only_reports_existing_elements():
    from model.layers import LayerId  # noqa: PLC0415

    doc = Document.from_dict(
        {
            "canvas": {"floor_plans": [{"fp_id": "grundriss-1"}]},
            "params": {
                "floorplans": {"grundriss-1": {"name": "EG"}},
                "circuits": {
                    "HK-1": {"circuit_id": "HK-1", "floor_plan_id": "grundriss-1"}
                },
            },
        }
    )
    used = doc.layers_in_use("grundriss-1")
    assert LayerId.HEATING in used
    assert LayerId.ELECTRICAL not in used


def test_json_serializable():
    doc = Document.from_dict(load_raw(EXAMPLES[0])) if EXAMPLES else Document()
    json.dumps(doc.to_dict(), ensure_ascii=False)
