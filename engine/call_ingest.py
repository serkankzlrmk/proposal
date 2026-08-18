"""
proposal/engine/call_ingest.py — Donor Call Ingestion & Agentic Rule Engine.

VISION (user brief, Aug 2026): the system reads a donor call-for-proposals PDF,
extracts the requirements/constraints the donor actually stated, surfaces a
human-reviewable summary + manifest draft, and — after the user approves or
edits it — publishes a root-level donors/<call_id>.yaml manifest that the
deterministic scoring engine consumes IMMEDIATELY (loader globs donors/*.yaml).

Human-in-the-loop contract:
  1. ingest   : PDF -> text -> LLM extraction -> {summary, requirements,
                manifest_draft, status: "review"}  (never auto-publishes)
  2. review   : user edits the draft (PUT) or rejects it
  3. publish  : validated DonorManifest written to donors/<call_id>.yaml
                -> engine picks it up on the next score() call

Resilience: no OPENROUTER_API_KEY -> deterministic regex extraction still
produces a usable draft (zero-crash, testable offline). LLM output is always
schema-validated against DonorManifest before it reaches the user.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import pymupdf as fitz  # pymupdf >= 1.24 exposes top-level module
except ImportError:
    import fitz  # legacy alias

from engine.models import DonorManifest
from engine.generator import _call_llm

logger = logging.getLogger(__name__)

DONORS_DIR = Path(__file__).resolve().parent.parent / "donors"

# ── PDF text extraction ───────────────────────────────────────────────────
def extract_pdf_text(pdf_bytes: bytes, max_chars: int = 120_000) -> str:
    """Extract text from a donor call PDF (first max_chars chars)."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages: List[str] = []
        for page in doc:
            txt = page.get_text() or ""
            pages.append(str(txt))
        text = "\n".join(pages)
        doc.close()
    except Exception as e:
        logger.warning("PDF text extraction failed: %s", e)
        return ""
    return (text or "")[:max_chars]


# ── LLM extraction (OpenRouter; graceful fallback to regex) ───────────────
_EXTRACTION_SCHEMA_HINT = """
Return ONLY a JSON object:
{
  "summary": "2-3 sentence donor call summary",
  "requirements": ["mandatory requirement 1", ...],
  "deadline": "2026-XX-XX or 'rolling'",
  "currency": "USD",
  "budget_max": 1500000,
  "max_duration_months": 12,
  "budget_cap_percent": 7.0,
  "min_source_ratio": 0.75,
  "mandatory_keywords": ["keyword", ...],
  "sections": {"mandatory": ["section_key", ...]},
  "hard_gates": {"gate_key": true_or_number}
}
Use canonical gate keys: sadd_disaggregation_mandatory,
cluster_coordination_mandatory, psea_policy_mandatory,
sphere_standards_mandatory, min_displaced_ratio, min_capacity_score.
"""


def extract_requirements(text: str) -> Dict[str, Any]:
    """Extract structured requirements from call text.

    LLM-first (OpenRouter); falls back to deterministic regex extraction so
    the pipeline never crashes and tests run offline.
    """
    prompt = (
        "You are a humanitarian grants compliance analyst. Read this donor "
        "call-for-proposals text and extract the hard requirements the donor "
        "states: mandatory sections/keywords, citation expectations, overhead "
        "cap, eligibility quotas (IDP ratio, PSEA, SADD, capacity score), "
        "deadline.\n\n" + _EXTRACTION_SCHEMA_HINT +
        "\n\nCALL TEXT (truncated):\n" + (text or "")[:50_000]
    )
    raw = _call_llm(prompt, temperature=0.1)
    if raw:
        try:
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            parsed = json.loads(clean)
            if isinstance(parsed, dict) and parsed.get("summary"):
                # Anti-hallucination: every claimed gate must have textual
                # evidence in the call; unverifiable gates are dropped.
                parsed["hard_gates"] = verify_gates_against_text(
                    parsed.get("hard_gates") or {}, text
                )
                return parsed
        except Exception as e:
            logger.warning("LLM extraction unparseable (%s); regex fallback", e)
    return _regex_extract(text)


