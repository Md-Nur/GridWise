# GridWise — LLM-Assisted Smart Campus Energy Optimization Service

**BUP CSE Fest 2026 Hackathon · Preliminary Round**

GridWise is an automated smart campus energy optimization service that pairs generative language intelligence with deterministic operations research. It receives 24-hour campus energy forecasts (demand, solar availability, and grid tariffs), battery parameters, and 1–3 natural-language campus operator notes. It interprets temporary operational conditions using **Google Gemini 2.0 Flash**, verifies the directives through **deterministic guardrails**, and computes the optimal cost-minimizing battery schedule using **PuLP (CBC solver)**.

---

## 1. System Architecture & Processing Pipeline

The system is architected in strictly separated sequential stages to guarantee that language flexibility never compromises mathematical feasibility:

```
[24-Hour Energy Data + Operator Notes]
                    │
                    ▼
┌──────────────────────────────────────────────┐
│  Stage 1: LLM Directive Interpreter          │
│  (Google Gemini 2.0 Flash / Free Tier)       │
│  - Parses notes into structured directives   │
│  - Normalizes hours & factors                │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│  Stage 2: Deterministic Guardrail Validator  │
│  (Pure Python / Zero Hallucination Risk)     │
│  - Enforces allowed directive types & order  │
│  - Bounds hours [0..23] & solar factors [0..1]│
│  - Coerces corrupt/malformed items to no_op  │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│  Stage 3: PuLP MILP Mathematical Optimizer   │
│  (Coin-or CBC Solver)                        │
│  - Enforces hourly energy balance            │
│  - Respects battery bounds & rate limits     │
│  - Enforces end-of-day battery neutrality    │
│  - Minimizes total grid electricity cost     │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│  Stage 4: Schedule Replayer & Metric Verifier │
│  - Recalculates total_grid_kwh, total_cost   │
│  - Replays and verifies constraint bounds    │
│  - Assembles exact JSON API response         │
└──────────────────────────────────────────────┘
```

### Role of the LLM vs. Deterministic Code
- **LLM's Role**: Natural language understanding only. The LLM converts free-text operator notes (including paraphrases, synonyms, and percentages) into a machine-checkable `directive_interpretation` with exact start-inclusive, end-exclusive hours `[start, end)`.
- **Guardrails' Role**: Strictly validates the LLM output against the canonical schema and domain physics before any value reaches the optimizer. If an LLM response is malformed, it safely fails to `no_op` rather than crashing or guessing.
- **Optimizer's Role**: Mathematical optimization. The optimizer finds the global cost-optimal schedule satisfying all campus physics, battery charge neutrality, and active operator restrictions.

---

## 2. Supported Directives

| Directive Type | Meaning | Required `structured_adjustment` Shape |
|---|---|---|
| `solar_reduction` | Usable solar generation reduced in listed hours | `{"hours": [int, ...], "factor": number}` (where `factor` is the usable fraction **remaining**, e.g., 80% reduction → `factor = 0.2`) |
| `minimum_battery_reserve` | Battery energy must stay ≥ stated level in listed hours | `{"hours": [int, ...], "minimum_energy_kwh": number}` |
| `no_charge_window` | Battery charging is forbidden in listed hours | `{"hours": [int, ...]}` |
| `no_discharge_window` | Battery discharging is forbidden in listed hours | `{"hours": [int, ...]}` |
| `max_grid_window` | Grid import capped in listed hours | `{"hours": [int, ...], "max_grid_kwh": number}` |
| `no_op` | Note does not affect today's 24-hour energy schedule | `null` (`applies: false`) |

---

## 3. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | **Yes** (for live LLM) | `""` | Google Gemini API Key (free tier). If omitted, safe fallback mode handles known patterns. |
| `GEMINI_MODEL` | No | `gemini-2.0-flash` | Gemini model name |
| `PORT` | No | `7860` | Service port (7860 matches Hugging Face Spaces default) |
| `HOST` | No | `0.0.0.0` | Host IP binding |

