"""
proposal/app.py — Standalone Flask Application for Proposal Design Pipeline.

Supports two deployment modes:
  1. Standalone:  python app.py  → http://localhost:5002/
  2. Reverse-proxy (Caddy /proposal):  set PROPOSAL_BASE_PATH=/proposal
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import logging
from flask import Flask, render_template, redirect, url_for
from flask_cors import CORS

try:
    from config import HOST, PORT, DEBUG, SECRET_KEY, BASE_DIR, PROPOSAL_BASE_PATH
    from blueprints.proposal_api import proposal_api_bp
    from blueprints.step3_logframe import step3_api_bp
    from blueprints.step4_budget_risk import step4_api_bp
    from blueprints.call_ingest_api import call_ingest_bp
    from db import init_db
except ImportError:
    from proposal.config import HOST, PORT, DEBUG, SECRET_KEY, BASE_DIR, PROPOSAL_BASE_PATH
    from proposal.blueprints.proposal_api import proposal_api_bp
    from proposal.blueprints.step3_logframe import step3_api_bp
    from proposal.blueprints.step4_budget_risk import step4_api_bp
    from proposal.blueprints.call_ingest_api import call_ingest_bp
    from proposal.db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("proposal_app")

# ── Static URL path: under reverse-proxy, serve static from /proposal/static ──
# Standalone → /static   |   Reverse-proxy → /proposal/static
_static_url = f"{PROPOSAL_BASE_PATH}/static" if PROPOSAL_BASE_PATH else "/static"

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
    static_url_path=_static_url,
)
app.secret_key = SECRET_KEY
CORS(app)

# ── Register blueprints ──────────────────────────────────────────────────────
# Blueprint url_prefixes stay the same (/api/proposals, /api/calls, etc.)
# Caddy routes /api/proposals* and /api/calls* to this container.
app.register_blueprint(proposal_api_bp)
app.register_blueprint(step3_api_bp)
app.register_blueprint(step4_api_bp)
app.register_blueprint(call_ingest_bp)


@app.route("/")
def index():
    """Serve the main single-page workspace UI."""
    return render_template("index.html", config={"PROPOSAL_BASE_PATH": PROPOSAL_BASE_PATH})


@app.route("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "proposal_pipeline", "version": "1.0.0"}


# ── Reverse-proxy entry point (e.g. /proposal → /) ──────────────────────────
if PROPOSAL_BASE_PATH:
    @app.route(PROPOSAL_BASE_PATH)
    @app.route(f"{PROPOSAL_BASE_PATH}/")
    def proxy_index():
        """Serve Proposal Studio under the base-path prefix."""
        return render_template("index.html", config={"PROPOSAL_BASE_PATH": PROPOSAL_BASE_PATH})

    @app.route(f"{PROPOSAL_BASE_PATH}/health")
    def proxy_health():
        """Health check under the base-path prefix."""
        return {"status": "ok", "service": "proposal_pipeline", "version": "1.0.0"}


if __name__ == "__main__":
    init_db()
    logger.info(
        "Starting Proposal Design Pipeline on http://%s:%d (base_path=%r)",
        HOST, PORT, PROPOSAL_BASE_PATH,
    )
    # use_reloader=False: the Sightline evidence bridge adds Sightline's root
    # to sys.path — the debug reloader watches it and restarts the server on
    # unrelated Sightline file changes, dropping in-flight requests.
    app.run(host=HOST, port=PORT, debug=DEBUG, use_reloader=False)