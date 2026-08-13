"""Hilfsfunktionen für eingebettete Asset-Data-URIs."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

_DATA_URI_PREFIX = "data:"


def is_data_uri(value: object) -> bool:
    return str(value or "").strip().lower().startswith(_DATA_URI_PREFIX)


def parse_data_uri(uri: str) -> tuple[str, bytes] | None:
    text = str(uri or "").strip()
    if not text.lower().startswith(_DATA_URI_PREFIX):
        return None
    header, sep, payload = text.partition(",")
    if not sep:
        return None
    meta = header[len(_DATA_URI_PREFIX) :]
    mime = "application/octet-stream"
    is_base64 = False
    if meta:
        parts = meta.split(";")
        if parts and "/" in parts[0]:
            mime = parts[0].strip() or mime
            parts = parts[1:]
        is_base64 = any(part.strip().lower() == "base64" for part in parts)
    if not is_base64:
        return None
    try:
        data = base64.b64decode(payload, validate=True)
    except Exception:
        return None
    return mime, data


def data_uri_mime(uri: str) -> str:
    parsed = parse_data_uri(uri)
    if parsed is None:
        return ""
    return parsed[0]


def is_svg_asset_ref(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if is_data_uri(text):
        return data_uri_mime(text).lower() == "image/svg+xml"
    return text.lower().endswith(".svg")


def _guess_mime(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def encode_file_to_data_uri(path: Path) -> str:
    data = path.read_bytes()
    mime = _guess_mime(path)
    payload = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{payload}"
