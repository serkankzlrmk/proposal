"""
proposal/blueprints/proposal_api.py — Flask Blueprint for Proposal Design Pipeline.
"""

import io
import json
import logging
from flask import Blueprint, jsonify, request, send_file

try:
    from db import (
        list_proposals,
        get_proposal,
        create_proposal,
        update_proposal,
        delete_proposal,
        save_review,
        get_reviews,
        ProposalLockedError,
    )
    from engine.donor_rules import DONOR_PROFILES, get_donor_profile
    from engine.generator import generate_toc, generate_logframe, generate_narrative_sections
    from engine.verifier import run_blind_verifier
    from engine.advisor import advisor_chat
    from engine.yaml_rules import YamlDonorRuleLoader, DonorScoringEngine, get_rule_engine
    from typst_engine.compiler import compile_pdf
except ImportError:
    from proposal.db import (
        list_proposals,
        get_proposal,
        create_proposal,
        update_proposal,
        delete_proposal,
        save_review,
        get_reviews,
        ProposalLockedError,
    )
    from proposal.engine.donor_rules import DONOR_PROFILES, get_donor_profile
    from proposal.engine.generator import generate_toc, generate_logframe, generate_narrative_sections
    from proposal.engine.verifier import run_blind_verifier
    from proposal.engine.advisor import advisor_chat
    from proposal.engine.yaml_rules import YamlDonorRuleLoader, DonorScoringEngine, get_rule_engine
    from proposal.typst_engine.compiler import compile_pdf

logger = logging.getLogger(__name__)

proposal_api_bp = Blueprint("proposal_api", __name__, url_prefix="/api/proposals")


@proposal_api_bp.route("/donors", methods=["GET"])
def get_donors():
    """List available donor profiles and section constraints."""
    return jsonify({"donors": DONOR_PROFILES})


@proposal_api_bp.route("/donors/yaml", methods=["GET"])
def get_yaml_donors():
    """List YAML-driven donor manifests (from /donors/*.yaml)."""
    loader = YamlDonorRuleLoader()
    return jsonify({"donors": loader.list_donors()})


@proposal_api_bp.route("/<proposal_id>/analyze", methods=["POST"])
def handle_analyze(proposal_id: str):
    """Deterministic scoring against the active donor YAML manifest.

    Returns total_score + full trace (per NotebookLM spec):
    each criterion carries score/max_score/target_step/target_field/details.
    """
    prop = get_proposal(proposal_id)
    if not prop:
        return jsonify({"error": "Proposal not found"}), 404

    donor_key = (prop.get("donor") or "OCHA_CBPF").lower().replace("_", "")
    # Map legacy donor keys (OCHA_CBPF, USAID_BHA, EU_PRAG) to YAML ids
    alias_map = {
        "ochacbpf": "ocha_cbpf",
        "usaidbha": "usaid_bha",
        "euprag": "eu_prag",
    }
    yaml_donor = alias_map.get(donor_key, donor_key)

    engine = get_rule_engine()
    result = engine.score(yaml_donor, prop)
    return jsonify(result)


@proposal_api_bp.route("", methods=["GET"])
def get_proposals_list():
    """List all proposals for the active user."""
    user_id = request.args.get("user_id", "default_user")
    props = list_proposals(user_id=user_id)
    return jsonify({"proposals": props})


@proposal_api_bp.route("/new", methods=["POST"])
def new_proposal():
    """Create a new proposal draft."""
    data = request.get_json(force=True, silent=True) or {}
    user_id = data.get("user_id", "default_user")
    title = data.get("title", "Untitled Proposal")
    country = data.get("country", "")
    donor = data.get("donor", "OCHA_CBPF")
    theme = data.get("theme", "Multi-sector")

    context_data = data.get("context_data", {
        "country": country,
        "theme": theme,
        "humanitarian_situation": data.get("humanitarian_situation", ""),
        "needs_assessment": data.get("needs_assessment", ""),
        "summary": data.get("summary", ""),
        "beneficiaries": data.get("beneficiaries", {"total": 20000, "idp_refugee": 11000}),
    })

    prop = create_proposal(
        user_id=user_id,
        title=title,
        country=country,
        donor=donor,
        theme=theme,
        context_data=context_data,
        toc_data=data.get("toc_data"),
        logframe_data=data.get("logframe_data"),
        narrative_data=data.get("narrative_data"),
        budget_data=data.get("budget_data"),
    )
    return jsonify({"proposal": prop}), 201


