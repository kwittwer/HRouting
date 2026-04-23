# Heizkreise

## Heizkreis hinzufügen

Klicken Sie in der rechten Seitenleiste auf **➕ Heizkreis**. Ein neuer Heizkreis (z.B. „HK-1") wird in der Baumansicht unter **🔥 Heizkreise** angelegt.

## Raumpolygon zeichnen

1. Wählen Sie den Heizkreis in der Baumansicht aus.
2. Klicken Sie auf **✏️ Polygon bearbeiten** (oder den Button zum Neuzeichnen).
3. Setzen Sie Eckpunkte per **Linksklick** auf der Zeichenfläche.
4. Beenden Sie das Polygon per **Rechtsklick**.

Das Polygon definiert die Raumfläche, in der die Heizungsrohre verlegt werden.

### Polygon bearbeiten

- **Doppelklick** auf das Polygon aktiviert den Bearbeitungsmodus.
- **Knoten ziehen** – Eckpunkte per Drag verschieben.
- **Rechtsklick auf Knoten** – Knoten löschen.
- **Rechtsklick auf Kante** – Neuen Knoten an der Kantenmitte einfügen.

## Rohrverlauf zeichnen

1. Klicken Sie auf **✏️ Rohrverlauf zeichnen**.
2. Setzen Sie Punkte für die Rohrführung innerhalb des Polygons.
3. Beenden Sie per **Rechtsklick**.

Der Rohrverlauf wird innerhalb der Raumgrenzen gehalten. Der **Fangwinkel** (Toolbar-Dropdown) sorgt für saubere Winkel (45°, 90° oder 120°).

> **Tipp**: Halten Sie **Strg** gedrückt, um die Begrenzungsbeschränkung temporär aufzuheben.

### Rohrverlauf bearbeiten

- **Doppelklick** auf den Rohrverlauf oder **✏️ Rohrverlauf bearbeiten** klicken.
- Knoten können verschoben, gelöscht oder eingefügt werden (wie beim Polygon).

## Zuleitung zeichnen

Die Zuleitung verbindet den Startpunkt des Heizkreises mit dem Heizkreisverteiler:

1. Klicken Sie auf **✏️ Zuleitung zeichnen**.
2. Zeichnen Sie die Leitung – sie snappt automatisch an HKV-Endpunkte.
3. Beenden Sie per **Rechtsklick**.

## Parameter pro Heizkreis

| Parameter | Beschreibung | Bereich |
|-----------|-------------|---------|
| **Name** | Bezeichnung des Heizkreises | Freitext |
| **Farbe** | Darstellungsfarbe | Farbauswahl |
| **Rohrdurchmesser** | Innendurchmesser des Heizungsrohrs | 1,0–3,2 cm |
| **Verlegeabstand** | Abstand zwischen den Rohrleitungen | 5–30 cm |
| **Randabstand** | Abstand der Rohre zur Wand | 0–50 cm |
| **Soll-Raumtemperatur** | Gewünschte Raumtemperatur | 10–35 °C |
| **Fußbodenbelag** | Belagsart (beeinflusst Wärmeleitung) | Dropdown |
| **Heizkreisverteiler** | Zuordnung zu einem HKV | Dropdown |

### Fußbodenbeläge

- Estrich (Standard)
- Fliesen / Naturstein
- PVC / Linoleum
- Laminat
- Parkett dünn / Parkett dick
- Teppich dünn / Teppich dick

## Berechnete Werte

Folgende Werte werden automatisch berechnet und in der Seitenleiste angezeigt:

| Wert | Beschreibung |
|------|-------------|
| **Fläche** | Raumfläche in m² |
| **Rohrlänge** | Gesamtlänge der verlegten Rohre in m |
| **Zuleitung** | Länge der Zuleitung in m |
| **Gesamt** | Rohrlänge + Zuleitung in m |
| **Leistung** | Heizleistung in W und W/m² |
| **Volumenstrom** | Benötigter Volumenstrom in l/min |
| **Druckverlust** | Druckverlust im Heizkreis in mbar |
