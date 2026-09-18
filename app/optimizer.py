"""
PuLP Linear Programming Optimizer for GridWise.
Solves the 24-hour campus energy scheduling problem to minimize grid electricity cost
while strictly obeying all physical, battery, and directive constraints.
"""

import logging
from typing import List, Dict, Any, Tuple
import pulp

from app.models import (
    HourInput,
    BatteryInput,
    DirectiveInterpretation,
    HourlyPlanEntry,
)

logger = logging.getLogger("gridwise.optimizer")


def solve_energy_schedule(
    hours: List[HourInput],
    battery: BatteryInput,
    directives: List[DirectiveInterpretation],
) -> List[HourlyPlanEntry]:
    """
    Formulates and solves the mixed-integer linear program (MILP) using PuLP with CBC.
    Returns the hourly_plan array with 24 entries.
    """
    # 1. Compute effective solar and minimum battery reserves per hour
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

    # 2. Create PuLP problem
    prob = pulp.LpProblem("GridWise_Cost_Minimization", pulp.LpMinimize)

    # Decision variables for each hour h in 0..23
    # G_h: Grid purchase
    # S_h: Solar used
    # C_h: Battery charge
    # D_h: Battery discharge
    # E_h: Battery energy after hour h
    # u_h: Binary indicator (1 if charging, 0 if discharging)
    G = [pulp.LpVariable(f"grid_{h}", lowBound=0) for h in range(24)]
    S = [pulp.LpVariable(f"solar_used_{h}", lowBound=0, upBound=effective_solar[h]) for h in range(24)]
    C = [pulp.LpVariable(f"charge_{h}", lowBound=0, upBound=battery.max_charge_kwh_per_hour) for h in range(24)]
    D = [pulp.LpVariable(f"discharge_{h}", lowBound=0, upBound=battery.max_discharge_kwh_per_hour) for h in range(24)]
    E = [pulp.LpVariable(f"energy_{h}", lowBound=min_reserves[h], upBound=battery.capacity_kwh) for h in range(24)]
    u = [pulp.LpVariable(f"is_charging_{h}", cat=pulp.LpBinary) for h in range(24)]

    # Objective: minimize total cost of grid electricity
    prob += pulp.lpSum(G[h] * hours[h].tariff_bdt_per_kwh for h in range(24))

    # Constraints for each hour
    for h in range(24):
        demand = hours[h].demand_kwh

        # Energy balance: grid + solar_used + discharge = demand + charge
        prob += G[h] + S[h] + D[h] == demand + C[h], f"Energy_Balance_{h}"

        # Mutual exclusivity of charging and discharging
        prob += C[h] <= battery.max_charge_kwh_per_hour * u[h], f"Charge_Indicator_{h}"
        prob += D[h] <= battery.max_discharge_kwh_per_hour * (1 - u[h]), f"Discharge_Indicator_{h}"

        # Battery state evolution
        if h == 0:
            prob += E[0] == battery.initial_energy_kwh + C[0] - D[0], f"Battery_Evol_{h}"
        else:
            prob += E[h] == E[h - 1] + C[h] - D[h], f"Battery_Evol_{h}"

        # Directive constraints
        if h in no_charge_hours:
            prob += C[h] == 0, f"No_Charge_{h}"

        if h in no_discharge_hours:
            prob += D[h] == 0, f"No_Discharge_{h}"

        if h in max_grid_limits:
            prob += G[h] <= max_grid_limits[h], f"Max_Grid_{h}"

    # End-of-day neutrality: final battery energy equals initial battery energy
    prob += E[23] == battery.initial_energy_kwh, "End_Of_Day_Neutrality"

    # Solve with bundled CBC solver (silent)
    solver = pulp.PULP_CBC_CMD(msg=False)
    status = prob.solve(solver)

    if status != pulp.LpStatusOptimal:
        logger.error(f"Solver failed to find optimal solution, status: {pulp.LpStatus[status]}")
        raise ValueError(f"Optimization failed to find an optimal solution: {pulp.LpStatus[status]}")

    # Build hourly_plan
    hourly_plan: List[HourlyPlanEntry] = []
    current_energy = battery.initial_energy_kwh

    for h in range(24):
        c_val = pulp.value(C[h]) or 0.0
        d_val = pulp.value(D[h]) or 0.0
        s_val = pulp.value(S[h]) or 0.0
        g_val = pulp.value(G[h]) or 0.0

        # Clean numerical jitter
        c_val = 0.0 if abs(c_val) < 1e-5 else c_val
        d_val = 0.0 if abs(d_val) < 1e-5 else d_val
        s_val = 0.0 if abs(s_val) < 1e-5 else s_val
        g_val = 0.0 if abs(g_val) < 1e-5 else g_val

        if c_val > 1e-4:
            action = "charge"
            b_kwh = round(c_val, 4)
            current_energy += b_kwh
        elif d_val > 1e-4:
            action = "discharge"
            b_kwh = round(d_val, 4)
            current_energy -= b_kwh
        else:
            action = "idle"
            b_kwh = 0.0

        current_energy = round(current_energy, 4)
        s_used = round(min(s_val, effective_solar[h]), 4)
        
        # Balance grid_kwh precisely: grid = demand + charge - solar_used - discharge
        demand_h = hours[h].demand_kwh
        c_amount = b_kwh if action == "charge" else 0.0
        d_amount = b_kwh if action == "discharge" else 0.0
        needed_grid = round(max(0.0, demand_h + c_amount - s_used - d_amount), 4)

        hourly_plan.append(
            HourlyPlanEntry(
                hour=h,
                grid_kwh=needed_grid,
                solar_used_kwh=s_used,
                battery_action=action,
                battery_kwh=b_kwh,
                battery_energy_after_kwh=current_energy,
            )
        )

    return hourly_plan
