"""
proposal/engine/evidence.py — Sightline Evidence Bridge (NO-CODE-MOVE integration).

VISION (user brief, Aug 2026): the proposal agent must be able to reach the
right evidence source (ReliefWeb SitReps, HDX refugee/IDP data) for citations
and grounding — WITHOUT copying Sightline code into this repo.

How it works:
  - At runtime, this module adds Sightline's `reliefweb_api/` package to
    sys.path and imports its functions directly (search_sitreps,
    hdx_get_country_overview, hdx_get_refugees, hdx_get_idps).
  - If Sightline is not present (SIGHTLINE_ROOT missing / import fails),
    every bridge call returns None — the proposal pipeline keeps working
    (zero-crash). Evidence is a bonus, never a hard dependency.

MIGRATION NOTE (leave for the future move):
  When Proposal Studio is migrated INTO Sightline as a module, delete this
  bridge and import `reliefweb_api` directly (it will already be in the same
  process). The bridge is the ONLY file that touches Sightline paths — the
  rest of the proposal code stays Sightline-free.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SIGHTLINE_ROOT = Path(
    __import__("os").getenv("SIGHTLINE_ROOT", "~/Documents/reliefweb/RedAgent")
).expanduser()

_loaded = False
_available = False


def _ensure_loaded() -> bool:
    """Add Sightline root to sys.path once; import via package path.

    Sightline's modules use relative imports (from . import ...), so the
    ROOT must be on sys.path and imports go through the `reliefweb_api`
    package — not the bare module name. Tools are LangChain StructuredTool
    objects: call them with .invoke({...}).

    HDX_APP_IDENTIFIER is loaded from Sightline's .env (the bridge does not
    copy the key into this repo — it reads it at runtime).
    """
    global _loaded, _available
    if _loaded:
        return _available
    _loaded = True
    if not SIGHTLINE_ROOT.exists():
        logger.info("Sightline root not found at %s — evidence bridge disabled", SIGHTLINE_ROOT)
        return False
    try:
        # Load Sightline's env (HDX_APP_IDENTIFIER etc.) without touching ours
        env_path = SIGHTLINE_ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    if k.strip() and not __import__("os").environ.get(k.strip()):
                        __import__("os").environ[k.strip()] = v.strip().strip('"').strip("'")
        if str(SIGHTLINE_ROOT) not in sys.path:
            sys.path.insert(0, str(SIGHTLINE_ROOT))
        # Import the tool modules through the package (relative imports work)
        import reliefweb_api.reliefweb  # noqa: F401
        import reliefweb_api.hdx_tools as hdx_tools  # noqa: F401
        # HDX client singleton needs explicit init (server.py does this at
        # startup; the bridge replicates it with the key from Sightline's env)
        hdx_key = __import__("os").environ.get("HDX_APP_IDENTIFIER", "")
        if hdx_key:
            hdx_tools.init_hdx_tools(app_identifier=hdx_key)
        _available = True
        logger.info("Sightline evidence bridge ACTIVE (%s)", SIGHTLINE_ROOT)
    except Exception as e:
        logger.warning("Sightline evidence bridge import failed: %s", e)
        _available = False
    return _available


def _invoke(tool, args: Dict[str, Any]) -> Optional[str]:
    """Call a LangChain StructuredTool safely; None on any failure."""
    try:
        result = tool.invoke(args)
        return str(result) if result else None
    except Exception as e:
        logger.warning("Sightline tool %s failed: %s", getattr(tool, "name", "?"), e)
        return None


def available() -> bool:
    return _ensure_loaded()


# ── ReliefWeb ─────────────────────────────────────────────────────────────
def search_sitreps(
    country: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 5,
    theme: Optional[str] = None,
) -> Optional[str]:
    """Search ReliefWeb SitReps via Sightline's tool. None when bridge off."""
    if not _ensure_loaded():
        return None
    try:
        import reliefweb_api.reliefweb as reliefweb

        return _invoke(reliefweb.search_sitreps, {
            "country": country, "query": query, "limit": limit, "theme": theme,
        })
    except Exception as e:
        logger.warning("ReliefWeb search failed: %s", e)
        return None


# ── HDX ───────────────────────────────────────────────────────────────────
def hdx_country_overview(country_code: str) -> Optional[str]:
    """HDX country overview (refugees, IDPs, funding, conflict)."""
    if not _ensure_loaded():
        return None
    try:
        import reliefweb_api.hdx_tools as hdx_tools

        return _invoke(hdx_tools.hdx_get_country_overview, {"country_code": country_code})
    except Exception as e:
        logger.warning("HDX overview failed: %s", e)
        return None


def hdx_refugees(country_code: str, limit: int = 5) -> Optional[str]:
    if not _ensure_loaded():
        return None
    try:
        import reliefweb_api.hdx_tools as hdx_tools

        return _invoke(hdx_tools.hdx_get_refugees, {"country_code": country_code, "limit": limit})
    except Exception as e:
        logger.warning("HDX refugees failed: %s", e)
        return None


def hdx_idps(country_code: str, limit: int = 5) -> Optional[str]:
    if not _ensure_loaded():
        return None
    try:
        import reliefweb_api.hdx_tools as hdx_tools

        return _invoke(hdx_tools.hdx_get_idps, {"country_code": country_code, "limit": limit})
    except Exception as e:
        logger.warning("HDX IDPs failed: %s", e)
        return None


# ── Evidence bundle for prompt injection ──────────────────────────────────
def collect_evidence(country: str, theme: str = "", country_code: Optional[str] = None) -> Dict[str, Any]:
    """Gather evidence snippets for a proposal context (best-effort).

    Returns {"sitreps": str|None, "hdx_overview": str|None, "refugees": str|None,
             "idps": str|None, "source": "sightline_bridge"|"unavailable"}.
    Never raises; every source is optional.
    """
    if not _ensure_loaded():
        return {"source": "unavailable"}

    evidence: Dict[str, Any] = {"source": "sightline_bridge"}
    evidence["sitreps"] = search_sitreps(country=country, query=theme or None, limit=5)
    if country_code:
        evidence["hdx_overview"] = hdx_country_overview(country_code)
        evidence["refugees"] = hdx_refugees(country_code)
        evidence["idps"] = hdx_idps(country_code)
    return evidence


def evidence_to_prompt(evidence: Dict[str, Any], max_chars: int = 4000) -> str:
    """Render collected evidence into a compact prompt block for the LLM.

    Only non-empty sources are included; the LLM is told to cite them with
    [ref: SIGHTLINE_<SOURCE>] so the citation registry can ground them later.
    """
    if not evidence or evidence.get("source") != "sightline_bridge":
        return ""
    blocks = []
    for key, label in (
        ("sitreps", "ReliefWeb SitReps"),
        ("hdx_overview", "HDX Country Overview"),
        ("refugees", "HDX Refugee Data"),
        ("idps", "HDX IDP Data"),
    ):
        val = evidence.get(key)
        if val and str(val).strip():
            blocks.append(f"--- {label} ---\n{str(val)[:max_chars]}")
    if not blocks:
        return ""
    return (
        "EVIDENCE FROM LIVE SOURCES (cite with [ref: SIGHTLINE_<SOURCE>] when you use them):\n"
        + "\n\n".join(blocks)
    )
