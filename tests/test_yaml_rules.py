"""Tests for the YAML-driven donor rule engine (deterministic, 0/1 style).

Covers the NotebookLM spec + refinement feedback:
  - manifest loading + graceful fallback
  - all 5 scoring formulas
  - SMART validation: measurable/time_bound/disaggregation patterns
  - source citations: standardized [ref: ID] format + registry verification
  - quota requirements as HARD PASS/FAIL gates (AUTOMATIC_REJECTION)
  - trace structure with target_step/target_field
  - missing-rule -> WARNING_MISSING_RULE behavior
"""

import pytest

from engine.yaml_rules import YamlDonorRuleLoader, DonorScoringEngine, evaluate_rule_safely


@pytest.fixture()
def engine():
    loader = YamlDonorRuleLoader()
    return DonorScoringEngine(loader)


def make_proposal(**over):
    base = {
        "setup_id": "setup_test",
        "narrative_data": {
            "project_summary": "Summary with citation [ref: RW-001], SADD disaggregation and GBV protection mainstreaming.",
            "humanitarian_situation": "Context paragraph [source: HDX-002] with humanitarian principles.",
            "needs_assessment": "Gap analysis with protection, PSEA and gender keywords, cluster coordination, sphere standards.",
            "beneficiaries": "Targets 12,000 IDPs [ref: OCHA-003].",
            "justification": "Local partner and SADD disaggregated targets.",
        },
        "logframe_data": {"matrix": [
            {"indicators": ">= 85% households access 15L/person/day by month 12"},
            {"indicators": "30% reduction in diarrheal cases among girls under 5"},
        ]},
        "budget_data": {"overhead_percent": 7.0},
        "context_data": {"beneficiaries": {"total": 20000, "idp_refugee": 11000}},
    }
    base.update(over)
    return base


# ── Manifest loading ──────────────────────────────────────────────────────
def test_list_donors_returns_yaml_files(engine):
    donors = engine.loader.list_donors()
    assert "ocha_cbpf" in donors
    assert "usaid_bha" in donors
    assert "eu_prag" in donors


def test_unknown_donor_falls_back(engine):
    result = engine.score("totally_unknown", {"setup_id": "s1"})
    assert result["total_score"] >= 0
    assert len(result["trace"]) == 5


# ── Scoring formulas ──────────────────────────────────────────────────────
def test_section_coverage_full(engine):
    result = engine.score("ocha_cbpf", make_proposal())
    sc = [t for t in result["trace"] if t["criterion"] == "section_coverage"][0]
    assert sc["score"] == 30.0
    assert sc["max_score"] == 30


def test_section_coverage_partial(engine):
    prop = make_proposal()
    prop["narrative_data"] = {"project_summary": "only one section"}
    result = engine.score("ocha_cbpf", prop)
    sc = [t for t in result["trace"] if t["criterion"] == "section_coverage"][0]
    assert sc["score"] == pytest.approx(6.0)  # 1/5 * 30


def test_source_citations_partial(engine):
    prop = make_proposal()
    result = engine.score("ocha_cbpf", prop)
    sc = [t for t in result["trace"] if t["criterion"] == "source_citations"][0]
    assert sc["score"] > 0
    assert sc["max_score"] == 25
    assert "draft (format-only)" in sc["details"]  # no reference registry


def test_source_citations_registry_verified(engine):
    prop = make_proposal(references=[{"id": "RW-001"}, {"id": "HDX-002"}])
    result = engine.score("ocha_cbpf", prop)
    sc = [t for t in result["trace"] if t["criterion"] == "source_citations"][0]
    assert "registry-verified" in sc["details"]


def test_source_citations_unmatched_references(engine):
    prop = make_proposal(references=[{"id": "ZZZ-999"}])  # none of the cites match
    result = engine.score("ocha_cbpf", prop)
    sc = [t for t in result["trace"] if t["criterion"] == "source_citations"][0]
    # All citations fail grounding -> cited stays 0
    assert sc["score"] == 0.0


def test_donor_keywords_score(engine):
    result = engine.score("ocha_cbpf", make_proposal())
    kw = [t for t in result["trace"] if t["criterion"] == "donor_keywords"][0]
    assert kw["max_score"] == 15
    assert kw["score"] > 0


def test_budget_alignment_at_cap(engine):
    result = engine.score("ocha_cbpf", make_proposal())
    ba = [t for t in result["trace"] if t["criterion"] == "budget_alignment"][0]
    assert ba["score"] == 10.0


def test_budget_alignment_over_cap(engine):
    prop = make_proposal()
    prop["budget_data"] = {"overhead_percent": 8.19}
    result = engine.score("ocha_cbpf", prop)
    ba = [t for t in result["trace"] if t["criterion"] == "budget_alignment"][0]
    # Canonical SPEC LINEAR PENALTY: max(0, 10 - (8.19 - 7.0) * 5) = 4.05
    assert ba["score"] == pytest.approx(4.05, abs=0.05)


