# Tastatur & Maus – Übersicht

## Mausaktionen

| Aktion | Beschreibung |
|--------|-------------|
| **Mausrad** | Zoom (zentriert auf Cursor) |
| **Mittlere Maustaste + Ziehen** | Zeichenfläche verschieben (Pan) |
| **Linksklick** (leere Fläche) | Zeichenfläche verschieben |
| **Linksklick** (auf Objekt) | Objekt auswählen / Startpunkt ziehen |
| **Doppelklick** (auf Objekt) | Bearbeitungsmodus aktivieren |
| **Rechtsklick** (beim Zeichnen) | Linie/Polygon abschließen |
| **Rechtsklick** (auf Objekt) | Kontextmenü öffnen |
| **Mittlere Maustaste** (Bearbeitung) | Bearbeitungsmodus beenden |

## Zeichenmodus-Aktionen

| Aktion | Beschreibung |
|--------|-------------|
| **Linksklick** | Punkt setzen (Polygon, Route, Kabel, Leitung) |
| **Rechtsklick** | Zeichnung abschließen |
| **Strg + Klick/Ziehen** | Begrenzungsbeschränkung aufheben (Route) |

## Bearbeitungsmodus-Aktionen

| Aktion | Beschreibung |
|--------|-------------|
| **Linksklick + Ziehen** auf Knoten | Knoten verschieben |
| **Rechtsklick** auf Knoten | Knoten löschen |
| **Rechtsklick** auf Kante | Neuen Knoten einfügen (Kantenmitte) |
| **Entf-Taste** | Gezogenen Knoten löschen |
| **Doppelklick** (Route) | Knoten an nächste gültige Position snappen |

## Tastenkürzel

| Taste | Beschreibung |
|-------|-------------|
| **Ctrl+S** | Projekt speichern |
| **Ctrl+Shift+S** | Projekt speichern unter… |
| **Ctrl+Z** | Rückgängig (Undo) |
| **Ctrl+Y** | Wiederherstellen (Redo) |
| **Ctrl+C** | Objekt kopieren |
| **Ctrl+V** | Objekt einfügen |
| **Escape** | Aktuelle Aktion abbrechen (Zeichnen/Bearbeiten) |
| **Entf / Delete** | Knoten löschen (im Bearbeitungsmodus beim Ziehen) |
| **Strg** (gehalten) | Begrenzungsbeschränkung beim Zeichnen/Bearbeiten aufheben |

## Rückgängig / Wiederherstellen (Undo/Redo)

HRouting verfügt über ein vollständiges Undo/Redo-System mit bis zu **80 Schritten** Verlauf.

- **Ctrl+Z** – Letzte Aktion rückgängig machen
- **Ctrl+Y** – Wiederhergestellte Aktion erneut ausführen
- Auch über das Menü **Bearbeiten → ↩ Rückgängig** / **↪ Wiederherstellen** erreichbar
- Der aktuelle Zoom und die Pan-Position bleiben beim Undo/Redo erhalten

Das System erfasst Snapshots des gesamten Projektzustandes. Änderungen werden mit einer kurzen Verzögerung (300 ms) zusammengefasst, um bei schnellen Bearbeitungen nicht zu viele Einträge zu erzeugen.

## Kopieren und Einfügen

Alle Objekttypen können kopiert und eingefügt werden:

- **Ctrl+C** – Ausgewähltes Objekt in die Zwischenablage kopieren
- **Ctrl+V** – Objekt aus der Zwischenablage einfügen (als neues Objekt mit neuer ID)

Unterstützte Objekttypen: Heizkreise, Anschlusspunkte, Elektro-Räume, Kabel, HKV, HKV-Leitungen, Text-Annotationen, Grundrisse und Einrichtungen.

Alternativ kann über das Kontextmenü (Rechtsklick) **📋 Kopieren**, **📄 Duplizieren** oder **📥 Einfügen** gewählt werden.

## Kontextmenü (Rechtsklick)

Ein Rechtsklick auf ein Objekt öffnet ein Kontextmenü mit objektspezifischen Aktionen:

### Allgemeine Aktionen (alle Objekte)

| Aktion | Beschreibung |
|--------|-------------|
| **📋 Kopieren** | Objekt in die Zwischenablage kopieren |
| **📄 Duplizieren** | Kopie des Objekts erstellen (sofort, ohne Zwischenablage) |
| **🗑️ Löschen** | Objekt löschen |
| **📥 Einfügen** | Objekt aus der Zwischenablage einfügen |

### Objektspezifische Aktionen

| Objekt | Zusätzliche Aktionen |
|--------|---------------------|
| **Anschlusspunkt (AP)** | ✥ Verschieben, ✏️ Unterputz-Gerät bearbeiten, 🎨 Gerätefarbe bearbeiten, 📝 Notiz bearbeiten |
| **HKV** | ✥ Verschieben |
| **Text-Annotation** | ✥ Verschieben |
| **Grundriss / Einrichtung** | ✥ Verschieben, ↻ Drehen |
| **Polylinien/Polygone** | ➕ Punkt hinzufügen, ➖ Punkt löschen |

### Info-Zeilen (AP)

Bei Anschlusspunkten zeigt das Kontextmenü zusätzlich Informationszeilen an:
- **Unterputz-Gerät** (z.B. „Shelly")
- **Gerätefarbe** (z.B. „weiß")
- **Notiz** (z.B. „Hinter dem Kühlschrank")

## Labels

Alle Beschriftungen können jederzeit per **Drag & Drop** verschoben werden – unabhängig vom aktiven Modus. Das gilt auch für Kabel-Beschriftungen mit optional eingeblendetem Kabeltyp.
