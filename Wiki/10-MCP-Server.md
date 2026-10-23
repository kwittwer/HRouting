# MCP-Server (KI-Integration)

## Übersicht

HRouting kann per MCP von KI-Agenten gesteuert werden. Dafür stehen zwei Betriebsarten zur Verfügung:

1. `--mcp` startet den lokalen HTTP-Endpunkt `http://127.0.0.1:3274/mcp`
2. `--mcpstdio` startet den MCP-Server direkt über Standard-Ein/-Ausgabe (ohne GUI und HTTP-Endpunkt)

Die App läuft auch ohne MCP normal weiter.

## Start

```bash
# HTTP-MCP (lokal)
python main.py --mcp

# Stdio-MCP
python main.py --mcpstdio
```

Für HTTP-MCP öffnet HRouting ein eigenes Log-Fenster mit Tool-Aufrufen und Fehlern.

## Abhängigkeiten

```bash
pip install "mcp[cli]>=1.0" "uvicorn>=0.30"
```

## Umfang

Die Tool-Sätze unterscheiden sich nach Transport: HTTP registriert derzeit 98 Werkzeuge, Stdio 45. Die Stdio-Implementierung ist ein kleinerer, separat gepflegter Umfang; 53 HTTP-Werkzeuge fehlen dort, darunter Kabel-/Raum-/HKV-Verwaltung, UV-/UP-Verteilung, räumliche Planung, Batch-Operationen und die PDF-Werkzeuge. Die jeweilige MCP-Werkzeugliste ist maßgeblich.

Abgedeckte Bereiche:

- Projekt lesen/speichern/validieren
- Heizkreise (inkl. Polygon, Route, Zuleitung und Berechnungen)
- Elektroplanung (AP, Räume, Kabel, UV-/UP-Verteilung)
- Heizkreisverteiler und HKV-Leitungen
- Grundrisse, Texte und weitere Projektmetadaten
- Referenz-/Transaktionshilfen für agentische Bearbeitung

Zusätzlich stellt der Server Ressourcen wie Schema und Agent-Hinweise bereit.

Der HTTP-MCP bietet `get_pdf_export_config`, `set_pdf_export_config` und `export_pdf`. Damit lassen sich Exportseiten und Metadaten lesen/ändern sowie ein PDF ohne Dialoge erstellen. Relative Ausgabepfade beziehen sich auf den Projektordner; vorhandene Dateien werden nur mit `overwrite=true` ersetzt. Im Stdio-MCP sind diese Werkzeuge nicht enthalten.

## Verbindungsbeispiel (HTTP)

```json
{
  "mcpServers": {
    "hrouting": {
      "url": "http://127.0.0.1:3274/mcp"
    }
  }
}
```
