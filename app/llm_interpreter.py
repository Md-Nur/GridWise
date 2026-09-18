"""
LLM Directive Interpreter for GridWise.
Uses Google Gemini (gemini-2.0-flash) to interpret natural-language operator notes
into machine-checkable structured directives.
"""

import json
import logging
import re
from typing import List, Dict, Any, Optional

from app.config import GEMINI_API_KEY, GEMINI_MODEL
from app.models import BatteryInput

logger = logging.getLogger("gridwise.llm_interpreter")

SYSTEM_PROMPT = """You are an expert energy scheduling assistant interpreting campus operator notes for a 24-hour smart grid optimization system.
You will receive:
1. Battery configuration (capacity_kwh, minimum_energy_kwh, etc.)
2. An array of 1 to 3 natural-language operator notes.

Your task is to classify EACH operator note into EXACTLY ONE of these 6 canonical directive types:
1. `solar_reduction`: Usable solar reduced during specific hours.
   - structured_adjustment shape: {"hours": [int, ...], "factor": float}
   - CRITICAL: "factor" is the usable fraction REMAINING (0.0 to 1.0).
     Example: "drop to about 20%" -> factor: 0.20
     Example: "80% reduction" -> factor: 0.20 (1.0 - 0.80 = 0.20)
     Example: "leave about half" -> factor: 0.50
     Example: "roughly 25% of forecast" -> factor: 0.25
2. `minimum_battery_reserve`: Keep battery energy at or above a required level during specific hours.
   - structured_adjustment shape: {"hours": [int, ...], "minimum_energy_kwh": float}
   - If stated as an absolute number (e.g., "at least 90 kWh"), minimum_energy_kwh = 90.0.
   - If stated as a percentage of battery capacity (e.g., "at least 50% of the battery capacity"), calculate: (percentage / 100.0) * battery_capacity_kwh.
3. `no_charge_window`: Battery charging is unavailable / prohibited during specific hours.
   - structured_adjustment shape: {"hours": [int, ...]}
4. `no_discharge_window`: Battery discharging is unavailable / prohibited during specific hours.
   - structured_adjustment shape: {"hours": [int, ...]}
5. `max_grid_window`: Campus grid intake / import may not exceed a stated amount during specific hours.
   - structured_adjustment shape: {"hours": [int, ...], "max_grid_kwh": float}
6. `no_op`: The note is irrelevant to today's 24-hour energy schedule (e.g. cafeteria menus, library hours, sports deadlines, future dates).
   - structured_adjustment: null
   - applies: false (no_op is the ONLY directive where applies = false)

TIME WINDOW CONVENTION (MANDATORY):
- Whole-hour intervals are START-INCLUSIVE and END-EXCLUSIVE.
- "1 PM to 3 PM" -> [13, 14] (NOT 15)
- "noon until 2 PM" -> [12, 13]
- "2 AM until 5 AM" -> [2, 3, 4]
- "6 PM until 8 PM" -> [18, 19]
- "6 PM until 9 PM" -> [18, 19, 20]
- "6 PM until 10 PM" -> [18, 19, 20, 21]
- "7 PM until 9 PM" -> [19, 20]
- "7 PM until 10 PM" -> [19, 20, 21]
- "10 AM until noon" -> [10, 11]
- "11 AM until 1 PM" -> [11, 12]
- "11 AM until 2 PM" -> [11, 12, 13]
- "2 PM until 4 PM" -> [14, 15]
- "5 PM until 7 PM" -> [17, 18]
- "hours" must be unique integers in strictly ascending order within [0, 23].

APPLIES RULE:
- applies must be true for: solar_reduction, minimum_battery_reserve, no_charge_window, no_discharge_window, max_grid_window.
- applies must be false ONLY for: no_op.

OUTPUT FORMAT:
Return a JSON array containing exactly one JSON object per note, in note_index order (0, 1, ... N-1):
[
  {
    "note_index": 0,
    "applies": true,
    "directive_type": "solar_reduction",
    "structured_adjustment": {
      "hours": [12, 13],
      "factor": 0.25
    },
    "explanation": "Solar availability is reduced to 25% from noon until 2 PM."
  }
]
Output valid JSON only. Do not include markdown formatting or backticks.
"""


