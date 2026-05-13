# MCP-Server (KI-Integration)

## Übersicht

HRouting enthält einen integrierten **MCP-Server** (Model Context Protocol), über den KI-Agenten wie GitHub Copilot, Claude oder andere MCP-fähige Assistenten das Programm fernsteuern können.

Der MCP-Server bietet **42 Tools** zum Lesen, Erstellen, Bearbeiten und Löschen aller Projektelemente sowie zur Durchführung von Heizlastberechnungen.

## MCP-Server starten

Der MCP-Server wird über das Kommandozeilen-Argument `--mcp` aktiviert:

```bash
python main.py --mcp
```

Beim Start öffnet sich automatisch ein **MCP-Log-Fenster**, das alle Aktivitäten der KI-Agenten protokolliert.

### Verbindungsdaten

| Eigenschaft | Wert |
|-------------|------|
| **Protokoll** | HTTP (Streamable-HTTP) |
| **Endpunkt** | `http://127.0.0.1:3274/mcp` |
| **Host** | `127.0.0.1` (nur lokal) |
| **Port** | `3274` |

### Konfiguration für Claude Desktop

In der Datei `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "hrouting": {
      "url": "http://127.0.0.1:3274/mcp"
    }
  }
}
```

### Abhängigkeiten

Der MCP-Server benötigt optionale Python-Pakete:

```bash
pip install "mcp[cli]>=1.0" "uvicorn>=0.30"
```

Ohne diese Pakete startet HRouting normal, aber ohne MCP-Funktionalität.

## MCP-Log-Fenster

Das Log-Fenster zeigt alle Aktivitäten der KI-Agenten in Echtzeit an:

- **⚙ Tool-Aufrufe**: Jeder Tool-Aufruf wird mit Funktionsname und Parametern protokolliert
- **✓ Erfolge**: Status und relevante IDs/Namen
- **✗ Fehler**: Fehlermeldungen werden farblich hervorgehoben

Die Log-Einträge sind farblich kodiert:

| Farbe | Level |
|-------|-------|
| Hellblau | DEBUG |
| Grau | INFO |
| Gelb | WARNING |
| Rot | ERROR / CRITICAL |

Das Log-Fenster schließt sich automatisch, wenn das Hauptprogramm beendet wird.

## Verfügbare Tools

### Lese-Tools (10)

| Tool | Beschreibung |
|------|-------------|
| `get_project_summary` | Projektübersicht: Anzahl aller Elemente, globale Parameter, Maßstab |
| `get_project_json` | Vollständiges Projekt als JSON (canvas + params) |
| `list_circuits` | Liste aller Heizkreise mit Parametern und Geometrie-Info |
| `list_elec_points` | Liste aller Elektro-Anschlusspunkte mit Position |
| `list_elec_rooms` | Liste aller Elektro-Räume mit Polygon-Info |
| `list_elec_cables` | Liste aller Elektro-Kabel mit Länge und AP-Verbindungen |
| `list_hkvs` | Liste aller Heizkreisverteiler mit Position |
| `list_hkv_lines` | Liste aller HKV-Verbindungsleitungen mit Länge |
| `list_texts` | Liste aller Text-Annotationen mit Inhalt und Position |
| `list_floor_plans` | Liste aller Grundriss-Layer mit Eigenschaften |

### Heizkreis-Tools (6)

| Tool | Beschreibung |
|------|-------------|
| `add_circuit` | Neuen Heizkreis mit Raumpolygon hinzufügen |
| `modify_circuit` | Parameter eines Heizkreises ändern (Name, Spacing, Durchmesser, etc.) |
| `delete_circuit` | Heizkreis löschen |
| `set_circuit_polygon` | Raumpolygon eines Heizkreises setzen/aktualisieren |
| `set_circuit_route` | Manuellen Rohrverlauf setzen/aktualisieren |
| `set_supply_line` | Zuleitung setzen/aktualisieren (inkl. HKV-Zuordnung) |

### Elektro-Punkt-Tools (3)

| Tool | Beschreibung |
|------|-------------|
| `add_elec_point` | Elektro-Anschlusspunkt platzieren (inkl. Symbol, Smarthome-Gerät, Notiz) |
| `modify_elec_point` | Parameter eines AP ändern |
| `delete_elec_point` | Anschlusspunkt löschen |

