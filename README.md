# HRouting

Desktop-Anwendung für die Planung von Fußbodenheizungen und Elektroinstallationen auf Grundrissen.

**Aktueller Stand:** Version `0.2.1` (siehe `main.py`).

## Kernfunktionen

- Heizkreise mit Polygon, Rohrverlauf, Zuleitung und automatischer Berechnung nach DIN EN 1264
- Elektroplanung mit Räumen, Anschlusspunkten, Kabeln, UV-/UP-Verteilung und Auswertung
- Grundrissverwaltung mit mehreren Layern, Maßstab pro Layer, Möbel/Einrichtung, Texten und Messwerkzeug
- Export als PDF, SVG und CSV sowie Projektübersicht mit Fach-Tabellen

## Voraussetzungen

- Python 3.10+
- `pip`

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Start

```bash
# GUI starten
python main.py

# GUI + MCP HTTP-Server (lokal: http://127.0.0.1:3274/mcp)
python main.py --mcp

# Stdio-MCP-Server (ohne HTTP-Endpunkt)
python main.py --mcpstdio

# Projekt direkt öffnen
python main.py mein_projekt.hrp
```

## Heizkurvenanalyse

Unter **Ansicht → Heizkurvenanalyse** öffnet sich ein eigenständiges Fenster.

1. Je Heizkreis genau drei Referenzwerte in die Tabelle eintragen:
   **spezifische Heizlast in W/m²**, zugehörige **Innentemperatur** und
   **Außentemperatur**. Doppelklick, F2 oder direktes Tippen bearbeitet die Zelle.
2. Den Außentemperaturbereich mit Minimum, Maximum und Schrittweite einstellen
   und **Berechnen** drücken.
3. Unter der Tabelle den **Heizkreis auswählen**. Das Diagramm zeigt für diesen
   Kreis die notwendige Vorlauftemperatur über der Außentemperatur. Ein Wechsel
   der Auswahl zeigt die bereits berechnete Kurve ohne erneuten Rechenlauf.

Mit **Alle Heizkreise** in derselben Auswahl werden sämtliche Kurven gemeinsam
angezeigt. Jeder Kreis hat eine eigene, beim Wechsel zur Einzelansicht stabile
Farbe; die Legende steht unter dem Diagramm. Nicht berechenbare Kreise werden
im Status genannt. CSV exportiert bei dieser Auswahl alle Kreise, sonst nur
den ausgewählten Kreis.

Im normalen **PDF-Export** kann über **Heizkurvenseite einfügen** eine eigene
Diagrammseite angelegt werden. Die Heizkreise werden direkt im PDF-Dialog
ausgewählt; neue Heizkurvenseiten wählen zunächst alle vorhandenen Kreise.
Eine leere Auswahl bleibt leer und wird im PDF als Hinweis ausgegeben.
Die Auswahl ist unabhängig von der aktuellen Diagrammansicht.

Der PDF-Export berechnet die Kurven aus den gespeicherten Referenzwerten,
dem eingestellten Außentemperaturbereich, der Heizgrenze und den aktuellen
Projekt-/Raumtemperaturen neu. Das Analysefenster muss nicht geöffnet sein.
Noch offene gültige Eingaben werden vor dem Export übernommen; ungültige
offene Eingaben verhindern den Export statt still alte Werte zu verwenden.
Fehlende Referenzwerte oder entfernte Heizkreise werden in der Legende benannt.
Diagramm und Texte werden als Vektoren exportiert. Lange Legenden laufen auf
Fortsetzungsseiten weiter, ohne die Schrift immer kleiner zu skalieren.
Die Heizkreis-Detailseite mit Grundriss bleibt unverändert verfügbar.

Jeder Heizkreis wird **unabhängig** berechnet. Es gibt keine gemeinsame
Anlagenkurve und keine Auswahl unterschiedlicher Lastmodi in diesem Fenster.
Fehlende oder fehlerhafte Daten eines anderen Kreises verhindern nicht die Kurve
eines korrekt ausgefüllten Kreises. Die drei Referenzfelder können jederzeit
bearbeitet werden; eine leere Heizlast ist unbekannt, eine explizite Null bedeutet
keinen Heizbedarf. Ungültige Eingaben müssen vor dem Projektspeichern korrigiert werden.

