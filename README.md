# HRouting – Fußbodenheizung und Kabel Planer

Desktop-Anwendung zur Planung von Fußbodenheizungskreisen und elektrischen Installationen mit Kabelverlegung und Unterverteilungen auf importierten Grundrissen (SVG, PNG, JPG, BMP).

Entwickelt mit **Python 3** und **PySide6** (Qt for Python).

---

## Funktionsübersicht

### Heizungsplanung
- **Heizkreise** – Raumpolygone zeichnen, Rohrverlauf planen, Zuleitungen zum HKV
- **Heizkreisverteiler (HKV)** – Verteiler platzieren, HKV-Leitungen zeichnen
- **Heizlastberechnung** – Automatische Berechnung nach DIN EN 1264 (Leistung, Volumenstrom, Druckverlust)
- **Hydraulischer Abgleich** – Kv-Wert und Ventil-Differenzdruck pro HKV

### Elektroplanung
- **Elektro-Räume** – Raum-Polygone für automatische AP-Zuordnung
- **Anschlusspunkte (AP)** – 35+ DIN-Symbole, Smarthome-Geräteverwaltung, Einbauhöhe, Notizen und UV-Untertyp
- **Kabelverbindungen** – Polylinie für Kabelwege/Verlegung mit Auto-Snap an APs und Kabeltyp-Verwaltung
- **Unterverteilungen (UV)** – Rasterbasierte Platzplanung für das Unterverteilen mit Belegung und optionaler Kabel-/Stromkreis-Zuordnung
- **Automatische Durchnummerierung** – Gleichnamige APs nummerieren

### Grundrisse & Ansicht
- **Multi-Layer-Grundrisse** – Mehrere Grundriss-Ebenen mit eigener Skalierung
- **Einrichtungen/Möbel** – Bild-Overlays mit eigener Referenzlinie und Maßen
- **Messwerkzeug** – Abstände direkt im Plan messen
- **Export-Rahmen** – Ausschnitt für SVG-/PDF-Export definieren
- **Text-Annotationen** – Frei positionierbare Beschriftungen
- **Raster** – Konfigurierbares Gitternetz mit Farbwahl

### Bearbeitung
- **Undo/Redo** – 80 Schritte Verlauf (Ctrl+Z / Ctrl+Y)
- **Kopieren/Einfügen** – Alle Objekttypen (Ctrl+C / Ctrl+V)
- **Kontextmenü** – Rechtsklick für objektspezifische Aktionen
- **Polygon-Editor** – Knoten verschieben, löschen, einfügen per Doppelklick
- **Fangwinkel** – 45°, 90°, 120° für saubere Linienführung

### Export & Berichte
- **PDF-Export** – Mehrseitige PDF mit konfigurierbaren Seiten (Plan, Tabellen, Hydraulik)
- **SVG-Export** – Vektorgrafik für Weiterbearbeitung
- **CSV-Export** – Tabellarische Daten aus Projektübersicht
- **Projektübersicht** – Tabs für Längen, Hydraulik, Abgleich, Elektro, Räume, HKV-Leitungen

### KI-Integration (MCP-Server)
- **44 MCP-Tools** – Vollständige Steuerung per KI-Agent (GitHub Copilot, Claude, etc.)
- **Log-Fenster** – Echtzeit-Protokollierung aller Agent-Aktivitäten
- **Start**: `python main.py --mcp`

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
├── mcp_server.py            # MCP-Server für KI-Agenten (44 Tools)
├── build.py                 # Build-Script (Version-Bump + PyInstaller)
├── generate_splash.py       # Splash Screen Generator
├── hrp_schema.json          # JSON-Schema für .hrp-Dateien (Agent-/Validierung)
├── validate_hrp.py          # HRP-Datei-Validator (Schema + semantische Prüfung)
├── register_filetype.py     # Windows .hrp-Dateiassoziation
├── gui/
│   ├── main_window.py       # Hauptfenster, Toolbar, Menüs, Export, Projektlogik
│   ├── canvas_widget.py     # Zeichenfläche (Polygone, Routen, Kabel, Raster, …)
│   ├── parameter_panel.py   # Rechte Seitenleiste (Einstellungen, Baumansicht)
│   └── pdf_export_dialog.py # PDF-Export-Seitenkonfiguration
├── logic/
│   ├── heating_calc.py      # Heizlastberechnung nach DIN EN 1264
│   └── svg_parser.py        # SVG-Parser (Dimensionen, ViewBox, Einheiten)
├── assets/
│   ├── icon.ico / icon.svg  # App-Icon
│   ├── splash.png           # Splash Screen
│   └── symbols/             # DIN EN 60617 Schaltsymbole (SVG)
├── icons/                   # Builtin-Symbole für Elektroplanung (PNG)
├── examples/
│   └── minimal.hrp          # Beispiel-HRP-Projekt
├── .github/
│   └── copilot-instructions.md  # Anleitung für KI-Agenten
└── Wiki/                    # Benutzerdokumentation (10 Seiten)
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

