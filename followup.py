"""Segment finished calls, classify with Orchestrator Agent, and send follow-ups.

    python followup.py               # process unprocessed calls, backfill missing, send queued messages
    python followup.py --backfill    # immediately backfill Orchestrator columns & actions on all calls
    python followup.py --reprocess   # re-run LLM extraction + Orchestrator classification on all calls
    python followup.py --report      # print Buildathon-ready report
"""
import argparse
import json
from datetime import datetime, timedelta, timezone

import config
import db
import notifier
import orchestrator
from normalize import FINAL_STATUSES, NOT_REACHED_STATUSES

IST = timezone(timedelta(hours=5, minutes=30))


def lead_summary(call, insights, name, email):
    call_dict = dict(call)
    materials = ", ".join(insights.get("scrap_materials") or []) or "-"
    qty = insights.get("approx_quantity") or "-"
    loc = insights.get("locality") or "-"
    avail = insights.get("availability") or insights.get("customer_mentioned_date") or "-"
    classification = call_dict.get("orchestrator_classification") or insights.get("orchestrator_classification") or "-"
    action = call_dict.get("next_best_action") or insights.get("next_best_action") or "-"
    reasoning = call_dict.get("classification_reasoning") or insights.get("reason") or "-"

    return "\n".join([
        f"Customer:       {name}",
        f"Phone:          {call_dict.get('phone') or '-'}",
        f"Email:          {email or '-'}",
        f"Classification: {classification}",
        f"Next Action:    {action}",
        f"Reasoning:      {reasoning}",
        f"Materials:      {materials}",
        f"Quantity:       {qty}",
        f"Locality:       {loc}",
        f"Availability:   {avail}",
        f"Language:       {insights.get('language') or '-'}",
        "",
        f"Summary:        {insights.get('summary') or '-'}",
        f"Recording:      {call_dict.get('recording_url') or '-'}",
        f"Call ID:        {call_dict.get('call_id')}",
    ])


def queue_followups(call, segment, insights, decision=None):
    call_dict = dict(call)
    phone = call_dict.get("phone")
    customer = db.get_customer(phone)
    cust_dict = dict(customer) if customer else {}
    real_name = (insights and insights.get("customer_name")) or cust_dict.get("name")
    name = real_name or "there"
    email = cust_dict.get("email")
    call_id = call_dict["call_id"]

    classification = (decision and decision.get("orchestrator_classification")) or call_dict.get("orchestrator_classification")
    if classification == "Do Not Call" or cust_dict.get("do_not_call") == 1:
        # Never queue customer-facing messages for opted-out / DNC customers
        return

    coordinator_email = config.COORDINATOR_EMAIL or ("coordinator@crapbin.local" if config.DRY_RUN else None)

    if segment == "interested" or classification == "Hot Lead":
        materials = ", ".join(insights.get("scrap_materials") or []) or "scrap items"
        locality = insights.get("locality") or "your area"
        if phone:
            db.queue_message(call_id, phone, "whatsapp", phone, segment, {
                "template": config.WA_TEMPLATE_INTERESTED, "params": [name, materials, locality]})
        if email:
            body = insights.get("follow_up_email") or (
                f"Hi {name},\n\nThank you for speaking with Crapbin today. We've noted your {materials} "
                f"for pickup. Our coordinator will call you shortly to confirm the details and rates.\n\n"
                f"Team Crapbin")
            db.queue_message(call_id, phone, "email", email, segment, {
                "subject": "Thanks for choosing Crapbin - your scrap pickup request", "body": body})
        if coordinator_email:
            db.queue_message(call_id, phone, "coordinator_email", coordinator_email, segment, {
                "subject": f"New pickup lead: {real_name or phone or call_id[:8]} ({locality})",
                "body": lead_summary(call_dict, insights, real_name or "-", email)})

    elif segment == "needs_review":
        if coordinator_email:
            db.queue_message(call_id, phone, "coordinator_email", coordinator_email, segment, {
                "subject": f"[Review] Possible lead: {real_name or phone or call_id[:8]}",
                "body": lead_summary(call_dict, insights, real_name or "-", email)})

    elif segment == "not_reached":
        if phone and db.recent_message_to(phone, config.REENGAGE_COOLDOWN_DAYS, call_id):
            print(f"[{call_id}] {phone} messaged in the last {config.REENGAGE_COOLDOWN_DAYS} days, skipping")
            return
        contact = config.CRAPBIN_CONTACT_PHONE or "crapbin.com"
        if phone:
            db.queue_message(call_id, phone, "whatsapp", phone, segment, {
                "template": config.WA_TEMPLATE_REENGAGE, "params": [name, contact]})
        if email:
            db.queue_message(call_id, phone, "email", email, segment, {
                "subject": "Have scrap to sell? Crapbin will pick it up",
                "body": (f"Hi {name},\n\nWe tried calling you today. You've used Crapbin's doorstep scrap "
                         f"pickup before - do you have any scrap now? Reply to this email or contact us at "
                         f"{contact} to schedule a pickup. We weigh digitally and pay instantly.\n\n"
                         f"Team Crapbin")})


