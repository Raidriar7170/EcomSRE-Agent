"""Report planned/started/scored denominators separately for the retained subset.

Only opaque case IDs, aggregate metrics and bounded tool decisions are exported;
source mappings, labels and full model text stay private.
"""

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from scripts.product_v050.evaluate_semantic_investigation import paired_interval


def summarize(config, root, batch, split, ledger):
    cases = {c["case_id"]: c for c in config["cases"] if c["split"] == split}
    runs = {}
    for path in (root / "runs").glob("*.json"):
        if path.name.endswith(".intent.json"):
            continue
        r = json.loads(path.read_text())
        if (
            r.get("batch") == batch
            and r.get("split") == split
            and r.get("provider") == "configured"
        ):
            key = (r["case_id"], r["method"])
            if key in runs:
                raise ValueError("REPEATS_REQUIRE_SEPARATE_REPORT")
            runs[key] = r
    rows, trajectories = [], []
    for case_id, case in cases.items():
        for method in "ABCD":
            r = runs.get((case_id, method))
            prefix = (
                f"semantic-investigation-v1:{batch}:{case_id}:{method}:0:configured:"
            )
            calls = [v for k, v in ledger.items() if k.startswith(prefix)]
            started = bool(calls)
            status = (
                r["status"]
                if r
                else ("STARTED_WITHOUT_TERMINAL" if started else "NOT_STARTED")
            )
            report = (r or {}).get("report") or {}
            ranks = report.get("ranked_components", [])
            # A started failure remains in the denominator with empty predictions.
            if status != "COMPLETED":
                ranks = []
            score = int(bool(ranks) and ranks[0] == case["root"]) if started else None
            rr = (
                (1 / (ranks.index(case["root"]) + 1) if case["root"] in ranks else 0)
                if started
                else None
            )
            row = dict(
                case_id=case_id,
                event_group=case["event_group"],
                method=method,
                status=status,
                started=started,
                completed=status == "COMPLETED",
                correct=score,
                mrr=rr,
                empty_ranking_abstention=status == "COMPLETED" and not ranks,
                insufficient_evidence_report=report.get("conclusion")
                == "INSUFFICIENT_EVIDENCE",
                format_failure=status == "FORMAT_FAILED",
                provider_failure=status.startswith("PROVIDER_"),
                other_failure=started
                and status not in ["COMPLETED", "FORMAT_FAILED"]
                and not status.startswith("PROVIDER_"),
                actual_requests=len(calls),
                accounted_microusd=sum(v["accounted"] for v in calls),
                input_tokens=sum(
                    v.get("payload", {}).get("usage", {}).get("input_tokens", 0)
                    for v in calls
                ),
                output_tokens=sum(
                    v.get("payload", {}).get("usage", {}).get("output_tokens", 0)
                    for v in calls
                ),
                queries=(r or {}).get("queries", 0),
                records_scanned=(r or {}).get("records_scanned", 0),
                preprocessing_records=(r or {}).get("preprocessing_records", 0),
                returned_bytes=(r or {}).get("returned_bytes", 0),
                analysis_actions=(r or {}).get("analysis_actions", 0),
                not_started_reason=None
                if started
                else "DEVELOPMENT_GATE_PENDING_OR_NOT_ADMITTED",
            )
            rows.append(row)
            if r:
                decisions = []
                for t in r.get("trajectory", []):
                    item = {
                        k: t[k]
                        for k in [
                            "turn",
                            "error",
                            "check",
                            "candidate_scores",
                            "selected",
                            "fixed_request",
                        ]
                        if k in t
                    }
                    if "decision" in t:
                        item["candidate_requests"] = [
                            dict(request=c["request"], question=c.get("question"))
                            for c in t["decision"].get("candidates", [])
                        ]
                    decisions.append(item)
                trajectories.append(
                    dict(
                        case_id=case_id,
                        method=method,
                        status=status,
                        decisions=decisions,
                    )
                )
    methods = {}
    for method in "ABCD":
        rs = [r for r in rows if r["method"] == method]
        started = [r for r in rs if r["started"]]
        sums = [
            "completed",
            "empty_ranking_abstention",
            "insufficient_evidence_report",
            "format_failure",
            "provider_failure",
            "other_failure",
            "actual_requests",
            "accounted_microusd",
            "input_tokens",
            "output_tokens",
            "queries",
            "records_scanned",
            "preprocessing_records",
            "returned_bytes",
            "analysis_actions",
        ]
        correct = sum(r["correct"] for r in started)
        methods[method] = dict(
            planned=len(rs),
            started=len(started),
            not_started=len(rs) - len(started),
            correct=correct,
            acc1=correct / len(started) if started else None,
            mrr=sum(r["mrr"] for r in started) / len(started) if started else None,
            **{k: sum(r[k] for r in rs) for k in sums},
            statuses=dict(Counter(r["status"] for r in rs)),
        )
    contrasts = {}
    for other in "ABC":
        d = {
            r["event_group"]: r["correct"]
            for r in rows
            if r["method"] == "D" and r["started"]
        }
        b = {
            r["event_group"]: r["correct"]
            for r in rows
            if r["method"] == other and r["started"]
        }
        shared = sorted(d.keys() & b.keys())
        diffs = [d[k] - b[k] for k in shared]
        contrasts["D-" + other] = dict(
            paired_events=len(shared),
            correct_difference=sum(diffs),
            acc1_difference=sum(diffs) / len(diffs) if diffs else None,
            paired_bootstrap_95=paired_interval(diffs),
        )
    summary = dict(
        batch=batch,
        split=split,
        methods=methods,
        contrasts=contrasts,
        distinct_planned_events=len(cases),
        effect="undetermined",
        limits=[
            "Failed started calls stay in Acc1/MRR denominator; not-started does not.",
            "Service-only stratified trace subset; not official RCA100 score.",
            "Access counters are processing proxies, not physical I/O.",
            "Independent event grouping does not prove IID or no model pretraining exposure.",
        ],
    )
    return summary, rows, trajectories


