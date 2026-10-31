import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

import config
import db
import followup
import orchestrator
from normalize import FINAL_STATUSES, from_bolna_payload
from send_test_webhook import SCENARIOS

BASELINE_CALL_IDS = [
    "d3ebd752-2ec9-40ee-9e67-0d7570e5814f",
    "577d10bd-c137-4170-862a-664ec80bc227",
    "3990569d-9563-47ab-b8fd-a504b0cd7b4e",
    "a43da8d1-bfd2-484a-ab1c-86476c29229b",
    "a4b07b9b-c35a-4699-9cf4-829eccce4f73",
    "c79c59d9-047e-4ada-addf-d5ec36af2c1f",
    "7a6a5bfb-5e8d-4918-ac4d-34d16343df60",
    "619bd64b-9b41-48c0-b38e-ffd04a84ca62",
    "125ee634-3341-483a-b00d-8908b1bf8b7e",
    "exec_sample_completed",
    "2fe7076c-3a36-4346-8460-53dab804a347",
]


def verify_token(request: Request, token: str = ""):
    if not config.WEBHOOK_TOKEN:
        return
    if token and token == config.WEBHOOK_TOKEN:
        return
    x_token = request.headers.get("x-webhook-token")
    if x_token and x_token == config.WEBHOOK_TOKEN:
        return
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and auth[7:].strip() == config.WEBHOOK_TOKEN:
        return
    raise HTTPException(status_code=401, detail="Unauthorized: invalid webhook token")


async def followup_loop():
    """Retries failed LLM calls and sends messages held back outside the send window."""
    while True:
        try:
            await asyncio.to_thread(followup.run)
        except Exception as e:
            print(f"follow-up run failed: {e}")
        await asyncio.sleep(config.FOLLOWUP_INTERVAL_MIN * 60)


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    os.makedirs(config.JSON_DIR, exist_ok=True)
    print(f"DRY_RUN={config.DRY_RUN}  provider={config.LLM_PROVIDER}  "
          f"model={config.OPENAI_MODEL if config.LLM_PROVIDER == 'openai' else config.GEMINI_MODEL}  "
          f"token={'set' if config.WEBHOOK_TOKEN else 'NOT SET'}")
    task = asyncio.create_task(followup_loop())
    yield
    task.cancel()


app = FastAPI(
    title="Crapbin Customer Retention & Orchestrator Agent API",
    version="1.0.0",
    lifespan=lifespan,
)


def handle_final_call(call_id):
    followup.process_call(call_id)
    followup.send_queued()


@app.get("/", response_class=HTMLResponse)
def dashboard_view():
    dashboard_file = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if os.path.exists(dashboard_file):
        with open(dashboard_file, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Crapbin Retention & Orchestrator Agent API is running. Visit /docs for Swagger UI.</h1>"


@app.post("/webhook/bolna")
async def receive_call(request: Request, background: BackgroundTasks, token: str = ""):
    verify_token(request, token)

    payload = await request.json()
    record = from_bolna_payload(payload)
    call_id = record["call_id"]
    if not call_id:
        return {"status": "ignored", "reason": "no call id"}

    # Bolna posts once per status change; same call_id overwrites, so we keep the latest
    db.upsert_call(record, payload)
    c = record.get("customer") or {}
    db.upsert_customer(
        record["phone"],
        name=c.get("name"),
        email=c.get("email"),
        last_pickup_date=c.get("last_pickup_date"),
        address_locality=c.get("address_locality"),
        pickup_history=c.get("pickup_history"),
    )
    with open(os.path.join(config.JSON_DIR, f"{call_id}.json"), "w", encoding="utf-8") as f:
        json.dump({"normalized": record, "raw": payload}, f, indent=2, ensure_ascii=False)

    print(f"[{call_id}] status={record['status']} phone={record['phone']} outcome={record['bolna_outcome']}")

    # LLM + Orchestrator + messaging run after we've replied, so Bolna doesn't time out
    if record["status"] in FINAL_STATUSES:
        background.add_task(handle_final_call, call_id)

    return {"status": "success", "call_id": call_id}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "llm_provider": config.LLM_PROVIDER,
        "model": config.OPENAI_MODEL if config.LLM_PROVIDER == "openai" else config.GEMINI_MODEL,
        "dry_run": config.DRY_RUN,
    }


@app.get("/calls")
def get_calls():
    calls = db.all_calls()
    results = []
    for c in calls:
        insights = None
        if c.get("insights_json"):
            try:
                insights = json.loads(c["insights_json"])
            except Exception:
                pass
        results.append({
            "call_id": c["call_id"],
            "agent_id": c["agent_id"],
            "phone": c["phone"],
            "status": c["status"],
            "duration_sec": c["duration_sec"],
            "created_at": c["created_at"],
            "recording_url": c["recording_url"],
            "summary": c["summary"],
            "transcript": c["transcript"],
            "bolna_outcome": c["bolna_outcome"],
            "bolna_confidence": c["bolna_confidence"],
            "segment": c["segment"],
            "orchestrator_classification": c["orchestrator_classification"],
            "next_best_action": c["next_best_action"],
            "classification_reasoning": c["classification_reasoning"],
            "followup_status": c["followup_status"],
            "insights": insights,
            "processed_at": c["processed_at"],
            "process_error": c["process_error"],
        })
    return {"total": len(results), "calls": results}


@app.get("/actions")
def get_actions(classification: Optional[str] = None):
    actions = db.all_orchestrator_actions(classification=classification)
    return {
        "total": len(actions),
        "classification_filter": classification,
        "actions": actions,
    }


