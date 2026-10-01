"""Independent retrospective research CLI; no Product DB, Docker or live reads."""

from __future__ import annotations
import argparse
from datetime import UTC, datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
from types import SimpleNamespace

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre.product.errors import ProductError
from ecomsre.product.investigation.contracts import PriceSchedule
from ecomsre.product.investigation.provider import StructuredProvider
from ecomsre.product.investigation.semantic_analysis import SemanticAnalysis, digest
from ecomsre.product.investigation.semantic_contracts import (
    AnalysisRequest,
    Candidate,
    Hypothesis,
    InvestigationReport,
    ObservableQuestion,
    SemanticDecision,
)
from ecomsre.product.investigation.semantic_policy import investigate_semantic_v1
from scripts.product_v050.diagnostic_semantics_replay import (
    Inputs,
    spans_from,
    deduplicate,
    raw_population,
)
from scripts.product_v050.project_environment import load_project_environment

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config/semantic-investigation-v1/experiment.json"


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    path.chmod(0o600)


class ResearchLedger:
    """Tiny append-only budget adapter for existing StructuredProvider transport.

    Lock covers each read/append transaction. A reserved call is permanently
    charged at its upper bound until settled, including process interruption.
    No old session reuse and no connection to the Product repository.
    """

    def __init__(self, root, limit=20_000_000, calls=1600):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.limit = min(limit, 20_000_000)
        self.calls = min(calls, 1600)
        self.path = self.root / "ledger.jsonl"
        self.objects = self

    def put_json(self, value):
        h = digest(value)
        path = self.root / "objects" / f"{h}.json"
        if not path.exists():
            write_new(path, value)
        return SimpleNamespace(object_sha256=h)

    def entries(self):
        if not self.path.exists():
            return {}
        calls = {}
        for line in self.path.read_text().splitlines():
            entry = json.loads(line)
            calls[entry["key"]] = entry
        return calls

    def transaction(self, callback):
        with (self.root / "ledger.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                entry = callback(self.entries())
                with self.path.open("a") as f:
                    f.write(json.dumps(entry, allow_nan=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                self.path.chmod(0o600)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def reserve(self, key, request, reserve, **_):
        def append(calls):
            if not key.startswith("semantic-investigation-v1:"):
                raise ValueError("RESEARCH_NAMESPACE_REQUIRED")
            if key in calls:
                raise ProductError(
                    "CALL_ALREADY_RESERVED",
                    "A real request is never silently reused or repeated.",
                )
            if any(v["state"] == "UNSAFE_COST_BOUND" for v in calls.values()):
                raise ProductError(
                    "UNSAFE_COST_BOUND",
                    "Prior model/cost mismatch stops this namespace.",
                )
            spent = sum(v["accounted"] for v in calls.values())
            if len(calls) >= self.calls or spent + reserve > self.limit:
                raise ProductError(
                    "BUDGET_EXHAUSTED", "Independent research ceiling reached."
                )
            return dict(
                key=key,
                state="RESERVED",
                accounted=reserve,
                reserved=reserve,
                request_sha256=digest(request),
                at=datetime.now(UTC).isoformat(),
            )

        self.transaction(append)
        return None

    def settle(self, key, payload, charge, state):
        def append(calls):
            old = calls[key]
            if old["state"] != "RESERVED":
                raise ValueError("ALREADY_SETTLED")
            return dict(
                old,
                state=state,
                accounted=old["reserved"] if charge is None else charge,
                payload=payload,
            )

        self.transaction(append)


def load_retained(root, number, config):
    inputs = Inputs(root)
    spans = []
    raw = inputs.lines(f"round-{number}/diagnosis-job/raw.jsonl")
    capture_metadata = []
    for e in raw:
        if (
            e["url"].endswith("/api/traces")
            and e["status_code"] == 200
            and not e["truncated"]
        ):
            payload = inputs.response(e)
            capture = dict(
                received_at=e["received_at"],
                truncated=e["truncated"],
                limit_reached=len(payload.get("data", [])) >= int(e["params"]["limit"]),
            )
            capture_metadata.append(capture)
            spans += spans_from(payload, capture)
    for p in sorted((Path(root) / f"round-{number}/context").glob("traces-*.json")):
        meta = inputs.read(str(p.relative_to(root)))
        body = str(p.with_suffix(".body").relative_to(root))
        payload = inputs.read(body)
        if (
            inputs.hashes[body] != meta["body_sha256"]
            or meta["status"] != 200
            or meta["error"]
        ):
            raise ValueError("RETAINED_SOURCE_BINDING_MISMATCH")
        capture = dict(
            received_at=meta["at"],
            truncated=meta["truncated"],
            limit_reached=len(payload.get("data", [])) >= int(meta["params"]["limit"]),
        )
        capture_metadata.append(capture)
        spans += spans_from(payload, capture)
    rows = []
    for s in deduplicate(spans):
        parents = [
            r
            for r in s["references"]
            if r.get("refType") == "CHILD_OF" and r.get("traceID") == s["trace_id"]
        ]
        if len(parents) > 1:
            raise ValueError("AMBIGUOUS_PARENT")
        status = s["status"]
        error = (
            True
            if status in ("ERROR", "STATUS_CODE_ERROR", 2)
            else False
            if status in ("OK", "UNSET", "STATUS_CODE_OK", "STATUS_CODE_UNSET", 0, 1)
            else None
        )
        if s["grpc_status"] is not None:
            if str(s["grpc_status"]) != "0":
                error = True
        # No instance identity, path, raw tag or capture filename is model-visible.
        rows.append(
            dict(
                record_ref="rec-" + digest([s["trace_id"], s["span_id"]])[:20],
                trace_id="t-" + digest(s["trace_id"])[:20],
                span_id="s-" + digest(s["span_id"])[:20],
                parent_span_id="s-" + digest(parents[0]["spanID"])[:20]
                if parents
                else None,
                service=s["service"],
                operation=s["operation"],
                direction=s["direction"],
                start=s["start"],
                end=s["end"],
                duration_ms=s["duration_us"] / 1000
                if s["duration_us"] is not None
                else None,
                error=error,
            )
        )
    rows.sort(key=lambda r: (r["service"], r.get("end") or 0, r["record_ref"]))
    # The retained public window locator is preexisting metadata, not a test label.
    prior = json.loads(
        (
            ROOT / "docs/results/product-v050/diagnostic-semantics/comparison.json"
        ).read_text()
    )
    w = next(w for w in prior["windows"] if w["round"] == number)["window"]
    evidence = inputs.read(f"round-{number}/evidence.json")
    residuals = []
    for obj in evidence["objects"]:
        for component in obj["payload"].get("connector_components", []):
            if component.get("source") == "TRACES":
                continue
            records = []
            # Typed numeric/state projection, not evaluator explanations or root labels.
            fields = {
                "service",
                "metric_kind",
                "value",
                "unit",
                "cpu_percent",
                "memory_bytes",
                "memory_slope_bytes_per_second",
                "state",
                "severity",
                "sample_count",
                "support_status",
                "sampling_window_seconds",
                "restart_count",
                "healthy",
            }
            for r in component.get("records", []):
                record = {
                    k: v
                    for k, v in r.items()
                    if k in fields and (v is None or type(v) in (str, int, float, bool))
                }
                if record:
                    records.append(record)
            if records:
                residuals.append(
                    dict(
                        source=component.get("source"),
                        records=records,
                        truncated=component.get("truncated", True),
                    )
                )
    for residual in residuals:
        residual["observation_id"] = "obs-" + digest(residual)[:20]
    sample_entries = inputs.lines(f"round-{number}/samples/raw.jsonl")
    counters = {}
    for service in sorted({r["service"] for r in rows}):
        calls = raw_population(inputs, sample_entries, service, "calls_total")
        buckets = raw_population(
            inputs, sample_entries, service, "duration_milliseconds_bucket"
        )
        if calls:
            # Strip instance/resource labels, retaining distinctions through opaque series IDs.
            def project(series):
                out = []
                for row in series:
                    labels = row["metric"]
                    kept = {
                        k: v
                        for k, v in labels.items()
                        if k
                        in {
                            "__name__",
                            "service_name",
                            "span_name",
                            "span_kind",
                            "status_code",
                            "le",
                        }
                    }
                    kept["instance_handle"] = (
                        "i-"
                        + digest(
                            {
                                k: v
                                for k, v in labels.items()
                                if k not in {"__name__", "le"}
                            }
                        )[:16]
                    )
                    out.append(dict(metric=kept, values=row["values"]))
                return out

            counters[service] = dict(
                rows=project(calls),
                buckets=project(buckets),
                source_refs=[
                    "counter-" + digest(calls)[:20],
                    "bucket-" + digest(buckets)[:20],
                ],
            )
    snapshot = dict(
        counters=counters,
        services=sorted(
            set(r["service"] for r in rows)
            | {"checkout", "fraud-detection", "payment", "kafka"}
        ),
        records=rows,
        windows={"current": [w["start"], w["end"]]},
        references={},
        topology=[],
        residuals=residuals,
        metadata=dict(
            record_count=len(rows),
            counter_points_by_service={
                s: sum(len(r["values"]) for r in c["rows"] + c["buckets"])
                for s, c in counters.items()
            },
            counter_points=sum(
                len(r["values"])
                for c in counters.values()
                for r in c["rows"] + c["buckets"]
            ),
            available_sources=["traces", "counters"],
            counter_scope="operation only; final 300s observed increments, not rate",
            trace_fields=True,
            coverage="RETAINED_BOUNDED_TRACE_SAMPLES",
            truncated=any(
                c["truncated"] or c["limit_reached"] for c in capture_metadata
            ),
            availability="RETROSPECTIVE_REPLAY_ONLINE_CUTOFF_UNPROVEN",
            capture_times=sorted({c["received_at"] for c in capture_metadata}),
            reference_status="NO_MATCHED_OPERATION_TRACE_REFERENCE",
        ),
    )
    return snapshot, dict(
        root=str(Path(root).resolve()),
        files=inputs.hashes,
        case_snapshot_sha256=digest(snapshot),
        round=number,
        independent_event_group="event-001",
        split="development",
        root_label=None,
    )


class FixtureProvider:
    def complete(self, *, view, schema=None, **_):
        if view.get("representation_version"):
            from ecomsre.product.investigation.semantic_lite import (
                TurnDecision,
                ReportDecision,
            )

            if view["evidence"] or view["report_required"]:
                decision = dict(
                    action="report",
                    rationale="fixture",
                    report=dict(
                        ranked_components=[],
                        explanation="Fixture only, no model inference.",
                        references=[],
                        residuals=[],
                        limitations=["FIXTURE_ONLY"],
                        conclusion="ABSTAIN",
                    ),
                )
                return (
                    ReportDecision(**decision)
                    if schema is ReportDecision
                    else TurnDecision(decision=decision)
                )
            return TurnDecision(
                decision=dict(
                    action="analyze",
                    question_id=view["questions"][0]["question_id"],
                    hypothesis_updates=[],
                    expectations=[],
                    rationale="fixture",
                )
            )
        results = view["analysis_results"] or view["observations"]
        target = view["metadata"]["services"][0]
        if results:
            return SemanticDecision(
                report=InvestigationReport(
                    ranked_components=[],
                    explanation="Fixture local computation only.",
                    facts=[],
                    alternatives=[],
                    residuals=["No causal truth."],
                    next_observation="Matched reference required.",
                    conclusion="INSUFFICIENT_EVIDENCE",
                ),
                rationale="fixture",
            )
        tool = "read_records" if view["method"] == "A" else "profile_operations"
        return SemanticDecision(
            hypotheses=[
                Hypothesis(
                    hypothesis_id="H1",
                    explanation="Operation errors may be present.",
                    target=target,
                )
            ],
            candidates=[
                Candidate(
                    request=AnalysisRequest(tool=tool, target=target),
                    question=ObservableQuestion(
                        field="count",
                        row_key="UNKNOWN",
                        comparator="gt",
                        threshold=0,
                        expectations={"H1": "YES"},
                    ),
                )
            ],
            rationale="fixture only",
        )


def provider_for(config, ledger):
    env = Path.home() / ".config/ecomsre/provider.env"
    if env.exists():
        load_project_environment(env)
    c = OpenAICompatibleConfig.from_environment()
    if (
        c is None
        or c.model != config["model"]
        or c.base_url != "https://api.openai.com/v1"
    ):
        raise ValueError("CONFIGURED_PROVIDER_NOT_APPROVED")
    prices = PriceSchedule.model_validate(config["prices"])
    return StructuredProvider(c, prices, ledger, api_style="responses")


def paired_interval(differences, seed=29):
    if len(differences) < 2:
        return None
    rng = random.Random(seed)
    samples = sorted(
        sum(rng.choices(differences, k=len(differences))) / len(differences)
        for _ in range(2000)
    )
    return [samples[49], samples[1949]]


def summarize_runs(runs, labels):
    methods = {}
    for method in "ABCD":
        selected = [r for r in runs if r["method"] == method]
        scored = [
            r for r in selected if labels.get(r["case_id"], {}).get("root") is not None
        ]
        grouped = {}
        for r in scored:
            label = labels[r["case_id"]]
            roots = (r.get("report") or {}).get("ranked_components", [])
            grouped.setdefault(label["event_group"], []).append(
                dict(
                    acc=int(bool(roots) and roots[0] == label["root"]),
                    mrr=1 / (roots.index(label["root"]) + 1)
                    if label["root"] in roots
                    else 0,
                )
            )
        event_scores = {
            g: sum(x["acc"] for x in xs) / len(xs) for g, xs in grouped.items()
        }
        methods[method] = dict(
            started=len(selected),
            completed=sum(r["status"] == "COMPLETED" for r in selected),
            independent_root_events=len(grouped),
            acc1=sum(event_scores.values()) / len(grouped) if grouped else None,
            mrr=sum(sum(x["mrr"] for x in xs) / len(xs) for xs in grouped.values())
            / len(grouped)
            if grouped
            else None,
            event_scores=event_scores,
            report_coverage=sum(r.get("report") is not None for r in selected)
            / len(selected)
            if selected
            else None,
            semantic_false_assertions=sum(
                any(
                    (r.get("report") or {}).get(k, False)
                    for k in (
                        "root_confirmed",
                        "business_fault_excluded",
                        "system_healthy",
                    )
                )
                for r in selected
                if labels.get(r["case_id"], {}).get("insufficient")
            ),
            provider_calls=sum(r["provider_calls"] for r in selected),
            analysis_actions=sum(r["analysis_actions"] for r in selected),
            queries=sum(r["queries"] for r in selected),
            records_scanned=sum(r["records_scanned"] for r in selected),
        )
    contrasts = {}
    for m in "ABC":
        common = sorted(
            methods["D"]["event_scores"].keys() & methods[m]["event_scores"].keys()
        )
        diffs = [
            methods["D"]["event_scores"][e] - methods[m]["event_scores"][e]
            for e in common
        ]
        contrasts["D-" + m] = dict(
            events=len(common),
            difference=sum(diffs) / len(diffs) if diffs else None,
            paired_bootstrap_95=paired_interval(diffs),
        )
    return dict(methods=methods, contrasts=contrasts, effect="undetermined")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument(
        "--mode",
        choices=["inspect", "smoke", "single", "evaluate", "summarize"],
        required=True,
    )
    p.add_argument("--provider", choices=["fixture", "configured"], default="fixture")
    p.add_argument("--methods", nargs="+", choices=list("ABCD"), default=list("ABCD"))
    p.add_argument("--views", nargs="+", choices=["service", "operation"])
    p.add_argument("--split", choices=["development", "test"], default="development")
    p.add_argument("--case", default=None)
    p.add_argument("--repeat", type=int, default=0)
    p.add_argument("--batch", default="dev-01")
    p.add_argument(
        "--input-root",
        type=Path,
        default=ROOT / ".local/engineering-calibration/live-03",
    )
    p.add_argument(
        "--output", type=Path, default=ROOT / ".local/semantic-investigation-v1"
    )
    args = p.parse_args()
    config = json.loads(args.config.read_text())
    if args.mode == "summarize":
        runs = [
            json.loads(p.read_text())
            for p in sorted((args.output / "runs").glob("*.json"))
            if not p.name.endswith(".intent.json")
        ]
        runs = [
            r
            for r in runs
            if r["provider"] == args.provider
            and r["batch"] == args.batch
            and r["split"] == args.split
            and r["repeat"] == args.repeat
            and r.get("config_sha256") == digest(config)
        ]
        labels = {c["case_id"]: c for c in config["cases"]}
        print(json.dumps(summarize_runs(runs, labels), indent=2))
        return
    selected = [
        c
        for c in config["cases"]
        if c["split"] == args.split and (args.case is None or c["case_id"] == args.case)
    ]
    if not selected:
        print(
            json.dumps(
                dict(status="NO_CASES_IN_SPLIT", split=args.split, experiment="partial")
            )
        )
        return
    snapshots = []
    for c in selected:
        if "snapshot_file" in c:
            path = args.input_root / c["snapshot_file"]
            snapshot = json.loads(path.read_text())
            manifest = dict(
                files={
                    c["snapshot_file"]: hashlib.sha256(path.read_bytes()).hexdigest()
                },
                source="retained-rca100-trace-subset-v1",
            )
        else:
            snapshot, manifest = load_retained(args.input_root, c["round"], config)
        snapshots.append((c, snapshot, manifest))
    if args.mode == "inspect":
        print(
            json.dumps(
                [
                    dict(
                        case_id=c["case_id"],
                        event_group=c["event_group"],
                        split=c["split"],
                        root_label=c["root"],
                        metadata=s["metadata"],
                        services=s["services"],
                        input_files=len(m["files"]),
                    )
                    for c, s, m in snapshots
                ],
                indent=2,
            )
        )
        return
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True)
    common = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=ROOT,
            text=True,
        ).strip()
    )
    stage = config.get("stage_budget") if config.get("representation_version") else None
    ledger = ResearchLedger(
        common / "semantic-investigation-v1/provider",
        limit=min(20_000_000, stage["start_microusd"] + stage["max_new_microusd"])
        if stage
        else 20_000_000,
        calls=min(1600, stage["start_calls"] + stage["max_new_calls"])
        if stage
        else 1600,
    )
    if stage:
        if args.split != "development" or args.methods != ["C"] or not args.views:
            raise ValueError("REPAIR_REQUIRES_DEVELOPMENT_C_AND_EXPLICIT_VIEWS")
        if args.batch not in (
            "representation-repair-smoke-v1",
            "representation-repair-paired-v1",
        ):
            raise ValueError("REPAIR_FIXED_BATCH_REQUIRED")
        if args.repeat not in (0, 1):
            raise ValueError("REPAIR_AT_MOST_TWO_REPEATS")
        if any(
            c["case_id"] not in [f"rcase-{i:03d}" for i in range(1, 5)]
            for c, _, _ in snapshots
        ):
            raise ValueError("REPAIR_FOUR_EXPOSED_EVENTS_ONLY")
        for c, s, _ in snapshots:
            if (
                s["metadata"].get("representation_version")
                != config["representation_version"]
            ):
                raise ValueError("REPAIR_REQUIRES_RESTORED_INPUTS")
    provider = (
        provider_for(config, ledger)
        if args.provider == "configured"
        else FixtureProvider()
    )
    if args.mode in ("single", "smoke"):
        snapshots = snapshots[:1]
    for i, (case, snapshot, manifest) in enumerate(snapshots):
        methods = ["C-" + view for view in args.views] if stage else list(args.methods)
        # Frozen rotating paired order, with every method on a case before next case.
        rotation = (
            (int(case["case_id"].split("-")[-1]) - 1 + args.repeat) % len(methods)
            if stage
            else i % len(methods)
        )
        methods = methods[rotation:] + methods[:rotation]
        for method in methods:
            if stage and args.provider == "configured":
                prior_intents = [
                    json.loads(p.read_text())
                    for p in (args.output / "runs").glob("*.intent.json")
                ]
                count = sum(
                    ":" + args.batch + ":" in x["run_id"] for x in prior_intents
                )
                if count >= (2 if "smoke" in args.batch else 16):
                    raise ValueError("REPAIR_TRAJECTORY_LIMIT")
            key = f"semantic-investigation-v1:{args.batch}:{case['case_id']}:{method}:{args.repeat}:{args.provider}"
            filename = hashlib.sha256(key.encode()).hexdigest()[:24]
            result_path = args.output / "runs" / f"{filename}.json"
            if result_path.exists():
                raise ValueError("RUN_EXISTS_USE_EXPLICIT_NEW_REPEAT_OR_BATCH")
            write_new(
                result_path.with_suffix(".intent.json"),
                dict(
                    run_id=key,
                    config_sha256=digest(config),
                    snapshot_sha256=digest(snapshot),
                    code_head=subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                    ).strip(),
                    source_sha256={
                        str(p.relative_to(ROOT)): hashlib.sha256(
                            p.read_bytes()
                        ).hexdigest()
                        for p in sorted(
                            [
                                *(ROOT / "src/ecomsre/product/investigation").glob(
                                    "semantic_*.py"
                                ),
                                ROOT / "src/ecomsre/product/investigation/provider.py",
                                Path(__file__).resolve(),
                                ROOT
                                / "scripts/product_v050/prepare_semantic_rca100.py",
                            ]
                        )
                    },
                    manifest=manifest,
                ),
            )
            runner = investigate_semantic_v1
            if config.get("research_protocol") == "semantic-lite-v1":
                from ecomsre.product.investigation.semantic_lite import (
                    investigate_semantic_lite,
                )

                runner = investigate_semantic_lite
            run_config = (
                dict(config, reference_view=method.removeprefix("C-"))
                if stage
                else config
            )
            state = runner(
                SemanticAnalysis(snapshot, run_config),
                provider,
                "C" if stage else method,
                key,
                run_config,
            )
            state["method"] = method
            state.update(
                config_sha256=digest(config),
                snapshot_sha256=digest(snapshot),
                case_id=case["case_id"],
                event_group=case["event_group"],
                split=case["split"],
                repeat=args.repeat,
                batch=args.batch,
                provider=args.provider,
            )
            calls = [v for k, v in ledger.entries().items() if k.startswith(key + ":")]
            state["cost"] = dict(
                actual_requests=len(calls),
                accounted_microusd=sum(v["accounted"] for v in calls),
                reserved_microusd=sum(v["reserved"] for v in calls),
                input_tokens=sum(
                    v.get("payload", {}).get("usage", {}).get("input_tokens", 0)
                    for v in calls
                ),
                output_tokens=sum(
                    v.get("payload", {}).get("usage", {}).get("output_tokens", 0)
                    for v in calls
                ),
                unknown_usage_calls=sum(
                    v.get("payload", {}).get("usage_status") != "reported"
                    for v in calls
                ),
            )
            write_new(result_path, state)
            print(
                json.dumps(
                    dict(
                        run_id=key,
                        status=state["status"],
                        actions=state["analysis_actions"],
                        cost=state["cost"],
                    )
                ),
                flush=True,
            )
            if state["status"] in (
                "BUDGET_EXHAUSTED",
                "UNSAFE_COST_BOUND",
                "PROVIDER_MODEL_MISMATCH",
                "PROVIDER_USAGE_BOUND_EXCEEDED",
            ):
                return


if __name__ == "__main__":
    main()