def summarize_representation(config, root, batch, ledger):
    """Four seen events, repeats separate, failed started runs retained; no bootstrap."""
    cases = {c["case_id"]: c for c in config["cases"]}
    runs = {}
    for path in (root / "runs").glob("*.json"):
        if path.name.endswith(".intent.json"):
            continue
        r = json.loads(path.read_text())
        if r.get("batch") == batch and r.get("provider") == "configured":
            key = (r["case_id"], r["method"], r["repeat"])
            if key in runs:
                raise ValueError("DUPLICATE_REPAIR_RUN")
            runs[key] = r
    rows, trajectories, alerts = [], [], []
    for case_id, case in cases.items():
        feature = json.loads((root / f"{case_id}-features.json").read_text())
        rank = feature["alert_only"]["ranked_components"]
        alerts.append(
            dict(
                case_id=case_id,
                ranked_components=rank,
                correct=int(bool(rank) and rank[0] == case["root"]),
                mrr=1 / (rank.index(case["root"]) + 1) if case["root"] in rank else 0,
                covered=bool(rank),
                model_calls=0,
            )
        )
        for repeat in (0, 1):
            for method in ("C-service", "C-operation"):
                r = runs.get((case_id, method, repeat), {})
                prefix = f"semantic-investigation-v1:{batch}:{case_id}:{method}:{repeat}:configured:"
                calls = [v for k, v in ledger.items() if k.startswith(prefix)]
                started = bool(calls)
                status = r.get(
                    "status", "STARTED_WITHOUT_TERMINAL" if started else "NOT_STARTED"
                )
                report = r.get("report") or {}
                ranks = (
                    report.get("ranked_components", []) if status == "COMPLETED" else []
                )
                row = dict(
                    case_id=case_id,
                    method=method,
                    repeat=repeat,
                    started=started,
                    status=status,
                    completed=status == "COMPLETED",
                    correct=int(bool(ranks) and ranks[0] == case["root"])
                    if started
                    else None,
                    mrr=(
                        1 / (ranks.index(case["root"]) + 1)
                        if case["root"] in ranks
                        else 0
                    )
                    if started
                    else None,
                    abstained=status == "COMPLETED"
                    and report.get("conclusion") == "ABSTAIN",
                    non_abstain=status == "COMPLETED" and bool(ranks),
                    actual_requests=len(calls),
                    accounted_microusd=sum(v["accounted"] for v in calls),
                    input_tokens=sum(
                        v.get("payload", {}).get("usage", {}).get("input_tokens", 0)
                        for v in calls
                    ),
                    output_tokens=sum(
                        v.get("payload", {}).get("usage", {}).get("output_tokens", 0)
                        for v in calls
                    ),
                    reasoning_tokens=sum(
                        v.get("payload", {})
                        .get("usage", {})
                        .get("output_tokens_details", {})
                        .get("reasoning_tokens", 0)
                        for v in calls
                    ),
                    unknown_usage_calls=sum(
                        v.get("payload", {}).get("usage_status") != "reported"
                        for v in calls
                    ),
                    **{
                        k: r.get(k, 0)
                        for k in (
                            "analysis_actions",
                            "queries",
                            "records_scanned",
                            "preprocessing_records",
                            "reference_preprocessing_record_visits",
                            "returned_bytes",
                            "compute_ms",
                        )
                    },
                )
                row["versus_alert"] = (
                    None
                    if not started
                    else "corrected"
                    if row["correct"] and not alerts[-1]["correct"]
                    else "wrong_shift"
                    if not row["correct"] and alerts[-1]["correct"]
                    else "maintained_correct"
                    if row["correct"]
                    else "both_incorrect"
                )
                rows.append(row)
                if r:
                    trajectories.append(
                        dict(
                            case_id=case_id,
                            method=method,
                            repeat=repeat,
                            status=status,
                            trajectory=r["trajectory"],
                            report=report,
                            cited_new_operations=[
                                dict(
                                    analysis_id=x["analysis_id"],
                                    scope=[
                                        x["request"]["target"],
                                        x["request"]["operation"],
                                        x["request"]["direction"],
                                    ],
                                )
                                for x in r["analysis_results"]
                                if x["request"]["operation"] is not None
                                and x["analysis_id"]
                                in report.get("resolved_references", [])
                            ],
                        )
                    )
    methods = {}
    for method in ("C-service", "C-operation"):
        selected = [r for r in rows if r["method"] == method]
        started = [r for r in selected if r["started"]]
        methods[method] = dict(
            planned=8,
            started=len(started),
            not_started=8 - len(started),
            completed=sum(r["completed"] for r in selected),
            correct=sum(r["correct"] for r in started),
            acc1=sum(r["correct"] for r in started) / len(started) if started else None,
            mrr=sum(r["mrr"] for r in started) / len(started) if started else None,
            abstained=sum(r["abstained"] for r in selected),
            non_abstain=sum(r["non_abstain"] for r in selected),
            statuses=dict(Counter(r["status"] for r in selected)),
            repeats={
                str(i): dict(
                    started=sum(r["started"] for r in selected if r["repeat"] == i),
                    correct=sum(
                        r["correct"] or 0 for r in selected if r["repeat"] == i
                    ),
                )
                for i in (0, 1)
            },
            **{
                k: sum(r[k] for r in selected)
                for k in (
                    "actual_requests",
                    "accounted_microusd",
                    "input_tokens",
                    "output_tokens",
                    "reasoning_tokens",
                    "unknown_usage_calls",
                    "analysis_actions",
                    "queries",
                    "records_scanned",
                    "preprocessing_records",
                    "reference_preprocessing_record_visits",
                    "returned_bytes",
                    "compute_ms",
                )
            },
        )
    paired = []
    for case_id in cases:
        for repeat in (0, 1):
            pair = [
                r for r in rows if r["case_id"] == case_id and r["repeat"] == repeat
            ]
            paired.append(
                dict(
                    case_id=case_id,
                    repeat=repeat,
                    operation_minus_service=pair[1]["correct"] - pair[0]["correct"]
                    if all(r["started"] for r in pair)
                    else None,
                )
            )
    stage_calls = [
        v
        for k, v in ledger.items()
        if ":representation-repair-" in k and ":configured:" in k
    ]
    count = sum(r["started"] for r in rows)
    summary = dict(
        independent_events=4,
        planned=16,
        started=count,
        not_started=16 - count,
        methods=methods,
        paired=paired,
        alert_only=dict(
            planned=4,
            completed=4,
            correct=sum(r["correct"] for r in alerts),
            acc1=sum(r["correct"] for r in alerts) / 4,
            mrr=sum(r["mrr"] for r in alerts) / 4,
            non_abstain=sum(r["covered"] for r in alerts),
            model_calls=0,
            events=alerts,
        ),
        experiment="complete_development"
        if count == 16 and all(r["status"] != "STARTED_WITHOUT_TERMINAL" for r in rows)
        else "partial"
        if count
        else "not_run",
        effect="undetermined",
        stage_cost_including_smoke=dict(
            requests=len(stage_calls),
            accounted_microusd=sum(v["accounted"] for v in stage_calls),
        ),
        cumulative_cost=dict(
            requests=len(ledger),
            accounted_microusd=sum(v["accounted"] for v in ledger.values()),
        ),
        limitations=[
            "Four exposed development events; repetitions are not independent events.",
            "Logical scan and preprocessing visits are not measured physical I/O.",
            "No D evaluation, no business health, no causal or generalization proof.",
        ],
    )
    return summary, rows, trajectories


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--ledger", type=Path, required=True)
    p.add_argument("--batch", required=True)
    p.add_argument("--split", choices=["development", "test"], required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    config = json.loads(a.config.read_text())
    ledger = {v["key"]: v for v in map(json.loads, a.ledger.read_text().splitlines())}
    if config.get("representation_version"):
        summary, rows, trajectory = summarize_representation(
            config, a.root, a.batch, ledger
        )
    else:
        summary, rows, trajectory = summarize(config, a.root, a.batch, a.split, ledger)
    a.output.mkdir(parents=True, exist_ok=True)
    prefix = f"{a.batch}-{a.split}"
    (a.output / f"{prefix}-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    for suffix, values in [("runs", rows), ("trajectory", trajectory)]:
        (a.output / f"{prefix}-{suffix}.jsonl").write_text(
            "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in values)
        )
    with (a.output / f"{prefix}-runs.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
