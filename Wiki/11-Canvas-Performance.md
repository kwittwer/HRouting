# Canvas-Performance – Umsetzungsstand

Stand: 18.09.2026. Zwei Umsetzungsschritte: Geometrie-/Interaktionsoptimierungen
und anschließend Rastercache, begrenzter Bewegungsmodus und Ereignismessung.
Der vollständige Performanceplan ist **noch nicht abgeschlossen**.

Nachtrag: gezielte Verbesserung des Auswahl-/Rechtsklickpfads, siehe unten.

## Schritt 1: Geometrie und Interaktionen

- [Benchmark](../benchmark_canvas_perf.py): `--last-project`, expliziter Projektpfad,
  `--scenario static|pan|zoom|all`, p50/p95/p99 sowie `--json-output` für neue
  Ergebnisdateien. Persönliche Einstellungen werden nur gelesen; Projektdateien
  und bestehende Ergebnisdateien werden nicht überschrieben.
- Die Messszene übernimmt die Kabelnormalisierung und AP-Darstellung aus dem
  Anwendungsladepfad, inklusive relativer/eingebetteter Bilder und Symbole.
  Persönliche Darstellungseinstellungen werden bewusst durch dokumentierte
  Canvas-Defaults ersetzt. Frühere Messungen ohne Normalisierung sind keine
  kompatible Vergleichsbasis.
- [Canvas](../gui/canvas_widget.py): Die globale Kabelsignatur wird höchstens
  einmal je Paint geprüft. Außerhalb des Paints bleibt eine vollständige
  Inhaltsprüfung erhalten, damit auch direkte Änderungen an Listen erkannt werden.
- Weltkoordinatenbasierte Überlappungsberechnungen bleiben beim Zoom erhalten;
  Kabelspurabstände werden aus dem ursprünglichen Cachemaßstab umgerechnet.
- Eine X-sortierte Kandidatensuche mit Y-Begrenzungsprüfung vermeidet unnötige
  exakte Segmentvergleiche. Die ursprüngliche Vergleichsrichtung bleibt erhalten:
  Die Projektionsprüfung ist bei fast parallelen Segmenten nicht symmetrisch.
- Hilfslinienschnittpunkte/Winkel werden nach Geometrie gecacht. Die
  zoomabhängige Endpunkttoleranz wird weiterhin bei jedem Aufruf geprüft;
  laufende Zeichenvorschauen bleiben separat aktuell.
- Heizkreisrouten prüfen den Pfadcache vor der Berechnung versetzter Linien.
- Labeländerungen aktualisieren nur die betroffene ID.
- Pan wird vor Hover, Snapping und Zeichenvorschau verarbeitet; Navigation
  verändert keine laufende Annotationsgeometrie.
- Beim Verschieben kompletter Kabel und Synchronisieren beider Endpunkte
  wird eine Route einmal statt nach jedem einzelnen Punkt zurückgeschrieben.
  Unveränderte Endpunkte erzeugen keine zusätzlichen Geometriewrites.

Die vorhandenen Änderungen im Arbeitsverzeichnis wurden erhalten. Dieser Schritt
ändert weder HRP-Dateiformat noch Heizungsberechnung, Undo-Architektur oder Exportqualität.

## Schritt 2: Rastercache und Bewegungsmodus

- [Rasterrenderer](../gui/grid_renderer.py): ein transparenter Viewport-Cache mit
  maximal **32 MiB Pixelpuffer**. Schlüssel enthalten exakten Zoom/Pan, Größe,
  DPR, Rasterabstand, Farbe/Alpha und Renderhinweise. Keine gerundete Rasterkachel,
  keine Quantisierung der Ansicht; bei zu großem Puffer oder ungeeignetem
  Painter-Zustand bleibt der direkte Zeichenpfad erhalten. Das Budget ist kein
  globales Speicherlimit für Qt, Bilder oder die Anwendung.
- Der Cache beschleunigt Wiederholungsbilder bei unveränderter Ansicht, etwa
  Hover und Zeichenvorschauen. Er ist **kein über Pan/Zoom wiederverwendbarer
  Hintergrundcache**. Verstecken und Dokumentwechsel geben ihn frei.
- Während Pan, Zoom, Ziehen und Zeichenvorschauen wird **nur das passive
  Anzeigeraster** ausgedünnt: ganzzahlige Vielfache des ursprünglichen Abstands,
  mindestens ungefähr acht logische Bildschirmpixel. Dieser Pfad zeichnet direkt.
  Snapping, aktive Geometrie, Labels, Grundrisse und gespeicherte Rastereinstellungen
  bleiben unverändert. Unter zwei Pixeln bleibt das Raster wie bisher unsichtbar.
