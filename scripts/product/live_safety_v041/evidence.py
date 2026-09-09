"""Read-only evidence extraction; all counts come from retained databases/audit."""

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any
from scripts.product.minimal_payment_acceptance_v040.owned import save, digest

TABLES = {
    "candidate": "remediation_candidates",
    "approval": "remediation_approvals",
    "authorization": "remediation_authorizations",
    "write_intent": "remediation_write_intents",
    "executor_dispatch": "remediation_executor_dispatches",
    "receipt": "remediation_step_receipts",
    "attempt": "remediation_attempts",
    "recovery_window": "remediation_recovery_windows",
    "recovery_evaluation": "remediation_recovery_evaluations",
}


def collect(root: Path, result: dict[str, Any]) -> None:
    path = root / "data/product.sqlite3"
    if not path.exists():
        result["evidence_status"] = "DATABASE_NOT_CREATED"
        return
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = {
            row[0]: [dict(v) for v in db.execute("SELECT * FROM " + row[0])]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'remediation_%'"
            ).fetchall()
        }
    save(root / "final-remediation-rows.json", rows)
    result["counts"] = {name: len(rows[table]) for name, table in TABLES.items()}
    ledger = root / "ledger/dispatch.sqlite3"
    if ledger.exists():
        with sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True) as db:
            consumed = db.execute(
                "SELECT intent_id, dispatch_sha256 FROM consumed"
            ).fetchall()
        result["gateway_evidence"] = {
            "kind": "PERSISTED_LEDGER",
            "consumptions": len(consumed),
            "rows_sha256": digest(consumed),
        }
    else:
        births = [
            json.loads(p.read_bytes())
            for p in (root / "births").glob("container-*.json")
        ]
        gateway = [
            r for r in births if r["Name"].endswith("-remediation-control-gateway")
        ]
        # S0 never starts gateway or executor; every object is created and inspected first.
        if result["case_id"] != "S0" or len(gateway) != 1:
            raise ValueError("GATEWAY_LEDGER_EVIDENCE_MISSING")
        result["gateway_evidence"] = {
            "kind": "NOT_STARTED_S0",
            "consumptions": 0,
            "birth_sha256": digest(gateway),
            "birth_running": gateway[0]["State"]["Running"],
        }
    result["counts"]["gateway_consumption"] = result["gateway_evidence"]["consumptions"]
    audit = root / "control/write-audit.jsonl"
    events = (
        [json.loads(line) for line in audit.read_text().splitlines()]
        if audit.exists()
        else []
    )
    applied = [e for e in events if e["stage"] == "APPLIED"]
    product = [e for e in applied if e["controller"] is None]
    births = [
        json.loads(p.read_bytes()) for p in (root / "births").glob("container-*.json")
    ]
    # Running network addresses are retained in final private inspect before cleanup.
    running = root / "running-gateway.json"
    peers = []
    if running.exists():
        peers = [
            n["IPAddress"]
            for n in json.loads(running.read_bytes())["NetworkSettings"][
                "Networks"
            ].values()
        ]
    if any(e["peer"] not in peers or e["document"] != "baseline" for e in product):
        raise ValueError("EXTERNAL_WRITE_ATTRIBUTION_UNKNOWN")
    result["counts"]["external_product_writes"] = len(product)
    result["external_write_audit"] = [
        {k: v for k, v in e.items() if k != "peer"} for e in events
    ]
    result["external_write_audit_sha256"] = digest(events)
    result["objects"] = {
        name: [json.loads(row["payload_json"]) for row in rows[table]]
        for name, table in TABLES.items()
    }
    result["decision_trace"] = rows.get("remediation_decision_trace_events", [])
    result["private_evidence_commitment"] = digest(
        {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*"))
            if p.is_file() and p.suffix in (".json", ".jsonl")
        }
    )
    result["state_snapshots"] = [
        json.loads(r["payload_json"])
        for r in rows.get("remediation_current_state_snapshots", [])
    ]
    expected_writes = 1 if result["case_id"] in ("S3", "S4") else 0
    for field in (
        "authorization",
        "write_intent",
        "executor_dispatch",
        "receipt",
        "gateway_consumption",
        "external_product_writes",
    ):
        if result["counts"][field] != expected_writes:
            raise ValueError("CASE_CARDINALITY_MISMATCH:" + field)
    if result["case_id"] == "S0" and (
        result["counts"]["candidate"] or result["counts"]["approval"]
    ):
        raise ValueError("HEALTHY_AUTHORITY_UNEXPECTED")
    result["evidence_status"] = "COLLECTED"