def test_budget_cap_is_donor_specific(engine):
    # USAID BHA allows 10% de minimis vs OCHA 7%
    prop = make_proposal()
    prop["budget_data"] = {"overhead_percent": 9.0}
    r_ocha = engine.score("ocha_cbpf", prop)
    r_usaid = engine.score("usaid_bha", prop)
    ba_ocha = [t for t in r_ocha["trace"] if t["criterion"] == "budget_alignment"][0]
    ba_usaid = [t for t in r_usaid["trace"] if t["criterion"] == "budget_alignment"][0]
    assert ba_ocha["score"] < ba_usaid["score"]  # 9% penalized under 7% cap, ok under 10%


# ── SMART validation patterns ─────────────────────────────────────────────
def test_smart_measurable_requires_quantity(engine):
    prop = make_proposal()
    prop["logframe_data"] = {"matrix": [{"indicators": "improve access to clean water"}]}
    result = engine.score("ocha_cbpf", prop)
    sc = [t for t in result["trace"] if t["criterion"] == "smart_criteria"][0]
    # measurable (no number) and achievable (no %) fail
    assert sc["score"] < 20.0


def test_smart_time_bound_detection(engine):
    prop = make_proposal()
    prop["logframe_data"] = {"matrix": [{"indicators": "50% coverage by end of 2026"}]}
    result = engine.score("ocha_cbpf", prop)
    sc = [t for t in result["trace"] if t["criterion"] == "smart_criteria"][0]
    assert sc["score"] > 0


def test_smart_disaggregation_detection(engine):
    prop = make_proposal()
    prop["logframe_data"] = {"matrix": [{"indicators": "40% of girls aged 6-12 enrolled by Q2"}]}
    result = engine.score("ocha_cbpf", prop)
    sc = [t for t in result["trace"] if t["criterion"] == "smart_criteria"][0]
    assert sc["score"] > 12.0  # disaggregation + time_bound + measurable


# ── Quota gates ───────────────────────────────────────────────────────────
def test_quota_sadd_present(engine):
    result = engine.score("ocha_cbpf", make_proposal())
    assert result["eligibility"]["passed"] is True


def test_quota_sadd_missing_rejects(engine):
    prop = make_proposal()
    prop["narrative_data"] = {
        "project_summary": "Plain summary without any target breakdown terms.",
        "humanitarian_situation": "Context only.",
        "needs_assessment": "Gap analysis.",
        "beneficiaries": "Targets 12,000 people.",
        "justification": "Local partner presence.",
    }
    result = engine.score("ocha_cbpf", prop)
    assert result["eligibility"]["status"] == "AUTOMATIC_REJECTION"
    assert "sadd_disaggregation_mandatory" in result["eligibility"]["failed_quotas"]


def test_quota_min_displaced_ratio(engine):
    prop = make_proposal()
    prop["context_data"] = {"beneficiaries": {"total": 20000, "idp_refugee": 5000}}  # 25% < 50%
    result = engine.score("usaid_bha", prop)
    assert result["eligibility"]["status"] == "AUTOMATIC_REJECTION"
    assert "min_displaced_ratio" in result["eligibility"]["failed_quotas"]


def test_quota_unverifiable_does_not_fail(engine):
    prop = make_proposal()
    prop["context_data"] = {}  # no beneficiaries -> min_displaced_ratio not verifiable
    # USAID also mandates PSEA + Sphere: keep them satisfied
    prop["narrative_data"]["risk_management"] = "PSEA code of conduct and sphere standards applied."
    result = engine.score("usaid_bha", prop)
    quota_check = [c for c in result["eligibility"]["checks"] if c["quota"] == "min_displaced_ratio"][0]
    assert quota_check["verifiable"] is False
    assert result["eligibility"]["passed"] is True


def test_high_score_failed_quota_still_rejected(engine):
    """90/100 text score + failed donor quota = AUTOMATIC_REJECTION (desk review)."""
    prop = make_proposal()
    # USAID schema: use its mandatory section keys so text score stays high
    prop["narrative_data"] = {
        "executive_summary": "Emergency WASH response summary [ref: RW-001] with PSEA and sphere standards.",
        "program_rationale": "Evidence-based rationale citing sphere standards and IDP displacement [source: HDX-002].",
        "beneficiary_targeting": "Targets 12,000 displaced persons with SADD disaggregation.",
        "risk_management": "PSEA code of conduct, do no harm, and security plan.",
        "sustainability_exit": "Local handover with ministry MoU and market transition.",
    }
    prop["context_data"] = {"beneficiaries": {"total": 1000, "idp_refugee": 100}}  # 10% < 50%
    result = engine.score("usaid_bha", prop)
    assert result["total_score"] > 70  # text passes threshold
    assert result["passed"] is False  # but eligibility gate blocks
    assert result["eligibility"]["status"] == "AUTOMATIC_REJECTION"


