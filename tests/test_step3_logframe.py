"""
proposal/tests/test_step3_logframe.py — Step 3 Backend Tests (Master Spec §3).

Covers:
  - structured logframe generation (canonical shape + matrix projection)
  - free-text -> structured SMART parsing (ARCHITECTURAL_DECISIONS #3)
  - deterministic SMART validator
  - /api/proposal-v2/steps/3/analyze + /lock + /generate endpoints
  - FSM lock guard: locked step content writes -> 409 Conflict
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import pytest

try:
    from app import app
    from db import create_proposal, get_proposal, update_proposal, lock_step, ProposalLockedError
    from engine.smart_parser import parse_indicator_text, validate_indicators, smart_validation_result
    from engine.generator import project_logframe_to_matrix
    from engine.models import iter_indicator_entries
except ImportError:
    from proposal.app import app
    from proposal.db import create_proposal, get_proposal, update_proposal, lock_step, ProposalLockedError
    from proposal.engine.smart_parser import parse_indicator_text, validate_indicators, smart_validation_result
    from proposal.engine.generator import project_logframe_to_matrix
    from proposal.engine.models import iter_indicator_entries


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def make_prop():
    return create_proposal(
        user_id="default_user",
        title="Step 3 Test Proposal",
        country="Sudan",
        donor="OCHA_CBPF",
        theme="WASH",
    )


# ── Structured logframe generation & projection ──────────────────────────
def test_project_logframe_to_matrix():
    structured = {
        "goal": "Impact goal narrative",
        "goal_indicators": [{
            "indicator_id": "g1",
            "narrative": "CMR below 0.5 per 10,000 per day",
            "target_value": 0.5,
            "unit": "CMR /10,000/day",
            "baseline": 0.0,
            "timeframe": "by end of project",
            "means_of_verification": "SMART surveys",
            "assumptions": "Access remains open",
            "disaggregated_by": ["gender", "age"],
        }],
        "outcomes": [{
            "outcome_id": "oc1",
            "narrative": "Outcome narrative",
            "indicators": [{
                "indicator_id": "oc1_i1",
                "narrative": ">= 85% households access 15L/person/day",
                "target_value": 85.0,
                "unit": "percent",
                "baseline": 0.0,
                "timeframe": "by month 12",
                "means_of_verification": "PDM reports",
                "assumptions": "Security allows access",
                "disaggregated_by": ["gender", "age", "disability"],
            }],
            "outputs": [{
                "output_id": "op1",
                "narrative": "Output narrative",
                "indicators": [{
                    "indicator_id": "op1_i1",
                    "narrative": "12 facilities operationalized",
                    "target_value": 12.0,
                    "unit": "facilities",
                    "baseline": 0.0,
                    "timeframe": "by month 9",
                    "means_of_verification": "Handover certificates",
                    "assumptions": "Procurement clears",
                    "disaggregated_by": ["gender", "age"],
                }],
                "activities": ["Act 1.1.1", "Act 1.1.2"],
            }],
        }],
    }
    out = project_logframe_to_matrix(structured)
    # Canonical fields preserved
    assert out["goal"] == "Impact goal narrative"
    assert out["outcomes"][0]["outcome_id"] == "oc1"
    # Matrix projection for UI/PDF backward compatibility
    rows = out["matrix"]
    assert len(rows) == 3  # goal + outcome + output
    assert rows[0]["level"] == "Impact / Overall Goal"
    assert "85%" in rows[1]["indicators"]
    assert rows[2]["indicators"] == "12 facilities operationalized"


def test_iter_indicator_entries_structured_and_flat():
    structured = project_logframe_to_matrix({
        "goal": "G",
        "goal_indicators": [{"narrative": "A"}],
        "outcomes": [{
            "outcome_id": "oc1", "narrative": "B",
            "indicators": [{"narrative": "C"}],
            "outputs": [{"output_id": "op1", "narrative": "D", "indicators": [{"narrative": "E"}]}],
        }],
    })
    # Structured shape has both matrix projection AND structured fields; the
    # helper prefers matrix when present (same text, single source of truth)
    entries = iter_indicator_entries(structured)
    assert len(entries) == 3
    assert entries[0]["indicators"] == "A"

    # Pure structured shape (no matrix)
    pure = {"goal": "G", "goal_indicators": [{"narrative": "A"}], "outcomes": []}
    assert [e["indicators"] for e in iter_indicator_entries(pure)] == ["G", "A"]


# ── Smart parser (ARCHITECTURAL_DECISIONS #3) ────────────────────────────
def test_parse_indicator_text_structured_fields():
    ind = parse_indicator_text(">= 85% households access 15L/person/day by month 12")
    assert ind.target_value == 85.0
    assert ind.unit == "households"
    assert "month 12" in ind.timeframe
    assert ind.narrative.startswith(">= 85%")


def test_parse_indicator_text_timeframe_2026():
    ind = parse_indicator_text("40% of girls aged 6-12 enrolled by Q2 2026")
    assert ind.target_value == 40.0
    assert "Q2 2026" in ind.timeframe


def test_parse_indicator_text_fallback():
    ind = parse_indicator_text("improve access to clean water")
    assert ind.target_value == 0.0
    assert ind.timeframe == "by end of project"
    assert ind.unit == "individuals"


def test_validate_indicators_smart_scores():
    res = smart_validation_result(">= 85% households access 15L/person/day by month 12")
    assert "measurable" in res["passed"]
    assert "time_bound" in res["passed"]

    res2 = smart_validation_result("improve access to clean water")
    assert "measurable" in res2["failed"]

    batch = validate_indicators([
        ">= 85% households access 15L/person/day by month 12, disaggregated by gender and age",
        "improve access to clean water",
    ])
    assert batch["total_indicators"] == 2
    assert batch["passed_indicators"] == 1
    assert batch["dimensions"]["measurable"]["passed"] == 1


# ── API endpoints ────────────────────────────────────────────────────────
def test_step3_analyze_endpoint(client):
    prop = make_prop()
    prop_id = prop["id"]
    r = client.post("/api/proposal-v2/steps/3/analyze", json={"proposal_id": prop_id})
    assert r.status_code == 200
    body = r.json
    assert "smart_report" in body
    assert "smart_score" in body
    assert body["max_score"] == 20
    # Empty logframe -> no indicator entries, SMART score floor 0
    assert body["smart_report"]["total_indicators"] == 0
    assert body["smart_score"] == 0.0


def test_step3_lock_endpoint_guards_writes(client):
    prop = make_prop()
    prop_id = prop["id"]

    # Populate logframe (unlocked OK)
    lf = project_logframe_to_matrix({
        "goal": "Goal",
        "goal_indicators": [],
        "outcomes": [],
    })
    update_proposal(prop_id, {"logframe_data": lf})

    # Lock step 3
    r = client.post("/api/proposal-v2/steps/3/lock", json={"proposal_id": prop_id})
    assert r.status_code == 200
    assert r.json["status"] == "locked"
    assert 3 in r.json["locked_steps"]

    # Attempt to CHANGE locked logframe -> 409 Conflict
    changed = {"logframe_data": project_logframe_to_matrix({"goal": "DIFFERENT GOAL", "goal_indicators": [], "outcomes": []})}
    r = client.put(f"/api/proposals/{prop_id}", json=changed)
    assert r.status_code == 409
    assert r.json["code"] == "STEP_LOCKED"

    # Identical-value write -> allowed (no-op)
    r = client.put(f"/api/proposals/{prop_id}", json={"logframe_data": lf})
    assert r.status_code == 200


def test_step3_lock_db_layer_raises():
    prop = make_prop()
    prop_id = prop["id"]
    lock_step(prop_id, 3)
    with pytest.raises(ProposalLockedError):
        update_proposal(prop_id, {"logframe_data": {"goal": "HACK", "goal_indicators": [], "outcomes": []}})
    # Unlocked fields still writable
    upd = update_proposal(prop_id, {"title": "Still Editable"})
    assert upd is not None
    assert upd["title"] == "Still Editable"


def test_step3_generate_endpoint(client):
    prop = make_prop()
    prop_id = prop["id"]
    r = client.post("/api/proposal-v2/steps/3/generate", json={"proposal_id": prop_id})
    assert r.status_code == 200
    lf = r.json["logframe_data"]
    # Structured canonical fields present + matrix projection for UI/PDF
    assert "goal" in lf
    assert "outcomes" in lf
    assert "matrix" in lf
    assert len(lf["matrix"]) >= 1
