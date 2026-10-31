"""Orchestrator Agent (Step 5 & Step 6).

Classifies each lead into one of 4 exact tiers and determines the Next-Best Action:
1. Hot Lead   -> Create pickup request for coordinator
2. Warm Lead  -> Schedule follow-up call
3. Cold Lead  -> Add to future nurture list
4. Do Not Call -> Mark as Do Not Call
"""
import json
from typing import Any, Dict, Optional

import db
import llm
from normalize import FINAL_STATUSES, NOT_REACHED_STATUSES, customer_spoke

TIER_ACTIONS = {
    "Hot Lead": "Create pickup request for coordinator",
    "Warm Lead": "Schedule follow-up call",
    "Cold Lead": "Add to future nurture list",
    "Do Not Call": "Mark as Do Not Call",
}

OPT_OUT_PHRASES = [
    "do not call",
    "don't call",
    "dont call",
    "stop calling",
    "wrong number",
    "remove my number",
    "remove this number",
    "remove me",
    "never call",
    "not interested at all",
]


def evaluate_lead(call: Dict[str, Any], insights: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Applies deterministic guardrails on top of the call and LLM insights to produce
    the exact Orchestrator classification, next-best action, reasoning, followup status,
    and legacy segment.
    """
    transcript = (call.get("transcript") or "").lower()
    is_explicit_opt_out = any(phrase in transcript for phrase in OPT_OUT_PHRASES)

    outcome = (insights.get("outcome") if insights else "").lower()
    llm_classification = (insights.get("orchestrator_classification") if insights else "")
    confidence = float(insights.get("confidence") or 0.0) if insights else 0.0

    scrap_materials = (insights.get("scrap_materials") if insights else []) or []
    approx_quantity = (insights.get("approx_quantity") if insights else None)
    locality = (insights.get("locality") if insights else None)
    has_scrap_details = bool(scrap_materials or approx_quantity or locality)

    # 1. Do Not Call: Explicit refusal, hostile/opt-out request, or wrong number
    if (
        is_explicit_opt_out
        or outcome in ("do_not_call", "wrong_number")
        or llm_classification == "Do Not Call"
    ):
        return {
            "orchestrator_classification": "Do Not Call",
            "next_best_action": TIER_ACTIONS["Do Not Call"],
            "classification_reasoning": (
                (insights and insights.get("reason"))
                or "Customer explicitly requested Do Not Call / wrong number / opt-out."
            ),
            "followup_status": "opted_out",
            "segment": "not_interested",
        }

    # 2. Hot Lead: Interested in pickup with scrap details and high confidence
    if outcome == "interested" and confidence >= 0.7 and has_scrap_details:
        return {
            "orchestrator_classification": "Hot Lead",
            "next_best_action": TIER_ACTIONS["Hot Lead"],
            "classification_reasoning": (
                (insights and insights.get("reason"))
                or "Customer confirmed interest and provided scrap details for pickup."
            ),
            "followup_status": "action_queued",
            "segment": "interested",
        }

    # 3. Warm Lead: Interested but lacks details, requested callback, or call was not reached / no conversation
    if (
        outcome == "callback_later"
        or (outcome == "interested" and (confidence < 0.7 or not has_scrap_details))
        or outcome == "no_conversation"
        or llm_classification == "Warm Lead"
        or insights is None
        or call.get("status") in NOT_REACHED_STATUSES
        or not customer_spoke(call.get("transcript"))
    ):
        if outcome == "interested":
            legacy_segment = "needs_review"
            reasoning = (
                (insights and insights.get("reason"))
                or "Customer expressed interest but details require coordinator review."
            )
        elif outcome == "callback_later":
            legacy_segment = "callback"
            reasoning = (
                (insights and insights.get("reason"))
                or "Customer requested a callback or needs time to confirm availability."
            )
        else:
            legacy_segment = "not_reached"
            reasoning = (
                (insights and insights.get("reason"))
                or "Customer did not answer or audio dropped before engagement; follow-up call scheduled."
            )

        return {
            "orchestrator_classification": "Warm Lead",
            "next_best_action": TIER_ACTIONS["Warm Lead"],
            "classification_reasoning": reasoning,
            "followup_status": "action_queued",
            "segment": legacy_segment,
        }

    # 4. Cold Lead: Polite decline for now, no scrap currently, but did not opt out
    if outcome in ("not_interested_now", "not_interested") or llm_classification == "Cold Lead":
        return {
            "orchestrator_classification": "Cold Lead",
            "next_best_action": TIER_ACTIONS["Cold Lead"],
            "classification_reasoning": (
                (insights and insights.get("reason"))
                or "Customer has no scrap currently; added to future nurture list."
            ),
            "followup_status": "completed",
            "segment": "not_interested",
        }

    # Fallback to Warm Lead
    return {
        "orchestrator_classification": "Warm Lead",
        "next_best_action": TIER_ACTIONS["Warm Lead"],
        "classification_reasoning": (
            (insights and insights.get("reason"))
            or "Uncertain lead outcome; follow-up call scheduled."
        ),
        "followup_status": "action_queued",
        "segment": "callback",
    }


def orchestrate_call(call_id: str, force: bool = False, reanalyze_llm: bool = False) -> Optional[Dict[str, Any]]:
    """Evaluates a call, records Orchestrator decisions in `calls` and `orchestrator_actions`,
    and handles customer DNC updates."""
    call = db.get_call(call_id)
    if not call:
        return None

    call_dict = dict(call)
    insights = None
    if call_dict.get("insights_json") and not reanalyze_llm:
        try:
            insights = json.loads(call_dict["insights_json"])
        except Exception:
            insights = None

    if insights is None and (not call_dict.get("processed_at") or force or reanalyze_llm):
        status = call_dict.get("status")
        if status in NOT_REACHED_STATUSES or not customer_spoke(call_dict.get("transcript")):
            insights = None
        elif status in ("completed", "call-disconnected"):
            try:
                insights = llm.analyze_call(call_dict.get("transcript"), call_dict.get("bolna_outcome"))
            except Exception as e:
                db.save_orchestrator_decision(
                    call_id=call_id,
                    segment=None,
                    insights=None,
                    orchestrator_classification=None,
                    classification_reasoning=None,
                    next_best_action=None,
                    followup_status=None,
                    error=f"{type(e).__name__}: {e}",
                )
                print(f"[{call_id}] LLM extraction failed: {e}")
                return None

    decision = evaluate_lead(call_dict, insights)

    # Save Orchestrator decision into calls
    db.save_orchestrator_decision(
        call_id=call_id,
        segment=decision["segment"],
        insights=insights,
        orchestrator_classification=decision["orchestrator_classification"],
        classification_reasoning=decision["classification_reasoning"],
        next_best_action=decision["next_best_action"],
        followup_status=decision["followup_status"],
    )

    # Prepare fields for orchestrator_actions table
    customer = db.get_customer(call_dict.get("phone"))
    customer_name = (insights and insights.get("customer_name")) or (customer and customer["name"])

    materials = ", ".join((insights and insights.get("scrap_materials")) or [])
    qty = (insights and insights.get("approx_quantity"))
    if materials and qty:
        scrap_summary = f"{materials} ({qty})"
    elif materials:
        scrap_summary = materials
    elif qty:
        scrap_summary = qty
    else:
        scrap_summary = (insights and insights.get("summary"))

    locality = (insights and insights.get("locality"))
    availability = (insights and (insights.get("availability") or insights.get("customer_mentioned_date")))

    db.upsert_orchestrator_action(
        call_id=call_id,
        phone=call_dict.get("phone"),
        customer_name=customer_name,
        orchestrator_classification=decision["orchestrator_classification"],
        next_best_action=decision["next_best_action"],
        classification_reasoning=decision["classification_reasoning"],
        scrap_summary=scrap_summary,
        locality=locality,
        availability=availability,
        status="open",
    )

    if decision["orchestrator_classification"] == "Do Not Call" and call_dict.get("phone"):
        db.mark_customer_dnc(call_dict["phone"], 1)

    return {
        "call_id": call_id,
        "insights": insights,
        "decision": decision,
    }


def backfill_existing_calls():
    """Immediately backfills all calls in crapbin.db using their stored insights and transcripts."""
    db.init_db()
    with db.connect() as conn:
        rows = conn.execute("SELECT call_id FROM calls").fetchall()

    backfilled_count = 0
    for r in rows:
        res = orchestrate_call(r["call_id"], force=True, reanalyze_llm=False)
        if res:
            backfilled_count += 1
    return backfilled_count

