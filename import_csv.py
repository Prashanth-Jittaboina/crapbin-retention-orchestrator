"""Backfill calls from a Bolna CSV export:  python import_csv.py "export.csv" [--process]"""
import argparse
import csv

import config
import db
import followup
from normalize import from_csv_row

parser = argparse.ArgumentParser()
parser.add_argument("csv_path")
parser.add_argument("--process", action="store_true", help="run the LLM + follow-up step after importing")
args = parser.parse_args()

db.init_db()
with open(args.csv_path, encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))

for row in rows:
    record = from_csv_row(row)
    if record["call_id"]:
        db.upsert_call(record, row)
        c = record.get("customer") or {}
        db.upsert_customer(
            phone=record["phone"],
            name=c.get("name"),
            email=c.get("email"),
            last_pickup_date=c.get("last_pickup_date"),
            address_locality=c.get("address_locality"),
            pickup_history=c.get("pickup_history"),
        )
print(f"Imported {len(rows)} calls into {config.DB_PATH}")

if args.process:
    followup.run()
