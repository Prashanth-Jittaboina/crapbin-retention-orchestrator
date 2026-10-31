"""Comprehensive Verification Suite for Crapbin Retention & Orchestrator Agent."""
import json
import sqlite3
import sys

from fastapi.testclient import TestClient

import config
import db
import orchestrator
from webhook_receiver import app


def test_schema_and_migration():
    print("[1/4] Testing database schema & migration...")
    db.init_db()
    with db.connect() as conn:
        call_cols = {col["name"] for col in conn.execute("PRAGMA table_info(calls)").fetchall()}
        required_call_cols = {
            "orchestrator_classification",
            "classification_reasoning",
            "next_best_action",
            "followup_status",
        }
        assert required_call_cols.issubset(call_cols), f"Missing columns in calls: {required_call_cols - call_cols}"

        cust_cols = {col["name"] for col in conn.execute("PRAGMA table_info(customers)").fetchall()}
        required_cust_cols = {"address_locality", "pickup_history", "do_not_call"}
        assert required_cust_cols.issubset(cust_cols), f"Missing columns in customers: {required_cust_cols - cust_cols}"

        action_cols = {col["name"] for col in conn.execute("PRAGMA table_info(orchestrator_actions)").fetchall()}
        required_action_cols = {
            "id", "call_id", "phone", "customer_name", "orchestrator_classification",
            "next_best_action", "classification_reasoning", "scrap_summary",
            "locality", "availability", "status", "created_at", "updated_at"
        }
        assert required_action_cols.issubset(action_cols), f"Missing columns in orchestrator_actions: {required_action_cols - action_cols}"
    print("      Database schema and migration verified successfully.")


def test_existing_11_calls():
    print("[2/4] Testing backward compatibility of 11 existing calls in crapbin.db...")
    valid_classifications = {"Hot Lead", "Warm Lead", "Cold Lead", "Do Not Call"}
    valid_actions = set(orchestrator.TIER_ACTIONS.values())

    calls = db.all_calls()
    assert len(calls) == 11, f"Expected 11 calls in crapbin.db, found {len(calls)}"

    web_call_ids = [
        "d3ebd752-2ec9-40ee-9e67-0d7570e5814f",
        "577d10bd-c137-4170-862a-664ec80bc227",
        "3990569d-9563-47ab-b8fd-a504b0cd7b4e",
        "2fe7076c-3a36-4346-8460-53dab804a347",
    ]

    actions = db.all_orchestrator_actions()
    action_map = {a["call_id"]: a for a in actions}

    for c in calls:
        cid = c["call_id"]
        cls = c["orchestrator_classification"]
        action = c["next_best_action"]
        reason = c["classification_reasoning"]
        status = c["followup_status"]

        assert cls in valid_classifications, f"Call {cid} has invalid classification: {cls}"
        assert action in valid_actions, f"Call {cid} has invalid action: {action}"
        assert reason, f"Call {cid} missing reasoning"
        assert status in ("action_queued", "completed", "opted_out", "pending"), f"Call {cid} has invalid status: {status}"

        # Check corresponding row in orchestrator_actions
        assert cid in action_map, f"Call {cid} missing from orchestrator_actions table"
        act = action_map[cid]
        assert act["orchestrator_classification"] == cls
        assert act["next_best_action"] == action

    # Specifically check the 4 web calls with phone=None
    for w_id in web_call_ids:
        assert w_id in action_map, f"Web call {w_id} missing from orchestrator_actions"
        assert action_map[w_id]["phone"] is None, f"Expected None phone for web call {w_id}"
        assert action_map[w_id]["orchestrator_classification"] == "Hot Lead"

    print("      All 11 existing calls and 4 web calls verified in calls and orchestrator_actions.")


def test_fastapi_endpoints():
    print("[3/4] Testing FastAPI REST endpoints & token authentication...")
    client = TestClient(app)

    # 1. Health
    r = client.get("/health")
    assert r.status_code == 200, f"/health returned {r.status_code}"
    data = r.json()
    assert data["status"] == "ok"
    assert "llm_provider" in data

    # 2. Summary
    r = client.get("/summary")
    assert r.status_code == 200, f"/summary returned {r.status_code}"
    summary = r.json()
    assert summary["total_calls"] == 11
    assert summary["tiers"]["Hot Lead"] == 6
    assert summary["tiers"]["Warm Lead"] == 4
    assert summary["tiers"]["Cold Lead"] == 1
    assert summary["tiers"]["Do Not Call"] == 0
    assert summary["total_actions"] == 11

    # 3. Calls
    r = client.get("/calls")
    assert r.status_code == 200, f"/calls returned {r.status_code}"
    calls_resp = r.json()
    assert calls_resp["total"] == 11
    assert len(calls_resp["calls"]) == 11

    # 4. Actions
    r = client.get("/actions")
    assert r.status_code == 200, f"/actions returned {r.status_code}"
    actions_resp = r.json()
    assert actions_resp["total"] == 11

    r_hot = client.get("/actions?classification=Hot Lead")
    assert r_hot.status_code == 200
    assert r_hot.json()["total"] == 6

    # 5. Token authentication
    r_unauth = client.post("/webhook/bolna", json={})
    assert r_unauth.status_code == 401

    r_auth_query = client.post(f"/webhook/bolna?token={config.WEBHOOK_TOKEN}", json={})
    assert r_auth_query.status_code == 200

    r_auth_header = client.post("/webhook/bolna", headers={"X-Webhook-Token": config.WEBHOOK_TOKEN}, json={})
    assert r_auth_header.status_code == 200

    r_auth_bearer = client.post("/webhook/bolna", headers={"Authorization": f"Bearer {config.WEBHOOK_TOKEN}"}, json={})
    assert r_auth_bearer.status_code == 200

    print("      FastAPI endpoints and auth tokens verified successfully.")


