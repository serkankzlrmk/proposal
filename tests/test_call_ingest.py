"""
proposal/tests/test_call_ingest.py — Donor Call Ingestion Tests.

Covers the human-in-the-loop contract:
  - PDF text extraction (pymupdf)
  - deterministic regex extraction fallback (offline)
  - manifest draft building (None-safe coercion)
  - publish -> donors/<call_id>.yaml -> engine picks it up
  - API: ingest (review status) + publish + engine scoring with new donor
"""

import io
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import pytest
import pymupdf

try:
    from app import app
    from engine.call_ingest import (
        extract_pdf_text,
        extract_requirements,
        build_manifest_draft,
        save_manifest,
        verify_gates_against_text,
    )
    from engine.yaml_rules import YamlDonorRuleLoader, DonorScoringEngine
except ImportError:
    from proposal.app import app
    from proposal.engine.call_ingest import (
        extract_pdf_text,
        extract_requirements,
        build_manifest_draft,
        save_manifest,
        verify_gates_against_text,
    )
    from proposal.engine.yaml_rules import YamlDonorRuleLoader, DonorScoringEngine


def make_pdf_bytes(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 550, 780), text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


CALL_TEXT = """
ECHO EMERGENCY RESPONSE CALL FOR PROPOSALS 2026
Submission deadline: 15 November 2026

1. Scope: Life-saving WASH and protection assistance for conflict-affected
IDPs and host communities in Darfur, Sudan.

2. Mandatory requirements:
- All indicators must include Sex, Age and Disability Disaggregation (SADD).
- Applicants must demonstrate PSEA policy compliance and Code of Conduct.
- Cluster coordination with the WASH and Protection clusters is mandatory.
- Interventions must align with Sphere standards (15L/person/day minimum).
- At least 50% of beneficiaries must be IDPs or refugees.
- Overhead / indirect costs must not exceed 7 percent of direct costs.

3. Keywords: accountability to affected populations, do no harm,
protection mainstreaming, humanitarian principles.
"""


# ── PDF extraction ────────────────────────────────────────────────────────
def test_extract_pdf_text():
    pdf = make_pdf_bytes(CALL_TEXT)
    text = extract_pdf_text(pdf)
    assert "ECHO EMERGENCY RESPONSE" in text
    assert "Sphere standards" in text


def test_extract_pdf_text_empty():
    assert extract_pdf_text(b"not a pdf") == ""


# ── Deterministic extraction fallback (offline) ───────────────────────────
def test_regex_extract_requirements(monkeypatch):
    # Force the deterministic fallback: no LLM available
    import engine.call_ingest as ci

    monkeypatch.setattr(ci, "_call_llm", lambda *a, **k: "")
    extracted = extract_requirements(CALL_TEXT)
    assert "summary" in extracted
    assert "psea" in [k.lower() for k in extracted.get("mandatory_keywords", [])]
    gates = extracted.get("hard_gates") or {}
    assert gates.get("sadd_disaggregation_mandatory") is True
    assert gates.get("psea_policy_mandatory") is True
    assert gates.get("min_displaced_ratio") == pytest.approx(0.5)
    assert extracted.get("budget_cap_percent") == pytest.approx(7.0)


# ── Manifest draft (None-safe coercion) ───────────────────────────────────
def test_build_manifest_draft_none_safe():
    draft = build_manifest_draft("call_x", "Call X", {
        "summary": "s",
        "min_source_ratio": None,          # LLM may emit nulls
        "budget_cap_percent": None,
        "hard_gates": {"psea_policy_mandatory": True, "min_capacity_score": None},
        "mandatory_keywords": ["psea", "sadd"],
    })
    assert draft["min_source_ratio"] == 0.75   # default, no crash
    assert draft["overhead_cap_percent"] == 7.0
    assert draft["hard_eligibility_gates"] == {"psea_policy_mandatory": True}  # None gate dropped


# ── Publish -> engine pickup ──────────────────────────────────────────────
def test_save_manifest_and_engine_pickup(tmp_path, monkeypatch):
    monkeypatch.setattr("engine.call_ingest.DONORS_DIR", tmp_path)
    draft = build_manifest_draft("new_donor_2026", "New Donor 2026", {
        "summary": "Test call",
        "hard_gates": {"min_displaced_ratio": 0.5},
        "mandatory_keywords": ["psea", "sadd"],
        "budget_cap_percent": 8.0,
    })
    path = save_manifest("new_donor_2026", draft)
    assert path.exists()

    loader = YamlDonorRuleLoader(donors_dir=tmp_path)
    assert "new_donor_2026" in loader.list_donors()
    m = loader.load("new_donor_2026")
    assert m.overhead_cap_percent == 8.0
    assert m.hard_eligibility_gates.get("min_displaced_ratio") == 0.5

    # Engine scores against the published manifest immediately
    engine = DonorScoringEngine(loader)
    result = engine.score("new_donor_2026", {"setup_id": "s1", "narrative_data": {}})
    assert result["donor_id"] == "new_donor_2026"
    assert len(result["trace"]) == 5


