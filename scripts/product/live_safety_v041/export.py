"""Export bounded public projections from retained completed case evidence."""

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any
from scripts.product.minimal_payment_acceptance_v040.owned import digest
from scripts.product.minimal_payment_acceptance_v040.plan import REPO

NAMES = {
    "S0": "healthy-non-action",
    "S1": "revoked-approval",
    "S2": "state-drift",
    "S3": "idempotent-recovery",
    "S4": "verification-failure",
}
OUT = REPO / "docs/results/product-v041-live-safety"


def read(path: Path) -> Any:
    return json.loads(path.read_bytes())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_event(value: str | float, source: str) -> dict[str, Any]:
    if isinstance(value, (int, float)):
        value = datetime.fromtimestamp(value, UTC).isoformat()
    return {
        "utc": value,
        "monotonic_ns": "NOT_MEASURED",
        "clock_scope": "persisted-runtime-utc",
        "source": source,
        "semantics": "persisted object timestamp; exact database commit monotonic not instrumented",
    }


def export_case(root: Path) -> dict[str, Any]:
    result = read(root / "result.json")
    if result.get("evidence_status") != "COLLECTED" or not result["cleanup"]["clean"]:
        raise ValueError("CASE_NOT_EXPORTABLE")
    public = dict(result)
    public["service_roles"] = list(read(root / "compose.json")["services"])
    if (root / "config/recovery-policy.json").exists():
        public["recovery_policy"] = read(root / "config/recovery-policy.json")
    # Raw database trace rows are replaced with their typed payload only.
    public["decision_trace"] = [
        json.loads(r["payload_json"]) for r in result["decision_trace"]
    ]
    public["images"] = {
        name: {k: r[k] for k in ("Id", "index_id", "Architecture", "Os")}
        for name, r in read(root / "images.json").items()
    }
    for key, file in [
        ("healthy_diagnosis", "healthy-diagnosis.json"),
        ("fault_diagnosis", "fault-diagnosis.json"),
    ]:
        if (root / file).exists():
            public[key] = read(root / file)["diagnosis"]
    if (root / "drift-state.json").exists():
        public["drift_state"] = read(root / "drift-state.json")
    if (root / "executor-replay.json").exists():
        public["executor_replay"] = read(root / "executor-replay.json")
    calls = [read(p) for p in sorted((root / "product").glob("*.json"))]
    public["api_calls"] = [
        {
            k: v
            for k, v in c.items()
            if k in ("method", "route", "status", "started", "ended")
        }
        | {
            "key_sha256": digest(c.get("idempotency_key")),
            "request_sha256": digest(c["request"]),
            "response_sha256": digest(c["response"]),
        }
        for c in calls
    ]
    events = dict(public["events"])
    candidate_calls = [
        c
        for c in calls
        if c["method"] == "POST" and c["route"].endswith("/remediation-candidates")
    ]
    if result["case_id"] == "S0":
        events["safe_denial_request_started"] = candidate_calls[-1]["started"]
        events["safe_denial"] = candidate_calls[-1]["ended"]
    else:
        events["safe_denial_request_started"] = events["attempt_request_started"]
    objects = public["objects"]
    for event, kind, field in [
        ("candidate_persisted", "candidate", "created_at"),
        ("approval_persisted", "approval", "issued_at"),
        ("authorization_persisted", "authorization", "created_at"),
        ("write_intent_committed", "write_intent", "committed_at"),
        ("StepReceipt_persisted", "receipt", "created_at"),
    ]:
        if objects[kind]:
            obj = objects[kind][0]
            if field in obj:
                events[event] = utc_event(obj[field], f"objects.{kind}.0.{field}")
    persisted_candidate_calls = [
        c for c in candidate_calls if c["response"].get("candidates")
    ]
    if persisted_candidate_calls:
        events["candidate_persisted"] = {
            **persisted_candidate_calls[0]["ended"],
            "source": "first Candidate POST response",
            "semantics": "Persistence observed at API response; includes local transport, not exact commit time. Candidate created_at is diagnosis-anchored and is not used as persistence latency.",
        }
    if public.get("fault_diagnosis"):
        diagnosis = public["fault_diagnosis"]
        incident_id = diagnosis["incident_id"]
        inc = next(
            c["response"]
            for c in calls
            if c["method"] == "POST"
            and c["route"] == "/v1/incidents"
            and c["response"]["incident_id"] == incident_id
        )
        events["incident_created"] = utc_event(
            inc["created_at"], "persisted Incident.created_at"
        )
        job_call = next(
            c
            for c in calls
            if c["method"] == "POST"
            and c["route"] == f"/v1/incidents/{incident_id}/diagnosis-jobs"
        )
        with sqlite3.connect(
            (root / "data/product.sqlite3").as_uri() + "?mode=ro", uri=True
        ) as db:
            job_events = db.execute(
                "SELECT event_type,created_at FROM job_events WHERE job_id=? ORDER BY created_at",
                (job_call["response"]["job_id"],),
            ).fetchall()
        public["diagnosis_job_events"] = [
            {"event_type": k, "utc": datetime.fromtimestamp(v, UTC).isoformat()}
            for k, v in job_events
        ]
        for kind, at in job_events:
            if kind == "CLAIMED":
                events["diagnosis_job_started"] = utc_event(at, "job_events.CLAIMED")
            elif kind == "SUCCEEDED":
                events["diagnosis_completed"] = utc_event(at, "job_events.SUCCEEDED")
    if public["state_snapshots"]:
        snapshot = public["state_snapshots"][0]
        events["current_state_observed"] = utc_event(
            snapshot["observed_at"], "state_snapshots.0.observed_at"
        )
    if objects["recovery_evaluation"]:
        evaluation = objects["recovery_evaluation"][0]
        events[evaluation["terminal"] + "_persisted"] = utc_event(
            evaluation["created_at"], "objects.recovery_evaluation.0.created_at"
        )
    observations = {}
    refs = set()
    for kind in ("receipt", "recovery_window"):
        for obj in objects[kind]:
            refs.update(obj["supporting_evidence_refs"])
    for ref in sorted(refs):
        p = root / "data/objects/sha256" / ref[:2] / (ref + ".json")
        if sha(p) != ref:
            raise ValueError("CAS_MISMATCH")
        observations[ref] = read(p)
    public["recovery_evidence"] = observations
    decision_evidence = {}
    for ref in sorted(
        {ref for event in public["decision_trace"] for ref in event["evidence_refs"]}
    ):
        path = root / "data/objects/sha256" / ref[:2] / (ref + ".json")
        if sha(path) != ref:
            raise ValueError("DECISION_CAS_MISMATCH")
        decision_evidence[ref] = read(path)
    public["decision_evidence"] = decision_evidence
    raw_windows = [
        read(p) for p in sorted((root / "observer/raw").glob("window-*.json"))
    ]
    for window in objects["recovery_window"]:
        ordinal = window["ordinal"]
        events[f"recovery_window_{ordinal}_started"] = utc_event(
            window["started_at"], f"objects.recovery_window.{ordinal - 1}.started_at"
        )
        events[f"recovery_window_{ordinal}_ended"] = utc_event(
            window["ended_at"], f"objects.recovery_window.{ordinal - 1}.ended_at"
        )
    if objects["receipt"]:
        receipt_at = datetime.fromisoformat(
            objects["receipt"][0]["ended_at"].replace("Z", "+00:00")
        )
        successes = [
            r
            for w in raw_windows
            for r in w["requests"]
            if r["value"]["ok"] and datetime.fromisoformat(r["at"]) >= receipt_at
        ]
        if successes:
            events["first_successful_business_request"] = min(
                successes, key=lambda r: r["at"]
            )["clock"]
    required = [
        "fault_write_started",
        "fault_write_acknowledged",
        "first_failed_business_request",
        "incident_created",
        "diagnosis_job_started",
        "diagnosis_completed",
        "candidate_persisted",
        "approval_persisted",
        "current_state_observed",
        "authorization_persisted",
        "write_intent_committed",
        "gateway_restore_consumed",
        "StepReceipt_persisted",
        "first_successful_business_request",
        "recovery_window_1_started",
        "recovery_window_1_ended",
        "recovery_window_2_started",
        "recovery_window_2_ended",
        "RECOVERED_persisted",
        "cleanup_started",
        "cleanup_completed",
    ]
    if result["case_id"] == "S3":
        for name in required:
            events.setdefault(
                name,
                {
                    "utc": "NOT_MEASURED",
                    "monotonic_ns": "NOT_MEASURED",
                    "source": "No authoritative runtime timestamp available",
                },
            )
    public["timeline"] = events
    public["timing_boundary"] = (
        "Single observed case; cross-process durations use persisted UTC. Only matching harness clock scopes use monotonic. Poll observations are not commit times. Gateway ledger has no consumption timestamp; unavailable values remain NOT_MEASURED."
    )
    public["source_evidence_sha256"] = sha(root / "result.json")
    OUT.mkdir(parents=True, exist_ok=True)
    (
        OUT / f"case-{result['case_id'].lower()}-{NAMES[result['case_id']]}.json"
    ).write_text(json.dumps(public, indent=2, sort_keys=True) + "\n")
    return public


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    value = export_case(parser.parse_args().root)
    print(
        json.dumps(
            {
                "case": value["case_id"],
                "terminal": value["terminal"],
                "counts": value["counts"],
            }
        )
    )


if __name__ == "__main__":
    main()
