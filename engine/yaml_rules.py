"""
proposal/engine/yaml_rules.py — ROOT-LEVEL DONOR RULE LOADER & SCORING ENGINE.

Implements the Master Architectural Specification (AGENT_MASTER_IMPLEMENTATION_SPEC.md)
+ ARCHITECTURAL_DECISIONS_RESPONSE (v1.1.0):
  - /donors/<donor_id>.yaml manifests are ROOT-LEVEL declarative schemas,
    validated 1-to-1 via Pydantic `DonorManifest` (engine/models.py).
  - Deterministic 5-criterion scoring engine (30/25/20/15/10 = 100).
  - Budget alignment uses the SPEC LINEAR PENALTY formula:
        overhead <= cap -> 10.0, else max(0, 10 - (overhead - cap) * 5)
  - SMART validation: regex + structural checks (measurable/time_bound/
    disaggregation); full semantic SMART stays in the Blind Verifier.
  - Source citations: standardized [ref: SOURCE_ID] / [source: X] format,
    verified against the loaded reference registry when present.
  - Hard eligibility gates (root-level hard_eligibility_gates):
    PASS/FAIL -> AUTOMATIC_REJECTION flag regardless of text score.
    Unverifiable gates warn but do not fail.
  - Graceful fallback: missing rules -> 0 points + WARNING_MISSING_RULE.
    Adding a donor = one YAML file. Missing rules never crash evaluation.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from engine.models import DEFAULT_WEIGHTS, DonorManifest

logger = logging.getLogger(__name__)

DEFAULT_DONOR_ID = "ocha_cbpf"

# ── SMART indicator validation patterns ───────────────────────────────────
# Initial guard level: regex + structural. Semantic checks belong to the
# Blind Verifier / M&E reviewer pipeline (avoids false positives).
SMART_PATTERNS = {
    "measurable": re.compile(
        r"\d+(\.\d+)?\s*%|\d+\s*(L|kg|m|people|households|children|women|men|girls|boys|schools|facilities|units|persons)\b",
        re.I,
    ),
    "time_bound": re.compile(
        r"\b(by|before|within|until|end of)\b|\b(q[1-4]|month|months|week|weeks|day|days|year|years|20\d\d)\b",
        re.I,
    ),
    "disaggregation": re.compile(
        r"\b(gender|sex|age|sadd|disaggregated|disaggregation|female|male)\b",
        re.I,
    ),
    "specific": re.compile(r".{15,}", re.S),  # proxy: substantive length
    "achievable": re.compile(
        r"\d{1,3}\s*%|\d+\s*of\s*\d+|\b(target|expected|baseline)\b", re.I
    ),
    "relevant": re.compile(
        r"\b(reduce|increase|improve|access|ensure|strengthen|maintain|restore)\b", re.I
    ),
}

# ── Hard gate alias map (root-level gate key -> evaluator + display name) ──
# Canonical keys come from the spec manifest; legacy keys (v1 YAML) are
# accepted and normalized so old manifests keep working without edits.
GATE_ALIASES = {
    "sadd_disaggregation": "sadd_disaggregation_mandatory",
    "cluster_coordination": "cluster_coordination_mandatory",
    "psea": "psea_policy_mandatory",
    "sphere_standards": "sphere_standards_mandatory",
}

GATE_TITLES = {
    "sadd_disaggregation_mandatory": "SADD Disaggregation (Sex, Age, Disability)",
    "cluster_coordination_mandatory": "Cluster Coordination",
    "psea_policy_mandatory": "PSEA Policy & Safeguarding",
    "sphere_standards_mandatory": "Sphere Standards Alignment",
    "min_displaced_ratio": "Minimum Displaced Population Ratio",
    "min_capacity_score": "Institutional Capacity Threshold",
    "capacity_threshold_score": "Institutional Capacity Threshold",
}

# ── Rule evaluation with graceful fallback ─────────────────────────────────
def evaluate_rule_safely(rule_function, default_weight: float = 0.0) -> Dict[str, Any]:
    """Evaluate a rule; on missing definition return 0 points + soft warning."""
    try:
        return rule_function()
    except KeyError:
        return {
            "score": 0.0,
            "max_score": default_weight,
            "status": "WARNING_MISSING_RULE",
            "message": "Rule definition missing in donor YAML; defaulting to 0 points.",
        }
    except Exception as e:  # defensive: never crash the pipeline
        return {
            "score": 0.0,
            "max_score": default_weight,
            "status": "WARNING_RULE_ERROR",
            "message": f"Rule evaluation error: {e}",
        }


# ── Loader ─────────────────────────────────────────────────────────────────
class YamlDonorRuleLoader:
    """Load and validate root-level donor manifests from /donors/*.yaml."""

    def __init__(self, donors_dir: Optional[Path] = None) -> None:
        self.donors_dir = Path(donors_dir) if donors_dir else Path(__file__).resolve().parent.parent / "donors"
        self._cache: Dict[str, DonorManifest] = {}

    def list_donors(self) -> List[str]:
        return sorted(p.stem for p in self.donors_dir.glob("*.yaml"))

    def load(self, donor_id: str) -> DonorManifest:
        """Load a donor manifest; fall back to default donor on missing file.

        Returns a validated DonorManifest (engine/models.py). Zero-crash:
        missing/unreadable manifest -> DEFAULT_DONOR_ID with a warning.
        """
        if donor_id in self._cache:
            return self._cache[donor_id]

        path = self.donors_dir / f"{donor_id}.yaml"
        if not path.exists():
            logger.warning("Donor manifest %s.yaml missing — falling back to %s", donor_id, DEFAULT_DONOR_ID)
            donor_id = DEFAULT_DONOR_ID
            path = self.donors_dir / f"{donor_id}.yaml"

        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        manifest = self._validate(raw, donor_id)
        self._cache[donor_id] = manifest
        return manifest

    def _validate(self, raw: Dict[str, Any], donor_id: str) -> DonorManifest:
        """Pydantic validation with graceful defaults for missing fields."""
        # Legacy v1 shape (rules: {sections/citations/keywords/budget/...}) is
        # normalized to the root-level canonical schema so old YAMLs load.
        raw = self._normalize_legacy(raw, donor_id)
        raw.setdefault("donor_id", donor_id)
        raw.setdefault("display_name", donor_id.upper())
        raw.setdefault("version", "0.0.0")
        return DonorManifest(**raw)

    @staticmethod
    def _normalize_legacy(raw: Dict[str, Any], donor_id: str) -> Dict[str, Any]:
        """Map v1 nested `rules:` shape onto root-level canonical fields."""
        rules = raw.get("rules")
        if not isinstance(rules, dict):
            return raw

        out = dict(raw)
        out.pop("rules", None)

        # name -> display_name (v1 used `name`)
        if "name" in out and "display_name" not in out:
            out["display_name"] = out["name"]

        # rules.sections -> sections + max_char_limits
        sec = rules.get("sections") or {}
        if isinstance(sec, dict):
            out.setdefault("sections", {})
            out["sections"].setdefault("mandatory", sec.get("mandatory", []))
            limits = sec.get("limits") or {}
            if isinstance(limits, dict):
                out["max_char_limits"] = {
                    k: (v.get("max_chars") if isinstance(v, dict) else v)
                    for k, v in limits.items()
                }

        # rules.citations.min_source_ratio -> min_source_ratio
        cit = rules.get("citations") or {}
        if isinstance(cit, dict) and "min_source_ratio" in cit:
            out.setdefault("min_source_ratio", cit["min_source_ratio"])

        # rules.keywords.expected_tokens -> mandatory_keywords
        kw = rules.get("keywords") or {}
        if isinstance(kw, dict) and kw.get("expected_tokens"):
            out.setdefault("mandatory_keywords", kw["expected_tokens"])

        # rules.budget.max_overhead_percent -> overhead_cap_percent
        bdg = rules.get("budget") or {}
        if isinstance(bdg, dict) and "max_overhead_percent" in bdg:
            out.setdefault("overhead_cap_percent", bdg["max_overhead_percent"])

        # rules.smart_indicators -> smart_indicators
        sm = rules.get("smart_indicators") or {}
        if isinstance(sm, dict):
            out.setdefault("smart_indicators", sm)

        # rules.quota_requirements -> hard_eligibility_gates (aliased)
        qq = rules.get("quota_requirements") or {}
        if isinstance(qq, dict) and qq:
            gates = {}
            for k, v in qq.items():
                gates[GATE_ALIASES.get(k, k)] = v
            out.setdefault("hard_eligibility_gates", gates)

        return out


# ── Scoring engine ─────────────────────────────────────────────────────────
class DonorScoringEngine:
    """Deterministic scoring against a DonorManifest. Total = 100 points.

    Result shape:
      {setup_id, donor_id, donor_name, total_score, pass_threshold, passed,
       trace[], eligibility{passed, status, failed_quotas[], checks[]}}
    """

    def __init__(self, loader: YamlDonorRuleLoader) -> None:
        self.loader = loader

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _narrative_blob(proposal: Dict[str, Any]) -> str:
        narrative = proposal.get("narrative_data") or {}
        return " ".join(str(v).lower() for v in narrative.values() if isinstance(v, str))

    @staticmethod
    def _paragraphs(proposal: Dict[str, Any]) -> List[str]:
        narrative = proposal.get("narrative_data") or {}
        return [v for v in narrative.values() if isinstance(v, str) and v.strip()]

    @staticmethod
    def _reference_registry(proposal: Dict[str, Any]) -> set:
        """Collect known source IDs from proposal['references'] / reference_text."""
        ref_ids: set = set()
        references = proposal.get("references")
        if isinstance(references, list):
            for r in references:
                if isinstance(r, dict):
                    rid = r.get("id") or r.get("source_id") or r.get("name")
                    if rid:
                        ref_ids.add(str(rid).upper())
                elif isinstance(r, str):
                    ref_ids.add(r.upper())
        return ref_ids

    # -- hard eligibility gates (root-level) ------------------------------
    def _evaluate_gates(self, manifest: DonorManifest, proposal: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Evaluate hard_eligibility_gates as PASS/FAIL gates.

        A verifiable failed gate triggers AUTOMATIC_REJECTION regardless of
        the text score (matches real donor desk-review behavior).
        Unverifiable gates (missing data) warn but do not fail.
        """
        gates = manifest.hard_eligibility_gates or {}
        blob = self._narrative_blob(proposal)
        context = proposal.get("context_data") or {}
        beneficiaries = context.get("beneficiaries") or {}
        budget_data = proposal.get("budget_data") or {}

        checks: List[Dict[str, Any]] = []
        for name, spec in gates.items():
            # Normalize aliased gate keys (legacy manifests)
            canonical = GATE_ALIASES.get(name, name)
            title = GATE_TITLES.get(canonical, name.replace("_", " ").title())
            check = {
                "quota": canonical,
                "title": title,
                "required": spec,
                "verifiable": False,
                "passed": False,
                "details": "",
            }
            try:
                if canonical == "sadd_disaggregation_mandatory" and spec is True:
                    check["verifiable"] = True
                    check["passed"] = bool(re.search(r"\b(sadd|sex|age|gender|disaggregat)\w*", blob))
                    check["details"] = (
                        "SADD/disaggregation terms found in narrative"
                        if check["passed"]
                        else "No SADD/disaggregation terms in narrative"
                    )
                elif canonical == "cluster_coordination_mandatory" and spec is True:
                    check["verifiable"] = True
                    check["passed"] = "cluster coordination" in blob
                    check["details"] = (
                        "'cluster coordination' present" if check["passed"] else "'cluster coordination' missing"
                    )
                elif canonical == "psea_policy_mandatory" and spec is True:
                    check["verifiable"] = True
                    check["passed"] = "psea" in blob
                    check["details"] = "'PSEA' present" if check["passed"] else "'PSEA' missing"
                elif canonical == "sphere_standards_mandatory" and spec is True:
                    check["verifiable"] = True
                    check["passed"] = "sphere" in blob
                    check["details"] = "'Sphere standards' present" if check["passed"] else "'Sphere standards' missing"
                elif canonical == "min_displaced_ratio":
                    total = beneficiaries.get("total", 0) or 0
                    displaced = beneficiaries.get("idp_refugee", 0) or 0
                    if total:
                        check["verifiable"] = True
                        ratio = displaced / total
                        check["passed"] = ratio >= float(spec)
                        check["details"] = f"Displaced ratio {ratio:.0%} vs required {float(spec):.0%}"
                    else:
                        check["details"] = "No beneficiaries data — not verifiable"
                elif canonical in ("min_capacity_score", "capacity_threshold_score"):
                    score = budget_data.get("capacity_score") or context.get("capacity_score")
                    if score is not None:
                        check["verifiable"] = True
                        check["passed"] = float(score) >= float(spec)
                        check["details"] = f"Capacity {score}/20 vs required {spec}"
                    else:
                        check["details"] = "No capacity score data — not verifiable"
                else:
                    check["details"] = "Unsupported gate — ignored (graceful)"
            except Exception as e:  # never crash on a gate
                check["details"] = f"Gate evaluation error: {e}"
            checks.append(check)
        return checks

    # -- main scoring -----------------------------------------------------
    def score(self, donor_id: str, proposal: Dict[str, Any]) -> Dict[str, Any]:
        """Score a proposal snapshot. Returns total_score + trace + eligibility."""
        manifest = self.loader.load(donor_id)
        weights = manifest.merged_weights()
        trace: List[Dict[str, Any]] = []

        # 1. section_coverage (30)
        def _section_coverage():
            mandatory = manifest.mandatory_sections
            narrative = proposal.get("narrative_data") or {}
            present = [s for s in mandatory if narrative.get(s)]
            score = (len(present) / len(mandatory)) * weights["section_coverage"] if mandatory else 0.0
            return {
                "criterion": "section_coverage",
                "score": round(score, 1),
                "max_score": weights["section_coverage"],
                "target_step": "step2",
                "target_field": present[0] if present else (mandatory[0] if mandatory else ""),
                "details": f"{len(present)} of {len(mandatory)} mandatory sections present."
                           + (f" Missing: {', '.join(sorted(set(mandatory) - set(present)))}." if set(mandatory) - set(present) else ""),
            }

        # 2. source_citations (25) — standardized [ref: ID] / [source: X]
        def _source_citations():
            min_ratio = manifest.min_source_ratio
            paragraphs = self._paragraphs(proposal)
            ref_ids = self._reference_registry(proposal)
            has_registry = bool(ref_ids) or bool(proposal.get("reference_text"))

            cited = 0
            for text in paragraphs:
                cites = re.findall(r"\[(?:ref|source):\s*([^\]]+)\]", text, re.I)
                if not cites:
                    continue
                if not has_registry:
                    cited += 1  # draft mode: standard format suffices
                elif any(c.strip().upper() in ref_ids for c in cites):
                    cited += 1  # grounded: citation matches a loaded reference

            ratio = cited / len(paragraphs) if paragraphs else 0.0
            score = min(ratio / min_ratio, 1.0) * weights["source_citations"] if min_ratio else 0.0
            mode = "registry-verified" if has_registry else "draft (format-only)"
            return {
                "criterion": "source_citations",
                "score": round(score, 1),
                "max_score": weights["source_citations"],
                "target_step": "step2",
                "target_field": "needs_assessment",
                "details": f"{cited}/{len(paragraphs)} paragraphs cited (ratio {ratio:.2f}, min {min_ratio}, {mode}).",
            }

        # 3. smart_criteria (20) — regex + structural per dimension
        def _smart_criteria():
            dims = manifest.required_dimensions
            logframe = proposal.get("logframe_data") or {}
            matrix = logframe.get("matrix", []) if isinstance(logframe, dict) else []
            indicators = [str(row.get("indicators", "")) for row in matrix if isinstance(row, dict)]

            passed = 0
            for dim in dims:
                pattern = SMART_PATTERNS.get(dim.lower())
                if pattern is None:
                    continue  # unknown dimension: not scored, not failed
                if any(pattern.search(ind) for ind in indicators):
                    passed += 1

            score = (passed / len(dims)) * weights["smart_criteria"]
            return {
                "criterion": "smart_criteria",
                "score": round(score, 1),
                "max_score": weights["smart_criteria"],
                "target_step": "step4",
                "target_field": "logframe",
                "details": f"{passed} of {len(dims)} SMART dimensions satisfied by indicator patterns.",
            }

        # 4. donor_keywords (15)
        def _donor_keywords():
            tokens = manifest.mandatory_keywords
            blob = self._narrative_blob(proposal)
            matched = sum(1 for t in tokens if t.lower() in blob)
            score = (matched / len(tokens)) * weights["donor_keywords"] if tokens else 0.0
            missing = [t for t in tokens if t.lower() not in blob]
            return {
                "criterion": "donor_keywords",
                "score": round(score, 1),
                "max_score": weights["donor_keywords"],
                "target_step": "step2",
                "target_field": "needs_assessment",
                "details": f"Matched {matched}/{len(tokens)} keywords."
                           + (f" Missing: {', '.join(missing)}." if missing else ""),
            }

        # 5. budget_alignment (10) — SPEC LINEAR PENALTY (canonical)
        #    overhead <= cap -> 10.0; else max(0, 10 - (overhead - cap) * 5)
        def _budget_alignment():
            cap = manifest.overhead_cap_percent
            actual = proposal.get("budget_data", {}).get("overhead_percent", 0.0) if isinstance(proposal.get("budget_data"), dict) else 0.0
            if float(actual) <= float(cap):
                score = weights["budget_alignment"]
                penalty = 0.0
            else:
                penalty = (float(actual) - float(cap)) * 5.0
                score = max(0.0, weights["budget_alignment"] - penalty)
            return {
                "criterion": "budget_alignment",
                "score": round(score, 1),
                "max_score": weights["budget_alignment"],
                "target_step": "step5",
                "target_field": "budget",
                "details": f"Overhead {actual}% vs donor cap {cap}% -> linear penalty {penalty:.2f}.",
            }

        criteria = [
            ("section_coverage", _section_coverage, weights["section_coverage"]),
            ("source_citations", _source_citations, weights["source_citations"]),
            ("smart_criteria", _smart_criteria, weights["smart_criteria"]),
            ("donor_keywords", _donor_keywords, weights["donor_keywords"]),
            ("budget_alignment", _budget_alignment, weights["budget_alignment"]),
        ]

        for name, fn, weight in criteria:
            result = evaluate_rule_safely(fn, default_weight=weight)
            if result.get("status", "").startswith("WARNING"):
                result["criterion"] = name
                result["max_score"] = weight
            trace.append(result)

        total = round(sum(t["score"] for t in trace), 1)
        threshold = manifest.pass_threshold

        # Hard eligibility gates (canonical root-level)
        gate_checks = self._evaluate_gates(manifest, proposal)
        failed_gates = [c for c in gate_checks if c["verifiable"] and not c["passed"]]
        eligibility_passed = len(failed_gates) == 0
        eligibility = {
            "passed": eligibility_passed,
            "status": "ELIGIBLE" if eligibility_passed else "AUTOMATIC_REJECTION",
            "failed_quotas": [c["quota"] for c in failed_gates],
            "checks": gate_checks,
        }

        return {
            "setup_id": proposal.get("setup_id", "setup_unknown"),
            "donor_id": donor_id,
            "donor_name": manifest.display_name or donor_id,
            "total_score": total,
            "pass_threshold": threshold,
            "passed": total >= threshold and eligibility_passed,
            "trace": trace,
            "eligibility": eligibility,
        }


# ── Convenience singleton ──────────────────────────────────────────────────
_loader: Optional[YamlDonorRuleLoader] = None
_engine: Optional[DonorScoringEngine] = None


def get_rule_engine() -> DonorScoringEngine:
    global _loader, _engine
    if _engine is None:
        _loader = YamlDonorRuleLoader()
        _engine = DonorScoringEngine(_loader)
    return _engine
