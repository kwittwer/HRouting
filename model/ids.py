"""Vergabe eindeutiger Element-IDs (ersetzt die Zähler im alten MainWindow)."""

from __future__ import annotations

import re
from typing import Iterable

_ID_RE = re.compile(r"^(?P<prefix>.+)-(?P<number>\d+)$")


class IdAllocator:
    """Vergibt fortlaufende IDs pro Präfix (``HK-1``, ``HK-2``, …).

    Der Allocator merkt sich die höchste je gesehene Nummer pro Präfix,
    damit gelöschte IDs nicht erneut vergeben werden.
    """

    def __init__(self) -> None:
        self._max: dict[str, int] = {}

    def observe(self, element_id: str) -> None:
        """Registriert eine bereits existierende ID."""
        match = _ID_RE.match(element_id or "")
        if not match:
            return
        prefix = match.group("prefix")
        number = int(match.group("number"))
        if number > self._max.get(prefix, 0):
            self._max[prefix] = number

    def observe_all(self, element_ids: Iterable[str]) -> None:
        for element_id in element_ids:
            self.observe(element_id)

    def next_id(self, prefix: str) -> str:
        number = self._max.get(prefix, 0) + 1
        self._max[prefix] = number
        return f"{prefix}-{number}"

    def peek(self, prefix: str) -> int:
        return self._max.get(prefix, 0)

    def reset(self) -> None:
        self._max.clear()