Die **Spreizung stammt aus dem Projekt** (aktueller Vorlauf minus Rücklauf) und
bleibt über die Kurve konstant. Sie muss positiv sein. Die gewünschte
**Soll-Raumtemperatur stammt aus dem jeweiligen Heizkreis**. Die Referenz-
Innentemperatur beschreibt ausschließlich den Berechnungspunkt des W/m²-Werts.
Der spezifische Bedarf wird proportional zur Temperaturdifferenz umgerechnet:

`Bedarf [W/m²] = Referenzlast × max(0, (Soll innen − außen) / (Referenz innen − Referenz außen))`

Die Referenz-Innentemperatur muss über der Referenz-Außentemperatur liegen.
Beispiel: 50 W/m² bei 20 °C innen und −13 °C außen ergeben bei gleicher
Solltemperatur und 0 °C außen rund 30,30 W/m². Der Gesamtbedarf verwendet die
aktuelle Heizkreisfläche. Alte Watt-Lasten, Flächenüberschreibungen und
Hydrauliklimits werden als Projektdaten erhalten, aber für dieses neue
Einzelkreisdiagramm nicht verwendet und nicht stillschweigend konvertiert.

Die notwendige Vorlauftemperatur wird aus der vorhandenen vereinfachten
stationären Heizleistungsformel bei fester Spreizung numerisch bestimmt und
auf 0,1 °C nach oben gerundet. Berücksichtigt werden Verlegeabstand, Bodenbelag
und Kreis-Solltemperatur. Ein gezeichneter Rohrverlauf ist für diese rein
thermische Kurve nicht erforderlich; eine gültige Fläche ist erforderlich.
Die Rechnung sucht bis zur **Modellobergrenze 90 °C**. Das ist keine zulässige
Betriebstemperatur oder Freigabe für den Fußboden. Bei nicht erreichbarer Last
wird keine Vorlauftemperatur erfunden. Ab der eingestellten **Heizgrenze** gilt
**Heizung aus**; Nullbedarf und nicht berechenbare Punkte werden als Lücken
statt als 0-°C-Vorlauf dargestellt. Der gesamte Außentemperaturbereich bleibt sichtbar.

Die ausgewählte Kurve kann als CSV mit Temperaturen, Bedarf und Referenzwerten
exportiert werden. Berechnungen sind abbrechbar. Eingaben werden nach gültigen
Änderungen mit dem HRP-Projekt gespeichert und unterstützen Undo/Redo. Öffnen
allein ändert das Projekt nicht. Ergebnisse werden nach Änderungen verworfen,
nicht gespeichert. Projekt-Vorlauf, -Rücklauf und Raumtemperaturen bleiben unverändert.

Die Analyse simuliert weder zeitliches Aufheizen noch hydraulische Ventilregelung,
Pumpenförderhöhe, COP oder Stromersparnis und ersetzt keinen zertifizierten
Heizlast- oder Sicherheitsnachweis.

## PIR- und Radarplanung

Im Eigenschaften-Dock eines Anschlusspunkts unter **Sensor** Typ, Winkel,
Reichweite und Sichtbarkeit einstellen. Vorhandene Symbole „Bewegungsmelder“
und „Praesenzmelder“ werden automatisch als PIR bzw. Radar erkannt; die
Technik kann unabhängig vom Symbol geändert werden. Unter **Ansicht →
Erfassungsbereiche anzeigen** lassen sich alle Flächen gemeinsam ausblenden.
Sichtbare Flächen erscheinen auch im Grundriss-Export.

Bei **Wand** zeigt der Plan einen richtbaren Sektor: 0° zeigt nach oben,
90° nach rechts. Winkel und Reichweite bestimmen die Draufsicht. Bei
**Decke** zeigt er die Bodenprojektion des Sensorkegels. Bei **Kippwinkel 0°**
zeigt der Sensor senkrecht nach unten und erzeugt einen Kreis. Ein Kippwinkel
von beispielsweise **25°** ergibt eine versetzte, meist elliptische Fläche.
**Richtung** bestimmt die Kipprichtung (0° nach oben, 90° nach rechts im Plan).
Bei starker Neigung oder kurzer Reichweite wird die Fläche durch die maximale
Schrägdistanz begrenzt und kann daher von einer vollständigen Ellipse abweichen.
Die bestehende „Höhe über Boden“ (Eingabe in m, Speicherung in mm) ist dafür die Montagehöhe;
Deckenöffnung, Kippwinkel und Reichweite bestimmen die Bodenfläche. Die Reichweite
ist hier die Schrägdistanz vom Sensor, an der Wand dagegen der Abstand in
der Draufsicht. Ohne plausible Deckenhöhe (mindestens 1 m) oder wenn die
Reichweite den Boden nicht erreicht, erscheint keine Fläche. Ein 360°-
Wandwinkel bedeutet Rundumsicht, nicht eine 360°-Deckenöffnung.

