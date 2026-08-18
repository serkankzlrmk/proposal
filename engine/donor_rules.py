"""
proposal/engine/donor_rules.py — Donor Guidelines, Compliance Rules & Section Definitions.

Encodes standards for UN OCHA CBPF (GPPi 8+3), USAID/BHA (EAG), and EU EuropeAid/PRAG.
"""

from typing import Dict, Any, List

DONOR_PROFILES: Dict[str, Dict[str, Any]] = {
    "OCHA_CBPF": {
        "name": "UN OCHA Country-Based Pooled Funds (CBPF)",
        "template": "GPPi 8+3 Harmonized Proposal",
        "currency": "USD",
        "sections": [
            {
                "key": "project_summary",
                "title": "Project Summary",
                "max_chars": 4000,
                "required": True,
                "description": "Executive summary with Sex, Age, Disability Disaggregated (SADD) target needs.",
            },
            {
                "key": "humanitarian_situation",
                "title": "Humanitarian Situation & Context",
                "max_chars": 4000,
                "required": True,
                "description": "Background of the crisis, geographical prioritization, and recent emergency triggers.",
            },
            {
                "key": "needs_assessment",
                "title": "Needs Assessment",
                "max_chars": 4000,
                "required": True,
                "description": "Specific sectoral gap analysis, protection risks (GBV, trafficking), and coping mechanisms.",
            },
            {
                "key": "beneficiaries",
                "title": "Beneficiary Identification",
                "max_chars": 4000,
                "required": True,
                "description": "IDPs, refugees, returnees, host community breakdowns by gender and age.",
            },
            {
                "key": "justification",
                "title": "Strategic Justification & Value-Add",
                "max_chars": 4000,
                "required": True,
                "description": "Comparative operational advantage, cluster coordination, and local footprint.",
            },
        ],
        "quota_requirements": {
            "sadd_disaggregation": True,
            "cluster_coordination_mandatory": True,
        },
        "max_file_size_mb": 4.86,
    },
    "USAID_BHA": {
        "name": "USAID Bureau for Humanitarian Assistance (BHA)",
        "template": "Emergency Application Guidelines (EAG)",
        "currency": "USD",
        "sections": [
            {
                "key": "executive_summary",
                "title": "Executive Summary",
                "max_chars": 4000,
                "required": True,
                "description": "Overview of emergency response, target population, and key measurable outputs.",
            },
            {
                "key": "program_rationale",
                "title": "Program Rationale & Crisis Background",
                "max_chars": 5000,
                "required": True,
                "description": "Evidence-based justification using Sphere standards and rapid assessment findings.",
            },
            {
                "key": "beneficiary_targeting",
                "title": "Beneficiary Targeting & Quotas",
                "max_chars": 4000,
                "required": True,
                "description": "Must ensure minimum 50% target are refugees, IDPs, or conflict-displaced persons.",
            },
            {
                "key": "risk_management",
                "title": "Risk Management, Security & PSEA",
                "max_chars": 4000,
                "required": True,
                "description": "IASC 6 Core Principles, PSEA Code of Conduct, and duty of care safety plan.",
            },
            {
                "key": "sustainability_exit",
                "title": "Sustainability & Local Handover",
                "max_chars": 4000,
                "required": True,
                "description": "Local ministry handover (MoH MoU for health) or market transition strategy.",
            },
        ],
        "quota_requirements": {
            "min_displaced_ratio": 0.50,  # At least 50% IDP/refugee
            "psea_mandatory": True,
            "sphere_standards_mandatory": True,
        },
        "max_file_size_mb": 10.0,
    },
    "EU_PRAG": {
        "name": "European Union / EuropeAid (PRAG & ECHO)",
        "template": "PRAG Concept & Full Application Guidelines",
        "currency": "EUR / USD",
        "sections": [
            {
                "key": "context_relevance",
                "title": "Relevance of the Action",
                "max_chars": 4500,
                "required": True,
                "description": "Alignment with EU priorities, target country strategy, and stakeholder synergy.",
            },
            {
                "key": "methodology",
                "title": "Methodology & Operational Strategy",
                "max_chars": 5000,
                "required": True,
                "description": "Theory of Change, Logframe operationalization, and implementation pacing.",
            },
            {
                "key": "capacity",
                "title": "Organizational & Financial Capacity",
                "max_chars": 3500,
                "required": True,
                "description": "Track record, budget execution capacity (minimum 12/20 score required).",
            },
            {
                "key": "cost_effectiveness",
                "title": "Cost-Effectiveness & Value for Money",
                "max_chars": 4000,
                "required": True,
                "description": "Unit cost efficiency ratio analysis and output-to-budget multiplier.",
            },
            {
                "key": "sustainability_visibility",
                "title": "Sustainability & EU Visibility Plan",
                "max_chars": 3500,
                "required": True,
                "description": "Institutional institutionalization, environmental safeguard, and EU flag co-branding.",
            },
        ],
        "quota_requirements": {
            "capacity_threshold_score": 12,  # 12 out of 20
            "cost_effectiveness_weighted": True,
        },
        "max_file_size_mb": 10.0,
    },
}


def get_donor_profile(donor_key: str) -> Dict[str, Any]:
    """Retrieve donor guidelines, defaulting to OCHA CBPF."""
    return DONOR_PROFILES.get(donor_key, DONOR_PROFILES["OCHA_CBPF"])


def validate_character_limits(donor_key: str, narrative_data: Dict[str, str]) -> List[Dict[str, Any]]:
    """Check section text lengths against donor-specific character ceilings."""
    profile = get_donor_profile(donor_key)
    issues = []
    for sec in profile["sections"]:
        key = sec["key"]
        max_c = sec["max_chars"]
        content = narrative_data.get(key, "")
        char_count = len(content)
        if char_count > max_c:
            issues.append({
                "rule": "character_limit",
                "severity": "critical",
                "section": sec["title"],
                "section_key": key,
                "current_length": char_count,
                "max_allowed": max_c,
                "message": f"Section '{sec['title']}' exceeds character limit ({char_count}/{max_c} chars).",
            })
    return issues