# ── Trace structure ───────────────────────────────────────────────────────
def test_trace_has_target_fields(engine):
    result = engine.score("ocha_cbpf", make_proposal())
    for t in result["trace"]:
        assert "criterion" in t
        assert "max_score" in t
        assert "target_step" in t
        assert "target_field" in t
        assert "details" in t


def test_total_score_matches_sum(engine):
    result = engine.score("ocha_cbpf", make_proposal())
    total = sum(t["score"] for t in result["trace"])
    assert result["total_score"] == pytest.approx(round(total, 1))


def test_pass_threshold(engine):
    good = engine.score("ocha_cbpf", make_proposal())
    assert good["passed"] is True
    bad = engine.score("ocha_cbpf", {"setup_id": "s_empty"})
    assert bad["passed"] is False


# ── Graceful fallback ─────────────────────────────────────────────────────
def test_evaluate_rule_safely_keyerror():
    def raises():
        raise KeyError("missing")

    result = evaluate_rule_safely(raises, default_weight=30)
    assert result["score"] == 0.0
    assert result["status"] == "WARNING_MISSING_RULE"


def test_evaluate_rule_safely_happy():
    def ok():
        return {"score": 10.0, "max_score": 10, "details": "ok"}

    result = evaluate_rule_safely(ok, default_weight=10)
    assert result["score"] == 10.0


# ── Root-level canonical schema (ARCHITECTURAL_DECISIONS #1) ─────────────
def test_manifest_is_root_level_pydantic(engine):
    """Loader returns a validated DonorManifest with root-level attributes."""
    manifest = engine.loader.load("ocha_cbpf")
    assert manifest.donor_id == "ocha_cbpf"
    assert manifest.display_name.startswith("UN OCHA")
    assert manifest.min_source_ratio == 0.75
    assert manifest.overhead_cap_percent == 7.0
    assert "sadd" in manifest.mandatory_keywords
    assert manifest.hard_eligibility_gates.get("sadd_disaggregation_mandatory") is True
    assert manifest.mandatory_sections == [
        "project_summary", "humanitarian_situation", "needs_assessment",
        "beneficiaries", "justification",
    ]


def test_manifest_root_gates_drive_engine(engine):
    """Canonical root-level hard_eligibility_gates drive AUTOMATIC_REJECTION."""
    prop = make_proposal()
    # Remove PSEA term (root-level psea_policy_mandatory) -> HARD FAIL
    prop["narrative_data"] = {
        "project_summary": "Summary with citation [ref: RW-001], SADD disaggregation and GBV protection mainstreaming.",
        "humanitarian_situation": "Context paragraph [source: HDX-002] with humanitarian principles.",
        "needs_assessment": "Gap analysis with protection, gender keywords, cluster coordination, sphere standards.",
        "beneficiaries": "Targets 12,000 IDPs [ref: OCHA-003].",
        "justification": "Local partner and SADD disaggregated targets.",
    }
    result = engine.score("ocha_cbpf", prop)
    assert result["eligibility"]["status"] == "AUTOMATIC_REJECTION"
    assert "psea_policy_mandatory" in result["eligibility"]["failed_quotas"]
    # Gate check carries the canonical key + human title
    psea_check = [c for c in result["eligibility"]["checks"] if c["quota"] == "psea_policy_mandatory"][0]
    assert psea_check["title"] == "PSEA Policy & Safeguarding"


def test_legacy_rules_shape_normalized(tmp_path, engine):
    """v1 nested rules: shape is normalized to the root-level canonical schema."""
    legacy = {
        "donor_id": "legacy_donor",
        "name": "Legacy Donor",
        "rules": {
            "sections": {
                "mandatory": ["alpha", "beta"],
                "limits": {"alpha": {"max_chars": 1000}},
            },
            "citations": {"min_source_ratio": 0.60},
            "keywords": {"expected_tokens": ["foo", "bar"]},
            "budget": {"max_overhead_percent": 9.0},
            "smart_indicators": {"required_dimensions": ["specific", "measurable"]},
            "quota_requirements": {"sadd_disaggregation": True},
        },
    }
    (tmp_path / "legacy_donor.yaml").write_text(
        __import__("yaml").safe_dump(legacy), encoding="utf-8"
    )
    loader = YamlDonorRuleLoader(donors_dir=tmp_path)
    manifest = loader.load("legacy_donor")
    assert manifest.display_name == "Legacy Donor"
    assert manifest.min_source_ratio == 0.60
    assert manifest.overhead_cap_percent == 9.0
    assert manifest.mandatory_keywords == ["foo", "bar"]
    assert manifest.mandatory_sections == ["alpha", "beta"]
    assert manifest.max_char_limits == {"alpha": 1000}
    # legacy quota key aliased to canonical gate key
    assert manifest.hard_eligibility_gates.get("sadd_disaggregation_mandatory") is True
