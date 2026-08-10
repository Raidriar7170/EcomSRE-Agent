"""Verify that the frozen public RCA100 report matches evaluator aggregate."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ecomsre.evidence.hashes import canonical_json_bytes, sha256_file
from ecomsre_rca100.lifecycle import PrivateRoots, load_strict_json
from ecomsre_rca100.public_projection import scan_public_artifacts


def main() -> None:
    repository = Path(__file__).resolve().parents[2]
    roots = PrivateRoots.from_environment(os.environ)
    roots.validate(repository_root=repository, create=False)
    aggregate = load_strict_json(roots.evaluator / "results" / "aggregate.json")
    report_path = (
        repository
        / "docs"
        / "results"
        / "rca100-metrics-arbitration-v1-final.json"
    )
    report = load_strict_json(report_path)
    if not isinstance(aggregate, dict) or not isinstance(report, dict):
        raise ValueError("RCA100 report verification inputs are invalid")
    if canonical_json_bytes(report.get("primary")) != canonical_json_bytes(
        aggregate.get("root")
    ):
        raise ValueError("RCA100 public primary result differs from canonical aggregate")
    if canonical_json_bytes(report.get("secondary_pair")) != canonical_json_bytes(
        aggregate.get("pair")
    ):
        raise ValueError("RCA100 public pair result differs from canonical aggregate")
    if canonical_json_bytes(report.get("m3")) != canonical_json_bytes(
        aggregate.get("m3")
    ):
        raise ValueError("RCA100 public M3 result differs from canonical aggregate")
    paths = tuple(
        (
            repository / "docs" / "results"
        ).glob("rca100-metrics-arbitration-v1*")
    ) + tuple(
        (
            repository
            / "docs"
            / "review-evidence"
            / "rca100-metrics-arbitration-v1"
        ).glob("*")
    )
    findings = scan_public_artifacts(paths)
    if findings:
        raise ValueError(f"RCA100 public leakage scan failed: {findings}")
    print(
        json.dumps(
            {
                "canonical_verification": "PASS",
                "public_leakage_scan": "PASS",
                "final_report_sha256": sha256_file(report_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
