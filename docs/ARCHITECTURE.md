# Architecture Principles

> Development rules for Proposal Studio. **Critical rule: Proposal Studio is an
> independent project — it never imports, copies, or modifies Sightline code.**

## 1. Independence (First Rule)

- Proposal Studio never imports, copies, or modifies Sightline (RedAgent) code.
- No file from this project is ever written into the Sightline repository.
- Sightline's tests, deployments, and databases are never affected by this project.
- Development (branches, PRs, commits) runs fully separate.

## 2. Sightline Compatibility (For Future Integration)

The system will later be added to Sightline as a separate module. Therefore:

- The evidence layer (`engine/evidence.py`) matches Sightline's `reliefweb_api/`
  pattern 1-to-1, so it can be moved as a whole when integration happens.
- Endpoint names and data structures are chosen so the move requires no changes.
- **At migration time:** delete the bridge and import `reliefweb_api` directly —
  it will already be in the same process. The bridge is the ONLY file that
  touches Sightline paths.

## 3. UI Language

- **No Turkish text in the UI** (placeholders, error messages, badges — everything
  is English).
- Design language: Sightline's Liquid Glass system (matching over time).

## 4. Observability (LLM-Ops)

- Every AI action (generate, verify, advisor, export) produces a trace event.
- Token usage is written to the persistent `ops/usage.jsonl` ledger
  (tokens = ground truth).
- This layer follows the Waku `ops/tracing.py` pattern.

## 5. Quality Standards

- Every change ships with tests: `pytest tests/ -v` must be green before commit.
- Commit messages: subject + reason (WHY), under 70 characters.
- Major refactors run on a dedicated branch; no push to main without user approval.
- Human-in-the-loop: the LLM drafts, the deterministic engine decides, the user
  approves at every gate.
