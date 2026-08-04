"""Offline-only commands for the frozen Phase 5B protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ecomsre.phase5b.contracts import ExecutionSchedule
from ecomsre.phase5b.dry_run import build_mock_dry_run_report
from ecomsre.phase5b.freeze import verify_freeze_manifest
from ecomsre.phase5b.hidden_pack import canonical_json_bytes, write_canonical_json
from ecomsre.phase5b.protocol import (
    load_analysis_plan,
    load_seed_policy,
    load_strict_json,
    load_suite_registry,
)
from ecomsre.phase5b.registry import (
    validate_ablation_registry,
    validate_hidden_pack_contract,
    validate_metrics_registry,
)
from ecomsre.phase5b.schedule import build_execution_schedule


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_ROOT = PROJECT_ROOT / "config/phase5b"
DEFAULT_DRY_RUN_REPORT = PROJECT_ROOT / "artifacts/phase5b/mock-protocol-dry-run.json"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _expected_schedule() -> ExecutionSchedule:
    return build_execution_schedule(
        load_suite_registry(CONFIG_ROOT / "suite-registry.v1.json"),
        load_seed_policy(CONFIG_ROOT / "seed-policy.v1.json"),
    )


def verify_schedule() -> tuple[ExecutionSchedule, str]:
    path = CONFIG_ROOT / "execution-schedule.v1.json"
    observed = load_strict_json(path, ExecutionSchedule)
    expected = _expected_schedule()
    if observed != expected or path.read_bytes() != canonical_json_bytes(expected.model_dump(mode="json")):
        raise ValueError("committed execution schedule differs from the frozen generator")
    return observed, _sha256(path.read_bytes())


def verify_protocol() -> dict[str, object]:
    suite = load_suite_registry(CONFIG_ROOT / "suite-registry.v1.json")
    seeds = load_seed_policy(CONFIG_ROOT / "seed-policy.v1.json")
    analysis = load_analysis_plan(CONFIG_ROOT / "analysis-plan.v1.json")
    validate_hidden_pack_contract(CONFIG_ROOT / "hidden-pack-contract.v1.json")
    validate_metrics_registry(CONFIG_ROOT / "metrics-registry.v1.json")
    ablations = validate_ablation_registry(CONFIG_ROOT / "ablation-registry.v1.json")
    schedule, schedule_sha = verify_schedule()
    return {
        "status": "VERIFIED",
        "evaluation_version": suite.evaluation_version,
        "template_count": suite.template_count,
        "seed_count_per_template": seeds.seed_count_per_template,
        "variant_count": 3,
        "main_run_count": schedule.run_count,
        "ablation_run_count": ablations["ablation_run_count"],
        "primary_population": analysis["primary_population"],
        "schedule_sha256": schedule_sha,
        "provider_calls": 0,
    }


def write_dry_run_report(path: Path) -> dict[str, object]:
    report = build_mock_dry_run_report()
    write_canonical_json(path, report)
    return report


def verify_dry_run_report(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("mock dry-run report must be a regular file")
    expected = canonical_json_bytes(build_mock_dry_run_report())
    if path.read_bytes() != expected:
        raise ValueError("mock dry-run report does not match the deterministic protocol")
    return _sha256(expected)


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight")
    subparsers.add_parser("protocol-verify")
    subparsers.add_parser("schedule")
    dry_run = subparsers.add_parser("dry-run")
    dry_run.add_argument("--output", type=Path, default=DEFAULT_DRY_RUN_REPORT)
    dry_verify = subparsers.add_parser("dry-run-verify")
    dry_verify.add_argument("--report", type=Path, default=DEFAULT_DRY_RUN_REPORT)
    arguments = parser.parse_args(argv)
    if arguments.command == "preflight":
        manifest_path = CONFIG_ROOT / "freeze-manifest.v1.json"
        manifest = verify_freeze_manifest(PROJECT_ROOT, manifest_path)
        _print(
            {
                "status": "FROZEN_PATHS_VERIFIED",
                "frozen_file_count": len(manifest.frozen_files),
                "manifest_sha256": _sha256(manifest_path.read_bytes()),
                "provider_calls": 0,
            }
        )
        return 0
    if arguments.command == "protocol-verify":
        _print(verify_protocol())
        return 0
    if arguments.command == "schedule":
        schedule, digest = verify_schedule()
        _print(
            {
                "status": "SCHEDULE_VERIFIED",
                "run_count": schedule.run_count,
                "pairing_unit_count": schedule.pairing_unit_count,
                "schedule_sha256": digest,
                "provider_calls": 0,
            }
        )
        return 0
    if arguments.command == "dry-run":
        report = write_dry_run_report(arguments.output)
        _print(
            {
                "status": report["report_type"],
                "evidence_class": report["evidence_class"],
                "report": str(arguments.output),
                "run_count": report["run_count"],
                "provider_calls": 0,
            }
        )
        return 0
    digest = verify_dry_run_report(arguments.report)
    _print(
        {
            "status": "VERIFIED",
            "report": str(arguments.report),
            "report_sha256": digest,
            "provider_calls": 0,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
