# Heizkreisverteiler (HKV)

## Übersicht

Heizkreisverteiler sind zentrale Verteiler, an die mehrere Heizkreise über Zuleitungen angeschlossen werden. HKV-Leitungen verbinden Verteiler untereinander.

## HKV platzieren

1. Klicken Sie auf **➕ HKV** in der Seitenleiste.
2. Ein neuer Verteiler (z.B. „HKV-1") erscheint in der Baumansicht unter **🔥 Heizkreisverteiler**.
3. Klicken Sie auf **📍 Platzieren** und dann auf die gewünschte Position im Plan.

### HKV verschieben

- Per **Drag & Drop** auf der Zeichenfläche verschieben.
- Angeschlossene Zuleitungen und HKV-Leitungen folgen automatisch.

### HKV-Parameter

| Parameter | Beschreibung | Bereich |
|-----------|-------------|---------|
| **Name** | Bezeichnung | Freitext |
| **Farbe** | Darstellungsfarbe | Farbauswahl |
| **Breite / Höhe** | Symbolgröße | 1–50 cm |
| **Symbol** | Eigenes Icon laden (optional) | PNG/JPG/SVG/BMP |
| **Schriftgröße** | Label-Schriftgröße | 4–80 pt |

## HKV-Leitung zeichnen

HKV-Leitungen verbinden zwei Heizkreisverteiler:

1. Klicken Sie auf **➕ HKV-Leitung** in der Seitenleiste.
2. Eine neue Leitung (z.B. „HL-1") erscheint unter **🔥 HKV-Leitungen**.
3. Klicken Sie auf **✏️ Zeichnen** und setzen Sie Punkte.
4. Beenden Sie per **Rechtsklick**.

> **Auto-Snap**: Start- und Endpunkt werden automatisch am nächstgelegenen HKV eingerastet.

### HKV-Leitung bearbeiten

- **Doppelklick** auf die Leitung oder **✂️ Bearbeiten** klicken.
- Knoten lassen sich verschieben, löschen und einfügen.

### HKV-Leitungs-Parameter

| Parameter | Beschreibung |
|-----------|-------------|
| **Name** | Bezeichnung |
| **Farbe** | Darstellungsfarbe |
| **Rohrtyp** | Leitungstyp (z.B. „DN20") |
| **Schriftgröße** | Label-Schriftgröße |

### Berechnete Werte

| Wert | Beschreibung |
|------|-------------|
| **Länge** | Leitungslänge in m |
| **Start-HKV** | Automatisch erkannter Start-Verteiler |
| **End-HKV** | Automatisch erkannter End-Verteiler |

## Heizkreise einem HKV zuordnen

In den Heizkreis-Eigenschaften gibt es ein Dropdown **Heizkreisverteiler**. Wählen Sie dort den gewünschten HKV aus. Die Zuordnung wird in der Projektübersicht und beim hydraulischen Abgleich berücksichtigt.
