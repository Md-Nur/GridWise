"""
Deterministic Guardrail Validator for GridWise.
Enforces all semantic, schema, and physical safety rules on LLM-extracted directives
before anything is passed to the mathematical optimizer.
"""

import logging
from typing import List, Dict, Any, Tuple
from app.models import DirectiveInterpretation, BatteryInput

logger = logging.getLogger("gridwise.guardrails")

ALLOWED_DIRECTIVE_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}


def _validate_hours_list(hours: Any) -> Tuple[bool, List[int], str]:
    """
    Validates that hours is a non-empty list of unique integers in strictly ascending order within [0, 23].
    Returns (is_valid, sanitized_hours, error_message).
    """
    if not isinstance(hours, list) or len(hours) == 0:
        return False, [], "hours must be a non-empty list"

    sanitized = []
    for h in hours:
        if not isinstance(h, int) or isinstance(h, bool):
            return False, [], f"hour {h} must be an integer"
        if h < 0 or h > 23:
            return False, [], f"hour {h} is outside [0, 23]"
        sanitized.append(h)

    # Check unique and strictly ascending
    for i in range(len(sanitized) - 1):
        if sanitized[i] >= sanitized[i + 1]:
            return False, [], f"hours must be unique and strictly ascending: {sanitized}"

    return True, sanitized, ""


def validate_and_sanitize_directive(
    raw_directive: Dict[str, Any],
    expected_note_index: int,
    battery: BatteryInput,
) -> DirectiveInterpretation:
    """
    Validates a single raw directive interpretation dictionary against deterministic guardrails.
    If the LLM output is malformed or invalid, safely coerces to no_op with explanation.
    """
    note_idx = raw_directive.get("note_index", expected_note_index)
    directive_type = raw_directive.get("directive_type")
    applies = raw_directive.get("applies", False)
    structured_adj = raw_directive.get("structured_adjustment")
    explanation = raw_directive.get("explanation", "")

    # Fallback template
    def make_safe_noop(reason: str) -> DirectiveInterpretation:
        logger.warning(f"Guardrail coerced note {expected_note_index} to no_op: {reason}")
        return DirectiveInterpretation(
            note_index=expected_note_index,
            applies=False,
            directive_type="no_op",
            structured_adjustment=None,
            explanation=f"Safely coerced to no_op by guardrail: {reason}",
        )

    # 1. Check directive_type
    if directive_type not in ALLOWED_DIRECTIVE_TYPES:
        return make_safe_noop(f"Unsupported directive type '{directive_type}'")

    # 2. Check no_op semantics
    if directive_type == "no_op":
        return DirectiveInterpretation(
            note_index=expected_note_index,
            applies=False,
            directive_type="no_op",
            structured_adjustment=None,
            explanation=explanation or "This note does not affect today's energy schedule.",
        )

    # 3. For all other directive types, applies MUST be True
    if not applies:
        return make_safe_noop(f"Directive '{directive_type}' specified applies=False")

    if not isinstance(structured_adj, dict):
        return make_safe_noop(f"Directive '{directive_type}' missing structured_adjustment dictionary")

    # 4. Validate hours list
    hours_valid, hours, err = _validate_hours_list(structured_adj.get("hours"))
    if not hours_valid:
        return make_safe_noop(f"Invalid hours for '{directive_type}': {err}")

    # 5. Type-specific validations
    if directive_type == "solar_reduction":
        factor = structured_adj.get("factor")
        if factor is None or not isinstance(factor, (int, float)) or isinstance(factor, bool):
            return make_safe_noop("solar_reduction requires a numeric 'factor'")
        factor = float(factor)
        if factor < 0.0 or factor > 1.0:
            return make_safe_noop(f"solar_reduction factor {factor} must be between 0.0 and 1.0")
        return DirectiveInterpretation(
            note_index=expected_note_index,
            applies=True,
            directive_type="solar_reduction",
            structured_adjustment={"hours": hours, "factor": factor},
            explanation=explanation,
        )

    elif directive_type == "minimum_battery_reserve":
        min_kwh = structured_adj.get("minimum_energy_kwh")
        if min_kwh is None or not isinstance(min_kwh, (int, float)) or isinstance(min_kwh, bool):
            return make_safe_noop("minimum_battery_reserve requires numeric 'minimum_energy_kwh'")
        min_kwh = float(min_kwh)
        if min_kwh < 0:
            return make_safe_noop(f"minimum_energy_kwh {min_kwh} cannot be negative")
        if min_kwh > battery.capacity_kwh:
            return make_safe_noop(
                f"minimum_energy_kwh {min_kwh} exceeds battery capacity {battery.capacity_kwh}"
            )
        return DirectiveInterpretation(
            note_index=expected_note_index,
            applies=True,
            directive_type="minimum_battery_reserve",
            structured_adjustment={"hours": hours, "minimum_energy_kwh": min_kwh},
            explanation=explanation,
        )

    elif directive_type == "no_charge_window":
        return DirectiveInterpretation(
            note_index=expected_note_index,
            applies=True,
            directive_type="no_charge_window",
            structured_adjustment={"hours": hours},
            explanation=explanation,
        )

    elif directive_type == "no_discharge_window":
        return DirectiveInterpretation(
            note_index=expected_note_index,
            applies=True,
            directive_type="no_discharge_window",
            structured_adjustment={"hours": hours},
            explanation=explanation,
        )

    elif directive_type == "max_grid_window":
        max_kwh = structured_adj.get("max_grid_kwh")
        if max_kwh is None or not isinstance(max_kwh, (int, float)) or isinstance(max_kwh, bool):
            return make_safe_noop("max_grid_window requires numeric 'max_grid_kwh'")
        max_kwh = float(max_kwh)
        if max_kwh < 0:
            return make_safe_noop(f"max_grid_kwh {max_kwh} cannot be negative")
        return DirectiveInterpretation(
            note_index=expected_note_index,
            applies=True,
            directive_type="max_grid_window",
            structured_adjustment={"hours": hours, "max_grid_kwh": max_kwh},
            explanation=explanation,
        )

    return make_safe_noop(f"Unhandled directive type '{directive_type}'")


def validate_directives_list(
    raw_directives: List[Dict[str, Any]],
    operator_notes: List[str],
    battery: BatteryInput,
) -> List[DirectiveInterpretation]:
    """
    Validates that each operator note has exactly one valid directive interpretation,
    returned in strictly note_index order (0..N-1).
    """
    num_notes = len(operator_notes)
    validated: List[DirectiveInterpretation] = []

    # Map raw directives by note_index if available
    by_index: Dict[int, Dict[str, Any]] = {}
    for item in raw_directives:
        if isinstance(item, dict) and "note_index" in item:
            idx = item["note_index"]
            if isinstance(idx, int) and 0 <= idx < num_notes:
                by_index[idx] = item

    for idx in range(num_notes):
        raw = by_index.get(idx)
        if raw is None and idx < len(raw_directives) and isinstance(raw_directives[idx], dict):
            raw = raw_directives[idx]
        if raw is None:
            raw = {
                "note_index": idx,
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": None,
                "explanation": "No valid interpretation produced for this note.",
            }

        sanitized = validate_and_sanitize_directive(raw, idx, battery)
        validated.append(sanitized)

    return validated
