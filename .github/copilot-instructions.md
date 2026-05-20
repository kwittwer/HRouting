# HRouting – Anweisungen für KI-Agenten

Dieses Dokument beschreibt, wie ein KI-Agent **HRP-Projektdateien** für HRouting lesen, erstellen und bearbeiten kann.

> **Schema-Datei:** [`hrp_schema.json`](../../hrp_schema.json) im Projektroot enthält das formale JSON-Schema.
> **Validierung:** `python validate_hrp.py <datei.hrp>` prüft eine Datei gegen das Schema + semantische Regeln.

---

## 1. Dateiformat

| Eigenschaft | Wert |
|---|---|
| **Dateiendung** | `.hrp` (intern JSON) |
| **Encoding** | UTF-8 |
| **Formatierung** | `json.dump(data, indent=2, ensure_ascii=False)` |
| **Schema** | `hrp_schema.json` (JSON Schema Draft 2020-12) |

Eine HRP-Datei ist ein JSON-Objekt mit vier Top-Level-Schlüsseln:

```json
{
  "svg_path": "",
  "canvas": { ... },
  "params": { ... },
  "pdf_export_pages": [ ... ]
}
```

- **`svg_path`** – Legacy-Feld, normalerweise leer (`""`).
- **`canvas`** – Alle visuellen/geometrischen Daten (Positionen, Polygone, Routen).
- **`params`** – Alle Konfigurationsdaten (Temperaturen, Kreisdefinitionen, Elektropunkte).
- **`pdf_export_pages`** – PDF-Export-Seitendefinitionen (optional, `[]` wenn nicht benötigt).

---

## 2. Koordinatensystem

- **Einheit:** Canvas-Pixel (float).
- **Ursprung:** oben links.
- **Y-Achse:** wächst nach unten (Standard-Bildschirmkoordinaten).
- **Punkte:** `[x, y]` Arrays.
- **Maßstab:** `mm_per_px` konvertiert Pixel → Millimeter der realen Welt.
  - Jeder Grundriss-Layer hat sein eigenes `mm_per_px`.
  - Die globale Skalierung steht in `canvas.mm_per_px`.
- **Kalibrierung:** Eine Referenzlinie (`ref_line`) auf dem Grundriss wird auf eine bekannte Reallänge (`ref_length_mm`) gesetzt → daraus wird `mm_per_px` berechnet.
- **Zoom:** `view_scale` = 0.1 bis 50.0.
- **Pan:** `view_offset` = `[x, y]` Verschiebung in Bildschirmpixeln.

### Pixel ↔ Millimeter

```
reale_mm = pixel_abstand * mm_per_px
pixel_abstand = reale_mm / mm_per_px
```

### Praxisbeispiel

Ein Grundriss-PNG hat 1000 px Breite. Die Referenzlinie misst 200 px auf dem Bild und entspricht 5000 mm (5 m) real.
→ `mm_per_px = 5000 / 200 = 25.0`
→ Das Bild stellt 1000 × 25 = 25.000 mm = 25 m dar.

---

## 3. ID-Konventionen

Alle Elemente haben eindeutige IDs mit festen Mustern. **IDs müssen eindeutig innerhalb ihrer Kategorie sein** und fortlaufend nummeriert werden.

| Element | ID-Muster | Beispiel |
|---|---|---|
| Grundriss | `grundriss-N` | `grundriss-1` |
| Einrichtung (Möbel) | `einrichtung-N` | `einrichtung-1` |
| Heizkreis | `HK-N` | `HK-1`, `HK-2` |
| Elektro-Anschlusspunkt | `AP-N` | `AP-1`, `AP-2` |
| Elektro-Raum | `ER-N` | `ER-1` |
| Elektro-Kabel | `EK-N` | `EK-1` |
| Heizkreisverteiler (HKV) | `HKV-N` | `HKV-1` |
| HKV-Verbindungsleitung | `HKVL-N` | `HKVL-1` |
| Text-Annotation | `TEXT-N` | `TEXT-1` |

> **Wichtig:** Die N-Werte müssen nicht bei 1 beginnen oder lückenlos sein, aber sie müssen positive Ganzzahlen sein.

