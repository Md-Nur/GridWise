# GridWise LLM — Project Context Document
### For use with Antigravity (or any agentic coding tool)

This document summarizes the canonical spec for the BUP CSE Fest 2026 "GridWise" hackathon challenge, plus the zero-cost tech stack decisions already made. Paste this whole file (or the "Build Prompt" section at the bottom) into Antigravity as project context before you start building.

---

## 1. What We're Building

One HTTP API service with two endpoints that:
1. Receives a 24-hour campus energy scenario + 1–3 natural-language operator notes
2. Uses an **LLM (Gemini API, free tier)** to interpret each note into a structured directive
3. Runs the structured directives through **deterministic guardrail validation**
4. Feeds valid directives into a **linear-programming optimizer (PuLP)** that returns the lowest-cost valid 24-hour battery/grid schedule
5. Returns both the interpretation and the schedule as one JSON response

Pipeline: `Energy Data + Operator Notes → LLM Interpreter → Guardrail Validator → Math Optimizer → Final API Response`

**Non-negotiable rule:** the LLM must actually produce the structured `directive_interpretation` output. Using an LLM only for `plan_summary` or cosmetic text does NOT satisfy the requirement and disqualifies the submission.

---

## 2. Tech Stack (zero cost)

| Layer | Choice | Why |
|---|---|---|
| LLM | **Gemini API free tier** (gemini-2.0-flash or gemini-2.5-flash-lite) | Free key, no billing card, fast structured JSON output |
| Backend | **FastAPI** (Python) | Async, easy JSON schema validation, fast to build |
| Guardrails | Plain Python (pydantic models + manual checks) | No dependency needed, fully deterministic |
| Optimizer | **PuLP** with bundled CBC solver | Free LP solver, handles this problem as a linear program |
| Hosting | **Hugging Face Spaces (Docker SDK)** | Free public HTTPS URL, native Docker support, doubles as the Docker fallback image deliverable |
| Repo | GitHub, created after question reveal, private → public after deadline | Per contest rules |

---

## 3. API Contract (EXACT — do not deviate)

### `GET /health`
Returns HTTP 200:
```json
{ "status": "ok" }
```
Must be ready within 60 seconds of service start.

### `POST /optimize-energy`
Accepts one JSON object, returns one JSON object. Must respond within **30 seconds** (hard timeout = failure). Target p95 latency ≤ 5s for full performance points.

**Request fields:**
- `scenario_id` (string)
- `operator_notes` (array of 1–3 non-empty strings)
- `hours` (array of exactly 24 entries): `hour` (0–23), `demand_kwh`, `solar_kwh`, `tariff_bdt_per_kwh`
- `battery` (object): `capacity_kwh`, `initial_energy_kwh`, `minimum_energy_kwh`, `max_charge_kwh_per_hour`, `max_discharge_kwh_per_hour`

**Response fields:**
- `scenario_id` (echo back)
- `directive_interpretation` (array, one entry per note, in `note_index` order 0..N-1): `note_index`, `applies`, `directive_type`, `structured_adjustment`, `explanation`
- `hourly_plan` (array of exactly 24 entries): `hour`, `grid_kwh`, `solar_used_kwh`, `battery_action` (`charge`/`discharge`/`idle`), `battery_kwh`, `battery_energy_after_kwh`
- `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh` (must match values recalculated from `hourly_plan`)
- `plan_summary` (short human-readable string)

**HTTP codes:** 200 success · 400 malformed/invalid JSON · 422 optional semantic validity failure · 500 controlled internal error (never leak stack traces or secrets).

---

## 4. Supported Directive Types (the LLM must classify every note into exactly one of these)

