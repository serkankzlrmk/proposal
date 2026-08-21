# Backend Design

> Target architecture and current layout for Proposal Studio's backend.
> Principles: Sightline independence + `reliefweb_api` compatibility + Waku
> LLM-Ops tracing pattern.

## 1. Architecture overview

```
┌──────────────────────────────────────────────────────────────┐
│                     PRESENTATION (API)                        │
│  blueprints/proposal_api.py · call_ingest_api.py             │
│  blueprints/step3_logframe.py · step4_budget_risk.py         │
├──────────────────────────────────────────────────────────────┤
│                        DOMAIN (ENGINE)                        │
│  donor_rules · yaml_rules · generator · verifier · advisor    │
│  smart_parser · call_ingest · evidence · advisor_context      │
│  → deterministic rules + LLM drafting (manifest-aware)        │
├──────────────────────────────────────────────────────────────┤
│                      INFRASTRUCTURE (ADAPTERS)                │
│  db.py (SQLite) · _call_llm (OpenRouter) · typst_engine       │
│  ops/tracing.py (usage ledger) · engine/evidence.py (bridge)  │
└──────────────────────────────────────────────────────────────┘
```

Hard dependency rule: `presentation → engine ← infrastructure`. The engine
never imports infrastructure directly; LLM calls go through `_call_llm` in
`engine/generator.py`, and the evidence bridge is isolated in
`engine/evidence.py` (the only file that touches Sightline paths).

## 2. Current module map

```
app.py                      Flask app (:5002) + blueprint registration
config.py                   Env settings (port, LLM endpoint, keys)
db.py                       SQLite: proposals, proposal_reviews, call_drafts
                            WAL mode, idempotent migrations, FSM lock guard
blueprints/
  proposal_api.py           CRUD, analyze, generate-*, verify, references,
                            advisor chat, full-summary, PDF export (eligibility gate)
  call_ingest_api.py        Call upload (multi-file), drafts, publish/reject, brief
  step3_logframe.py         SMART analyze, lock, generate
  step4_budget_risk.py      Risk/budget analyze, lock, summary, per-subtab agents
engine/
  models.py                 Pydantic schemas (DonorManifest, Logframe*, Risk*, Budget*)
  yaml_rules.py             Manifest loader + deterministic 5-criterion scoring
  donor_resolver.py         Shared donor-id resolution (call-ingested ↔ builtin)
  donor_rules.py            Legacy Python donor profiles (backward compat)
  call_ingest.py            Multi-format extraction, anti-hallucination gates
  generator.py              ToC/Logframe/Narrative LLM generation + fallbacks
  smart_parser.py           SMART indicator parsing + hardening
  advisor.py / advisor_context.py   Advisor chat + token-efficient context
  verifier.py               Blind verifier (LLM-as-a-judge, separate model)
  evidence.py               Sightline bridge (ReliefWeb/HDX, zero-crash)
typst_engine/compiler.py    Typst PDF (real score block, narrative, risk, budget)
donors/*.yaml               Donor manifests (data, not code)
ops/tracing.py              LLM usage ledger (JSONL: tokens, cost, latency)
```

## 3. Data model

- `proposals` — one row per proposal; step content in JSON columns
  (`context_data`, `toc_data`, `logframe_data`, `narrative_data`,
  `budget_data`, `review_data`, `references_data`, `locked_steps`).
- `proposal_reviews` — blind verifier audit history (cascade-deleted).
- `call_drafts` — call ingestion review pipeline (`review | published |
  rejected`), with `documents_json` + `brief` columns.

## 4. FSM / lock contract

- Steps flow 1→5; each step transitions `draft → analyzed → locked` via
  `lock_step()`.
- `update_proposal` enforces the guard: content change to a locked step raises
  `ProposalLockedError` → API 409 `{code: "STEP_LOCKED"}`. Byte-identical
  writes pass silently (autosave resends whole payloads).
- PDF export is the final gate: `AUTOMATIC_REJECTION` → 403
  `PDF_LOCKED_ELIGIBILITY`.

## 5. Observability

Every LLM call (generate, verify, advisor, call extract, brief, identity)
appends one line to `ops/usage.jsonl` via `ops/tracing.py`:
model, action, chars, tokens, estimated cost, latency. The ledger is
append-only and never blocks the pipeline.

## 6. Not implemented (target, future phases)

The following were part of the earlier target design and are intentionally
deferred: `services/` orchestration layer, `adapters/llm_client.py` (pooled
client), `data_sources/` full client set (reliefweb/hdx/gdacs/fts as standalone
clients), `ops_api.py` dashboards. Current implementation covers the same
behaviors through `engine/` + `blueprints/` directly.