@proposal_api_bp.route("/<proposal_id>", methods=["GET"])
def get_proposal_detail(proposal_id: str):
    """Retrieve full proposal record."""
    prop = get_proposal(proposal_id)
    if not prop:
        return jsonify({"error": "Proposal not found"}), 404
    reviews = get_reviews(proposal_id)
    prop["reviews_history"] = reviews
    return jsonify({"proposal": prop})


@proposal_api_bp.route("/<proposal_id>", methods=["PUT"])
def autosave_proposal(proposal_id: str):
    """Update fields of an active proposal.

    FSM guard: writing to a locked step's content returns 409 Conflict
    (Master Spec invariant #1). Identical-value writes pass silently.
    """
    data = request.get_json(force=True, silent=True) or {}
    user_id = data.get("user_id", "default_user")
    try:
        updated = update_proposal(proposal_id, data, user_id=user_id)
    except ProposalLockedError as e:
        return jsonify({"error": str(e), "code": "STEP_LOCKED"}), 409
    if not updated:
        return jsonify({"error": "Proposal not found or update failed"}), 404
    return jsonify({"proposal": updated})


@proposal_api_bp.route("/<proposal_id>", methods=["DELETE"])
def remove_proposal(proposal_id: str):
    """Delete a proposal."""
    user_id = request.args.get("user_id", "default_user")
    deleted = delete_proposal(proposal_id, user_id=user_id)
    if not deleted:
        return jsonify({"error": "Proposal not found"}), 404
    return jsonify({"status": "deleted", "id": proposal_id})


@proposal_api_bp.route("/<proposal_id>/generate-toc", methods=["POST"])
def handle_generate_toc(proposal_id: str):
    """Trigger AI Theory of Change generation."""
    prop = get_proposal(proposal_id)
    if not prop:
        return jsonify({"error": "Proposal not found"}), 404

    ctx = prop.get("context_data") or {}
    donor = prop.get("donor", "OCHA_CBPF")
    toc_data = generate_toc(ctx, donor=donor)

    updated = update_proposal(proposal_id, {"toc_data": toc_data, "step": 2})
    return jsonify({"status": "ok", "toc_data": toc_data, "proposal": updated})


@proposal_api_bp.route("/<proposal_id>/generate-logframe", methods=["POST"])
def handle_generate_logframe(proposal_id: str):
    """Trigger AI 4x4 Logframe generation."""
    prop = get_proposal(proposal_id)
    if not prop:
        return jsonify({"error": "Proposal not found"}), 404

    ctx = prop.get("context_data") or {}
    toc = prop.get("toc_data") or {}
    donor = prop.get("donor", "OCHA_CBPF")

    logframe_data = generate_logframe(toc, ctx, donor=donor)
    updated = update_proposal(proposal_id, {"logframe_data": logframe_data, "step": 3})
    return jsonify({"status": "ok", "logframe_data": logframe_data, "proposal": updated})


@proposal_api_bp.route("/<proposal_id>/generate-narrative", methods=["POST"])
def handle_generate_narrative(proposal_id: str):
    """Trigger AI narrative drafting."""
    prop = get_proposal(proposal_id)
    if not prop:
        return jsonify({"error": "Proposal not found"}), 404

    ctx = prop.get("context_data") or {}
    logframe = prop.get("logframe_data") or {}
    donor = prop.get("donor", "OCHA_CBPF")

    narrative_data = generate_narrative_sections(logframe, ctx, donor=donor)
    updated = update_proposal(proposal_id, {"narrative_data": narrative_data, "step": 4})
    return jsonify({"status": "ok", "narrative_data": narrative_data, "proposal": updated})


