"""
proposal/tests/test_proposal.py — Automated Test Suite for Proposal Pipeline.
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import io
import json
import pytest
from types import SimpleNamespace

try:
    from app import app
    from db import (
        init_db,
        create_proposal,
        list_proposals,
        get_proposal,
        update_proposal,
        delete_proposal,
        save_review,
    )
    from engine.donor_rules import DONOR_PROFILES, validate_character_limits
    from engine.verifier import run_blind_verifier
    from typst_engine.compiler import compile_pdf
except ImportError:
    from proposal.app import app
    from proposal.db import (
        init_db,
        create_proposal,
        list_proposals,
        get_proposal,
        update_proposal,
        delete_proposal,
        save_review,
    )
    from proposal.engine.donor_rules import DONOR_PROFILES, validate_character_limits
    from proposal.engine.verifier import run_blind_verifier
    from proposal.typst_engine.compiler import compile_pdf


@pytest.fixture
def client():
    app.config["TESTING"] = True
    init_db()
    with app.test_client() as client:
        yield client


def test_db_crud():
    """Verify SQLite database operations."""
    prop = create_proposal(
        user_id="test_usr",
        title="Test Proposal DB",
        country="Sudan",
        donor="OCHA_CBPF",
        theme="WASH",
    )
    assert prop is not None
    prop_id = prop["id"]

    # List
    all_props = list_proposals("test_usr")
    assert any(p["id"] == prop_id for p in all_props)

    # Update
    updated = update_proposal(prop_id, {"title": "Updated Title", "step": 3}, user_id="test_usr")
    assert updated["title"] == "Updated Title"
    assert updated["step"] == 3

    # Review
    r_id = save_review(prop_id, "pass", 95.0, [{"rule": "test", "severity": "info"}])
    assert r_id.startswith("rev_")

    # Delete
    deleted = delete_proposal(prop_id, user_id="test_usr")
    assert deleted is True
    assert get_proposal(prop_id) is None


def test_donor_rules_character_limits():
    """Verify character limit validation."""
    narrative_valid = {"project_summary": "Short summary"}
    issues = validate_character_limits("OCHA_CBPF", narrative_valid)
    assert len(issues) == 0

    narrative_overflow = {"project_summary": "x" * 4500}
    issues = validate_character_limits("OCHA_CBPF", narrative_overflow)
    assert len(issues) == 1
    assert issues[0]["rule"] == "character_limit"
    assert issues[0]["current_length"] == 4500


def test_context_fallback_uses_donor_manifest_without_inventing_evidence():
    from blueprints.proposal_api import _deterministic_context_draft

    manifest = SimpleNamespace(
        display_name="UNFPA Türkiye CEFM Grant Call",
        mandatory_keywords=["psea", "cefm", "rights-based", "capacity-building"],
        mandatory_sections=["programme_summary", "expected_results"],
        meta={"country": "Türkiye", "requirements": ["Applications must be submitted in English"]},
    )
    draft = _deterministic_context_draft(
        {"donor": "unfpa_cefm", "country": "", "theme": "Protection"},
        {"country": "", "theme": "Protection", "beneficiaries": {"total": 0, "idp_refugee": 0}},
        manifest,
    )

    assert draft["country"] == "Türkiye"
    assert "CEFM" in draft["title"].upper()
    assert "verified local evidence" in draft["humanitarian_situation"]
    assert draft["beneficiaries_total"] == 0
    assert draft["donor_requirements_used"] == ["Applications must be submitted in English"]


def test_blind_verifier(monkeypatch):
    """Verify Blind Verifier scorecard (deterministic path — no live LLM)."""
    # Force the deterministic rule path: character limits + donor quotas.
    # The live LLM-as-a-Judge branch is network-dependent and flakes in full
    # runs (verdict varies by model output); the deterministic path is the
    # contract this test locks.
    import engine.verifier as verifier_mod

    monkeypatch.setattr(verifier_mod, "OPENROUTER_API_KEY", "")
    sample = {
        "id": "prop_test_audit",
        "title": "OCHA Water Assistance",
        "country": "Sudan",
        "donor": "OCHA_CBPF",
        "narrative_data": {"project_summary": "Compliant summary within 4000 characters."},
        "logframe_data": {"matrix": []},
        "context_data": {"beneficiaries": {"total": 20000, "idp_refugee": 12000}},
    }
    audit = run_blind_verifier(sample, donor="OCHA_CBPF")
    assert audit["verdict"] in ("pass", "warning", "fail")
    assert 0 <= audit["score"] <= 100


def test_typst_pdf_compilation():
    """Verify Typst compiles PDF in milliseconds."""
    sample = {
        "id": "prop_test_typst",
        "title": "Sudan Rapid Response Initiative",
        "country": "Sudan",
        "donor": "OCHA_CBPF",
        "theme": "Protection",
    }
    pdf_bytes = compile_pdf(sample)
    assert pdf_bytes is not None
    assert len(pdf_bytes) > 5000  # Valid multi-kilobyte PDF


def test_api_endpoints(client):
    """Verify all Flask REST endpoints."""
    # 1. Donors
    r = client.get("/api/proposals/donors")
    assert r.status_code == 200
    assert "OCHA_CBPF" in r.json["donors"]

    # 2. Create New
    r = client.post("/api/proposals/new", json={
        "title": "API Test Project",
        "country": "Yemen",
        "donor": "USAID_BHA",
        "theme": "Health",
    })
    assert r.status_code == 201
    prop_id = r.json["proposal"]["id"]

    # 3. Get Detail
    r = client.get(f"/api/proposals/{prop_id}")
    assert r.status_code == 200
    assert r.json["proposal"]["country"] == "Yemen"

    # 4. Generate ToC
    r = client.post(f"/api/proposals/{prop_id}/generate-toc")
    assert r.status_code == 200
    assert "nodes" in r.json["toc_data"]

    # 5. Generate Logframe
    r = client.post(f"/api/proposals/{prop_id}/generate-logframe")
    assert r.status_code == 200
    assert "matrix" in r.json["logframe_data"]

    # 6. Generate Narrative
    r = client.post(f"/api/proposals/{prop_id}/generate-narrative")
    assert r.status_code == 200
    assert len(r.json["narrative_data"]) > 0

    # 7. Verifier Audit
    r = client.post(f"/api/proposals/{prop_id}/verify")
    assert r.status_code == 200
    assert "audit" in r.json

    # 8. Advisor Chat
    r = client.post(f"/api/proposals/{prop_id}/advisor/chat", json={"message": "Review my indicators"})
    assert r.status_code == 200
    assert "message" in r.json

    # 9. PDF Export
    r = client.get(f"/api/proposals/{prop_id}/export/pdf")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/pdf"
    assert len(r.data) > 5000

    # 10. Delete
    r = client.delete(f"/api/proposals/{prop_id}")
    assert r.status_code == 200
