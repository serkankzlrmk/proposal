"""
proposal/engine/advisor.py — Interactive AI Proposal Advisor & Patch Generator.

v2: Advisor now consumes a token-efficient AdvisorContext (built from the
deterministic scoring engine's trace), NOT the raw proposal dump.
  - Gate status shapes strategy: AUTOMATIC_REJECTION -> fix quotas first.
  - Diagnostics carry only violating blocks (section_key + snippet).
  - Registry is passed so the LLM never invents references.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional
import httpx

try:
    from config import OPENROUTER_API_KEY, LLM_BASE_URL, LLM_MODEL
    from ops.tracing import log_llm_call
except ImportError:
    from proposal.config import OPENROUTER_API_KEY, LLM_BASE_URL, LLM_MODEL
    from proposal.ops.tracing import log_llm_call

try:
    from engine.advisor_context import AdvisorContext, build_advisor_system_prompt
except ImportError:
    from proposal.engine.advisor_context import AdvisorContext, build_advisor_system_prompt

logger = logging.getLogger(__name__)


# ── Small-talk fast path (deterministic, no LLM call) ─────────────────────
# Language policy: the advisor ALWAYS answers in English, even when the user
# greets in Turkish — the product language is English (user directive).
_GREETINGS = ("hello", "hi", "hey", "good morning", "good evening", "good afternoon", "merhaba", "selam", "nasılsın", "naber")
_THANKS = ("thanks", "thank you", "teşekkür", "tesekkur", "sağol", "eyvallah")


def _small_talk_reply(user_message: str, title: str, donor: str, country: str, step: int) -> Optional[str]:
    """Deterministic replies for greetings/thanks — no LLM cost. English only."""
    msg = (user_message or "").strip().lower()
    if not msg:
        return None
    if any(g in msg for g in _GREETINGS):
        return (
            f"Hello! 👋 I'm your GMS Proposal Advisor. You're currently working on "
            f"**'{title}'** ({donor}, {country}) — at **Step {step}/5**.\n\n"
            f"Ask me anything: whether an indicator is SMART, whether the budget fits the cap, "
            f"which section is weak, or just say 'fix it' and I'll propose an action. How shall we proceed?"
        )
    if any(t in msg for t in _THANKS):
        return (
            "You're welcome! 🙌 Anything else — I can run a SMART check on your logframe "
            "indicators or review the narrative sections against the donor requirements."
        )
    return None


def _proposal_status_summary(proposal: Dict[str, Any]) -> str:
    """Compact status summary so the advisor knows where the proposal stands."""
    donor = proposal.get("donor", "OCHA_CBPF")
    step = proposal.get("step", 1)
    title = proposal.get("title", "Untitled")
    country = proposal.get("country", "—")
    logframe = proposal.get("logframe_data") or {}
    narrative = proposal.get("narrative_data") or {}
    matrix = logframe.get("matrix", []) if isinstance(logframe, dict) else []
    nar_count = len(narrative) if isinstance(narrative, dict) else 0
    refs = proposal.get("references") or []
    return (
        f"Proposal: '{title}' | donor={donor} | country={country} | step={step}/5 | "
        f"logframe_rows={len(matrix)} | narrative_sections={nar_count} | references={len(refs)}"
    )


def advisor_chat(
    proposal: Dict[str, Any],
    user_message: str,
    chat_history: List[Dict[str, str]] = None,
    advisor_ctx: AdvisorContext = None,
) -> Dict[str, Any]:
    """Provide interactive conversational advice with actionable proposal patches.

    advisor_ctx: optional pre-built AdvisorContext (from /analyze). When absent,
    the function degrades gracefully to a deterministic response.
    """
    donor = proposal.get("donor", "OCHA_CBPF")
    country = proposal.get("country", "Target Region")
    title = proposal.get("title", "Proposal")
    logframe = proposal.get("logframe_data") or {}
    narrative = proposal.get("narrative_data") or {}
    step = proposal.get("step", 1)

    # ── Small-talk fast path (deterministic, no LLM call) ──────────────────
    small_talk = _small_talk_reply(user_message, title, donor, country, step)
    if small_talk is not None:
        return {"message": small_talk, "patch": None}

    # ── Proposal status summary (injected so the advisor knows where we are) ─
    status_summary = _proposal_status_summary(proposal)

    # System prompt: context-aware when AdvisorContext provided
    if advisor_ctx is not None:
        base_system = build_advisor_system_prompt(advisor_ctx)
    else:
        base_system = (
            f"You are an expert Senior Project Design Advisor assisting a proposal writer for a {donor} grant in {country}.\n"
            "LANGUAGE POLICY: ALWAYS reply in English, no matter what language the user writes in.\n"
            "You are a friendly, collaborative partner — not a robot. Start by acknowledging what the user says, "
            "then give precise, actionable feedback.\n"
            "When the user greets you (hello, hi, merhaba, selam), greet back warmly and briefly summarize the "
            "current proposal status and what you recommend next.\n"
            "When the user thanks you, acknowledge it and offer the next concrete step.\n"
            "Always ground feedback in the proposal's actual state (step, score, gates) — never invent data.\n"
            "If you recommend a specific improvement to a Logframe cell or narrative section, include a structured patch block in this JSON format at the very end of your reply:\n"
            "```json\n"
            "{\n"
            '  "action": "update_logframe",\n'
            '  "row_index": 1,\n'
            '  "field": "indicators",\n'
            '  "suggested_value": ">= 85% of target households access >= 15L water/person/day."\n'
            "}\n"
            "```"
        )

    if OPENROUTER_API_KEY:
        messages = [{"role": "system", "content": base_system}]
        if chat_history:
            for m in chat_history[-6:]:
                messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})
        # Compact context injection: localized snippets, not full proposal
        if advisor_ctx is not None:
            context_payload = advisor_ctx.model_dump_json()
        else:
            context_payload = f"Logframe: {json.dumps(logframe.get('matrix', []))}"
        messages.append({
            "role": "user",
            "content": f"Proposal Status: {status_summary}\n\nAdvisor Context:\n{context_payload}\n\nUser Question: {user_message}",
        })

        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://sightline.humanitarian.ai",
            "X-Title": "Sightline Proposal Advisor",
        }
        payload = {
            "model": LLM_MODEL,
            "temperature": 0.3,
            "messages": messages,
        }
        try:
            t0 = time.time()
            with httpx.Client(timeout=35.0) as client:
                resp = client.post(f"{LLM_BASE_URL}/chat/completions", headers=headers, json=payload)
                resp.raise_for_status()
                reply_text = resp.json()["choices"][0]["message"]["content"].strip()
                usage = resp.json().get("usage") or {}
                log_llm_call(
                    action="advisor_chat",
                    model=LLM_MODEL,
                    prompt_chars=sum(len(m.get("content", "")) for m in messages),
                    response_chars=len(reply_text),
                    usage=usage,
                    latency_ms=(time.time() - t0) * 1000.0,
                )
                # Parse patch if present
                patch = None
                if "```json" in reply_text:
                    try:
                        json_block = reply_text.split("```json", 1)[1].split("```", 1)[0].strip()
                        patch = json.loads(json_block)
                    except Exception:
                        pass
                return {
                    "message": reply_text,
                    "patch": patch,
                }
        except Exception as e:
            logger.warning("Advisor LLM call failed: %s", e)
            log_llm_call(
                action="advisor_chat",
                model=LLM_MODEL,
                prompt_chars=sum(len(m.get("content", "")) for m in messages),
                response_chars=0,
                error=str(e),
            )

    # Deterministic helpful response with interactive patch example
    if advisor_ctx is not None and advisor_ctx.is_blocked:
        blocking = advisor_ctx.gate_evaluation.blocking_reasons
        first = blocking[0] if blocking else None
        message = (
            f"Regarding your project **'{title}'** for **{donor}**:\n\n"
            f"⚠️ **This proposal is currently BLOCKED** — {len(blocking)} hard gate issue(s).\n\n"
            f"Top blocker: **{first.field if first else 'quota'}** — {first.detail if first else ''}\n\n"
            "I recommend fixing the quota/eligibility issue BEFORE polishing text, "
            "otherwise the proposal faces desk rejection regardless of narrative quality."
        )
        return {"message": message, "patch": None}

    return {
        "message": (
            f"Regarding your project **'{title}'** for **{donor}**:\n\n"
            "I recommend strengthening the **Outcome 1 indicator** by explicitly benchmarking against the Sphere minimum standard of **15 liters per person per day** and incorporating sex-disaggregated PDM surveys.\n\n"
            "Would you like me to apply this refined indicator directly to your Logframe?"
        ),
        "patch": {
            "action": "update_logframe",
            "row_index": 1,
            "field": "indicators",
            "suggested_value": ">= 85% of target population accessing >= 15L clean drinking water/person/day (Sphere standard); disaggregated by SADD.",
        },
    }
