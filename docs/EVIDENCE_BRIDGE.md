# Evidence Bridge — Sightline Integration Note (MIGRATION NOTE)

> **Status:** Proposal Studio uses Sightline's `reliefweb_api/` tools at runtime
> **without copying any code** (the `engine/evidence.py` bridge). This note
> describes what happens when Proposal Studio is moved into Sightline as a module.

## How the bridge works today

1. `engine/evidence.py` reads `SIGHTLINE_ROOT` from the environment (see
   `.env.example`). **It defaults to empty** — without this variable the bridge
   is disabled and the pipeline runs fully standalone.
2. When set, Sightline's root is added to `sys.path` and its packages are
   imported through the package path (`import reliefweb_api.reliefweb`) so
   relative imports work.
3. Tools are LangChain `StructuredTool` objects — the bridge calls them via
   `tool.invoke({...})` (never directly).
4. The HDX key is read from **Sightline's own `.env`** at runtime
   (`HDX_APP_IDENTIFIER`); it is never copied into this repository.
5. If Sightline is missing/unavailable, every bridge call returns `None` — the
   proposal pipeline keeps working (zero-crash). Evidence is a bonus, never a
   hard dependency.

## What the bridge provides

| Function | Backing source | Used for |
|---|---|---|
| `search_sitreps()` | ReliefWeb SitReps | crisis situation text |
| `hdx_country_overview()` | HDX | country-level context |
| `hdx_refugees()` / `hdx_idps()` | HDX | beneficiary/IDP figures |
| `evidence_to_prompt()` | — | compact prompt block with `[ref: SIGHTLINE_<SOURCE>]` hints |
| `evidence_to_references()` | — | citation registry entries (`SIGHTLINE_SITREPS`, …) |

Evidence registered as `SIGHTLINE_*` references is grounded by the citation
registry in the deterministic scoring engine (`source_citations` criterion).

## Migration checklist (when integrating into Sightline)

1. Delete `engine/evidence.py` — it is the ONLY file that touches Sightline paths.
2. Import `reliefweb_api` directly (it will be in the same process).
3. Keep `ascii_country()` / `country_code_for()` (small, dependency-free helpers)
   — move them into a shared utils module.
4. Re-run the full test suite; the evidence tests (`test_call_ingest.py`) mock
   `SIGHTLINE_ROOT` to a nonexistent path and must stay green without Sightline.

## Pitfalls (learned)

- Sightline tools are `StructuredTool` objects, not callables — use `.invoke({...})`.
- Import through the PACKAGE path (`import reliefweb_api.reliefweb`) with the
  Sightline ROOT on `sys.path`; bare `import reliefweb` fails with "attempted
  relative import with no known parent package".
- HDX needs explicit `init_hdx_tools(app_identifier=...)` (Sightline's server.py
  does this at startup; the bridge replicates it).
- HDX 429 rate-limits surface as error JSON strings, not exceptions — filter
  `"error": true` results before injecting into prompts.
- Sightline's modules pull extra deps (`requests`, `langchain`, `langchain-core`,
  `pypdf`) — listed in `requirements.txt` under the optional section.