def test_orchestrator_guardrails_and_tiers():
    print("[4/4] Testing Orchestrator 4-Tier Guardrails & Scenarios...")

    # Scenario 1: Hot Lead
    hot_call = {"transcript": "user: Yes I have old newspapers and iron almirah. Available tomorrow in Kukatpally."}
    hot_insights = {
        "outcome": "interested",
        "confidence": 0.95,
        "scrap_materials": ["newspapers", "iron almirah"],
        "approx_quantity": "15 kgs",
        "locality": "Kukatpally",
        "availability": "tomorrow morning",
        "reason": "Customer confirmed pickup with scrap details.",
    }
    dec_hot = orchestrator.evaluate_lead(hot_call, hot_insights)
    assert dec_hot["orchestrator_classification"] == "Hot Lead"
    assert dec_hot["next_best_action"] == "Create pickup request for coordinator"
    assert dec_hot["segment"] == "interested"
    assert dec_hot["followup_status"] == "action_queued"

    # Scenario 2: Warm Lead (callback)
    warm_call = {"transcript": "user: Please call me back on Saturday evening."}
    warm_insights = {
        "outcome": "callback_later",
        "confidence": 0.85,
        "scrap_materials": ["old books"],
        "availability": "Saturday evening",
        "reason": "Customer requested callback.",
    }
    dec_warm = orchestrator.evaluate_lead(warm_call, warm_insights)
    assert dec_warm["orchestrator_classification"] == "Warm Lead"
    assert dec_warm["next_best_action"] == "Schedule follow-up call"
    assert dec_warm["segment"] == "callback"
    assert dec_warm["followup_status"] == "action_queued"

    # Scenario 3: Warm Lead (interested but low confidence / missing details -> needs_review)
    review_call = {"transcript": "user: Maybe yes."}
    review_insights = {
        "outcome": "interested",
        "confidence": 0.5,
        "scrap_materials": [],
        "reason": "Ambiguous response.",
    }
    dec_review = orchestrator.evaluate_lead(review_call, review_insights)
    assert dec_review["orchestrator_classification"] == "Warm Lead"
    assert dec_review["next_best_action"] == "Schedule follow-up call"
    assert dec_review["segment"] == "needs_review"

    # Scenario 4: Warm Lead (no answer / customer didn't speak)
    no_ans_call = {"status": "no-answer", "transcript": None}
    dec_no_ans = orchestrator.evaluate_lead(no_ans_call, None)
    assert dec_no_ans["orchestrator_classification"] == "Warm Lead"
    assert dec_no_ans["next_best_action"] == "Schedule follow-up call"
    assert dec_no_ans["segment"] == "not_reached"

    # Scenario 5: Cold Lead (polite decline, no scrap currently)
    cold_call = {"transcript": "user: Already cleared all scrap last month, maybe check after 3 months."}
    cold_insights = {
        "outcome": "not_interested_now",
        "confidence": 0.90,
        "reason": "Customer disposed of scrap recently.",
    }
    dec_cold = orchestrator.evaluate_lead(cold_call, cold_insights)
    assert dec_cold["orchestrator_classification"] == "Cold Lead"
    assert dec_cold["next_best_action"] == "Add to future nurture list"
    assert dec_cold["segment"] == "not_interested"
    assert dec_cold["followup_status"] == "completed"

    # Scenario 6: Do Not Call (transcript opt-out phrase)
    dnc_call = {"transcript": "user: Stop calling me and remove my number immediately!"}
    dnc_insights = {
        "outcome": "not_interested",
        "confidence": 0.99,
        "reason": "Customer requested removal.",
    }
    dec_dnc = orchestrator.evaluate_lead(dnc_call, dnc_insights)
    assert dec_dnc["orchestrator_classification"] == "Do Not Call"
    assert dec_dnc["next_best_action"] == "Mark as Do Not Call"
    assert dec_dnc["followup_status"] == "opted_out"

    # Scenario 7: Do Not Call (wrong number)
    wrong_call = {"transcript": "user: Wrong number, I am not this person."}
    wrong_insights = {
        "outcome": "wrong_number",
        "confidence": 0.99,
        "reason": "Wrong number.",
    }
    dec_wrong = orchestrator.evaluate_lead(wrong_call, wrong_insights)
    assert dec_wrong["orchestrator_classification"] == "Do Not Call"
    assert dec_wrong["next_best_action"] == "Mark as Do Not Call"

    print("      All 4 tiers and guardrails verified successfully.")


if __name__ == "__main__":
    test_schema_and_migration()
    test_existing_11_calls()
    test_fastapi_endpoints()
    test_orchestrator_guardrails_and_tiers()
    print("\n" + "=" * 60)
    print(" ALL 4 VERIFICATION SUITE CHECKS PASSED (100% ALIGNMENT) ")
    print("=" * 60)

