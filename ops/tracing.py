"""
proposal/ops/tracing.py — LLM-Ops Usage Ledger (Waku tracing pattern).

Every LLM call (generate, verify, advisor, call extraction) appends one JSONL
line to ops/usage.jsonl: model, action, prompt/response chars, tokens
(usage when the provider reports them), cost estimate, latency, timestamp.

Ground truth = tokens. The ledger is append-only and never blocks the
pipeline (all failures are swallowed with a warning).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

LEDGER_PATH = Path(__file__).resolve().parent.parent / "ops" / "usage.jsonl"

# Rough per-1K-token cost table (USD) for common OpenRouter models.
# Used only when the provider does not report cost; estimates, not billing.
MODEL_COST_PER_1K = {
    "google/gemini-2.5-flash": {"input": 0.0003, "output": 0.0025},
    "google/gemini-2.5-pro": {"input": 0.00125, "output": 0.01},
    "default": {"input": 0.0005, "output": 0.002},
}


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = MODEL_COST_PER_1K.get(model, MODEL_COST_PER_1K["default"])
    return round(
        (prompt_tokens / 1000.0) * rates["input"]
        + (completion_tokens / 1000.0) * rates["output"],
        6,
    )


def log_llm_call(
    action: str,
    model: str,
    prompt_chars: int,
    response_chars: int,
    usage: Optional[Dict[str, Any]] = None,
    latency_ms: float = 0.0,
    error: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Append one usage event to the JSONL ledger (never raises)."""
    try:
        usage = usage or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        entry = {
            "ts": time.time(),
            "action": action,
            "model": model,
            "prompt_chars": prompt_chars,
            "response_chars": response_chars,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost_usd": _estimate_cost(model, prompt_tokens, completion_tokens),
            "latency_ms": round(latency_ms, 1),
            "error": error,
        }
        if extra:
            entry["extra"] = extra
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:  # ledger must never break the pipeline
        logger.warning("Usage ledger write failed: %s", e)


def read_ledger(limit: int = 50) -> list[Dict[str, Any]]:
    """Read the most recent N ledger entries (for dashboards/endpoints)."""
    if not LEDGER_PATH.exists():
        return []
    try:
        lines = LEDGER_PATH.read_text(encoding="utf-8").strip().splitlines()
        return [json.loads(l) for l in lines[-limit:]]
    except Exception as e:
        logger.warning("Usage ledger read failed: %s", e)
        return []


def ledger_summary() -> Dict[str, Any]:
    """Aggregate totals: calls, tokens, estimated cost, per-action counts."""
    entries = read_ledger(limit=10_000)
    total_tokens = sum(e.get("total_tokens", 0) for e in entries)
    total_cost = round(sum(e.get("cost_usd", 0.0) for e in entries), 4)
    by_action: Dict[str, int] = {}
    for e in entries:
        by_action[e.get("action", "?")] = by_action.get(e.get("action", "?"), 0) + 1
    return {
        "calls": len(entries),
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "by_action": by_action,
    }