Falls ein gezeichneter Elektro-Raum den AP enthält, wird seine Fläche auf
den vom Sensor aus sichtbaren Teil des Raum-Polygons begrenzt, auch bei
verwinkelten Räumen und APs direkt auf einer Raumkante. Ohne Raum-Polygon
bleibt sie unbeschnitten. Zusätzliche Wände aus Grundrissbildern, Möbel und
tatsächliche Sensoreigenschaften werden nicht simuliert.

## AP-Aufkleber als PDF

Im PDF-Exportdialog mit **AP-Aufkleberbogen einfügen** eine Aufkleberseite
anlegen und unter **Aufkleber-Raum** einen Raum oder **Alle Räume** auswählen.
Bei allen Räumen werden auch APs ohne Raumzuordnung berücksichtigt.

Die Bögen sind A4 im Querformat mit einem Raster aus 4 × 3 Aufklebern,
jeweils 60 × 60 mm. Zusätzliche Bögen werden automatisch erzeugt.
Jeder Aufkleber enthält Mittellinien mit Mittelkreuz, einen Kreis mit 60 mm
Durchmesser, das AP-Symbol, AP-Nummer und Namen, Raum, Montageposition,
Montagehöhe sowie angeschlossene Kabel mit Nummer, Namen und Kabeltyp.
Lange Beschriftungen werden umgebrochen und bei Bedarf verkleinert.

Für maßhaltige Aufkleber im Druckdialog **Tatsächliche Größe / 100 %** wählen
und die Option zur Anpassung an die Seitengröße deaktivieren.

## Fotodokumentation und Messung

Unter **Ansicht → Fotodokumentation** öffnet sich ein eigenes Fenster. Das
Projekt muss zuerst gespeichert werden. Die Verarbeitung erfolgt lokal.

1. Über **Flächen vorbereiten** die Wandseiten, Boden- und Deckenflächen aus
   den Elektro-Raumpolygonen anlegen. Unter **APs den Flächen zuordnen** die
   vorgeschlagene Wand oder Fläche bestätigen; Raumecken bleiben bewusst unklar.
   In der Grundrissübersicht bezeichnet **A** den Ursprung und **B** die positive
   horizontale Richtung der gewählten Fläche.
2. **Messmarker registrieren und exportieren** erzeugt Dokumentationsaufkleber
   für APs und zusätzliche Flächenreferenzen. Alternativ im PDF-Export einen
   AP-Aufkleberbogen anlegen und **Messmarker / Fotodokumentation** aktivieren.
   Die Kennungen werden vor der PDF-Ausgabe mit dem Projekt gespeichert.
   AP-Muster haben 80 mm, zusätzliche Referenzmuster 120 mm schwarze Kantenlänge.
   Zunächst steht jeweils ein Marker auf einer A4-Seite, mit Beschriftung,
   Ausrichtung und einer 100-mm-Kontrollstrecke. Die bisherigen 60-mm-Bögen
   bleiben unverändert verfügbar.
3. Bei **100 % / Originalgröße** drucken und die Kontrollstrecke nachmessen.
   Den Marker flach und unverdeckt auf dieselbe Ebene kleben, die später
   gemessen werden soll. Bezug zum AP ist die **Markermitte**, nicht ein
   versetzter Geräte- oder Bohrmittelpunkt. Für große Bildbereiche zusätzliche
   Referenzen verteilen. Marker nicht über Kanten kleben.
4. Für absolute Abstände unter **Gemessene Markerreferenz / FFB** die tatsächlich
   gemessene Markermitte und Ausrichtung eintragen. An Wänden ist `u` der Abstand
   ab A und `v` die Höhe über Fertigfußboden. Bei Bezug zum Rohboden wird der
   erfasste Bodenaufbau abgezogen. Diese Ist-Referenzen werden nicht aus
   geplanten AP-Höhen erfunden. Ohne eingemessene Referenz sind lokale Strecken
   möglich, aber keine absoluten FFB- oder Eckabstände.