Create a `.env` file from the provided example:
```bash
cp .env.example .env
# Edit .env and insert your GEMINI_API_KEY
```

> **Security Note:** Never commit `.env` or API keys. `.gitignore` is pre-configured to ignore all environment files.

---

## 4. Local Setup and Quickstart

### Prerequisites
- Python 3.10+
- [`uv`](https://github.com/astral-sh/uv) (recommended) or standard `pip`

### Step 1: Clone Repository & Create Virtual Environment
Using `uv`:
```bash
# Create virtual environment with uv
uv venv

# Activate virtual environment
source .venv/bin/activate

# Install all dependencies with uv
uv pip install -r requirements.txt
```

*(Alternative with standard pip: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`)*

### Step 2: Configure Environment
```bash
export GEMINI_API_KEY="your_actual_gemini_api_key"
```

### Step 3: Start the Service
```bash
uvicorn app.main:app --host 0.0.0.0 --port 7860
```
The service will be ready at `http://0.0.0.0:7860` in under 2 seconds.

---

## 5. API Endpoints and `curl` Examples

### 1. `GET /health`
Readiness probe for the judging harness.

**Request:**
```bash
curl -i http://localhost:7860/health
```

**Response (HTTP 200):**
```json
{
  "status": "ok"
}
```

---

### 2. `POST /optimize-energy`
Main optimization endpoint.

**Request:**
```bash
curl -X POST http://localhost:7860/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "SAMPLE-01",
    "operator_notes": [
      "Facilities will wash the rooftop solar panels from noon until 2 PM. During cleaning, usable solar should be treated as roughly 25% of the forecast.",
      "The sports office moved next months registration deadline."
    ],
    "hours": [
      {"hour": 0, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 1, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 2, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 3, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 4, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 5, "demand_kwh": 95, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 6, "demand_kwh": 110, "solar_kwh": 5, "tariff_bdt_per_kwh": 8},
      {"hour": 7, "demand_kwh": 130, "solar_kwh": 20, "tariff_bdt_per_kwh": 10},
      {"hour": 8, "demand_kwh": 150, "solar_kwh": 50, "tariff_bdt_per_kwh": 12},
      {"hour": 9, "demand_kwh": 165, "solar_kwh": 90, "tariff_bdt_per_kwh": 14},
      {"hour": 10, "demand_kwh": 175, "solar_kwh": 130, "tariff_bdt_per_kwh": 16},
      {"hour": 11, "demand_kwh": 180, "solar_kwh": 160, "tariff_bdt_per_kwh": 16},
      {"hour": 12, "demand_kwh": 185, "solar_kwh": 180, "tariff_bdt_per_kwh": 15},
      {"hour": 13, "demand_kwh": 180, "solar_kwh": 170, "tariff_bdt_per_kwh": 14},
      {"hour": 14, "demand_kwh": 170, "solar_kwh": 140, "tariff_bdt_per_kwh": 13},
      {"hour": 15, "demand_kwh": 165, "solar_kwh": 90, "tariff_bdt_per_kwh": 14},
      {"hour": 16, "demand_kwh": 170, "solar_kwh": 45, "tariff_bdt_per_kwh": 18},
      {"hour": 17, "demand_kwh": 185, "solar_kwh": 10, "tariff_bdt_per_kwh": 22},
      {"hour": 18, "demand_kwh": 205, "solar_kwh": 0, "tariff_bdt_per_kwh": 28},
      {"hour": 19, "demand_kwh": 215, "solar_kwh": 0, "tariff_bdt_per_kwh": 30},
      {"hour": 20, "demand_kwh": 205, "solar_kwh": 0, "tariff_bdt_per_kwh": 26},
      {"hour": 21, "demand_kwh": 175, "solar_kwh": 0, "tariff_bdt_per_kwh": 18},
      {"hour": 22, "demand_kwh": 135, "solar_kwh": 0, "tariff_bdt_per_kwh": 10},
      {"hour": 23, "demand_kwh": 105, "solar_kwh": 0, "tariff_bdt_per_kwh": 7}
    ],
    "battery": {
      "capacity_kwh": 220,
      "initial_energy_kwh": 110,
      "minimum_energy_kwh": 40,
      "max_charge_kwh_per_hour": 50,
      "max_discharge_kwh_per_hour": 50
    }
  }'
```

**Response (HTTP 200):**
```json
{
  "scenario_id": "SAMPLE-01",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {
        "hours": [12, 13],
        "factor": 0.25
      },
      "explanation": "Solar availability is reduced to 25% during the panel-cleaning window."
    },
    {
      "note_index": 1,
      "applies": false,
      "directive_type": "no_op",
      "structured_adjustment": null,
      "explanation": "This note does not affect today's 24-hour energy schedule."
    }
  ],
  "hourly_plan": [
    {
      "hour": 0,
      "grid_kwh": 90.0,
      "solar_used_kwh": 0.0,
      "battery_action": "idle",
      "battery_kwh": 0.0,
      "battery_energy_after_kwh": 110.0
    },
    ...
  ],
  "total_grid_kwh": 2692.5,
  "total_cost_bdt": 38365.0,
  "peak_grid_kwh": 175.0,
  "plan_summary": "Optimized 24-hour schedule applying directives (solar_reduction). Total grid import is 2692.5 kWh at a total cost of 38365.0 BDT (peak 175.0 kWh), while preserving end-of-day battery neutrality."
}
```

---

## 6. Running the 10 Public Sample Cases

GridWise comes with an automated test suite verifying all 10 sample cases against the specification:

```bash
# In-process validation (does not require external server)
python test_samples.py

# Or against a running server:
python test_samples.py --url http://localhost:7860
```

### Verification Criteria Checked:
- HTTP 200 response with exact schema conformance
- Echoed `scenario_id`
- Directive interpretation exact matching (type, applies, hours, factor/reserve/cap)
- Physical constraint satisfaction (demand balance, solar limit, battery limits)
- End-of-day battery neutrality ($E_{23} = E_{initial}$)
- Total cost equality within tolerance (0.01 BDT)

---

## 7. Docker Fallback Image

GridWise provides a self-contained, multi-platform Docker container matching the Hugging Face Spaces Docker SDK specification.

### Build the Image:
```bash
docker build -t gridwise:latest .
```

### Run the Container:
```bash
docker run -p 7860:7860 -e GEMINI_API_KEY="your_api_key_here" gridwise:latest
```

### Verify Container Health:
```bash
curl http://localhost:7860/health
```

---

## 8. Dependencies and External Libraries Credited

- **FastAPI**: Modern, high-performance web framework for the API endpoints.
- **Uvicorn**: Lightning-fast ASGI server.
- **Pydantic v2**: High-speed, strict schema validation and serialization.
- **PuLP & Coin-or CBC**: Mathematical linear programming modeling framework and solver.
- **Google GenAI SDK (`google-genai`)**: Official Google Gemini SDK for Gemini 2.0 Flash structured generation.
- **`uv` by Astral**: Blazing fast Python environment and package manager.
- **HTTPX**: Robust asynchronous and synchronous HTTP client for test execution.

---

## 9. Known Limitations and Design Choices

1. **Deterministic Fallback Engine**: If the Gemini API key is missing or internet connectivity is disrupted during local testing, the service automatically switches to a deterministic heuristic parser to ensure tests never crash. In production judging, `GEMINI_API_KEY` ensures full generative linguistic flexibility.
2. **End-of-day Battery Neutrality**: The LP formulation strictly enforces $E_{23} = E_{initial}$ so battery energy cannot be consumed as an unreplenished free resource.
3. **Whole-Hour Interval Rule**: All directive intervals follow the canonical start-inclusive, end-exclusive convention: "1 PM to 3 PM" is mapped to hours `[13, 14]`.
