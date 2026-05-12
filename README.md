# HRouting – Fußbodenheizung und Kabel Planer

Desktop-Anwendung zur Planung von Fußbodenheizungskreisen und elektrischen Kabelverlegungen auf importierten Grundrissen (SVG, PNG, JPG, BMP).

Entwickelt mit **Python 3** und **PySide6** (Qt for Python).

---

## Release Notes

### V 0.1.16 (08.05.2026)

#### Features ✨
- **Elektro-Räume als Polygone**: Räume für die Elektroplanung können jetzt je Grundriss als Polygon gezeichnet und bearbeitet werden
- **Automatische AP-Raumzuordnung**: Liegt ein Anschlusspunkt innerhalb eines Raum-Polygons, wird er automatisch diesem Raum zugeordnet
- **Projektübersicht mit Raum-Reitern**: Für jeden Elektro-Raum wird ein eigener Reiter in der Projektübersicht erzeugt
- **Eigener Reiter für AP-Verkabelung**: Die AP-Kabelzuordnung wurde aus dem Elektro-Tab ausgelagert und in einen separaten Reiter verschoben

#### Verbesserungen 🔧
- **Elektro-Tab entschlackt**: Der Elektro-Reiter zeigt jetzt fokussiert nur Kabellängen-Summen pro Typ und eine vollständige Kabelliste
- **Erweiterte Kabelliste**: Zusätzlich zu Name, Typ und Länge werden jetzt Start-/End-AP sowie Start-/End-Raum ausgewiesen
- **Erweiterter Projektbericht**: Raumbezogene AP-Listen mit Kabelziel-Referenzen in Projektübersicht, CSV und PDF

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
├── hrp_schema.json          # JSON-Schema für .hrp-Dateien (Agent-/Validierung)
├── validate_hrp.py          # HRP-Datei-Validator (Schema + semantische Prüfung)
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
├── examples/
│   └── minimal.hrp          # Beispiel-HRP-Projekt
├── .github/
│   └── copilot-instructions.md  # Anleitung für KI-Agenten
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

### Projekt-Format (.hrp)

Projekte werden als **JSON** (UTF-8, `indent=2`) mit der Dateiendung `.hrp` gespeichert.

```json
{
  "svg_path": "",
  "canvas": { "polygons": {}, "elec_points": {}, "hkv_points": {}, ... },
  "params": { "t_supply": 35, "circuits": {}, "elec_points": {}, ... },
  "pdf_export_pages": []
}
```

Die vollständige Dokumentation des Dateiformats:

| Datei | Beschreibung |
|-------|-------------|
| [`hrp_schema.json`](hrp_schema.json) | Formales JSON-Schema (Draft 2020-12) mit allen Feldern, Typen, Enums und Wertebereichen |
| [`.github/copilot-instructions.md`](.github/copilot-instructions.md) | Agenten-Anleitung: Koordinatensystem, ID-Konventionen, Beziehungen, Beispiele |
| [`examples/minimal.hrp`](examples/minimal.hrp) | Vollständiges Beispielprojekt (2 Heizkreise, 3 Elektropunkte, 1 HKV) |

#### HRP-Datei validieren

```bash
# Schema + semantische Prüfung
python validate_hrp.py projekt.hrp

# Nur Schema-Validierung
python validate_hrp.py --schema-only projekt.hrp

# Maschinenlesbares JSON-Format (für KI-Agenten)
python validate_hrp.py --json projekt.hrp
```

Der Validator prüft: JSON-Schema-Konformität, referentielle Integrität (IDs/Verweise), Polygon-Konsistenz (≥3 Punkte), Canvas↔Params-Synchronisation, gültige Bodenbeläge und Farbformate.

#### KI-Agent-Integration

KI-Agenten (GitHub Copilot, Claude, etc.) können HRP-Dateien programmatisch erstellen und bearbeiten:

1. **Schema lesen:** `hrp_schema.json` als Kontext laden
2. **Anleitung lesen:** `.github/copilot-instructions.md` (wird von Copilot automatisch geladen)
3. **Datei erzeugen/bearbeiten:** JSON nach Schema erstellen
4. **Validieren:** `python validate_hrp.py --json datei.hrp` → maschinenlesbares Ergebnis

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
