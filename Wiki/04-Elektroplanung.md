# Elektroplanung

## Übersicht

HRouting unterstützt die Planung elektrischer Installationen auf dem Grundriss:

- **Anschlusspunkte (AP)** – Steckdosen, Leuchten, Schalter etc.
- **Kabelverbindungen (KV)** – Leitungen zwischen Anschlusspunkten

## Anschlusspunkt platzieren

1. Klicken Sie auf **➕ AP** in der Seitenleiste.
2. Ein neuer Anschlusspunkt (z.B. „AP-1") erscheint in der Baumansicht unter **🔌 Anschlusspunkte**.
3. Klicken Sie auf **📍 Platzieren** und dann an die gewünschte Stelle auf der Zeichenfläche.

### Anschlusspunkt verschieben

- Einfach per **Drag & Drop** auf der Zeichenfläche verschieben.
- Verbundene Kabel folgen automatisch mit.

### Anschlusspunkt duplizieren

Klicken Sie auf **📋 Duplizieren**, um eine Kopie mit gleichen Einstellungen zu erstellen.

## Symbole (DIN EN 60617)

Folgende Standardsymbole stehen zur Verfügung:

| Symbol | Beschreibung |
|--------|-------------|
| Steckdose | Einfache Steckdose |
| Doppelsteckdose | Doppelte Steckdose |
| Leuchte | Deckenleuchte |
| Ausschalter | Einfacher Ausschalter |
| Wechselschalter | Wechselschalter (2 Schaltstellen) |
| Serienschalter | Serienschalter (2 Kreise) |
| Kreuzschalter | Kreuzschalter (3+ Schaltstellen) |
| Taster | Taster (Klingel etc.) |

Über **Eigenes Bild…** kann ein beliebiges Symbol (PNG, JPG, SVG, BMP) geladen werden.

### AP-Parameter

| Parameter | Beschreibung | Bereich |
|-----------|-------------|---------|
| **Name** | Bezeichnung | Freitext |
| **Farbe** | Darstellungsfarbe | Farbauswahl |
| **Breite / Höhe** | Symbolgröße | 0,5–20 cm |
| **Symbol** | Schaltzeichen-Typ | Dropdown |
| **Schriftgröße** | Label-Schriftgröße | 4–80 pt |

## Kabelverbindung zeichnen

1. Klicken Sie auf **➕ Kabel** in der Seitenleiste.
2. Eine neue Kabelverbindung (z.B. „KV-1") erscheint unter **🔌 Kabelverbindungen**.
3. Klicken Sie auf **✏️ Kabel zeichnen** und setzen Sie Punkte für den Kabelverlauf.
4. Beenden Sie per **Rechtsklick**.

> **Auto-Snap**: Start- und Endpunkt der Kabel werden automatisch am nächstgelegenen Anschlusspunkt eingerastet. Die zugehörigen APs werden in den berechneten Werten angezeigt.

### Kabel bearbeiten

- **Doppelklick** auf ein Kabel oder **✏️ Kabel bearbeiten** aktiviert den Bearbeitungsmodus.
- Knoten verschieben, löschen oder einfügen wie bei anderen Polylinien.

### Kabel-Parameter

| Parameter | Beschreibung |
|-----------|-------------|
| **Name** | Bezeichnung |
| **Farbe** | Darstellungsfarbe |
| **Typ** | Kabeltyp (z.B. „5x1,5") |
| **Kommentar** | Freitext-Notiz |
| **Schriftgröße** | Label-Schriftgröße |

### Berechnete Werte

| Wert | Beschreibung |
|------|-------------|
| **Länge** | Kabellänge in m |
| **Start-AP** | Automatisch erkannter Start-Anschlusspunkt |
| **End-AP** | Automatisch erkannter End-Anschlusspunkt |