---

## 4. Datenstruktur-Überblick

### 4.1 `canvas` – Geometrische Daten

Der `canvas`-Abschnitt speichert alle **Positionen und Formen** auf der Zeichenfläche.

#### Heizkreis-Geometrie (Schlüssel = Heizkreis-ID, z.B. `"HK-1"`)

| Feld | Typ | Beschreibung |
|---|---|---|
| `polygons.{HK-ID}` | `[[x,y], ...]` | Raumpolygon (≥3 Punkte) |
| `start_points.{HK-ID}` | `[x,y]` | Rohrstartpunkt |
| `manual_routes.{HK-ID}` | `[[x,y], ...]` | Manueller Rohrverlauf |
| `route_wall_dist_px.{HK-ID}` | `float` | Wandabstand in px |
| `route_line_dist_px.{HK-ID}` | `float` | Leitungsabstand in px |
| `supply_lines.{HK-ID}` | `[[x,y], ...]` | Versorgungsleitung |

#### Elektro-Geometrie (Schlüssel = Element-ID)

| Feld | Typ | Beschreibung |
|---|---|---|
| `elec_points.{AP-ID}` | `[x,y]` | Punkt-Position |
| `elec_point_size_px.{AP-ID}` | `[w,h]` | Symbolgröße in px |
| `elec_rooms.{ER-ID}` | `[[x,y], ...]` | Raum-Polygon |
| `elec_cables.{EK-ID}` | `[[x,y], ...]` | Kabel-Polylinie |
| `cable_start_ap.{EK-ID}` | `string` | Start-AP-ID |
| `cable_end_ap.{EK-ID}` | `string` | End-AP-ID |

#### HKV-Geometrie (Schlüssel = HKV-ID)

| Feld | Typ | Beschreibung |
|---|---|---|
| `hkv_points.{HKV-ID}` | `[x,y]` | HKV-Position |
| `hkv_size_px.{HKV-ID}` | `[w,h]` | HKV-Symbolgröße in px |
| `supply_hkv.{HK-ID}` | `string` | Zuordnung Heizkreis → HKV |
| `hkv_lines.{HKVL-ID}` | `[[x,y], ...]` | Verbindungsleitung |

#### Grundriss-Layer

`floor_plans` ist ein **Array** (geordnet von hinten nach vorne):

```json
{
  "fp_id": "grundriss-1",
  "offset_x": 0.0, "offset_y": 0.0,
  "rotation": 0.0,
  "opacity": 1.0,
  "visible": true,
  "mm_per_px": 25.0,
  "ref_length_mm": 5000.0,
  "fixed_width_mm": 0.0, "fixed_height_mm": 0.0,
  "polygon_color": "#8d99ae",
  "ref_line": [[100, 200], [300, 200]],
  "polygon": []
}
```

### 4.2 `params` – Konfigurationsdaten

Der `params`-Abschnitt speichert alle **Einstellungen und Eigenschaften** der Elemente.

#### Globale Heizungsparameter

| Feld | Typ | Default | Beschreibung |
|---|---|---|---|
| `t_supply` | `float` | 35.0 | Vorlauftemperatur °C |
| `t_return` | `float` | 30.0 | Rücklauftemperatur °C |
| `t_norm_outdoor` | `float` | -12.0 | Normaußentemperatur °C |

#### Heizkreis-Definition (`params.circuits.{HK-ID}`)

```json
{
  "circuit_id": "HK-1",
  "floor_plan_id": "grundriss-1",
  "name": "Wohnzimmer",
  "color": "#2a9d8f",
  "diameter": 16.0,
  "spacing": 150.0,
  "wall_dist": 200.0,
  "visible": true,
  "label_visible": true,
  "label_size": 12.0,
  "room_temp": 20.0,
  "floor_covering": "Fliesen / Keramik",
  "distributor": ""
}
```

**Einheiten in params:** `diameter`, `spacing`, `wall_dist` sind in **mm**. `width`, `height` von Elektropunkten/HKVs ebenfalls in **mm**.

#### Elektro-Anschlusspunkt (`params.elec_points.{AP-ID}`)

