"""
proposal/config.py — Configuration for Proposal Design Pipeline.
"""

import os
import secrets
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

# Load environment variables from parent or local .env
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(BASE_DIR / ".env")

PORT = int(os.getenv("PROPOSAL_PORT", "5002"))
HOST = os.getenv("PROPOSAL_HOST", "0.0.0.0")
DEBUG = os.getenv("PROPOSAL_DEBUG", "false").lower() in ("1", "true", "yes")

# Database Path
# Embedded mode: PROPOSAL_DB_PATH wins (set by docker-compose app env) so the
# Proposal DB stays separate from Sightline's reliefweb.db. Fallback chain:
# PROPOSAL_DB_PATH -> DB_PATH -> repo-local proposal.db.
DB_PATH = Path(
    os.getenv("PROPOSAL_DB_PATH")
    or os.getenv("DB_PATH")
    or str(BASE_DIR / "data" / "proposal.db")
)
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(BASE_DIR / "output")))
BUILTIN_DONORS_DIR = BASE_DIR / "donors"
# Embedded production mounts the Proposal checkout read-only. Human-approved
# donor manifests therefore live beside proposal.db in the writable data
# volume, while standalone development keeps the historical repo-local path.
DONORS_DIR = Path(
    os.getenv("PROPOSAL_DONORS_DIR")
    or (
        str(DB_PATH.parent / "proposal_donors")
        if os.getenv("PROPOSAL_DB_PATH")
        else str(BUILTIN_DONORS_DIR)
    )
)
USAGE_LEDGER_PATH = Path(
    os.getenv("PROPOSAL_USAGE_LEDGER_PATH")
    or (
        str(DB_PATH.parent / "proposal_usage.jsonl")
        if os.getenv("PROPOSAL_DB_PATH")
        else str(BASE_DIR / "ops" / "usage.jsonl")
    )
)
# NOTE: no mkdir at import time — the embedded deployment mounts the proposal
# repo read-only (:/app/proposal:ro); callers must create OUTPUT_DIR lazily
# (see typst_engine.compiler._ensure_output_dir).

# LLM Configuration (Compatible with OpenRouter / OpenAI)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "google/gemini-2.5-flash")
VERIFIER_MODEL = os.getenv("VERIFIER_MODEL", "google/gemini-2.5-pro")

# Secret key for Flask sessions.
# No hardcoded fallback: a public default would let anyone forge sessions.
# Production MUST set SECRET_KEY in the environment. If unset, a random
# per-process key is generated (sessions won't survive a restart, but the
# app stays safe rather than silently using a known key).
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)

# ── Auth Configuration ────────────────────────────────────────────────────────
# Shares Firebase project with Sightline (same domain, same user pool).
FIREBASE_SERVICE_ACCOUNT_PATH = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "")
DESKTOP_MODE = os.getenv("DESKTOP_MODE", "false").lower() in ("1", "true", "yes")
DEV_AUTH_BYPASS = os.getenv("DEV_AUTH_BYPASS", "false").lower() in ("1", "true", "yes")
SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
ADMIN_UIDS = os.getenv("ADMIN_UIDS", "")
SERVER_API_KEY = os.getenv("SERVER_API_KEY", "")

# ── Path prefix for reverse-proxy deployment ──────────────────────────────────
# When served under /proposal via Caddy, set PROPOSAL_BASE_PATH=/proposal
# Standalone (local dev): leave empty or omit.
PROPOSAL_BASE_PATH = os.getenv("PROPOSAL_BASE_PATH", "").rstrip("/")

# Sightline evidence bridge — path to Sightline checkout for ReliefWeb/HDX imports
SIGHTLINE_ROOT = os.getenv("SIGHTLINE_ROOT", "")
