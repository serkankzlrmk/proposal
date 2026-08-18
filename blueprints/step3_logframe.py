"""
proposal/blueprints/step3_logframe.py — Step 3 Backend API (Master Spec §3 STEP 3).

Endpoints:
  POST /api/proposal-v2/steps/3/analyze
      Validates the logframe (structured or free-text matrix) against the
      deterministic SMART validator; auto-parses free-text indicator strings
      into structured fields via the regex adapter (ARCHITECTURAL_DECISIONS #3).
      Returns smart report + donor score for smart_criteria + gate status.

  POST /api/proposal-v2/steps/3/lock
      Freezes Step 3 content (FSM: draft -> analyzed -> locked). Once locked,
      logframe_data writes are rejected with 409 by the PUT guard.

  POST /api/proposal-v2/steps/3/generate
      LLM-structured logframe generation (canonical shape + matrix projection).
"""

import logging

from flask import Blueprint, jsonify, request

try:
    from db import get_proposal, lock_step, update_proposal
    from engine.models import iter_indicator_entries
    from engine.smart_parser import parse_indicators_list, validate_indicators
    from engine.generator import generate_logframe, project_logframe_to_matrix
    from engine.yaml_rules import YamlDonorRuleLoader, DonorScoringEngine
    from engine.donor_resolver import resolve_donor_id
except ImportError:
    from proposal.db import get_proposal, lock_step, update_proposal
    from proposal.engine.models import iter_indicator_entries
    from proposal.engine.smart_parser import parse_indicators_list, validate_indicators
    from proposal.engine.generator import generate_logframe, project_logframe_to_matrix
    from proposal.engine.yaml_rules import YamlDonorRuleLoader, DonorScoringEngine
    from proposal.engine.donor_resolver import resolve_donor_id

logger = logging.getLogger(__name__)

step3_api_bp = Blueprint("step3_logframe", __name__, url_prefix="/api/proposal-v2/steps/3")


def _resolve_donor(prop) -> str:
    donor_key = (prop.get("donor") or "OCHA_CBPF").lower().replace("_", "")
    alias_map = {"ochacbpf": "ocha_cbpf", "usaidbha": "usaid_bha", "euprag": "eu_prag"}
    return alias_map.get(donor_key, donor_key)


def _smart_report_for(logframe) -> dict:
    """Deterministic SMART validation over structured or flat logframe."""
    entries = iter_indicator_entries(logframe or {})
    texts = [e["indicators"] for e in entries]
    report = validate_indicators(texts)
    report["entries"] = entries
    return report


@step3_api_bp.route("/analyze", methods=["POST"])
def analyze_step3():
    """SMART validation + donor smart_criteria score for the current logframe."""
    data = request.get_json(force=True, silent=True) or {}
    proposal_id = data.get("proposal_id") or data.get("id")
    user_id = data.get("user_id", "default_user")

    prop = get_proposal(proposal_id, user_id) if proposal_id else None
    if not prop:
        return jsonify({"error": "Proposal not found"}), 404

    # Optional payload override: parse free-text indicators into structured
    logframe = prop.get("logframe_data") or {}
    indicators_raw = data.get("indicators")
    if indicators_raw is not None:
        parsed = parse_indicators_list(indicators_raw, prefix="ind")
        logframe = dict(logframe)
        logframe.setdefault("goal_indicators", [])
        logframe["goal_indicators"] = [p.model_dump() for p in parsed]

    smart = _smart_report_for(logframe)

    # Donor smart_criteria score (deterministic engine)
    loader = YamlDonorRuleLoader()
    engine = DonorScoringEngine(loader)
    donor_id = resolve_donor_id(prop.get("donor", "OCHA_CBPF"), loader)
    scored = engine.score(donor_id, prop)
    smart_trace = next(
        (t for t in scored["trace"] if t["criterion"] == "smart_criteria"), {}
    )

    return jsonify({
        "proposal_id": proposal_id,
        "smart_report": smart,
        "smart_score": smart_trace.get("score", 0.0),
        "max_score": smart_trace.get("max_score", 20),
        "eligibility": scored["eligibility"],
    })


@step3_api_bp.route("/lock", methods=["POST"])
def lock_step3():
    """Freeze Step 3 (logframe_data) as an immutable snapshot."""
    data = request.get_json(force=True, silent=True) or {}
    proposal_id = data.get("proposal_id") or data.get("id")
    user_id = data.get("user_id", "default_user")

    prop = get_proposal(proposal_id, user_id) if proposal_id else None
    if not prop:
        return jsonify({"error": "Proposal not found"}), 404

    updated = lock_step(proposal_id, 3, user_id=user_id)
    if not updated:
        return jsonify({"error": "Proposal not found"}), 404
    return jsonify({"status": "locked", "step": 3, "locked_steps": updated.get("locked_steps", [])})


@step3_api_bp.route("/generate", methods=["POST"])
def generate_step3():
    """LLM-generated structured logframe (goal/outcomes/outputs + matrix)."""
    data = request.get_json(force=True, silent=True) or {}
    proposal_id = data.get("proposal_id") or data.get("id")
    user_id = data.get("user_id", "default_user")

    prop = get_proposal(proposal_id, user_id) if proposal_id else None
    if not prop:
        return jsonify({"error": "Proposal not found"}), 404

    ctx = prop.get("context_data") or {}
    toc = prop.get("toc_data") or {}
    donor = prop.get("donor", "OCHA_CBPF")
    logframe = generate_logframe(toc, ctx, donor=donor)

    updated = update_proposal(proposal_id, {"logframe_data": logframe}, user_id=user_id)
    if not updated:
        return jsonify({"error": "Proposal not found"}), 404
    return jsonify({"status": "ok", "logframe_data": updated.get("logframe_data"), "proposal": updated})
