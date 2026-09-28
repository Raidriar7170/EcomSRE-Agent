"""Recompute one retained engineering round into a fresh independent directory."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from scripts.product_v050 import ingestion_evidence as ie, sampling_support as ss


def replay(root, number, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists() or root == output or root in output.parents:
        raise ValueError("fresh separate replay output required")
    hashes = {}

    def read(path):
        raw = path.read_bytes()
        hashes[str(path)] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    original = read(root / f"round-{number}/result.json")
    application = read(root / "application-support.json")
    collector = read(root / "collector.json")

    def proof(h):
        p = root / "objects/sha256" / h[:2] / (h + ".json")
        raw = p.read_bytes()
        hashes[str(p)] = hashlib.sha256(raw).hexdigest()
        return raw

    ss.verify_default_evidence(application, proof)

    def response(name, params):
        p = root / f"round-{number}" / name
        meta = read(p)
        raw = p.with_suffix(".body").read_bytes()
        h = hashlib.sha256(raw).hexdigest()
        hashes[str(p.with_suffix(".body"))] = h
        if h != meta["body_sha256"] or meta["params"] != params:
            raise ValueError("retained request/response binding differs")
        if meta["status"] != 200 or meta["error"] or meta["truncated"]:
            return None
        return json.loads(raw)

    results = []
    for index, query in enumerate(original["queries"]):
        start, end = original["start"], original["end"]
        q = query["query"]
        actual = response(
            f"{index:02}-query.json", dict(query=q, start=start, end=end, step=10)
        )
        states = {}
        details = {}
        bodies = {}
        for j, (selector, left, params) in enumerate(
            ie.expected_reads(q, start, end, version=ss.VERSION)
        ):
            body = response(f"{index:02}-raw-{j}.json", params)
            if body is None:
                states[selector] = "HTTP_EVIDENCE_UNAVAILABLE"
                continue
            detail = ss.assess(
                body,
                selector,
                left,
                end,
                ss.profile(collector, selector, application),
                query_start=start,
                inner_seconds=start - left,
            )
            states[selector] = detail["state"]
            details[selector] = detail
            if detail["state"] in {"FRESH_COVERED", "EMPTY"}:
                bodies[selector] = body
        problems = ss.correspondence(bodies)
        results.append(
            dict(
                key=query["key"],
                query=q,
                actual_query_available=actual is not None,
                states=states,
                diagnostics=details,
                correspondence=problems,
                coverage="INVALID_SAMPLE_EVIDENCE"
                if problems or actual is None
                else ie.query_coverage(q, states),
            )
        )
    if any(
        hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in hashes.items()
    ):
        raise ValueError("input changed during replay")
    output.mkdir(mode=0o700, parents=True)
    result = dict(
        mode="SEEN_DEVELOPMENT_REPLAY",
        round=number,
        original_diagnostics_reproduced=results == original["queries"],
        query_results=results,
        input_sha256=hashes,
        source_sha256={
            str(Path(module.__file__).resolve()): hashlib.sha256(
                Path(module.__file__).read_bytes()
            ).hexdigest()
            for module in (ie, ss)
        }
        | {
            str(Path(__file__).resolve()): hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest()
        },
        replayed_at=datetime.now(UTC).isoformat(),
        captured_source_sha256=original.get("source_sha256", {}),
        formal_pass=False,
        promotion_eligible=False,
    )
    p = output / "replay.json"
    p.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    p.chmod(0o600)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True)
    p.add_argument("--round", type=int, required=True, choices=[1, 2, 3])
    p.add_argument("--output", required=True)
    a = p.parse_args()
    result = replay(a.root, a.round, a.output)
    print(
        json.dumps(
            {
                "round": a.round,
                "original_diagnostics_reproduced": result[
                    "original_diagnostics_reproduced"
                ],
                "supported": sum(
                    q["coverage"].startswith("SUPPORTED")
                    for q in result["query_results"]
                ),
            }
        )
    )
