"""
Replayer and Schedule Validator for GridWise.
Independently verifies that the generated 24-hour schedule satisfies all physical,
battery, and operator-directive constraints, and recalculates total metrics.
"""

import logging
from typing import List, Dict, Any, Tuple
from app.models import (
    HourInput,
    BatteryInput,
    DirectiveInterpretation,
    HourlyPlanEntry,
)

logger = logging.getLogger("gridwise.replayer")


def replay_and_verify_schedule(
    hours: List[HourInput],
    battery: BatteryInput,
    directives: List[DirectiveInterpretation],
    hourly_plan: List[HourlyPlanEntry],
    tolerance: float = 0.01,
) -> Tuple[bool, List[str]]:
    """
    Replays the schedule hour by hour, checking all GridWise constraints.
    Returns (is_valid, list_of_violations).
    """
    violations: List[str] = []

    if len(hourly_plan) != 24:
        violations.append(f"hourly_plan has length {len(hourly_plan)}, expected 24")
        return False, violations

    # Compute effective solar and reserves per hour
    effective_solar = [h.solar_kwh for h in hours]
    min_reserves = [battery.minimum_energy_kwh for _ in range(24)]
    no_charge_hours = set()
    no_discharge_hours = set()
    max_grid_limits: Dict[int, float] = {}

    for d in directives:
        if not d.applies or not d.structured_adjustment:
            continue
        adj = d.structured_adjustment
        d_hours = adj.get("hours", [])
        if d.directive_type == "solar_reduction":
            factor = float(adj.get("factor", 1.0))
            for h in d_hours:
                if 0 <= h < 24:
                    effective_solar[h] = effective_solar[h] * factor
        elif d.directive_type == "minimum_battery_reserve":
            reserve_kwh = float(adj.get("minimum_energy_kwh", battery.minimum_energy_kwh))
            for h in d_hours:
                if 0 <= h < 24:
                    min_reserves[h] = max(min_reserves[h], reserve_kwh)
        elif d.directive_type == "no_charge_window":
            for h in d_hours:
                if 0 <= h < 24:
                    no_charge_hours.add(h)
        elif d.directive_type == "no_discharge_window":
            for h in d_hours:
                if 0 <= h < 24:
                    no_discharge_hours.add(h)
        elif d.directive_type == "max_grid_window":
            grid_cap = float(adj.get("max_grid_kwh", 1e9))
            for h in d_hours:
                if 0 <= h < 24:
                    if h in max_grid_limits:
                        max_grid_limits[h] = min(max_grid_limits[h], grid_cap)
                    else:
                        max_grid_limits[h] = grid_cap

    prev_energy = battery.initial_energy_kwh

    for h, entry in enumerate(hourly_plan):
        if entry.hour != h:
            violations.append(f"Hour mismatch at index {h}: got {entry.hour}")

        # Check non-negative
        for field_name, val in [
            ("grid_kwh", entry.grid_kwh),
            ("solar_used_kwh", entry.solar_used_kwh),
            ("battery_kwh", entry.battery_kwh),
            ("battery_energy_after_kwh", entry.battery_energy_after_kwh),
        ]:
            if val < -tolerance:
                violations.append(f"Hour {h}: negative {field_name} = {val}")

        # Solar usage check
        if entry.solar_used_kwh > effective_solar[h] + tolerance:
            violations.append(
                f"Hour {h}: solar_used_kwh {entry.solar_used_kwh} exceeds effective solar {effective_solar[h]}"
            )

        # Battery action and transition
        c_kwh = entry.battery_kwh if entry.battery_action == "charge" else 0.0
        d_kwh = entry.battery_kwh if entry.battery_action == "discharge" else 0.0

        if entry.battery_action == "idle" and entry.battery_kwh > tolerance:
            violations.append(f"Hour {h}: battery_action is idle but battery_kwh = {entry.battery_kwh}")

        if entry.battery_action == "charge" and entry.battery_kwh > battery.max_charge_kwh_per_hour + tolerance:
            violations.append(f"Hour {h}: charge {entry.battery_kwh} exceeds max rate {battery.max_charge_kwh_per_hour}")

        if entry.battery_action == "discharge" and entry.battery_kwh > battery.max_discharge_kwh_per_hour + tolerance:
            violations.append(f"Hour {h}: discharge {entry.battery_kwh} exceeds max rate {battery.max_discharge_kwh_per_hour}")

        expected_energy = prev_energy + c_kwh - d_kwh
        if abs(entry.battery_energy_after_kwh - expected_energy) > tolerance:
            violations.append(
                f"Hour {h}: battery_energy_after_kwh {entry.battery_energy_after_kwh} != expected {expected_energy}"
            )

        # Battery bounds check
        if entry.battery_energy_after_kwh < min_reserves[h] - tolerance:
            violations.append(
                f"Hour {h}: battery energy {entry.battery_energy_after_kwh} violates minimum reserve {min_reserves[h]}"
            )
        if entry.battery_energy_after_kwh > battery.capacity_kwh + tolerance:
            violations.append(
                f"Hour {h}: battery energy {entry.battery_energy_after_kwh} exceeds capacity {battery.capacity_kwh}"
            )

        # Energy balance check: grid + solar_used + discharge == demand + charge
        left_side = entry.grid_kwh + entry.solar_used_kwh + d_kwh
        right_side = hours[h].demand_kwh + c_kwh
        if abs(left_side - right_side) > tolerance:
            violations.append(
                f"Hour {h}: energy balance violated (grid {entry.grid_kwh} + solar {entry.solar_used_kwh} + d {d_kwh} = {left_side}) != (demand {hours[h].demand_kwh} + c {c_kwh} = {right_side})"
            )

        # Directives enforcement
        if h in no_charge_hours and entry.battery_action == "charge" and entry.battery_kwh > tolerance:
            violations.append(f"Hour {h}: charging occurred during no_charge_window")

        if h in no_discharge_hours and entry.battery_action == "discharge" and entry.battery_kwh > tolerance:
            violations.append(f"Hour {h}: discharging occurred during no_discharge_window")

        if h in max_grid_limits and entry.grid_kwh > max_grid_limits[h] + tolerance:
            violations.append(
                f"Hour {h}: grid_kwh {entry.grid_kwh} exceeds cap {max_grid_limits[h]}"
            )

        prev_energy = entry.battery_energy_after_kwh

    # End-of-day neutrality check
    final_energy = hourly_plan[23].battery_energy_after_kwh
    if abs(final_energy - battery.initial_energy_kwh) > tolerance:
        violations.append(
            f"End-of-day battery energy {final_energy} does not equal initial energy {battery.initial_energy_kwh}"
        )

    return len(violations) == 0, violations