# Alle Abhängigkeiten (inkl. MCP-Server)
pip install -r requirements.txt

# Nur Kernabhängigkeiten (ohne MCP-Server)
pip install PySide6 PyInstaller markdown jsonschema
```

| Paket | Zweck | Optional? |
|-------|-------|-----------|
| `PySide6>=6.5` | Qt-GUI-Framework | Nein |
| `PyInstaller>=6.0` | EXE-Build | Nein |
| `markdown>=3.5` | Markdown-Rendering | Nein |
| `jsonschema>=4.20` | HRP-Validierung | Nein |
| `mcp[cli]>=1.0` | MCP-Server für KI-Agenten | Ja |
| `uvicorn>=0.30` | HTTP-Server für MCP | Ja |

---

## Anwendung starten (Entwicklung)

```bash
# Normal starten
python main.py

# Mit MCP-Server für KI-Agenten (GitHub Copilot, Claude, etc.)
python main.py --mcp

# Projekt direkt öffnen
python main.py mein_projekt.hrp
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

- **`main.py`** — Minimaler Einstieg: erstellt `QApplication`, zeigt Splash, lädt dann `MainWindow`. Startet optional MCP-Server und Log-Fenster (`--mcp`).
- **`mcp_server.py`** — MCP-Server mit 42 Tools für KI-Agenten. Läuft in eigenem Thread, kommuniziert thread-sicher über Qt-Events mit dem Main-Thread.
- **`gui/main_window.py`** — Orchestriert die gesamte UI-Logik: Toolbar, Menüs (Datei/Bearbeiten/Hilfe), Signale, Projekt-Speicherung/Laden, PDF/SVG-Export, Undo/Redo (80 Schritte), Kopieren/Einfügen, Kontextmenü.
- **`gui/canvas_widget.py`** — Zentrale Zeichenfläche (`QWidget`). Verwaltet alle Zeichenmodi (Polygon, Route, Kabel, Messen, Export-Rahmen, …), Zoom/Pan, Hit-Testing und das `paintEvent`.
- **`gui/parameter_panel.py`** — Rechte Seitenleiste mit Baumansicht und objektspezifischen Eigenschafts-Panels.
- **`gui/pdf_export_dialog.py`** — Dialog zur Konfiguration der PDF-Export-Seiten.
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
| `MEASURE` | Messwerkzeug (Abstände messen) |
| `DRAW_EXPORT_FRAME` | Export-Rahmen aufziehen |
| `EDIT_*` | Bearbeitungsmodi (Knoten verschieben/löschen/einfügen) |
| `PLACE_*` | Platzierungsmodi (AP, HKV, Text) |
| `MOVE_*` | Verschiebungsmodi (AP, HKV, Text, Grundriss, Einrichtung) |

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

KI-Agenten (GitHub Copilot, Claude, etc.) können HRouting-Projekte über zwei Wege bearbeiten:

**1. MCP-Server (empfohlen):** Starten Sie HRouting mit `--mcp` und verbinden Sie den Agenten per HTTP. 42 Tools für vollständige Projektsteuerung. Siehe [Wiki: MCP-Server](Wiki/10-MCP-Server.md).

**2. Datei-basiert:** HRP-Dateien programmatisch erstellen/bearbeiten:
1. Schema lesen: `hrp_schema.json`
2. Anleitung lesen: `.github/copilot-instructions.md`
3. JSON erzeugen/bearbeiten
4. Validieren: `python validate_hrp.py --json datei.hrp`

---

## Benutzerdokumentation

Die vollständige Benutzerdokumentation befindet sich im [Wiki/](Wiki/README.md)-Ordner:

1. [Erste Schritte](Wiki/01-Erste-Schritte.md) — Installation, Programmstart, Kommandozeile
2. [Grundriss & Maßstab](Wiki/02-Grundriss-und-Massstab.md) — Multi-Layer-Grundrisse, Einrichtungen, Referenzlinie
3. [Heizkreise](Wiki/03-Heizkreise.md) — Raumpolygon, Rohrverlauf, Parameter
4. [Elektroplanung](Wiki/04-Elektroplanung.md) — Räume, APs, Kabelverlegung, Unterverteilungen, 35+ Symbole, Smarthome
5. [Heizkreisverteiler](Wiki/05-Heizkreisverteiler.md) — HKV, Leitungen
6. [Ansicht & Raster](Wiki/06-Ansicht-und-Raster.md) — Messen, Export-Rahmen, Text-Annotationen
7. [Projekt & Export](Wiki/07-Projekt-und-Export.md) — Speichern, PDF/SVG/CSV-Export
8. [Tastatur & Maus](Wiki/08-Tastatur-und-Maus.md) — Shortcuts, Kontextmenü, Undo/Redo, Copy/Paste
9. [Berechnungen](Wiki/09-Berechnungen.md) — DIN EN 1264, hydraulischer Abgleich
10. [MCP-Server](Wiki/10-MCP-Server.md) — KI-Integration, 42 Tools, Log-Fenster

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
