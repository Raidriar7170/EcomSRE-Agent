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