def _regex_extract(text: str) -> Dict[str, Any]:
    """Deterministic fallback extraction (offline-safe, testable)."""
    low = (text or "").lower()
    keywords: List[str] = []
    for kw in ("psea", "sadd", "gbv", "sphere standards", "do no harm",
               "accountability to affected populations", "protection mainstreaming",
               "humanitarian principles", "cluster coordination", "localization"):
        if kw in low:
            keywords.append(kw)

    gates: Dict[str, Any] = {}
    if "psea" in low or "protection from sexual exploitation" in low:
        gates["psea_policy_mandatory"] = True
    if "sadd" in low or "sex, age and disability" in low:
        gates["sadd_disaggregation_mandatory"] = True
    if "cluster coordination" in low:
        gates["cluster_coordination_mandatory"] = True
    if "sphere" in low:
        gates["sphere_standards_mandatory"] = True

    m = re.search(r"(\d{1,3})\s*%\s*(?:of|idp|refugee|displaced)", low)
    if m:
        gates["min_displaced_ratio"] = float(m.group(1)) / 100.0

    m = re.search(r"(?:overhead|indirect)[^.\n]{0,40}?(\d{1,2}(?:\.\d)?)\s*%", low)
    budget_cap = float(m.group(1)) if m else 7.0

    m = re.search(r"(?:deadline|closing|submission)[^.\n]{0,30}?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", low)
    deadline = m.group(1) if m else "unknown"

    summary = (
        "Donor call extracted deterministically (no LLM): "
        + f"{len(keywords)} priority keywords, {len(gates)} hard gates detected."
        if text else "No extractable text (empty PDF?)."
    )
    return {
        "summary": summary,
        "requirements": [f"Keyword requirement: {k}" for k in keywords],
        "deadline": deadline,
        "budget_cap_percent": budget_cap,
        "min_source_ratio": 0.75,
        "mandatory_keywords": keywords,
        "sections": {"mandatory": ["project_summary", "humanitarian_situation", "needs_assessment"]},
        "hard_gates": gates,
    }


# ── Evidence-gated extraction (anti-hallucination layer) ───────────────────
# Every gate the LLM claims must be verifiable in the call text itself.
# A gate with no textual evidence is DROPPED — the deterministic layer
# catches LLM hallucination so the manifest only carries what the call
# actually requires (VISION: "the system must know the call's real asks").
GATE_EVIDENCE_PATTERNS = {
    "sadd_disaggregation_mandatory": re.compile(
        r"\b(sadd|sex,?\s*age|disability disaggregat|disaggregat\w*\s+by\s+(sex|age|disability))\b", re.I
    ),
    "cluster_coordination_mandatory": re.compile(r"\bcluster coordination\b", re.I),
    "psea_policy_mandatory": re.compile(
        r"\b(psea|protection from sexual exploitation|sexual exploitation and abuse)\b", re.I
    ),
    "sphere_standards_mandatory": re.compile(r"\bsphere\b", re.I),
    "min_displaced_ratio": re.compile(r"\b(idps?|refugees?|displaced|returnees?)\b", re.I),
    "min_capacity_score": re.compile(r"\b(capacity|administrative capacity|score)\b", re.I),
}


def verify_gates_against_text(gates: Dict[str, Any], text: str) -> Dict[str, Any]:
    """Keep only gates with textual evidence in the call (anti-hallucination).

    Numeric gates (min_displaced_ratio, min_capacity_score) are kept when
    their subject term appears; boolean gates require their exact evidence
    pattern. Gates with no evidence are dropped and logged.
    """
    low = (text or "").lower()
    verified: Dict[str, Any] = {}
    for key, value in gates.items():
        if value is None or value is False or value == "":
            continue  # LLM said "not required" — never include
        pattern = GATE_EVIDENCE_PATTERNS.get(key)
        if pattern is None:
            verified[key] = value  # unknown gate key: keep (defensive)
            continue
        if pattern.search(low):
            verified[key] = value
        else:
            logger.warning(
                "Gate %s dropped: no evidence in call text (LLM hallucination guard)", key
            )
    return verified


