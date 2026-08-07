# HRouting – Feature ToDo

Stand: 2026-08-07
Basis: Code-Analyse der neuen UI und Testprojekt `examples/Planung_Linda.hrp`

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

## Grundriss

- [ ] G-1 Grundriss hinzufuegen und Bild laden pruefen
- [ ] G-2 Grundriss verschieben pruefen
- [ ] G-3 Grundriss drehen pruefen
- [ ] G-4 Referenzlinie zeichnen pruefen
- [X] G-5 Referenzlaenge nur ueber `Aktualisieren` neu berechnen
- [ ] G-6 Grundriss-Umriss entfernen
- [ ] G-8 Opacity / Offset / Rotation im Properties-Dock pruefen
- [ ] G-9 Referenzlinie sichtbar / unsichtbar pruefen
- [ ] G-10 Hilfslinien je Grundriss pruefen

## Heizung

- [ ] H-1 Heizkreis hinzufuegen pruefen
- [ ] H-2 Heizkreis-Polygon zeichnen / bearbeiten pruefen
- [ ] H-3 Route zeichnen / bearbeiten pruefen
- [ ] H-4 Versorgungsleitung zeichnen / bearbeiten pruefen
- [ ] H-5 HKV platzieren und Leitungen pruefen
- [ ] H-6 Berechnungen-Dock gegen Planung_Linda pruefen

## Elektro

- [ ] E-1 Anschlusspunkt hinzufuegen / platzieren pruefen
- [ ] E-2 Eigenschaften von APs editieren pruefen
- [ ] E-3 UV-Dialog pruefen
- [ ] E-4 UP-Distribution pruefen
- [ ] E-5 Elektro-Raum zeichnen / bearbeiten pruefen
- [ ] E-6 Kabel zeichnen / bearbeiten pruefen
- [ ] E-7 Strangschema gegen Planung_Linda pruefen
- [ ] E-8 BOM / Exportdaten gegen Planung_Linda pruefen


## Einrichtung

- [ ] F-1 Einrichtung hinzufuegen Workflow pruefen
- [ ] F-2 Einrichtungs-Polygon zeichnen pruefen
- [ ] F-3 Einrichtung verschieben / rotieren pruefen
- [ ] F-4 Parent-Floorplan und feste Groessen pruefen

## Vermessung

- [ ] A-1 Distanzmessung pruefen
- [ ] A-2 Winkelmessung pruefen
- [ ] A-3 Hilfslinien zeichnen / bearbeiten pruefen
- [ ] A-4 Text platzieren / editieren pruefen

## Export

- [ ] X-1 Exportrahmen zeichnen pruefen
- [ ] X-2 PDF-Export mit Planung_Linda pruefen
- [ ] X-3 SVG / KiCad / QElectroTech Export pruefen

## Allgemein

- [ ] ALL-1 Undo / Redo fuer bearbeitete Workflows pruefen
- [ ] ALL-2 Kopieren / Einfuegen / Duplizieren pruefen
- [ ] ALL-3 Loeschen inkl. Referenzen pruefen
- [ ] ALL-4 Letzte Projekte / Dirty-State pruefen
- [ ] ALL-5 `validate_hrp.py examples/Planung_Linda.hrp` laufen lassen
- [ ] ALL-6 Absolute `icon_path`-Pfade bewerten und bereinigen

## Reihenfolge

1. Blocker B-1 bis B-4 beheben
2. Grundriss-Workflow mit Planung_Linda pruefen
3. Properties-Editing gezielt pro Workspace pruefen
4. Danach Heizung, Elektro, Einrichtung, Vermessung, Export systematisch abarbeiten
