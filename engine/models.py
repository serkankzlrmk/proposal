"""
proposal/engine/models.py — Canonical Pydantic Core Schemas (Master Spec §2.1).

Rollout strategy: this module grows per phase. Current scope (Phase 2 alignment):
  - DonorManifest   : root-level declarative donor manifest (ARCHITECTURAL_DECISIONS #1)
  - ReferenceEntry  : citation registry entry (shared by advisor/registry layers)

Later phases (Step 3/4) add: LogframeIndicator/LogframeOutcome/LogicalFramework,
BudgetItem, RiskMatrixItem, ScoreTraceItem, EligibilityGateResult,
ProposalEvaluationReport.
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
