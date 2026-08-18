"""
proposal/engine/advisor_context.py — Advisor Context Builder (Pydantic models).

Bridges the deterministic scoring engine and the LLM Advisor:

  - Takes the RAW engine output (score + trace + eligibility) and the proposal
  - Produces an ACTIONABLE, token-efficient context for the Advisor LLM:
      * gate_evaluation: does the proposal have AUTOMATIC_REJECTION? (first line)
      * diagnostics: only the offending blocks with section_key + snippet
        (NOT the whole proposal text -> token cost drops ~60%)
      * available_registry: only valid source_ids from the pipeline
        (Advisor must never invent references)
  - Pydantic validation: AdvisorContext.model_validate() keeps engine and
    agent decoupled.

Design principles (NotebookLM feedback):
  - Advisor's job is synthesis of violations -> revision suggestions,
    NOT re-computing all rules from scratch.
  - Raw text snippets are localized to the violation site.
  - Gate status shapes the advisor's strategy: fix quota first, then polish.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ── Pydantic models (shared by engine bridge + advisor prompt builder) ────
class BlockingReason(BaseModel):
    gate_type: str = "QUOTA"  # QUOTA | CITATION | BUDGET | SMART
    field: str
    expected: str
    actual: Any = None
    detail: str = ""


class GateEvaluation(BaseModel):
    status: str = "PASSED"  # PASSED | AUTOMATIC_REJECTION
    passed: bool = True
    blocking_reasons: List[BlockingReason] = Field(default_factory=list)


class Diagnostic(BaseModel):
    section_key: str
    rule_type: str  # SMART_VALIDATION | CITATION_REGISTRY | BUDGET_CAP | ...
    severity: str = "WARNING"  # ERROR | WARNING | INFO
    failed_aspects: List[str] = Field(default_factory=list)
    raw_text_snippet: str = ""
    missing_elements: List[str] = Field(default_factory=list)
    invalid_citations: List[str] = Field(default_factory=list)
    registry_status: str = "DRAFT_MODE"  # DRAFT_MODE | REGISTRY_VERIFIED
    metric: Optional[str] = None
    threshold: Optional[float] = None
    actual: Optional[float] = None
    delta: Optional[float] = None


class RegistryEntry(BaseModel):
    source_id: str
    title: str = ""
    url: str = ""


class DonorContext(BaseModel):
    donor_id: str
    min_source_ratio: float = 0.75
    budget_cap_overhead_percent: float = 7.0
    mandatory_quotas: Dict[str, Any] = Field(default_factory=dict)


class AdvisorContext(BaseModel):
    """The ONLY data the Advisor LLM sees about a proposal run."""

    proposal_id: str
    donor_context: DonorContext
    gate_evaluation: GateEvaluation
    diagnostics: List[Diagnostic] = Field(default_factory=list)
    available_registry: List[RegistryEntry] = Field(default_factory=list)

    @property
    def is_blocked(self) -> bool:
        return self.gate_evaluation.status == "AUTOMATIC_REJECTION"


# ── Bridge: raw engine output -> AdvisorContext ────────────────────────────
class AdvisorContextBuilder:
    """Build an AdvisorContext from (engine_result, proposal)."""

    # SMART regex set: presence-of-format ONLY, no success judgment
    # (NotebookLM: regex must not penalize valid text with over-strict rules)
    _SMART_PATTERNS = {
        "measurable": re.compile(
            r"\d+(\.\d+)?\s*%|\d+\s*(L|kg|m|people|households|children|women|men|girls|boys|persons|schools|facilities|units)\b", re.I
        ),
        "time_bound": re.compile(
            r"\b(by|before|within|until|end of)\b|\b(q[1-4]|month|months|week|weeks|year|years|20\d\d)\b", re.I
        ),
        "disaggregation": re.compile(
            r"\b(gender|sex|age|sadd|disaggregated|disaggregation|female|male)\b", re.I
        ),
    }

    def __init__(self, donor_id: str, manifest: Dict[str, Any]) -> None:
        self.donor_id = donor_id
        self.manifest = manifest

    def build(self, engine_result: Dict[str, Any], proposal: Dict[str, Any]) -> AdvisorContext:
        donor_rules = self.manifest.get("rules", {})
        donor_ctx = DonorContext(
            donor_id=self.donor_id,
            min_source_ratio=donor_rules.get("citations", {}).get("min_source_ratio", 0.75),
            budget_cap_overhead_percent=donor_rules.get("budget", {}).get("max_overhead_percent", 7.0),
            mandatory_quotas=donor_rules.get("quota_requirements", {}),
        )

        gate = self._build_gate(engine_result)
        diagnostics = self._build_diagnostics(engine_result, proposal)
        registry = self._build_registry(proposal)

        return AdvisorContext(
            proposal_id=proposal.get("setup_id") or proposal.get("id") or "unknown",
            donor_context=donor_ctx,
            gate_evaluation=gate,
            diagnostics=diagnostics,
            available_registry=registry,
        )

    # -- gate ------------------------------------------------------------
    def _build_gate(self, engine_result: Dict[str, Any]) -> GateEvaluation:
        elig = engine_result.get("eligibility", {})
        reasons: List[BlockingReason] = []
        for check in elig.get("checks", []):
            if check.get("verifiable") and not check.get("passed"):
                reasons.append(
                    BlockingReason(
                        gate_type="QUOTA",
                        field=check.get("quota", "unknown_quota"),
                        expected=str(check.get("required", "")),
                        actual=check.get("details", ""),
                        detail=check.get("details", "Quota requirement not met."),
                    )
                )
        status = "AUTOMATIC_REJECTION" if reasons else "PASSED"
        return GateEvaluation(status=status, passed=not reasons, blocking_reasons=reasons)

    # -- diagnostics -----------------------------------------------------
    def _build_diagnostics(self, engine_result: Dict[str, Any], proposal: Dict[str, Any]) -> List[Diagnostic]:
        diags: List[Diagnostic] = []
        trace = engine_result.get("trace", [])

        # SMART: scan indicators for missing format dimensions
        logframe = proposal.get("logframe_data") or {}
        matrix = logframe.get("matrix", []) if isinstance(logframe, dict) else []
        for idx, row in enumerate(matrix):
            if not isinstance(row, dict):
                continue
            text = str(row.get("indicators", ""))
            failed = [dim for dim, pat in self._SMART_PATTERNS.items() if not pat.search(text)]
            if failed:
                diags.append(
                    Diagnostic(
                        section_key=f"logframe.outcomes.{idx}",
                        rule_type="SMART_VALIDATION",
                        severity="ERROR" if "measurable" in failed or "time_bound" in failed else "WARNING",
                        failed_aspects=failed,
                        raw_text_snippet=text[:200],
                        missing_elements=[
                            "Measurable target (number and unit missing)" if "measurable" in failed else "",
                            "Time-bound indicator (timeframe not specified)" if "time_bound" in failed else "",
                            "Disaggregation tags (gender/age/SADD)" if "disaggregation" in failed else "",
                        ][:3],
                    )
                )

        # Citations: detect ungrounded / missing citations
        registry_ids = {r.source_id.upper() for r in self._build_registry(proposal)}
        narrative = proposal.get("narrative_data") or {}
        for sec_key, text in narrative.items():
            if not isinstance(text, str):
                continue
            cites = re.findall(r"\[(?:ref|source):\s*([^\]]+)\]", text, re.I)
            invalid = [c.strip() for c in cites if c.strip().upper() not in registry_ids]
            if registry_ids and invalid:
                diags.append(
                    Diagnostic(
                        section_key=sec_key,
                        rule_type="CITATION_REGISTRY",
                        severity="WARNING",
                        invalid_citations=invalid[:5],
                        registry_status="REGISTRY_VERIFIED",
                    )
                )

        # Budget: overhead cap delta
        budget = proposal.get("budget_data") or {}
        actual_overhead = budget.get("overhead_percent", 0.0)
        cap = self.manifest.get("rules", {}).get("budget", {}).get("max_overhead_percent", 7.0)
        if float(actual_overhead) > float(cap):
            diags.append(
                Diagnostic(
                    section_key="budget.summary",
                    rule_type="BUDGET_CAP",
                    severity="ERROR",
                    metric="overhead_ratio",
                    threshold=float(cap),
                    actual=float(actual_overhead),
                    delta=round(float(actual_overhead) - float(cap), 2),
                )
            )

        return diags

    # -- registry --------------------------------------------------------
    def _build_registry(self, proposal: Dict[str, Any]) -> List[RegistryEntry]:
        entries: List[RegistryEntry] = []
        refs = proposal.get("references") or []
        if isinstance(refs, list):
            for r in refs:
                if isinstance(r, dict):
                    entries.append(
                        RegistryEntry(
                            source_id=str(r.get("id") or r.get("source_id") or ""),
                            title=str(r.get("title") or ""),
                            url=str(r.get("url") or ""),
                        )
                    )
        return [e for e in entries if e.source_id]


# ── Prompt builder (token-efficient) ─────────────────────────────────────
def build_advisor_system_prompt(advisor_ctx: AdvisorContext) -> str:
    """Render the AdvisorContext into a compact system prompt for the LLM."""
    gate = advisor_ctx.gate_evaluation
    lines = [
        "You are the Proposal Advisor for a donor-compliant humanitarian proposal.",
        f"Donor: {advisor_ctx.donor_context.donor_id}.",
        f"Gate status: {gate.status}.",
    ]
    if gate.blocking_reasons:
        lines.append("BLOCKING ISSUES (fix these FIRST — proposal cannot be submitted):")
        for r in gate.blocking_reasons:
            lines.append(f"  - {r.field}: expected {r.expected}, actual {r.detail}")
    if advisor_ctx.diagnostics:
        lines.append("Diagnostics (only the violating blocks):")
        for d in advisor_ctx.diagnostics:
            lines.append(
                f"  - [{d.severity}] {d.section_key} ({d.rule_type}): "
                f"{d.raw_text_snippet[:120]}"
            )
    lines.append(
        "Your job: propose concrete, donor-compliant revisions for the diagnosed "
        "issues. Do NOT invent references — use only the available registry."
    )
    return "\n".join(lines)