@proposal_api_bp.route("/<proposal_id>/verify", methods=["POST"])
def handle_verify(proposal_id: str):
    """Trigger Blind Verifier audit."""
    prop = get_proposal(proposal_id)
    if not prop:
        return jsonify({"error": "Proposal not found"}), 404

    donor = prop.get("donor", "OCHA_CBPF")
    audit = run_blind_verifier(prop, donor=donor)

    save_review(
        proposal_id=proposal_id,
        verdict=audit["verdict"],
        score=audit["score"],
        issues=audit["issues"],
    )
    updated = get_proposal(proposal_id)
    return jsonify({"status": "ok", "audit": audit, "proposal": updated})


@proposal_api_bp.route("/<proposal_id>/references", methods=["POST"])
def add_references(proposal_id: str):
    """Append source entries to proposal.references[] (auto + manual).

    Body: {"sources": [{"id": "HDX_SUDAN_2026", "title": "...", "url": "..."}, ...]}
    Deduplicates by source id. Returns the updated reference registry.
    """
    prop = get_proposal(proposal_id)
    if not prop:
        return jsonify({"error": "Proposal not found"}), 404

    data = request.get_json(force=True, silent=True) or {}
    sources = data.get("sources") or []
    if not isinstance(sources, list):
        return jsonify({"error": "sources must be a list"}), 400

    registry = prop.get("references") or []
    seen = {str(r.get("id", "")).upper() for r in registry if isinstance(r, dict)}
    added = []
    for s in sources:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "").strip()
        if not sid:
            continue
        if sid.upper() in seen:
            continue
        entry = {
            "id": sid,
            "title": str(s.get("title") or ""),
            "url": str(s.get("url") or ""),
            "added_at": __import__("time").time(),
        }
        registry.append(entry)
        seen.add(sid.upper())
        added.append(entry)

    updated = update_proposal(proposal_id, {"references": registry}) or {}
    return jsonify({"status": "ok", "added": added, "references": updated.get("references", [])})


@proposal_api_bp.route("/<proposal_id>/advisor/chat", methods=["POST"])
def handle_advisor_chat(proposal_id: str):
    """Interactive Advisor chat session.

    When the client supplies an analysis run (or we can build one on the fly),
    the chat receives a token-efficient AdvisorContext with diagnostics +
    remediation prompts. The LLM returns a structured RemediationSuggestion
    (suggested_text + rationale) that the frontend can apply and re-score.
    """
    prop = get_proposal(proposal_id)
    if not prop:
        return jsonify({"error": "Proposal not found"}), 404

    data = request.get_json(force=True, silent=True) or {}
    user_msg = data.get("message", "")
    history = data.get("history", [])

    # Build AdvisorContext from the deterministic engine (Step B)
    try:
        from engine.yaml_rules import YamlDonorRuleLoader, DonorScoringEngine
        from engine.advisor_context import AdvisorContextBuilder

        donor_key = (prop.get("donor") or "OCHA_CBPF").lower().replace("_", "")
        alias_map = {"ochacbpf": "ocha_cbpf", "usaidbha": "usaid_bha", "euprag": "eu_prag"}
        yaml_donor = alias_map.get(donor_key, donor_key)

        loader = YamlDonorRuleLoader()
        manifest = loader.load(yaml_donor)
        engine = DonorScoringEngine(loader)
        engine_result = engine.score(yaml_donor, prop)

        builder = AdvisorContextBuilder(yaml_donor, manifest)
        advisor_ctx = builder.build(engine_result, prop)
    except Exception as e:
        logger.warning("AdvisorContext build failed (%s); falling back to plain chat", e)
        advisor_ctx = None

    result = advisor_chat(prop, user_msg, chat_history=history, advisor_ctx=advisor_ctx)
    return jsonify(result)


@proposal_api_bp.route("/<proposal_id>/export/pdf", methods=["GET"])
def handle_export_pdf(proposal_id: str):
    """Compile and stream publication-grade Typst PDF."""
    prop = get_proposal(proposal_id)
    if not prop:
        return jsonify({"error": "Proposal not found"}), 404

    pdf_bytes = compile_pdf(prop)
    if not pdf_bytes:
        return jsonify({"error": "PDF compilation failed"}), 500

    filename = f"{prop.get('donor', 'GMS')}_{prop.get('country', 'Proposal')}_{proposal_id}.pdf".replace(" ", "_")
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )
