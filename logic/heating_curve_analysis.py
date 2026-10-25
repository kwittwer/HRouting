"""Stationary operating-point sweep and outdoor-dependent load matching."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import math

from logic.heating_calc import FLOOR_COVERINGS, calc_circuit, calc_specific_heat_output


SETTINGS_KEY = "heating_curve_analysis"
MAX_EVALUATIONS = 250_000


def number(value: object) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Alle Werte muessen endlich sein.")
    return result


def range_values(bounds: list[float]) -> list[float]:
    lower, upper, step = (Decimal(str(number(value))) for value in bounds)
    if lower > upper or step <= 0:
        raise ValueError("Bereiche benoetigen Minimum <= Maximum und Schritt > 0.")
    if upper - lower >= step * MAX_EVALUATIONS:
        raise ValueError("Zu viele Bereichsschritte.")
    count = int((upper - lower) // step) + 1
    if count > MAX_EVALUATIONS:
        raise ValueError("Zu viele Bereichsschritte.")
    return [float(lower + index * step) for index in range(count)]


def default_config(norm_outdoor: float = -12.0) -> dict:
    return {
        "version": 1,
        "supply": [25.0, 45.0, 1.0],
        "spread": [3.0, 10.0, 1.0],
        "outdoor": [norm_outdoor, 15.0, 1.0],
        "heating_limit": 15.0,
        "max_flow": None,
        "max_pressure": None,
        "circuits": {},
    }


def validate_config(config: dict, norm_outdoor: float) -> None:
    sizes = {}
    for key in ("supply", "spread", "outdoor"):
        if not isinstance(config[key], list) or len(config[key]) != 3:
            raise ValueError("Temperaturbereiche brauchen drei Zahlen.")
        sizes[key] = len(range_values(config[key]))
    if sizes["supply"] * sizes["spread"] > MAX_EVALUATIONS:
        raise ValueError("Raster zu gross. Bitte die Schrittweite erhoehen.")
    evaluations = sizes["supply"] * sizes["spread"] * max(len(config.get("circuits", {})), 1)
    if evaluations > MAX_EVALUATIONS or evaluations * sizes["outdoor"] > 5_000_000:
        raise ValueError("Raster zu gross. Bitte die Schrittweite erhoehen.")
    if number(config["spread"][0]) <= 0:
        raise ValueError("Die Spreizung muss positiv sein.")
    if not 20 <= number(config["supply"][0]) <= number(config["supply"][1]) <= 90:
        raise ValueError("Der Vorlaufbereich muss zwischen 20 und 90 C liegen.")
    if number(config["heating_limit"]) <= number(norm_outdoor):
        raise ValueError("Die Heizgrenze muss ueber der Norm-Aussentemperatur liegen.")
    for limits in [config, *config.get("circuits", {}).values()]:
        for key in ("max_flow", "max_pressure", "heat_load_w"):
            if limits.get(key) is not None and number(limits[key]) < 0:
                raise ValueError("Heizlasten und Grenzen duerfen nicht negativ sein.")
    for settings in config.get("circuits", {}).values():
        validate_load_settings(settings)


def validate_load_settings(settings: dict) -> None:
    mode = settings.get("load_mode", "design_watts")
    if mode not in ("design_watts", "reference_specific"):
        raise ValueError("Unbekannter Heizlastmodus.")
    for key in ("specific_heat_load_wm2", "reference_room_temp", "reference_outdoor_temp", "reference_area_m2"):
        if settings.get(key) is not None:
            value = number(settings[key])
            if key == "specific_heat_load_wm2" and value < 0:
                raise ValueError("Die spezifische Heizlast darf nicht negativ sein.")
            if key == "reference_area_m2" and value <= 0:
                raise ValueError("Die Bezugsflaeche muss positiv sein.")
    if (settings.get("reference_room_temp") is not None
            and settings.get("reference_outdoor_temp") is not None
            and number(settings["reference_room_temp"]) <= number(settings["reference_outdoor_temp"])):
        raise ValueError("Die Referenz-Raumtemperatur muss ueber der Referenz-Aussentemperatur liegen.")


def heat_demand(load_w: float, outdoor: float, norm_outdoor: float, limit: float) -> float:
    return load_w * max(0.0, (limit - outdoor) / (limit - norm_outdoor))


def circuit_heat_demand(circuit: dict, settings: dict, outdoor: float,
                        norm_outdoor: float, limit: float) -> float | None:
    validate_load_settings(settings)
    if settings.get("load_mode", "design_watts") == "design_watts":
        load = settings.get("heat_load_w")
        return None if load is None else (0.0 if outdoor >= limit else heat_demand(load, outdoor, norm_outdoor, limit))
    keys = ("specific_heat_load_wm2", "reference_room_temp", "reference_outdoor_temp")
    if any(settings.get(key) is None for key in keys):
        return None
    specific, room_reference, outdoor_reference = (number(settings[key]) for key in keys)
    area = number(settings["reference_area_m2"] if settings.get("reference_area_m2") is not None else circuit["area_m2"])
    if area <= 0:
        return None
    if room_reference <= outdoor_reference:
        raise ValueError("Referenz-Temperaturdifferenz muss positiv sein.")
    if outdoor >= limit:
        return 0.0
    result = specific * area * max(0.0, (number(circuit["room_temp"]) - outdoor) / (room_reference - outdoor_reference))
    return number(result)


def load_metadata(circuit: dict, settings: dict) -> dict:
    return {
        "load_mode": settings.get("load_mode", "design_watts"),
        "specific_heat_load_wm2": settings.get("specific_heat_load_wm2"),
        "reference_room_temp": settings.get("reference_room_temp"),
        "reference_outdoor_temp": settings.get("reference_outdoor_temp"),
        "reference_area_m2": settings.get("reference_area_m2") if settings.get("reference_area_m2") is not None else circuit["area_m2"],
        "room_temp": circuit["room_temp"],
    }


def circuit_result(circuit: dict, supply: float, spread: float, limits: dict) -> dict:
    status = "OK"
    if circuit["area_m2"] <= 0 or circuit["route_m"] <= 0:
        status = "Geometrie unvollstaendig"
    elif circuit["diameter_mm"] <= 4 or circuit["spacing_mm"] <= 0:
        status = "Rohrparameter ungueltig"
    elif spread <= 0 or supply - spread <= circuit["room_temp"]:
        status = "Ruecklauf <= Raumtemperatur"
    values = calc_circuit(
        t_supply=supply, t_return=supply - spread,
        t_room=circuit["room_temp"], spacing_cm=circuit["spacing_mm"] / 10,
        r_lambda_b=FLOOR_COVERINGS.get(circuit["floor_covering"], 0.01),
        area_m2=circuit["area_m2"], pipe_length_m=circuit["route_m"] / 2,
        outer_diameter_mm=circuit["diameter_mm"], total_pipe_length_m=circuit["total_m"],
    )
    if status == "OK":
        for limit_key, value_key in (("max_flow", "volume_flow_lmin"), ("max_pressure", "pressure_drop_mbar")):
            if limits.get(limit_key) is not None and values[value_key] > limits[limit_key]:
                status = "Hydrauliklimit"
    return {"id": circuit["id"], "name": circuit["name"], "distributor": circuit["distributor"],
            "supply": supply, "return": supply - spread, "spread": spread, "status": status, **values}


def analysis_steps(circuits: list[dict], config: dict, baseline_supply: float,
                   baseline_return: float, norm_outdoor: float):
    """Yield progress between bounded units; return the complete result on exhaustion."""
    circuits, config = deepcopy(circuits), deepcopy(config)
    validate_config(config, norm_outdoor)
    supplies, spreads, outdoors = (range_values(config[key]) for key in ("supply", "spread", "outdoor"))
    count = len(supplies) * len(spreads) * len(circuits)
    if (len(supplies) * len(spreads) > MAX_EVALUATIONS or count > MAX_EVALUATIONS
            or len(outdoors) * len(supplies) * len(spreads) * max(len(circuits), 1) > 5_000_000):
        raise ValueError("Raster zu gross. Bitte die Schrittweite erhoehen.")
    baseline = {circuit["id"]: circuit_result(circuit, baseline_supply,
                baseline_supply - baseline_return, {}) for circuit in circuits}
    points, groups, curve, details = [], [], [], []
    total = count + len(outdoors) * len(supplies) * len(spreads)
    done = 0
    for supply in supplies:
        for spread in spreads:
            group = []
            for circuit in circuits:
                specific = config["circuits"].get(circuit["id"], {})
                limits = {key: specific.get(key) if specific.get(key) is not None else config.get(key)
                          for key in ("max_flow", "max_pressure")}
                row = circuit_result(circuit, supply, spread, limits)
                for key in ("power_w", "q_wm2", "volume_flow_lmin", "pressure_drop_mbar"):
                    base = baseline[circuit["id"]][key]
                    row[f"delta_{key}"] = row[key] - base
                    row[f"percent_{key}"] = 100 * (row[key] - base) / base if base > 0 else None
                points.append(row)
                group.append(row)
                done += 1
                yield done, total
            groups.append(group)
    for outdoor in outdoors:
        calculated_demands = {circuit["id"]: circuit_heat_demand(circuit, config["circuits"].get(circuit["id"], {}),
                              outdoor, norm_outdoor, config["heating_limit"]) for circuit in circuits}
        missing = any(value is None for value in calculated_demands.values())
        demands = {key: value if value is not None else 0.0 for key, value in calculated_demands.items()}
        best = None
        best_key = None
        thermally_covered = False
        for group in groups:
            covered = bool(group) and all(row["power_w"] + 1e-9 >= demands[row["id"]] for row in group)
            thermally_covered |= covered and all(row["status"] in ("OK", "Hydrauliklimit") for row in group)
            if covered and all(row["status"] == "OK" for row in group):
                key = (group[0]["supply"], max(row["pressure_drop_mbar"] for row in group),
                       sum(row["volume_flow_lmin"] for row in group), group[0]["spread"])
                if best_key is None or key < best_key:
                    best, best_key = group, key
            done += 1
            yield done, total
        status = "OK" if best else ("Hydrauliklimit" if thermally_covered else "Bedarf nicht gedeckt")
        if not circuits:
            status = "Keine Heizkreise"
        elif outdoor >= config["heating_limit"]:
            status = "Heizung aus"
        elif missing:
            status = "Heizlast unbekannt"
        elif any(row["status"] in ("Geometrie unvollstaendig", "Rohrparameter ungueltig") for row in baseline.values()):
            status = "Geometrie / Rohrparameter unvollstaendig"
        if status != "OK":
            best = None
        bottleneck = min(best, key=lambda row: row["power_w"] - demands[row["id"]]) if best else None
        selected = {row["id"]: row for row in best} if best else {}
        for circuit in circuits:
            circuit_id = circuit["id"]
            settings = config["circuits"].get(circuit_id, {})
            known_load = calculated_demands[circuit_id] is not None
            demand = demands[circuit_id] if known_load else None
            operating = selected.get(circuit_id)
            values = {key: operating[key] if operating else (0.0 if status == "Heizung aus" else None)
                      for key in ("power_w", "q_wm2", "volume_flow_lmin", "pressure_drop_mbar")}
            details.append({
                "outdoor": outdoor, "id": circuit_id, "name": circuit["name"],
                **load_metadata(circuit, settings),
                "demand_w": demand, "status": status, **values,
                "baseline_power_w": baseline[circuit_id]["power_w"],
                "coverage_percent": 100 * values["power_w"] / demand
                    if demand and values["power_w"] is not None else None,
                "reserve_w": values["power_w"] - demand
                    if demand is not None and values["power_w"] is not None else None,
            })
        curve.append({
            "outdoor": outdoor, "demand_w": None if missing else sum(demands.values()),
            "supply": best[0]["supply"] if best else None,
            "return": best[0]["return"] if best else None,
            "spread": best[0]["spread"] if best else None,
            "max_pressure": max(row["pressure_drop_mbar"] for row in best) if best else None,
            "total_flow": sum(row["volume_flow_lmin"] for row in best) if best else None,
            "bottleneck": bottleneck["id"] if bottleneck else "",
            "baseline_covered": None if missing else all(row["power_w"] >= demands[row["id"]]
                and row["status"] == "OK" for row in baseline.values()),
            "delta_supply": best[0]["supply"] - baseline_supply if best else None,
            "status": status,
        })
    return {"points": points, "curve": curve, "baseline": baseline, "details": details}


def analyze(*args, **kwargs) -> dict:
    iterator = analysis_steps(*args, **kwargs)
    while True:
        try:
            next(iterator)
        except StopIteration as finished:
            return finished.value


def required_supply_temperature(circuit: dict, demand_wm2: float, spread: float) -> float | None:
    demand_wm2, spread = number(demand_wm2), number(spread)
    room = number(circuit["room_temp"])
    spacing = number(circuit["spacing_mm"]) / 10.0
    if spread <= 0 or spacing <= 0 or demand_wm2 < 0:
        raise ValueError("Spreizung und Verlegeabstand muessen positiv, die Heizlast nichtnegativ sein.")
    if demand_wm2 == 0:
        return None
    covering = FLOOR_COVERINGS.get(circuit["floor_covering"], 0.01)

    def output(supply):
        return calc_specific_heat_output(supply, supply - spread, room, spacing, covering)

    lower, upper = room + spread, 90.0
    if lower >= upper or output(upper) < demand_wm2:
        return None
    for _ in range(40):
        middle = (lower + upper) / 2
        if output(middle) >= demand_wm2:
            upper = middle
        else:
            lower = middle
    return min(90.0, math.ceil(upper * 10.0) / 10.0)


def individual_curve_steps(circuits: list[dict], settings: dict, outdoor_bounds: list[float],
                           spread: float, heating_limit: float):
    """Compute independent thermal curves, without plant-wide feasibility constraints."""
    circuits, settings = deepcopy(circuits), deepcopy(settings)
    spread, heating_limit = number(spread), number(heating_limit)
    if spread <= 0:
        raise ValueError("Die Projektspreizung muss positiv sein (Vorlauf groesser als Ruecklauf).")
    outdoors = range_values(outdoor_bounds)
    total = len(circuits) * len(outdoors)
    if total > MAX_EVALUATIONS:
        raise ValueError("Zu viele Kurvenpunkte. Bitte die Aussentemperatur-Schrittweite erhoehen.")
    result = {}
    done = 0
    for circuit in circuits:
        specific = settings.get(circuit["id"], {})
        error = None
        reference = {}
        try:
            reference = {key: specific.get(key) for key in
                         ("specific_heat_load_wm2", "reference_room_temp", "reference_outdoor_temp")}
            validate_load_settings({"load_mode": "reference_specific", **reference})
            for key in ("area_m2", "spacing_mm", "room_temp"):
                number(circuit[key])
            if specific.get("reference_error"):
                error = "Referenzwerte ungueltig"
            elif circuit["area_m2"] <= 0 or circuit["spacing_mm"] <= 0:
                error = "Flaeche / Verlegeabstand ungueltig"
            elif any(value is None for value in reference.values()):
                error = "Referenzwerte fehlen"
        except (ValueError, TypeError, KeyError, OverflowError):
            error = "Referenzwerte / Kreisparameter ungueltig"
        rows = []
        for outdoor in outdoors:
            demand, supply = None, None
            status = error or "OK"
            if error is None:
                demand = number(reference["specific_heat_load_wm2"]) * max(
                    0.0, (circuit["room_temp"] - outdoor) /
                    (reference["reference_room_temp"] - reference["reference_outdoor_temp"]))
                if outdoor >= heating_limit:
                    demand, status = 0.0, "Heizung aus"
                elif demand == 0:
                    status = "Kein Heizbedarf"
                else:
                    supply = required_supply_temperature(circuit, demand, spread)
                    if supply is None:
                        status = "Keine Lastdeckung bis 90 C"
            rows.append({"id": circuit["id"], "name": circuit["name"], "outdoor": outdoor,
                         "supply": supply, "return": supply - spread if supply is not None else None,
                         "spread": spread, "q_needed": demand,
                         "demand_w": demand * circuit["area_m2"] if demand is not None else None,
                         "room_temp": circuit["room_temp"], "status": status, **reference})
            done += 1
            yield done, total
        result[circuit["id"]] = rows
    return result