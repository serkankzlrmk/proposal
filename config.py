"""
proposal/config.py — Configuration for Proposal Design Pipeline.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

# Load environment variables from parent or local .env
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(BASE_DIR / ".env")

PORT = int(os.getenv("PROPOSAL_PORT", "5002"))
HOST = os.getenv("PROPOSAL_HOST", "0.0.0.0")
DEBUG = os.getenv("PROPOSAL_DEBUG", "true").lower() in ("1", "true", "yes")

# Database Path
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "proposal.db")))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(BASE_DIR / "output")))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# LLM Configuration (Compatible with OpenRouter / OpenAI)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "google/gemini-2.5-flash")
VERIFIER_MODEL = os.getenv("VERIFIER_MODEL", "google/gemini-2.5-pro")

# Secret key for Flask sessions
SECRET_KEY = os.getenv("SECRET_KEY", "proposal-design-secret-key-2026")

# ── Path prefix for reverse-proxy deployment ──────────────────────────────────
# When served under /proposal via Caddy, set PROPOSAL_BASE_PATH=/proposal
# Standalone (local dev): leave empty or omit.
PROPOSAL_BASE_PATH = os.getenv("PROPOSAL_BASE_PATH", "").rstrip("/")

# Sightline evidence bridge — path to Sightline checkout for ReliefWeb/HDX imports
SIGHTLINE_ROOT = os.getenv("SIGHTLINE_ROOT", "")
