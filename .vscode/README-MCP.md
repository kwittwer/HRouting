# HRouting MCP-Server – VS Code Einrichtung

## Voraussetzung

HRouting muss **laufen** (als EXE oder via `python main.py`), damit der MCP-Server auf Port 3274 erreichbar ist.

## Einrichtung (3 Schritte)

### 1. VS Code Workspace öffnen

Öffne diesen Ordner (oder die `HRouting.code-workspace`) in VS Code.
Die MCP-Konfiguration unter `.vscode/mcp.json` wird automatisch erkannt.

### 2. HRouting starten

**Variante A – Installierte EXE (MSI):**
- HRouting über das Startmenü starten

**Variante B – Entwicklung:**
```bash
pip install -r requirements.txt
python main.py
```

### 3. Copilot Chat verwenden

In VS Code den Copilot Chat öffnen (Ctrl+Shift+I). Die HRouting-MCP-Tools erscheinen automatisch.

Verfügbare Tools:
- `get_project_summary` – Projektübersicht
- `list_circuits` / `add_circuit` / `modify_circuit` / `delete_circuit`
- `list_elec_points` / `add_elec_point` / `modify_elec_point` / `delete_elec_point`
- `configure_uv_distribution` / `clear_uv_distribution`
- `configure_up_distribution` / `clear_up_distribution`
- `list_hkvs` / `add_hkv` / `delete_hkv`
- `calculate_heating` / `calculate_all_circuits`
- `set_heating_params`
- `save_project` / `validate_project`
- `get_project_json`

---

## Globale Nutzung (ohne Workspace)

Falls du den MCP-Server in **jeder** VS Code Instanz nutzen willst (nicht nur in diesem Workspace), kopiere die Konfiguration in deine User-Settings:

**Datei:** `%APPDATA%\Code\User\settings.json`

```json
{
  "mcp": {
    "servers": {
      "hrouting": {
        "type": "http",
        "url": "http://127.0.0.1:3274/mcp"
      }
    }
  }
}
```

---

## Troubleshooting

| Problem | Lösung |
|---|---|
| MCP-Tools nicht sichtbar | HRouting muss laufen! Prüfe: `curl http://127.0.0.1:3274/mcp` |
| Port belegt | Anderer Prozess nutzt 3274. Nur eine HRouting-Instanz starten. |
| EXE startet MCP nicht | Sicherstellen, dass die EXE mit dem neuesten Build erstellt wurde (enthält MCP-Pakete) |
