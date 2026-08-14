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

## Unterverteilung (UV) planen

Ein Anschlusspunkt kann zusätzlich als **Unterverteilung (UV)** markiert werden.

1. Öffnen Sie den gewünschten AP.
2. Stellen Sie **AP-Typ** auf **Unterverteilung (UV)**.
3. Klicken Sie auf **🗂️ UV planen…**.
4. Wählen Sie ein Preset oder definieren Sie Reihen und TE manuell.
5. Hinterlegen Sie pro Platz die **Belegung**, eine **Bezeichnung** sowie optional **Kabel/Stromkreis** und **Notiz**.

Die UV-Belegung wird im Projekt gespeichert und in **Projektübersicht**, **CSV** und **PDF-Export** ausgegeben.

## Verteilung in Unterputzdose planen

Ein Anschlusspunkt kann als **Verteilung in Unterputzdose** geführt werden.

1. Öffnen Sie den gewünschten AP.
2. Stellen Sie **AP-Typ** auf **Verteilung in Unterputzdose**.
3. Klicken Sie auf **Verteilung in Unterputzdose…**.
4. Wählen Sie die **Zuleitung**.
5. Legen Sie in der Zuordnungstabelle fest, welche **Ader der Zuleitung** auf welches **abgehende Kabel** und welche **Ader im Abgang** geführt wird.
6. Optional können Sie je Zuordnung eine Notiz ergänzen.

Die Zuordnung wird als strukturierte Daten im Projekt gespeichert und kann über den MCP-Server automatisiert gesetzt oder ausgelesen werden. Außerdem wird sie in **Projektübersicht**, **CSV** und **PDF-Export** ausgegeben.

## Kabelverbindung zeichnen

1. Klicken Sie auf **➕ Kabel** in der Seitenleiste.
2. Eine neue Kabelverbindung (z.B. „EK-1") erscheint unter **🔌 Kabelverbindungen**.
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
| **Kabeltyp im Plan** | Blendet den Kabeltyp direkt in der Plan-Beschriftung ein/aus |
| **Schriftgröße** | Label-Schriftgröße |

> **Hinweis:** Der Kabeltyp ist im Plan standardmäßig ausgeblendet. Wenn aktiviert, erscheint er als **eigene Beschriftung** (unabhängig vom Namen) und kann wie andere Labels per Drag & Drop separat verschoben werden.

### Berechnete Werte

| Wert | Beschreibung |
|------|-------------|
| **Länge** | Kabellänge in m |
| **Start-AP** | Automatisch erkannter Start-Anschlusspunkt |
| **End-AP** | Automatisch erkannter End-Anschlusspunkt |

## Auswertung in der Projektübersicht

- Pro definiertem Elektro-Raum wird ein eigener Reiter mit den zugeordneten APs und Kabelzielen angezeigt.
- Zusätzlich enthält die Gesamtübersicht kabelbezogene Summen und Detailtabellen.
