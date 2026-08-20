"""Run the one preregistered DTA v2.2 PR-D Provider Boundary v4 campaign."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time
from typing import Any, Literal

from ecomsre.dta_v2.v22.controller_modes import (
    PRIMARY_MODEL_V22,
    ProviderOutputModeV22,
)
from ecomsre.dta_v2.v22.controller_provider import ProviderHttpErrorV22
from ecomsre.dta_v2.v22.provider_boundary_v4 import (
    build_provider_probe_request_v4,
)
from ecomsre.dta_v2.v22.provider_protocol_v4 import (
    OpenAICompatibleProviderBoundaryV4,
    ProviderBoundaryTurnV4,
    ProviderModeProbeAbortV4,
)
from ecomsre.dta_v2.v22.protocol_suite_v4 import (
    ProviderProtocolReplicateReportV4,
    run_protocol_replicate_v4,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.model.gateway import OpenAICompatibleConfig
from scripts.ci.verify_dta_v22_pr_d_v4 import (
    load_and_verify_manifest_v4,
    verify_private_execution_v4,
    verify_pre_execution_admission_v4,
)
from scripts.dta_v22.run_pr_d_provider_protocol import _parse_provider_env


_PRIVATE_ROOT = (
    Path.home()
    / ".ecomsre"
    / "private"
    / "dta-v22-p0-master-v1"
    / "pr-d"
    / "provider-boundary-v4"
)
_MANIFEST = Path("config/dta-v22/provider-gate/pr-d-provider-boundary-v4-manifest.json")
_PUBLIC = {
    "A": Path("docs/analysis/dta-v22-pr-d-provider-boundary-v4-replicate-a.json"),
    "B": Path("docs/analysis/dta-v22-pr-d-provider-boundary-v4-replicate-b.json"),
    "campaign": Path("docs/analysis/dta-v22-pr-d-provider-boundary-v4-campaign.json"),
}
_INTER_REPLICATE_COOLDOWN_SECONDS = 120.0


def _canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(root), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _dirty_paths(root: Path) -> set[str]:
    output = subprocess.run(
        ("git", "-C", str(root), "status", "--porcelain=v1", "-z"),
        check=True,
        capture_output=True,
    ).stdout
    paths: set[str] = set()
    for raw in output.split(b"\0"):
        if not raw:
            continue
        entry = raw.decode("utf-8", errors="strict")
        if len(entry) < 4:
            raise RuntimeError("BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE")
        paths.add(entry[3:])
    return paths


def _verify_exact_execution_tree(
    *,
    root: Path,
    commit: str,
    tree: str,
    allowed_dirty: set[str],
) -> None:
    if (
        _git(root, "rev-parse", "HEAD") != commit
        or _git(root, "rev-parse", "HEAD^{tree}") != tree
        or _dirty_paths(root) != allowed_dirty
    ):
        raise RuntimeError("BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE")


def _prepare_private_root(root: Path, repository_root: Path) -> None:
    expected = (
        ".ecomsre",
        "private",
        "dta-v22-p0-master-v1",
        "pr-d",
        "provider-boundary-v4",
    )
    if root.parts[-len(expected) :] != expected or root.is_relative_to(repository_root):
        raise ValueError("v4 private evidence root differs from Goal")
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    root.chmod(0o700)
    detail = root.lstat()
    if not stat.S_ISDIR(detail.st_mode) or detail.st_uid != os.getuid():
        raise ValueError("v4 private evidence root authority differs")


def _write_once(path: Path, value: object, *, mode: int) -> tuple[str, str]:
    text = _canonical_json(value)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(mode)
    observed = path.read_text(encoding="utf-8")
    if observed != text:
        raise OSError("persisted v4 evidence differs")
    raw = hashlib.sha256(observed.encode("utf-8")).hexdigest()
    semantic = semantic_sha256_v22(json.loads(observed))
    return raw, semantic


def _private_replicate(
    *,
    report: ProviderProtocolReplicateReportV4,
    turns: tuple[ProviderBoundaryTurnV4, ...],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22-pr-d-private-provider-boundary-v4-replicate.v1",
        "report": report.model_dump(mode="json"),
        "completed_turns": [item.model_dump(mode="json") for item in turns],
    }
    return {**payload, "evidence_sha256": semantic_sha256_v22(payload)}


def _public_replicate(
    *,
    report: ProviderProtocolReplicateReportV4,
    private_raw_sha256: str,
    private_semantic_sha256: str,
    executed_at: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22-pr-d-provider-boundary-v4-replicate-result.v1",
        "executed_at": executed_at,
        "report": report.model_dump(mode="json"),
        "private_raw_sha256": private_raw_sha256,
        "private_semantic_sha256": private_semantic_sha256,
    }
    return {**payload, "result_sha256": semantic_sha256_v22(payload)}


def _persist_replicate(
    *,
    root: Path,
    report: ProviderProtocolReplicateReportV4,
    turns: tuple[ProviderBoundaryTurnV4, ...],
    executed_at: str,
) -> dict[str, Any]:
    replicate = report.replicate_id
    private = _private_replicate(report=report, turns=turns)
    private_raw, private_semantic = _write_once(
        _PRIVATE_ROOT / f"replicate-{replicate.lower()}.json",
        private,
        mode=0o600,
    )
    public = _public_replicate(
        report=report,
        private_raw_sha256=private_raw,
        private_semantic_sha256=private_semantic,
        executed_at=executed_at,
    )
    public_raw, public_semantic = _write_once(
        root / _PUBLIC[replicate],
        public,
        mode=0o644,
    )
    return {
        "replicate_id": replicate,
        "report_sha256": report.report_sha256,
        "terminal": report.terminal.value,
        "private_raw_sha256": private_raw,
        "private_semantic_sha256": private_semantic,
        "public_raw_sha256": public_raw,
        "public_semantic_sha256": public_semantic,
    }


def _campaign(
    *,
    commit: str,
    tree: str,
    manifest_sha256: str,
    probe_binding: dict[str, Any],
    replicate_bindings: tuple[dict[str, Any], ...],
    observed_provider_calls: int,
    selected_mode: ProviderOutputModeV22 | None,
) -> dict[str, Any]:
    complete = len(replicate_bindings) == 2
    passed = complete and all(item["terminal"] == "PASS" for item in replicate_bindings)
    probe_calls = probe_binding.get("provider_calls")
    expected_calls = (
        int(probe_calls) + 48
        if complete and isinstance(probe_calls, int)
        else observed_provider_calls
    )
    call_gate = observed_provider_calls == expected_calls and (
        not complete
        or (
            selected_mode is not None
            and probe_binding.get("selected_mode") == selected_mode.value
        )
    )
    terminal = (
        "DTA_V22_PR_D_CONTROLLER_READY"
        if passed and call_gate
        else "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v22-pr-d-provider-boundary-v4-campaign-result.v1",
        "goal_version": "dta-v22-p0-master-v1",
        "amendment_version": "dta-v22-pr-d-provider-boundary-v4-amendment-v1",
        "decision_id": "DEC-058",
        "implementation_commit": commit,
        "implementation_tree": tree,
        "manifest_sha256": manifest_sha256,
        "probe_binding": probe_binding,
        "selected_mode": None if selected_mode is None else selected_mode.value,
        "replicate_bindings": list(replicate_bindings),
        "completed_replicate_count": len(replicate_bindings),
        "observed_provider_calls": observed_provider_calls,
        "expected_provider_calls_for_complete_campaign": (
            None if not complete else expected_calls
        ),
        "provider_call_gate": call_gate,
        "http_auto_retry_count": 0,
        "semantic_retry_count": 0,
        "replacement_replicate_count": 0,
        "third_v3_replicate_count": 0,
        "docker_calls": 0,
        "scenario_executions": 0,
        "fault_injections": 0,
        "agent_evidence_dispatches": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "held_out_executions": 0,
        "terminal": terminal,
        "merge_ready": terminal == "DTA_V22_PR_D_CONTROLLER_READY",
    }
    return {**payload, "campaign_sha256": semantic_sha256_v22(payload)}


def _persist_campaign(
    *,
    root: Path,
    campaign: dict[str, Any],
) -> dict[str, Any]:
    private_raw, private_semantic = _write_once(
        _PRIVATE_ROOT / "campaign.json",
        campaign,
        mode=0o600,
    )
    public_without_digest = {
        **campaign,
        "private_campaign_raw_sha256": private_raw,
        "private_campaign_semantic_sha256": private_semantic,
    }
    public = {
        **public_without_digest,
        "campaign_sha256": semantic_sha256_v22(
            {
                key: value
                for key, value in public_without_digest.items()
                if key != "campaign_sha256"
            }
        ),
    }
    _write_once(root / _PUBLIC["campaign"], public, mode=0o644)
    return public


def _probe_abort_fields(error: BaseException) -> tuple[str, str, tuple[str, ...]]:
    if isinstance(error, ProviderModeProbeAbortV4):
        return (
            error.failure_class,
            error.safe_failure_code,
            tuple(mode.value for mode in error.attempted_modes),
        )
    if isinstance(error, ProviderHttpErrorV22):
        code = (
            "HTTP_RATE_LIMITED"
            if error.status == 429
            else "HTTP_CLIENT_ERROR"
            if 400 <= error.status < 500
            else "HTTP_SERVER_ERROR"
        )
        return ("PROVIDER_TRANSPORT_ABORT", code, ("STRICT_STRUCTURED_OUTPUT",))
    if isinstance(error, TimeoutError):
        return (
            "PROVIDER_TRANSPORT_ABORT",
            "TIMEOUT_ERROR",
            ("STRICT_STRUCTURED_OUTPUT",),
        )
    return (
        "PROVIDER_TRANSPORT_ABORT",
        "CONNECTION_ERROR",
        ("STRICT_STRUCTURED_OUTPUT",),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument("--implementation-commit", required=True)
    parser.add_argument("--implementation-tree", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    root = args.repository_root.resolve(strict=True)
    commit = str(args.implementation_commit)
    tree = str(args.implementation_tree)
    _verify_exact_execution_tree(
        root=root, commit=commit, tree=tree, allowed_dirty=set()
    )
    manifest = load_and_verify_manifest_v4(root)
    manifest_sha256 = str(manifest["manifest_sha256"])
    verify_pre_execution_admission_v4(root, require_private_history=True)
    values = _parse_provider_env(args.provider_env)
    config = OpenAICompatibleConfig.from_environment(values)
    if config is None or config.model != PRIMARY_MODEL_V22:
        raise RuntimeError("BLOCKED_DTA_V22_MODEL_CONTINUITY")
    provider = OpenAICompatibleProviderBoundaryV4(
        config=config,
        timeout_seconds=60.0,
        max_completion_tokens=256,
        min_request_interval_seconds=float(
            manifest["minimum_request_start_interval_seconds"]
        ),
    )
    for relative in _PUBLIC.values():
        if (root / relative).exists() or (root / relative).is_symlink():
            raise FileExistsError("v4 public result is create-once")
    if _PRIVATE_ROOT.exists() or _PRIVATE_ROOT.is_symlink():
        raise FileExistsError("v4 private campaign is create-once")
    _prepare_private_root(_PRIVATE_ROOT, root)
    binding_payload: dict[str, Any] = {
        "schema_version": "dta-v22-pr-d-provider-boundary-v4-manifest-binding.v1",
        "implementation_commit": commit,
        "implementation_tree": tree,
        "manifest_sha256": manifest_sha256,
        "bound_at": datetime.now(UTC).isoformat(),
    }
    binding = {
        **binding_payload,
        "binding_sha256": semantic_sha256_v22(binding_payload),
    }
    binding_raw, binding_semantic = _write_once(
        _PRIVATE_ROOT / "manifest-binding.json",
        binding,
        mode=0o600,
    )
    probe_request = build_provider_probe_request_v4()
    probe_started = provider.attempted_calls
    try:
        probe_report = provider.probe(request=probe_request)
    except (
        ConnectionError,
        TimeoutError,
        ProviderHttpErrorV22,
        ProviderModeProbeAbortV4,
    ) as error:
        failure_class, safe_failure_code, attempted_modes = _probe_abort_fields(error)
        probe_payload: dict[str, Any] = {
            "schema_version": "dta-v22-pr-d-private-provider-boundary-v4-probe.v1",
            "implementation_commit": commit,
            "implementation_tree": tree,
            "manifest_sha256": manifest_sha256,
            "supported": False,
            "provider_calls": provider.attempted_calls - probe_started,
            "selected_mode": None,
            "failure_class": failure_class,
            "safe_failure_code": safe_failure_code,
            "attempted_modes": attempted_modes,
            "probe_report": None,
        }
    else:
        probe_payload = {
            "schema_version": "dta-v22-pr-d-private-provider-boundary-v4-probe.v1",
            "implementation_commit": commit,
            "implementation_tree": tree,
            "manifest_sha256": manifest_sha256,
            "supported": probe_report.supported,
            "provider_calls": provider.attempted_calls - probe_started,
            "selected_mode": (
                None
                if probe_report.selected_mode is None
                else probe_report.selected_mode.value
            ),
            "failure_class": (
                None if probe_report.supported else "PROVIDER_RESPONSE_PROTOCOL_FAILURE"
            ),
            "safe_failure_code": (
                None if probe_report.supported else "PROBE_DECISION_REJECTED"
            ),
            "attempted_modes": tuple(
                attempt.mode.value for attempt in probe_report.attempts
            ),
            "probe_report": probe_report.model_dump(mode="json"),
        }
    probe_value = {
        **probe_payload,
        "probe_evidence_sha256": semantic_sha256_v22(probe_payload),
    }
    probe_raw, probe_semantic = _write_once(
        _PRIVATE_ROOT / "provider-mode-probe.json",
        probe_value,
        mode=0o600,
    )
    probe_binding = {
        "manifest_sha256": manifest_sha256,
        "private_raw_sha256": probe_raw,
        "private_semantic_sha256": probe_semantic,
        "probe_evidence_sha256": probe_value["probe_evidence_sha256"],
        "provider_calls": probe_value["provider_calls"],
        "supported": probe_value["supported"],
        "selected_mode": probe_value["selected_mode"],
        "probe_report_sha256": (
            None
            if probe_value["probe_report"] is None
            else probe_value["probe_report"]["report_sha256"]
        ),
        "probe_report": probe_value["probe_report"],
        "failure_class": probe_value["failure_class"],
        "safe_failure_code": probe_value["safe_failure_code"],
        "attempted_modes": probe_value["attempted_modes"],
        "manifest_binding_raw_sha256": binding_raw,
        "manifest_binding_semantic_sha256": binding_semantic,
    }
    if (
        not probe_value["supported"]
        or probe_value["provider_calls"] not in {1, 2}
        or probe_value["selected_mode"] is None
    ):
        campaign = _campaign(
            commit=commit,
            tree=tree,
            manifest_sha256=manifest_sha256,
            probe_binding=probe_binding,
            replicate_bindings=(),
            observed_provider_calls=provider.attempted_calls,
            selected_mode=None,
        )
        public_campaign = _persist_campaign(root=root, campaign=campaign)
        _verify_exact_execution_tree(
            root=root,
            commit=commit,
            tree=tree,
            allowed_dirty={str(_PUBLIC["campaign"])},
        )
        verify_private_execution_v4(
            root=root,
            private_root=_PRIVATE_ROOT,
            manifest=manifest,
            public_campaign=public_campaign,
        )
        raise RuntimeError("BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE")
    probe_report_sha256 = str(probe_value["probe_report"]["report_sha256"])
    selected_mode = ProviderOutputModeV22(str(probe_value["selected_mode"]))
    replicate_bindings: list[dict[str, Any]] = []
    for index, replicate_id in enumerate(("A", "B")):
        allowed = {str(_PUBLIC["A"])} if replicate_id == "B" else set()
        _verify_exact_execution_tree(
            root=root,
            commit=commit,
            tree=tree,
            allowed_dirty=allowed,
        )
        turns: list[ProviderBoundaryTurnV4] = []

        def complete(request, mode):
            turn = provider.complete(request=request, mode=mode)
            turns.append(turn)
            return turn

        typed_id: Literal["A", "B"] = "A" if replicate_id == "A" else "B"
        report = run_protocol_replicate_v4(
            replicate_id=typed_id,
            implementation_commit=commit,
            implementation_tree=tree,
            manifest_sha256=manifest_sha256,
            probe_report_sha256=probe_report_sha256,
            selected_mode=selected_mode,
            complete=complete,
            attempted_calls=lambda: provider.attempted_calls,
        )
        replicate_bindings.append(
            _persist_replicate(
                root=root,
                report=report,
                turns=tuple(turns),
                executed_at=datetime.now(UTC).isoformat(),
            )
        )
        if report.completed_response_count != 24:
            break
        if index == 0:
            time.sleep(_INTER_REPLICATE_COOLDOWN_SECONDS)
    campaign = _campaign(
        commit=commit,
        tree=tree,
        manifest_sha256=manifest_sha256,
        probe_binding=probe_binding,
        replicate_bindings=tuple(replicate_bindings),
        observed_provider_calls=provider.attempted_calls,
        selected_mode=selected_mode,
    )
    public_payload = _persist_campaign(root=root, campaign=campaign)
    allowed_result_paths = {
        str(_PUBLIC["campaign"]),
        *(str(_PUBLIC[str(binding["replicate_id"])]) for binding in replicate_bindings),
    }
    _verify_exact_execution_tree(
        root=root,
        commit=commit,
        tree=tree,
        allowed_dirty=allowed_result_paths,
    )
    verify_private_execution_v4(
        root=root,
        private_root=_PRIVATE_ROOT,
        manifest=manifest,
        public_campaign=public_payload,
    )
    print(
        json.dumps(
            {
                "implementation_commit": commit,
                "implementation_tree": tree,
                "observed_provider_calls": provider.attempted_calls,
                "terminal": campaign["terminal"],
                "campaign_sha256": public_payload["campaign_sha256"],
            },
            sort_keys=True,
        )
    )
    if campaign["terminal"] != "DTA_V22_PR_D_CONTROLLER_READY":
        raise RuntimeError("BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
