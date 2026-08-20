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


# ── Multi-format document extraction (pdf | docx | md) ────────────────────
SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".md", ".markdown", ".txt")


def extract_document_text(data: bytes, filename: str, max_chars: int = 120_000) -> str:
    """Extract text from a donor call document by extension.

    pdf  -> pymupdf
    docx -> zipfile + XML paragraph walk (no python-docx dependency)
    md/txt -> plain text
    Unsupported extensions return "" (caller decides).
    """
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return extract_pdf_text(data, max_chars=max_chars)
    if name.endswith(".docx"):
        return _extract_docx_text(data, max_chars=max_chars)
    if name.endswith((".md", ".markdown", ".txt")):
        try:
            return (data.decode("utf-8", errors="ignore") or "")[:max_chars]
        except Exception as e:
            logger.warning("Text extraction failed: %s", e)
            return ""
    logger.warning("Unsupported document type: %s", filename)
    return ""


def _extract_docx_text(data: bytes, max_chars: int = 120_000) -> str:
    """Extract paragraph text from a .docx (zip + XML, no python-docx)."""
    import re
    import zipfile

    try:
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
    except Exception as e:
        logger.warning("DOCX extraction failed: %s", e)
        return ""
    paras = re.findall(r"<w:p[^>]*>(.*?)</w:p>", xml, re.S)
    out = []
    for p in paras:
        texts = re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)
        out.append("".join(texts))
    return "\n".join(t for t in out if t.strip())[:max_chars]


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
        "You are a humanitarian grants compliance analyst. LANGUAGE POLICY: ALWAYS respond in English, " \
        "no matter the language of the call documents. Read this donor "
        "call-for-proposals text and extract the hard requirements the donor "
        "states: mandatory sections/keywords, citation expectations, overhead "
        "cap, eligibility quotas (IDP ratio, PSEA, SADD, capacity score), "
        "deadline.\n\n" + _EXTRACTION_SCHEMA_HINT +
        "\n\nCALL TEXT (truncated):\n" + (text or "")[:50_000]
    )
    raw = _call_llm(
        prompt,
        temperature=0.1,
        action="call_extract",
        timeout_seconds=20.0,
    )
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
    """Deterministic fallback extraction (offline-safe, evidence-based)."""
    low = (text or "").lower()
    keywords: List[str] = []
    for kw in (
        "psea", "sadd", "gbv", "sphere standards", "do no harm",
        "accountability to affected populations", "protection mainstreaming",
        "humanitarian principles", "cluster coordination", "localization",
        "child, early and forced marriages", "cefm", "gender equality",
        "rights-based", "multi-sectoral collaboration", "capacity-building",
    ):
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

    m = re.search(
        r"(?:overhead|indirect|support\s+cost)[^.\n]{0,40}?(?:max(?:imum)?\s*)?(\d{1,2}(?:\.\d)?)\s*%",
        low,
    )
    budget_cap = float(m.group(1)) if m else 7.0

    deadline = _extract_deadline(text)
    currency, budget_max = _extract_budget_ceiling(text)
    country = _extract_country(text)

    duration_match = re.search(
        r"(?:maximum|max(?:imum)?\.?|up\s+to)\s+(\d{1,3})\s+months?",
        low,
    )
    max_duration_months = int(duration_match.group(1)) if duration_match else None

    sections = _extract_application_sections(text)
    requirements = [f"Priority language: {keyword}" for keyword in keywords]
    if deadline != "unknown":
        requirements.append(f"Submission deadline: {deadline}")
    if budget_max is not None:
        requirements.append(f"Maximum budget: {currency} {budget_max:,.0f}")
    if max_duration_months is not None:
        requirements.append(f"Maximum programme duration: {max_duration_months} months")
    if re.search(r"applications?\s+must\s+be\s+submitted\s+in\s+english", low):
        requirements.append("Applications must be submitted in English")
    if re.search(r"(?:copy of provisions of )?legal status", low):
        requirements.append("Proof of the organisation's legal status is required")
    if "annual report" in low and "audit report" in low:
        requirements.append("Latest annual report and audit report are required")
    reference_match = re.search(r"provide\s+(\d+)\s+references?", low)
    if reference_match:
        requirements.append(f"Provide {reference_match.group(1)} references")
    if "list of indicators" in low:
        requirements.append("A completed list of indicators is required")
    if "proposed budget" in low or "budget template" in low:
        requirements.append("The donor budget template is required")

    title_match = re.search(
        r"(?im)^\s*((?:grant|funding|open)\s+call[^\n]{0,160})$",
        text or "",
    )
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else "Uploaded donor call"
    donor = next((name for name in ("UNFPA", "UNICEF", "OCHA", "ECHO", "UNHCR", "USAID") if name.lower() in low), "The donor")
    focus = ""
    if "child, early, and forced marriages" in low or "cefm" in low:
        focus = " It focuses on preventing child, early and forced marriages and strengthening civil society capacity."

    summary = (
        f"{title}. {donor} call requirements were extracted locally from the uploaded source documents.{focus} "
        f"Detected {len(requirements)} explicit requirements and {len(gates)} evidenced eligibility rules."
        if text else "No extractable text (empty PDF?)."
    )
    return {
        "summary": summary,
        "requirements": requirements,
        "deadline": deadline,
        "currency": currency,
        "budget_max": budget_max,
        "max_duration_months": max_duration_months,
        "country": country,
        "budget_cap_percent": budget_cap,
        "min_source_ratio": 0.75,
        "mandatory_keywords": keywords,
        "sections": {"mandatory": sections or ["project_summary", "needs_assessment", "monitoring_and_evaluation"]},
        "hard_gates": gates,
        "extraction_mode": "deterministic",
    }


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def _extract_country(text: str) -> str:
    """Best-effort country extraction for offline proposal initialization."""
    low = (text or "").lower()
    aliases = (
        (("türkiye", "turkiye", "turkey"), "Türkiye"),
        (("south sudan",), "South Sudan"),
        (("sudan",), "Sudan"),
        (("syrian arab republic", "syria"), "Syria"),
        (("ukraine",), "Ukraine"),
        (("afghanistan",), "Afghanistan"),
        (("somalia",), "Somalia"),
        (("ethiopia",), "Ethiopia"),
        (("yemen",), "Yemen"),
        (("lebanon",), "Lebanon"),
        (("jordan",), "Jordan"),
    )
    for names, canonical in aliases:
        if any(re.search(rf"\b{re.escape(name)}\b", low) for name in names):
            return canonical
    return ""


