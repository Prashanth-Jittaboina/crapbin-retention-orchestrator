"""Read a call transcript with Gemini (or OpenAI) and return structured insights + a follow-up email draft."""
import json
import re
import time
from typing import List, Literal, Optional

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

import config

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY or None)  # None -> reads GEMINI_API_KEY env
    return _client


SYSTEM = """You analyse phone call transcripts from Crapbin, a doorstep scrap pickup service in Hyderabad.
An AI agent ("assistant") called a previous customer ("user") to ask whether they have scrap for another pickup.
Transcripts may be in English, Hindi, Telugu, or a mix; speech-to-text errors are common.

1. Classify the customer outcome into one of these 6 values:
- interested: the customer said they have scrap now and/or wants to schedule a pickup.
- callback_later: the customer asked to be called later, needs time to check with family/spouse, or the call disconnected before answering.
- not_interested_now: the customer politely declined for now, has no scrap currently, or recently sold scrap, but did NOT ask to stop calling or opt out.
- do_not_call: the customer explicitly asked not to be called again ("don't call", "stop calling", "remove my number"), or gave a hostile/firm refusal.
- wrong_number: the person stated this is a wrong number, wrong person, or invalid contact.
- no_conversation: the customer never engaged (only "hello", silence, hung up within greeting, garbled audio, no speech).

2. Classify into one of 4 Orchestrator Lead Classifications and exact next_best_action:
- Hot Lead:
  - Definition: Customer wants to schedule a pickup, provided scrap details (materials, quantity, or locality), and is available in near term.
  - next_best_action: "Create pickup request for coordinator"
- Warm Lead:
  - Definition: Generally interested, needs time to confirm, requested callback, or call was not reached / no conversation.
  - next_best_action: "Schedule follow-up call"
- Cold Lead:
  - Definition: Polite decline for now, no scrap currently, might consider later, but not opting out.
  - next_best_action: "Add to future nurture list"
- Do Not Call:
  - Definition: Explicit opt-out request, firm refusal, or wrong number.
  - next_best_action: "Mark as Do Not Call"

3. Extract detailed fields:
- scrap_materials: list of items mentioned (translated to English, e.g. "newspapers", "iron almirah", "plastic", "cardboard"). Empty list if none.
- approx_quantity: e.g. "15 kgs", "2 boxes", null if unknown.
- locality: area, colony, or landmark mentioned in Hyderabad (e.g. "Kukatpally", "Madhapur", "Banjara Hills"), null if unknown.
- availability: customer's availability time window (e.g. "tomorrow morning", "this weekend", "next week", "Saturday evening"), null if unknown.
- customer_mentioned_date: specific date if mentioned, null if unknown.
- language: "english", "hindi", "telugu", or "mixed".
- summary: concise 1-2 sentence summary of the call.
- reason: brief explanation of classification and next action.
- follow_up_email: write a warm 3-5 sentence plain-text email in English thanking them and noting their scrap details ONLY when outcome is "interested" (Hot Lead). Never promise dates or prices. Otherwise null."""


class CallInsights(BaseModel):
    outcome: Literal[
        "interested",
        "callback_later",
        "not_interested_now",
        "do_not_call",
        "wrong_number",
        "no_conversation",
    ]
    orchestrator_classification: Literal[
        "Hot Lead",
        "Warm Lead",
        "Cold Lead",
        "Do Not Call",
    ]
    next_best_action: Literal[
        "Create pickup request for coordinator",
        "Schedule follow-up call",
        "Add to future nurture list",
        "Mark as Do Not Call",
    ]
    confidence: float
    reason: str
    customer_name: Optional[str] = None
    scrap_materials: List[str] = []
    approx_quantity: Optional[str] = None
    locality: Optional[str] = None
    availability: Optional[str] = None
    customer_mentioned_date: Optional[str] = None
    language: Literal["english", "hindi", "telugu", "mixed"]
    summary: str
    follow_up_email: Optional[str] = None


class LLMBlocked(Exception):
    pass


def analyze_call(transcript, bolna_outcome=None):
    user_msg = f"<transcript>\n{transcript}\n</transcript>"
    if bolna_outcome:
        user_msg += f"\n\nBolna's own label (often wrong on short calls): {bolna_outcome}"

    if config.LLM_PROVIDER == "openai" and config.OPENAI_API_KEY:
        insights = _generate_openai(user_msg)
    else:
        insights = _generate_gemini(user_msg)

    if isinstance(insights, BaseModel):
        return insights.model_dump()
    return insights


def _generate_openai(user_msg, attempts=3):
    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    system_content = (
        SYSTEM
        + "\nYou must respond strictly with a valid JSON object matching the requested schema."
    )
    payload = {
        "model": config.OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_msg},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }
    for attempt in range(attempts):
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return CallInsights.model_validate_json(content)
        except Exception as e:
            if attempt == attempts - 1:
                raise
            time.sleep(2)


def _generate_gemini(user_msg, attempts=4):
    """Call Gemini, waiting out per-minute rate limits (free tier allows 5 requests/minute)."""
    for attempt in range(attempts):
        try:
            resp = _get_client().models.generate_content(
                model=config.GEMINI_MODEL,
                contents=user_msg,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM,
                    response_mime_type="application/json",
                    response_schema=CallInsights,
                    temperature=0.1,
                ),
            )
            if resp.parsed is None:
                raise LLMBlocked(
                    f"no parsable output (finish_reason="
                    f"{resp.candidates[0].finish_reason if resp.candidates else 'none'})"
                )
            return resp.parsed
        except errors.APIError as e:
            if e.code not in (429, 503) or attempt == attempts - 1:
                raise
            # Gemini says how long to wait ("retry in 46.4s"); fall back to 60s
            m = re.search(r"retry in ([\d.]+)s", str(e))
            wait = float(m.group(1)) + 2 if m else 60
            print(f"Gemini rate-limited, waiting {wait:.0f}s...")
            time.sleep(wait)
