"""
proposal/blueprints/step4_budget_risk.py — Step 4 Backend API (Master Spec §3 STEP 4).

Endpoints:
  POST /api/proposal-v2/steps/4/analyze
      Validates the 5x5 risk matrix (severity >= 12 -> mandatory mitigation),
      itemized budget (overhead cap check vs donor manifest, linear penalty),
      and PSEA institutional commitments.

  POST /api/proposal-v2/steps/4/lock
      Freezes Step 4 content (budget_data) — FSM draft -> analyzed -> locked.
      Subsequent content changes return 409 via the PUT guard.

  POST /api/proposal-v2/steps/4/summary
      Recomputes and persists the budget summary (overhead_percent etc.) from
      the itemized rows. The scoring engine reads overhead_percent from
      budget_data, so saving the summary keeps scoring consistent.
"""

import logging

from flask import Blueprint, jsonify, request

try:
    from db import get_proposal, lock_step, update_proposal
    from engine.models import (
        BudgetItem,
        PseaCommitments,
        RiskMatrixItem,
        compute_budget_summary,
    )
    from engine.yaml_rules import YamlDonorRuleLoader, DonorScoringEngine
except ImportError:
    from proposal.db import get_proposal, lock_step, update_proposal
    from proposal.engine.models import (
        BudgetItem,
        PseaCommitments,
        RiskMatrixItem,
        compute_budget_summary,
    )
    from proposal.engine.yaml_rules import YamlDonorRuleLoader, DonorScoringEngine

logger = logging.getLogger(__name__)

step4_api_bp = Blueprint("step4_budget_risk", __name__, url_prefix="/api/proposal-v2/steps/4")

RISK_CATEGORIES = ["security", "safeguarding_psea", "financial", "operational", "environmental"]
BUDGET_CATEGORIES = [
    "personnel", "travel_transport", "equipment_supplies",
    "contractual", "direct_operational", "indirect_overhead",
]


def _resolve_donor(prop) -> str:
    donor_key = (prop.get("donor") or "OCHA_CBPF").lower().replace("_", "")
    alias_map = {"ochacbpf": "ocha_cbpf", "usaidbha": "usaid_bha", "euprag": "eu_prag"}
    return alias_map.get(donor_key, donor_key)


def _risk_report(risk_rows) -> dict:
    """5x5 severity validation: every risk >= 12 requires mitigation."""
    risks = []
    issues = []
    for row in risk_rows or []:
        try:
            r = row if isinstance(row, RiskMatrixItem) else RiskMatrixItem(**row)
        except Exception as e:
            issues.append({"row": row, "error": f"Invalid risk row: {e}"})
            continue
        risk = {
            **r.model_dump(),
            "severity_score": r.severity_score,
            "severity_tag": r.severity_tag,
        }
        if r.severity_score >= 12 and not (r.mitigation_strategy or "").strip():
            issues.append({
                "risk_id": r.risk_id or "?",
                "severity": r.severity_score,
                "message": "Risks with severity >= 12 require a mandatory mitigation plan.",
            })
        risks.append(risk)
    return {"risks": risks, "issues": issues, "high_risk_count": len(issues)}


def _budget_report(budget_data) -> dict:
    """Itemized budget summary + overhead cap check vs donor manifest."""
    items = budget_data.get("items") or []
    summary = compute_budget_summary(items)
    cap = budget_data.get("overhead_cap_percent", 7.0)
    actual = summary["overhead_percent"]
    if actual > cap:
        penalty = round(10.0 - (actual - cap) * 5.0, 2)
        status = "OVER_CAP"
    else:
        penalty = 10.0
        status = "OK"
    return {
        "summary": summary,
        "cap_percent": cap,
        "status": status,
        "budget_alignment_score": max(0.0, min(10.0, penalty)),
    }


def _psea_report(psea) -> dict:
    p = psea if isinstance(psea, PseaCommitments) else PseaCommitments(**psea)
    missing = [
        name for name, val in {
            "psea_policy_attached": p.psea_policy_attached,
            "code_of_conduct": p.code_of_conduct,
            "anti_terrorism_vetting": p.anti_terrorism_vetting,
        }.items() if not val
    ]
    return {
        "committed": not missing,
        "missing": missing,
        "passed": not missing,
    }


@step4_api_bp.route("/analyze", methods=["POST"])
def analyze_step4():
    """Full Step 4 validation: risks + budget + PSEA."""
    data = request.get_json(force=True, silent=True) or {}
    proposal_id = data.get("proposal_id") or data.get("id")
    user_id = data.get("user_id", "default_user")

    prop = get_proposal(proposal_id, user_id) if proposal_id else None
    if not prop:
        return jsonify({"error": "Proposal not found"}), 404

    budget = prop.get("budget_data") or {}

    # Donor overhead cap from manifest
    loader = YamlDonorRuleLoader()
    donor_id = _resolve_donor(prop)
    manifest = loader.load(donor_id)
    budget["overhead_cap_percent"] = manifest.overhead_cap_percent

    risk = _risk_report(budget.get("risks") or [])
    bdg = _budget_report(budget)
    psea = _psea_report(budget.get("psea") or {})

    # Deterministic engine score (budget_alignment with linear penalty)
    engine = DonorScoringEngine(loader)
    scored = engine.score(donor_id, prop)
    budget_trace = next((t for t in scored["trace"] if t["criterion"] == "budget_alignment"), {})
    budget_trace["recomputed"] = bdg["budget_alignment_score"]

    return jsonify({
        "proposal_id": proposal_id,
        "donor_id": donor_id,
        "risk_report": risk,
        "budget_report": bdg,
        "psea_report": psea,
        "budget_alignment_score": bdg["budget_alignment_score"],
        "max_score": 10,
        "eligibility": scored["eligibility"],
    })


@step4_api_bp.route("/lock", methods=["POST"])
def lock_step4():
    """Freeze Step 4 (budget_data) as an immutable snapshot."""
    data = request.get_json(force=True, silent=True) or {}
    proposal_id = data.get("proposal_id") or data.get("id")
    user_id = data.get("user_id", "default_user")

    prop = get_proposal(proposal_id, user_id) if proposal_id else None
    if not prop:
        return jsonify({"error": "Proposal not found"}), 404

    updated = lock_step(proposal_id, 4, user_id=user_id)
    if not updated:
        return jsonify({"error": "Proposal not found"}), 404
    return jsonify({"status": "locked", "step": 4, "locked_steps": updated.get("locked_steps", [])})


@step4_api_bp.route("/summary", methods=["POST"])
def recompute_summary():
    """Persist recomputed budget summary into budget_data (scoring consistency)."""
    data = request.get_json(force=True, silent=True) or {}
    proposal_id = data.get("proposal_id") or data.get("id")
    user_id = data.get("user_id", "default_user")

    prop = get_proposal(proposal_id, user_id) if proposal_id else None
    if not prop:
        return jsonify({"error": "Proposal not found"}), 404

    budget = prop.get("budget_data") or {}
    items = budget.get("items") or []
    summary = compute_budget_summary(items)

    budget = dict(budget)
    budget.update(summary)
    budget["items"] = items

    updated = update_proposal(proposal_id, {"budget_data": budget}, user_id=user_id)
    if not updated:
        return jsonify({"error": "Proposal not found"}), 404
    return jsonify({"status": "ok", "summary": summary, "proposal": updated})