- Nach 120 ms ohne neuen Bewegungsimpuls wird ein vollständiges Bild angefordert;
  Loslassen, Escape/Enter, Modus-/Workspacewechsel und Fokusverlust beenden den
  Bewegungsmodus ebenfalls. **120 ms sind eine Timerfrist, keine garantierte
  Fertigstellungszeit** bei ausgelastetem GUI-Thread.
- Export verwendet unabhängig vom Bewegungsmodus den vollständigen direkten
  Rasterrenderer. Verschachtelte Aufrufe und Ausnahmen stellen den Guard wieder her.
- Ein durch die neuen Messungen aufgedeckter Modellseiteneffekt wurde in
  [Document-Views](../model/views.py) behoben: lesende Zugriffe auf fehlende
  Hilfslinienmaps legen keine leeren Dokumenteinträge mehr an. Explizite Schreib-
  und `setdefault`-Zugriffe behalten ihre Writeback-Semantik.

## Kontrollierter Rendervergleich – Schritt 1

Referenz: das zuletzt geöffnete Benutzerprojekt, nicht das Beispielprojekt.
Private absolute Projektpfade und Rohdaten verbleiben außerhalb des Repositorys.

- Python 3.12.2, Windows, Qt offscreen, 1280 × 800, DPR 1.
- Drei abwechselnde Vorher-/Nachher-Durchläufe je Szenario.
- Je Durchlauf 60 gemessene Bilder nach 10 Aufwärmbildern.
- Identische normalisierte Ladeszene und bestehende Sichtbarkeitsprüfungen.
- Referenz: frühere Kabel-/Hilfslinien-/Routenberechnungsmethoden aus Git HEAD
  ausschließlich im Prozessspeicher eingesetzt; keine Dateien zurückgesetzt.
- Die 60 Bilder decken die erste Hälfte des deterministischen 120-Bilder-Zyklus ab.
  Das ist ein erster Vergleich, noch nicht die geplante vollständige Testmatrix.

| Szenario | Vorher p50 (ms) | Nachher p50 (ms) | Vorher p95 (ms) | Nachher p95 (ms) |
| --- | ---: | ---: | ---: | ---: |
| Statische Ansicht | 233,1 | 153,3 | 310,9 | 187,5 |
| Pan-Renderfolge | 293,1 | 157,3 | 466,2 | 255,6 |
| Zoom-Renderfolge | 219,3 | 120,5 | 337,3 | 168,2 |

Tabellenwerte sind **Mediane der jeweiligen drei Lauf-Perzentile**, keine
Perzentile einer zusammengefassten Stichprobe. Die Einzelmessungen schwanken
deutlich; beispielsweise reicht die Pan-p95 vorher von 343 bis 767 ms.
Eine 60-FPS-Zusage lässt sich daraus nicht ableiten. Das 16,7-ms-Ziel ist noch
nicht erreicht. Qt meldet außerdem ein fehlendes mitgeliefertes Fontverzeichnis;
Onscreen-/HiDPI-Ergebnisse müssen separat gemessen werden.

Gemessen wird die Wall-Time von `grab()` plus `processEvents()`, **nicht**
Monitorpräsentation, Eingabelatenz oder die Kosten sämtlicher Docks. Pan/Zoom
setzen deterministische Viewtransformationen; sie simulieren nicht den gesamten
Mausereignispfad. Die Ergebnisse bewerten diesen Umsetzungsschritt, nicht die
fertige interaktive Benutzeroberfläche.

## Windows-Vergleich – Schritt 2

Identisches zuletzt geöffnetes Projekt und normalisierte Ladeszene, Python 3.12.2,
Qt 6.11.0, **natives Windows-Backend**, 1280 × 800, DPR 1. Je Szenario drei
Vorher-/Nachher-Paare mit wechselnder Reihenfolge, 120 Messschritte nach zehn
Aufwärmschritten. Für „Vorher“ wurden ausschließlich die alte Rastermethode und
ein deaktivierter Bewegungsmodus im Prozess eingesetzt. Die Optimierungen aus
Schritt 1 blieben in beiden Varianten aktiv. Keine Quelldateien zurückgesetzt;
Projektdatei per SHA-256 und letzte Projektauswahl vor/nach dem Lauf unverändert.