# ── Manifest draft building & publishing ──────────────────────────────────
def build_manifest_draft(call_id: str, display_name: str, extracted: Dict[str, Any]) -> Dict[str, Any]:
    """Map extracted requirements onto the canonical root-level manifest shape.

    Returns a plain dict; validated against DonorManifest on publish.
    Every field is coerced safely (None/empty -> default) so a partially
    populated LLM extraction can never crash the pipeline.
    """
    def _f(value, default: float) -> float:
        try:
            if value is None or value == "":
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _int(value) -> Optional[int]:
        try:
            if value is None or value == "":
                return None
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def _list(value, default=None) -> List[str]:
        if isinstance(value, list):
            return [str(v) for v in value if str(v).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return list(default or [])

    def _dict(value, default=None) -> Dict[str, Any]:
        return dict(value) if isinstance(value, dict) else dict(default or {})

    gates = _dict(extracted.get("hard_gates"))
    # Drop gates the extraction marked as None/empty/False (e.g. LLM emitted
    # "cluster_coordination_mandatory": false) — a false/None gate means the
    # call does NOT require it, so it must not appear in the manifest at all.
    gates = {k: v for k, v in gates.items() if v is not None and v is not False and v != ""}
    sections = _dict(extracted.get("sections"))
    sections.setdefault("mandatory", _list(extracted.get("mandatory_sections")))
    return {
        "donor_id": call_id,
        "display_name": display_name,
        "template_standard": "Custom Donor Call Manifest",
        "version": "1.0.0",
        "currency": str(extracted.get("currency") or "USD").upper(),
        "budget_max": _f(extracted.get("budget_max"), 0.0) or None,
        "max_duration_months": _int(extracted.get("max_duration_months")),
        "deadline": str(extracted.get("deadline") or "").strip() or None,
        "scoring_weights": {
            "section_coverage": 30, "source_citations": 25,
            "smart_criteria": 20, "donor_keywords": 15, "budget_alignment": 10,
        },
        "sections": sections,
        "min_source_ratio": _f(extracted.get("min_source_ratio"), 0.75),
        "overhead_cap_percent": _f(extracted.get("budget_cap_percent"), 7.0),
        "mandatory_keywords": _list(extracted.get("mandatory_keywords")),
        "smart_indicators": {"required_dimensions": ["specific", "measurable", "achievable", "relevant", "time_bound"]},
        "hard_eligibility_gates": gates,
        "pass_threshold": 70.0,
        "meta": {
            "source": "call_ingest",
            "deadline": str(extracted.get("deadline", "unknown")),
            "requirements": _list(extracted.get("requirements")),
            "created_at": time.time(),
        },
    }


def save_manifest(call_id: str, manifest_dict: Dict[str, Any]) -> Path:
    """Validate and write a manifest to donors/<call_id>.yaml.

    Raises ValueError on schema violation (never writes invalid YAML).
    """
    manifest = DonorManifest(**manifest_dict)  # raises pydantic ValidationError
    slug = re.sub(r"[^a-z0-9_]", "_", call_id.lower())
    path = DONORS_DIR / f"{slug}.yaml"
    path.write_text(_dump_yaml(manifest), encoding="utf-8")
    logger.info("Published donor manifest %s (%s)", slug, path)
    return path


def _dump_yaml(manifest: DonorManifest) -> str:
    """Serialize a validated manifest back to canonical YAML (root-level)."""
    import yaml

    data = {
        "donor_id": manifest.donor_id,
        "display_name": manifest.display_name,
        "template_standard": manifest.template_standard,
        "version": manifest.version,
        "currency": manifest.currency,
        "budget_max": manifest.budget_max,
        "max_duration_months": manifest.max_duration_months,
        "deadline": manifest.deadline,
        "scoring_weights": manifest.scoring_weights,
        "sections": manifest.sections,
        "max_char_limits": manifest.max_char_limits,
        "min_source_ratio": manifest.min_source_ratio,
        "overhead_cap_percent": manifest.overhead_cap_percent,
        "mandatory_keywords": manifest.mandatory_keywords,
        "smart_indicators": manifest.smart_indicators,
        "hard_eligibility_gates": manifest.hard_eligibility_gates,
        "pass_threshold": manifest.pass_threshold,
    }
    if getattr(manifest, "meta", None):
        data["meta"] = manifest.meta
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
