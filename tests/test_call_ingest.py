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


def test_regex_extracts_real_call_constraints_without_llm(monkeypatch):
    import engine.call_ingest as ci

    monkeypatch.setattr(ci, "_call_llm", lambda *a, **k: "")
    text = """
Grant Call for the Prevention of Child, Early, and Forced Marriages (CEFM)
UNFPA Türkiye invites civil society organizations to submit proposals.
Applications must be submitted in English by 2 May 2025.
Copy of provisions of legal status is required for review.
Attachment II - Proposed Budget (maximum TRY 1,000,000)
C.3 Proposed programme duration (for maximum 12 months)
D.1 Programme Summary
D.2 Organizational background and capacity to implement
E.1 Risks
E.2 Monitoring
Section F. References
Please provide 3 references to support your proposal.
SUPPORT COST (MAX 5%)
"""

    extracted = ci.extract_requirements(text)

    assert extracted["deadline"] == "2025-05-02"
    assert extracted["currency"] == "TRY"
    assert extracted["budget_max"] == pytest.approx(1_000_000)
    assert extracted["max_duration_months"] == 12
    assert extracted["budget_cap_percent"] == pytest.approx(5.0)
    assert extracted["country"] == "Türkiye"
    assert "Applications must be submitted in English" in extracted["requirements"]
    assert "programme_summary" in extracted["sections"]["mandatory"]
    assert "references" in extracted["sections"]["mandatory"]
    assert extracted["extraction_mode"] == "deterministic"


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


def test_evidence_bridge_disabled_when_root_unset(monkeypatch):
    """Unset SIGHTLINE_ROOT must disable the bridge (no accidental cwd use)."""
    import engine.evidence as ev

    monkeypatch.setattr(ev, "SIGHTLINE_ROOT", Path(""))
    monkeypatch.setattr(ev, "_loaded", False)
    monkeypatch.setattr(ev, "_available", False)
    assert ev.available() is False
    assert ev.search_sitreps(country="Turkey") is None


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


# ── Advisor small-talk (deterministic, no LLM) ────────────────────────────
def test_advisor_small_talk_greeting():
    """Greeting gets a warm, status-aware reply without an LLM call."""
    from engine.advisor import _small_talk_reply

    reply = _small_talk_reply("hello", "WASH Project", "OCHA_CBPF", "Sudan", 3)
    assert reply is not None
    assert "WASH Project" in reply
    assert "Step 3" in reply
    assert "OCHA_CBPF" in reply
    # Language policy: English even for Turkish greetings
    tr_reply = _small_talk_reply("merhaba", "X", "Y", "Z", 1)
    assert tr_reply is not None
    assert "Hello" in tr_reply
    assert "Merhaba" not in tr_reply


def test_advisor_small_talk_thanks():
    from engine.advisor import _small_talk_reply

    reply = _small_talk_reply("teşekkürler", "X", "Y", "Z", 1)
    assert reply is not None
    assert "welcome" in reply.lower()


def test_advisor_small_talk_ignores_real_questions():
    """Real questions must NOT be swallowed by the small-talk path."""
    from engine.advisor import _small_talk_reply

    assert _small_talk_reply("Are my WASH indicators SMART?", "X", "Y", "Z", 1) is None
    assert _small_talk_reply("fix the budget", "X", "Y", "Z", 1) is None


def test_advisor_status_summary():
    from engine.advisor import _proposal_status_summary

    prop = {
        "title": "T", "donor": "unfpa_turkiye_cefm", "country": "Türkiye",
        "step": 4, "logframe_data": {"matrix": [{"a": 1}]},
        "narrative_data": {"s1": "x", "s2": "y"}, "references": [{"id": "R1"}],
    }
    s = _proposal_status_summary(prop)
    assert "step=4/5" in s
    assert "logframe_rows=1" in s
    assert "narrative_sections=2" in s
    assert "references=1" in s


# ── API contract (ingest -> review -> publish) ───────────────────────────
@pytest.fixture
def client(tmp_path, monkeypatch):
    """Keep API fixtures out of the user's local proposal database."""
    import db
    import blueprints.call_ingest_api as call_api

    test_db = tmp_path / "proposal-test.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    monkeypatch.setattr(call_api, "DB_PATH", test_db)
    db.init_db()
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


