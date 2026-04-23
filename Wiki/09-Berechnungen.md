# Berechnungen und Formeln

In HRouting werden alle heizungstechnischen Kennwerte **automatisch** berechnet, sobald ein Heizkreis mit Polygon, Rohrverlauf und den relevanten Parametern angelegt ist. Dieses Kapitel erklärt die zugrunde liegenden Formeln und wie die Eingabewerte zusammenhängen.

---

## Übersicht – Datenfluss

```
Eingaben (pro Heizkreis)         Globale Eingaben
├─ Raumpolygon → Fläche A        ├─ Vorlauftemperatur T_V
├─ Rohrverlauf → Rohrlänge L_R   └─ Rücklauftemperatur T_R
├─ Zuleitung → Zuleitungslänge L_Z
├─ Verlegeabstand VA
├─ Rohrdurchmesser d_a
├─ Fußbodenbelag → R_λ,B
└─ Soll-Raumtemperatur T_Raum
          │
          ▼
  ┌─────────────────────────┐
  │  1. Übertemperatur ΔT_H │
  │  2. K_H-Wert            │
  │  3. Spez. Leistung q    │
  │  4. Heizleistung Q      │
  │  5. Volumenstrom V̇      │
  │  6. Druckverlust Δp     │
  │  7. Hydraul. Abgleich   │
  └─────────────────────────┘
```

---

## 1. Eingabeparameter

### Globale Parameter (Heizung Allgemein)

| Parameter | Symbol | Einheit | Standard | Bereich |
|-----------|--------|---------|----------|---------|
| Vorlauftemperatur | T_V | °C | 35,0 | 20–90 |
| Rücklauftemperatur | T_R | °C | 30,0 | 15–80 |
| Normaußentemperatur | T_außen | °C | −12,0 | −30–5 |

Diese Werte gelten für **alle** Heizkreise gemeinsam und werden in der Seitenleiste unter „🌡 Heizung Allgemein" eingestellt.

### Parameter pro Heizkreis

| Parameter | Symbol | Einheit | Standard | Bereich |
|-----------|--------|---------|----------|---------|
| Verlegeabstand | VA | cm | 15 | 5–30 |
| Rohrdurchmesser (außen) | d_a | cm | 1,6 | 1,0–3,2 |
| Randabstand | — | cm | 20 | 0–50 |
| Soll-Raumtemperatur | T_Raum | °C | 20,0 | 10–35 |
| Fußbodenbelag | — | — | Fliesen | Dropdown |

### Geometrische Werte (aus Zeichnung ermittelt)

