"""
proposal/engine/generator.py — AI Generator for Theory of Change, Logframe Matrix & Narrative.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional
import httpx

try:
    from config import OPENROUTER_API_KEY, LLM_BASE_URL, LLM_MODEL
    from engine.donor_rules import get_donor_profile
    from ops.tracing import log_llm_call
except ImportError:
    from proposal.config import OPENROUTER_API_KEY, LLM_BASE_URL, LLM_MODEL
    from proposal.engine.donor_rules import get_donor_profile
    from proposal.ops.tracing import log_llm_call

logger = logging.getLogger(__name__)


# ── Country helpers for the evidence bridge ────────────────────────────────
def _ascii_country(country: str) -> str:
    """Normalize a country label to ASCII for ReliefWeb queries."""
    try:
        from engine.evidence import ascii_country
        return ascii_country(country)
    except Exception:
        return (country or "").split("(")[0].strip().title()


def _country_code_for(country: str) -> Optional[str]:
    """Map a country label to an ISO-3 code for HDX queries (best-effort)."""
    try:
        from engine.evidence import country_code_for
        return country_code_for(country)
    except Exception:
        return None


# ── Donor manifest context (call-aware generation) ────────────────────────
def _donor_manifest_context(donor: str) -> str:
    """Render the donor manifest requirements for LLM prompt injection.

    Loads the canonical root-level manifest (call-ingested donors included)
    and renders what the donor actually requires — mandatory sections,
    keywords, citation ratio, overhead cap, hard gates — so the generator
    writes FOR that donor. Empty string on any failure (zero-crash).
    """
    try:
        from engine.yaml_rules import YamlDonorRuleLoader
        from engine.donor_resolver import resolve_donor_id

        loader = YamlDonorRuleLoader()
        yaml_donor = resolve_donor_id(donor, loader)
        manifest = loader.load(yaml_donor)
        lines = [
            f"Donor: {manifest.display_name}",
            f"Mandatory sections: {', '.join(manifest.mandatory_sections) or 'none'}",
            f"Mandatory keywords: {', '.join(manifest.mandatory_keywords) or 'none'}",
            f"Min source citation ratio: {manifest.min_source_ratio}",
            f"Overhead cap: {manifest.overhead_cap_percent}%",
        ]
        if manifest.currency:
            lines.append(f"Budget currency: {manifest.currency} — write ALL budget figures in {manifest.currency}")
        if manifest.budget_max:
            lines.append(f"Budget ceiling: {manifest.budget_max:,.0f} {manifest.currency} max — total budget MUST NOT exceed this ceiling")
        if manifest.max_duration_months:
            lines.append(f"Max project duration: {manifest.max_duration_months} months")
        if manifest.deadline:
            lines.append(f"Submission deadline: {manifest.deadline}")
        gates = manifest.hard_eligibility_gates or {}
        if gates:
            lines.append("Hard eligibility gates: " + ", ".join(f"{k}={v}" for k, v in gates.items()))
        return "\n".join(lines)
    except Exception as e:
        logger.debug("Donor manifest context unavailable: %s", e)
        return ""

# ── Reference formatting (NotebookLM Step B: auto-populate references[]) ──
def format_source_id(source: str, country: str, year: str = "2026") -> str:
    """Format a canonical source id, e.g. format_source_id('HDX','Sudan') -> 'HDX_SUDAN_2026'."""
    src = source.upper().replace(" ", "_")
    ctry = country.upper().replace(" ", "_")
    return f"{src}_{ctry}_{year}"


def append_references(references: List[Dict[str, Any]], sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate-append source entries to a references registry.

    Each source: {"id": ..., "title": ..., "url": ...}
    Returns the updated list (safe to store as proposal.references).
    """
    registry = list(references or [])
    seen = {str(r.get("id", "")).upper() for r in registry if isinstance(r, dict)}
    for s in sources:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "").strip()
        if not sid or sid.upper() in seen:
            continue
        registry.append({
            "id": sid,
            "title": str(s.get("title") or ""),
            "url": str(s.get("url") or ""),
        })
        seen.add(sid.upper())
    return registry


