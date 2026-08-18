"""
proposal/engine/models.py — Canonical Pydantic Core Schemas (Master Spec §2.1).

Rollout: Phase 2 alignment added DonorManifest + ReferenceEntry.
Step 3 (this phase) adds the STRUCTURED logframe architecture
(ARCHITECTURAL_DECISIONS #3): LogframeIndicator / LogframeOutput /
LogframeOutcome / LogicalFramework / GanttItem. The free-text regex engine
(engine/smart_parser.py) adapts unstructured strings into these fields.

Later phases (Step 4) add: BudgetItem, RiskMatrixItem, ScoreTraceItem,
EligibilityGateResult, ProposalEvaluationReport.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

DEFAULT_WEIGHTS = {
    "section_coverage": 30.0,
    "source_citations": 25.0,
    "smart_criteria": 20.0,
    "donor_keywords": 15.0,
    "budget_alignment": 10.0,
}

DEFAULT_SMART_DIMENSIONS = ["specific", "measurable", "achievable", "relevant", "time_bound"]


class DonorManifest(BaseModel):
    """Root-level declarative donor manifest (canonical — Master Spec §2.2).

    All donor policies are declared at the root: display_name,
    min_source_ratio, overhead_cap_percent, mandatory_keywords and
    hard_eligibility_gates. Validated 1-to-1 with Pydantic; missing fields
    fall back to engine defaults (zero-crash guarantee).
    """

    donor_id: str
    display_name: str = ""
    template_standard: str = ""
    version: str = "0.0.0"
    currency: str = "USD"

    scoring_weights: Dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    sections: Dict[str, Any] = Field(default_factory=dict)  # {"mandatory": [...], "limits": {...}}
    max_char_limits: Dict[str, int] = Field(default_factory=dict)

    min_source_ratio: float = 0.75
    overhead_cap_percent: float = 7.0
    mandatory_keywords: List[str] = Field(default_factory=list)

    smart_indicators: Dict[str, Any] = Field(default_factory=dict)  # {"required_dimensions": [...]}
    hard_eligibility_gates: Dict[str, Any] = Field(default_factory=dict)
    pass_threshold: float = 70.0

    # -- semantic helpers ------------------------------------------------
    @property
    def mandatory_sections(self) -> List[str]:
        sec = self.sections or {}
        return list(sec.get("mandatory", []))

    @property
    def required_dimensions(self) -> List[str]:
        si = self.smart_indicators or {}
        dims = si.get("required_dimensions")
        return list(dims) if dims else list(DEFAULT_SMART_DIMENSIONS)

    def merged_weights(self) -> Dict[str, float]:
        """Scoring weights with engine defaults merged (defined ones win)."""
        weights = dict(DEFAULT_WEIGHTS)
        weights.update(self.scoring_weights or {})
        return weights


class ReferenceEntry(BaseModel):
    """Citation registry entry (Master Spec §2.1)."""

    source_id: str  # e.g. "OCHA_SITREP_2026_01", "HDX_NUTRITION_DATA"
    title: str = ""
    publisher: str = ""
    year: int = 0
    url: Optional[str] = None
    verified: bool = False


# ── Step 3: Structured Logframe Architecture (ARCHITECTURAL_DECISIONS #3) ──
class LogframeIndicator(BaseModel):
    """SMART indicator with structured target fields (Master Spec §2.1)."""

    indicator_id: str = ""
    narrative: str = ""
    target_value: float = Field(default=0.0)
    unit: str = Field(default="individuals")
    baseline: float = Field(default=0.0)
    timeframe: str = Field(default="by end of project")
    means_of_verification: str = Field(default="")
    assumptions: str = Field(default="")
    disaggregated_by: List[str] = Field(default_factory=lambda: ["gender", "age", "disability"])

    @property
    def smart_blob(self) -> str:
        """Compact string used by the deterministic SMART regex engine."""
        parts = [self.narrative, self.timeframe, self.unit, self.means_of_verification]
        return " ".join(str(p) for p in parts if p)


class LogframeOutput(BaseModel):
    output_id: str = ""
    narrative: str = ""
    indicators: List[LogframeIndicator] = Field(default_factory=list)
    activities: List[str] = Field(default_factory=list)


class LogframeOutcome(BaseModel):
    outcome_id: str = ""
    narrative: str = ""
    indicators: List[LogframeIndicator] = Field(default_factory=list)
    outputs: List[LogframeOutput] = Field(default_factory=list)


class LogicalFramework(BaseModel):
    """Structured 4x4 hierarchy: Goal -> Outcomes -> Outputs -> Activities."""

    goal: str = ""
    goal_indicators: List[LogframeIndicator] = Field(default_factory=list)
    outcomes: List[LogframeOutcome] = Field(default_factory=list)
    theory_of_change_narrative: str = ""
    assumptions: List[str] = Field(default_factory=list)


class GanttItem(BaseModel):
    """Activity schedule row (Step 3 directive #4)."""

    activity_id: str = ""
    name: str = ""
    output_id: str = ""
    start_month: int = Field(default=1, ge=1, le=24)
    end_month: int = Field(default=1, ge=1, le=24)
    lead_role: str = ""


def iter_indicator_entries(logframe: Dict[str, Any]) -> List[Dict[str, str]]:
    """Yield (level, indicators_text) pairs from any logframe shape.

    Supports BOTH the structured LogicalFramework shape (goal/outcomes/
    outputs, each with indicator objects) and the legacy flat matrix rows
    ({"indicators": "..."}). The SMART regex engine consumes the returned
    indicator text strings, so the structured model degrades gracefully
    against the existing scoring path.
    """
    entries: List[Dict[str, str]] = []

    matrix = logframe.get("matrix", []) if isinstance(logframe, dict) else []
    if matrix:
        for row in matrix:
            if not isinstance(row, dict):
                continue
            text = str(row.get("indicators", ""))
            if text.strip():
                entries.append({"level": str(row.get("level", "matrix")), "indicators": text})
        return entries

    # Structured shape
    goal = str(logframe.get("goal", ""))
    if goal:
        entries.append({"level": "goal", "indicators": goal})
    for gi in logframe.get("goal_indicators", []) or []:
        if isinstance(gi, dict) and str(gi.get("narrative", "")).strip():
            entries.append({"level": "goal", "indicators": str(gi.get("narrative", ""))})

    for oc in logframe.get("outcomes", []) or []:
        if not isinstance(oc, dict):
            continue
        on = str(oc.get("narrative", ""))
        if on:
            entries.append({"level": f"outcome:{oc.get('outcome_id', '')}", "indicators": on})
        for oi in oc.get("indicators", []) or []:
            if isinstance(oi, dict) and str(oi.get("narrative", "")).strip():
                entries.append({"level": f"outcome:{oc.get('outcome_id', '')}", "indicators": str(oi.get("narrative", ""))})
        for op in oc.get("outputs", []) or []:
            if not isinstance(op, dict):
                continue
            pn = str(op.get("narrative", ""))
            if pn:
                entries.append({"level": f"output:{op.get('output_id', '')}", "indicators": pn})
            for pi in op.get("indicators", []) or []:
                if isinstance(pi, dict) and str(pi.get("narrative", "")).strip():
                    entries.append({"level": f"output:{op.get('output_id', '')}", "indicators": str(pi.get("narrative", ""))})

    return entries
