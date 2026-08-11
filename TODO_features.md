
# HRouting – Feature ToDo

Stand: 2026-08-07
Basis: Code-Analyse der neuen UI und Testprojekt `examples/Planung_Linda.hrp`

# Basics

* [X] A-1 per Kontextmenü (rechte maustatse) sollen immer in abhänigkeit des aktuell gewählten worspaces die für das aktuell selektierte elemnete verfügbaren werkzeuge angezeigt werden
* [X] A-2 im Kontextmenü allegeime für alles einfügen:
  -> undo
  -> redo
  -> kopieren
  -> einfügen
  -> ausschneiden
  -> dupizieren
  -> löschen
* [X] A-3 Funktion zum kopieren und einfügen von elementen (AP, kabel, usw)
* [X] A-4 grundsätzlich soll für alle elemente der Name anstatt der nummer angezeigt werden also z.B. Ankleide anstatt ER-13 (siehe Planung-Linda.hrp) Das gilt für alle elemente
* [X] es soll persistent sein was in der navigation auf bzw zugefaltet ist. bitte füge einen butteon um alle zusammen zu falten ein

## Blocker

- [X] B-1 Navigator-Selektion gegen echten Canvas/Properties-Workflow pruefen und reparieren
  Das im Navigator selektierte element soll aktiv geschalten sein, Blau gekennzeichnet und dessen eigenschaften sollen im eigenschaften fenster sichtbar sein
- [X] B-2 `fp.polygon` im Grundriss-Workspace an selektierten Grundriss binden
- [X] B-3 `draw_polygon` fuer Grundriss und Einrichtung korrekt routen
- [X] B-4 Strucktur in Seitenleiste überarbeiten. Stuckur soll wie folgt sein:
  Grundriss
  -> Heizung
  -> Heizkreisverteiler
  -> Heizkreise
  -> Elektro
  -> Räume
  -> Anschlusspunkte
  -> Kabel
  -> Einrichtung
  -> Texte
  [X] F-1 Einrichtung hinzufuegen Workflow pruefen
  [X] F-2 Einrichtungs-Polygon zeichnen pruefen
  [X] F-3 Einrichtung verschieben / rotieren pruefen

# Grundriss

- [X] G-2 Grundriss verschieben pruefen
- [X] G-3 Grundriss drehen pruefen
- [X] G-4 Referenzlinie zeichnen pruefen
- [X] G-5 Referenzlaenge nur ueber `Aktualisieren` neu berechnen
- [X] G-6 Grundriss-Umriss entfernen
- [X] G-8 Opacity / Offset / Rotation im Properties-Dock pruefen
- [X] G-9 Referenzlinie sichtbar / unsichtbar pruefen
- [X] G-10 Hilfslinien je Grundriss pruefen

## Heizung

- [X] H-1 Heizkreis hinzufuegen pruefen
- [X] H-2 Heizkreis-Polygon zeichnen / bearbeiten pruefen
- [X] H-3 Route zeichnen / bearbeiten pruefen
- [X] H-4 Versorgungsleitung zeichnen / bearbeiten pruefen
- [X] H-5 HKV platzieren und Leitungen pruefen
- [X] H-6 Berechnungen-Dock gegen Planung_Linda pruefen

## Elektro

- [X] E-1 Anschlusspunkt hinzufuegen / platzieren pruefen
- [X] E-2 Eigenschaften von APs editieren pruefen
- [X] E-3 UV-Dialog pruefen
- [X] E-4 UP-Distribution pruefen
- [X] E-5 Elektro-Raum zeichnen / bearbeiten pruefen
- [X] E-6 Kabel zeichnen / bearbeiten pruefen
  - [X] E-6.1 ich kann keine neuen kabel zichen
- [X] E-7 Strangschema gegen Planung_Linda pruefen
- [X] E-8 BOM / Exportdaten gegen Planung_Linda pruefen
- [X] E-9 in den APs werden keine Bilder angezeigt
- [X] E-10 aktuell werden alle APs blau dargetsellt obwohl in den eingeschaften etwas anderes eingestellt ist. bite fixen

## Einrichtung