def in_send_window():
    return config.SEND_WINDOW_START <= datetime.now(IST).hour < config.SEND_WINDOW_END


def send_queued():
    customer_ok = in_send_window()
    for m in db.queued_messages():
        if m["channel"] != "coordinator_email" and not customer_ok:
            continue   # stays queued until the next run inside the window
        if not db.claim_message(m["id"]):
            continue   # another worker got it first
        if m["channel"] == "whatsapp" and not config.DRY_RUN and not config.WHATSAPP_TOKEN:
            db.mark_message(m["id"], "skipped", {"reason": "WhatsApp not configured"})
            continue
        payload = json.loads(m["payload_json"])
        try:
            if m["channel"] == "whatsapp":
                resp = notifier.send_whatsapp_template(m["recipient"], payload["template"], payload["params"])
            else:
                resp = notifier.send_email(m["recipient"], payload["subject"], payload["body"])
            db.mark_message(m["id"], "dry_run" if config.DRY_RUN else "sent", resp)
        except Exception as e:
            db.mark_message(m["id"], "failed", {"error": f"{type(e).__name__}: {e}"})
            print(f"message {m['id']} ({m['channel']}) failed: {e}")


def process_call(call_id, force=False, reanalyze_llm=False):
    call = db.get_call(call_id)
    if not call or call["status"] not in FINAL_STATUSES:
        return
    if call["processed_at"] and not force and not reanalyze_llm:
        return

    res = orchestrator.orchestrate_call(call_id, force=force, reanalyze_llm=reanalyze_llm)
    if not res:
        return

    # Refresh call row after orchestration
    call = db.get_call(call_id)
    queue_followups(call, res["decision"]["segment"], res["insights"] or {}, res["decision"])
    print(f"[{call_id[:8]}] {call['status']} -> {res['decision']['orchestrator_classification']} ({res['decision']['next_best_action']})")


def run():
    db.init_db()
    for call_id in db.unprocessed_call_ids(FINAL_STATUSES):
        process_call(call_id)
    # Also process any final call that has not yet been classified by the orchestrator
    with db.connect() as conn:
        marks = ",".join("?" * len(FINAL_STATUSES))
        missing = conn.execute(
            f"SELECT call_id FROM calls WHERE orchestrator_classification IS NULL AND status IN ({marks})",
            list(FINAL_STATUSES),
        ).fetchall()
    for r in missing:
        process_call(r["call_id"], force=True)
    send_queued()


def backfill():
    db.init_db()
    with db.connect() as conn:
        rows = conn.execute("SELECT call_id FROM calls").fetchall()
    print(f"Backfilling Orchestrator classification & actions for {len(rows)} calls...")
    count = 0
    for r in rows:
        process_call(r["call_id"], force=True, reanalyze_llm=False)
        count += 1
    send_queued()
    print(f"Successfully backfilled {count} calls and queued actions.")


def reprocess():
    db.init_db()
    with db.connect() as conn:
        marks = ",".join("?" * len(FINAL_STATUSES))
        rows = conn.execute(
            f"SELECT call_id FROM calls WHERE status IN ({marks})",
            list(FINAL_STATUSES),
        ).fetchall()
    print(f"Reprocessing {len(rows)} calls with full LLM extraction & Orchestrator classification...")
    for r in rows:
        process_call(r["call_id"], force=True, reanalyze_llm=True)
    send_queued()
    print("Reprocessing complete.")


