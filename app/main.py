"""
GridWise Main FastAPI Application.
Exposes:
  - GET /health
  - POST /optimize-energy
"""

import logging
import sys
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.models import (
    OptimizeEnergyRequest,
    OptimizeEnergyResponse,
    HealthResponse,
)
from app.llm_interpreter import interpret_operator_notes
from app.guardrails import validate_directives_list
from app.optimizer import solve_energy_schedule
from app.replayer import (
    replay_and_verify_schedule,
    recalculate_metrics,
    generate_plan_summary,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("gridwise.main")

app = FastAPI(
    title="GridWise Energy Optimizer",
    description="LLM-Assisted Smart Campus Energy Optimization Service",
    version="1.0.0",
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Returns HTTP 400 for structurally invalid requests or malformed payloads
    per the BUP CSE Fest GridWise specification.
    """
    logger.warning(f"Request validation error: {exc}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Malformed JSON or structurally invalid request.", "errors": exc.errors()},
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """
    Controlled HTTP 500 error handler that never leaks stack traces or secrets.
    """
    logger.error(f"Internal server error: {type(exc).__name__}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal error occurred while processing the energy schedule."},
    )


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """
    Readiness endpoint for the judging harness.
    Must return status 'ok' with HTTP 200 within 60 seconds of startup.
    """
    return HealthResponse(status="ok")


@app.post("/optimize-energy", response_model=OptimizeEnergyResponse, tags=["Optimization"])
async def optimize_energy(payload: OptimizeEnergyRequest):
    """
    Main GridWise optimization pipeline:
    1. LLM Directive Interpretation (Gemini 2.0 Flash)
    2. Deterministic Guardrails Validation
    3. Mathematical Optimization (PuLP LP/MILP with CBC)
    4. Constraint Replay & Metric Recalculation
    5. Response Assembly
    """
    logger.info(f"Processing scenario: {payload.scenario_id} with {len(payload.operator_notes)} operator notes")

    # Stage 1: Interpret operator notes using LLM
    raw_directives = interpret_operator_notes(payload.operator_notes, payload.battery)

    # Stage 2: Validate through deterministic guardrails
    validated_directives = validate_directives_list(raw_directives, payload.operator_notes, payload.battery)

    # Stage 3: Solve LP / MILP optimization problem
    hourly_plan = solve_energy_schedule(payload.hours, payload.battery, validated_directives)

    # Stage 4: Replay schedule to verify constraint satisfaction
    is_valid, violations = replay_and_verify_schedule(
        payload.hours, payload.battery, validated_directives, hourly_plan
    )
    if not is_valid:
        logger.warning(f"Scenario {payload.scenario_id} had replay warnings: {violations}")

    # Stage 5: Recalculate metrics directly from hourly_plan
    total_grid, total_cost, peak_grid = recalculate_metrics(payload.hours, hourly_plan)
    summary = generate_plan_summary(validated_directives, total_grid, total_cost, peak_grid)

    # Stage 6: Assemble final response matching exact contract
    return OptimizeEnergyResponse(
        scenario_id=payload.scenario_id,
        directive_interpretation=validated_directives,
        hourly_plan=hourly_plan,
        total_grid_kwh=total_grid,
        total_cost_bdt=total_cost,
        peak_grid_kwh=peak_grid,
        plan_summary=summary,
    )
