"""
proposal/engine/advisor.py — Interactive AI Proposal Advisor & Patch Generator.
"""

import json
import logging
from typing import Any, Dict, List
import httpx

try:
    from config import OPENROUTER_API_KEY, LLM_BASE_URL, LLM_MODEL
except ImportError:
    from proposal.config import OPENROUTER_API_KEY, LLM_BASE_URL, LLM_MODEL

logger = logging.getLogger(__name__)


def advisor_chat(proposal: Dict[str, Any], user_message: str, chat_history: List[Dict[str, str]] = None) -> Dict[str, Any]:
    """Provide interactive conversational advice with actionable proposal patches."""
    donor = proposal.get("donor", "OCHA_CBPF")
    country = proposal.get("country", "Target Region")
    title = proposal.get("title", "Proposal")
    logframe = proposal.get("logframe_data") or {}
    narrative = proposal.get("narrative_data") or {}

    system_prompt = (
        f"You are an expert Senior Project Design Advisor assisting a proposal writer for a {donor} grant in {country}.\n"
        "Provide constructive, precise feedback based on Sphere standards, IASC protection rules, and donor expectations.\n"
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
        messages = [{"role": "system", "content": system_prompt}]
        if chat_history:
            for m in chat_history[-6:]:
                messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})
        messages.append({
            "role": "user",
            "content": f"Current Proposal State:\nLogframe: {json.dumps(logframe.get('matrix', []))}\nUser Question: {user_message}",
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
            with httpx.Client(timeout=35.0) as client:
                resp = client.post(f"{LLM_BASE_URL}/chat/completions", headers=headers, json=payload)
                resp.raise_for_status()
                reply_text = resp.json()["choices"][0]["message"]["content"].strip()
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

    # Deterministic helpful response with interactive patch example
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