@app.get("/messages")
def get_messages():
    messages = db.all_messages()
    parsed = []
    for m in messages:
        m_dict = dict(m)
        if m_dict.get("payload_json"):
            try:
                m_dict["payload"] = json.loads(m_dict["payload_json"])
            except Exception:
                m_dict["payload"] = m_dict["payload_json"]
        parsed.append(m_dict)
    return {
        "total": len(parsed),
        "messages": parsed,
    }


@app.get("/summary")
def get_summary():
    calls = db.all_calls()
    actions = db.all_orchestrator_actions()

    tier_counts = {
        "Hot Lead": 0,
        "Warm Lead": 0,
        "Cold Lead": 0,
        "Do Not Call": 0,
    }
    unclassified_count = 0
    answered_count = 0
    not_answered_count = 0

    for c in calls:
        cls = c.get("orchestrator_classification")
        if cls in tier_counts:
            tier_counts[cls] += 1
        else:
            unclassified_count += 1

        # Check telephony outcome & whether customer picked up and spoke
        status = (c.get("status") or "").lower()
        from normalize import NOT_REACHED_STATUSES, customer_spoke
        if status in NOT_REACHED_STATUSES or not customer_spoke(c.get("transcript")):
            not_answered_count += 1
        else:
            answered_count += 1

    actions_by_status = {}
    for a in actions:
        st = a.get("status") or "open"
        actions_by_status[st] = actions_by_status.get(st, 0) + 1

    with db.connect() as conn:
        msg_rows = conn.execute("SELECT status, COUNT(*) n FROM messages GROUP BY status").fetchall()
        messages_by_status = {r["status"]: r["n"] for r in msg_rows}
        total_messages = sum(messages_by_status.values())

    answer_rate = round((answered_count / len(calls) * 100), 1) if calls else 0.0

    return {
        "total_calls": len(calls),
        "telephony": {
            "answered": answered_count,
            "not_answered": not_answered_count,
            "answer_rate_pct": answer_rate,
        },
        "tiers": tier_counts,
        "unclassified_calls": unclassified_count,
        "total_actions": len(actions),
        "actions_by_status": actions_by_status,
        "total_messages": total_messages,
        "messages_by_status": messages_by_status,
        "tier_actions": orchestrator.TIER_ACTIONS,
    }


@app.post("/api/simulate/{tier}")
def simulate_tier(tier: str):
    tier_key = tier.lower()
    if tier_key not in SCENARIOS:
        raise HTTPException(status_code=400, detail=f"Invalid tier: {tier}. Choose from: {list(SCENARIOS.keys())}")

    sc = SCENARIOS[tier_key]
    call_id = f"demo-{tier_key}-{uuid.uuid4().hex[:6]}"

    extracted_data = {}
    if sc["bolna_outcome"]:
        extracted_data = {
            "Call Outcome & Scrap Info": {
                "Call Outcome": {
                    "subjective": sc["bolna_outcome"],
                    "confidence": sc["bolna_confidence"],
                }
            }
        }

    status = "no-answer" if tier_key == "no-answer" else "completed"
    payload = {
        "id": call_id,
        "agent_id": "crapbin-retention-agent",
        "status": status,
        "transcript": sc["transcript"],
        "extracted_data": extracted_data,
        "context_details": {
            "recipient_phone_number": sc["phone"],
            "recipient_data": {
                "name": sc["name"],
                "email": f"{sc['name'].lower().replace(' ', '.')}@example.com",
                "last_pickup_date": "2024-01-15",
            },
        },
        "telephony_data": {
            "to_number": sc["phone"],
            "duration": str(sc["duration"]),
            "recording_url": f"https://recordings.crapbin.local/{call_id}.mp3",
        },
    }

    record = from_bolna_payload(payload)
    db.upsert_call(record, payload)
    c = record.get("customer") or {}
    db.upsert_customer(
        record["phone"],
        name=c.get("name"),
        email=c.get("email"),
        last_pickup_date=c.get("last_pickup_date"),
        address_locality=c.get("address_locality"),
        pickup_history=c.get("pickup_history"),
    )

    # Process immediately through Orchestrator
    followup.process_call(call_id, force=True)
    followup.send_queued()

    updated_call = db.get_call(call_id)
    return {
        "status": "success",
        "call_id": call_id,
        "classification": updated_call["orchestrator_classification"] if updated_call else None,
        "next_best_action": updated_call["next_best_action"] if updated_call else None,
        "reasoning": updated_call["classification_reasoning"] if updated_call else None,
    }


@app.post("/api/reset-demo")
def reset_demo():
    """Resets database state back to the original 11 baseline calls."""
    with db.connect() as conn:
        marks = ",".join("?" * len(BASELINE_CALL_IDS))
        conn.execute(f"DELETE FROM calls WHERE call_id NOT IN ({marks})", BASELINE_CALL_IDS)
        conn.execute(f"DELETE FROM orchestrator_actions WHERE call_id NOT IN ({marks})", BASELINE_CALL_IDS)
        conn.execute(f"DELETE FROM messages WHERE call_id NOT IN ({marks})", BASELINE_CALL_IDS)
    followup.backfill()
    return {
        "status": "success",
        "message": f"Successfully reset database to {len(BASELINE_CALL_IDS)} baseline calls.",
    }


if __name__ == "__main__":
    import uvicorn
    print("\n" + "=" * 60)
    print(f" Crapbin Dashboard URL : http://localhost:{config.PORT}")
    print(f" Swagger API Docs      : http://localhost:{config.PORT}/docs")
    print("=" * 60 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=config.PORT)
