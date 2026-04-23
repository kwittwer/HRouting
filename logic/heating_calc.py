# HRouting – Fußbodenheizung und Kabel Planer
# Copyright (C) 2026 Konrad-Fabian Wittwer
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
Berechnungsmodul für Fußbodenheizung.

Berechnet Heizleistung, Volumenstrom und Druckverlust
basierend auf DIN EN 1264 (vereinfacht).
"""

import math

# ── Fußbodenbeläge mit Wärmeleitwiderstand R_λ,B (m²·K/W) ──

FLOOR_COVERINGS: dict[str, float] = {
    "Estrich (kein Belag)":     0.00,
    "Fliesen / Keramik":        0.01,
    "Naturstein":               0.02,
    "PVC / Vinyl":              0.02,
    "Laminat":                  0.05,
    "Parkett dünn (≤ 10 mm)":   0.05,
    "Parkett dick (> 10 mm)":   0.10,
    "Teppich dünn":             0.10,
    "Teppich dick":             0.15,
}

# ── K_H-Basiswerte nach DIN EN 1264 Tabelle (Zementestrich, 16 mm Rohr) ──
# Verlegeabstand in cm → K_H [W/(m²·K^n)]
# Diese Werte entsprechen der vereinfachten Leistungsgleichung mit Exponent.
_KH_TABLE: list[tuple[float, float]] = [
    ( 5.0, 5.80),
    (10.0, 4.20),
    (15.0, 3.20),
    (20.0, 2.60),
    (25.0, 2.20),
    (30.0, 1.90),
]

# DIN EN 1264 Exponent für die Heizleistungskennlinie
_EXPONENT_N = 1.1

# Wasserkennwerte bei ca. 35 °C
_C_W   = 4182.0    # spezifische Wärmekapazität [J/(kg·K)]
_RHO_W = 994.0     # Dichte [kg/m³]
_NU_W  = 0.73e-6   # kinematische Viskosität [m²/s]
_WALL_THICKNESS_MM = 2.0   # Standard-Rohrwand PE-X / PE-RT


def _interp_kh_base(spacing_cm: float) -> float:
    """Lineare Interpolation des K_H-Basiswerts aus der Tabelle."""
    if spacing_cm <= _KH_TABLE[0][0]:
        return _KH_TABLE[0][1]
    if spacing_cm >= _KH_TABLE[-1][0]:
        return _KH_TABLE[-1][1]
    for i in range(len(_KH_TABLE) - 1):
        s1, k1 = _KH_TABLE[i]
        s2, k2 = _KH_TABLE[i + 1]
        if s1 <= spacing_cm <= s2:
            t = (spacing_cm - s1) / (s2 - s1)
            return k1 + t * (k2 - k1)
    return _KH_TABLE[-1][1]


def _log_mean_temp_diff(t_supply: float, t_return: float, t_room: float) -> float:
    """
    Logarithmische Übertemperatur ΔT_H nach DIN EN 1264.

    ΔT_H = (T_V - T_R) / ln((T_V - T_Raum) / (T_R - T_Raum))

    Falls T_V ≈ T_R, wird die arithmetische Mitteltemperatur verwendet.
    """
    dt_supply = t_supply - t_room
    dt_return = t_return - t_room
    if dt_supply <= 0 or dt_return <= 0:
        return 0.0
    if abs(dt_supply - dt_return) < 0.01:
        return (dt_supply + dt_return) / 2.0
    return (dt_supply - dt_return) / math.log(dt_supply / dt_return)


def get_kh(spacing_cm: float, r_lambda_b: float) -> float:
    """
    Wärmedurchgangszahl K_H [W/(m²·K^n)] mit Belagskorrektur.

    Die Belagskorrektur nach DIN EN 1264-2 reduziert K_H bei höherem
    Wärmeleitwiderstand des Bodenbelags.
    """
    kh_base = _interp_kh_base(spacing_cm)
    # Belagskorrektur: K_H sinkt ca. 3–4 % pro 0.01 m²·K/W
    # Vereinfachte Formel: K_H = K_H,0 / (1 + R_λ,B / R_λ,0)
    # mit R_λ,0 ≈ 0.10 m²·K/W als Referenz
    return kh_base / (1.0 + r_lambda_b / 0.10)


def calc_specific_heat_output(
    t_supply: float,
    t_return: float,
    t_room: float,
    spacing_cm: float,
    r_lambda_b: float,
) -> float:
    """
    Spezifische Heizleistung q [W/m²] nach DIN EN 1264.

    q = K_H · ΔT_H^n

    Parameters
    ----------
    t_supply : Vorlauftemperatur [°C]
    t_return : Rücklauftemperatur [°C]
    t_room : Soll-Raumtemperatur [°C]
    spacing_cm : Verlegeabstand [cm]
    r_lambda_b : Wärmeleitwiderstand des Fußbodenbelags [m²·K/W]
    """
    delta_t_h = _log_mean_temp_diff(t_supply, t_return, t_room)
    if delta_t_h <= 0:
        return 0.0
    kh = get_kh(spacing_cm, r_lambda_b)
    return kh * (delta_t_h ** _EXPONENT_N)


def calc_heating_power(
    t_supply: float,
    t_return: float,
    t_room: float,
    spacing_cm: float,
    r_lambda_b: float,
    area_m2: float,
) -> float:
    """Heizleistung Q [W]."""
    q = calc_specific_heat_output(t_supply, t_return, t_room, spacing_cm, r_lambda_b)
    return q * area_m2


def calc_volume_flow(
    power_w: float,
    t_supply: float,
    t_return: float,
) -> float:
    """
    Volumenstrom [l/min].

    V̇ = Q / (c_w · ρ · ΔT)   →  Umrechnung in l/min
    """
    delta_t = t_supply - t_return
    if delta_t <= 0 or power_w <= 0:
        return 0.0
    # m³/s
    v_dot_m3s = power_w / (_C_W * _RHO_W * delta_t)
    # → l/min  (1 m³ = 1000 l, 1 min = 60 s)
    return v_dot_m3s * 1000.0 * 60.0


def calc_pressure_drop(
    volume_flow_lmin: float,
    pipe_length_m: float,
    outer_diameter_mm: float,
) -> float:
    """
    Druckverlust [mbar] für PE-X-Rohr (vereinfacht nach Darcy-Weisbach).

    Parameters
    ----------
    volume_flow_lmin : Volumenstrom [l/min]
    pipe_length_m : Gesamte Rohrlänge [m] (inkl. Zuleitung)
    outer_diameter_mm : Rohraußendurchmesser [mm]
    """
    if volume_flow_lmin <= 0 or pipe_length_m <= 0:
        return 0.0

    d_inner_m = (outer_diameter_mm - 2.0 * _WALL_THICKNESS_MM) / 1000.0
    if d_inner_m <= 0:
        return 0.0

    area = math.pi * (d_inner_m / 2.0) ** 2
    # l/min → m³/s
    v_dot = volume_flow_lmin / (1000.0 * 60.0)
    velocity = v_dot / area  # m/s

    # Reynolds-Zahl
    re = velocity * d_inner_m / _NU_W
    if re < 1:
        return 0.0

    # Rohrreibungszahl (Blasius für glatte Rohre)
    if re < 2320:
        # laminar
        lam = 64.0 / re
    else:
        # turbulent (Blasius, gültig bis Re ≈ 100 000)
        lam = 0.3164 / (re ** 0.25)

    # Druckverlust pro Meter [Pa/m]
    r_pa_m = lam / d_inner_m * _RHO_W * velocity ** 2 / 2.0

    # Gesamtdruckverlust [Pa] → [mbar]  (1 mbar = 100 Pa)
    dp_pa = r_pa_m * pipe_length_m
    return dp_pa / 100.0


def calc_circuit(
    t_supply: float,
    t_return: float,
    t_room: float,
    spacing_cm: float,
    r_lambda_b: float,
    area_m2: float,
    pipe_length_m: float,
    outer_diameter_mm: float,
    total_pipe_length_m: float | None = None,
) -> dict:
    """
    Berechnet alle Kennwerte eines Heizkreises.

    Parameters
    ----------
    pipe_length_m : Rohrlänge im Heizkreis (ohne Zuleitung)
    total_pipe_length_m : Gesamtrohrlänge inkl. Zuleitung (für Druckverlust).
                          Falls None, wird pipe_length_m verwendet.

    Returns
    -------
    dict mit Schlüsseln:
        power_w         – Heizleistung [W] (basierend auf Fläche)
        q_wm2           – Spezifische Heizleistung [W/m²]
        volume_flow_lmin – Volumenstrom [l/min]
        pressure_drop_mbar – Druckverlust [mbar] (über gesamte Rohrlänge)
    """
    q = calc_specific_heat_output(t_supply, t_return, t_room, spacing_cm, r_lambda_b)
    power = q * area_m2
    vf = calc_volume_flow(power, t_supply, t_return)
    dp_length = total_pipe_length_m if total_pipe_length_m is not None else pipe_length_m
    dp = calc_pressure_drop(vf, dp_length, outer_diameter_mm)
    return {
        "power_w": power,
        "q_wm2": q,
        "volume_flow_lmin": vf,
        "pressure_drop_mbar": dp,
    }


# ── Hydraulischer Abgleich ──────────────────────────────────────── #

def calc_balancing(circuits: list[dict]) -> list[dict]:
    """
    Berechnet Einstellwerte für den hydraulischen Abgleich.

    Es wird angenommen, dass **eine gemeinsame Pumpe** alle Heizkreis-
    verteiler speist.  Referenz-Druckverlust ist daher der **globale**
    Maximalwert über alle Kreise – nicht nur pro Verteiler.

    Für jeden Heizkreis wird bestimmt:
      - dp_valve_mbar : Restdruckverlust, der über das Ventil abgebaut werden muss
      - kv_value      : Kv-Wert (l/h bei 1 bar = 100 000 mbar) des Ventils

    Parameters
    ----------
    circuits : list of dicts, jeweils mit Schlüsseln:
        volume_flow_lmin, pressure_drop_mbar, distributor (optional)

    Returns
    -------
    list of dicts mit zusätzlichen Schlüsseln:
        dp_max_mbar    – max. Druckverlust im Gesamtsystem (alle HKV)
        dp_valve_mbar  – erforderlicher Ventil-Druckverlust
        kv_value       – Kv-Wert [m³/h]
    """
    if not circuits:
        return circuits

    # Gemeinsame Pumpe → globaler max. Druckverlust über ALLE Kreise
    global_max = max(
        (c.get("pressure_drop_mbar", 0.0) for c in circuits), default=0.0
    )

    result = []
    for c in circuits:
        dp_pipe = c.get("pressure_drop_mbar", 0.0)
        dp_valve = max(0.0, global_max - dp_pipe)
        vf = c.get("volume_flow_lmin", 0.0)

        # Kv-Wert: V̇ = Kv · √(Δp)  mit Δp in bar, V̇ in m³/h
        # → Kv = V̇ / √(Δp_valve)
        # V̇ [l/min] → [m³/h]: * 0.06
        # Δp [mbar] → [bar]: / 1000
        kv = 0.0
        if dp_valve > 0 and vf > 0:
            vf_m3h = vf * 0.06
            dp_bar = dp_valve / 1000.0
            kv = vf_m3h / math.sqrt(dp_bar)

        out = dict(c)
        out["dp_max_mbar"] = global_max
        out["dp_valve_mbar"] = dp_valve
        out["kv_value"] = kv
        result.append(out)

    return result
