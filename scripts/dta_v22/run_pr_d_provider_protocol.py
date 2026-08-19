"""Run the bounded DTA v2.2 PR-D Provider protocol-only gate once."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Any

from ecomsre.dta_v2.v22.controller_modes import (
    PRIMARY_MODEL_V22,
    probe_provider_output_mode_v22,
)
from ecomsre.dta_v2.v22.controller_provider import (
    OpenAICompatibleControllerProviderV22,
)
from ecomsre.dta_v2.v22.protocol_suite import (
    ProviderProtocolCapabilityReportV22,
    run_provider_protocol_capability_suite_v22,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.model.gateway import OpenAICompatibleConfig


_ENVIRONMENT_NAMES = frozenset(
    {
        "ECOMSRE_LLM_BASE_URL",
        "ECOMSRE_LLM_API_KEY",
        "ECOMSRE_LLM_MODEL",
    }
)
_PRIVATE_EVIDENCE_ROOT = (
    Path.home() / ".ecomsre" / "private" / "dta-v22-p0-master-v1"
)
_PUBLIC_SUMMARY_RELATIVE = Path(
    "docs/analysis/dta-v22-pr-d-provider-protocol-summary.json"
)
_FORMAL_MIN_REQUEST_INTERVAL_SECONDS = 3.0


def _parse_provider_env(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Provider env must be a regular non-symlink file")
    details = path.stat()
    if stat.S_IMODE(details.st_mode) != 0o600 or details.st_uid != os.getuid():
        raise ValueError("Provider env requires current-user ownership and mode 0600")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        value = raw_value.strip()
        if separator != "=" or key not in _ENVIRONMENT_NAMES:
            raise ValueError("Provider env contains unsupported syntax or key")
        if any(token in value for token in ("$(", "${", "`")):
            raise ValueError("Provider env contains shell expansion")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not value or key in values:
            raise ValueError("Provider env contains an empty or duplicate value")
        values[key] = value
    if set(values) != _ENVIRONMENT_NAMES:
        raise ValueError("Provider env must contain exactly three variables")
    if values["ECOMSRE_LLM_MODEL"] != PRIMARY_MODEL_V22:
        raise RuntimeError("BLOCKED_DTA_V22_MODEL_CONTINUITY")
    return values


def _git_text(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(root), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def _validate_output_paths(
    *,
    private_report: Path,
    public_summary: Path,
    repository_root: Path,
    implementation_commit: str,
    private_root: Path = _PRIVATE_EVIDENCE_ROOT,
) -> tuple[Path, Path]:
    root = repository_root.resolve(strict=True)
    expected_private = private_root.resolve(strict=False)
    private_path = private_report.resolve(strict=False)
    public_path = public_summary.resolve(strict=False)
    expected_name = f"dta-v22-pr-d-provider-protocol-{implementation_commit[:12]}.json"
    if private_path.parent != expected_private or private_path.name != expected_name:
        raise ValueError("private Provider report path differs from exact Goal root")
    if private_path.is_relative_to(root):
        raise ValueError("private Provider report cannot be written inside repository")
    if private_path.exists() or private_path.is_symlink():
        raise FileExistsError("private Provider report is create-once")
    expected_public = (root / _PUBLIC_SUMMARY_RELATIVE).resolve(strict=False)
    if public_path != expected_public:
        raise ValueError("public summary path differs from PR-D contract")
    if not public_path.parent.resolve(strict=True).is_relative_to(root):
        raise ValueError("public summary parent escapes repository")
    if public_path.exists() or public_path.is_symlink():
        raise FileExistsError("public summary is create-once")
    return private_path, public_path


def _prepare_private_parent(path: Path, *, repository_root: Path) -> None:
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("private report parent must be a regular directory")
    parent.chmod(0o700)
    if parent.stat().st_uid != os.getuid():
        raise ValueError("private report parent owner differs")
    resolved = path.resolve(strict=False)
    if resolved.is_relative_to(repository_root.resolve(strict=True)):
        raise ValueError("private Provider report cannot be written inside repository")
    if path.exists() or path.is_symlink():
        raise FileExistsError("private Provider report is create-once")


def _write_pair_create_once(
    *,
    private_path: Path,
    private_text: str,
    public_path: Path,
    public_text: str,
) -> None:
    private_descriptor = os.open(
        private_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        public_descriptor = os.open(
            public_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o644,
        )
    except Exception:
        os.close(private_descriptor)
        private_path.unlink(missing_ok=True)
        raise
    with os.fdopen(private_descriptor, "w", encoding="utf-8") as private_handle:
        private_handle.write(private_text)
        private_handle.flush()
        os.fsync(private_handle.fileno())
    with os.fdopen(public_descriptor, "w", encoding="utf-8") as public_handle:
        public_handle.write(public_text)
        public_handle.flush()
        os.fsync(public_handle.fileno())
    private_path.chmod(0o600)
    public_path.chmod(0o644)


def _public_summary(
    *,
    report: ProviderProtocolCapabilityReportV22,
    implementation_commit: str,
    implementation_tree: str,
    executed_at: str,
    private_evidence_raw_sha256: str,
    private_evidence_semantic_sha256: str,
) -> dict[str, Any]:
    response_digests = sorted(
        transition.provider_turn.raw_response_sha256
        for transition in report.transitions
    )
    categories = Counter(item.category.value for item in report.transitions)
    arms = Counter(item.arm.value for item in report.transitions)
    payload: dict[str, Any] = {
        "schema_version": "dta-v22-pr-d-provider-protocol-summary.v1",
        "goal_version": "dta-v22-p0-master-v1",
        "execution_id": (
            f"dta-v22-pr-d-protocol-{report.report_sha256[:12]}"
        ),
        "implementation_commit": implementation_commit,
        "implementation_tree": implementation_tree,
        "executed_at": executed_at,
        "model": report.model,
        "selected_mode": report.selected_mode.value,
        "controller_schema_sha256": report.provider_probe.controller_schema_sha256,
        "provider_probe_report_sha256": report.provider_probe.report_sha256,
        "provider_protocol_report_sha256": report.report_sha256,
        "private_evidence_raw_sha256": private_evidence_raw_sha256,
        "private_evidence_semantic_sha256": private_evidence_semantic_sha256,
        "private_evidence_location_class": "DTA_V22_PRIVATE_ROOT",
        "controller_identity_sha256s": list(report.controller_identity_sha256s),
        "transition_count": report.transition_count,
        "transition_category_counts": dict(sorted(categories.items())),
        "controller_arm_counts": dict(sorted(arms.items())),
        "first_pass_accepted_count": report.first_pass_accepted_count,
        "first_pass_protocol_acceptance": report.first_pass_protocol_acceptance,
        "post_correction_accepted_count": report.post_correction_accepted_count,
        "post_correction_protocol_acceptance": (
            report.post_correction_protocol_acceptance
        ),
        "correction_count": report.correction_count,
        "correction_rate": report.correction_rate,
        "invalid_dispatches": report.invalid_dispatches,
        "provider_probe_calls": report.provider_probe.provider_calls,
        "provider_protocol_calls": report.provider_calls,
        "input_tokens": report.input_tokens,
        "output_tokens": report.output_tokens,
        "total_tokens": report.total_tokens,
        "response_digest_set_sha256": semantic_sha256_v22(response_digests),
        "provider_gate_eligible": report.provider_gate_eligible,
        "terminal": report.terminal.value,
        "raw_provider_content_published": False,
        "agent_read_dispatches_executed": 0,
        "agent_write_calls": 0,
        "runbook_executions": 0,
        "docker_calls": 0,
    }
    return {
        **payload,
        "summary_sha256": semantic_sha256_v22(payload),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument("--private-report", type=Path, required=True)
    parser.add_argument("--public-summary", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = args.repository_root.resolve(strict=True)
    if _git_text(root, "status", "--porcelain"):
        raise ValueError("formal Provider protocol run requires a clean worktree")
    implementation_commit = _git_text(root, "rev-parse", "HEAD")
    implementation_tree = _git_text(root, "rev-parse", "HEAD^{tree}")
    private_path, public_path = _validate_output_paths(
        private_report=args.private_report,
        public_summary=args.public_summary,
        repository_root=root,
        implementation_commit=implementation_commit,
    )
    _prepare_private_parent(private_path, repository_root=root)
    values = _parse_provider_env(args.provider_env)
    config = OpenAICompatibleConfig.from_environment(values)
    if config is None:
        raise RuntimeError("BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE")
    provider = OpenAICompatibleControllerProviderV22(
        config=config,
        timeout_seconds=60.0,
        max_completion_tokens=256,
        min_request_interval_seconds=_FORMAL_MIN_REQUEST_INTERVAL_SECONDS,
    )
    probe = probe_provider_output_mode_v22(probe=provider.probe_output_mode)
    report = run_provider_protocol_capability_suite_v22(
        provider_probe=probe,
        complete=provider.complete_controller_turn,
    )
    if not report.provider_gate_eligible:
        raise RuntimeError("BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE")
    executed_at = datetime.now(UTC).isoformat()
    private_payload: dict[str, Any] = {
        "schema_version": "dta-v22-pr-d-private-provider-protocol-evidence.v2",
        "implementation_commit": implementation_commit,
        "implementation_tree": implementation_tree,
        "executed_at": executed_at,
        "report": report.model_dump(mode="json"),
    }
    private_payload["evidence_sha256"] = semantic_sha256_v22(private_payload)
    private_text = _canonical_json(private_payload)
    private_raw_sha256 = hashlib.sha256(private_text.encode("utf-8")).hexdigest()
    summary = _public_summary(
        report=report,
        implementation_commit=implementation_commit,
        implementation_tree=implementation_tree,
        executed_at=executed_at,
        private_evidence_raw_sha256=private_raw_sha256,
        private_evidence_semantic_sha256=private_payload["evidence_sha256"],
    )
    _write_pair_create_once(
        private_path=private_path,
        private_text=private_text,
        public_path=public_path,
        public_text=_canonical_json(summary),
    )
    print(
        json.dumps(
            {
                "execution_id": summary["execution_id"],
                "selected_mode": summary["selected_mode"],
                "first_pass_protocol_acceptance": summary[
                    "first_pass_protocol_acceptance"
                ],
                "post_correction_protocol_acceptance": summary[
                    "post_correction_protocol_acceptance"
                ],
                "invalid_dispatches": summary["invalid_dispatches"],
                "provider_protocol_calls": summary["provider_protocol_calls"],
                "terminal": summary["terminal"],
                "summary_sha256": summary["summary_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
