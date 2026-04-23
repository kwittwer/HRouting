# HRouting – Fußbodenheizung und Kabel Planer
# Copyright (C) 2026 Konrad-Fabian Wittwer
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import re

def parse_svg_dimensions(filepath: str) -> dict:
    """
    Liest width, height und viewBox aus einer SVG-Datei.
    """
    result = {"width": None, "height": None, "viewBox": None}

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    m = re.search(r'<svg[^>]*\swidth=["\']([^"\']+)["\']', content)
    if m:
        result["width"] = _parse_unit(m.group(1))

    m = re.search(r'<svg[^>]*\sheight=["\']([^"\']+)["\']', content)
    if m:
        result["height"] = _parse_unit(m.group(1))

    m = re.search(r'<svg[^>]*\sviewBox=["\']([^"\']+)["\']', content)
    if m:
        parts = m.group(1).split()
        if len(parts) == 4:
            result["viewBox"] = {
                "min_x":  float(parts[0]),
                "min_y":  float(parts[1]),
                "width":  float(parts[2]),
                "height": float(parts[3]),
            }

    return result

def _parse_unit(value: str) -> float:
    """Konvertiert SVG-Einheiten nach Pixel (96 dpi Basis)."""
    conversions = {
        "mm": 3.7795275591,
        "cm": 37.795275591,
        "in": 96.0,
        "pt": 1.3333333333,
        "pc": 16.0,
        "px": 1.0,
    }
    value = value.strip()
    for unit, factor in conversions.items():
        if value.endswith(unit):
            return float(value[: -len(unit)]) * factor
    return float(value)