def report():
    db.init_db()
    with db.connect() as conn:
        calls = [dict(r) for r in conn.execute("SELECT * FROM calls ORDER BY rowid ASC").fetchall()]
        actions = [dict(r) for r in conn.execute("SELECT * FROM orchestrator_actions ORDER BY id ASC").fetchall()]
        messages = [dict(r) for r in conn.execute("SELECT * FROM messages ORDER BY id ASC").fetchall()]

    print("=" * 88)
    print("           CRAPBIN CUSTOMER RETENTION & ORCHESTRATOR AGENT REPORT")
    print("=" * 88)

    # 1. Classification Summary
    counts = {"Hot Lead": 0, "Warm Lead": 0, "Cold Lead": 0, "Do Not Call": 0, "Unclassified": 0}
    for c in calls:
        cls = c.get("orchestrator_classification")
        if cls in counts:
            counts[cls] += 1
        else:
            counts["Unclassified"] += 1

    print("\n[STEP 5 & 6] ORCHESTRATOR CLASSIFICATION SUMMARY")
    print("-" * 88)
    for tier, action in orchestrator.TIER_ACTIONS.items():
        print(f"  {tier:<12} : {counts.get(tier, 0):>2} leads  ->  Next-Best Action: {action}")
    if counts["Unclassified"]:
        print(f"  {'Unclassified':<12} : {counts['Unclassified']:>2} leads")
    print(f"  Total Calls  : {len(calls):>2}")

    # 2. Detailed Lead Breakdown Table
    print("\n[LEAD BREAKDOWN TABLE]")
    print("-" * 115)
    header = (
        f"{'Call ID':<9} | {'Phone / Customer':<20} | {'Lang':<7} | "
        f"{'Scrap Details':<22} | {'Locality':<15} | {'Classification':<12} | {'Next-Best Action'}"
    )
    print(header)
    print("-" * 115)
    for c in calls:
        insights = {}
        if c.get("insights_json"):
            try:
                insights = json.loads(c["insights_json"])
            except Exception:
                pass

        cid = c["call_id"][:8]
        phone = c.get("phone") or "(web-call)"
        cust_name = insights.get("customer_name")
        phone_cust = f"{phone} / {cust_name}" if cust_name else phone
        if len(phone_cust) > 20:
            phone_cust = phone_cust[:17] + "..."

        lang = insights.get("language") or "-"
        materials = ", ".join(insights.get("scrap_materials") or [])
        qty = insights.get("approx_quantity")
        scrap = f"{materials} ({qty})" if materials and qty else (materials or qty or "-")
        if len(scrap) > 22:
            scrap = scrap[:19] + "..."

        locality = insights.get("locality") or "-"
        if len(locality) > 15:
            locality = locality[:12] + "..."

        cls = c.get("orchestrator_classification") or "(none)"
        action = c.get("next_best_action") or "-"

        print(f"{cid:<9} | {phone_cust:<20} | {lang:<7} | {scrap:<22} | {locality:<15} | {cls:<12} | {action}")

    # 3. Action Queue (orchestrator_actions)
    print("\n[ORCHESTRATOR ACTIONS QUEUE] (Dedicated Audit Table)")
    print("-" * 105)
    print(f"{'Action ID':<10} | {'Call ID':<9} | {'Phone':<14} | {'Classification':<12} | {'Action':<35} | {'Status'}")
    print("-" * 105)
    for a in actions:
        aid = f"#{a['id']}"
        cid = a["call_id"][:8]
        phone = a.get("phone") or "(web-call)"
        cls = a["orchestrator_classification"]
        action = a["next_best_action"]
        status = a["status"]
        print(f"{aid:<10} | {cid:<9} | {phone:<14} | {cls:<12} | {action:<35} | {status}")

    # 4. Messages Queue Summary
    print("\n[MESSAGES QUEUE & DISPATCH SUMMARY]")
    print("-" * 50)
    with db.connect() as conn:
        msg_rows = conn.execute("SELECT channel, status, COUNT(*) n FROM messages GROUP BY channel, status").fetchall()
    if msg_rows:
        for r in msg_rows:
            print(f"  {r['channel']:<20} {r['status']:<10} {r['n']}")
    else:
        print("  (No messages queued)")
    print("=" * 88)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true", help="display orchestrator report")
    parser.add_argument("--backfill", action="store_true", help="backfill existing calls into orchestrator")
    parser.add_argument("--reprocess", action="store_true", help="re-run LLM + Orchestrator on all final calls")
    args = parser.parse_args()

    if args.report:
        report()
    elif args.backfill:
        backfill()
    elif args.reprocess:
        reprocess()
    else:
        run()
