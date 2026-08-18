"""
proposal/engine/advisor_context.py — Advisor Context Builder (Pydantic models).

Bridges the deterministic scoring engine and the LLM Advisor.

ARCHITECTURAL_DECISIONS #4 (CANONICAL: SUPERSET / MERGED):
  The rich Step B diagnostics (gate_evaluation, diagnostics,
  available_registry) are RETAINED and merged with the Master Spec §4.1
  patch-contract fields (step_id, field_name, current_text, donor_id,
  rule_violation, target_criterion, suggested_action, reference_candidates).

  - Takes the RAW engine output (score + trace + eligibility) and the proposal
  - Produces an ACTIONABLE, token-efficient context for the Advisor LLM:
      * gate_evaluation: does the proposal have AUTOMATIC_REJECTION? (first line)
      * diagnostics: only the offending blocks with section_key + snippet
        (NOT the whole proposal text -> token cost drops ~60%)
      * available_registry / reference_candidates: only valid source_ids
        (Advisor must never invent references)
  - Pydantic validation: AdvisorContext.model_validate() keeps engine and
    agent decoupled.

Design principles (Master Spec / NotebookLM feedback):
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

from engine.models import DonorManifest, iter_indicator_entries

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
    current_text: str = ""  # Step B: full text of the offending block
    missing_elements: List[str] = Field(default_factory=list)
    invalid_citations: List[str] = Field(default_factory=list)
    registry_status: str = "DRAFT_MODE"  # DRAFT_MODE | REGISTRY_VERIFIED
    metric: Optional[str] = None
    threshold: Optional[float] = None
    actual: Optional[float] = None
    delta: Optional[float] = None
    remediation_prompt: str = ""  # Step B: donor-specific actionable ask


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
    """The ONLY data the Advisor LLM sees about a proposal run.

    Superset (ARCHITECTURAL_DECISIONS #4): Master Spec §4.1 single-focus
    patch fields + retained Step B rich diagnostics.
    """

    proposal_id: str
    donor_context: DonorContext
    gate_evaluation: GateEvaluation
    diagnostics: List[Diagnostic] = Field(default_factory=list)
    available_registry: List[RegistryEntry] = Field(default_factory=list)

    # ── Master Spec §4.1 patch-contract fields (single-focus view) ──────
    step_id: int = 5
    field_name: str = ""
    current_text: str = ""
    donor_id: str = ""
    rule_violation: Optional[str] = None
    target_criterion: str = ""
    suggested_action: str = ""
    reference_candidates: List[RegistryEntry] = Field(default_factory=list)

    @property
    def is_blocked(self) -> bool:
        return self.gate_evaluation.status == "AUTOMATIC_REJECTION"


class RemediationSuggestion(BaseModel):
    """Structured diff/proposal returned by the Advisor LLM (Step B)."""

    section_key: str
    rule_type: str
    field: str = "text"
    suggested_text: str
    rationale: str = ""
    row_index: Optional[int] = None  # for logframe matrix rows


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

    def __init__(self, donor_id: str, manifest: DonorManifest) -> None:
        self.donor_id = donor_id
        self.manifest = manifest

    def build(self, engine_result: Dict[str, Any], proposal: Dict[str, Any]) -> AdvisorContext:
        donor_ctx = DonorContext(
            donor_id=self.donor_id,
            min_source_ratio=self.manifest.min_source_ratio,
            budget_cap_overhead_percent=self.manifest.overhead_cap_percent,
            mandatory_quotas=self.manifest.hard_eligibility_gates,
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
            # Spec §4.1 single-focus view: first blocking item wins
            step_id=self._focus_step(gate, diagnostics),
            field_name=self._focus_field(gate, diagnostics),
            current_text=self._focus_text(gate, diagnostics),
            donor_id=self.donor_id,
            rule_violation=self._focus_violation(gate, diagnostics),
            target_criterion=self._focus_criterion(diagnostics),
            suggested_action="Apply the suggested revision for the diagnosed issue and re-score.",
            reference_candidates=registry,
        )

    # -- focus helpers (Spec §4.1 single-focus view) ---------------------
    @staticmethod
    def _focus_step(gate: GateEvaluation, diagnostics: List[Diagnostic]) -> int:
        if gate.blocking_reasons:
            return 1  # quota fixes start at targeting/context step
        if diagnostics:
            key = diagnostics[0].section_key
            if key.startswith("logframe"):
                return 3
            if key.startswith("budget"):
                return 4
            return 4  # narrative sections
        return 5

    @staticmethod
    def _focus_field(gate: GateEvaluation, diagnostics: List[Diagnostic]) -> str:
        if gate.blocking_reasons:
            return gate.blocking_reasons[0].field
        if diagnostics:
            return diagnostics[0].section_key
        return ""

    @staticmethod
    def _focus_text(gate: GateEvaluation, diagnostics: List[Diagnostic]) -> str:
        if diagnostics:
            return diagnostics[0].current_text or diagnostics[0].raw_text_snippet
        if gate.blocking_reasons:
            return gate.blocking_reasons[0].detail
        return ""

    @staticmethod
    def _focus_violation(gate: GateEvaluation, diagnostics: List[Diagnostic]) -> Optional[str]:
        if diagnostics:
            return diagnostics[0].rule_type
        if gate.blocking_reasons:
            return f"QUOTA_{gate.blocking_reasons[0].field.upper()}"
        return None

    @staticmethod
    def _focus_criterion(diagnostics: List[Diagnostic]) -> str:
        if not diagnostics:
            return ""
        return {
            "SMART_VALIDATION": "smart_criteria",
            "CITATION_REGISTRY": "source_citations",
            "BUDGET_CAP": "budget_alignment",
        }.get(diagnostics[0].rule_type, "")

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
        # (structured LogicalFramework + legacy flat matrix both supported)
        logframe = proposal.get("logframe_data") or {}
        entries = iter_indicator_entries(logframe)
        for idx, entry in enumerate(entries):
            text = entry["indicators"]
            failed = [dim for dim, pat in self._SMART_PATTERNS.items() if not pat.search(text)]
            if failed:
                missing = [
                    "Measurable target (number and unit missing)" if "measurable" in failed else "",
                    "Time-bound indicator (timeframe not specified)" if "time_bound" in failed else "",
                    "Disaggregation tags (gender/age/SADD)" if "disaggregation" in failed else "",
                ]
                missing = [m for m in missing if m]
                diags.append(
                    Diagnostic(
                        section_key=f"logframe.outcomes.{idx}",
                        rule_type="SMART_VALIDATION",
                        severity="ERROR" if "measurable" in failed or "time_bound" in failed else "WARNING",
                        failed_aspects=failed,
                        raw_text_snippet=text[:200],
                        current_text=text,
                        missing_elements=missing,
                        remediation_prompt=(
                            f"Rewrite indicator row {idx} to satisfy: {', '.join(failed)}. "
                            "Include a quantified target with unit, an explicit timeframe, "
                            "and gender/age disaggregation where applicable. "
                            "Return ONLY a replacement indicator text."
                        ),
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
                        current_text=text,
                        remediation_prompt=(
                            f"Fix invalid citations in section '{sec_key}': {', '.join(invalid[:5])}. "
                            "Replace them with valid source IDs from the available registry. "
                            "Return ONLY the corrected section text."
                        ),
                    )
                )

        # Budget: overhead cap delta (linear penalty, canonical formula)
        budget = proposal.get("budget_data") or {}
        actual_overhead = budget.get("overhead_percent", 0.0)
        cap = self.manifest.overhead_cap_percent
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
                    current_text=f"Overhead: {actual_overhead}% (cap {cap}%)",
                    remediation_prompt=(
                        f"Reduce overhead from {actual_overhead}% to at most {cap}%. "
                        "Return ONLY a budget note with the corrected overhead percentage."
                    ),
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
    lines.append(
        "When you recommend a specific edit, end your reply with a JSON patch block:\n"
        "```json\n"
        '{"action": "apply_suggestion", "section_key": "<diagnostic section_key>", '
        '"rule_type": "<SMART_VALIDATION|CITATION_REGISTRY|BUDGET_CAP|QUOTA>", '
        '"field": "text|indicators", "suggested_text": "<full replacement text>", '
        '"rationale": "<one-line why>", "row_index": <optional number>}\n'
        "```\n"
        "The suggested_text MUST fully replace the offending content."
    )
    return "\n".join(lines)
