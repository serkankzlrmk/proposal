"""
proposal/engine/generator.py — AI Generator for Theory of Change, Logframe Matrix & Narrative.
"""

import json
import logging
from typing import Any, Dict, List
import httpx

try:
    from config import OPENROUTER_API_KEY, LLM_BASE_URL, LLM_MODEL
    from engine.donor_rules import get_donor_profile
except ImportError:
    from proposal.config import OPENROUTER_API_KEY, LLM_BASE_URL, LLM_MODEL
    from proposal.engine.donor_rules import get_donor_profile

logger = logging.getLogger(__name__)


def _call_llm(prompt: str, system_prompt: str = "", temperature: float = 0.3) -> str:
    """Call OpenRouter or LLM provider with fallback heuristics."""
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
    try:
        with httpx.Client(timeout=45.0) as client:
            resp = client.post(f"{LLM_BASE_URL}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning("LLM API call failed: %s; using deterministic fallback.", e)
        return ""


def generate_toc(context_data: Dict[str, Any], donor: str = "OCHA_CBPF") -> Dict[str, Any]:
    """Generate structured Theory of Change DAG nodes and causal links."""
    country = context_data.get("country", "Target Region")
    theme = context_data.get("theme", "Emergency Response")
    needs = context_data.get("needs_assessment", "")

    prompt = f"""
    Create a structured Theory of Change (ToC) for a humanitarian project in {country} focusing on {theme}.
    Context: {needs}
    Donor Format: {donor}

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
    """Generate 4x4 Logical Framework matrix matching donor standards."""
    country = context_data.get("country", "Target Country")
    theme = context_data.get("theme", "Emergency WASH & Multi-sector")

    prompt = f"""
    Generate a complete 4x4 Logframe Matrix for {donor} guidelines in {country} for {theme}.
    Return ONLY a JSON object with this schema:
    {{
      "matrix": [
        {{
          "level": "Impact / Overall Goal",
          "logic": "...",
          "indicators": "...",
          "mov": "...",
          "assumptions": "..."
        }},
        {{
          "level": "Outcome 1 (Specific Objective)",
          "logic": "...",
          "indicators": "...",
          "mov": "...",
          "assumptions": "..."
        }},
        {{
          "level": "Output 1.1",
          "logic": "...",
          "indicators": "...",
          "mov": "...",
          "assumptions": "..."
        }},
        {{
          "level": "Activities",
          "logic": "...",
          "indicators": "...",
          "mov": "...",
          "assumptions": "..."
        }}
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
            logger.debug("Failed to parse LLM Logframe JSON: %s", e)

    # Standard humanitarian 4x4 matrix
    return {
        "matrix": [
            {
                "level": "Impact / Overall Goal",
                "logic": f"Reduced excess morbidity, mortality, and vulnerability among conflict-affected IDPs and host communities in {country}.",
                "indicators": "Crude Mortality Rate (CMR) < 0.5 / 10,000 / day; Global Acute Malnutrition (GAM) prevalence < 10% in catchment zone.",
                "mov": "UN OCHA Cluster Surveys, Ministry of Health Epidemiological Bulletins, SMART Assessment Reports.",
                "assumptions": "Political stability permits ongoing humanitarian agency operations without prolonged blockades.",
            },
            {
                "level": "Outcome 1 (Specific Objective)",
                "logic": f"Vulnerable displaced and host households maintain uninterrupted access to dignified, safe {theme} services.",
                "indicators": ">= 85% of target population accessing standard emergency allocations per Sphere guidelines; >= 90% user satisfaction.",
                "mov": "Periodic Post-Distribution Monitoring (PDM) reports, community feedback logs, Third-Party Monitoring (TPM) audits.",
                "assumptions": "Catchment security allows beneficiaries safe daytime access to service facilities.",
            },
            {
                "level": "Output 1.1",
                "logic": f"Essential community infrastructure rehabilitated, solarized, and handed over to gender-balanced local committees.",
                "indicators": "12 critical facilities fully operationalized; 24 local committee members (50% female) trained in maintenance.",
                "mov": "Engineering handover certificates, training attendance rosters with SADD disaggregation, water quality test certificates.",
                "assumptions": "Equipment clearance and technical hardware supply lines remain uninterrupted.",
            },
            {
                "level": "Activities",
                "logic": f"Act 1.1.1: Rapid technical assessment and site baseline survey.\nAct 1.1.2: Competitive procurement and solar installation.\nAct 1.1.3: Community hygiene and protection promotion campaigns.",
                "indicators": "Milestone delivery >= 95% against workplan timeline; 100% of procured items verified against Sphere specifications.",
                "mov": "Weekly contractor field progress logs, photographic verification geo-tagged dossiers, monthly financial ledgers.",
                "assumptions": "Community leadership buy-in and peaceful coexistence between displaced and host populations.",
            },
        ]
    }


def generate_narrative_sections(logframe_data: Dict[str, Any], context_data: Dict[str, Any], donor: str = "OCHA_CBPF") -> Dict[str, str]:
    """Generate donor-compliant narrative sections adhering to character limits."""
    profile = get_donor_profile(donor)
    country = context_data.get("country", "Target Country")
    theme = context_data.get("theme", "Emergency Response")

    sections = {}
    for sec in profile["sections"]:
        key = sec["key"]
        title = sec["title"]
        max_c = sec["max_chars"]

        prompt = f"""
        Draft the '{title}' section for a {donor} proposal in {country} ({theme}).
        Maximum Character Limit: {max_c} characters. Do NOT exceed this limit under any circumstance.
        Context: {context_data.get('needs_assessment', '')}
        Logframe Summary: {json.dumps(logframe_data.get('matrix', []))}

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
