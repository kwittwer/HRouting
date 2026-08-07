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
* [ ] A-4 grundsätzlich soll für alle elemente der Name anstatt der nummer angezeigt werden also z.B. Ankleide anstatt ER-13 (siehe Planung-Linda.hrp) Das gilt für alle elemente
* [ ] es soll persistent sein was in der navigation auf bzw zugefaltet ist. bitte füge einen butteon um alle zusammen zu falten ein
* [ ] 

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
- [ ] H-6 Berechnungen-Dock gegen Planung_Linda pruefen

## Elektro

- [X] E-1 Anschlusspunkt hinzufuegen / platzieren pruefen
- [X] E-2 Eigenschaften von APs editieren pruefen
- [X] E-3 UV-Dialog pruefen
- [X] E-4 UP-Distribution pruefen
- [X] E-5 Elektro-Raum zeichnen / bearbeiten pruefen
- [X] E-6 Kabel zeichnen / bearbeiten pruefen
  - [ ] E-6.1 ich kann keine neuen kabel zichen
- [X] E-7 Strangschema gegen Planung_Linda pruefen
- [X] E-8 BOM / Exportdaten gegen Planung_Linda pruefen
- [ ] E-9 in den APs werden keine Bilder angezeigt
- [ ] E-10 aktuell werden alle APs blau dargetsellt obwohl in den eingeschaften etwas anderes eingestellt ist. bite fixen
- [ ]



## Einrichtung

- [X] F-1 Einrichtung hinzufuegen Workflow pruefen
- [X] F-2 Einrichtungs-Polygon zeichnen pruefen
- [X] F-3 Einrichtung verschieben / rotieren pruefen
- [X] F-4 Parent-Floorplan und feste Groessen pruefen
- [ ] F-5 wenn ich im einrichtungs Workspace pin mmöchte ich die elemente direkt per drag and drop verschieben können

## Vermessung

- [X] A-1 Distanzmessung pruefen
  - [ ] A-1.1 maß wird nicht richtig angezeigt. -> mass soll schon beim zeiehn des zweiten messpunkte angezeigt werden
- [X] A-2 Winkelmessung pruefen
- [X] A-3 Hilfslinien zeichnen / bearbeiten pruefen
- [X] A-4 Text platzieren / editieren pruefen

  - [ ] funktioniert überhaupt nicht

## Export

- [X] X-1 Exportrahmen zeichnen pruefen
- [X] X-2 PDF-Export mit Planung_Linda pruefen
- [ ] beim PDF export verschwindest die Anzeige des planes.
- [ ] das exportierte pdf enthält nur eien screenshot. Es soll der export aber wider so funktionieren wie vor dem gui umbau.
- [X] X-3 SVG / KiCad / QElectroTech Export pruefen
- [ ] bitte kicad und qelektrotech export entfernen
- [ ] die ansicht der stückliste ist unvollständig, bitte orienteire dich auch hiuer an der alten implementierung

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