### Elektro-Raum-Tools (3)

| Tool | Beschreibung |
|------|-------------|
| `add_elec_room` | Elektro-Raum mit Polygon hinzufügen |
| `modify_elec_room` | Raum-Parameter ändern |
| `delete_elec_room` | Elektro-Raum löschen |

### Elektro-Kabel-Tools (3)

| Tool | Beschreibung |
|------|-------------|
| `add_elec_cable` | Kabel als Polylinie hinzufügen (inkl. AP-Verbindungen) |
| `modify_elec_cable` | Kabel-Parameter ändern |
| `delete_elec_cable` | Kabel löschen |

### HKV-Tools (3)

| Tool | Beschreibung |
|------|-------------|
| `add_hkv` | Heizkreisverteiler platzieren |
| `modify_hkv` | HKV-Parameter ändern |
| `delete_hkv` | HKV löschen |

### HKV-Leitungs-Tools (3)

| Tool | Beschreibung |
|------|-------------|
| `add_hkv_line` | HKV-Verbindungsleitung als Polylinie hinzufügen |
| `modify_hkv_line` | Leitungs-Parameter ändern |
| `delete_hkv_line` | HKV-Leitung löschen |

### Text-Annotations-Tools (3)

| Tool | Beschreibung |
|------|-------------|
| `add_text` | Text-Annotation auf dem Canvas platzieren |
| `modify_text` | Text-Parameter ändern (Inhalt, Größe, Farbe, Position) |
| `delete_text` | Text-Annotation löschen |

### Grundriss-Tools (3)

| Tool | Beschreibung |
|------|-------------|
| `add_floor_plan` | Neuen Grundriss-Layer hinzufügen (mit optionalem Bild) |
| `modify_floor_plan` | Layer-Eigenschaften ändern (Position, Rotation, Maßstab, etc.) |
| `delete_floor_plan` | Grundriss-Layer löschen |

### Berechnungs-Tools (3)

| Tool | Beschreibung |
|------|-------------|
| `calculate_heating` | Heizlastberechnung nach DIN EN 1264 für einen Heizkreis |
| `calculate_all_circuits` | Heizlastberechnung für alle Heizkreise |
| `set_heating_params` | Globale Heizungsparameter ändern (Vorlauf, Rücklauf, Normaußentemperatur) |

### Projekt-Tools (2)

| Tool | Beschreibung |
|------|-------------|
| `save_project` | Projekt speichern (optional mit Pfadangabe) |
| `validate_project` | Projekt gegen HRP-Schema validieren (Schema + semantische Prüfung) |

## MCP-Resources

Zusätzlich zu den Tools stellt der MCP-Server zwei Ressourcen bereit:

| Resource | URI | Beschreibung |
|----------|-----|-------------|
| **Schema** | `hrp://schema` | JSON-Schema für HRP-Projektdateien |
| **Anleitung** | `hrp://instructions` | Agenten-Anleitung (copilot-instructions.md) |

## Thread-Sicherheit

Der MCP-Server läuft in einem eigenen Hintergrund-Thread. Alle Zugriffe auf die Qt-Oberfläche (Zeichenfläche, Parameter, etc.) werden über eine **Bridge** synchron auf dem Qt-Main-Thread ausgeführt. Dadurch sind alle Tool-Aufrufe thread-sicher und blockieren die Benutzeroberfläche nicht.

## Typische Agent-Workflows

### Heizkreis erstellen

1. Agent ruft `get_project_summary()` auf → Projektübersicht
2. Agent ruft `add_circuit(name="Wohnzimmer", polygon=[[...], ...])` auf → Neuer Heizkreis
3. Agent ruft `calculate_heating(circuit_id="HK-1")` auf → Berechnung

### Elektroplanung

1. Agent ruft `add_elec_room(name="Küche", polygon=[[...], ...])` auf → Raum definieren
2. Agent ruft `add_elec_point(name="Steckdose", x=100, y=200, builtin_symbol="Steckdose")` auf
3. Agent ruft `add_elec_cable(name="Zuleitung", polyline=[[...], ...], start_ap_id="AP-1")` auf

### Projekt validieren und speichern

1. Agent ruft `validate_project()` auf → Fehler/Warnungen prüfen
2. Agent ruft `save_project(path="mein_projekt.hrp")` auf
