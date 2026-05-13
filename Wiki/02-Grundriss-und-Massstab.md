# Grundriss & Maßstab

## Grundriss laden

Klicken Sie in der Toolbar auf **📂 Grundriss laden**.

Unterstützte Formate:
- **SVG** (Vektorgrafik – empfohlen für beste Qualität)
- **PNG**, **JPG**, **JPEG**, **BMP** (Rasterbilder)

Der Grundriss wird auf der Zeichenfläche angezeigt und kann per Mausrad gezoomt und per Mittelklick-Drag verschoben werden.

## Mehrere Grundrisse (Multi-Layer)

HRouting unterstützt **mehrere Grundriss-Layer**, die übereinander angezeigt werden. Dies ist nützlich z.B. für:
- Verschiedene Stockwerke in einem Projekt
- Detailpläne über dem Gesamtgrundriss
- Einblendung von Referenzplänen

### Grundriss-Layer hinzufügen

1. Klicken Sie in der Seitenleiste auf **➕ Grundriss**.
2. Ein neuer Layer (z.B. `grundriss-2`) wird angelegt.
3. Laden Sie ein Bild für den neuen Layer.

### Layer-Eigenschaften

Jeder Grundriss-Layer hat eigene Einstellungen:

| Eigenschaft | Beschreibung |
|-------------|-------------|
| **Name** | Anzeigename (z.B. „Erdgeschoss", „Obergeschoss") |
| **Bild** | SVG/PNG/JPG/BMP-Datei |
| **Maßstab** | Eigene Referenzlinie und Skalierung (`mm_per_px`) |
| **Position** | Horizontale/vertikale Verschiebung (Offset X, Y) |
| **Drehung** | Rotation in Grad |
| **Deckkraft** | Transparenz (0 % – 100 %) |
| **Sichtbarkeit** | Ein-/Ausblenden des Layers |
| **Polygonfarbe** | Farbe der Grundriss-Umrandung |

### Layer-Reihenfolge

Die Layer werden in der Reihenfolge der Baumansicht gerendert (oben = hinten, unten = vorne). Die Reihenfolge kann per Drag & Drop angepasst werden.

## Einrichtungen / Möbel

Unter jedem Grundriss-Layer können **Einrichtungsgegenstände** als Bild-Overlays hinzugefügt werden. Damit lassen sich Möbel, Küchenzeilen oder Sanitärobjekte auf dem Plan positionieren.

### Einrichtung hinzufügen

1. Klicken Sie unter dem gewünschten Grundriss auf **➕ Einrichtung**.
2. Laden Sie ein Bild (SVG, PNG, JPG, BMP).
3. Zeichnen Sie optional eine Referenzlinie für die Skalierung.
4. Alternativ geben Sie feste Maße (Breite × Höhe in mm) ein.

### Einrichtungs-Eigenschaften

| Eigenschaft | Beschreibung |
|-------------|-------------|
| **Name** | Bezeichnung (z.B. „Küche", „Badewanne") |
| **Bild** | SVG/PNG/JPG/BMP-Datei |
| **Feste Maße** | Breite × Höhe in mm (alternativ zu Referenzlinie) |
| **Position** | Verschiebung auf dem Grundriss |
| **Drehung** | Rotation in Grad |
| **Deckkraft** | Transparenz |
| **Sichtbarkeit** | Ein-/Ausblenden |
| **Polygonfarbe** | Farbe der Umrandung |

### Einrichtung verschieben / drehen

- **Rechtsklick** auf die Einrichtung → **✥ Verschieben** oder **↻ Drehen**.
- Oder über die Parameter in der Seitenleiste.

## Referenzlinie zeichnen

Um korrekte Maße zu erhalten, muss ein Maßstab gesetzt werden:

1. In der rechten Seitenleiste unter **📏 Maßstab** auf **① Referenzlinie im Plan zeichnen** klicken.
2. Auf der Zeichenfläche zwei Punkte anklicken, deren reale Entfernung bekannt ist (z.B. eine Wand mit bekannter Länge).
3. Die Referenzlinie wird als rote Linie angezeigt.

> **Hinweis**: Jeder Grundriss-Layer kann seine eigene Referenzlinie und damit seinen eigenen Maßstab haben.

## Reale Länge eingeben

1. Im Feld **② Reale Länge** den tatsächlichen Abstand in Metern eingeben.
2. Auf **✔ Anwenden** klicken.
3. Der berechnete Maßstab (m/px) wird angezeigt.

> **Hinweis**: Der Maßstab wird für alle Berechnungen verwendet (Flächen, Rohrlängen, Kabellängen). Eine korrekte Kalibrierung ist daher wichtig.
