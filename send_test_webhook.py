"""Pretend to be Bolna: post simulated call events across all 4 reference architecture tiers.

Usage:
    python send_test_webhook.py               # default: --hot
    python send_test_webhook.py --hot         # Hot Lead -> Create pickup request for coordinator
    python send_test_webhook.py --warm        # Warm Lead -> Schedule follow-up call
    python send_test_webhook.py --cold        # Cold Lead -> Add to future nurture list
    python send_test_webhook.py --dnc         # Do Not Call -> Mark as Do Not Call
    python send_test_webhook.py --no-answer   # No Answer -> Warm Lead (Schedule follow-up call)
    python send_test_webhook.py https://...   # Target a remote URL or tunnel
"""
import argparse
import sys
import time
import uuid

import httpx

import config

SCENARIOS = {
    "hot": {
        "tier": "Hot Lead",
        "action": "Create pickup request for coordinator",
        "phone": "+919876543210",
        "name": "Suresh Kumar",
        "bolna_outcome": "INTERESTED_NOW",
        "bolna_confidence": 0.95,
        "duration": 85,
        "transcript": (
            "assistant: Namaste Suresh garu! I'm Priya calling from Crapbin doorstep scrap pickup service.\n"
            "user: Ha Priya garu, cheppandi.\n"
            "assistant: We noticed you used Crapbin scrap pickup earlier. Do you have any scrap items ready for pickup this week?\n"
            "user: Yes, we have some old newspapers, around 15 kgs, and one iron almirah we want to dispose of.\n"
            "assistant: Wonderful! Can you tell us your locality and when you would be available for pickup?\n"
            "user: We are in Kukatpally, KPHB Phase 1. Tomorrow morning between 10 AM and 12 PM works best for us.\n"
            "assistant: Perfect! Our coordinator will contact you to confirm the pickup time and digital weighment. Thank you!\n"
        ),
    },
    "warm": {
        "tier": "Warm Lead",
        "action": "Schedule follow-up call",
        "phone": "+919876543211",
        "name": "Anita Sharma",
        "bolna_outcome": "CALLBACK_REQUESTED",
        "bolna_confidence": 0.85,
        "duration": 55,
        "transcript": (
            "assistant: Hello Anita ji, this is Priya calling from Crapbin customer support.\n"
            "user: Haan Priya, bolie.\n"
            "assistant: Do you have any scrap or recyclables at home that you'd like to get picked up?\n"
            "user: Yes, I do have some old cartons, books, and plastic containers in the storeroom, but I am at office right now. "
            "Can you call me back on Saturday evening after 5 PM? I need to check with my husband once.\n"
            "assistant: Absolutely Anita ji! We will schedule a callback on Saturday evening to confirm. Thank you!\n"
        ),
    },
    "cold": {
        "tier": "Cold Lead",
        "action": "Add to future nurture list",
        "phone": "+919876543212",
        "name": "Venkat Rao",
        "bolna_outcome": "NOT_INTERESTED",
        "bolna_confidence": 0.90,
        "duration": 40,
        "transcript": (
            "assistant: Hello Mr. Venkat, I'm calling from Crapbin regarding doorstep scrap pickup.\n"
            "user: Hello. Actually we cleared all our scrap and old newspapers just last month during renovation. There is nothing right now at home.\n"
            "assistant: No problem at all sir. May we check back with you in a couple of months?\n"
            "user: Yeah, sure, maybe after 3 or 4 months you can check again. Nothing right now. Thank you.\n"
            "assistant: Thank you Mr. Venkat, have a great day!\n"
        ),
    },
    "dnc": {
        "tier": "Do Not Call",
        "action": "Mark as Do Not Call",
        "phone": "+919876543213",
        "name": "Rajesh Verma",
        "bolna_outcome": "NOT_INTERESTED",
        "bolna_confidence": 0.99,
        "duration": 25,
        "transcript": (
            "assistant: Hello Rajesh ji, I am Priya calling from Crapbin doorstep scrap pickup.\n"
            "user: Listen, please don't call this number again. I am not interested at all. Remove my number from your calling list right now.\n"
            "assistant: We are very sorry for the disturbance, sir. We will immediately remove your number from our calling list and ensure you are not contacted again. Have a good day.\n"
        ),
    },
    "no-answer": {
        "tier": "Warm Lead (Not Reached)",
        "action": "Schedule follow-up call",
        "phone": "+919876543214",
        "name": "Pooja Reddy",
        "bolna_outcome": None,
        "bolna_confidence": None,
        "duration": 0,
        "transcript": None,
    },
}


def main():
    parser = argparse.ArgumentParser(description="Send simulated Bolna webhook calls across 4 architecture tiers")
    parser.add_argument("url", nargs="?", default=f"http://localhost:{config.PORT}", help="Target API base URL")
    parser.add_argument("--hot", action="store_true", help="Hot Lead scenario")
    parser.add_argument("--warm", action="store_true", help="Warm Lead scenario")
    parser.add_argument("--cold", action="store_true", help="Cold Lead scenario")
    parser.add_argument("--dnc", action="store_true", help="Do Not Call scenario")
    parser.add_argument("--no-answer", action="store_true", help="No Answer scenario")
    args = parser.parse_args()

    # Determine scenario (default to hot)
    scenario_key = "hot"
    if args.warm:
        scenario_key = "warm"
    elif args.cold:
        scenario_key = "cold"
    elif args.dnc:
        scenario_key = "dnc"
    elif args.no_answer:
        scenario_key = "no-answer"

    sc = SCENARIOS[scenario_key]
    base = args.url.rstrip("/")
    url = f"{base}/webhook/bolna?token={config.WEBHOOK_TOKEN}"
    call_id = f"sim-{scenario_key}-{uuid.uuid4().hex[:6]}"

    print("=" * 70)
    print(f" SIMULATING WEBHOOK: {sc['tier'].upper()} SCENARIO")
    print(f" Target Action : {sc['action']}")
    print(f" Customer      : {sc['name']} ({sc['phone']})")
    print(f" Target URL    : {url}")
    print("=" * 70)

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

    payload = {
        "id": call_id,
        "agent_id": "crapbin-retention-agent",
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

    if scenario_key == "no-answer":
        statuses = ["queued", "ringing", "no-answer"]
    else:
        statuses = ["queued", "ringing", "in-progress", "completed"]

    headers = {"X-Webhook-Token": config.WEBHOOK_TOKEN}
    with httpx.Client(timeout=30) as client:
        for status in statuses:
            body = {**payload, "status": status}
            if status == "completed":
                body["transcript"] = sc["transcript"]
            r = client.post(url, json=body, headers=headers)
            print(f"  Event: {status:<12} -> HTTP {r.status_code} {r.text}")
            time.sleep(0.3)

    print("-" * 70)
    print(f"Sent simulated call {call_id}.")
    print("To inspect lead classification & audit logs:")
    print("  python followup.py --report")
    print(f"  curl {base}/summary")
    print(f"  curl {base}/actions")
    print("=" * 70)


if __name__ == "__main__":
    main()