def _call_llm(prompt: str, system_prompt: str = "", temperature: float = 0.3, action: str = "generate") -> str:
    """Call OpenRouter or LLM provider with fallback heuristics.

    Every call is traced to ops/usage.jsonl (LLM-Ops ledger, Waku pattern):
    model, tokens, estimated cost, latency. Tracing never blocks the call.
    """
    if not OPENROUTER_API_KEY:
        logger.warning("No OPENROUTER_API_KEY found; using structured deterministic fallback generation.")
        return ""

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://sightline.humanitarian.ai",
        "X-Title": "Sightline Proposal Engine",
    }
    payload = {
        "model": LLM_MODEL,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt or "You are an elite humanitarian grant proposal architect."},
            {"role": "user", "content": prompt},
        ],
    }
    t0 = time.time()
    try:
        with httpx.Client(timeout=45.0) as client:
            resp = client.post(f"{LLM_BASE_URL}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage") or {}
            log_llm_call(
                action=action,
                model=LLM_MODEL,
                prompt_chars=len(prompt),
                response_chars=len(text),
                usage=usage,
                latency_ms=(time.time() - t0) * 1000.0,
            )
            return text
    except Exception as e:
        logger.warning("LLM API call failed: %s; using deterministic fallback.", e)
        log_llm_call(
            action=action,
            model=LLM_MODEL,
            prompt_chars=len(prompt),
            response_chars=0,
            latency_ms=(time.time() - t0) * 1000.0,
            error=str(e),
        )
        return ""


def generate_toc(context_data: Dict[str, Any], donor: str = "OCHA_CBPF") -> Dict[str, Any]:
    """Generate structured Theory of Change DAG nodes and causal links."""
    country = context_data.get("country", "Target Region")
    theme = context_data.get("theme", "Emergency Response")
    needs = context_data.get("needs_assessment", "")
    donor_reqs = _donor_manifest_context(donor)

    prompt = f"""
    Create a structured Theory of Change (ToC) for a humanitarian project in {country} focusing on {theme}.
    Context: {needs}
    Donor Format: {donor}

    DONOR REQUIREMENTS (from the call manifest — align causal logic to these):
    {donor_reqs}

    Return ONLY a valid JSON object matching this schema:
    {{
      "nodes": [
        {{"id": "inp_1", "type": "input", "label": "Technical inputs & emergency procurement"}},
        {{"id": "act_1", "type": "activity", "label": "Rapid field deployment & capacity training"}},
        {{"id": "out_1", "type": "output", "label": "Functional infrastructure & services delivered"}},
        {{"id": "oc_1", "type": "outcome", "label": "Vulnerable households have sustained dignified access"}},
        {{"id": "imp_1", "type": "impact", "label": "Reduced excess mortality and enhanced resilience"}}
      ],
      "edges": [
        {{"from": "inp_1", "to": "act_1"}},
        {{"from": "act_1", "to": "out_1"}},
        {{"from": "out_1", "to": "oc_1"}},
        {{"from": "oc_1", "to": "imp_1"}}
      ],
      "assumptions": [
        "Unimpeded humanitarian access and security clearance",
        "Stable supply chain routes and vendor responsiveness",
        "Active community engagement and local stakeholder buy-in"
      ]
    }}
    """
    raw = _call_llm(prompt)
    if raw:
        try:
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            return json.loads(clean)
        except Exception as e:
            logger.debug("Failed to parse LLM ToC JSON: %s", e)

    # Deterministic high-grade fallback
    return {
        "nodes": [
            {"id": "inp_1", "type": "input", "label": f"Procurement of critical {theme} supplies and technical staffing in {country}"},
            {"id": "act_1", "type": "activity", "label": f"Rehabilitation of key facilities and community engagement sessions"},
            {"id": "out_1", "type": "output", "label": f"Operational service points established adhering to Sphere standards"},
            {"id": "oc_1", "type": "outcome", "label": f"Conflict-affected population in {country} has dignified, equitable access to {theme}"},
            {"id": "imp_1", "type": "impact", "label": f"Mitigated mortality and sustained protection for vulnerable households"},
        ],
        "edges": [
            {"from": "inp_1", "to": "act_1"},
            {"from": "act_1", "to": "out_1"},
            {"from": "out_1", "to": "oc_1"},
            {"from": "oc_1", "to": "imp_1"},
        ],
        "assumptions": [
            "Humanitarian corridors remain open for continuous aid delivery",
            "Local authorities maintain operational MoUs and access permits",
            "Community elders and women-led committees support site operations",
        ],
    }


