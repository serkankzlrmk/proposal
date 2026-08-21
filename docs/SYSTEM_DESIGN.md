# System Design

> Design history and canonical contracts for Proposal Studio.
> The current implementation follows the NotebookLM Master Spec, which was
> cross-verified against the v3 design — two independent sources converged on
> the same architecture: **YAML-driven donor rules + deterministic scoring +
> interactive trace loop.**

## 1. Design lineage

| NotebookLM spec | Earlier design | Status |
|---|---|---|
| `/donors/<donor_id>.yaml` root-level manifest | nested `rules/donor_*.yaml` | ✅ canonical (root-level, v1.1) |
| `YamlDonorRuleLoader` + schema validation | `manifest_loader.py` | ✅ implemented |
| 5 criteria (30/25/20/15/10) | same 5 + weights | ✅ identical |
| `evaluate_rule_safely` → 0 pts + `WARNING_MISSING_RULE` | "undefined → 0 + warning" | ✅ implemented |
| Trace JSON (criterion/score/target_step/target_field) | `trace.py` | ✅ implemented |
| Click → Scroll → Advisor → Apply → Re-Score | interactive scoring | ✅ implemented |

## 2. Donor manifest (canonical, current)

```yaml
donor_id: "custom_donor"
display_name: "Custom Institutional Donor"
currency: TRY            # call-specific: budget figures written in TRY
budget_max: 1000000.0    # call ceiling — total MUST NOT exceed
max_duration_months: 12
deadline: "2026-05-02"

scoring_weights:          # 30/25/20/15/10 = 100
  section_coverage: 30
  source_citations: 25
  smart_criteria: 20
  donor_keywords: 15
  budget_alignment: 10

sections:
  mandatory: [programme_summary, expected_results, ...]

min_source_ratio: 0.75
overhead_cap_percent: 7.0
mandatory_keywords: [psea, sadd, ...]
hard_eligibility_gates:   # violated → AUTOMATIC_REJECTION
  sadd_disaggregation_mandatory: true
pass_threshold: 70
```

Adding a donor = one YAML file. The engine never changes.

## 3. Deterministic scoring formulas

| Criterion | Max | Formula |
|---|---|---|
| section_coverage | 30 | (present mandatory / total) × 30 |
| source_citations | 25 | min((cited / paragraphs) ÷ min_source_ratio, 1) × 25 — registry-verified when `proposal.references[]` exists, format-only (draft mode) otherwise |
| smart_criteria | 20 | (passed SMART dims / total dims) × 20 |
| donor_keywords | 15 | (matched keywords / expected) × 15 |
| budget_alignment | 10 | overhead ≤ cap → 10.0; else `max(0, 10 − (overhead − cap) × 5)` (linear penalty) |

Missing rule → 0 points + `WARNING_MISSING_RULE` (never crashes). Hard gates:
verifiable failed gate → `AUTOMATIC_REJECTION` regardless of text score;
unverifiable gates warn but do not fail.

## 4. Trace contract

```json
{
  "setup_id": "setup_99812",
  "donor_id": "custom_donor",
  "total_score": 71.2,
  "pass_threshold": 70.0,
  "passed": true,
  "trace": [{
    "criterion": "section_coverage",
    "score": 24.0,
    "max_score": 30,
    "target_step": "step2",
    "target_field": "humanitarian_context",
    "details": "4 of 5 mandatory sections present. Missing: budget_breakdown."
  }],
  "eligibility": { "passed": true, "status": "ELIGIBLE", "failed_quotas": [], "checks": [...] }
}
```

`target_step`/`target_field` drive the UI's click-to-jump behavior — the
interactive loop's critical piece.

## 5. Interactive UI loop

```
[Score Table] ──(click row)──► [Editor auto-scroll + focus] ──►
[AI Advisor (context-injected diagnostics)] ──► [Apply & Re-Score]
```

Advisor context is token-efficient: gate status first, diagnostics only for
violating blocks, registry passed so the LLM never invents references.

## 6. Sightline integration (future)

| Layer | Connection | Change needed |
|---|---|---|
| Rules | `donors/*.yaml` | **data, not code** — nothing |
| Sources | `reliefweb_api/` (via evidence bridge) | delete bridge, import directly |
| LLM | OpenRouter (config) | import model |
| Auth | — | `@require_auth` + `@require_role` |
| DB | proposal.db | isolated `call_drafts`/`proposals` tables |

See `docs/EVIDENCE_BRIDGE.md` for the migration checklist.

## 7. Open decisions (resolved in implementation)

1. Manifest content — resolved: canonical root-level YAML, call-ingested donors
   auto-loaded (loader globs `donors/*.yaml`).
2. Pass threshold — 70/100 default, per-manifest override.
3. Source tools — resolved: Sightline bridge + manual `references[]` registry.
4. Human-in-the-loop — resolved: LLM extracts/drafts, deterministic
   anti-hallucination gate verifies, human approves before publish.
