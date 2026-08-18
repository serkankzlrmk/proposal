"""
proposal/engine/smart_parser.py — Free-Text SMART Indicator Parser & Validator.

ARCHITECTURAL_DECISIONS #3 (CANONICAL: STRUCTURED PYDANTIC + REGEX PARSER):
  The structured LogframeIndicator model is canonical. When unstructured
  string indicators are posted, this regex engine parses target_value, unit,
  baseline, and timeframe automatically — the "smart adapter" layer.

Also hosts the deterministic SMART dimension validator shared by the
scoring engine and the Step 3 analyze endpoint:

    specific | measurable | achievable | relevant | time_bound
    (+ disaggregation for the advisor diagnostic layer)

Presence-of-format ONLY — no success judgment (semantic checks belong to
the Blind Verifier). Regex must never penalize valid text.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from engine.models import LogframeIndicator

# ── Shared SMART patterns (canonical; mirrors engine/yaml_rules.SMART_PATTERNS) ──
PATTERN_MEASURABLE = re.compile(
    r"\d+(\.\d+)?\s*%|\d+\s*(L|kg|m|people|households|children|women|men|girls|boys|schools|facilities|units|persons|individuals)\b",
    re.I,
)
PATTERN_NUMBER = re.compile(r"\d+(\.\d+)?")
PATTERN_UNIT = re.compile(
    r"\b(L|kg|m|people|households|children|women|men|girls|boys|schools|facilities|units|persons|individuals|liters|kg)\b",
    re.I,
)
PATTERN_TIMEFRAME = re.compile(
    r"\b(by|before|within|until|end of)\b|\b(q[1-4]|month|months|week|weeks|year|years|20\d\d)\b",
    re.I,
)
PATTERN_DISAGG = re.compile(
    r"\b(gender|sex|age|sadd|disaggregated|disaggregation|female|male)\b",
    re.I,
)
PATTERN_SPECIFIC = re.compile(r".{15,}", re.S)
PATTERN_ACHIEVABLE = re.compile(r"\d{1,3}\s*%|\d+\s*of\s*\d+|\b(target|expected|baseline)\b", re.I)
PATTERN_RELEVANT = re.compile(
    r"\b(reduce|increase|improve|access|ensure|strengthen|maintain|restore)\b", re.I
)

DIMENSION_PATTERNS = {
    "specific": PATTERN_SPECIFIC,
    "measurable": PATTERN_MEASURABLE,
    "achievable": PATTERN_ACHIEVABLE,
    "relevant": PATTERN_RELEVANT,
    "time_bound": PATTERN_TIMEFRAME,
    "disaggregation": PATTERN_DISAGG,
}


# ── Smart adapter: free text -> structured LogframeIndicator ───────────────
def parse_indicator_text(text: str, indicator_id: str = "") -> LogframeIndicator:
    """Parse an unstructured indicator string into structured fields.

    Example:
        ">= 85% households access 15L/person/day by month 12"
      -> target_value=85.0, unit="households", timeframe="by month 12",
         narrative unchanged.
    """
    text = (text or "").strip()

    # 1. Percentage target (e.g. ">= 85%", "40%")
    m = re.search(r"(>=|<=|>|<|≥|≤)?\s*(\d+(?:\.\d+)?)\s*%", text, re.I)
    target_value = 0.0
    if m:
        target_value = float(m.group(2))

    # 2. Unit (first unit token after a number)
    m = re.search(PATTERN_UNIT, text)
    unit = m.group(1).lower() if m else "individuals"

    # 3. Timeframe (full clause, e.g. "by month 12", "by Q2 2026")
    m = re.search(
        r"(?:(?:by|before|within|until|end of)\s+[^,;.]+|(?:q[1-4]|month(?:s)?|week(?:s)?|year(?:s)?|20\d\d)(?:\s+20\d\d)?)",
        text,
        re.I,
    )
    timeframe = m.group(0).strip() if m else "by end of project"

    # 4. Baseline (pattern "from X" / "baseline X" / "X to Y")
    m = re.search(r"\b(?:baseline|from)\s+(\d+(?:\.\d+)?)", text, re.I)
    baseline = float(m.group(1)) if m else 0.0

    # 5. Disaggregation tags
    disaggregated_by = [d for d in ("gender", "age", "disability") if d in text.lower()] or ["gender", "age", "disability"]

    return LogframeIndicator(
        indicator_id=indicator_id,
        narrative=text,
        target_value=target_value,
        unit=unit,
        baseline=baseline,
        timeframe=timeframe,
        disaggregated_by=disaggregated_by,
    )


def parse_indicators_list(items: List[Any], prefix: str = "ind") -> List[LogframeIndicator]:
    """Parse a list of free-text indicator strings into structured indicators."""
    parsed: List[LogframeIndicator] = []
    for i, item in enumerate(items):
        if isinstance(item, dict):
            # Already structured (dict shape) -> coerce via model
            parsed.append(LogframeIndicator(**item))
        else:
            parsed.append(parse_indicator_text(str(item), indicator_id=f"{prefix}_{i + 1}"))
    return parsed


# ── Deterministic SMART validator ─────────────────────────────────────────
def smart_validation_result(indicator_text: str) -> Dict[str, Any]:
    """Validate one indicator string against the SMART dimensions.

    Returns {passed: [...], failed: [...], score: 0..5}.
    """
    text = (indicator_text or "").strip()
    passed = [dim for dim, pat in DIMENSION_PATTERNS.items() if pat.search(text)]
    failed = [dim for dim in DIMENSION_PATTERNS if dim not in passed]
    return {
        "passed": passed,
        "failed": failed,
        "score": len(passed),
        "total": len(DIMENSION_PATTERNS),
    }


def validate_indicators(entries: List[str]) -> Dict[str, Any]:
    """Validate a batch of indicator strings; aggregates per-dimension pass.

    Returns {total_indicators, passed_indicators, dimensions: {dim: {passed, total}}, score}
    """
    dims: Dict[str, Dict[str, int]] = {}
    passed_count = 0
    for entry in entries:
        res = smart_validation_result(entry)
        for dim in DIMENSION_PATTERNS:
            dims.setdefault(dim, {"passed": 0, "total": 0})
            dims[dim]["total"] += 1
            if dim in res["passed"]:
                dims[dim]["passed"] += 1
        if res["score"] == res["total"]:
            passed_count += 1
    return {
        "total_indicators": len(entries),
        "passed_indicators": passed_count,
        "dimensions": dims,
        "score": round((passed_count / len(entries)) * 100.0, 1) if entries else 0.0,
    }


# ── SMART hardening (deterministic indicator strengthening) ───────────────
# LLM-generated indicators often miss a dimension (no timeframe, no
# disaggregation). This layer completes missing dimensions with standard
# humanitarian phrasing — no extra LLM call, fully testable. The user can
# still edit every indicator afterwards.
HARDENING_SUFFIXES = {
    "time_bound": "by month 12",
    "disaggregation": "disaggregated by gender and age",
    "measurable": "targeting at least 50% of the target population",
    "achievable": "against a documented baseline",
    "relevant": "aligned with donor priorities",
}


def harden_indicator_text(text: str) -> str:
    """Append standard phrasing for every missing SMART dimension.

    Presence-of-format only: if a dimension's pattern already matches, it is
    left untouched. Returns the hardened indicator string.
    """
    text = (text or "").strip()
    if not text:
        return text
    res = smart_validation_result(text)
    missing = [d for d in ("time_bound", "disaggregation", "measurable", "achievable", "relevant") if d in res["failed"]]
    if not missing:
        return text
    parts = [text]
    for dim in missing:
        parts.append(HARDENING_SUFFIXES[dim])
    return "; ".join(parts)


def harden_indicators_list(entries: List[str]) -> List[str]:
    """Harden a batch of indicator strings; returns hardened copies."""
    return [harden_indicator_text(e) for e in entries]