def _extract_deadline(text: str) -> str:
    """Return the submission deadline as ISO YYYY-MM-DD when evidenced."""
    source = text or ""
    candidates = []
    date_patterns = (
        re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b"),
        re.compile(
            r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})\b",
            re.I,
        ),
    )
    for pattern in date_patterns:
        for match in pattern.finditer(source):
            day = int(match.group(1))
            month = int(match.group(2)) if match.group(2).isdigit() else _MONTHS[match.group(2).lower()]
            year = int(match.group(3))
            year = year + 2000 if year < 100 else year
            context = source[max(0, match.start() - 180):match.end() + 80].lower()
            score = 0
            if "deadline for submissions" in context or "deadline for submission" in context:
                score += 10
            if "send their submission" in context or "applications must be submitted" in context:
                score += 9
            if "deadline" in context:
                score += 4
            if "submit" in context or "closing" in context:
                score += 3
            if "proposal" in context:
                score += 2
            if any(term in context for term in ("additional information", "clarification", "queries", "questions")):
                score -= 20
            if any(term in context for term in ("issue date", "review of", "notification of results")):
                score -= 8
            candidates.append((score, match.start(), f"{year:04d}-{month:02d}-{day:02d}"))
    if not candidates:
        return "unknown"
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2] if candidates[0][0] > 0 else "unknown"


def _extract_budget_ceiling(text: str) -> tuple[str, Optional[float]]:
    """Extract a donor budget ceiling without treating dates as money."""
    source = text or ""
    match = re.search(
        r"(?:maximum|max(?:imum)?\.?|up\s+to)\s*(?:budget\s*)?"
        r"(USD|EUR|GBP|TRY|TL)\s*([0-9][0-9.,\s]{2,})",
        source,
        re.I,
    )
    if not match:
        match = re.search(
            r"(?:budget|grant)[^\n]{0,50}?(USD|EUR|GBP|TRY|TL)\s*([0-9][0-9.,\s]{2,})",
            source,
            re.I,
        )
    if not match:
        return "USD", None
    currency = "TRY" if match.group(1).upper() == "TL" else match.group(1).upper()
    digits = re.sub(r"[^0-9]", "", match.group(2))
    return currency, float(digits) if digits else None


def _extract_application_sections(text: str) -> List[str]:
    """Collect proposal-template headings that become scoring sections."""
    source = text or ""
    headings = re.findall(r"(?im)^\s*([DEF]\.\d+\s+[^\n]{3,100}|Section\s+F\.\s+References)\s*$", source)
    sections: List[str] = []
    for heading in headings:
        label = re.sub(r"^(?:[DEF]\.\d+|Section\s+F\.)\s*", "", heading, flags=re.I)
        slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
        if slug and slug not in sections:
            sections.append(slug)
    return sections


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
            "country": str(extracted.get("country") or ""),
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
