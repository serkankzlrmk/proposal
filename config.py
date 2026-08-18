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
HOST = os.getenv("PROPOSAL_HOST", "127.0.0.1")
DEBUG = os.getenv("PROPOSAL_DEBUG", "true").lower() in ("1", "true", "yes")

# Database Path
DB_PATH = BASE_DIR / "data" / "proposal.db"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# LLM Configuration (Compatible with OpenRouter / OpenAI)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "google/gemini-2.5-flash")
VERIFIER_MODEL = os.getenv("VERIFIER_MODEL", "google/gemini-2.5-pro")

# Secret key for Flask sessions
SECRET_KEY = os.getenv("SECRET_KEY", "proposal-design-secret-key-2026")