def generate_logframe(toc_data: Dict[str, Any], context_data: Dict[str, Any], donor: str = "OCHA_CBPF") -> Dict[str, Any]:
    """Generate structured LogicalFramework (goal/outcomes/outputs/indicators).

    Canonical shape (ARCHITECTURAL_DECISIONS #3 / Master Spec §2.1). A legacy
    flat `matrix` projection is included for UI/PDF backward compatibility
    (matrix[].indicators strings feed the existing SMART regex path).
    """
    country = context_data.get("country", "Target Country")
    theme = context_data.get("theme", "Emergency WASH & Multi-sector")
    donor_reqs = _donor_manifest_context(donor)

    prompt = f"""
    Generate a structured Logical Framework for {donor} guidelines in {country} for {theme}.

    DONOR REQUIREMENTS (from the call manifest — indicators must satisfy these):
    {donor_reqs}
    Return ONLY a JSON object matching this schema:
    {{
      "goal": "Impact / Overall Goal narrative",
      "goal_indicators": [
        {{"indicator_id": "g1", "narrative": "...", "target_value": 0.5, "unit": "CMR /10k/day",
          "baseline": 0.0, "timeframe": "by end of project", "means_of_verification": "...",
          "assumptions": "...", "disaggregated_by": ["gender", "age", "disability"]}}
      ],
      "outcomes": [
        {{"outcome_id": "oc1", "narrative": "...",
          "indicators": [{{"indicator_id": "oc1_i1", "narrative": "...", "target_value": 85.0,
            "unit": "percent", "baseline": 0.0, "timeframe": "by month 12", "means_of_verification": "...",
            "assumptions": "...", "disaggregated_by": ["gender", "age", "disability"]}}],
          "outputs": [
            {{"output_id": "op1", "narrative": "...",
              "indicators": [{{"indicator_id": "op1_i1", "narrative": "...", "target_value": 12.0,
                "unit": "facilities", "baseline": 0.0, "timeframe": "by month 9", "means_of_verification": "...",
                "assumptions": "...", "disaggregated_by": ["gender", "age", "disability"]}}],
              "activities": ["Act 1.1.1 ...", "Act 1.1.2 ..."]}}
          ]}}
      ],
      "theory_of_change_narrative": "causal pathway narrative",
      "assumptions": ["assumption 1", "assumption 2", "assumption 3"]
    }}
    """
    raw = _call_llm(prompt)
    if raw:
        try:
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            structured = json.loads(clean)
            return project_logframe_to_matrix(structured, harden=True)
        except Exception as e:
            logger.debug("Failed to parse LLM Logframe JSON: %s", e)

    # Deterministic structured fallback (high-grade humanitarian matrix)
    structured = {
        "goal": f"Reduced excess morbidity, mortality, and vulnerability among conflict-affected IDPs and host communities in {country}.",
        "goal_indicators": [
            {
                "indicator_id": "g1",
                "narrative": "Crude Mortality Rate (CMR) below 0.5 per 10,000 per day",
                "target_value": 0.5,
                "unit": "CMR /10,000/day",
                "baseline": 0.0,
                "timeframe": "by end of project",
                "means_of_verification": "UN OCHA Cluster Surveys, Ministry of Health Epidemiological Bulletins, SMART Assessment Reports.",
                "assumptions": "Political stability permits ongoing humanitarian agency operations without prolonged blockades.",
                "disaggregated_by": ["gender", "age", "disability"],
            }
        ],
        "outcomes": [
            {
                "outcome_id": "oc1",
                "narrative": f"Vulnerable displaced and host households maintain uninterrupted access to dignified, safe {theme} services.",
                "indicators": [
                    {
                        "indicator_id": "oc1_i1",
                        "narrative": "At least 85% of target population accessing standard emergency allocations per Sphere guidelines",
                        "target_value": 85.0,
                        "unit": "percent",
                        "baseline": 0.0,
                        "timeframe": "by month 12",
                        "means_of_verification": "Periodic Post-Distribution Monitoring (PDM) reports, community feedback logs, Third-Party Monitoring (TPM) audits.",
                        "assumptions": "Catchment security allows beneficiaries safe daytime access to service facilities.",
                        "disaggregated_by": ["gender", "age", "disability"],
                    }
                ],
                "outputs": [
                    {
                        "output_id": "op1",
                        "narrative": "Essential community infrastructure rehabilitated, solarized, and handed over to gender-balanced local committees.",
                        "indicators": [
                            {
                                "indicator_id": "op1_i1",
                                "narrative": "12 critical facilities fully operationalized and 24 local committee members (50% female) trained in maintenance",
                                "target_value": 12.0,
                                "unit": "facilities",
                                "baseline": 0.0,
                                "timeframe": "by month 9",
                                "means_of_verification": "Engineering handover certificates, training attendance rosters with SADD disaggregation, water quality test certificates.",
                                "assumptions": "Equipment clearance and technical hardware supply lines remain uninterrupted.",
                                "disaggregated_by": ["gender", "age", "disability"],
                            }
                        ],
                        "activities": [
                            "Act 1.1.1: Rapid technical assessment and site baseline survey.",
                            "Act 1.1.2: Competitive procurement and solar installation.",
                            "Act 1.1.3: Community hygiene and protection promotion campaigns.",
                        ],
                    }
                ],
            }
        ],
        "theory_of_change_narrative": (
            "Immediate inputs (procurement, staffing) enable rapid field deployment; "
            "rehabilitated infrastructure and trained committees deliver Sphere-standard services; "
            "sustained dignified access for displaced and host households reduces excess mortality "
            "and builds local resilience."
        ),
        "assumptions": [
            "Humanitarian corridors remain open for continuous aid delivery.",
            "Local authorities maintain operational MoUs and access permits.",
            "Community elders and women-led committees support site operations.",
        ],
    }
    return project_logframe_to_matrix(structured)


