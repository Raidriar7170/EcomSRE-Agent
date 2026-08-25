#!/usr/bin/env python3
"""Build fresh v2.3.2 observer bytes and truth-shard bindings."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.dta_v2.v22.replay import ReplayCaptureV22
from build_dta_v231_successor_fixed_set import (
    build as build_predecessor_shape,
)


_SALT = "dta-v232-total-interpretation-successor-a-9d31c642"


def _service(case_id: str, slot: str) -> str:
    digest = hashlib.sha256(f"{_SALT}:{case_id}:{slot}".encode()).hexdigest()
    return f"svc-{digest[:10]}"


def _shift_timestamp(value: str) -> str:
    return (datetime.fromisoformat(value) + timedelta(days=1)).isoformat()


def _translate_case(
    *,
    source: dict[str, Any],
    ordinal: int,
    counterfactual_target: str | None,
    counterfactual_role: str | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    case_id = f"vx-{ordinal:03d}"
    old_services = tuple(source["candidate_services"])
    service_map: dict[str, str] = {
        service: _service(case_id, f"slot-{index}")
        for index, service in enumerate(old_services)
    }
    if counterfactual_target is not None and counterfactual_role is not None:
        peer = next(item for item in old_services if item != counterfactual_target)
        low, high = sorted(service_map.values())
        mapped_target, mapped_peer = (
            (low, high)
            if counterfactual_role == "TARGET_LOW"
            else (high, low)
        )
        service_map = {
            counterfactual_target: mapped_target,
            peer: mapped_peer,
        }
    services = tuple(sorted(service_map.values()))
    capture = deepcopy(source["capture"])
    capture["captured_at"] = _shift_timestamp(capture["captured_at"])
    for metric in capture["metrics"]:
        metric["service"] = service_map[metric["service"]]
        metric["sample_count"] += 1
        metric["window_started_at"] = _shift_timestamp(
            metric["window_started_at"]
        )
        metric["window_ended_at"] = _shift_timestamp(metric["window_ended_at"])
    for log in capture["logs"]:
        log["observed_at"] = _shift_timestamp(log["observed_at"])
        log["service"] = service_map[log["service"]]
        log["message"] = f"successor v232 {log['message']}"
    old_id = source["case_id"]
    if old_id == "vx-115":
        target = service_map[min(old_services)]
        capture["logs"].append(
            {
                "schema_version": "dta-v22.log-record.v1",
                "observed_at": capture["captured_at"],
                "service": target,
                "severity": "ERROR",
                "message": "successor v232 configuration setting rejected by parser",
            }
        )
    if old_id == "vx-122":
        target = service_map[min(old_services)]
        capture["logs"].append(
            {
                "schema_version": "dta-v22.log-record.v1",
                "observed_at": capture["captured_at"],
                "service": target,
                "severity": "ERROR",
                "message": "successor v232 memory pressure increased without resource corroboration",
            }
        )
    for trace in capture["traces"]:
        trace["observed_at"] = _shift_timestamp(trace["observed_at"])
        trace["service_path"] = [service_map[item] for item in trace["service_path"]]
        trace["service"] = service_map[trace["service"]]
        trace["parent_service"] = service_map[trace["parent_service"]]
        trace["operation"] = f"v232-{trace['operation']}"
        trace["duration_ms"] += 3.0
    for runtime in capture["runtime"]:
        runtime["service"] = service_map[runtime["service"]]
    for resource in capture["resources"]:
        resource["service"] = service_map[resource["service"]]
        for sample in resource["samples"]:
            sample["cpu_percent"] += 0.125
            sample["memory_bytes"] += 4_000_000
    for index, change in enumerate(capture["changes"]):
        change["service"] = service_map[change["service"]]
        change["observed_at"] = _shift_timestamp(change["observed_at"])
        change["opaque_change_id"] = (
            "chg_"
            + hashlib.sha256(
                f"{_SALT}:{case_id}:change:{index}".encode()
            ).hexdigest()[:16]
        )
        change["revision_digest"] = hashlib.sha256(
            f"{_SALT}:{case_id}:revision:{index}".encode()
        ).hexdigest()

    validated_capture = ReplayCaptureV22.model_validate_json(json.dumps(capture))
    topology = tuple(
        sorted(
            tuple(sorted((service_map[left], service_map[right])))
            for left, right in source["topology_edges"]
        )
    )
    observer = {
        "case_id": case_id,
        "candidate_services": services,
        "topology_edges": topology,
        "capture": validated_capture.model_dump(mode="json"),
    }
    return {
        **observer,
        "source_bytes_sha256": semantic_sha256_v22(observer),
    }, service_map


def build() -> tuple[
    dict[str, Any],
    tuple[dict[str, Any], ...],
    dict[str, Any],
    dict[str, Any],
]:
    predecessor_cases, predecessor_truth, predecessor_views = (
        build_predecessor_shape()
    )
    cases: list[dict[str, Any]] = []
    truth_records: list[dict[str, Any]] = []
    views: list[dict[str, Any]] = []
    strata: dict[str, list[str]] = {}
    for index, source in enumerate(predecessor_cases["cases"]):
        ordinal = 201 + index
        source_truth = predecessor_truth[index]
        case, service_map = _translate_case(
            source=source,
            ordinal=ordinal,
            counterfactual_target=source_truth["evaluator_truth"][
                "expected_root_service"
            ],
            counterfactual_role=source_truth["counterfactual_target_role"],
        )
        cases.append(case)
        truth = deepcopy(predecessor_truth[index])
        evaluator = truth["evaluator_truth"]
        evaluator["case_id"] = case["case_id"]
        if evaluator["expected_root_service"] is not None:
            evaluator["expected_root_service"] = service_map[
                evaluator["expected_root_service"]
            ]
        if evaluator["counterfactual_pair_id"] is not None:
            predecessor_pair = evaluator["counterfactual_pair_id"].split("-")[-1]
            evaluator["counterfactual_pair_id"] = f"v232-cf-{predecessor_pair}"
        role = truth["counterfactual_target_role"]
        if role is not None:
            target = evaluator["expected_root_service"]
            truth["counterfactual_target_role"] = (
                "TARGET_LOW" if target == min(case["candidate_services"]) else "TARGET_HIGH"
            )
        truth_records.append(truth)
        view = deepcopy(predecessor_views["views"][index])
        view["case_id"] = case["case_id"]
        views.append(view)
        strata.setdefault(truth["admission_stratum"], []).append(case["case_id"])

    old_hashes = {
        item["source_bytes_sha256"] for item in predecessor_cases["cases"]
    }
    new_hashes = {item["source_bytes_sha256"] for item in cases}
    if len(new_hashes) != 24 or old_hashes.intersection(new_hashes):
        raise ValueError("v2.3.2 observer bytes are not a fresh 24-case set")
    old_ids = {item["case_id"] for item in predecessor_cases["cases"]}
    if old_ids.intersection({item["case_id"] for item in cases}):
        raise ValueError("v2.3.2 case IDs overlap the consumed successor")
    old_services = {
        service
        for item in predecessor_cases["cases"]
        for service in item["candidate_services"]
    }
    new_services = {
        service for item in cases for service in item["candidate_services"]
    }
    if old_services.intersection(new_services):
        raise ValueError("v2.3.2 opaque service IDs overlap the consumed successor")

    return (
        {
            "schema_version": "dta-v232.evaluation-case-set.v1",
            "freeze_id": "dta-v232-total-interpretation-freeze-20260826-a",
            "cases": cases,
        },
        tuple(truth_records),
        {
            "schema_version": "dta-v232.ontology-view-set.v1",
            "views": views,
        },
        {
            "schema_version": "dta-v232.evaluation-strata.v1",
            "strata": [
                {"name": name, "case_ids": ids}
                for name, ids in sorted(strata.items())
            ],
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    cases, truth_records, views, strata = build()
    args.output_root.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("cases.json", cases),
        ("ontology-views.json", views),
        ("strata.json", strata),
    ):
        (args.output_root / name).write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
    truth_root = args.output_root / "truth"
    truth_root.mkdir(parents=True, exist_ok=True)
    bindings = []
    for record in truth_records:
        case_id = record["evaluator_truth"]["case_id"]
        payload = {
            "schema_version": "dta-v232.evaluation-truth-shard.v1",
            "record": record,
        }
        raw = json.dumps(payload, indent=2, sort_keys=False) + "\n"
        (truth_root / f"{case_id}.json").write_text(raw, encoding="utf-8")
        bindings.append(
            {
                "case_id": case_id,
                "path": f"truth/{case_id}.json",
                "sha256": hashlib.sha256(raw.encode()).hexdigest(),
            }
        )
    truth_index_payload = {
        "schema_version": "dta-v232.evaluation-truth-index.v1",
        "shards": bindings,
    }
    truth_index = {
        **truth_index_payload,
        "index_sha256": semantic_sha256_v22(truth_index_payload),
    }
    (args.output_root / "truth.json").write_text(
        json.dumps(truth_index, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