```json
{
  "point_id": "AP-1",
  "floor_plan_id": "grundriss-1",
  "name": "Steckdose Küche",
  "color": "#4fc3f7",
  "width": 30.0,
  "height": 30.0,
  "icon_path": "",
  "builtin_symbol": "Steckdose",
  "visible": true,
  "label_visible": true,
  "label_size": 12.0,
  "position": "Wand",
  "height_from_floor": 30.0,
  "smarthome_device": "Shelly",
  "smarthome_device_color": "weiß",
  "note": "Hinter dem Kühlschrank"
}
```

#### UV-Phasenschienen (Busbars)

Innerhalb einer `UvConfig` können Phasenschienen definiert werden, die anzeigen welche Phase auf welchen TE-Positionen anliegt. **Mehrere Einträge mit derselben Phase sind erlaubt** (für nicht-zusammenhängende Bereiche).

**Format 1 – Einzelner Bereich (klassisch):**
```json
{"phase": "L1", "color": "#e53935", "te_start": 15, "te_end": 16}
```

**Format 2 – Nicht-zusammenhängende Bereiche (te_ranges):**
```json
{"phase": "L1", "te_ranges": [[15, 16], [28, 28], [39, 39]]}
```

`te_ranges` wird bei der Normalisierung in separate Busbar-Einträge aufgelöst. Im gespeicherten Format gibt es immer nur `te_start`/`te_end`-Paare – `te_ranges` ist eine Eingabe-Konvenienz für die MCP-Tools.

**Dreiphasige Sammelschiene:** `"phase": "L1/L2/L3"` rotiert automatisch die Farben L1→L2→L3 pro TE (z.B. für Kochfeld-Anschluss oder Hauptschalter).

---

## 5. Beziehungen zwischen Elementen

Viele Elemente haben Querverweise:

```
Heizkreis (HK-1) ──── floor_plan_id ──→ Grundriss (grundriss-1)
         │
         └─── distributor ──────────→ HKV-Name (nicht ID!)
         └─── supply_hkv (canvas) ──→ HKV-ID (z.B. "HKV-1")

Kabel (EK-1) ─── start_ap ──→ AP-ID (z.B. "AP-1")
             └── end_ap ────→ AP-ID (z.B. "AP-2")

HKV-Leitung (HKVL-1) ─── start_hkv ──→ HKV-ID
                      └── end_hkv ────→ HKV-ID
```

**Konsistenzregeln:**
- `floor_plan_id` muss auf eine existierende ID in `params.floorplans` verweisen (oder leer sein).
- `cable_start_ap` / `cable_end_ap` müssen auf existierende AP-IDs verweisen (oder leer sein).
- `supply_hkv` (in `canvas`) mappt eine Heizkreis-ID auf eine HKV-ID.
- `start_hkv` / `end_hkv` müssen auf existierende HKV-IDs verweisen (oder leer sein).
- Jede Element-ID, die in `canvas` vorkommt (z.B. Polygon für `HK-1`), muss auch eine Definition in `params` haben und umgekehrt.

---

## 6. Bildpfade und Assets

### Regeln für Dateipfade

