"""Tests for the YAML-driven donor rule engine (deterministic, 0/1 style).

Covers the NotebookLM spec:
  - manifest loading + graceful fallback
  - all 5 scoring formulas
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
            "project_summary": "Summary with citation [ref: RW-001] and SADD disaggregation.",
            "humanitarian_situation": "Context paragraph [source: HDX].",
            "needs_assessment": "Gap analysis with protection and gender keywords.",
            "beneficiaries": "Targets 12,000 IDPs [ref: OCHA].",
            "justification": "Local partner and cluster coordination.",
        },
        "logframe_data": {"matrix": [
            {"indicators": ">= 85% households access 15L/person/day"},
            {"indicators": "30% reduction in diarrheal cases"},
        ]},
        "budget_data": {"overhead_percent": 7.0},
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
    # Fallback should not crash and should return a score
    assert result["total_score"] >= 0
    assert len(result["trace"]) == 5


def test_missing_manifest_logs_warning(engine, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        engine.score("nope_donor", {"setup_id": "s1"})
    assert any("falling back" in r.message for r in caplog.records)


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
    # 3 of 5 cited -> ratio 0.60 < 0.75 min -> 20/25
    result = engine.score("ocha_cbpf", prop)
    sc = [t for t in result["trace"] if t["criterion"] == "source_citations"][0]
    assert sc["score"] == pytest.approx(20.0, abs=0.1)
    assert sc["max_score"] == 25


def test_donor_keywords_score(engine):
    result = engine.score("ocha_cbpf", make_proposal())
    kw = [t for t in result["trace"] if t["criterion"] == "donor_keywords"][0]
    assert kw["max_score"] == 15
    assert kw["score"] > 0


def test_budget_alignment_at_cap(engine):
    prop = make_proposal()
    result = engine.score("ocha_cbpf", prop)
    ba = [t for t in result["trace"] if t["criterion"] == "budget_alignment"][0]
    assert ba["score"] == 10.0  # 7% == 7% cap -> no penalty


def test_budget_alignment_over_cap(engine):
    prop = make_proposal()
    prop["budget_data"] = {"overhead_percent": 8.19}  # 17% over cap
    result = engine.score("ocha_cbpf", prop)
    ba = [t for t in result["trace"] if t["criterion"] == "budget_alignment"][0]
    assert ba["score"] == pytest.approx(8.3, abs=0.1)  # 10 * (1 - 0.17)


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
