import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import config

TABLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    phone            TEXT PRIMARY KEY,
    name             TEXT,
    email            TEXT,
    address_locality TEXT,
    pickup_history   TEXT,
    do_not_call      INTEGER DEFAULT 0,
    last_pickup_date TEXT,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS calls (
    call_id          TEXT PRIMARY KEY,
    agent_id         TEXT,
    phone            TEXT,
    status           TEXT,
    duration_sec     REAL,
    created_at       TEXT,
    recording_url    TEXT,
    transcript       TEXT,
    summary          TEXT,
    hangup_by        TEXT,
    hangup_reason    TEXT,
    bolna_outcome    TEXT,
    bolna_confidence REAL,
    call_json        TEXT,   -- normalized record
    raw_json         TEXT,   -- payload exactly as Bolna sent it
    segment          TEXT,   -- interested / not_reached / callback / not_interested / needs_review
    insights_json    TEXT,   -- LLM output
    orchestrator_classification TEXT, -- 'Hot Lead', 'Warm Lead', 'Cold Lead', 'Do Not Call'
    classification_reasoning   TEXT,
    next_best_action           TEXT, -- 'Create pickup request for coordinator', 'Schedule follow-up call', 'Add to future nurture list', 'Mark as Do Not Call'
    followup_status            TEXT, -- 'pending', 'action_queued', 'completed', 'opted_out'
    processed_at     TEXT,
    process_error    TEXT,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id           TEXT NOT NULL,
    phone             TEXT,
    channel           TEXT NOT NULL,   -- whatsapp / email / coordinator_email
    recipient         TEXT,
    segment           TEXT,
    payload_json      TEXT,            -- template params or subject/body
    status            TEXT NOT NULL,   -- queued / sent / dry_run / failed / skipped
    provider_response TEXT,
    created_at        TEXT,
    sent_at           TEXT,
    UNIQUE (call_id, channel)
);

CREATE TABLE IF NOT EXISTS orchestrator_actions (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id                    TEXT NOT NULL UNIQUE,
    phone                      TEXT,
    customer_name              TEXT,
    orchestrator_classification TEXT NOT NULL,
    next_best_action           TEXT NOT NULL,
    classification_reasoning   TEXT,
    scrap_summary              TEXT,
    locality                   TEXT,
    availability               TEXT,
    status                     TEXT NOT NULL DEFAULT 'open',
    created_at                 TEXT,
    updated_at                 TEXT
);
"""

INDEXES_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_calls_segment ON calls(segment);
CREATE INDEX IF NOT EXISTS idx_calls_orchestrator ON calls(orchestrator_classification);
CREATE INDEX IF NOT EXISTS idx_messages_phone ON messages(phone, status);
CREATE INDEX IF NOT EXISTS idx_actions_classification ON orchestrator_actions(orchestrator_classification);
CREATE INDEX IF NOT EXISTS idx_actions_status ON orchestrator_actions(status);
"""

SCHEMA = TABLES_SCHEMA + INDEXES_SCHEMA

CALL_COLUMNS = [
    "agent_id", "phone", "status", "duration_sec", "created_at", "recording_url",
    "transcript", "summary", "hangup_by", "hangup_reason", "bolna_outcome", "bolna_confidence",
]


def now():
    # Same format as SQLite's datetime('now') so the two compare correctly
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(conn):
    """Idempotently add new columns to existing SQLite tables without losing data."""
    # Check calls columns
    call_cols = {col["name"] for col in conn.execute("PRAGMA table_info(calls)").fetchall()}
    if "orchestrator_classification" not in call_cols:
        conn.execute("ALTER TABLE calls ADD COLUMN orchestrator_classification TEXT")
    if "classification_reasoning" not in call_cols:
        conn.execute("ALTER TABLE calls ADD COLUMN classification_reasoning TEXT")
    if "next_best_action" not in call_cols:
        conn.execute("ALTER TABLE calls ADD COLUMN next_best_action TEXT")
    if "followup_status" not in call_cols:
        conn.execute("ALTER TABLE calls ADD COLUMN followup_status TEXT")

    # Check customers columns
    cust_cols = {col["name"] for col in conn.execute("PRAGMA table_info(customers)").fetchall()}
    if "address_locality" not in cust_cols:
        conn.execute("ALTER TABLE customers ADD COLUMN address_locality TEXT")
    if "pickup_history" not in cust_cols:
        conn.execute("ALTER TABLE customers ADD COLUMN pickup_history TEXT")
    if "do_not_call" not in cust_cols:
        conn.execute("ALTER TABLE customers ADD COLUMN do_not_call INTEGER DEFAULT 0")