- Alle Pfade in der HRP-Datei sind **relativ** zum Projektverzeichnis (dem Ordner der .hrp-Datei).
- Pfade verwenden **POSIX-Format** (Schrägstriche `/`, nicht `\`).
- Grundrissbilder liegen normalerweise im `images/`-Unterordner.
- Beispiel: `"file_path": "images/erdgeschoss.png"`

### Builtin-Symbole

Builtin-Symbole werden über `builtin_symbol` in `params.elec_points` referenziert (nicht über `icon_path`). Verfügbar sind:

**PNG-Symbole (icons/):**
Audio, Audiobuchse, Bewegungsmelder, E-Rollladen, E-Rollo, Fensterkontakt, Garagentor, Gong, HDMI, Heizkreisverteiler, Hitzemelder, LAN, LAN 2fach, Licht, Lichtquelle, Lichtquelle dimmbar, Lichtquelle Wand, Markise, Praesenzmelder, Rauchmelder, Steckdose, Steckdose 2fach, Steckdose 5fach, Steckdose schaltbar, Steckdose Starkstrom, Taster, Taster 4fach, Temperaturfuehler, Temperaturmessung, Thermostat, TV, Türkontakt, Wetterstation, WLanHotspot, Zutritt

**SVG-Symbole (assets/symbols/):**
Ausschalter, Doppelsteckdose, Kreuzschalter, Leuchte, Serienschalter, Steckdose, Taster, Wechselschalter

> **Hinweis:** Die Labels werden aus dem Dateinamen abgeleitet (Unterstriche → Leerzeichen, erster Buchstabe groß).

---

## 7. Bodenbeläge

Gültige Werte für `floor_covering` mit ihrem Wärmeleitwiderstand $R_{\lambda,B}$ (m²·K/W):

| Bodenbelag | $R_{\lambda,B}$ |
|---|---|
| `Estrich (kein Belag)` | 0.00 |
| `Fliesen / Keramik` | 0.01 |
| `Naturstein` | 0.02 |
| `PVC / Vinyl` | 0.02 |
| `Laminat` | 0.05 |
| `Parkett dünn (≤ 10 mm)` | 0.05 |
| `Parkett dick (> 10 mm)` | 0.10 |
| `Teppich dünn` | 0.10 |
| `Teppich dick` | 0.15 |

---

## 8. Heizkreisberechnung (DIN EN 1264)

HRouting berechnet für jeden Heizkreis:

1. **Log. mittlere Übertemperatur:**
   $$\Delta T_H = \frac{T_V - T_R}{\ln\frac{T_V - T_{Raum}}{T_R - T_{Raum}}}$$

2. **Spezifische Heizleistung:**
   $$q = K_H \cdot \Delta T_H^{1.1}$$
   $K_H$ wird aus einer Tabelle nach Verlegeabstand interpoliert und für den Bodenbelag korrigiert.

3. **Volumenstrom:**
   $$\dot{V} = \frac{q \cdot A}{\rho \cdot c_w \cdot (T_V - T_R)}$$

4. **Druckverlust:** Darcy-Weisbach mit Blasius-Reibungsbeiwert für PE-X-Rohre (2 mm Wandstärke).

**Relevant für den Agenten:** Die Berechnung wird automatisch durch die App ausgeführt. Der Agent muss nur die **Parameter** korrekt setzen (`t_supply`, `t_return`, `room_temp`, `spacing`, `diameter`, `floor_covering`) und das **Polygon** zeichnen.

---

## 9. Typische Agent-Operationen

### 9.1 Neues leeres Projekt erstellen

```json
{
  "svg_path": "",
  "canvas": {
    "view_scale": 1.0,
    "view_offset": [0, 0],
    "bg_color": "#2b2b2b",
    "grid_visible": true,
    "grid_spacing_mm": 100,
    "grid_color": [255, 255, 255, 30],
    "snap_angle": 0,
    "export_frame": null,
    "measure_color": "#ffdd00",
    "mm_per_px": 1.0,
    "ref_line": null,
    "ref_line_colors": {},
    "ref_line_visible": {},
    "polygons": {},
    "start_points": {},
    "manual_routes": {},
    "route_wall_dist_px": {},
    "route_line_dist_px": {},
    "supply_lines": {},
    "elec_points": {},
    "elec_point_size_px": {},
    "elec_point_position": {},
    "elec_point_height": {},
    "elec_point_notes": {},
    "elec_point_smarthome_device": {},
    "elec_point_smarthome_device_color": {},
    "elec_rooms": {},
    "elec_room_visible": {},
    "elec_cables": {},
    "elec_cable_notes": {},
    "cable_start_ap": {},
    "cable_end_ap": {},
    "elec_visible": {},
    "hkv_points": {},
    "hkv_size_px": {},
    "hkv_visible": {},
    "supply_hkv": {},
    "hkv_lines": {},
    "hkv_line_start": {},
    "hkv_line_end": {},
    "hkv_line_visible": {},
    "label_positions": {},
    "label_font_sizes": {},
    "label_visible": {},
    "text_annotations": {},
    "floor_plans": []
  },
  "params": {
    "t_supply": 35.0,
    "t_return": 30.0,
    "t_norm_outdoor": -12.0,
    "elec_cable_defaults": {},
    "floorplans_order": [],
    "floorplans": {},
    "furniture": {},
    "circuits": {},
    "elec_points": {},
    "elec_rooms": {},
    "elec_cables": {},
    "hkv_points": {},
    "hkv_lines": {},
    "text_annotations": {}
  },
  "pdf_export_pages": []
}
```

### 9.2 Heizkreis zu bestehendem Projekt hinzufügen

1. **Neue ID finden:** Höchste bestehende `HK-N` Nummer + 1 → z.B. `HK-3`.
2. **In `params.circuits` eintragen:**
   ```json
   "HK-3": {
     "circuit_id": "HK-3",
     "floor_plan_id": "grundriss-1",
     "name": "Badezimmer",
     "color": "#e76f51",
     "diameter": 16.0,
     "spacing": 100.0,
     "wall_dist": 150.0,
     "visible": true,
     "label_visible": true,
     "label_size": 12.0,
     "room_temp": 24.0,
     "floor_covering": "Fliesen / Keramik",
     "distributor": ""
   }
   ```
3. **In `canvas.polygons` das Raumpolygon setzen:**
   ```json
   "HK-3": [[100, 200], [400, 200], [400, 500], [100, 500]]
   ```

### 9.3 Elektropunkt hinzufügen

1. **Neue ID:** `AP-N` (nächste freie Nummer).
2. **In `params.elec_points`:** Definition mit `point_id`, `name`, `builtin_symbol` etc.
3. **In `canvas.elec_points`:** Position `[x, y]`.
4. **Optional in `canvas.elec_point_size_px`:** Symbolgröße `[w, h]`.

### 9.4 Bestehende HRP-Datei modifizieren

1. Datei lesen und als JSON parsen.
2. Gewünschte Änderungen vornehmen (neues Element hinzufügen, Parameter ändern, etc.).
3. Mit `python validate_hrp.py <datei.hrp>` validieren.
4. Datei speichern (UTF-8, indent=2, ensure_ascii=False).

---

## 10. Validierung

```bash
# Schema-Validierung + semantische Prüfungen
python validate_hrp.py projekt.hrp

# Nur Schema-Validierung
python validate_hrp.py --schema-only projekt.hrp

# Maschinenlesbares JSON-Format
python validate_hrp.py --json projekt.hrp
```

Der Validator prüft:
- **JSON-Schema-Konformität** gegen `hrp_schema.json`
- **Referentielle Integrität:** `floor_plan_id`, `cable_start_ap`, `cable_end_ap`, `start_hkv`, `end_hkv`, `supply_hkv` verweisen auf existierende IDs
- **Polygon-Konsistenz:** Mindestens 3 Punkte pro Polygon
- **Canvas-Params-Konsistenz:** Elemente in `canvas` haben Entsprechungen in `params`

---

## 11. Hinweise für den Agenten

- **Einheiten nicht verwechseln:** In `params` sind Maße in **mm** (diameter, spacing, wall_dist, width, height). In `canvas` sind alle Positionen in **Pixeln**.
- **Pixelwerte für canvas-Geometrie** hängen vom Maßstab `mm_per_px` des Grundrisses ab. Um einen Punkt 5 m vom Ursprung zu platzieren: `x = 5000 / mm_per_px`.
- **`floorplans_order`** muss alle Grundriss-IDs aus `params.floorplans` enthalten (bestimmt Reihenfolge im UI-Baum).
- **Bilder/Icons nicht vergessen:** Wenn das Projekt Grundrissbilder referenziert, müssen die Dateien im `images/`-Ordner relativ zur HRP-Datei existieren. Ohne Bilder kann das Projekt trotzdem geladen werden (leerer Hintergrund).
- **Farben** sind immer im Format `#rrggbb` (6 Hex-Zeichen), niemals `#rgb` oder `rgba`.
- **Keine doppelten IDs** – jede ID darf nur einmal in ihrer jeweiligen Kategorie vorkommen.