def project_logframe_to_matrix(structured: Dict[str, Any], harden: bool = False) -> Dict[str, Any]:
    """Project a structured LogicalFramework onto the legacy flat 4x4 matrix.

    The returned dict carries BOTH the canonical structured fields AND a
    `matrix` projection (level/logic/indicators/mov/assumptions rows) so the
    existing wizard UI and Typst PDF pipeline keep working unchanged.

    harden=True: every indicator narrative is passed through the deterministic
    SMART hardening layer (missing dimensions get standard phrasing appended)
    before projection — no extra LLM call.
    """
    out = dict(structured or {})

    def _harden_text(s: str) -> str:
        if not harden:
            return s
        from engine.smart_parser import harden_indicator_text
        return harden_indicator_text(s)

    goal = str(out.get("goal", ""))
    goal_inds = out.get("goal_indicators", []) or []
    outcomes = out.get("outcomes", []) or []

    def _ind_text(ind) -> str:
        if isinstance(ind, dict):
            return _harden_text(str(ind.get("narrative", "")))
        return _harden_text(str(ind))

    rows = []
    # Goal row
    rows.append({
        "level": "Impact / Overall Goal",
        "logic": goal,
        "indicators": "; ".join(_ind_text(i) for i in goal_inds),
        "mov": "; ".join(str(i.get("means_of_verification", "")) for i in goal_inds if isinstance(i, dict)),
        "assumptions": "; ".join(str(i.get("assumptions", "")) for i in goal_inds if isinstance(i, dict)),
    })
    # Outcome rows (1-3) + output rows (1-5 per outcome)
    for oc in outcomes:
        if not isinstance(oc, dict):
            continue
        rows.append({
            "level": f"Outcome {oc.get('outcome_id', '1')} (Specific Objective)",
            "logic": str(oc.get("narrative", "")),
            "indicators": "; ".join(_ind_text(i) for i in oc.get("indicators", []) or []),
            "mov": "; ".join(str(i.get("means_of_verification", "")) for i in oc.get("indicators", []) if isinstance(i, dict)),
            "assumptions": "; ".join(str(i.get("assumptions", "")) for i in oc.get("indicators", []) if isinstance(i, dict)),
        })
        for op in oc.get("outputs", []) or []:
            if not isinstance(op, dict):
                continue
            rows.append({
                "level": f"Output {op.get('output_id', '1.1')}",
                "logic": str(op.get("narrative", "")),
                "indicators": "; ".join(_ind_text(i) for i in op.get("indicators", []) or []),
                "mov": "; ".join(str(i.get("means_of_verification", "")) for i in op.get("indicators", []) if isinstance(i, dict)),
                "assumptions": "; ".join(str(i.get("assumptions", "")) for i in op.get("indicators", []) if isinstance(i, dict)),
            })
    out["matrix"] = rows
    return out


