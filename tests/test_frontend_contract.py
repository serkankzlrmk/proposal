"""Frontend integration contracts for the standalone Proposal Studio shell."""

from pathlib import Path

import pytest

try:
    from app import app
except ImportError:
    from proposal.app import app


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def test_premium_shell_and_modular_assets_are_served(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    for asset in (
        "/static/css/design/tokens.css",
        "/static/css/design/shell.css",
        "/static/css/design/components.css",
        "/static/css/design/workspace.css",
        "/static/css/design/donor-intelligence.css",
        "/static/css/design/responsive.css",
    ):
        assert asset in html

    assert '<script type="module" src="/static/js/app.js"></script>' in html
    assert "Grant intelligence workspace" in html


def test_existing_dom_contracts_are_preserved(client):
    html = client.get("/").get_data(as_text=True)
    required_ids = (
        "btnSightlineHome",
        "btnProposalHome",
        "workspaceProposalTitle",
        "autosaveIndicator",
        "workspace",
        "inputTitle",
        "selectDonor",
        "tocVisualizer",
        "logframeBody",
        "step4SubTabs",
        "btnRunVerifier",
        "btnExportPdf",
        "advisorPopup",
        "newProposalModal",
        "donorCallSection",
        "btnDonorLibrary",
        "callDraftsList",
        "callIngestResult",
    )
    for element_id in required_ids:
        assert f'id="{element_id}"' in html


def test_frontend_stage_count_and_modules_stay_consistent():
    app_js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "Step ${step}/6" not in app_js
    assert "./modules/core.js" in app_js
    assert "./modules/workspace-state.js" in app_js
    assert "./modules/donor-intelligence.js" in app_js
    assert "ready-donor" not in app_js
    assert "loadPublishedCalls" not in app_js
    assert "proposalSelect" not in app_js
    assert (ROOT / "static" / "js" / "modules" / "core.js").is_file()
    assert (ROOT / "static" / "js" / "modules" / "workspace-state.js").is_file()
    assert (ROOT / "static" / "js" / "modules" / "donor-intelligence.js").is_file()


def test_long_running_actions_have_visible_activity_feedback():
    app_js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    core_js = (ROOT / "static" / "js" / "modules" / "core.js").read_text(encoding="utf-8")
    components_css = (ROOT / "static" / "css" / "design" / "components.css").read_text(encoding="utf-8")

    assert "beginActivity" in core_js
    assert "setButtonBusy" in core_js
    assert app_js.count("beginActivity({") >= 10
    assert ".activity-region" in components_css
    assert "bottom: 18px; left: 18px" in components_css
    assert ".activity-progress" in components_css


def test_donor_preview_guards_against_horizontal_overflow():
    css = (ROOT / "static" / "css" / "design" / "donor-intelligence.css").read_text(encoding="utf-8")
    assert ".call-library-panel { min-width: 0; max-width: 100%; overflow: hidden;" in css
    assert ".call-library-item h3 { min-width: 0;" in css
    assert ".document-item span { min-width: 0; overflow-wrap: anywhere;" in css