def test_api_ingest_multiple_formats(client):
    """docx + md + pdf all accepted in one multi-file upload."""
    from engine.call_ingest import extract_document_text

    # DOCX bytes (minimal valid docx: zip with word/document.xml)
    import zipfile

    docx_buf = io.BytesIO()
    with zipfile.ZipFile(docx_buf, "w") as z:
        z.writestr("word/document.xml",
                   '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   '<w:body><w:p><w:r><w:t>DOCX requirement: PSEA policy mandatory</w:t></w:r></w:p></w:body></w:document>')
    docx_bytes = docx_buf.getvalue()

    md_bytes = b"# Call Guidelines\n\nDeadline: 15 June 2026\n\nSADD disaggregation required."

    r = client.post("/api/calls/ingest", data={
        "files": [
            (io.BytesIO(docx_bytes), "guidelines.docx"),
            (io.BytesIO(md_bytes), "annex.md"),
        ],
        "call_id": "multi_format_call",
        "display_name": "Multi Format Call",
    }, content_type="multipart/form-data")
    assert r.status_code == 201, r.json
    body = r.json
    assert body["documents_accepted"] == 2
    assert body["manifest_draft"]["donor_id"] == "multi_format_call"
    # Both formats' content reached the extractor
    assert "PSEA" in body["summary"] or "psea" in str(body["requirements"]).lower() or "SADD" in body["summary"]


def test_extract_document_text_formats():
    """extract_document_text handles pdf/docx/md; rejects others."""
    from engine.call_ingest import extract_document_text

    # md
    assert "hello" in extract_document_text(b"hello world", "notes.md")
    # docx
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   '<w:body><w:p><w:r><w:t>DOCX text here</w:t></w:r></w:p></w:body></w:document>')
    assert "DOCX text here" in extract_document_text(buf.getvalue(), "form.docx")
    # unsupported
    assert extract_document_text(b"x", "notes.xlsx") == ""


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


def test_api_agent_names_call_when_identity_is_empty(client, monkeypatch):
    import blueprints.call_ingest_api as call_api

    monkeypatch.setattr(call_api, "_generate_call_identity", lambda corpus, extracted: {
        "display_name": "ECHO Emergency Response 2026",
        "call_id": "echo_emergency_response_2026",
    })
    pdf = make_pdf_bytes(CALL_TEXT)
    response = client.post("/api/calls/ingest", data={
        "file": (io.BytesIO(pdf), "call.pdf"),
        "call_id": "",
        "display_name": "",
    }, content_type="multipart/form-data")

    assert response.status_code == 201
    assert response.json["call_id"] == "echo_emergency_response_2026"
    assert response.json["manifest_draft"]["display_name"] == "ECHO Emergency Response 2026"


def test_fallback_extraction_does_not_retry_llm_for_identity_or_brief(monkeypatch):
    import blueprints.call_ingest_api as call_api
    import engine.generator as generator

    calls = []
    monkeypatch.setattr(generator, "_call_llm", lambda *args, **kwargs: calls.append(kwargs.get("action")) or "")
    extracted = {
        "summary": "Donor call extracted deterministically (no LLM): 2 priority keywords, 1 hard gates detected.",
        "requirements": ["PSEA is mandatory"],
        "extraction_mode": "deterministic",
    }
    draft = build_manifest_draft("fallback_call", "Fallback Call", extracted)

    identity = call_api._generate_call_identity("# Fallback Call\nPSEA is mandatory", extracted)
    brief = call_api._generate_brief("# Fallback Call", extracted, draft)

    assert identity["display_name"] == "# Fallback Call"
    assert "What this call is about" in brief
    assert calls == []


def test_api_delete_unused_call(client):
    pdf = make_pdf_bytes(CALL_TEXT)
    created = client.post("/api/calls/ingest", data={
        "file": (io.BytesIO(pdf), "delete.pdf"),
        "call_id": "delete_unused_call",
        "display_name": "Delete Unused Call",
    }, content_type="multipart/form-data")
    draft_id = created.json["draft_id"]

    deleted = client.delete(f"/api/calls/drafts/{draft_id}")
    assert deleted.status_code == 200
    assert deleted.json["status"] == "deleted"
    assert client.get(f"/api/calls/drafts/{draft_id}").status_code == 404


def test_api_delete_protects_call_used_by_proposal(client):
    from db import create_proposal

    pdf = make_pdf_bytes(CALL_TEXT)
    created = client.post("/api/calls/ingest", data={
        "file": (io.BytesIO(pdf), "protected.pdf"),
        "call_id": "protected_call",
        "display_name": "Protected Call",
    }, content_type="multipart/form-data")
    create_proposal(donor="protected_call", title="Uses protected call")

    blocked = client.delete(f"/api/calls/drafts/{created.json['draft_id']}")
    assert blocked.status_code == 409
    assert blocked.json["code"] == "CALL_IN_USE"
