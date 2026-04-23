# Projekt & Export

## Projekt speichern

### Speichern (Ctrl+S Prinzip)

Klicken Sie auf **💾 Speichern**:
- Beim ersten Speichern wird nach einem Dateinamen gefragt.
- Danach wird in die gleiche Datei gespeichert.

### Speichern unter…

Klicken Sie auf **💾 Speichern unter…**, um das Projekt unter einem neuen Namen/Pfad zu speichern.

Projekte werden als `.json`-Dateien gespeichert. Enthält:
- Pfad zum Grundriss (relativ)
- Alle Zeichnungsobjekte (Polygone, Routen, Kabel, APs, HKVs, …)
- Alle Parameter (Temperaturen, Rohrdurchmesser, Bodenbeläge, …)
- Label-Positionen und Schriftgrößen
- Eigene Symbole (werden als Bilder in einen `images/`-Ordner neben der Projektdatei kopiert)

## Projekt öffnen

Klicken Sie auf **📂 Projekt öffnen…** und wählen Sie eine `.json`-Projektdatei.

> **Automatisches Laden**: Das zuletzt geöffnete Projekt wird beim Programmstart automatisch geladen.

## SVG exportieren

Klicken Sie auf **📤 SVG exportieren**, um den gesamten Plan als SVG-Datei zu exportieren.

- Der Grundriss wird eingebettet (SVG direkt, Rasterbilder als Base64).
- Alle Zeichnungsobjekte werden als Vektorgrafik exportiert.
- Geeignet für Weiterbearbeitung in Inkscape, Illustrator etc.

## PDF exportieren

Klicken Sie auf **📄 Als PDF exportieren**, um eine mehrseitige PDF zu erzeugen.

Die PDF enthält:
- Deckblatt mit Projektinformationen
- Verlegeplan mit allen Heizkreisen
- Weitere Plan-Seiten je nach Inhalt

## Projektübersicht

Klicken Sie auf **📊 Projektübersicht**, um eine detaillierte Aufstellung zu sehen:

### Tab: Längenübersicht

| Spalte | Beschreibung |
|--------|-------------|
| Heizkreis | Name |
| Fläche | m² |
| Rohrlänge | m |
| Zuleitung | m |
| Gesamt | m |

### Tab: Hydraulik

| Spalte | Beschreibung |
|--------|-------------|
| Leistung | W und W/m² |
| Volumenstrom | l/min |
| Druckverlust | mbar |

### Tab: Hydraulischer Abgleich

Pro HKV werden alle angeschlossenen Heizkreise mit Kv-Wert und Ventil-Differenzdruck berechnet. Basiert auf einer gemeinsamen Pumpe.

### Tab: Kabel & HKV-Leitungen

Übersicht aller Kabelverbindungen und HKV-Leitungen mit Längen und zugeordneten Anschlusspunkten/Verteilern.

### CSV-Export

Aus der Projektübersicht können die Daten als CSV-Datei exportiert werden.
