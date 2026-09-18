"""
Data models and schemas for GridWise smart campus energy optimization service.
Conforms strictly to BUP CSE Fest 2026 Preliminary Problem Statement.
"""

from typing import List, Literal, Optional, Union, Dict, Any
from pydantic import BaseModel, Field, field_validator, model_validator


DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
]

BatteryAction = Literal["charge", "discharge", "idle"]


# --- Request Models ---

class HourInput(BaseModel):
    hour: int = Field(..., ge=0, le=23, description="Hour of the day (0-23)")
    demand_kwh: float = Field(..., ge=0, description="Campus demand in kWh")
    solar_kwh: float = Field(..., ge=0, description="Base solar generation available in kWh")
    tariff_bdt_per_kwh: float = Field(..., ge=0, description="Grid tariff in BDT per kWh")


class BatteryInput(BaseModel):
    capacity_kwh: float = Field(..., gt=0, description="Maximum energy capacity in kWh")
    initial_energy_kwh: float = Field(..., ge=0, description="Initial battery energy in kWh")
    minimum_energy_kwh: float = Field(..., ge=0, description="Base reserve level in kWh")
    max_charge_kwh_per_hour: float = Field(..., ge=0, description="Maximum charge rate in kWh/h")
    max_discharge_kwh_per_hour: float = Field(..., ge=0, description="Maximum discharge rate in kWh/h")

    @model_validator(mode="after")
    def validate_battery(self) -> "BatteryInput":
        if self.minimum_energy_kwh > self.capacity_kwh:
            raise ValueError("minimum_energy_kwh cannot exceed capacity_kwh")
        if self.initial_energy_kwh < self.minimum_energy_kwh or self.initial_energy_kwh > self.capacity_kwh:
            raise ValueError("initial_energy_kwh must be between minimum_energy_kwh and capacity_kwh")
        return self


class OptimizeEnergyRequest(BaseModel):
    scenario_id: str = Field(..., min_length=1, description="Unique scenario identifier")
    operator_notes: List[str] = Field(..., min_length=1, max_length=3, description="1 to 3 operator notes")
    hours: List[HourInput] = Field(..., min_length=24, max_length=24, description="Exactly 24 hourly intervals")
    battery: BatteryInput = Field(..., description="Battery configuration")

    @field_validator("operator_notes")
    @classmethod
    def validate_notes(cls, notes: List[str]) -> List[str]:
        for i, note in enumerate(notes):
            if not note or not note.strip():
                raise ValueError(f"operator_notes[{i}] cannot be empty")
        return notes

    @field_validator("hours")
    @classmethod
    def validate_hours_sequence(cls, hours: List[HourInput]) -> List[HourInput]:
        for expected_h, entry in enumerate(hours):
            if entry.hour != expected_h:
                raise ValueError(f"Expected hour {expected_h} at index {expected_h}, got {entry.hour}")
        return hours


# --- Directive Interpretation Models ---

class SolarReductionAdjustment(BaseModel):
    hours: List[int]
    factor: float = Field(..., ge=0.0, le=1.0)


class MinimumBatteryReserveAdjustment(BaseModel):
    hours: List[int]
    minimum_energy_kwh: float = Field(..., ge=0.0)


class WindowAdjustment(BaseModel):
    hours: List[int]


class MaxGridWindowAdjustment(BaseModel):
    hours: List[int]
    max_grid_kwh: float = Field(..., ge=0.0)


StructuredAdjustmentType = Optional[
    Union[
        SolarReductionAdjustment,
        MinimumBatteryReserveAdjustment,
        MaxGridWindowAdjustment,
        WindowAdjustment,
        Dict[str, Any],
    ]
]


class DirectiveInterpretation(BaseModel):
    note_index: int = Field(..., ge=0, description="Zero-based index of corresponding operator note")
    applies: bool = Field(..., description="True for active directives, false ONLY for no_op")
    directive_type: DirectiveType = Field(..., description="One of 6 canonical directive types")
    structured_adjustment: Optional[Dict[str, Any]] = Field(None, description="Adjustment payload or null for no_op")
    explanation: str = Field(..., description="Short explanation of the interpretation")


# --- Hourly Plan and Response Models ---

class HourlyPlanEntry(BaseModel):
    hour: int = Field(..., ge=0, le=23)
    grid_kwh: float = Field(..., ge=0.0)
    solar_used_kwh: float = Field(..., ge=0.0)
    battery_action: BatteryAction
    battery_kwh: float = Field(..., ge=0.0)
    battery_energy_after_kwh: float = Field(..., ge=0.0)


class OptimizeEnergyResponse(BaseModel):
    scenario_id: str
    directive_interpretation: List[DirectiveInterpretation]
    hourly_plan: List[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str


class HealthResponse(BaseModel):
    status: str = "ok"
