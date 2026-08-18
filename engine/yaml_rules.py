"""
proposal/engine/yaml_rules.py — YAML-DRIVEN DONOR RULE LOADER & SCORING ENGINE.

Implements the NotebookLM technical specification:
  - /donors/<donor_id>.yaml manifests (declarative, zero-code donor extension)
  - YamlDonorRuleLoader with schema validation and default fallback handling
  - Deterministic 5-criterion scoring engine (30/25/20/15/10 = 100)
  - Graceful fallback: missing rules -> 0 points + WARNING_MISSING_RULE

Design goals:
  - Adding a new donor = adding one YAML file. Engine untouched.
  - Missing rules never crash evaluation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {
    "section_coverage": 30,
    "source_citations": 25,
    "smart_criteria": 20,
    "donor_keywords": 15,
    "budget_alignment": 10,
}

DEFAULT_DONOR_ID = "ocha_cbpf"

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
    """Load and validate donor manifests from /donors/*.yaml."""

    def __init__(self, donors_dir: Optional[Path] = None) -> None:
        self.donors_dir = Path(donors_dir) if donors_dir else Path(__file__).resolve().parent.parent / "donors"
        self._cache: Dict[str, Dict[str, Any]] = {}

    def list_donors(self) -> List[str]:
        return sorted(p.stem for p in self.donors_dir.glob("*.yaml"))

    def load(self, donor_id: str) -> Dict[str, Any]:
        """Load a donor manifest; fall back to default donor on missing file."""
        if donor_id in self._cache:
            return self._cache[donor_id]

        path = self.donors_dir / f"{donor_id}.yaml"
        if not path.exists():
            logger.warning("Donor manifest %s.yaml missing — falling back to %s", donor_id, DEFAULT_DONOR_ID)
            donor_id = DEFAULT_DONOR_ID
            path = self.donors_dir / f"{donor_id}.yaml"

        with open(path, "r", encoding="utf-8") as f:
            manifest = yaml.safe_load(f) or {}

        manifest = self._validate(manifest, donor_id)
        self._cache[donor_id] = manifest
        return manifest

    def _validate(self, manifest: Dict[str, Any], donor_id: str) -> Dict[str, Any]:
        """Schema validation with graceful defaults for missing fields."""
        manifest.setdefault("donor_id", donor_id)
        manifest.setdefault("name", donor_id.upper())
        manifest.setdefault("version", "0.0.0")
        manifest.setdefault("scoring_weights", dict(DEFAULT_WEIGHTS))
        manifest.setdefault("rules", {})
        manifest.setdefault("pass_threshold", 70)

        # Merge missing weights with defaults (never drop defined ones)
        weights = manifest["scoring_weights"]
        for k, v in DEFAULT_WEIGHTS.items():
            weights.setdefault(k, v)

        # Rules sub-sections default to empty dicts
        rules = manifest["rules"]
        for section in ("sections", "citations", "smart_indicators", "keywords", "budget", "quota_requirements"):
            rules.setdefault(section, {})

        return manifest


# ── Scoring engine ─────────────────────────────────────────────────────────
class DonorScoringEngine:
    """Deterministic scoring against a donor manifest. Total = 100 points."""

    def __init__(self, loader: YamlDonorRuleLoader) -> None:
        self.loader = loader

    def score(self, donor_id: str, proposal: Dict[str, Any]) -> Dict[str, Any]:
        """Score a proposal snapshot. Returns {total_score, trace[], passed}."""
        manifest = self.loader.load(donor_id)
        weights = manifest["scoring_weights"]
        rules = manifest["rules"]

        trace: List[Dict[str, Any]] = []

        # 1. section_coverage (30)
        def _section_coverage():
            mandatory = rules["sections"].get("mandatory", [])
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
                           + (f" Missing: {', '.join(set(mandatory) - set(present))}." if present else ""),
            }

        # 2. source_citations (25)
        def _source_citations():
            min_ratio = rules["citations"].get("min_source_ratio", 0.75)
            narrative = proposal.get("narrative_data") or {}
            paragraphs = [v for v in narrative.values() if isinstance(v, str) and v.strip()]
            cited = 0
            for text in paragraphs:
                if isinstance(text, str) and ("[" in text or "source" in text.lower() or "ref:" in text.lower()):
                    cited += 1
            ratio = cited / len(paragraphs) if paragraphs else 0.0
            score = min(ratio / min_ratio, 1.0) * weights["source_citations"] if min_ratio else 0.0
            return {
                "criterion": "source_citations",
                "score": round(score, 1),
                "max_score": weights["source_citations"],
                "target_step": "step2",
                "target_field": "needs_assessment",
                "details": f"{cited}/{len(paragraphs)} paragraphs cited (ratio {ratio:.2f}, min {min_ratio}).",
            }

        # 3. smart_criteria (20)
        def _smart_criteria():
            dims = rules["smart_indicators"].get("required_dimensions", ["specific", "measurable", "achievable", "relevant", "time_bound"])
            logframe = proposal.get("logframe_data") or {}
            matrix = logframe.get("matrix", []) if isinstance(logframe, dict) else []
            # Heuristic: an indicator is "measured" if it contains a number or unit
            passed = 0
            total = len(dims) or 1
            for dim in dims:
                # Count indicators that carry quantitative language
                indicators = []
                for row in matrix:
                    if isinstance(row, dict):
                        indicators.append(str(row.get("indicators", "")))
                if any(any(ch.isdigit() for ch in ind) or "%" in ind for ind in indicators):
                    passed += 1
            score = (passed / total) * weights["smart_criteria"]
            return {
                "criterion": "smart_criteria",
                "score": round(score, 1),
                "max_score": weights["smart_criteria"],
                "target_step": "step4",
                "target_field": "logframe",
                "details": f"{passed} of {total} SMART dimensions satisfied by quantified indicators.",
            }

        # 4. donor_keywords (15)
        def _donor_keywords():
            tokens = rules["keywords"].get("expected_tokens", [])
            narrative = proposal.get("narrative_data") or {}
            blob = " ".join(str(v).lower() for v in narrative.values())
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

        # 5. budget_alignment (10)
        def _budget_alignment():
            cap = rules["budget"].get("max_overhead_percent", 7.0)
            actual = proposal.get("budget_data", {}).get("overhead_percent", 0.0) if isinstance(proposal.get("budget_data"), dict) else 0.0
            penalty = max(0.0, (float(actual) - float(cap)) / float(cap)) if cap else 0.0
            score = weights["budget_alignment"] * (1.0 - penalty)
            return {
                "criterion": "budget_alignment",
                "score": round(score, 1),
                "max_score": weights["budget_alignment"],
                "target_step": "step5",
                "target_field": "budget",
                "details": f"Overhead {actual}% vs cap {cap}% -> penalty {penalty:.2f}.",
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
        threshold = manifest.get("pass_threshold", 70)

        return {
            "setup_id": proposal.get("setup_id", "setup_unknown"),
            "donor_id": donor_id,
            "donor_name": manifest.get("name", donor_id),
            "total_score": total,
            "pass_threshold": threshold,
            "passed": total >= threshold,
            "trace": trace,
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