- [X] F-1 Einrichtung hinzufuegen Workflow pruefen
- [X] F-2 Einrichtungs-Polygon zeichnen pruefen
- [X] F-3 Einrichtung verschieben / rotieren pruefen
- [X] F-4 Parent-Floorplan und feste Groessen pruefen
- [X] F-5 wenn ich im einrichtungs Workspace pin mmöchte ich die elemente direkt per drag and drop verschieben können

## Vermessung

- [X] A-1 Distanzmessung pruefen

  - [X] A-1.1 maß wird nicht richtig angezeigt. -> mass soll schon beim zeiehn des zweiten messpunkte angezeigt werden
- [X] A-2 Winkelmessung pruefen
- [X] A-3 Hilfslinien zeichnen / bearbeiten pruefen
- [X] A-4 Text platzieren / editieren pruefen
- [X] V-01 Distanz-/Winkel-Text wird standardmäßig nahe des zuletzt gezeichneten Punktes gesetzt und kann danach verschoben werden
- [X] V-02 Punkte von Maßlinien sind im Workspace Vermessung per Drag-and-drop verschiebbar; Messwerte aktualisieren sich live
- [X] V-03 Per Kontextmenü auf Maßlinie ist das Löschen der einzelnen Linie verfügbar
- [X] V-04 Eigenschaften je Maßlinie sind im Eigenschaftenfenster editierbar (Farbe, Linientyp, Strichstärke, Textgröße, Name)
- [X] V-05 Selektierte Maßlinie wird gehighlightet (wie z. B. Kabel)
- [X] V-06 Maßlinien sind als eigene Kategorien im Navigator aufgeführt
- [X] V-07 Werkzeug "Text platzieren" funktioniert per Klick, Text ist platzierbar und Eigenschaften (Text, Textgröße, Farbe) sind editierbar
- [X] V-08 Maßlinienpunkte fangen am Raster; mit gedrückter Strg-Taste wird Rasterfang ignoriert

# Projektübersicht

Die Projektübersicht soll ein zusätliches eigenes andockbares Fenster sein ähnlich wie z.B. das Eigenschaten Fenster. Es soll dazu dienen berechnete Infomation für das gesamte Projekt darstellen.
Das Fenster soll über folgende Tabs verfügen: 
- Allgemein
- Heizung
- Elektro

Innerhalb der Tabs soll in zusammenfaltbarenen sektionen dann die infos dargestellt werden

## Tab Allgemein
Hier soll die summer aller elemente nach ihmrem typ sortiert aufgeführt werden

## Heizung
- Tabelle mit Heizkreisen
  - HK name
  - Heizkreisverteiler
  - Rohrlänge gesamt
  - Rohrlänge im raum
  - Rohrlänge der zuleitung
  - Solltemperatur
  - Spreizung
  - Durchfluss
  - Leistung
  - Druckverlust
  - berechnete einstellung druckabgleich
- Tabelle Heizkreisverteiler
  - Gesamtdurchfluss
  - Durchfluss pro Heizkreis
  - gesamtleistung
- Materialliste
  - Gesamtlänge Rohr nach Querschnitt
  - Anzahl Ventile
  - Anzahl Fittings 
  - Anzahl HKV Kästen

## Elektro
- Materialliste 
  - Gesamtlänge Kabel nach typ 
  - Anzahl APs nach typ
- Raumliste -> übersicht der APs nach Raum mit Verweis auf die angeschlossnen kabel
- Kabelliste -> Übersicht der Kabel mit Name, Typ, Länge Start und End AP 




## Allgemein

- [X] ALL-1 Undo / Redo fuer bearbeitete Workflows pruefen
- [X] ALL-2 Kopieren / Einfuegen / Duplizieren pruefen
- [X] ALL-3 Loeschen inkl. Referenzen pruefen
- [X] ALL-4 Letzte Projekte / Dirty-State pruefen
- [X] ALL-5 `validate_hrp.py examples/Planung_Linda.hrp` laufen lassen (Rohdatei: ungueltig, 531 Fehler; migriert via `load_raw`: schema 0 / semantik 0)
- [X] ALL-6 Absolute `icon_path`-Pfade bewerten und bereinigen

## Reihenfolge

1. Blocker B-1 bis B-4 beheben
2. Grundriss-Workflow mit Planung_Linda pruefen
3. Properties-Editing gezielt pro Workspace pruefen
4. Danach Heizung, Elektro, Einrichtung, Vermessung, Export systematisch abarbeiten
