# Proposal Studio

The **grant proposal production pipeline** for the Sightline project. It reads a
donor call (call), extracts its requirements, and produces a humanitarian grant
proposal that follows those rules — with human approval and editing at every step.

> Developed independently from the Sightline repo; integration note:
> `docs/EVIDENCE_BRIDGE.md`.

---

## What It Does

End-to-end flow:

```
Donor call documents (pdf/docx/md)
        │
        ▼
Call Ingestion ──► summary + requirements extracted ──► human approval
        │                                                  │
        ▼                                                  ▼
  Proposal creation ◄── donors/<call_id>.yaml (manifest, auto-loaded by engine)
        │
        ▼
  Step 1  Context & Targeting      (AI draft + manual editing)
  Step 2  Theory of Change         (AI generation + manual node add/remove)
  Step 3  4x4 Logframe             (AI generation + manual row add/remove, GOAL/OUTCOME/OUTPUT/ACTIVITY)
  Step 4  Narrative / Risk / Budget (3 sub-tabs, each with its own agent)
  Step 5  Blind Verifier + PDF     (deterministic scoring + Typst PDF)
```

Scoring is **deterministic** (the LLM never decides): it is computed over 5
criteria from the donor manifest, and a hard eligibility violation produces an
automatic rejection.

---

## Setup & Run

```bash
# Dependencies (Python 3.11)
uv venv --python 3.11
uv pip install --python .venv/bin/python -r requirements.txt

# Server
PYTHONPATH="" VIRTUAL_ENV=$(pwd)/.venv .venv/bin/python app.py
# → http://127.0.0.1:5002

# Tests
.venv/bin/python -m pytest tests/ -q
```

Requirements: `flask`, `flask-cors`, `httpx`, `python-dotenv`, `typst`, `pymupdf`,
`requests`, `langchain`, `pypdf`.

---

## Project Structure

```
app.py                      Flask server (:5002) + blueprint registration
config.py                   Environment settings (port, LLM endpoint, keys)
db.py                       SQLite layer: proposal CRUD, step locking (FSM),
                            audit log (proposal_reviews), call draft table

blueprints/
  proposal_api.py           Proposal CRUD, AI generation endpoints, full-summary,
                            PDF export (eligibility gate), advisor chat
  call_ingest_api.py        Call upload (multi-file), draft approve/reject, brief
  step3_logframe.py         Logframe analyze (SMART), lock, generate
  step4_budget_risk.py      Risk matrix / budget analysis, lock, sub-tab agents

engine/
  models.py                 Pydantic schemas: DonorManifest, LogframeIndicator,
                            RiskMatrixItem, BudgetItem, PseaCommitments
  yaml_rules.py             Donor manifest loader + deterministic scoring engine
                            (5 criteria, hard gates, linear budget penalty)
  donor_resolver.py         Donor id resolution (call-ingested ↔ builtin)
  donor_rules.py            Legacy Python donor profiles (backward compat)
  call_ingest.py            Call document → requirement extraction, anti-hallucination
                            gate verification, manifest generation
  generator.py              ToC / Logframe / Narrative generation (manifest-aware),
                            structured logframe → matrix projection
  smart_parser.py           SMART indicator validation + deterministic hardening
  advisor.py                Interactive advisor (small-talk fast path + LLM)
  advisor_context.py        Advisor context schema (superset)
  verifier.py               Blind verifier (LLM-as-a-judge, separate model)
  evidence.py               Sightline bridge: uses ReliefWeb/HDX tools at runtime
                            WITHOUT copying the code

typst_engine/compiler.py    Typst PDF generation (narrative, logframe, risk,
                            budget, real score block)
donors/*.yaml               Donor manifests (OCHA, USAID, EU, Generic +
                            call-ingested ones)
ops/tracing.py              LLM usage ledger (JSONL: tokens, cost, latency)
templates/ + static/        SPA (5-step wizard + landing + donor call section)
docs/
  EVIDENCE_BRIDGE.md        Sightline integration/migration note
  SYSTEM_DESIGN.md          System design
  ARCHITECTURE.md           Architecture principles
  BACKEND_DESIGN.md         Backend design notes
```

---

## Donor Manifest System

Each donor is a declarative root-level YAML; adding a donor = 1 file:

```yaml
donor_id: ocha_cbpf
display_name: OCHA Country-Based Pooled Funds
currency: USD
overhead_cap_percent: 7
mandatory_keywords: [PSEA, Sphere standards, protection mainstreaming]
hard_eligibility_gates:
  sadd_disaggregation_mandatory: true
mandatory_sections: [Executive Summary, Context, ...]
```

The scoring engine (`engine/yaml_rules.py`) loads this manifest:

- **5 criteria** — section_coverage (30), source_citations (25), smart_criteria (20),
  donor_keywords (15), budget_alignment (10) — total 100
- **Hard gates** — a violated quota/condition produces `AUTOMATIC_REJECTION`
  regardless of score; PDF export is locked with 403
- **Linear budget penalty** — `10 − (overage × 5)` for overhead above the cap
- **Zero-crash** — a missing/broken rule never crashes; 0 points + `WARNING_MISSING_RULE`

## Call Ingestion (Human-Approved Rule Extraction)

1. **Upload** — pdf/docx/md, **multi-file** (guidelines + form + annex in one go)
2. **Extraction** — summary + requirements + deadline + budget rule (TRY/cap) + hard gates
3. **Anti-hallucination** — every gate claimed by the LLM MUST be evidenced in the
   document text; without evidence it never enters the manifest (deterministic check)
4. **Human approval** — brief ("what it says, what it wants, what to do") + Publish/Reject
5. **Manifest** — written to `donors/<call_id>.yaml`; the engine picks it up
   automatically on the next request (glob-based dynamic loading)

## Sightline Evidence Bridge

`engine/evidence.py` calls Sightline's `reliefweb_api/` tools (ReliefWeb sitrep
search, HDX country/refugee/IDP data) at runtime **without copying the code**:

- Sightline's root is added to `sys.path`; modules are imported as a package
- The HDX key is read from Sightline's own `.env` (never copied into this repo)
- Collected evidence enters the prompt as `[ref: SIGHTLINE_*]` citations; those
  citations count as grounded in the citation registry (source_citations score)
- If Sightline is missing/unavailable, every call returns `None` — the pipeline
  never breaks

What to do at migration time: `docs/EVIDENCE_BRIDGE.md` (checklist).

---

## Frontend

Single-page app: landing (proposal list + delete) → "+ New" pop-up
(published call / ready donor / upload new call) → 5-step wizard + a separate
Donor Call section at the end. A 💬 floating advisor sits bottom-right
(pop-up on click). Every step supports both AI generation and manual editing;
the sub-tabs (Risk, Budget) have their own agent buttons.

## LLM Usage (only where needed)

- Call requirement extraction and brief generation
- ToC / Logframe / Narrative draft generation
- Risk / Budget draft generation (sub-tab agents)
- Blind verifier (separate model, chain-of-thought never shared)
- Advisor chat

Every LLM call is written to `ops/usage.jsonl` (tokens, cost, latency). The
pipeline's **critical decisions** (score, eligibility, gate verification) always
live in deterministic code.

## Test Coverage

| File | Topic |
|---|---|
| `test_yaml_rules.py` | Manifest loading, scoring, hard gates, budget penalty |
| `test_call_ingest.py` | Multi-format extraction, anti-hallucination, brief, API flow |
| `test_step3_logframe.py` | Structured logframe, SMART parser, lock (FSM) |
| `test_proposal.py` | End-to-end flow, blind verifier, PDF |