def init_db():
    with connect() as conn:
        conn.executescript(TABLES_SCHEMA)
        _migrate(conn)
        conn.executescript(INDEXES_SCHEMA)


def upsert_customer(phone, name=None, email=None, last_pickup_date=None, address_locality=None, pickup_history=None, do_not_call=None):
    if not phone:
        return
    with connect() as conn:
        conn.execute(
            """INSERT INTO customers (phone, name, email, last_pickup_date, address_locality, pickup_history, do_not_call, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, 0), ?)
               ON CONFLICT(phone) DO UPDATE SET
                 name = COALESCE(excluded.name, name),
                 email = COALESCE(excluded.email, email),
                 last_pickup_date = COALESCE(excluded.last_pickup_date, last_pickup_date),
                 address_locality = COALESCE(excluded.address_locality, address_locality),
                 pickup_history = COALESCE(excluded.pickup_history, pickup_history),
                 do_not_call = CASE WHEN excluded.do_not_call != 0 THEN excluded.do_not_call ELSE do_not_call END,
                 updated_at = excluded.updated_at""",
            (phone, name, email, last_pickup_date, address_locality, pickup_history, do_not_call, now()),
        )


def mark_customer_dnc(phone, do_not_call=1):
    if not phone:
        return
    with connect() as conn:
        conn.execute(
            """UPDATE customers SET do_not_call = ?, updated_at = ? WHERE phone = ?""",
            (do_not_call, now(), phone),
        )


def upsert_call(record, raw):
    """Insert or refresh a call. Bolna fires one webhook per status change, so this runs
    several times per call; it never touches the segment/processing columns."""
    values = [record.get(c) for c in CALL_COLUMNS]
    updates = ", ".join(f"{c} = COALESCE(excluded.{c}, {c})" for c in CALL_COLUMNS)
    with connect() as conn:
        conn.execute(
            f"""INSERT INTO calls (call_id, {", ".join(CALL_COLUMNS)}, call_json, raw_json, updated_at)
                VALUES (?, {", ".join("?" * len(CALL_COLUMNS))}, ?, ?, ?)
                ON CONFLICT(call_id) DO UPDATE SET {updates},
                  call_json = excluded.call_json, raw_json = excluded.raw_json,
                  updated_at = excluded.updated_at""",
            [record["call_id"], *values,
             json.dumps(record, ensure_ascii=False), json.dumps(raw, ensure_ascii=False), now()],
        )


def get_call(call_id):
    with connect() as conn:
        return conn.execute("SELECT * FROM calls WHERE call_id = ?", (call_id,)).fetchone()


def get_customer(phone):
    if not phone:
        return None
    with connect() as conn:
        return conn.execute("SELECT * FROM customers WHERE phone = ?", (phone,)).fetchone()


def unprocessed_call_ids(statuses):
    marks = ",".join("?" * len(statuses))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT call_id FROM calls WHERE processed_at IS NULL AND status IN ({marks})",
            list(statuses),
        ).fetchall()
    return [r["call_id"] for r in rows]


def save_processing(call_id, segment, insights, error=None):
    with connect() as conn:
        conn.execute(
            """UPDATE calls SET segment = ?, insights_json = ?, process_error = ?,
                 processed_at = CASE WHEN ? IS NULL THEN ? ELSE NULL END,
                 updated_at = ?
               WHERE call_id = ?""",
            (segment, json.dumps(insights, ensure_ascii=False) if insights else None,
             error, error, now(), now(), call_id),
        )