| directive_type | Meaning | structured_adjustment shape |
|---|---|---|
| `solar_reduction` | Usable solar reduced in listed hours | `{"hours":[...], "factor": number}` — factor = fraction **remaining** (an 80% reduction → factor = 0.2) |
| `minimum_battery_reserve` | Battery must stay ≥ X kWh in listed hours | `{"hours":[...], "minimum_energy_kwh": number}` |
| `no_charge_window` | Charging disabled in listed hours | `{"hours":[...]}` |
| `no_discharge_window` | Discharging disabled in listed hours | `{"hours":[...]}` |
| `max_grid_window` | Grid import capped in listed hours | `{"hours":[...], "max_grid_kwh": number}` |
| `no_op` | Note is irrelevant to the schedule | `null` |

**Parsing rules the LLM prompt must enforce:**
- Time windows are **start-inclusive, end-exclusive**: "1 PM to 3 PM" → hours `[13, 14]` (NOT `[13,14,15]`)
- `hours` arrays: unique integers, 0–23, ascending order
- `applies = false` is allowed **only** for `no_op`; every other directive type requires `applies = true`
- Notes will be **paraphrased** in hidden test cases — do not hardcode phrase matching, rely on the LLM's language understanding
- The LLM must NOT invent demand, tariff, battery parameters, or any directive type not in this table

---

## 5. Deterministic Guardrails (run AFTER the LLM call, BEFORE optimization)

Validate every LLM output against:
- `directive_type` is one of the 6 allowed values
- `note_index` correctly maps to an existing note; every note appears exactly once, in order
- `hours` entries are unique, ascending, within 0–23
- `solar_reduction.factor` is between 0 and 1 inclusive
- Reserve / grid-cap values are finite and non-negative; reserve ≤ battery capacity
- `applies`/`structured_adjustment` semantics match the no_op rule above

If LLM output is malformed or invalid: **fail safely** (log it, treat as `no_op` or return a controlled error) — never crash, never invent a new directive type, never pass raw untrusted output straight to the optimizer.

---

## 6. Optimization Model (PuLP linear program, per scenario)

**Objective:** minimize `Σ grid_kwh[h] * tariff_bdt_per_kwh[h]` for h = 0..23

**Hard constraints, every hour:**
- Energy balance: `grid_kwh + solar_used_kwh + battery_discharge_kwh = demand_kwh + battery_charge_kwh`
- `0 ≤ solar_used_kwh ≤ effective_solar_kwh[h]` (effective solar = base solar × any active `solar_reduction` factor)
- `minimum_energy_kwh ≤ battery_energy_after_kwh ≤ capacity_kwh` (raised by any active `minimum_battery_reserve` directive)
- `battery_charge_kwh ≤ max_charge_kwh_per_hour`, `battery_discharge_kwh ≤ max_discharge_kwh_per_hour`
- Charge = 0 in any `no_charge_window` hour; discharge = 0 in any `no_discharge_window` hour
- `grid_kwh ≤ max_grid_kwh` in any `max_grid_window` hour
- **End-of-day neutrality:** `battery_energy_after_kwh[23] == initial_energy_kwh`
- All values non-negative and finite

Use the **Public Sample Cases JSON** (10 worked examples) to validate your pipeline locally before touching anything else — they cover every directive type individually and in combination, plus distractor notes.

---

## 7. Deliverables Checklist

1. Deployed public API (both endpoints reachable, no auth/VPN, stable for the full 4-hour window)
2. GitHub repo — create AFTER question reveal, private during event, public after deadline
3. `README.md` — setup, exact run command, env var names (e.g. `GEMINI_API_KEY`), model/provider used, LLM's role in the pipeline, guardrail description, optimizer/solver used, `/health` and `/optimize-energy` curl examples, public-sample test command, dependencies, known limitations, **no secret values committed**
4. Docker fallback image — pullable (e.g. via HF Spaces registry or Docker Hub), exposes documented port, binds `0.0.0.0`, no baked-in secrets, verified `docker run` command
5. 3-minute video — problem, architecture, LLM→guardrail→optimizer flow, how to run/test (tie-break scoring only)

---

## 8. Scoring Reminder (100 pts, automated)

