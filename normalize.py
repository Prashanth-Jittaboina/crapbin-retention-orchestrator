"""Turn a Bolna webhook payload or a Bolna CSV export row into one canonical call record."""
import re

# Statuses after which Bolna sends no more updates for the call
FINAL_STATUSES = {
    "completed", "call-disconnected", "no-answer", "busy", "failed",
    "canceled", "cancelled", "stopped", "error", "balance-low",
}
# Final statuses where the customer never picked up
NOT_REACHED_STATUSES = {"no-answer", "busy", "failed"}


def _flatten(d, prefix=""):
    """{"a": {"b": 1}} -> {"a_b": 1}, matching the column names in Bolna's CSV export."""
    out = {}
    for k, v in (d or {}).items():
        key = f"{prefix}_{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


def _find(flat, suffix):
    """Look up an extraction field by suffix, so renaming the extraction group in Bolna
    ("Call Outcome & Scrap Info") doesn't break us."""
    for k, v in flat.items():
        if k.endswith(suffix) and v not in (None, ""):
            return v
    return None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalize_phone(phone):
    """Return digits with country code (91XXXXXXXXXX), or None."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if len(digits) == 10:
        digits = "91" + digits
    elif len(digits) == 11 and digits.startswith("0"):
        digits = "91" + digits[1:]
    return digits or None


def _outcome(flat):
    outcome = _find(flat, "Call Outcome_subjective") or _find(flat, "Call Outcome_objective")
    return (
        str(outcome).strip().upper() if outcome else None,
        _float(_find(flat, "Call Outcome_confidence")),
    )


def from_bolna_payload(payload):
    tel = payload.get("telephony_data") or {}
    ctx = payload.get("context_details") or {}
    recipient = ctx.get("recipient_data") or {}
    flat = _flatten(payload.get("extracted_data") or {})
    outcome, confidence = _outcome(flat)

    return {
        "call_id": payload.get("id") or payload.get("execution_id") or payload.get("call_id"),
        "agent_id": payload.get("agent_id"),
        "status": (payload.get("status") or "").lower() or None,
        "phone": normalize_phone(
            tel.get("to_number") or ctx.get("recipient_phone_number") or payload.get("user_number")
        ),
        "duration_sec": _float(tel.get("duration") or payload.get("conversation_time")),
        "created_at": payload.get("created_at"),
        "recording_url": tel.get("recording_url"),
        "transcript": payload.get("transcript") or None,
        "summary": payload.get("summary") or None,
        "hangup_by": tel.get("hangup_by"),
        "hangup_reason": tel.get("hangup_reason"),
        "bolna_outcome": outcome,
        "bolna_confidence": confidence,
        # Inactive customer details from CRM / batch CSV
        "customer": {
            "name": recipient.get("name") or recipient.get("customer_name"),
            "email": recipient.get("email"),
            "last_pickup_date": recipient.get("last_pickup_date"),
            "address_locality": recipient.get("address_locality") or recipient.get("locality"),
            "pickup_history": recipient.get("pickup_history"),
        },
    }


def from_csv_row(row):
    flat = {k.removeprefix("extracted_data_"): v for k, v in row.items() if k.startswith("extracted_data_")}
    outcome, confidence = _outcome(flat)

    return {
        "call_id": row.get("id"),
        "agent_id": row.get("agent_id") or None,
        "status": (row.get("status") or "").lower() or None,
        "phone": normalize_phone(row.get("user_number")),
        "duration_sec": _float(row.get("duration")),
        "created_at": row.get("created_at") or None,
        "recording_url": row.get("recording_url") or None,
        "transcript": row.get("transcript") or None,
        "summary": row.get("summary") or None,
        "hangup_by": row.get("hangup_by") or None,
        "hangup_reason": row.get("hangup_reason") or None,
        "bolna_outcome": outcome,
        "bolna_confidence": confidence,
        "customer": {
            "name": row.get("name") or row.get("customer_name") or row.get("recipient_name") or None,
            "email": row.get("email") or row.get("recipient_email") or None,
            "last_pickup_date": row.get("last_pickup_date") or None,
            "address_locality": row.get("address_locality") or row.get("locality") or None,
            "pickup_history": row.get("pickup_history") or None,
        },
    }


def customer_spoke(transcript):
    """True if the transcript has at least one non-empty customer turn."""
    for line in (transcript or "").splitlines():
        if line.lower().startswith("user:") and line[5:].strip():
            return True
    return False
