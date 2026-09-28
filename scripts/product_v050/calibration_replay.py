"""Offline, repeatable development replay. No DB, HTTP, Docker or Provider imports.

Every invocation creates a new private output directory. Retained captures are
read-only; v3 diagnostics never become a receipt or a historical acceptance.
"""

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import subprocess
import time

from scripts.product_v050 import ingestion_evidence as ie
from scripts.product_v050 import sampling_support as support


def replay(capture_root, objects, output, *, application_config=None, context=None):
    capture_root, objects, output = (
        Path(p).resolve() for p in (capture_root, objects, output)
    )
    if any(output == p or p in output.parents for p in (capture_root, objects)):
        raise ValueError("output must be separate from retained inputs")
    if output.exists():
        raise ValueError("output already exists; each development replay is retained")
    started_at = datetime.now(UTC).isoformat()
    started = time.monotonic()
    hashes = {}

    def read(path):
        raw = path.read_bytes()
        hashes[str(path)] = hashlib.sha256(raw).hexdigest()
        return raw

    def document(name):
        return json.loads(read(capture_root / name))

    def cas(digest):
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid CAS digest")
        raw = read(objects / "sha256" / digest[:2] / (digest + ".json"))
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("retained CAS integrity failure")
        return raw

    receipt = document("ingestion-readiness-result.json")
    entries = [
        json.loads(s)
        for s in read(capture_root / "ingestion-readiness.jsonl").decode().splitlines()
    ]
    auth = document("validation-authorization.json")
    collector = document("collector.json")
    compose = document("compose.json")
    collection = auth["plan"]["collection"]
    queries = collection["actual_queries"]
    command = compose["services"]["prometheus"]["command"]
    # First reproduce the historical assessment with its original binding.
    historical = ie.verify(
        collection["ingestion"],
        entries=entries,
        occurrence=receipt["assessment"]["occurrence"],
        incident_id=None,
        queries=queries,
        read_bytes=cas,
        collector=collector,
        command=command,
        requirements=receipt["requirements"],
    )
    if historical != receipt["assessment"]:
        raise ValueError("historical assessment does not reproduce")
    application = (
        json.loads(read(Path(application_config).resolve()))
        if application_config
        else None
    )
    context_doc = json.loads(read(Path(context).resolve())) if context else {}
    retained_context = {}
    for name in (
        "baseline-settlement.json",
        "baseline-ready.json",
        "episodes/N4/preparation.json",
        "runtime/snapshot-baseline.json",
    ):
        if (capture_root / name).is_file():
            retained_context[name] = document(name)
    log_observations = []
    index = capture_root / "raw-readiness.jsonl"
    if index.is_file():
        from urllib.parse import urlsplit

        for entry in map(json.loads, read(index).decode().splitlines()):
            if not urlsplit(entry["url"]).path.endswith("/_search"):
                continue
            payload = json.loads(cas(entry["response_object_sha256"]))
            hits = payload.get("hits", {})
            sources = [hit.get("_source", {}) for hit in hits.get("hits", [])]
            stamps = sorted(s["@timestamp"] for s in sources if "@timestamp" in s)
            log_observations.append(
                dict(
                    requested_at=entry["requested_at"],
                    response_sha256=entry["response_object_sha256"],
                    query=entry.get("json_body"),
                    returned_records=len(sources),
                    total=hits.get("total"),
                    first_record=stamps[0] if stamps else None,
                    last_record=stamps[-1] if stamps else None,
                    truncated=entry.get("truncated"),
                    service_names=sorted(
                        {
                            s.get("resource", {}).get("service.name", "UNKNOWN")
                            for s in sources
                        }
                    ),
                )
            )
    retained_context["logs"] = log_observations
    retained_context["spans"] = "NO_CORRELATED_TRACE_DETAIL_IN_PREPARATION_CAPTURE"
    retained_context["otlp_start_time"] = "NOT_RETAINED"
    results = []
    for req in receipt["requirements"]:
        states, diagnostics, bodies = {}, {}, {}
        for selector, start, params in ie.expected_reads(
            req["query"],
            req["start"],
            req["end"],
            version=collection["ingestion"]["version"],
        ):
            matches = [e for e in entries if e["params"] == params]
            if len(matches) != 1:
                states[selector] = "MISSING_RAW_SAMPLE_QUERY"
                continue
            body = json.loads(cas(matches[0]["response_object_sha256"]))
            detail = support.assess(
                body,
                selector,
                start,
                req["end"],
                support.profile(collector, selector, application),
                query_start=req["start"],
                inner_seconds=req["start"] - start,
            )
            detail["raw_response_sha256"] = matches[0]["response_object_sha256"]
            detail["request"] = dict(
                params=params,
                requested_at=matches[0]["requested_at"],
                received_at=matches[0]["received_at"],
            )
            diagnostics[selector] = detail
            states[selector] = detail["state"]
            if detail["state"] in {"EMPTY", "FRESH_COVERED"}:
                bodies[selector] = body
        correspondence = support.correspondence(bodies)
        # Keep the actual aggregate response separate from source sufficiency.
        normal = [
            e
            for e in entries
            if e["params"]
            == dict(query=req["query"], start=req["start"], end=req["end"], step=10)
        ]
        actual = [
            dict(
                response_sha256=e["response_object_sha256"],
                body=json.loads(cas(e["response_object_sha256"])),
            )
            for e in normal
        ]
        results.append(
            dict(
                requirement=req,
                selectors=states,
                sample_diagnostics=diagnostics,
                correspondence_reasons=correspondence,
                coverage="INVALID_SAMPLE_EVIDENCE"
                if correspondence
                else ie.query_coverage(req["query"], states),
                actual_query_results=actual,
                business_health="NOT_EVALUATED",
                negative_control_qualification="NOT_ESTABLISHED",
            )
        )
    root = Path(__file__).resolve().parents[2]
    source_paths = [
        Path(__file__).resolve(),
        root / "scripts/product_v050/ingestion_evidence.py",
        root / "scripts/product_v050/sampling_support.py",
    ]
    source_hashes = {
        str(p.relative_to(root)): hashlib.sha256(read(p)).hexdigest()
        for p in source_paths
    }
    result = dict(
        version=support.VERSION,
        mode="SEEN_DEVELOPMENT_REPLAY",
        started_at=started_at,
        elapsed_seconds=time.monotonic() - started,
        head=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        source_sha256=source_hashes,
        historical_assessment_reproduced=True,
        historical_passed=historical["passed"],
        formal_pass=False,
        promotion_eligible=False,
        query_results=results,
        context=dict(
            retained=retained_context,
            supplemental=context_doc,
            lifecycle_causality="UNKNOWN_WITHOUT_CORRELATED_BIRTH_OR_RESTART_EVIDENCE",
        ),
        context_note="Configuration restoration is not telemetry health; context never fills missing samples",
        original_binding=collection["ingestion"],
        input_sha256=hashes,
    )
    # Freshly verify every read object before publication; no mutable store open.
    if any(
        hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in hashes.items()
    ):
        raise ValueError("input changed during replay")
    output.mkdir(parents=True, mode=0o700)
    target = output / "replay.json"
    with target.open("x") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
    target.chmod(0o600)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--objects", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--application-config", type=Path)
    parser.add_argument("--context", type=Path)
    args = parser.parse_args()
    result = replay(**vars(args))
    print(
        json.dumps(
            dict(
                mode=result["mode"],
                queries=len(result["query_results"]),
                supported=sum(
                    q["coverage"].startswith("SUPPORTED")
                    for q in result["query_results"]
                ),
                formal_pass=False,
                output=str(args.output / "replay.json"),
            )
        )
    )


if __name__ == "__main__":
    main()