def generate_narrative_sections(logframe_data: Dict[str, Any], context_data: Dict[str, Any], donor: str = "OCHA_CBPF") -> Dict[str, str]:
    """Generate donor-compliant narrative sections adhering to character limits.

    Call-aware (VISION): when the donor manifest defines its OWN mandatory
    sections (call-ingested donors like UNFPA CEFM with 15 Annex sections),
    the generator writes THOSE sections — not the legacy OCHA 5. Char limits
    come from manifest max_char_limits when present (default 3000).
    """
    profile = get_donor_profile(donor)
    country = context_data.get("country", "Target Country")
    theme = context_data.get("theme", "Emergency Response")
    donor_reqs = _donor_manifest_context(donor)  # call-aware: what THIS donor requires

    # ── Resolve the ACTUAL manifest (call-ingested donors included) ────────
    try:
        from engine.yaml_rules import YamlDonorRuleLoader
        from engine.donor_resolver import resolve_donor_id

        loader = YamlDonorRuleLoader()
        yaml_donor = resolve_donor_id(donor, loader)
        manifest = loader.load(yaml_donor)
    except Exception:
        manifest = None

    # Section plan: manifest sections win over legacy donor_rules profile
    if manifest is not None and manifest.mandatory_sections:
        plan = []
        for key in manifest.mandatory_sections:
            plan.append({
                "key": key,
                "title": key.replace("_", " ").title(),
                "max_chars": int((manifest.max_char_limits or {}).get(key, 3000)),
            })
    else:
        plan = [{"key": s["key"], "title": s["title"], "max_chars": s["max_chars"]} for s in profile["sections"]]

    # ── Live evidence (Sightline bridge — ReliefWeb/HDX, no code move) ─────
    evidence_block = ""
    try:
        from engine.evidence import collect_evidence, evidence_to_prompt

        country_code = _country_code_for(context_data.get("country", ""))
        ev = collect_evidence(
            country=_ascii_country(context_data.get("country", "")),
            theme=theme,
            country_code=country_code,
        )
        evidence_block = evidence_to_prompt(ev, max_chars=2500)
    except Exception as e:
        logger.debug("Evidence collection skipped: %s", e)

    sections = {}
    for sec in plan:
        key = sec["key"]
        title = sec["title"]
        max_c = sec["max_chars"]

        prompt = f"""
        Draft the '{title}' section for a {donor} proposal in {country} ({theme}).
        Maximum Character Limit: {max_c} characters. Do NOT exceed this limit under any circumstance.
        Context: {context_data.get('needs_assessment', '')}
        Logframe Summary: {json.dumps(logframe_data.get('matrix', []))}

        DONOR REQUIREMENTS (from the call manifest — write to satisfy these):
        {donor_reqs}

        {evidence_block}

        When you use a fact from the evidence, cite it inline as [ref: SIGHTLINE_<SOURCE>]
        (e.g. [ref: SIGHTLINE_SITREPS]). Do NOT invent citations.

        Write direct, high-impact donor text. Return ONLY the section narrative.
        """
        text = _call_llm(prompt)
        if text:
            # Enforce hard ceiling
            sections[key] = text[:max_c].strip()
        else:
            # Deterministic donor text within limit
            fallback_text = (
                f"This emergency intervention addresses acute multi-sectoral gaps in {country} within the {theme} sector. "
                f"In alignment with {profile['name']} criteria, the action directly prioritizes vulnerable displaced populations (IDPs, returnees, and host communities). "
                f"The response utilizes Sphere Minimum Standards and IASC guidelines, integrating strict PSEA safeguards, community engagement mechanisms, and cost-effective delivery frameworks. "
                f"Continuous monitoring through KoboToolbox and PDM surveys will ensure accountability to affected populations."
            )
            sections[key] = fallback_text[:max_c]

    return sections