def recalculate_metrics(
    hours: List[HourInput],
    hourly_plan: List[HourlyPlanEntry],
) -> Tuple[float, float, float]:
    """
    Recalculates total_grid_kwh, total_cost_bdt, and peak_grid_kwh from the hourly_plan.
    """
    total_grid = sum(entry.grid_kwh for entry in hourly_plan)
    total_cost = sum(entry.grid_kwh * hours[h].tariff_bdt_per_kwh for h, entry in enumerate(hourly_plan))
    peak_grid = max(entry.grid_kwh for entry in hourly_plan) if hourly_plan else 0.0

    return round(total_grid, 4), round(total_cost, 4), round(peak_grid, 4)


def generate_plan_summary(
    directives: List[DirectiveInterpretation],
    total_grid: float,
    total_cost: float,
    peak_grid: float,
) -> str:
    """
    Constructs a concise, informative human-readable summary of the operating strategy.
    """
    applied = [d for d in directives if d.applies]
    if applied:
        applied_types = ", ".join(d.directive_type for d in applied)
        return (
            f"Optimized 24-hour schedule applying directives ({applied_types}). "
            f"Total grid import is {total_grid:.1f} kWh at a total cost of {total_cost:.1f} BDT "
            f"(peak {peak_grid:.1f} kWh), while preserving end-of-day battery neutrality."
        )
    else:
        return (
            f"Baseline optimal 24-hour schedule with no active operator restrictions. "
            f"Total grid import is {total_grid:.1f} kWh at a total cost of {total_cost:.1f} BDT "
            f"(peak {peak_grid:.1f} kWh), restoring initial battery charge."
        )
