# Elektroplanung

## Übersicht

HRouting unterstützt die Planung elektrischer Installationen auf dem Grundriss:

- **Elektro-Räume (Polygon)** – Räume zur Zuordnung und Auswertung von Anschlusspunkten
- **Anschlusspunkte (AP)** – Steckdosen, Leuchten, Schalter etc.
- **Kabelverbindungen (KV)** – Leitungen zwischen Anschlusspunkten

## Elektro-Räume definieren

1. Klicken Sie auf **➕ Raum** in der Seitenleiste.
2. Ein neuer Raum erscheint unter **🏠 Räume**.
3. Klicken Sie auf **✏️ Raum zeichnen** und setzen Sie die Polygonpunkte.
4. Beenden Sie den Raum per **Rechtsklick**.

### Raum bearbeiten

- **Doppelklick** auf das Raum-Polygon oder **✏️ Raum bearbeiten** startet den Edit-Modus.
- Punkte können verschoben, eingefügt und gelöscht werden.

### AP-Zuordnung zu Räumen

- Die Zuordnung erfolgt automatisch über die Polygonlage.
- Liegt ein AP innerhalb eines Raum-Polygons, wird er diesem Raum zugeordnet.
- Bei Verschieben von AP oder Raum-Polygon wird die Zuordnung automatisch aktualisiert.

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
| Steckdose 2fach | Doppelte Steckdose |
| Steckdose 5fach | 5-fach Steckdosenleiste |
| Steckdose schaltbar | Schaltbare Steckdose |
| Steckdose Starkstrom | CEE-Steckdose |
| Licht | Deckenlicht |
| Lichtquelle | Deckenleuchte |
| Lichtquelle dimmbar | Dimmbare Deckenleuchte |
| Lichtquelle Wand | Wandleuchte |
| Taster | Taster (Klingel etc.) |
| Taster 4fach | 4-fach Taster |
| Ausschalter | Einfacher Ausschalter |
| Wechselschalter | Wechselschalter (2 Schaltstellen) |
| Serienschalter | Serienschalter (2 Kreise) |
| Kreuzschalter | Kreuzschalter (3+ Schaltstellen) |
| LAN | LAN-Dose |
| LAN 2fach | Doppelte LAN-Dose |
| HDMI | HDMI-Anschluss |
| TV | TV-Anschlussdose |
| Audio | Audio-Anschluss |
| Audiobuchse | Audio-Buchse |
| Rauchmelder | Rauchmelder |
| Hitzemelder | Hitzemelder |
| Bewegungsmelder | Bewegungsmelder |
| Praesenzmelder | Präsenzmelder |
| Temperaturfuehler | Temperaturfühler |
| Temperaturmessung | Temperaturmessung |
| Thermostat | Thermostat |
| Fensterkontakt | Fensterkontakt |
| Türkontakt | Türkontakt |
| E-Rollladen | Elektrischer Rollladen |
| E-Rollo | Elektrisches Rollo |
| Markise | Markise |
| Garagentor | Garagentor |
| Gong | Gong / Klingel |
| Heizkreisverteiler | HKV-Symbol |
| Wetterstation | Wetterstation |
| WLanHotspot | WLAN-Hotspot |
| Zutritt | Zutrittskontrolle |

Über **Eigenes Bild…** kann ein beliebiges Symbol (PNG, JPG, SVG, BMP) geladen werden.

## Anschlusspunkte durchnummerieren

Über das Menü **Bearbeiten → 🏷️ Anschlusspunkte durchnummerieren** können gleichnamige Anschlusspunkte automatisch nummeriert werden. Beispiel: Drei APs mit dem Namen „Steckdose" werden zu „Steckdose1", „Steckdose2", „Steckdose3" umbenannt. Nur Gruppen mit mehr als einem gleichnamigen AP werden nummeriert.

### AP-Parameter

| Parameter | Beschreibung | Bereich |
|-----------|-------------|---------|
| **Name** | Bezeichnung | Freitext |
| **Farbe** | Darstellungsfarbe | Farbauswahl |
| **Breite / Höhe** | Symbolgröße | 0,5–20 cm |
| **Symbol** | Schaltzeichen-Typ | Dropdown |
| **Einbauort** | Montageposition | Wand / Decke / Boden / Freitext |
| **Einbauhöhe** | Höhe ab Boden in cm | 0–999 cm |
| **Unterputz-Gerät** | Smarthome-Gerät (z.B. „Shelly") | Freitext |
| **Gerätefarbe** | Farbe des Smarthome-Geräts (z.B. „weiß") | Freitext |
| **Notiz** | Freitext-Notiz (z.B. „Hinter dem Kühlschrank") | Freitext |
| **Schriftgröße** | Label-Schriftgröße | 4–80 pt |

> **Smarthome-Geräte**: Die Felder „Unterputz-Gerät" und „Gerätefarbe" dienen der zusätzlichen Dokumentation verbauter Smart-Home-Aktoren (z.B. Shelly, Sonoff). Die Angaben werden im Kontextmenü (Rechtsklick) angezeigt und im Projektbericht mit exportiert. Über das Kontextmenü können diese auch direkt bearbeitet werden.

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

## Auswertung in der Projektübersicht

- Pro definiertem Elektro-Raum wird ein eigener Reiter mit den zugeordneten APs und Kabelzielen angezeigt.
- Zusätzlich enthält die Gesamtübersicht kabelbezogene Summen und Detailtabellen.
