# HRouting

Desktop-Anwendung für die Planung von Fußbodenheizungen und Elektroinstallationen auf Grundrissen.

**Aktueller Stand:** Version `0.2.1` (siehe `main.py`).

## Kernfunktionen

- Heizkreise mit Polygon, Rohrverlauf, Zuleitung und automatischer Berechnung nach DIN EN 1264
- Elektroplanung mit Räumen, Anschlusspunkten, Kabeln, UV-/UP-Verteilung und Auswertung
- Grundrissverwaltung mit mehreren Layern, Maßstab pro Layer, Möbel/Einrichtung, Texten und Messwerkzeug
- Export als PDF, SVG und CSV sowie Projektübersicht mit Fach-Tabellen

## Voraussetzungen

- Python 3.10+
- `pip`

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Start

```bash
# GUI starten
python main.py

# GUI + MCP HTTP-Server (lokal: http://127.0.0.1:3274/mcp)
python main.py --mcp

# Stdio-MCP-Server (ohne HTTP-Endpunkt)
python main.py --mcpstdio

# Projekt direkt öffnen
python main.py mein_projekt.hrp
```

## Projektformat (.hrp)

HRouting speichert Projekte als JSON mit der Endung `.hrp`.

- Schema: [`hrp_schema.json`](hrp_schema.json)
- Validator: [`validate_hrp.py`](validate_hrp.py)
- Beispiel: [`examples/minimal.hrp`](examples/minimal.hrp)

```bash
python validate_hrp.py projekt.hrp
python validate_hrp.py --schema-only projekt.hrp
python validate_hrp.py --json projekt.hrp
```

## Build

```bash
python build.py
```

Die EXE wird unter `dist\HRouting_<VERSION>.exe` erzeugt.  
Dateizuordnung für `.hrp` kann bei Bedarf manuell gesetzt werden:

```bash
python register_filetype.py install dist\HRouting_<VERSION>.exe
python register_filetype.py uninstall
```

## Dokumentation

- Benutzerdoku: [`Wiki/README.md`](Wiki/README.md)
- MCP-Integration: [`Wiki/10-MCP-Server.md`](Wiki/10-MCP-Server.md)

## Lizenz

Copyright (C) 2026 Konrad-Fabian Wittwer

Dieses Programm ist freie Software: Sie können es unter den Bedingungen der
**GNU General Public License** (GPL v3), wie von der Free Software Foundation
veröffentlicht, weitergeben und/oder modifizieren – entweder gemäß Version 3
der Lizenz oder (nach Ihrer Wahl) jeder späteren Version.

Siehe [LICENSE](LICENSE) für den vollständigen Lizenztext.