def _parse_time_range(text: str) -> List[int]:
    """Helper for fallback regex parser: converts English time range to [start, end)."""
    t_lower = text.lower()
    # Find patterns like "from X (am|pm)? until/to Y (am|pm)?" or "between X and Y"
    pattern = r"(?:from|between)\s+(\d{1,2}|noon|midnight)(?::00)?\s*(am|pm)?\s*(?:until|to|and)\s+(\d{1,2}|noon|midnight)(?::00)?\s*(am|pm)?"
    m = re.search(pattern, t_lower)
    if not m:
        return []

    start_str, start_meridiem, end_str, end_meridiem = m.groups()

    def to_24h(s: str, meridiem: Optional[str], default_meridiem: Optional[str] = None) -> int:
        if s == "noon":
            return 12
        if s == "midnight":
            return 0
        h = int(s)
        eff_meridiem = meridiem or default_meridiem
        if eff_meridiem == "pm" and h < 12:
            return h + 12
        if eff_meridiem == "am" and h == 12:
            return 0
        return h

    # Infer start meridiem if only end has am/pm (e.g., "1 until 3 PM" -> both pm)
    start_hour = to_24h(start_str, start_meridiem, end_meridiem)
    end_hour = to_24h(end_str, end_meridiem, end_meridiem)

    if start_hour < end_hour and 0 <= start_hour <= 23 and 0 <= end_hour <= 24:
        return list(range(start_hour, end_hour))
    return []


def _fallback_interpret_note(note: str, note_index: int, battery: BatteryInput) -> Dict[str, Any]:
    """
    Safe offline deterministic interpreter used ONLY if Gemini API is unreachable or key is missing.
    Ensures graceful local testing while logging an explicit fallback warning.
    """
    logger.warning(f"Using fallback heuristic interpreter for note {note_index}: '{note}'")
    n_lower = note.lower()

    # Distractor check
    distractor_keywords = [
        "sports", "cafeteria", "menu", "library", "registration",
        "deadline", "book-return", "seminar room", "club notice", "tomorrow", "next week"
    ]
    energy_keywords = [
        "solar", "pv", "panel", "battery", "charg", "discharg",
        "grid", "feeder", "transformer", "substation", "intake", "kwh"
    ]

    if any(kw in n_lower for kw in distractor_keywords) and not any(k in n_lower for k in ["solar", "battery", "charg", "grid", "feeder", "transformer"]):
        return {
            "note_index": note_index,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "This note does not affect today's 24-hour energy schedule.",
        }

    if not any(k in n_lower for k in energy_keywords):
        return {
            "note_index": note_index,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "This note does not affect today's 24-hour energy schedule.",
        }

    hours = _parse_time_range(note)
    if not hours:
        return {
            "note_index": note_index,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "No relevant operational time window found.",
        }

    # Solar reduction
    if "solar" in n_lower or "pv" in n_lower or "panel" in n_lower:
        factor = 1.0
        pct_match = re.search(r"(\d+)%", note)
        if pct_match:
            pct = float(pct_match.group(1))
            if "reduction" in n_lower or "drop by" in n_lower or "cut by" in n_lower:
                factor = round((100.0 - pct) / 100.0, 4)
            else:
                factor = round(pct / 100.0, 4)
        elif "half" in n_lower:
            factor = 0.5
        elif "one-fifth" in n_lower:
            factor = 0.2

        return {
            "note_index": note_index,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": hours, "factor": factor},
            "explanation": f"Solar output adjusted by factor {factor} during specified window.",
        }

    # Minimum battery reserve
    if "reserve" in n_lower or "in the battery" in n_lower or "remain" in n_lower or "stored" in n_lower:
        kwh_match = re.search(r"(\d+(?:\.\d+)?)\s*kwh", n_lower)
        pct_match = re.search(r"(\d+)%", note)
        min_kwh = battery.minimum_energy_kwh
        if kwh_match:
            min_kwh = float(kwh_match.group(1))
        elif pct_match:
            pct = float(pct_match.group(1))
            min_kwh = round((pct / 100.0) * battery.capacity_kwh, 4)

        return {
            "note_index": note_index,
            "applies": True,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": hours, "minimum_energy_kwh": min_kwh},
            "explanation": f"Minimum battery reserve of {min_kwh} kWh enforced during specified window.",
        }

    # No charge
    if "not charge" in n_lower or "no charge" in n_lower or ("charg" in n_lower and any(w in n_lower for w in ["disabled", "isolated", "unavailable", "maintenance", "inspection", "outage"])):
        return {
            "note_index": note_index,
            "applies": True,
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": hours},
            "explanation": "Battery charging is disabled during this window.",
        }

    # No discharge
    if "not discharge" in n_lower or "no discharge" in n_lower or ("discharg" in n_lower and any(w in n_lower for w in ["disabled", "unavailable", "relay testing", "protection", "testing"])):
        return {
            "note_index": note_index,
            "applies": True,
            "directive_type": "no_discharge_window",
            "structured_adjustment": {"hours": hours},
            "explanation": "Battery discharging is disabled during this window.",
        }

    # Max grid window
    if any(w in n_lower for w in ["grid", "feeder", "transformer", "substation", "intake"]):
        kwh_match = re.search(r"(\d+(?:\.\d+)?)\s*kwh", n_lower)
        cap = 1e9
        if kwh_match:
            cap = float(kwh_match.group(1))
        return {
            "note_index": note_index,
            "applies": True,
            "directive_type": "max_grid_window",
            "structured_adjustment": {"hours": hours, "max_grid_kwh": cap},
            "explanation": f"Grid import capped at {cap} kWh during specified window.",
        }

    return {
        "note_index": note_index,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": "This note does not affect today's energy schedule.",
    }


