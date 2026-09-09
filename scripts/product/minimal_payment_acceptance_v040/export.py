"""Export verified public-safe objects from a completed private live attempt."""

from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any
from scripts.ci.verify_product_v040_minimal_payment import verify
from .owned import digest
from .plan import REPO

TABLES = {
    "candidate": "remediation_candidates",
    "approval": "remediation_approvals",
    "authorization": "remediation_authorizations",
    "write_intent": "remediation_write_intents",
    "dispatch": "remediation_executor_dispatches",
    "receipt": "remediation_step_receipts",
    "recovery_policy": "remediation_recovery_policies",
    "recovery_evaluation": "remediation_recovery_evaluations",
    "attempt": "remediation_attempts",
}


def read(path: Path) -> Any:
    return json.loads(path.read_bytes())


def export(root: Path) -> dict[str, Any]:
    result = read(root / "result.json")
    if result["terminal"] != "RECOVERED" or not result["cleanup"]["clean"]:
        raise ValueError("NO_COMPLETE_LIVE_RESULT")
    rows = read(root / "remediation-rows.json")
    objects = {}
    for name, table in TABLES.items():
        if len(rows[table]) != 1:
            raise ValueError("PRODUCT_OBJECT_CARDINALITY:" + table)
        objects[name] = json.loads(rows[table][0]["payload_json"])
    snapshots = [
        json.loads(row["payload_json"])
        for row in rows["remediation_current_state_snapshots"]
    ]

    def select_snapshot(field: str, expected: str) -> Any:
        found = [snapshot for snapshot in snapshots if snapshot[field] == expected]
        if len(found) != 1:
            raise ValueError("STATE_SNAPSHOT_BINDING")
        return found[0]

    objects["current_state"] = select_snapshot(
        "snapshot_id", objects["authorization"]["current_state_snapshot_id"]
    )
    objects["write_state"] = select_snapshot(
        "snapshot_id", objects["write_intent"]["before_state_snapshot_id"]
    )
    objects["dispatch_state"] = select_snapshot(
        "snapshot_sha256", objects["dispatch"]["before_state_sha256"]
    )
    objects["recovery_windows"] = [
        json.loads(row["payload_json"]) for row in rows["remediation_recovery_windows"]
    ]
    objects["recovery_windows"].sort(key=lambda w: w["ordinal"])
    refs = set(objects["receipt"]["supporting_evidence_refs"])
    for window in objects["recovery_windows"]:
        refs.update(window["supporting_evidence_refs"])
    recovery_evidence = {}
    for ref in refs:
        path = root / "data/objects/sha256" / ref[:2] / (ref + ".json")
        if hashlib.sha256(path.read_bytes()).hexdigest() != ref:
            raise ValueError("PRIVATE_CAS_MISMATCH")
        recovery_evidence[ref] = read(path)
    with sqlite3.connect(
        (root / "ledger/dispatch.sqlite3").as_uri() + "?mode=ro", uri=True
    ) as db:
        consumed = db.execute(
            "SELECT intent_id, dispatch_sha256 FROM consumed"
        ).fetchall()
    if consumed != [
        (
            objects["write_intent"]["write_intent_id"],
            objects["dispatch"]["dispatch_sha256"],
        )
    ]:
        raise ValueError("GATEWAY_CONSUMPTION_MISMATCH")
    images = read(root / "images.json")
    public = {
        **result,
        "objects": objects,
        "recovery_evidence": recovery_evidence,
        "gateway_consumptions": len(consumed),
        "matched_clause": objects["candidate"]["matched_clause_id"],
        "goal_authorization": read(root / "approval-authority.json"),
        "images": {
            name: {
                "platform_id": row["Id"],
                "index_id": row["index_id"],
                "os": row["Os"],
                "architecture": row["Architecture"],
            }
            for name, row in images.items()
        },
        "claim_boundary": "Pinned local minimal Payment configuration-fault recovery only; no production, full-Demo, generalization or exactly-once side-effect claim.",
    }
    for event_path in sorted((root / "product").glob("*.json")):
        event = read(event_path)
        if event["method"] == "GET" and event["status"] == 200:
            if event["route"].endswith("/baselines"):
                public["active_baseline"] = event["response"]["items"][0]
            elif event["route"].endswith("/capabilities"):
                public["capabilities"] = event["response"]
    diagnosis = read(root / "fault-diagnosis.json")
    public["product_diagnosis"] = diagnosis["diagnosis"]
    public["evidence_reference_commitments"] = diagnosis["index"][
        "all_object_sha256_by_ref"
    ]
    public["healthy_product_diagnosis"] = read(root / "healthy-diagnosis.json")[
        "diagnosis"
    ]
    public["private_evidence_commitment"] = digest(
        {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*.json"))
            if p.is_file()
        }
    )
    verify(public)
    out = REPO / "docs/results/product-v040-minimal-payment"
    out.mkdir(exist_ok=True)
    (out / "live-result.json").write_text(
        json.dumps(public, indent=2, sort_keys=True) + "\n"
    )
    return public


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("private_attempt_root", type=Path)
    result = export(parser.parse_args().private_attempt_root)
    print(
        json.dumps(
            {"terminal": result["terminal"], "source_head": result["source_head"]}
        )
    )