| Szenario und Messgröße | Vorher p50 (ms) | Nachher p50 (ms) | Vorher p95 (ms) | Nachher p95 (ms) |
| --- | ---: | ---: | ---: | ---: |
| Statisch: `grab()` + Ereignisverarbeitung | 102,63 | 28,36 | 143,02 | 33,89 |
| Pan: Qt-Eingang bis nachfolgendes Paint-Ende | 99,98 | 47,97 | 135,35 | 63,47 |
| Zoom: Qt-Eingang bis nachfolgendes Paint-Ende | 82,40 | 49,36 | 238,19 | 97,97 |

Werte sind wieder **Mediane der drei Lauf-Perzentile**, nicht gepoolte Perzentile.
Nur Vorher/Nachher innerhalb einer Zeile vergleichen; statische Renderzeit und
Ereignislatenz sind verschiedene Messgrößen. Deutliche Streuung: Pan-p95 nachher
59–101 ms, Zoom-p95 nachher 66–113 ms. Die p95 der Eingabehandler selbst lag
im Median bei 0,39 ms (Pan) bzw. 0,07 ms (Zoom); der verbleibende Aufwand liegt
in diesen Canvas-Messungen überwiegend beim Zeichnen, nicht in den Handlern.

Alle geposteten Eingaben erhielten ein nachfolgendes natürliches Paint.
Das erste natürliche Bild mit voller Qualität nach dem letzten Eingang endete
bei Pan nach 25–169 ms, bei Zoom nach **225–356 ms**. Diese Qualitätsmarkierung
erfasst den Modus am Paint-Beginn, nicht eine Bildschirmaufnahme oder einen
Beweis für Monitorpräsentation. Pixel-/Exportregressionen werden separat getestet.
Zusätzlich liefen fünf neue Windows-Ereigniswiederholungen und ein Offscreen-Lauf
mit jeweils 120 Schritten. Diese unpaarigen Messungen ersetzen keinen A/B-Vergleich.

**Das 16,7-ms-/60-FPS-Ziel ist noch nicht erreicht.** Die Zahlen gelten für diesen
Rechner und dieses Referenzprojekt, nicht für sämtliche Projektgrößen, Docks oder
Maus-/Monitorlatenzen. Private Rohberichte bleiben außerhalb des Repositorys.

## Benchmark-Modi und Grenzen

[Benchmark](../benchmark_canvas_perf.py) und
[Ereignisinstrumentierung](../benchmark_event_support.py) verwenden keine
zusätzlichen Laufzeitabhängigkeiten und werden nicht von der Anwendung importiert.

| Option | Bedeutung |
| --- | --- |
| `--last-project` oder positionaler Projektpfad | Letztes Projekt nur lesen oder explizite Referenz wählen |
| `--mode grab` | Standard; statische/Pan-/Zoom-Transformationsfolge, `grab()` + `processEvents()` |
| `--mode events` | Gepostete Maus-/Wheel-Ereignisse, normale `update()`-Zusammenfassung, kein `grab()`/`repaint()` pro Schritt |
| `--platform offscreen\|windows` | Standard offscreen; Auswahl vor Erzeugung der QApplication |
| `--scenario all` | Bei grab: static/pan/zoom; bei events: pan/zoom |
| `--frames 120 --warmup 10 --repeats 3` | Vollständiger Pan-Zyklus und drei Wheel-Zyklen je Ereignislauf |
| `--grid project\|on\|off` | Rastervergleich ausschließlich im Messprozess |
| `--interval-ms 16 --idle-ms 250` | Soll-Eingabetakt und abschließende Ruhephase |
| `--deadline-ms 180000` | Abbruchfrist pro Ereignislauf einschließlich Aufwärmlauf; unterbricht keinen blockierten nativen Aufruf |
| `--json-output` | Neue Ergebnisdatei, Schema-Version 3; vorhandene Dateien werden nicht überschrieben |

JSON trennt Handlerdauer, Qt-Queue-Wartezeit, Enqueue-/Dispatch-bis-Paint-Ende,
Paint-Dauer, Qualitätsmodus und zusammengefasste Eingaben. Mehrere Eingaben können
dasselbe Paint teilen; das bedeutet nicht, dass jeder Zwischenzustand gezeichnet
wurde. Ein separat markiertes, einmaliges `idle_probe` wird erst nach den
natürlichen Eingabepaints angefordert und zählt nicht als natürliche Nachverfeinerung.
Fehlende Nachverfeinerungsmessungen werden als `null` ausgegeben, nicht geschätzt.