def interpret_operator_notes(
    operator_notes: List[str],
    battery: BatteryInput,
) -> List[Dict[str, Any]]:
    """
    Main interpretation function. Calls Gemini 2.0 Flash via google-genai SDK.
    If the API key is not configured or an error occurs, falls back safely to the offline parser.
    """
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set. Using safe fallback interpreter.")
        return [_fallback_interpret_note(note, i, battery) for i, note in enumerate(operator_notes)]

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)

        user_content = {
            "battery_capacity_kwh": battery.capacity_kwh,
            "battery_minimum_energy_kwh": battery.minimum_energy_kwh,
            "operator_notes": [
                {"note_index": i, "text": note} for i, note in enumerate(operator_notes)
            ],
        }

        prompt = (
            f"Context: {json.dumps(user_content)}\n\n"
            f"Interpret each note in operator_notes and return a JSON array of directive interpretations."
        )

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )

        raw_text = response.text.strip()
        # Strip potential markdown fences if present
        if raw_text.startswith("```"):
            lines = raw_text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            raw_text = "\n".join(lines).strip()

        parsed = json.loads(raw_text)
        if isinstance(parsed, list):
            return parsed
        elif isinstance(parsed, dict) and "directives" in parsed and isinstance(parsed["directives"], list):
            return parsed["directives"]
        elif isinstance(parsed, dict) and "directive_interpretation" in parsed and isinstance(parsed["directive_interpretation"], list):
            return parsed["directive_interpretation"]
        else:
            logger.error(f"Unexpected JSON structure from Gemini: {raw_text[:200]}")
            return [_fallback_interpret_note(note, i, battery) for i, note in enumerate(operator_notes)]

    except Exception as e:
        logger.error(f"Error calling Gemini API: {type(e).__name__}: {e}. Falling back safely.")
        return [_fallback_interpret_note(note, i, battery) for i, note in enumerate(operator_notes)]
