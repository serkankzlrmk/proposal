"""
proposal/app.py — Standalone Flask Application for Proposal Design Pipeline.
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import logging
from flask import Flask, render_template
from flask_cors import CORS

try:
    from config import HOST, PORT, DEBUG, SECRET_KEY, BASE_DIR
    from blueprints.proposal_api import proposal_api_bp
    from blueprints.step3_logframe import step3_api_bp
    from blueprints.step4_budget_risk import step4_api_bp
    from db import init_db
except ImportError:
    from proposal.config import HOST, PORT, DEBUG, SECRET_KEY, BASE_DIR
    from proposal.blueprints.proposal_api import proposal_api_bp
    from proposal.blueprints.step3_logframe import step3_api_bp
    from proposal.blueprints.step4_budget_risk import step4_api_bp
    from proposal.db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("proposal_app")

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
    static_url_path="/static",
)
app.secret_key = SECRET_KEY
CORS(app)

# Register Proposal API Blueprint
app.register_blueprint(proposal_api_bp)
app.register_blueprint(step3_api_bp)
app.register_blueprint(step4_api_bp)


@app.route("/")
def index():
    """Serve the main single-page workspace UI."""
    return render_template("index.html")


@app.route("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "proposal_pipeline", "version": "1.0.0"}


if __name__ == "__main__":
    init_db()
    logger.info("Starting Proposal Design Pipeline on http://%s:%d", HOST, PORT)
    app.run(host=HOST, port=PORT, debug=DEBUG)