Der Eingabegenerator läuft im GUI-Thread: bei Überlast wird auch das Erzeugen der
Eingaben verzögert, ohne Aufhol-Bursts. Das ist **kein unabhängiger Hardware-
Eingabestrom** und misst weder Präsentations-FPS noch das komplette Hauptfenster.
HiDPI wurde funktional per Pixeltests, nicht als native Performance-Matrix gemessen.

## Tests

Neue deterministische Regressionen:

- [Benchmark/Ladeszene](../tests/test_canvas_benchmark.py): Settings-Isolation,
  CLI, Statistiken, Bilder/Symbole, appgleiche Normalisierung/Darstellung,
  Szenarien und Schutz gegen Überschreiben.
- [Renderperformance](../tests/test_canvas_render_performance.py): ein
  Signaturaufbau pro echtem Paint, Ausnahmebereinigung, Cache-Invalidierung,
  Zoomabstände/Hit-Tests, unabhängiger All-Pairs-Vergleich einschließlich
  asymmetrischer Grenzfälle, räumliche Kandidatenfilter und Routencache.
- [Hilfsliniencache](../tests/test_helper_intersection_cache.py): Pan/Zoom,
  Toleranzgrenzen, In-place-Änderungen, Wiederherstellung gleicher IDs,
  Sichtbarkeit und Live-Vorschau.
- [Interaktionen](../tests/test_canvas_interaction_performance.py): Pan am
  Ursprung, trailing timer, unveränderte Annotationsvorschau, einzelne
  Writebacks und AP-gebundene Endpunkte einschließlich Legacy-Fallback.
- [Rastercache](../tests/test_canvas_grid_cache.py): unabhängiger Legacy-Pixel-
  vergleich bei DPR 1/1,5/2, negativen Offsets, gebrochenen Abständen, Transparenz,
  Clipping, Budgetgrenzen und Cache-Invalidierung. Exakte Pixelabdeckung;
  höchstens zwei Kanalstufen Rundungsdifferenz in premultipliziertem RGBA durch
  den transparenten Zwischenpuffer, keine Bildverschiebungs-/Unschärfetoleranz.
- [Bewegungsqualität](../tests/test_canvas_quality.py): echte Qt-Ereignisse und
  Idle-Timer, Abschluss bei Release/Fokus/Moduswechsel, unverändertes Snapping
  und Dokument, Exportguard auch bei Ausnahmen.
- [Document-Views](../tests/test_document_views.py): reine Lesezugriffe auf
  fehlende Maps und veraltete Proxys, weiterhin funktionierende Schreibzugriffe.
- Ereignisbenchmark: Eingabekorrelation, zusammengefasste Paints, Deadlines,
  Fehlerbereinigung, Plattformwahl, Qualitätsstatistik, kurze Aufwärm-Ruhephasen
  und unveränderte Quelldateien/Einstellungen.

Schritt 1: **180 Tests bestanden**. Abschlusslauf von Schritt 2:
**454 Tests bestanden**, einschließlich bestehender Document-Views-, Canvas-,
Text-Safety-, E6-, visueller und GUI-Interaktionstests. Der erste breite Lauf
überschritt beim Laden des großen Beispielprojekts das 30-Sekunden-Testlimit;
der abschließende Lauf verwendet 120 Sekunden pro Test, ohne Testauslassungen
oder geänderte Assertions.
Anwendungseinstellungen waren isoliert; die persönliche letzte Projektauswahl
blieb unverändert. Keine Editor-Diagnosen in den bearbeiteten Dateien.

Die vollständige Suite ist **nicht als grün bestätigt**. Im vorherigen Schritt
traten ohne Hauptfenstertests zwölf
Fehler in KiCad-Importdaten, Kabelnamens-/Eigenschaftserwartungen und
Kabellängen-Roundtrip auf. Diese zwölf Fehler sowie der separat isolierte
PDF-Kabelnamenfehler reproduzieren sich auch mit der alten Canvas-Version.
Der vollständige Lauf brach zusätzlich beim Auflösen eines Iconpfads in einem
Hauptfenstertest durch Timeout ab. Diese Probleme wurden nicht durch Änderungen
an fachfremden Bereichen kaschiert. Die komplette Suite wurde in Schritt 2 nicht
erneut ausgeführt.

## Nachtrag: Verzögerung vor dem Kontextmenü