# ── Anti-hallucination gate verification ──────────────────────────────────
def test_verify_gates_drops_unfounded():
    """LLM-claimed gates without textual evidence are dropped (VISION)."""
    unfpa_text = (
        "UNFPA Türkiye invites CSOs to submit grant proposals for the prevention "
        "of Child, Early, and Forced Marriages (CEFM). The project aims to enhance "
        "technical capacities of CSOs and strengthen multi-sectoral collaboration. "
        "Children and women at risk/survivors of CEFM have better access to quality "
        "protection and response services in line with international standards."
    )
    llm_gates = {
        "psea_policy_mandatory": True,          # NOT in call text -> drop
        "sphere_standards_mandatory": True,     # NOT in call text -> drop
        "min_displaced_ratio": 0.5,             # no IDP/refugee mention -> drop
    }
    verified = verify_gates_against_text(llm_gates, unfpa_text)
    assert verified == {}


def test_verify_gates_keeps_evidenced():
    """Gates with real textual evidence survive verification."""
    echo_text = (
        "Applicants must demonstrate PSEA compliance. Sphere standards apply: "
        "15L/person/day. At least 50% of beneficiaries must be IDPs."
    )
    gates = {
        "psea_policy_mandatory": True,
        "sphere_standards_mandatory": True,
        "min_displaced_ratio": 0.5,
    }
    assert verify_gates_against_text(gates, echo_text) == gates


# ── Evidence bridge (Sightline, no code move) ─────────────────────────────
def test_evidence_bridge_zero_crash_when_sightline_missing(monkeypatch):
    """Bridge must never break the pipeline when Sightline is absent."""
    import engine.evidence as ev

    monkeypatch.setattr(ev, "SIGHTLINE_ROOT", Path("/nonexistent/sightline"))
    monkeypatch.setattr(ev, "_loaded", False)
    monkeypatch.setattr(ev, "_available", False)
    assert ev.available() is False
    assert ev.search_sitreps(country="Turkey") is None
    assert ev.collect_evidence("Turkey") == {"source": "unavailable"}
    assert ev.evidence_to_prompt({"source": "unavailable"}) == ""


def test_evidence_prompt_rendering():
    """Evidence block renders only non-empty sources with citation hint."""
    from engine.evidence import evidence_to_prompt

    ev = {
        "source": "sightline_bridge",
        "sitreps": "UNICEF SitRep: 1.2M children affected",
        "hdx_overview": None,
        "refugees": "",
        "idps": "320K IDPs registered",
    }
    prompt = evidence_to_prompt(ev, max_chars=200)
    assert "EVIDENCE FROM LIVE SOURCES" in prompt
    assert "UNICEF SitRep" in prompt
    assert "320K IDPs" in prompt
    assert "HDX Country Overview" not in prompt  # empty source skipped
    assert "[ref: SIGHTLINE_<SOURCE>]" in prompt


def test_evidence_to_references_registry_entries():
    """Evidence converts to citation registry entries (SIGHTLINE_* ids)."""
    from engine.evidence import evidence_to_references

    ev = {
        "source": "sightline_bridge",
        "sitreps": "UNICEF SitRep data",
        "hdx_overview": None,
        "refugees": "1.2M refugees",
        "idps": "",
    }
    refs = evidence_to_references(ev, country="Türkiye")
    ids = [r["id"] for r in refs]
    assert "SIGHTLINE_SITREPS" in ids
    assert "SIGHTLINE_REFUGEES" in ids
    assert "SIGHTLINE_IDPS" not in ids  # empty source skipped
    assert "SIGHTLINE_HDX_OVERVIEW" not in ids
    assert all(r["source"] == "sightline_bridge" for r in refs)
    # Unavailable bridge -> no refs
    assert evidence_to_references({"source": "unavailable"}) == []


# ── API contract (ingest -> review -> publish) ───────────────────────────
@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_api_ingest_returns_review_draft(client):
    pdf = make_pdf_bytes(CALL_TEXT)
    r = client.post("/api/calls/ingest", data={
        "file": (io.BytesIO(pdf), "call.pdf"),
        "call_id": "api_test_call",
        "display_name": "API Test Call",
    }, content_type="multipart/form-data")
    assert r.status_code == 201
    body = r.json
    assert body["status"] == "review"          # never auto-publishes
    assert body["manifest_draft"]["donor_id"] == "api_test_call"
    assert body["manifest_draft"]["hard_eligibility_gates"].get("psea_policy_mandatory") is True


def test_api_publish_then_engine_scores(client, tmp_path, monkeypatch):
    monkeypatch.setattr("engine.call_ingest.DONORS_DIR", tmp_path)
    pdf = make_pdf_bytes(CALL_TEXT)
    r = client.post("/api/calls/ingest", data={
        "file": (io.BytesIO(pdf), "call.pdf"),
        "call_id": "api_pub_call",
        "display_name": "API Publish Call",
    }, content_type="multipart/form-data")
    draft_id = r.json["draft_id"]

    r2 = client.post(f"/api/calls/drafts/{draft_id}/publish")
    assert r2.status_code == 200
    assert r2.json["status"] == "published"

    # manifest written + engine sees it
    loader = YamlDonorRuleLoader(donors_dir=tmp_path)
    assert "api_pub_call" in loader.list_donors()


def test_api_reject_draft(client):
    pdf = make_pdf_bytes(CALL_TEXT)
    r = client.post("/api/calls/ingest", data={
        "file": (io.BytesIO(pdf), "call.pdf"),
        "call_id": "reject_me",
    }, content_type="multipart/form-data")
    draft_id = r.json["draft_id"]
    r2 = client.post(f"/api/calls/drafts/{draft_id}/reject")
    assert r2.status_code == 200
    assert r2.json["status"] == "rejected"
