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

import json
import logging

from flask import Blueprint, jsonify, request
from auth import current_role, current_uid, require_auth, require_role

try:
    from proposal.db import get_proposal, lock_step, update_proposal
    from proposal.engine.models import (
        BudgetItem,
        PseaCommitments,
        RiskMatrixItem,
        compute_budget_summary,
    )
    from proposal.engine.yaml_rules import YamlDonorRuleLoader, DonorScoringEngine
    from proposal.engine.donor_resolver import resolve_donor_id

except ImportError:
    from db import get_proposal, lock_step, update_proposal
    from engine.models import (
        BudgetItem,
        PseaCommitments,
        RiskMatrixItem,
        compute_budget_summary,
    )
    from engine.yaml_rules import YamlDonorRuleLoader, DonorScoringEngine
    from engine.donor_resolver import resolve_donor_id
logger = logging.getLogger(__name__)

step4_api_bp = Blueprint("step4_budget_risk", __name__, url_prefix="/api/proposal-v2/steps/4")

RISK_CATEGORIES = ["security", "safeguarding_psea", "financial", "operational", "environmental"]
BUDGET_CATEGORIES = [
    "personnel", "travel_transport", "equipment_supplies",
    "contractual", "direct_operational", "indirect_overhead",
]


def _call_llm(prompt: str, system_prompt: str = "", temperature: float = 0.3) -> str:
    """Minimal OpenRouter call (shared pattern with engine/generator)."""
    import httpx
    import time

    try:
        from proposal.config import OPENROUTER_API_KEY, LLM_BASE_URL, LLM_MODEL
    except ImportError:
        from config import OPENROUTER_API_KEY, LLM_BASE_URL, LLM_MODEL
    if not OPENROUTER_API_KEY:
        return ""
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt or "You are an expert humanitarian grant proposal architect. LANGUAGE POLICY: ALWAYS respond in English."},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
    }
    try:
        t0 = time.time()
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(f"{LLM_BASE_URL}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
        data = resp.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        try:
            from ops.tracing import log_llm_call
            log_llm_call("step4_generate", LLM_MODEL, len(prompt), len(content), None, (time.time() - t0) * 1000)
        except Exception:
            try:
                from proposal.ops.tracing import log_llm_call
                log_llm_call("step4_generate", LLM_MODEL, len(prompt), len(content), None, (time.time() - t0) * 1000)
            except Exception:
                pass
        return content or ""
    except Exception as e:
        logger.warning("LLM call failed: %s", e)
        return ""


def _request_proposal(data: dict):
    """Resolve the authenticated proposal and preserve admin access."""
    proposal_id = data.get("proposal_id") or data.get("id")
    prop = get_proposal(proposal_id) if proposal_id else None
    if not prop:
        return proposal_id, None, None, (jsonify({"error": "Proposal not found"}), 404)
    uid = current_uid()
    if prop.get("user_id") != uid and current_role() != "admin":
        return proposal_id, None, None, (jsonify({"error": "Not your proposal"}), 403)
    return proposal_id, prop, prop.get("user_id"), None


def _resolve_donor(prop) -> str:
    return resolve_donor_id(prop.get("donor", "OCHA_CBPF"))


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


def _normalize_generated_budget(items, budget_max, overhead_cap_percent):
    """Enforce donor ceiling and indirect-cost cap on an AI budget draft."""
    normalized = [dict(item) for item in (items or [])]
    direct = [item for item in normalized if item.get("category") != "indirect_overhead"]
    indirect = [item for item in normalized if item.get("category") == "indirect_overhead"]

    direct_total = sum(float(item.get("unit_count", 0)) * float(item.get("unit_cost", 0)) for item in direct)
    indirect_total = sum(float(item.get("unit_count", 0)) * float(item.get("unit_cost", 0)) for item in indirect)
    allowed_indirect = direct_total * max(0.0, float(overhead_cap_percent or 0)) / 100.0
    if indirect_total > allowed_indirect and indirect_total > 0:
        overhead_factor = allowed_indirect / indirect_total
        for item in indirect:
            item["unit_cost"] = round(float(item.get("unit_cost", 0)) * overhead_factor, 2)

    total = sum(float(item.get("unit_count", 0)) * float(item.get("unit_cost", 0)) for item in normalized)
    if budget_max and total > float(budget_max) and total > 0:
        ceiling_factor = float(budget_max) / total
        for item in normalized:
            item["unit_cost"] = round(float(item.get("unit_cost", 0)) * ceiling_factor, 2)

        # Cent rounding can leave a tiny excess; trim it deterministically.
        rounded_total = sum(float(item.get("unit_count", 0)) * float(item.get("unit_cost", 0)) for item in normalized)
        excess = rounded_total - float(budget_max)
        if excess > 0:
            adjustable = next((item for item in normalized if float(item.get("unit_count", 0)) > 0), None)
            if adjustable:
                count = float(adjustable.get("unit_count", 0))
                cost = float(adjustable.get("unit_cost", 0))
                adjustable["unit_cost"] = round(max(0.0, cost - (excess / count) - 0.01), 2)

    return normalized


def _render_budget_template(items, currency, budget_max, overhead_cap_percent):
    """Render one authoritative narrative budget from the saved line items."""
    lines = [
        "Final Consolidated Budget",
        f"Currency: {currency}; donor ceiling: {float(budget_max):,.2f} {currency}; "
        f"indirect-cost cap: {float(overhead_cap_percent):g}% of direct costs.",
    ]
    for item in items or []:
        count = float(item.get("unit_count", 0))
        cost = float(item.get("unit_cost", 0))
        lines.append(
            f"- {item.get('description', '')} [{item.get('category', '')}]: "
            f"{count:g} {item.get('unit_type', 'unit')} × {cost:,.2f} = {count * cost:,.2f} {currency}."
        )
    summary = compute_budget_summary(items or [])
    lines.append(f"Direct subtotal: {summary['direct_total']:,.2f} {currency}.")
    lines.append(
        f"Indirect costs: {summary['indirect_total']:,.2f} {currency} "
        f"({summary['overhead_percent']:.2f}% of direct costs)."
    )
    lines.append(f"Grand total: {summary['grand_total']:,.2f} {currency}.")
    return "\n".join(lines)[:3000]


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
@require_auth
@require_role("premium")
def analyze_step4():
    """Full Step 4 validation: risks + budget + PSEA."""
    data = request.get_json(force=True, silent=True) or {}
    proposal_id, prop, user_id, error = _request_proposal(data)
    if error:
        return error

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
@require_auth
@require_role("premium")
def lock_step4():
    """Freeze Step 4 (budget_data) as an immutable snapshot."""
    data = request.get_json(force=True, silent=True) or {}
    proposal_id, prop, user_id, error = _request_proposal(data)
    if error:
        return error

    updated = lock_step(proposal_id, 4, user_id=user_id)
    if not updated:
        return jsonify({"error": "Proposal not found"}), 404
    return jsonify({"status": "locked", "step": 4, "locked_steps": updated.get("locked_steps", [])})


@step4_api_bp.route("/summary", methods=["POST"])
@require_auth
@require_role("premium")
def recompute_summary():
    """Persist recomputed budget summary into budget_data (scoring consistency)."""
    data = request.get_json(force=True, silent=True) or {}
    proposal_id, prop, user_id, error = _request_proposal(data)
    if error:
        return error

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


@step4_api_bp.route("/generate-risk", methods=["POST"])
@require_auth
@require_role("premium")
def generate_risk():
    """AI-draft the 5x5 risk matrix from the proposal context (per-subtab agent)."""
    data = request.get_json(force=True, silent=True) or {}
    proposal_id, prop, user_id, error = _request_proposal(data)
    if error:
        return error

    ctx = prop.get("context_data") or {}
    country = ctx.get("country") or prop.get("country") or "Target Country"
    theme = ctx.get("theme") or prop.get("theme") or "Multi-sector"
    donor = _resolve_donor(prop)

    prompt = f"""
Draft a 5x5 risk matrix for a humanitarian proposal in {country} ({theme}) for donor {donor}.
Return ONLY JSON: a list of 5 risk objects, one per category:
security, safeguarding_psea, financial, operational, environmental.
Each object: {{"category": "...", "description": "...", "likelihood": 1-5, "impact": 1-5, "mitigation_strategy": "..."}}
Severity = likelihood x impact. Risks with severity >= 12 MUST have a mitigation strategy.
LANGUAGE POLICY: ALWAYS respond in English.
"""
    raw = _call_llm(prompt, temperature=0.3)
    risks = []
    if raw:
        try:
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            parsed = json.loads(clean)
            if isinstance(parsed, dict) and "risks" in parsed:
                parsed = parsed["risks"]
            for item in parsed or []:
                risks.append({
                    "category": item.get("category", "security"),
                    "description": item.get("description", ""),
                    "likelihood": int(item.get("likelihood", 2) or 2),
                    "impact": int(item.get("impact", 2) or 2),
                    "mitigation_strategy": item.get("mitigation_strategy", ""),
                })
        except Exception as e:
            logger.warning("Risk JSON parse failed: %s", e)
    if not risks:
        # Deterministic fallback: one row per category
        risks = [
            {"category": "security", "description": f"Security incidents affecting staff access in {country}", "likelihood": 3, "impact": 4, "mitigation_strategy": "Security risk assessment, movement protocols, remote management"},
            {"category": "safeguarding_psea", "description": "PSEA incidents during community engagement", "likelihood": 2, "impact": 5, "mitigation_strategy": "PSEA policy, code of conduct, reporting hotline"},
            {"category": "financial", "description": "Currency fluctuation and delayed fund transfers", "likelihood": 3, "impact": 3, "mitigation_strategy": "Quarterly budget reviews, buffer planning"},
            {"category": "operational", "description": "Supply chain and logistics delays", "likelihood": 3, "impact": 3, "mitigation_strategy": "Local procurement, pre-positioning"},
            {"category": "environmental", "description": "Environmental impact of activities", "likelihood": 2, "impact": 2, "mitigation_strategy": "Environmental screening, waste management"},
        ]

    budget = dict(prop.get("budget_data") or {})
    budget["risks"] = risks
    updated = update_proposal(proposal_id, {"budget_data": budget}, user_id=user_id)
    if not updated:
        return jsonify({"error": "Proposal not found"}), 404
    return jsonify({"status": "ok", "risks": risks, "proposal": updated})


@step4_api_bp.route("/generate-budget", methods=["POST"])
@require_auth
@require_role("premium")
def generate_budget():
    """AI-draft the itemized budget from the proposal context (per-subtab agent)."""
    data = request.get_json(force=True, silent=True) or {}
    proposal_id, prop, user_id, error = _request_proposal(data)
    if error:
        return error

    ctx = prop.get("context_data") or {}
    country = ctx.get("country") or prop.get("country") or "Target Country"
    theme = ctx.get("theme") or prop.get("theme") or "Multi-sector"
    donor = _resolve_donor(prop)
    logframe_context = json.dumps(prop.get("logframe_data") or {}, ensure_ascii=False)[:8000]
    narrative_context = json.dumps(prop.get("narrative_data") or {}, ensure_ascii=False)[:8000]

    # Manifest currency / budget ceiling (call-specific rules)
    currency = "USD"
    budget_max = None
    overhead_cap_percent = 7.0
    try:
        loader = YamlDonorRuleLoader()
        manifest = loader.load(donor)
        if manifest:
            currency = manifest.currency or "USD"
            budget_max = manifest.budget_max
            overhead_cap_percent = manifest.overhead_cap_percent
    except Exception as e:
        logger.warning("Donor budget rules could not be loaded for %s: %s", donor, e)
    prompt = f"""
Draft an itemized budget for a humanitarian proposal in {country} ({theme}) for donor {donor}.
Budget currency: {currency}. Budget ceiling: {budget_max or 'not specified'} {currency} max — MUST NOT exceed.
PROJECT LOGFRAME (budget lines must map directly to these activities/outputs):
{logframe_context}
PROJECT NARRATIVE (do not introduce sectors or commodities absent here):
{narrative_context}
Return ONLY JSON: a list of 8-12 budget line items.
Each object: {{"category": "personnel|travel_transport|equipment_supplies|contractual|direct_operational|indirect_overhead", "description": "...", "unit_type": "...", "unit_count": number, "unit_cost": number}}
Indirect overhead must stay at or below {overhead_cap_percent}% of direct costs.
Every non-overhead line MUST directly support a named activity or output above.
Budget quantities for meetings, sessions, materials and participants MUST match
the corresponding numeric target in the logframe unless explicitly justified.
Do not add generic kits, winterization, WASH, shelter, food or other commodities
unless they explicitly appear in the project logframe or narrative.
LANGUAGE POLICY: ALWAYS respond in English.
"""
    raw = _call_llm(prompt, temperature=0.3)
    items = []
    if raw:
        try:
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            parsed = json.loads(clean)
            if isinstance(parsed, dict) and "items" in parsed:
                parsed = parsed["items"]
            for item in parsed or []:
                items.append({
                    "category": item.get("category", "personnel"),
                    "description": item.get("description", ""),
                    "unit_type": item.get("unit_type", "month"),
                    "unit_count": float(item.get("unit_count", 1) or 1),
                    "unit_cost": float(item.get("unit_cost", 0) or 0),
                })
        except Exception as e:
            logger.warning("Budget JSON parse failed: %s", e)
    if not items:
        items = [
            {"category": "personnel", "description": "Project Manager", "unit_type": "month", "unit_count": 12, "unit_cost": 2500},
            {"category": "personnel", "description": "Field Officers (2)", "unit_type": "month", "unit_count": 24, "unit_cost": 1500},
            {"category": "travel_transport", "description": "Field visits", "unit_type": "trip", "unit_count": 24, "unit_cost": 200},
            {"category": "equipment_supplies", "description": "WASH kits", "unit_type": "kit", "unit_count": 2000, "unit_cost": 25},
            {"category": "contractual", "description": "Training facilitators", "unit_type": "session", "unit_count": 12, "unit_cost": 500},
            {"category": "direct_operational", "description": "Office running costs", "unit_type": "month", "unit_count": 12, "unit_cost": 800},
            {"category": "indirect_overhead", "description": f"Indirect costs ({overhead_cap_percent}% cap)", "unit_type": "lump", "unit_count": 1, "unit_cost": 0},
        ]

    items = _normalize_generated_budget(items, budget_max, overhead_cap_percent)

    budget = dict(prop.get("budget_data") or {})
    budget["items"] = items
    budget["currency"] = currency
    budget["overhead_cap_percent"] = overhead_cap_percent
    if budget_max:
        budget["budget_max"] = budget_max
    fields = {"budget_data": budget}
    narrative = dict(prop.get("narrative_data") or {})
    budget_section = next(
        (key for key in narrative if str(key).strip().lower().replace("_", " ") == "budget template"),
        None,
    )
    if budget_section and 4 not in set(prop.get("locked_steps") or []):
        narrative[budget_section] = _render_budget_template(
            items, currency, budget_max or 0, overhead_cap_percent
        )
        fields["narrative_data"] = narrative
    updated = update_proposal(proposal_id, fields, user_id=user_id)
    if not updated:
        return jsonify({"error": "Proposal not found"}), 404
    return jsonify({"status": "ok", "items": items, "currency": currency, "proposal": updated})
