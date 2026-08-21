# Sightline Proposal Studio

Turn donor call documents into **submission-ready grant proposals** — with
human approval at every step. Part of the [Sightline](https://github.com/serkankzlrmk/sightline)
ecosystem.

```
Donor call (pdf/docx/md) → rule extraction → human approval
        → donors/<call_id>.yaml → 5-step design wizard
        → deterministic compliance scoring → blind verifier → Typst PDF
```

## Why it works

The system never lets the AI decide anything important. **The LLM drafts;
a deterministic engine decides.**

- **Rules are data, not code.** A donor call becomes one YAML manifest
  (`donors/*.yaml`) — sections, keywords, budget caps, eligibility gates.
  Adding a donor = one file.
- **5-criterion scoring** (100 pts) with hard eligibility gates: a violated
  quota is an automatic rejection, regardless of text score.
- **Anti-hallucination**: every rule the LLM claims to extract from a call
  must be *evidenced in the call text* — otherwise it is dropped.
- **Human in the loop**: extracted rules are reviewed and approved before a
  single word of the proposal is written.

## Quick start

```bash
uv venv --python 3.11
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env          # add OPENROUTER_API_KEY
PYTHONPATH="" VIRTUAL_ENV=$(pwd)/.venv .venv/bin/python app.py
# → http://127.0.0.1:5002
```

Run the tests:

```bash
.venv/bin/python -m pytest tests/ -q
```

> `SIGHTLINE_ROOT` is optional. Set it to a local Sightline checkout to ground
> citations with live ReliefWeb/HDX evidence; unset, the pipeline runs fully
> standalone.

## The pipeline

| Stage | What happens |
|---|---|
| **Call ingestion** | Upload call documents (PDF/DOCX/MD, multiple at once) → summary, requirements, deadline, budget rules, gates → human review → manifest |
| **Step 1 · Context** | Target geography, humanitarian situation, needs, beneficiaries (AI draft + manual edit) |
| **Step 2 · ToC** | Causal pathway — inputs → activities → outputs → outcomes → impact |
| **Step 3 · Logframe** | 4×4 results matrix (GOAL/OUTCOME/OUTPUT/ACTIVITY), SMART validation |
| **Step 4 · Design** | Narrative sections, 5×5 risk matrix, itemized budget with overhead-cap check |
| **Step 5 · Verify** | Deterministic donor score + eligibility gates → blind verifier audit → PDF (locked if rejected) |

Every step supports **both** AI drafting and full manual editing, with a
floating advisor for on-demand guidance.

## Project layout

```
app.py / config.py / db.py     Flask (:5002), env, SQLite + step-lock FSM
blueprints/                     API: proposals, call ingestion, steps 3 & 4
engine/                         Deterministic core + LLM drafting layer
  yaml_rules.py                   manifest loader + 5-criterion scoring
  call_ingest.py                extraction + anti-hallucination gate checks
  generator.py                  ToC / logframe / narrative generation
  advisor.py · verifier.py      advisor chat · blind verifier
  evidence.py                   optional Sightline bridge (ReliefWeb/HDX)
donors/*.yaml                   donor manifests (data, not code)
typst_engine/compiler.py        PDF generation (real score, dynamic sections)
ops/tracing.py                  LLM usage ledger (tokens, cost, latency)
```

## LLM usage (bounded)

LLM is used only for drafting and conversation: call extraction + brief,
ToC/logframe/narrative drafts, risk/budget agents, advisor chat, blind
verifier. Every call is recorded in `ops/usage.jsonl`. Scoring, eligibility,
and gate verification are 100% deterministic.

## Test coverage

| File | Topic |
|---|---|
| `test_yaml_rules.py` | Manifest loading, scoring, hard gates, budget penalty |
| `test_call_ingest.py` | Multi-format extraction, anti-hallucination, publish flow |
| `test_step3_logframe.py` | Structured logframe, SMART parser, step locking |
| `test_proposal.py` | DB CRUD, verifier, PDF, end-to-end API |
| `test_frontend_contract.py` | DOM/asset contracts for the single-page app |

## License

[AGPL-3.0](LICENSE) — same license as Sightline.