def save_orchestrator_decision(
    call_id,
    segment,
    insights,
    orchestrator_classification,
    classification_reasoning,
    next_best_action,
    followup_status,
    error=None,
):
    with connect() as conn:
        conn.execute(
            """UPDATE calls SET
                 segment = ?,
                 insights_json = ?,
                 orchestrator_classification = ?,
                 classification_reasoning = ?,
                 next_best_action = ?,
                 followup_status = ?,
                 process_error = ?,
                 processed_at = CASE WHEN ? IS NULL THEN ? ELSE NULL END,
                 updated_at = ?
               WHERE call_id = ?""",
            (
                segment,
                json.dumps(insights, ensure_ascii=False) if insights else None,
                orchestrator_classification,
                classification_reasoning,
                next_best_action,
                followup_status,
                error,
                error,
                now(),
                now(),
                call_id,
            ),
        )


def upsert_orchestrator_action(
    call_id,
    phone,
    customer_name,
    orchestrator_classification,
    next_best_action,
    classification_reasoning,
    scrap_summary,
    locality,
    availability,
    status="open",
):
    with connect() as conn:
        conn.execute(
            """INSERT INTO orchestrator_actions
                 (call_id, phone, customer_name, orchestrator_classification, next_best_action,
                  classification_reasoning, scrap_summary, locality, availability, status,
                  created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(call_id) DO UPDATE SET
                 phone = COALESCE(excluded.phone, phone),
                 customer_name = COALESCE(excluded.customer_name, customer_name),
                 orchestrator_classification = excluded.orchestrator_classification,
                 next_best_action = excluded.next_best_action,
                 classification_reasoning = excluded.classification_reasoning,
                 scrap_summary = excluded.scrap_summary,
                 locality = excluded.locality,
                 availability = excluded.availability,
                 status = excluded.status,
                 updated_at = excluded.updated_at""",
            (
                call_id,
                phone,
                customer_name,
                orchestrator_classification,
                next_best_action,
                classification_reasoning,
                scrap_summary,
                locality,
                availability,
                status,
                now(),
                now(),
            ),
        )


def all_calls():
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM calls ORDER BY created_at DESC, rowid DESC").fetchall()]


def all_orchestrator_actions(classification=None):
    with connect() as conn:
        if classification:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM orchestrator_actions WHERE orchestrator_classification = ? ORDER BY id DESC",
                (classification,),
            ).fetchall()]
        return [dict(r) for r in conn.execute(
            "SELECT * FROM orchestrator_actions ORDER BY id DESC"
        ).fetchall()]


def all_messages():
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM messages ORDER BY id DESC"
        ).fetchall()]


def queue_message(call_id, phone, channel, recipient, segment, payload):
    """Returns False if this call already has a message on this channel."""
    with connect() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO messages
                 (call_id, phone, channel, recipient, segment, payload_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)""",
            (call_id, phone, channel, recipient, segment,
             json.dumps(payload, ensure_ascii=False), now()),
        )
        return cur.rowcount == 1


def recent_message_to(phone, days, exclude_call_id):
    if not phone:
        return False
    with connect() as conn:
        row = conn.execute(
            """SELECT 1 FROM messages
               WHERE phone = ? AND call_id != ? AND status IN ('sent', 'dry_run', 'queued')
                 AND created_at >= datetime('now', ?)
               LIMIT 1""",
            (phone, exclude_call_id, f"-{days} days"),
        ).fetchone()
    return row is not None


def queued_messages():
    with connect() as conn:
        return conn.execute("SELECT * FROM messages WHERE status = 'queued' ORDER BY id").fetchall()


def claim_message(message_id):
    """Atomically move queued -> sending, so two workers never send the same message."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE messages SET status = 'sending' WHERE id = ? AND status = 'queued'", (message_id,))
        return cur.rowcount == 1


def mark_message(message_id, status, response):
    with connect() as conn:
        conn.execute(
            "UPDATE messages SET status = ?, provider_response = ?, sent_at = ? WHERE id = ?",
            (status, json.dumps(response, ensure_ascii=False, default=str), now(), message_id),
        )
