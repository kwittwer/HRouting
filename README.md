# HRouting – Fußbodenheizung und Kabel Planer

Desktop-Anwendung zur Planung von Fußbodenheizungskreisen und elektrischen Kabelverlegungen auf importierten Grundrissen (SVG, PNG, JPG, BMP).

Entwickelt mit **Python 3** und **PySide6** (Qt for Python).

---

## Release Notes

### V 0.1.1 (27.04.2026)

#### Features ✨
- **Möbel-Polygone für Grundrisse**: Möbel können jetzt direkt als Polygone auf dem Grundriss gezeichnet werden (Alternative zu Bildern)
- **Möbel-Polygon-Editor**: Doppelklick auf Möbel-Polygon startet Edit-Modus mit Punkt-Verschiebung, Löschen und Einfügen
- **Polygon-Farbwahl**: Farbe der Möbel-Polygone individuell konfigurierbar über Farbwähler
- **Grid-Snapping für Polygon-Punkte**: Polygon-Punkte fangen am Raster beim Ziehen (wenn Raster sichtbar)
- **Linienlängen-Anzeige**: Im Polygon-Edit-Modus werden Längen der benachbarten Segmente angezeigt

#### Verbesserungen 🔧
- **Zoom-Limits**: Maximales Zoom von 100x auf 50x reduziert (verhindert Rendering-Fehler)
- **Pixmap-Fallback**: Bei extremem Zoom wird Platzhalter statt fehlerhaftem Bild angezeigt
- **Mausrad-Schutz**: Alle Spinboxes ignorieren Mausrad komplett - verhindert versehentliche Wertänderungen
- **Heizkreisgrößen flexibel**: Rohrdurchmesser, Verlegeabstand und Randabstand haben keine starren Obergrenzen mehr (nur > 0)

#### Bugfixes 🐛
- Pixmap verschwindet bei zu starkem Zoom (jetzt mit Fallback)
- Grundrissbild-Rendering bei extremem Zoom

### V 0.1.0 (27.04.2026)
- Initiale Version mit Heizkreis- und Elektro-Planung

---

## Projektstruktur

```
HRouting/
├── main.py                  # Einstiegspunkt, Versionsverwaltung, Splash Screen
├── build.py                 # Build-Script (Version-Bump + PyInstaller)
├── generate_splash.py       # Splash Screen Generator
├── gui/
│   ├── main_window.py       # Hauptfenster, Toolbar, Menüs, Export, Projektlogik
│   ├── canvas_widget.py     # Zeichenfläche (Polygone, Routen, Kabel, Raster, …)
│   └── parameter_panel.py   # Rechte Seitenleiste (Einstellungen, Baumansicht)
├── logic/
│   ├── heating_calc.py      # Heizlastberechnung nach DIN EN 1264
│   └── svg_parser.py        # SVG-Parser (Dimensionen, ViewBox, Einheiten)
├── assets/
│   ├── icon.ico / icon.svg  # App-Icon
│   ├── splash.png           # Splash Screen
│   └── symbols/             # DIN EN 60617 Schaltsymbole
└── Wiki/                    # Benutzerdokumentation
```

---

## Voraussetzungen

- **Python** ≥ 3.10
- **pip** (wird mit Python mitgeliefert)

### Abhängigkeiten installieren

```bash
# Virtuelle Umgebung erstellen und aktivieren
python -m venv .venv

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# Abhängigkeiten
pip install -r requirements.txt
```

---

## Anwendung starten (Entwicklung)

```bash
python main.py
```

---

## Build (EXE erstellen)

Das Build-Script `build.py` erledigt alles automatisch:

1. **Patch-Version** in `main.py` wird automatisch inkrementiert (`0.0.2` → `0.0.3`)
2. **Splash Screen** wird neu generiert (mit aktueller Version + Datum)
3. **PyInstaller** baut eine einzelne `.exe`-Datei

### Build starten

```bash
python build.py
```

Oder über den VS Code Task **🔨 Build EXE** (`Ctrl+Shift+B`).

### Build-Ergebnis

Die fertige EXE liegt unter:

```
dist/HRouting_<VERSION>.exe
```

> Die EXE ist eine Einzeldatei mit allen Ressourcen (Assets, Daten) eingebettet.

### Dateiassoziation (.hrp)

HRouting-Projekte verwenden die Dateiendung `.hrp`. Nach dem Build wird die Dateiassoziation automatisch registriert. Zum manuellen Registrieren/Entfernen:

```bash
# Registrieren
python register_filetype.py install dist\HRouting_0.0.7.exe

# Entfernen
python register_filetype.py uninstall
```

Nach der Registrierung können `.hrp`-Dateien per Doppelklick mit HRouting geöffnet werden.

### Build-Konfiguration anpassen

In `build.py` können folgende Punkte angepasst werden:

| Abschnitt | Beschreibung |
|-----------|-------------|
| `bump_version()` | Versionierungslogik (Major/Minor manuell in `main.py` ändern) |
| `--hidden-import` | Zusätzliche Python-Module einbinden |
| `--exclude-module` | Unbenötigte PySide6-Module ausschließen (spart ca. 200 MB) |
| `--add-data` | Ressourcen-Ordner zur EXE hinzufügen |

---

## Hinweise für die Programmierung

### Architektur

- **`main.py`** — Minimaler Einstieg: erstellt `QApplication`, zeigt Splash, lädt dann `MainWindow`.
- **`gui/main_window.py`** — Orchestriert die gesamte UI-Logik: Toolbar, Signale, Projekt-Speicherung/Laden, PDF/SVG-Export, Berechnungsaufrufe.
- **`gui/canvas_widget.py`** — Zentrale Zeichenfläche (`QWidget`). Verwaltet alle Zeichenmodi (Polygon, Route, Kabel, …), Zoom/Pan, Hit-Testing und das `paintEvent`.
- **`gui/parameter_panel.py`** — Rechte Seitenleiste mit Baumansicht und objektspezifischen Eigenschafts-Panels.
- **`logic/heating_calc.py`** — Reine Berechnungslogik (keine UI-Abhängigkeiten). Heizleistung, Volumenstrom, Druckverlust, hydraulischer Abgleich nach DIN EN 1264.
- **`logic/svg_parser.py`** — Liest SVG-Dimensionen (`width`, `height`, `viewBox`) mit Einheiten-Konvertierung.

### Zeichenmodi (Canvas)

Neue Zeichenmodi werden über ein `_mode`-Attribut in `canvas_widget.py` gesteuert:

| Modus | Beschreibung |
|-------|-------------|
| `NONE` | Standard – Pan, Drag, Objekt-Auswahl |
| `DRAW_POLY` | Raumpolygon zeichnen |
| `DRAW_ROUTE` | Rohrverlauf zeichnen |
| `DRAW_REF` | Referenzlinie setzen |
| `DRAW_SUPPLY_LINE` | Zuleitung zeichnen |
| `DRAW_ELEC_CABLE` | Kabelverbindung zeichnen |
| `DRAW_HKV_LINE` | HKV-Leitung zeichnen |
| `EDIT_*` | Bearbeitungsmodi (Knoten verschieben/löschen/einfügen) |
| `PLACE_*` | Platzierungsmodi (AP, HKV) |
| `MOVE_*` | Verschiebungsmodi (AP, HKV) |

### Signale & Slots

Die Kommunikation zwischen Canvas, ParameterPanel und MainWindow erfolgt über Qt-Signale:

```
canvas.polygon_finished  →  main_window._on_polygon_finished
canvas.route_changed     →  main_window._on_route_changed
param_panel.add_circuit  →  main_window._add_circuit
...
```

### Projekt-Format (JSON)

Projekte werden als JSON gespeichert mit folgender Grundstruktur:

```json
{
  "svg_path": "relative/path/to/floorplan.svg",
  "canvas": {
    "polygons": {},
    "manual_routes": {},
    "elec_points": {},
    "elec_cables": {},
    "hkv_points": {},
    "hkv_lines": {},
    "mm_per_px": 26.48
  },
  "params": {
    "t_supply": 35,
    "t_return": 30,
    "circuits": {},
    "elec_points": {},
    "elec_cables": {}
  }
}
```

### Tipps

- **Versionierung**: Major/Minor-Version in `main.py` manuell setzen, Patch wird beim Build automatisch inkrementiert.
- **Neue Objekt-Typen hinzufügen**: Pattern aus bestehenden Typen (z.B. Elektro-AP) folgen: Signal in Canvas → Handler in MainWindow → Panel in ParameterPanel → Speicher-/Ladelogik.
- **Berechnungen**: Heizlastberechnung in `logic/heating_calc.py` ist UI-unabhängig und kann isoliert getestet werden.
- **Assets**: Alle Bilder/Icons unter `assets/` ablegen. Werden beim Build in die EXE eingebettet. Zugriff über `BASE_DIR / "assets" / ...`.

---

## Lizenz

Copyright (C) 2026 Konrad-Fabian Wittwer

Dieses Programm ist freie Software: Sie können es unter den Bedingungen der
**GNU General Public License** (GPL v3), wie von der Free Software Foundation
veröffentlicht, weitergeben und/oder modifizieren – entweder gemäß Version 3
der Lizenz oder (nach Ihrer Wahl) jeder späteren Version.

Siehe [LICENSE](LICENSE) für den vollständigen Lizenztext.