| Wert | Symbol | Herkunft |
|------|--------|----------|
| Raumfläche | A | Fläche des Raumpolygons (Gauß'sche Trapezformel) |
| Rohrlänge | L_R | Summe aller Segmente des Rohrverlaufs |
| Zuleitungslänge | L_Z | Länge der Zuleitung zum HKV |
| Gesamtrohrlänge | L_ges | L_R + L_Z |

---

## 2. Fußbodenbeläge – Wärmeleitwiderstand

Der Wärmeleitwiderstand R_λ,B des Bodenbelags beeinflusst, wie gut die Wärme vom Heizrohr zur Raumluft gelangt. Je höher R_λ,B, desto schlechter die Wärmeleitung und desto geringer die Heizleistung.

| Bodenbelag | R_λ,B (m²·K/W) |
|------------|----------------|
| Estrich (kein Belag) | 0,00 |
| Fliesen / Keramik | 0,01 |
| Naturstein | 0,02 |
| PVC / Vinyl | 0,02 |
| Laminat | 0,05 |
| Parkett dünn (≤ 10 mm) | 0,05 |
| Parkett dick (> 10 mm) | 0,10 |
| Teppich dünn | 0,10 |
| Teppich dick | 0,15 |

**Zusammenhang:** Fliesen leiten Wärme am besten (niedriger R_λ,B), Teppich am schlechtesten (hoher R_λ,B). Bei Teppichboden muss der Verlegeabstand enger gewählt oder die Vorlauftemperatur erhöht werden, um dieselbe Heizleistung zu erreichen.

---

## 3. Logarithmische Übertemperatur ΔT_H

Die Übertemperatur beschreibt den mittleren Temperaturunterschied zwischen Heizwasser und Raum. Sie wird nach DIN EN 1264 als **logarithmischer Mittelwert** berechnet:

```
             T_V − T_R
ΔT_H = ──────────────────────
         ln( (T_V − T_Raum) / (T_R − T_Raum) )
```

**Sonderfall:** Wenn T_V ≈ T_R (Differenz < 0,01 K), wird stattdessen das arithmetische Mittel verwendet:

```
ΔT_H = ( (T_V − T_Raum) + (T_R − T_Raum) ) / 2
```

### Beispiel

Mit T_V = 35 °C, T_R = 30 °C, T_Raum = 20 °C:

```
ΔT_H = (35 − 30) / ln( (35 − 20) / (30 − 20) )
     = 5 / ln(1,5)
     = 5 / 0,4055
     ≈ 12,33 K
```

---

## 4. Wärmedurchgangszahl K_H

Die K_H-Zahl beschreibt, wie effizient das Heizsystem Wärme an den Raum abgibt. Sie hängt vom **Verlegeabstand** und vom **Bodenbelag** ab.

### Basiswerte nach DIN EN 1264

Die K_H-Basiswerte gelten für Zementestrich mit 16 mm PE-X-Rohr:

| Verlegeabstand (cm) | K_H,0 (W/(m²·K^n)) |
|---------------------|---------------------|
| 5 | 5,80 |
| 10 | 4,20 |
| 15 | 3,20 |
| 20 | 2,60 |
| 25 | 2,20 |
| 30 | 1,90 |

Für Zwischenwerte wird **linear interpoliert**.

### Belagskorrektur

Der Bodenbelag reduziert K_H proportional zu seinem Wärmeleitwiderstand:

```
K_H = K_H,0 / (1 + R_λ,B / 0,10)
```

Dabei ist 0,10 m²·K/W der Referenz-Wärmeleitwiderstand.

### Beispiel

Verlegeabstand 15 cm, Fliesen (R_λ,B = 0,01):

```
K_H = 3,20 / (1 + 0,01 / 0,10)
    = 3,20 / 1,10
    ≈ 2,91 W/(m²·K^n)
```

Bei Teppich dick (R_λ,B = 0,15):

```
K_H = 3,20 / (1 + 0,15 / 0,10)
    = 3,20 / 2,50
    = 1,28 W/(m²·K^n)    ← deutlich schlechter
```

---

## 5. Spezifische Heizleistung q

Die spezifische Heizleistung in W/m² ergibt sich aus der DIN EN 1264-Leistungsgleichung:

```
q = K_H · ΔT_H^n
```

Dabei ist **n = 1,1** der Exponent der Heizleistungskennlinie (DIN EN 1264).

### Beispiel

Mit K_H ≈ 2,91 und ΔT_H ≈ 12,33 K:

```
q = 2,91 · 12,33^1,1
  = 2,91 · 14,59
  ≈ 42,5 W/m²
```

---

## 6. Heizleistung Q

Die Gesamtheizleistung des Heizkreises ergibt sich durch Multiplikation mit der Raumfläche:

```
Q = q · A
```

### Beispiel

Raumfläche A = 18 m²:

```
Q = 42,5 · 18 = 765 W
```

---

## 7. Volumenstrom V̇

Der benötigte Volumenstrom ergibt sich aus der Heizleistung und der Temperaturspreizung:

```
V̇ = Q / (c_w · ρ_w · (T_V − T_R))
```

| Konstante | Wert | Einheit | Beschreibung |
|-----------|------|---------|-------------|
| c_w | 4182 | J/(kg·K) | Spezifische Wärmekapazität Wasser (~35 °C) |
| ρ_w | 994 | kg/m³ | Dichte Wasser (~35 °C) |

Das Ergebnis wird von m³/s in **l/min** umgerechnet (× 1000 × 60).

### Beispiel

```
V̇ = 765 / (4182 · 994 · 5)
   = 765 / 20 784 540
   = 3,68 · 10⁻⁵ m³/s
   = 3,68 · 10⁻⁵ · 1000 · 60
   ≈ 2,21 l/min
```

---

## 8. Druckverlust Δp

Der Druckverlust wird für die **Gesamtrohrlänge** (Rohrverlauf + Zuleitung) nach dem **Darcy-Weisbach-Verfahren** berechnet.

### Innendurchmesser

```
d_i = (d_a − 2 · s) / 1000
```

Dabei ist s = 2,0 mm die Standard-Rohrwandstärke (PE-X / PE-RT).

### Strömungsgeschwindigkeit

```
A_rohr = π · (d_i / 2)²
v = V̇ / A_rohr
```

### Reynolds-Zahl

```
Re = v · d_i / ν_w
```

| Konstante | Wert | Einheit | Beschreibung |
|-----------|------|---------|-------------|
| ν_w | 0,73 · 10⁻⁶ | m²/s | Kinematische Viskosität Wasser (~35 °C) |

### Rohrreibungszahl λ

Die Rohrreibungszahl wird in Abhängigkeit der Strömungsform bestimmt:

| Strömung | Bedingung | Formel |
|----------|-----------|--------|
| Laminar | Re < 2320 | λ = 64 / Re |
| Turbulent | Re ≥ 2320 | λ = 0,3164 / Re^0,25 (Blasius) |

### Gesamtdruckverlust

```
R = λ / d_i · ρ_w · v² / 2       [Pa/m]  (Druckverlust pro Meter)

Δp = R · L_ges / 100              [mbar]  (1 mbar = 100 Pa)
```

### Beispiel

d_a = 16 mm → d_i = 12 mm = 0,012 m, L_ges = 95 m, V̇ = 2,21 l/min:

```
A_rohr = π · 0,006² = 1,131 · 10⁻⁴ m²
v = (2,21 / 60000) / 1,131 · 10⁻⁴ = 0,326 m/s
Re = 0,326 · 0,012 / 0,73 · 10⁻⁶ = 5356 → turbulent
λ = 0,3164 / 5356^0,25 = 0,0370
R = 0,0370 / 0,012 · 994 · 0,326² / 2 = 163,1 Pa/m
Δp = 163,1 · 95 / 100 = 154,9 mbar
```

---

## 9. Hydraulischer Abgleich

Damit alle Heizkreise gleichmäßig durchströmt werden, muss der Druckverlust in jedem Kreis gleich groß sein. Da die Rohrlängen und Volumenströme unterschiedlich sind, gleicht man den Druck über **Ventile** an den Heizkreisverteilern aus.

### Prinzip

1. Es wird angenommen, dass **eine gemeinsame Pumpe** alle Heizkreise versorgt.
2. Der **maximale Druckverlust** Δp_max über alle Kreise wird ermittelt.
3. Für jeden Kreis wird berechnet, welcher Restdruck über das Ventil abgebaut werden muss:

```
Δp_Ventil = Δp_max − Δp_Rohr
```

- Der Heizkreis mit dem höchsten Druckverlust hat Δp_Ventil = 0 (Ventil ganz offen).
- Alle anderen Kreise haben Δp_Ventil > 0 (Ventil teilweise geschlossen).

### Kv-Wert

Der Kv-Wert beschreibt den Durchfluss eines Ventils bei 1 bar Druckdifferenz. Er wird berechnet als:

```
Kv = V̇_m³/h / √(Δp_Ventil_bar)
```

Umrechnungen:
- V̇ [l/min] → V̇ [m³/h]: × 0,06
- Δp [mbar] → Δp [bar]: ÷ 1000

### Beispiel

Drei Heizkreise mit unterschiedlichen Druckverlusten:

| Kreis | Δp_Rohr (mbar) | V̇ (l/min) | Δp_Ventil (mbar) | Kv (m³/h) |
|-------|----------------|-----------|-------------------|-----------|
| HK-1 | 155 | 2,21 | 0 | — (offen) |
| HK-2 | 98 | 1,50 | 57 | 0,38 |
| HK-3 | 120 | 1,80 | 35 | 0,58 |

HK-1 hat den höchsten Druckverlust → Referenz. Die anderen Kreise werden über den berechneten Kv-Wert am Ventil eingestellt.

---

## 10. Zusammenhang der Werte

Die folgende Übersicht zeigt, welche Eingabe welche Ergebnisse beeinflusst:

| Änderung | Auswirkung |
|----------|-----------|
| **Vorlauftemperatur ↑** | ΔT_H ↑ → q ↑ → Q ↑ → V̇ ↑ → Δp ↑ |
| **Rücklauftemperatur ↑** | ΔT_H ↓ → q ↓ → Q ↓, aber Spreizung ↓ → V̇ ↑ |
| **Raumtemperatur ↑** | ΔT_H ↓ → q ↓ → Q ↓ → V̇ ↓ → Δp ↓ |
| **Verlegeabstand ↑** | K_H ↓ → q ↓ → Q ↓ → V̇ ↓ → Δp ↓ |
| **Fläche ↑** | Q ↑ → V̇ ↑ → Δp ↑ (q bleibt gleich) |
| **Rohrlänge ↑** | Δp ↑ (Q, V̇ unverändert) |
| **Rohrdurchmesser ↑** | d_i ↑ → v ↓ → Δp ↓ |
| **R_λ,B ↑ (z.B. Teppich)** | K_H ↓ → q ↓ → Q ↓ → V̇ ↓ |

### Typische Auslegungsziele

- **Spez. Leistung q** für Wohnräume: 40–80 W/m²
- **Oberflächentemperatur**: max. 29 °C in Aufenthaltszonen (DIN EN 1264)
- **Volumenstrom**: typisch 1–3 l/min pro Heizkreis
- **Druckverlust pro Kreis**: Zielwert < 250 mbar
- **Temperaturspreizung** (T_V − T_R): üblich 5–10 K

---

## Normengrundlage

Die Berechnungen basieren vereinfacht auf:

- **DIN EN 1264** – Raumflächenintegrierte Heiz- und Kühlsysteme
  - Teil 2: Fußbodenheizung – Nachweis der Eignung
  - Teil 3: Auslegung
  - Teil 5: Heiz- und Kühlflächen in Boden, Decke und Wänden
- **Darcy-Weisbach-Gleichung** – Druckverlustberechnung in Rohren
- **Blasius-Gleichung** – Rohrreibungszahl für glatte Rohre (Re < 100 000)

> **Hinweis:** Die Berechnungen in HRouting dienen der Planung und Übersicht. Für die endgültige Auslegung (z.B. Pumpenauswahl, Fußbodenaufbau) sollte eine fachgerechte Berechnung durch einen Heizungsingenieur erfolgen.
