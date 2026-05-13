# Ansicht & Raster

## Zoom

- **Mausrad nach oben** – Hineinzoomen (zentriert auf Mausposition)
- **Mausrad nach unten** – Herauszoomen
- **Zoom-Bereich**: 0,1× bis 50×

## Verschieben (Pan)

- **Mittlere Maustaste gedrückt halten** und Maus bewegen.
- Alternativ: **Linksklick auf leere Fläche** und ziehen.

## Raster anzeigen

1. Aktivieren Sie das Häkchen **Raster** in der Toolbar.
2. Ein Gitternetz wird über die Zeichenfläche gelegt.

### Rasterabstand einstellen

Im Feld **Abstand** (neben dem Raster-Häkchen) den gewünschten Abstand eingeben:

- **Bereich**: 0,01 m bis 10,00 m
- **Standardwert**: 0,10 m (10 cm)
- **Schrittweite**: 0,01 m

### Rasterfarbe ändern

Klicken Sie auf den Button **Rasterfarbe** in der Toolbar. Es öffnet sich ein Farbwähler mit Transparenz-Unterstützung (Alpha-Kanal).

## Hintergrundfarbe ändern

Klicken Sie auf den Button **Hintergrund** in der Toolbar. Es öffnet sich ein Farbwähler, mit dem Sie die Hintergrundfarbe der Zeichenfläche anpassen können.

- **Standardfarbe**: Dunkelgrau (#2b2b2b)
- Der Button zeigt immer die aktuell gewählte Farbe an.

> **Tipp**: Eine helle Hintergrundfarbe kann bei hellen Grundrissen die Lesbarkeit verbessern.

## Messwerkzeug (📏 Messen)

Mit dem Messwerkzeug können Abstände direkt auf dem Plan gemessen werden.

1. Klicken Sie auf **📏 Messen** in der Toolbar (Toggle-Button).
2. Klicken Sie zwei Punkte auf der Zeichenfläche an.
3. Eine Messlinie mit der realen Länge wird eingeblendet.
4. Es können beliebig viele Messlinien erstellt werden.
5. Klicken Sie erneut auf **📏 Messen**, um den Modus zu beenden.

### Messlinien-Farbe

Neben dem Messen-Button befindet sich ein Farbwähler-Button, mit dem die Farbe der Messlinien angepasst werden kann (Standard: Gelb).

### Messlinien löschen

Klicken Sie auf **✕ Alle Messlinien löschen**, um alle Messlinien auf einmal zu entfernen.

## Export-Rahmen (⬚)

Mit dem Export-Rahmen definieren Sie einen rechteckigen Ausschnitt, der beim SVG- oder PDF-Export verwendet wird.

1. Klicken Sie auf **⬚ Export-Rahmen** in der Toolbar (Toggle-Button).
2. Ziehen Sie mit **gedrückter linker Maustaste** ein Rechteck auf der Zeichenfläche.
3. Nur der Inhalt innerhalb des Rahmens wird exportiert.
4. **Rechtsklick** oder **Escape** → Rahmen abbrechen.
5. Klicken Sie auf **✕** neben dem Button, um den Rahmen zu löschen.

> **Tipp**: Ohne Export-Rahmen wird die gesamte Zeichenfläche exportiert.

## Text-Annotationen (Beschriftungen)

Auf der Zeichenfläche können frei positionierbare Textlabels platziert werden – z.B. für Raumnamen, Hinweise oder Maßangaben.

### Text hinzufügen

1. Klicken Sie auf **➕ Text** in der Seitenleiste.
2. Ein neuer Text (z.B. „Text-1") wird in der Baumansicht angelegt.
3. Klicken Sie auf **📍 Platzieren** und dann auf die gewünschte Stelle im Plan.

### Text-Parameter

| Parameter | Beschreibung |
|-----------|-------------|
| **Name** | Interner Name (für Baumansicht) |
| **Inhalt** | Der angezeigte Text (mehrzeilig möglich) |
| **Schriftgröße** | Größe in pt (Standard: 14) |
| **Farbe** | Textfarbe (Standard: Weiß) |
| **Kommentar** | Interne Notiz (wird nicht auf dem Plan angezeigt) |
| **Sichtbarkeit** | Ein-/Ausblenden |

### Text verschieben

Per **Drag & Drop** auf der Zeichenfläche frei positionieren.

## Sichtbarkeit von Objekten

In der Baumansicht (rechte Seitenleiste) können einzelne Objekte oder ganze Gruppen ein-/ausgeblendet werden:

- **Gruppen-Checkbox** (z.B. 🔥 Heizkreise) – Blendet alle Objekte der Gruppe ein/aus.
- **Einzelobjekt-Checkbox** – Blendet ein einzelnes Objekt ein/aus.
- **Sichtbar-Checkbox** in den Objekteigenschaften – Gleiche Funktion.

## Labels verschieben

Alle Beschriftungen (Heizkreis-Namen, AP-Namen, Kabelnamen etc.) können per **Drag & Drop** auf der Zeichenfläche frei positioniert werden – unabhängig vom aktuellen Zeichenmodus.