LLM Interpretation (25) + Directive Application/Correctness (25) + Optimization Quality (10) + API Contract (10) + Performance/Reliability (10) + Deployment/Docker (10) + Documentation (10).

**Key principle:** interpretation and downstream application are scored separately. A cheap schedule built on a wrong or ignored directive gets **zero optimization credit** for that case — correctness gates cost. Get the pipeline right before chasing lower cost.

---

## 9. Build Prompt for Antigravity

Copy everything below this line into Antigravity as your build instruction:

```
Build a Python FastAPI service called "GridWise" for a hackathon challenge. 
Follow this exact spec (full details in the attached context document):

ENDPOINTS:
- GET /health → returns {"status": "ok"}, HTTP 200
- POST /optimize-energy → accepts a 24-hour energy scenario + 1-3 operator notes, 
  returns directive interpretation + optimized 24-hour schedule

ARCHITECTURE (must be built in this order, each stage strictly separated):
1. Pydantic models for the exact request/response schema (see context doc section 3)
2. A Gemini API client (use the free-tier gemini-2.0-flash model, API key from 
   env var GEMINI_API_KEY) that takes each operator note and returns structured 
   JSON matching one of the 6 directive types in context doc section 4. Prompt 
   the model to return ONLY valid JSON, one object per note, with note_index, 
   applies, directive_type, structured_adjustment, explanation. Give it few-shot 
   examples covering all 6 directive types and the whole-hour/factor conventions.
3. A pure-Python deterministic guardrail validator (section 5) that checks every 
   field of the LLM's output before anything touches the optimizer. Invalid or 
   malformed LLM output must fail safely (coerce to no_op or return a controlled 
   error) -- never crash, never pass unvalidated data to the optimizer.
4. A PuLP-based linear program (section 6) that takes the validated directives 
   plus the 24-hour demand/solar/tariff arrays and battery parameters, and 
   returns the minimum-cost valid 24-hour schedule satisfying every constraint, 
   including end-of-day battery neutrality.
5. Assemble the final response matching the exact schema in section 3, with 
   total_grid_kwh/total_cost_bdt/peak_grid_kwh recalculated from the hourly_plan 
   (never returned independently).

REQUIREMENTS:
- The LLM call must be the actual interpretation step -- not decorative.
- Handle malformed input JSON with HTTP 400. Handle internal errors with a 
  controlled HTTP 500 that never leaks stack traces or the API key.
- Response time for /optimize-energy must stay well under 30 seconds.
- No hardcoded phrase-matching as a substitute for the LLM call -- hidden test 
  notes will be paraphrased versions of the same directives.
- Include a requirements.txt (fastapi, uvicorn, pydantic, pulp, google-generativeai 
  or google-genai, python-dotenv).
- Include a Dockerfile that exposes port 7860 (Hugging Face Spaces default), 
  binds to 0.0.0.0, and does not bake in any secret values -- read GEMINI_API_KEY 
  from environment/Space secrets only.
- Include a README.md documenting setup, the exact run command, required env 
  vars, the LLM/guardrail/optimizer architecture, curl examples for both 
  endpoints, and how to run the 10 public sample cases against the service.
- Write a small test script that loads the public sample cases JSON and POSTs 
  each one to /optimize-energy locally, printing pass/fail per case based on 
  schema validity and constraint satisfaction (not exact byte match).

Start by scaffolding the project structure and the Pydantic schemas first, 
then build stage by stage in the order above, testing each stage against the 
public sample cases before moving to the next.
```

---

## 10. Quick Reference While Building

- Prioritize in this order: exact API contract → LLM interpretation → guardrails → directive application/energy correctness → optimization quality → deployment/Docker → docs → video (video last, it carries no base points)
- Numeric tolerance for judging: 0.01 kWh / 0.01 BDT
- Test against all 10 public sample cases before deploying
- Don't forget the keep-alive concern if you switch off HF Spaces later — HF Spaces itself doesn't sleep the same way free Render does, so this is a non-issue with the current stack, but revisit if hosting changes