5. JPG-/JPEG-/PNG-Fotos importieren. Erkannte Marker, APs und zugehörige Flächen
   werden automatisch ausgewertet. Jede im Foto erkannte Ebene bekommt ihre
   eigene Kalibrierung. Unbekannte oder nicht zugeordnete Bilder bleiben als
   Dokumentation erhalten; Zuordnungen lassen sich korrigieren und Fotos erneut
   analysieren. Import und Analyse sind abbrechbar.
6. Im Foto eine kalibrierte Fläche auswählen und über **Messbereich: vier
   Eckpunkte** ihren sichtbaren ebenen Bereich abgrenzen. Innerhalb dieses
   Bereichs stehen Strecke, Horizontal-/Vertikalabstand, FFB, Wandursprung A und
   Abstand zur AP-Markermitte zur Verfügung. Original- und entzerrte Ansicht
   verwenden dieselben Messkoordinaten. Ein unabhängig gemessenes **Kontrollmaß**
   zeigt die Abweichung der Kalibrierung; eine Abweichung bis 20 mm wird als
   geprüft gekennzeichnet, ist aber keine Genauigkeitsgarantie für andere Punkte.

Fotos können mit Notiz und Bauzustand versehen werden. Messungen lassen sich
benennen, löschen und per Undo/Redo zurücknehmen. Änderungen an Raumgeometrie,
Flächenzuordnung oder Referenzen sperren veraltete Kalibrierungen, statt alte
Messwerte still an neue Planmaße anzupassen. Bereits ausgegebene Markerkennungen
werden auch beim Rückgängigmachen nicht an andere APs vergeben.

Die HRP-Datei enthält Zuordnungen, Markerregister, Kalibrierungen und Messungen.
Originalfotos liegen unverändert unter `photos/<Projektkennung>/` neben dem
Projekt. **HRP-Datei und Fotoordner gemeinsam weitergeben.** Beim Speichern unter
einem anderen Ordner werden benötigte Fotos mitkopiert. Datei-Hashes erkennen
ersetzte Originale; fehlende Fotos können mit dem identischen Original neu
verknüpft werden. Gelöschte Fotos werden nicht sofort physisch entfernt, damit
Undo weiterhin funktioniert.

**Grenzen der ersten Version:** Perspektiventzerrung gilt nur für die gewählte
Ebene, nicht für Tiefen oder Messungen um Raumecken. Vor- oder zurückliegende
Leitungen verursachen Parallaxe. Kleine, unscharfe oder stark schräg aufgenommene
Marker sind keine verlässliche Referenz für eine ganze Wand. Objektivverzeichnung
ist noch ungeprüft; insbesondere Ultraweitwinkel vermeiden. Das Ziel von etwa
1–2 cm muss mit echten Aufnahmen und Kontrollmaßen überprüft werden. Die Funktion
liefert keine Bohrfreigabe. Panoramen, HEIC/RAW, 3D-Rekonstruktion und automatische
Kamerakalibrierung sind nicht enthalten.

## Projektformat (.hrp)

HRouting speichert Projekte als JSON mit der Endung `.hrp`.

- Schema: [`hrp_schema.json`](hrp_schema.json)
- Validator: [`validate_hrp.py`](validate_hrp.py)
- Beispiel: [`examples/minimal.hrp`](examples/minimal.hrp)

```bash
python validate_hrp.py projekt.hrp
python validate_hrp.py --schema-only projekt.hrp
python validate_hrp.py --json projekt.hrp
```

## Build

```bash
python build.py
```

Die EXE wird unter `dist\HRouting_<VERSION>.exe` erzeugt.  
Dateizuordnung für `.hrp` kann bei Bedarf manuell gesetzt werden:

```bash
python register_filetype.py install dist\HRouting_<VERSION>.exe
python register_filetype.py uninstall
```

## Dokumentation

- Benutzerdoku: [`Wiki/README.md`](Wiki/README.md)
- MCP-Integration: [`Wiki/10-MCP-Server.md`](Wiki/10-MCP-Server.md)

## Lizenz

Copyright (C) 2026 Konrad-Fabian Wittwer

Dieses Programm ist freie Software: Sie können es unter den Bedingungen der
**GNU General Public License** (GPL v3), wie von der Free Software Foundation
veröffentlicht, weitergeben und/oder modifizieren – entweder gemäß Version 3
der Lizenz oder (nach Ihrer Wahl) jeder späteren Version.

Siehe [LICENSE](LICENSE) für den vollständigen Lizenztext.
