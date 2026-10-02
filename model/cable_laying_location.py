"""Physical cable laying locations, independent of Qt and electrical ratings."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

CABLE_LAYING_OPTIONS: tuple[tuple[str, str], ...] = (
    ("floor", "Auf dem Boden"),
    ("wall", "In der Wand"),
    ("ceiling", "In der Decke"),
)


def normalize_cable_laying_location(value: Any) -> dict:
    """Return a fresh full object; invalid/missing members default to empty.

    Preset codes are unique and ordered. Inactive custom text is retained
    verbatim for persistence, but never included in formatted output.
    """
    source = value if isinstance(value, Mapping) else {}
    locations = source.get("locations", [])
    if not isinstance(locations, (list, tuple)):
        locations = []
    custom_enabled = source.get("custom_enabled", False)
    custom_text = source.get("custom_text", "")
    return {
        "locations": [code for code, _label in CABLE_LAYING_OPTIONS if code in locations],
        "custom_enabled": custom_enabled if isinstance(custom_enabled, bool) else False,
        "custom_text": custom_text if isinstance(custom_text, str) else "",
    }


def cable_laying_location_labels(value: Any) -> list[str]:
    """Active labels in preset order followed by custom text/``Sonstiges``."""
    normalized = normalize_cable_laying_location(value)
    labels = [label for code, label in CABLE_LAYING_OPTIONS if code in normalized["locations"]]
    if normalized["custom_enabled"]:
        custom_label = normalized["custom_text"].strip() or "Sonstiges"
        if custom_label not in labels:
            labels.append(custom_label)
    return labels


def format_cable_laying_location(value: Any, *, empty: str = "–") -> str:
    """Format a single metadata object (not a cable element)."""
    return ", ".join(cable_laying_location_labels(value)) or empty


def aggregate_cable_laying_locations(values: Iterable[Any], *, empty: str = "–") -> str:
    """Union active labels from a one-pass iterator of metadata objects.

    Standard labels always precede custom labels, in stable preset order.
    Custom labels retain their first-seen order without duplicates.
    """
    seen: dict[str, None] = {}
    for value in values:
        for label in cable_laying_location_labels(value):
            seen.setdefault(label, None)
    preset_labels = [label for _code, label in CABLE_LAYING_OPTIONS]
    ordered = [label for label in preset_labels if label in seen]
    ordered.extend(label for label in seen if label not in preset_labels)
    return ", ".join(ordered) or empty