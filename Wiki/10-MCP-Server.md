# MCP-Server (KI-Integration)

## Übersicht

HRouting kann per MCP von KI-Agenten gesteuert werden. Dafür stehen zwei Betriebsarten zur Verfügung:

1. `--mcp` startet den lokalen HTTP-Endpunkt `http://127.0.0.1:3274/mcp`
2. `--mcpstdio` startet MCP über Standard-Ein/-Ausgabe (ohne HTTP-Endpunkt)

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

Der MCP-Server stellt derzeit **96 Tools** bereit (Stand Codebasis v0.2.1 in `mcp_server.py`).

Abgedeckte Bereiche:

- Projekt lesen/speichern/validieren
- Heizkreise (inkl. Polygon, Route, Zuleitung und Berechnungen)
- Elektroplanung (AP, Räume, Kabel, UV-/UP-Verteilung)
- Heizkreisverteiler und HKV-Leitungen
- Grundrisse, Texte und weitere Projektmetadaten
- Referenz-/Transaktionshilfen für agentische Bearbeitung

Zusätzlich stellt der Server Ressourcen wie Schema und Agent-Hinweise bereit.

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
