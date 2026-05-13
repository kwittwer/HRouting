# Projekt & Export

## Neues Projekt

Klicken Sie auf **📄 Neues Projekt** (oder Menü **Datei → Neues Projekt**), um ein leeres Projekt zu erstellen. Alle bisherigen Objekte werden entfernt (mit Sicherheitsabfrage bei ungespeicherten Änderungen).

## Projekt speichern

### Speichern (Ctrl+S)

Klicken Sie auf **💾 Speichern** oder drücken Sie **Ctrl+S**:
- Beim ersten Speichern wird nach einem Dateinamen gefragt.
- Danach wird in die gleiche Datei gespeichert.

### Speichern unter… (Ctrl+Shift+S)

Klicken Sie auf **💾 Speichern unter…** oder drücken Sie **Ctrl+Shift+S**, um das Projekt unter einem neuen Namen/Pfad zu speichern.

Projekte werden als `.hrp`-Dateien gespeichert (intern JSON-Format). Enthält:
- Pfad zum Grundriss (relativ)
- Alle Zeichnungsobjekte (Polygone, Routen, Kabel, APs, HKVs, Texte, …)
- Alle Parameter (Temperaturen, Rohrdurchmesser, Bodenbeläge, …)
- Label-Positionen und Schriftgrößen
- Eigene Symbole (werden als Bilder in einen `images/`-Ordner neben der Projektdatei kopiert)
- PDF-Export-Seiteneinstellungen

## Projekt öffnen

Klicken Sie auf **📂 Projekt öffnen…** und wählen Sie eine `.hrp`- oder `.json`-Projektdatei.

> **Automatisches Laden**: Das zuletzt geöffnete Projekt wird beim Programmstart automatisch geladen.

## Letzte Projekte

Über das Menü **Datei → 🕑 Letzte Projekte** werden die zuletzt geöffneten Projektdateien als Untermenü angezeigt. Ein Klick auf einen Eintrag öffnet das jeweilige Projekt direkt.

## SVG exportieren

Klicken Sie auf **📤 SVG exportieren**, um den gesamten Plan als SVG-Datei zu exportieren.

- Der Grundriss wird eingebettet (SVG direkt, Rasterbilder als Base64).
- Alle Zeichnungsobjekte werden als Vektorgrafik exportiert.
- Geeignet für Weiterbearbeitung in Inkscape, Illustrator etc.
- Ist ein **Export-Rahmen** gesetzt, wird nur der Ausschnitt innerhalb des Rahmens exportiert.

## PDF exportieren

Klicken Sie auf **📄 Als PDF exportieren**, um eine mehrseitige PDF zu erzeugen.

### PDF-Seitenkonfiguration

Vor dem Export öffnet sich ein Konfigurationsdialog, in dem einzelne Seiten aktiviert oder deaktiviert werden können:

| Seite | Inhalt |
|-------|--------|
| **Übersicht** | Vollständiger Plan mit allen Elementen |
| **Heizung** | Plan nur mit Heizkreisen |
| **Rohrlängen** | Tabelle: Heizkreise mit Flächen und Rohrlängen |
| **Hydraulik & Abgleich** | Tabelle: Heizleistung, Volumenstrom, Druckverlust, hydraulischer Abgleich |
| **Elektro** | Elektro-Plan mit Kabellisten und Summen |
| **Pro Grundriss** | Jeweils eine separate Seite pro Grundriss-Layer |

Die Seitenkonfiguration wird im Projekt gespeichert und bei erneutem Export wiederhergestellt.

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

### Tab: Elektro

Der Elektro-Reiter zeigt:
- **Summe pro Leitungstyp** (Gesamtlänge je Kabeltyp)
- **Kabelliste** mit Name, Typ, Länge, Start-AP, End-AP, Start-Raum und End-Raum

### Tab: AP-Verkabelung

Eigener Reiter für die Anschlusspunkt-Sicht:
- je AP alle verbundenen Kabel
- Kabeltyp, Anschlussrolle (Start/Ende) und Länge

### Tabs: Räume (Elektro)

Für jeden definierten Elektro-Raum wird ein separater Reiter erzeugt mit:
- zugeordneten Anschlusspunkten
- zugehörigen Kabeln
- Ziel-AP je Verbindung
- Verbindungslängen

### Tab: HKV-Leitungen

Übersicht aller HKV-Leitungen mit Längen und zugeordneten Start-/Endverteilern.

### CSV-Export

Aus der Projektübersicht können die Daten als CSV-Datei exportiert werden, inklusive raumbezogener Elektro-Auswertung.
