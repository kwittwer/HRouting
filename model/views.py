"""Dict-kompatible Proxy-Views auf das :class:`~model.document.Document`.

Der Canvas hält historisch rund fünfzig eigene Dicts mit Projektdaten. Damit
existieren zwei Wahrheiten: das ``Document`` (wird gespeichert) und der Canvas
(wird bearbeitet). Diese Views lösen das auf, ohne die rund 140 Zugriffsstellen
im Canvas umschreiben zu müssen.

Eine :class:`DocumentMapView` verhält sich wie ein ``dict``::

    canvas._polygons["HK-1"] = [QPointF(0, 0), QPointF(1, 1)]
    del canvas._polygons["HK-2"]
    for cid, pts in canvas._polygons.items():
        ...

Die Daten landen aber im ``Document`` und damit direkt in der gespeicherten
Datei. An der Grenze wird zwischen Qt-Typen (``QPointF``) und den serialisierbaren
Listenformen des .hrp-Formats konvertiert.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator, MutableMapping

from PySide6.QtCore import QPointF

from .document import Document
from .elements import Element

# ---------------------------------------------------------------------------
# Konvertierung zwischen .hrp-Rohwerten und Qt-Typen
# ---------------------------------------------------------------------------


def to_point(value: Any) -> QPointF | None:
    """``[x, y]`` -> ``QPointF``."""
    if value is None:
        return None
    if isinstance(value, QPointF):
        return QPointF(value)
    try:
        return QPointF(float(value[0]), float(value[1]))
    except (TypeError, ValueError, IndexError):
        return None


def from_point(value: Any) -> list[float] | None:
    """``QPointF`` -> ``[x, y]``."""
    if value is None:
        return None
    if isinstance(value, QPointF):
        return [float(value.x()), float(value.y())]
    try:
        return [float(value[0]), float(value[1])]
    except (TypeError, ValueError, IndexError):
        return None


def to_point_list(value: Any) -> list[QPointF]:
    """``[[x, y], ...]`` -> ``[QPointF, ...]``."""
    if not value:
        return []
    result: list[QPointF] = []
    for entry in value:
        point = to_point(entry)
        if point is not None:
            result.append(point)
    return result


def from_point_list(value: Any) -> list[list[float]]:
    """``[QPointF, ...]`` -> ``[[x, y], ...]``."""
    if not value:
        return []
    result: list[list[float]] = []
    for entry in value:
        point = from_point(entry)
        if point is not None:
            result.append(point)
    return result


def to_size(value: Any) -> tuple[float, float] | None:
    """``[w, h]`` -> ``(w, h)``."""
    if value is None:
        return None
    try:
        return (float(value[0]), float(value[1]))
    except (TypeError, ValueError, IndexError):
        return None


def from_size(value: Any) -> list[float] | None:
    """``(w, h)`` -> ``[w, h]``."""
    if value is None:
        return None
    try:
        return [float(value[0]), float(value[1])]
    except (TypeError, ValueError, IndexError):
        return None


def identity(value: Any) -> Any:
    return value


#: Konverterpaare: ``(aus dem Document lesen, ins Document schreiben)``
POINT = (to_point, from_point)
POINT_LIST = (to_point_list, from_point_list)
SIZE = (to_size, from_size)
RAW = (identity, identity)


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class DocumentMapView(MutableMapping):
    """Bildet eine id-basierte ``canvas``-Map auf das ``Document`` ab.

    :param document: Zieldokument.
    :param geom_key: Schlüssel der canvas-Map, z. B. ``"polygons"``.
    :param element_cls: Elementtyp, dessen Container bedient wird.
    :param converters: ``(read, write)``-Paar für die Wertkonvertierung.
    :param default: Wert, der für existierende Elemente ohne Eintrag gilt.
    :param has_default: Ob ``default`` verwendet werden soll. Ist er aktiv,
        erscheinen alle Elemente des Typs in der View, auch ohne
        gespeicherten Eintrag (nötig z. B. für Sichtbarkeits-Maps).
    """

    __slots__ = (
        "_document",
        "_geom_key",
        "_element_cls",
        "_read",
        "_write",
        "_default",
        "_has_default",
        "_on_change",
    )

    def __init__(
        self,
        document: Document,
        geom_key: str,
        element_cls: type[Element],
        converters: tuple[Callable[[Any], Any], Callable[[Any], Any]] = RAW,
        default: Any = None,
        has_default: bool = False,
        on_change: Callable[[str], None] | None = None,
    ) -> None:
        self._document = document
        self._geom_key = geom_key
        self._element_cls = element_cls
        self._read, self._write = converters
        self._default = default
        self._has_default = has_default
        self._on_change = on_change

    # -- Hilfsfunktionen -------------------------------------------------
    @property
    def _bucket(self) -> dict[str, Element]:
        return self._document.container(self._element_cls)

    def _element(self, key: str) -> Element | None:
        return self._bucket.get(key)

    def _notify(self, key: str) -> None:
        if self._on_change is not None:
            self._on_change(key)

    # -- MutableMapping --------------------------------------------------
    def __getitem__(self, key: str) -> Any:
        element = self._element(key)
        if element is None:
            raise KeyError(key)
        if self._geom_key in element.geom:
            return self._read(element.geom[self._geom_key])
        if self._has_default:
            return self._default
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        element = self._element(key)
        if element is None:
            # Geometrie ohne Element ist im .hrp-Format nicht darstellbar.
            # Solche Einträge werden als verwaiste canvas-Daten bewahrt.
            self._document.canvas_orphans.setdefault(self._geom_key, {})[key] = (
                self._write(value)
            )
            return
        element.geom[self._geom_key] = self._write(value)
        self._notify(key)

    def __delitem__(self, key: str) -> None:
        element = self._element(key)
        if element is None:
            orphans = self._document.canvas_orphans.get(self._geom_key, {})
            if key in orphans:
                del orphans[key]
                return
            raise KeyError(key)
        if self._geom_key in element.geom:
            del element.geom[self._geom_key]
            self._notify(key)
        elif not self._has_default:
            raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        if self._has_default:
            return iter(list(self._bucket.keys()))
        return iter(
            [
                key
                for key, element in self._bucket.items()
                if self._geom_key in element.geom
            ]
        )

    def __len__(self) -> int:
        return sum(1 for _ in iter(self))

    def __contains__(self, key: object) -> bool:
        element = self._bucket.get(str(key))
        if element is None:
            return False
        return self._has_default or self._geom_key in element.geom

    # -- dict-Komfort, den der Canvas nutzt ------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def setdefault(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            self[key] = default
            return default

    def pop(self, key: str, *args: Any) -> Any:
        try:
            value = self[key]
        except KeyError:
            if args:
                return args[0]
            raise
        try:
            del self[key]
        except KeyError:
            pass
        return value

    def clear(self) -> None:
        for element in list(self._bucket.values()):
            element.geom.pop(self._geom_key, None)
        self._document.canvas_orphans.pop(self._geom_key, None)

    def update(self, other=(), /, **kwargs: Any) -> None:  # type: ignore[override]
        if hasattr(other, "items"):
            other = other.items()
        for key, value in other:
            self[key] = value
        for key, value in kwargs.items():
            self[key] = value

    def copy(self) -> dict[str, Any]:
        return dict(self.items())

    def __repr__(self) -> str:  # pragma: no cover - Debughilfe
        return f"<DocumentMapView {self._geom_key} n={len(self)}>"


class NestedEntryView(MutableMapping):
    """View auf ein Feld innerhalb eines verschachtelten canvas-Eintrags.

    Text-Annotationen speichern mehrere Werte in einem Dict
    (``{"pos": ..., "content": ..., "font_size": ...}``). Diese View bildet
    ein einzelnes Feld daraus als flache Map ab.
    """

    __slots__ = (
        "_document",
        "_geom_key",
        "_field",
        "_element_cls",
        "_read",
        "_write",
        "_default",
        "_on_change",
    )

    def __init__(
        self,
        document: Document,
        geom_key: str,
        field: str,
        element_cls: type[Element],
        converters: tuple[Callable[[Any], Any], Callable[[Any], Any]] = RAW,
        default: Any = None,
        on_change: Callable[[str], None] | None = None,
    ) -> None:
        self._document = document
        self._geom_key = geom_key
        self._field = field
        self._element_cls = element_cls
        self._read, self._write = converters
        self._default = default
        self._on_change = on_change

    @property
    def _bucket(self) -> dict[str, Element]:
        return self._document.container(self._element_cls)

    def _entry(self, key: str, create: bool = False) -> dict | None:
        element = self._bucket.get(key)
        if element is None:
            return None
        entry = element.geom.get(self._geom_key)
        if not isinstance(entry, dict):
            if not create:
                return None
            entry = {}
            element.geom[self._geom_key] = entry
        return entry

    def __getitem__(self, key: str) -> Any:
        entry = self._entry(key)
        if entry is None:
            raise KeyError(key)
        return self._read(entry.get(self._field, self._default))

    def __setitem__(self, key: str, value: Any) -> None:
        entry = self._entry(key, create=True)
        if entry is None:
            return
        entry[self._field] = self._write(value)
        if self._on_change is not None:
            self._on_change(key)

    def __delitem__(self, key: str) -> None:
        entry = self._entry(key)
        if entry is None or self._field not in entry:
            raise KeyError(key)
        del entry[self._field]

    def __iter__(self) -> Iterator[str]:
        return iter(
            [
                key
                for key, element in self._bucket.items()
                if isinstance(element.geom.get(self._geom_key), dict)
            ]
        )

    def __len__(self) -> int:
        return sum(1 for _ in iter(self))

    def __contains__(self, key: object) -> bool:
        return self._entry(str(key)) is not None

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def setdefault(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            self[key] = default
            return default

    def pop(self, key: str, *args: Any) -> Any:
        try:
            value = self[key]
        except KeyError:
            if args:
                return args[0]
            raise
        try:
            del self[key]
        except KeyError:
            pass
        return value

    def clear(self) -> None:
        for element in list(self._bucket.values()):
            entry = element.geom.get(self._geom_key)
            if isinstance(entry, dict):
                entry.pop(self._field, None)

    def update(self, other=(), /, **kwargs: Any) -> None:  # type: ignore[override]
        if hasattr(other, "items"):
            other = other.items()
        for key, value in other:
            self[key] = value
        for key, value in kwargs.items():
            self[key] = value

    def copy(self) -> dict[str, Any]:
        return dict(self.items())

    def __repr__(self) -> str:  # pragma: no cover - Debughilfe
        return f"<NestedEntryView {self._geom_key}.{self._field} n={len(self)}>"


class ParamsMapView(MutableMapping):
    """View auf ein Feld im ``params``-Eintrag der Elemente.

    Für Werte, die im .hrp-Format nicht in ``canvas``, sondern in ``params``
    liegen – etwa Sichtbarkeit oder Beschriftungsgröße.
    """

    __slots__ = (
        "_document",
        "_field",
        "_element_classes",
        "_read",
        "_write",
        "_default",
        "_on_change",
    )

    def __init__(
        self,
        document: Document,
        field: str,
        element_classes: tuple[type[Element], ...],
        converters: tuple[Callable[[Any], Any], Callable[[Any], Any]] = RAW,
        default: Any = None,
        on_change: Callable[[str], None] | None = None,
    ) -> None:
        self._document = document
        self._field = field
        self._element_classes = element_classes
        self._read, self._write = converters
        self._default = default
        self._on_change = on_change

    def _element(self, key: str) -> Element | None:
        for element_cls in self._element_classes:
            element = self._document.container(element_cls).get(key)
            if element is not None:
                return element
        return None

    def _keys(self) -> list[str]:
        keys: list[str] = []
        for element_cls in self._element_classes:
            keys.extend(self._document.container(element_cls).keys())
        return keys

    def __getitem__(self, key: str) -> Any:
        element = self._element(key)
        if element is None:
            raise KeyError(key)
        return self._read(element.data.get(self._field, self._default))

    def __setitem__(self, key: str, value: Any) -> None:
        element = self._element(key)
        if element is None:
            return
        element.data[self._field] = self._write(value)
        if self._on_change is not None:
            self._on_change(key)

    def __delitem__(self, key: str) -> None:
        element = self._element(key)
        if element is None or self._field not in element.data:
            raise KeyError(key)
        del element.data[self._field]

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys())

    def __len__(self) -> int:
        return len(self._keys())

    def __contains__(self, key: object) -> bool:
        return self._element(str(key)) is not None

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def setdefault(self, key: str, default: Any = None) -> Any:
        element = self._element(key)
        if element is None:
            return default
        if self._field not in element.data:
            element.data[self._field] = self._write(default)
            return default
        return self._read(element.data[self._field])

    def pop(self, key: str, *args: Any) -> Any:
        try:
            value = self[key]
        except KeyError:
            if args:
                return args[0]
            raise
        try:
            del self[key]
        except KeyError:
            pass
        return value

    def clear(self) -> None:
        for element_cls in self._element_classes:
            for element in self._document.container(element_cls).values():
                element.data.pop(self._field, None)

    def update(self, other=(), /, **kwargs: Any) -> None:  # type: ignore[override]
        if hasattr(other, "items"):
            other = other.items()
        for key, value in other:
            self[key] = value
        for key, value in kwargs.items():
            self[key] = value

    def copy(self) -> dict[str, Any]:
        return dict(self.items())

    def __repr__(self) -> str:  # pragma: no cover - Debughilfe
        return f"<ParamsMapView {self._field} n={len(self)}>"