Die Messung des tatsächlichen Hauptfenster-Eingabepfads zeigte einen zusätzlichen
Engpass außerhalb des Paints: Beim Tooltip-Hit-Test und linken Auswahlklick
wurde die globale Kabelsignatur für jedes geprüfte Kabel erneut aufgebaut.
In einer Stichprobe des Referenzprojekts mit 96 Kabeln waren es **93 Aufbauten
pro Suchdurchlauf**. Der profilierte Hover dauerte dort etwa 425 ms, der linke
Auswahlklick 430 ms. Der direkte Rechtsklickpfad selbst war deutlich kürzer;
eine mehrsekündige Menüverzögerung wurde in dieser Messumgebung nicht vollständig
reproduziert. Vorausgehende Eingaben können den Rechtsklick jedoch aufhalten.

Umgesetzt:

- [Canvas](../gui/canvas_widget.py): globale Spurprüfung nur einmal innerhalb
  eines synchronen, schreibfreien Kabel-Hit-Test-Durchlaufs. Kein über Eingaben
  hinweg als gültig angenommener Cache: direkte Geometrieänderungen werden beim
  nächsten Suchlauf weiterhin geprüft. Trefferreihenfolge, versetzte Kabelspuren,
  Anschlussbögen, Sichtbarkeit und Workspacefilter bleiben erhalten.
- Unveränderte Klicks auf Kabelkanten/-punkte erzeugen beim Loslassen keine
  Kabeländerungsmeldung mehr. Dadurch entfallen Änderungs-/Undo-/Folgeupdates
  für bloße Auswahl. Wirkliche Drags und Endpunktbindungen bleiben aktiv.
- [Auswahlfelder](../gui/properties/field_widgets.py): identische Eintragslisten
  werden nicht mehr gelöscht und neu befüllt. Verglichen werden die tatsächlichen
  Werte **und** Labels; geänderte Anschlüsse und veraltete Zusatzwerte bleiben
  korrekt behandelt. Verschachtelte Signalunterdrückung bleibt erhalten.

Native Nachmessung mit **echtem QMenu im Hauptfenster**, Windows, letztes Projekt:
neun Abläufe über drei Kabelpositionen, jeweils Hover → Linksklick → Loslassen →
Rechtsklick. Der Zeitraum vom Eintritt in den Rechtsklickhandler bis zum Ende des
ersten Menü-Paints lag bei **37–92 ms, Median 57 ms**. Das Menü schloss danach
automatisch. Das ist kein gemessener Hardware-/Monitor-Present-Zeitpunkt und
keine Vorher-/Nachher-Garantie für sämtliche Projekte. Im separaten profilierten
Nachlauf lagen die zuvor genannten Hover-/Auswahlstichproben bei etwa 48/93 ms;
diese Einzelwerte unterliegen Last- und Cache-Schwankungen.

Validierung: **516 Canvas-/Interaktionstests + 53 Eigenschaftentests + 15 passende
Hauptfenstertests bestanden**. Enthalten sind
[Kontext-/Eingaberegressionen](../tests/test_canvas_context_performance.py) und
[Auswahllisten-Regressionen](../tests/test_choice_refresh_performance.py).
Der bekannte unabhängige Kabelnamen-Roundtrip-Test blieb ausgeschlossen;
die übrigen Hauptfenstertests wurden in diesem Zusatzlauf nicht ausgeführt.
Anwendungseinstellungen waren isoliert, die Referenzdatei blieb unverändert.

## Nächste Schritte

1. Eigenschaften-/Schema-/Dockaktualisierungen bündeln und im vollständigen
  Hauptfenster messen; der große Ladetest zeigt weiterhin wiederholte Topologiearbeit.
2. Wiederholte Punktkonvertierungen/Bounds reduzieren und den AP-Kabel-Reverse-
  Index vollständig absichern; weitere Geometrieoptimierungen profilgestützt wählen.
3. Speicherbudgetierte Grundriss-/Symbolcaches und Hintergrund-LOD prüfen,
  danach echte Mehrschicht-Komposition und Dirty-Regions.
4. Messmatrix um große synthetische Szenen, natives HiDPI, Zeichnen sowie Einzel-/
  Mehrfachdrag mit Hauptfenster erweitern. Aktuelle Ereignisläufe messen Pan/Zoom.

Ein einfacher `drawLines`-Batch und ein Versuch ohne Raster-Antialiasing zeigten
keinen belastbaren Vorteil und wurden nicht übernommen. Globale Cachebudgets,
Grundriss-LOD, vollständige Layer-/Dock-Optimierung und der Reverse-Index sind
**noch nicht umgesetzt**; der neue Pixmap-Cache umfasst ausschließlich das Raster.
