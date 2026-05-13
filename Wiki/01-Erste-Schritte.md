# Erste Schritte

## Programmstart

Starten Sie HRouting per Doppelklick auf die EXE-Datei oder im Entwicklungsmodus:

```bash
python main.py
```

Nach dem Splash Screen öffnet sich das Hauptfenster mit:

- **Toolbar** (oben) – Alle Werkzeuge und Einstellungen
- **Zeichenfläche** (Mitte) – Hier wird der Grundriss angezeigt und bearbeitet
- **Seitenleiste** (rechts) – Parameter, Baumansicht, Objekteigenschaften

### Kommandozeilen-Argumente

| Argument | Beschreibung |
|----------|-------------|
| `--mcp` | MCP-Server starten (KI-Agenten-Anbindung, siehe [MCP-Server](10-MCP-Server.md)) |
| `<datei.hrp>` | Projektdatei direkt öffnen (z.B. per Doppelklick im Explorer) |

Beispiele:

```bash
# Normal starten
python main.py

# Mit MCP-Server für KI-Agenten
python main.py --mcp

# Projekt direkt öffnen
python main.py mein_projekt.hrp

# Beides kombinieren
python main.py --mcp mein_projekt.hrp
```

## Dateiassoziation (.hrp)

Unter Windows können `.hrp`-Projektdateien mit HRouting verknüpft werden. Nach der Registrierung öffnet ein Doppelklick auf eine `.hrp`-Datei automatisch HRouting mit diesem Projekt.

```bash
# Registrieren
python register_filetype.py install dist\HRouting_0.1.34.exe

# Entfernen
python register_filetype.py uninstall
```

Die Registrierung verwendet die ProgID `HRouting.Project` und aktualisiert sich automatisch, wenn eine neuere EXE-Version erkannt wird.

## Typischer Arbeitsablauf

1. **Grundriss laden** – Klicken Sie auf **📂 Grundriss laden** und wählen Sie eine SVG-, PNG- oder JPG-Datei.
2. **Maßstab setzen** – Zeichnen Sie eine Referenzlinie und geben Sie die reale Länge ein.
3. **Heizkreis anlegen** – Klicken Sie auf **➕ Heizkreis**, zeichnen Sie das Raumpolygon und den Rohrverlauf.
4. **Elektroplan** (optional) – Platzieren Sie Anschlusspunkte und zeichnen Sie Kabelverbindungen.
5. **Speichern & Exportieren** – Projekt speichern und als PDF oder SVG exportieren.

## Letztes Projekt automatisch laden

HRouting merkt sich das zuletzt geöffnete Projekt und lädt es beim nächsten Start automatisch.

## Über HRouting

Über das Menü **Hilfe → ℹ️ Über HRouting…** können Sie die aktuelle Versionsnummer und Lizenzinformationen einsehen.
