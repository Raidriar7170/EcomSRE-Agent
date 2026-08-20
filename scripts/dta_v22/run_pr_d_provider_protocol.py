"""Run the fixed replicated DTA v2.2 PR-D Provider protocol v3 campaign."""

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
from typing import Any, Callable, Literal, TypeVar

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.v22.controller_modes import (
    PRIMARY_MODEL_V22,
    ProviderModeCapabilityReportV22,
    probe_provider_output_mode_v22,
)
from ecomsre.dta_v2.v22.controller_provider import (
    OpenAICompatibleControllerProviderV22,
)
from ecomsre.dta_v2.v22.protocol_suite import (
    ProviderProtocolCapabilityReportV3,
    ProviderProtocolFailureClassV3,
    ProviderProtocolPartialFailureReceiptV3,
    ProviderProtocolSuiteTerminalV3,
    run_provider_protocol_replicate_v3,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    Sha256V22,
    semantic_sha256_v22,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


_ENVIRONMENT_NAMES = frozenset(
    {
        "ECOMSRE_LLM_BASE_URL",
        "ECOMSRE_LLM_API_KEY",
        "ECOMSRE_LLM_MODEL",
    }
)
_PRIVATE_EVIDENCE_ROOT = (
    Path.home()
    / ".ecomsre"
    / "private"
    / "dta-v22-p0-master-v1"
    / "pr-d"
    / "provider-protocol-v3"
)
_PUBLIC_REPLICATE_SUMMARY_RELATIVES = {
    "A": Path(
        "docs/analysis/dta-v22-pr-d-provider-protocol-v3-replicate-a-summary.json"
    ),
    "B": Path(
        "docs/analysis/dta-v22-pr-d-provider-protocol-v3-replicate-b-summary.json"
    ),
}
_PUBLIC_CAMPAIGN_SUMMARY_RELATIVE = Path(
    "docs/analysis/dta-v22-pr-d-provider-protocol-v3-campaign-summary.json"
)
_PREREGISTRATION_RELATIVE = Path(
    "config/dta-v22/pr-d-provider-protocol-v3-preregistration.json"
)
_FORMAL_MIN_REQUEST_INTERVAL_SECONDS = 4.0
_FORMAL_INTER_REPLICATE_COOLDOWN_SECONDS = 60.0
_FORMAL_HTTP_AUTO_RETRY_COUNT = 0
_FORMAL_REPLICATE_IDS = ("A", "B")


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


def _validate_campaign_paths(
    *,
    repository_root: Path,
    private_root: Path = _PRIVATE_EVIDENCE_ROOT,
) -> tuple[dict[str, Path], dict[str, Path]]:
    root = repository_root.resolve(strict=True)
    expected_suffix = (
        ".ecomsre",
        "private",
        "dta-v22-p0-master-v1",
        "pr-d",
        "provider-protocol-v3",
    )
    resolved_private_root = private_root.resolve(strict=False)
    if resolved_private_root.parts[-5:] != expected_suffix:
        raise ValueError("private Provider report path differs from exact Goal root")
    if resolved_private_root.is_relative_to(root):
        raise ValueError("private Provider report cannot be written inside repository")
    private_paths = {
        name: resolved_private_root / name
        for name in (
            "provider-mode-probe.json",
            "replicate-a.json",
            "replicate-b.json",
            "campaign.json",
        )
    }
    public_paths = {
        "A": (root / _PUBLIC_REPLICATE_SUMMARY_RELATIVES["A"]).resolve(
            strict=False
        ),
        "B": (root / _PUBLIC_REPLICATE_SUMMARY_RELATIVES["B"]).resolve(
            strict=False
        ),
        "campaign": (root / _PUBLIC_CAMPAIGN_SUMMARY_RELATIVE).resolve(
            strict=False
        ),
    }
    for path in (*private_paths.values(), *public_paths.values()):
        if path.exists() or path.is_symlink():
            raise FileExistsError("Provider protocol v3 artifact is create-once")
    for path in public_paths.values():
        if not path.parent.resolve(strict=True).is_relative_to(root):
            raise ValueError("public summary parent escapes repository")
    return private_paths, public_paths


_Replicate = TypeVar("_Replicate")
_Binding = TypeVar("_Binding")
_Campaign = TypeVar("_Campaign")


def _execute_fixed_schedule(
    *,
    execute_replicate: Callable[[str], _Replicate],
    persist_replicate: Callable[[_Replicate], _Binding],
    cooldown: Callable[[float], None],
    build_campaign: Callable[
        [tuple[_Replicate, _Replicate], tuple[_Binding, _Binding]],
        _Campaign,
    ],
    persist_campaign: Callable[[_Campaign], _Campaign],
) -> _Campaign:
    """Execute exactly A then B, durably binding each result before continuing."""

    outcomes: list[_Replicate] = []
    bindings: list[_Binding] = []
    for index, replicate_id in enumerate(_FORMAL_REPLICATE_IDS):
        outcome = execute_replicate(replicate_id)
        binding = persist_replicate(outcome)
        outcomes.append(outcome)
        bindings.append(binding)
        if index == 0:
            cooldown(_FORMAL_INTER_REPLICATE_COOLDOWN_SECONDS)
    campaign = build_campaign(
        (outcomes[0], outcomes[1]),
        (bindings[0], bindings[1]),
    )
    return persist_campaign(campaign)


class ProviderProbeFailureReceiptV3(DtaModelV22):
    schema_version: Literal["dta-v22.provider-probe-failure-receipt.v3"]
    replicate_id: Literal["A", "B"]
    implementation_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    implementation_tree: str = Field(pattern=r"^[0-9a-f]{40}$")
    preregistration_sha256: Sha256V22
    model: str
    planned_transition_count: Literal[52]
    completed_transition_count: Literal[0]
    provider_calls: Literal[0]
    failure_classification: Literal[
        ProviderProtocolFailureClassV3.PROVIDER_PROBE_FAILED
    ]
    failure_taxonomy: dict[str, StrictInt]
    invalid_dispatches: Literal[0]
    http_auto_retry_count: Literal[0]
    provider_gate_eligible: Literal[False]
    terminal: Literal[
        ProviderProtocolSuiteTerminalV3.BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE
    ]
    receipt_sha256: Sha256V22

    @model_validator(mode="after")
    def require_receipt(self) -> ProviderProbeFailureReceiptV3:
        expected_taxonomy = {
            item.value: (
                52
                if item is ProviderProtocolFailureClassV3.PROVIDER_PROBE_FAILED
                else 0
            )
            for item in ProviderProtocolFailureClassV3
        }
        if self.model != PRIMARY_MODEL_V22 or self.failure_taxonomy != expected_taxonomy:
            raise ValueError("Provider probe failure receipt differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"receipt_sha256"})
        )
        if self.receipt_sha256 != expected:
            raise ValueError("Provider probe failure receipt digest differs")
        return self


ReplicateOutcomeV3 = (
    ProviderProtocolCapabilityReportV3
    | ProviderProtocolPartialFailureReceiptV3
    | ProviderProbeFailureReceiptV3
)


def _prepare_private_root(path: Path, *, repository_root: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("private report root must be a regular directory")
    path.chmod(0o700)
    if path.stat().st_uid != os.getuid():
        raise ValueError("private report root owner differs")
    if path.resolve(strict=True).is_relative_to(repository_root.resolve(strict=True)):
        raise ValueError("private Provider report cannot be written inside repository")


def _write_verified_create_once(path: Path, text: str, *, mode: int) -> str:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(mode)
    observed = path.read_text(encoding="utf-8")
    if observed != text:
        raise OSError("persisted Provider artifact differs after write")
    return hashlib.sha256(observed.encode("utf-8")).hexdigest()


def _load_preregistration(root: Path) -> dict[str, Any]:
    path = root / _PREREGISTRATION_RELATIVE
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError("Provider protocol v3 preregistration must be regular")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Provider protocol v3 preregistration must be an object")
    claimed = value.get("preregistration_sha256")
    expected = semantic_sha256_v22(
        {key: item for key, item in value.items() if key != "preregistration_sha256"}
    )
    if (
        claimed != expected
        or value.get("schema_version")
        != "dta-v22-pr-d-provider-protocol-v3-preregistration.v1"
        or value.get("amendment_version")
        != "dta-v22-pr-d-provider-protocol-replicated-gate-v1"
        or value.get("replicate_ids") != ["A", "B"]
        or value.get("transition_count_per_replicate") != 52
        or value.get("minimum_request_start_interval_seconds") != 4.0
        or value.get("inter_replicate_cooldown_seconds") != 60.0
        or value.get("http_auto_retry_count") != 0
    ):
        raise ValueError("Provider protocol v3 preregistration differs")
    return value


def _worktree_dirty_paths(root: Path) -> set[str]:
    completed = subprocess.run(
        (
            "git",
            "-C",
            str(root),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ),
        check=True,
        capture_output=True,
    )
    observed: set[str] = set()
    for raw_entry in completed.stdout.split(b"\0"):
        if not raw_entry:
            continue
        entry = raw_entry.decode("utf-8", errors="strict")
        status = entry[:2]
        if len(entry) < 4 or status != "??":
            raise RuntimeError("BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE")
        observed.add(entry[3:])
    return observed


def _verify_frozen_execution_identity(
    *,
    root: Path,
    implementation_commit: str,
    implementation_tree: str,
    preregistration_sha256: str,
    allowed_dirty_paths: frozenset[str] = frozenset(),
) -> None:
    if (
        _git_text(root, "rev-parse", "HEAD") != implementation_commit
        or _git_text(root, "rev-parse", "HEAD^{tree}") != implementation_tree
    ):
        raise RuntimeError("BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE")
    if _worktree_dirty_paths(root) != set(allowed_dirty_paths):
        raise RuntimeError("BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE")
    preregistration = _load_preregistration(root)
    if preregistration.get("preregistration_sha256") != preregistration_sha256:
        raise RuntimeError("BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE")
    for field in (
        "frozen_raw_sha256_by_path",
        "historical_attempt_raw_sha256_by_path",
    ):
        bindings = preregistration.get(field)
        if not isinstance(bindings, dict) or not bindings:
            raise RuntimeError("BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE")
        for relative_text, expected_sha256 in bindings.items():
            if (
                not isinstance(relative_text, str)
                or not isinstance(expected_sha256, str)
                or Path(relative_text).is_absolute()
                or ".." in Path(relative_text).parts
            ):
                raise RuntimeError("BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE")
            path = root / relative_text
            details = path.lstat()
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
                raise RuntimeError("BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE")
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
                raise RuntimeError("BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE")


def _probe_failure_receipt(
    *,
    replicate_id: Literal["A", "B"],
    implementation_commit: str,
    implementation_tree: str,
    preregistration_sha256: str,
) -> ProviderProbeFailureReceiptV3:
    taxonomy = {
        item.value: (
            52 if item is ProviderProtocolFailureClassV3.PROVIDER_PROBE_FAILED else 0
        )
        for item in ProviderProtocolFailureClassV3
    }
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.provider-probe-failure-receipt.v3",
        "replicate_id": replicate_id,
        "implementation_commit": implementation_commit,
        "implementation_tree": implementation_tree,
        "preregistration_sha256": preregistration_sha256,
        "model": PRIMARY_MODEL_V22,
        "planned_transition_count": 52,
        "completed_transition_count": 0,
        "provider_calls": 0,
        "failure_classification": ProviderProtocolFailureClassV3.PROVIDER_PROBE_FAILED,
        "failure_taxonomy": taxonomy,
        "invalid_dispatches": 0,
        "http_auto_retry_count": 0,
        "provider_gate_eligible": False,
        "terminal": (
            ProviderProtocolSuiteTerminalV3.BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE
        ),
    }
    draft = ProviderProbeFailureReceiptV3.model_construct(
        **payload, receipt_sha256="0" * 64
    )
    return ProviderProbeFailureReceiptV3.model_validate(
        {
            **payload,
            "receipt_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"receipt_sha256"})
            ),
        }
    )


def _acceptance_cell(
    *,
    planned: int,
    accepted: int,
) -> dict[str, int | float]:
    return {
        "transition_count": planned,
        "accepted_count": accepted,
        "acceptance": accepted / planned,
    }


def _partial_metric_families(
    outcome: ProviderProtocolPartialFailureReceiptV3,
) -> dict[str, Any]:
    ordinary = tuple(
        item for item in outcome.completed_transitions if item.transition_kind == "ORDINARY"
    )
    corrections = tuple(
        item
        for item in outcome.completed_transitions
        if item.transition_kind == "CORRECTION_ENVELOPE"
    )
    ordinary_by_arm = {
        arm: _acceptance_cell(
            planned=24,
            accepted=sum(
                bool(item.ordinary_first_pass_accepted)
                for item in ordinary
                if item.arm.value == arm
            ),
        )
        for arm in ("FLAT_CANONICAL", "PLANNER_LITE")
    }
    correction_by_arm = {
        arm: _acceptance_cell(
            planned=2,
            accepted=sum(
                bool(item.correction_envelope_accepted)
                for item in corrections
                if item.arm.value == arm
            ),
        )
        for arm in ("FLAT_CANONICAL", "PLANNER_LITE")
    }
    correction_by_error = {
        error: _acceptance_cell(
            planned=2,
            accepted=sum(
                bool(item.correction_envelope_accepted)
                for item in corrections
                if item.category.value == error
            ),
        )
        for error in ("STALE_ACTION_CORRECTION", "INVALID_REF_CORRECTION")
    }
    ordinary_plan_by_category = {
        "VALID_READ": 8,
        "VALID_COMMIT": 8,
        "VALID_NO_INCIDENT": 8,
        "VALID_ABSTAIN": 8,
        "BUDGET_EXHAUSTION": 6,
        "EMPTY_SOURCE": 5,
        "UNAVAILABLE_SOURCE": 5,
    }
    ordinary_by_category = {
        category: _acceptance_cell(
            planned=planned,
            accepted=sum(
                bool(item.ordinary_first_pass_accepted)
                for item in ordinary
                if item.category.value == category
            ),
        )
        for category, planned in ordinary_plan_by_category.items()
    }
    ordinary_accepted = sum(
        bool(item.ordinary_first_pass_accepted) for item in ordinary
    )
    correction_accepted = sum(
        bool(item.correction_envelope_accepted) for item in corrections
    )
    final_accepted = sum(item.final_accepted for item in outcome.completed_transitions)
    return {
        "transition_count": 52,
        "parsed_decision_count": outcome.parsed_decision_count,
        "runtime_protocol_admitted_count": outcome.runtime_protocol_admitted_count,
        "semantic_category_accepted_count": outcome.semantic_category_accepted_count,
        "ordinary_transition_count": 48,
        "ordinary_first_pass_accepted_count": ordinary_accepted,
        "ordinary_first_pass_protocol_acceptance": ordinary_accepted / 48,
        "ordinary_first_pass_by_arm": ordinary_by_arm,
        "ordinary_first_pass_by_category": ordinary_by_category,
        "correction_transition_count": 4,
        "correction_envelope_accepted_count": correction_accepted,
        "correction_envelope_acceptance": correction_accepted / 4,
        "correction_acceptance_by_arm": correction_by_arm,
        "correction_acceptance_by_error_class": correction_by_error,
        "final_accepted_count": final_accepted,
        "final_protocol_acceptance": final_accepted / 52,
        "provider_calls": outcome.provider_calls,
        "input_tokens": outcome.input_tokens,
        "output_tokens": outcome.output_tokens,
        "total_tokens": outcome.total_tokens,
        "latency": outcome.latency.model_dump(mode="json"),
    }


def _replicate_public_summary(
    *,
    outcome: ReplicateOutcomeV3,
    executed_at: str,
    probe_evidence_sha256: str,
    private_raw_sha256: str,
    private_semantic_sha256: str,
) -> dict[str, Any]:
    if isinstance(outcome, ProviderProtocolCapabilityReportV3):
        metrics = {
            key: value
            for key, value in outcome.model_dump(mode="json").items()
            if key
            in {
                "transition_count",
                "parsed_decision_count",
                "runtime_protocol_admitted_count",
                "semantic_category_accepted_count",
                "ordinary_transition_count",
                "ordinary_first_pass_accepted_count",
                "ordinary_first_pass_protocol_acceptance",
                "ordinary_first_pass_by_arm",
                "ordinary_first_pass_by_category",
                "correction_transition_count",
                "correction_envelope_accepted_count",
                "correction_envelope_acceptance",
                "correction_acceptance_by_arm",
                "correction_acceptance_by_error_class",
                "final_accepted_count",
                "final_protocol_acceptance",
                "provider_calls",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "latency",
            }
        }
        selected_mode: str | None = outcome.selected_mode.value
        probe_report_sha256: str | None = outcome.provider_probe.report_sha256
        schema_sha256: str | None = outcome.controller_schema_sha256
        identities = list(outcome.controller_identity_sha256s)
        prompts = list(outcome.controller_prompt_sha256s)
        outcome_sha256 = outcome.report_sha256
    elif isinstance(outcome, ProviderProtocolPartialFailureReceiptV3):
        metrics = _partial_metric_families(outcome)
        selected_mode = outcome.selected_mode.value
        probe_report_sha256 = outcome.provider_probe.report_sha256
        schema_sha256 = outcome.controller_schema_sha256
        identities = list(outcome.controller_identity_sha256s)
        prompts = list(outcome.controller_prompt_sha256s)
        outcome_sha256 = outcome.receipt_sha256
    else:
        metrics = {
            "transition_count": 52,
            "parsed_decision_count": 0,
            "runtime_protocol_admitted_count": 0,
            "semantic_category_accepted_count": 0,
            "ordinary_transition_count": 48,
            "ordinary_first_pass_accepted_count": 0,
            "ordinary_first_pass_protocol_acceptance": 0.0,
            "ordinary_first_pass_by_arm": {
                arm: _acceptance_cell(planned=24, accepted=0)
                for arm in ("FLAT_CANONICAL", "PLANNER_LITE")
            },
            "ordinary_first_pass_by_category": {
                category: _acceptance_cell(planned=planned, accepted=0)
                for category, planned in {
                    "VALID_READ": 8,
                    "VALID_COMMIT": 8,
                    "VALID_NO_INCIDENT": 8,
                    "VALID_ABSTAIN": 8,
                    "BUDGET_EXHAUSTION": 6,
                    "EMPTY_SOURCE": 5,
                    "UNAVAILABLE_SOURCE": 5,
                }.items()
            },
            "correction_transition_count": 4,
            "correction_envelope_accepted_count": 0,
            "correction_envelope_acceptance": 0.0,
            "correction_acceptance_by_arm": {
                arm: _acceptance_cell(planned=2, accepted=0)
                for arm in ("FLAT_CANONICAL", "PLANNER_LITE")
            },
            "correction_acceptance_by_error_class": {
                error: _acceptance_cell(planned=2, accepted=0)
                for error in (
                    "STALE_ACTION_CORRECTION",
                    "INVALID_REF_CORRECTION",
                )
            },
            "final_accepted_count": 0,
            "final_protocol_acceptance": 0.0,
            "provider_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "latency": {"total_ms": 0, "maximum_ms": 0},
        }
        selected_mode = None
        probe_report_sha256 = None
        schema_sha256 = None
        identities = []
        prompts = []
        outcome_sha256 = outcome.receipt_sha256
    payload: dict[str, Any] = {
        "schema_version": "dta-v22-pr-d-provider-protocol-v3-replicate-summary.v1",
        "goal_version": "dta-v22-p0-master-v1",
        "amendment_version": "dta-v22-pr-d-provider-protocol-replicated-gate-v1",
        "replicate_id": outcome.replicate_id,
        "implementation_commit": outcome.implementation_commit,
        "implementation_tree": outcome.implementation_tree,
        "preregistration_sha256": outcome.preregistration_sha256,
        "executed_at": executed_at,
        "model": outcome.model,
        "temperature": 0,
        "selected_mode": selected_mode,
        "provider_probe_report_sha256": probe_report_sha256,
        "provider_probe_evidence_sha256": probe_evidence_sha256,
        "controller_schema_sha256": schema_sha256,
        "controller_identity_sha256s": identities,
        "controller_prompt_sha256s": prompts,
        "outcome_sha256": outcome_sha256,
        **metrics,
        "failure_taxonomy": outcome.failure_taxonomy,
        "invalid_dispatches": outcome.invalid_dispatches,
        "http_auto_retry_count": outcome.http_auto_retry_count,
        "provider_gate_eligible": outcome.provider_gate_eligible,
        "terminal": outcome.terminal.value,
        "private_evidence_raw_sha256": private_raw_sha256,
        "private_evidence_semantic_sha256": private_semantic_sha256,
        "private_evidence_location_class": "DTA_V22_PRIVATE_ROOT",
        "raw_provider_content_published": False,
        "private_paths_published": False,
        "agent_read_dispatches_executed": 0,
        "agent_write_calls": 0,
        "runbook_executions": 0,
        "docker_calls": 0,
        "held_out_executions": 0,
        "scenario_executions": 0,
        "fault_injections": 0,
    }
    return {**payload, "summary_sha256": semantic_sha256_v22(payload)}


def _persist_replicate_artifacts(
    *,
    outcome: ReplicateOutcomeV3,
    private_path: Path,
    public_path: Path,
    probe_evidence_sha256: str,
) -> dict[str, Any]:
    executed_at = datetime.now(UTC).isoformat()
    private_payload: dict[str, Any] = {
        "schema_version": "dta-v22-pr-d-private-provider-protocol-v3-replicate.v1",
        "executed_at": executed_at,
        "outcome": outcome.model_dump(mode="json"),
    }
    private_semantic = semantic_sha256_v22(private_payload)
    private_payload["evidence_sha256"] = private_semantic
    private_text = _canonical_json(private_payload)
    private_raw = _write_verified_create_once(private_path, private_text, mode=0o600)
    summary = _replicate_public_summary(
        outcome=outcome,
        executed_at=executed_at,
        probe_evidence_sha256=probe_evidence_sha256,
        private_raw_sha256=private_raw,
        private_semantic_sha256=private_semantic,
    )
    public_text = _canonical_json(summary)
    public_raw = _write_verified_create_once(public_path, public_text, mode=0o644)
    observed = json.loads(public_path.read_text(encoding="utf-8"))
    if (
        observed.get("summary_sha256")
        != semantic_sha256_v22(
            {key: value for key, value in observed.items() if key != "summary_sha256"}
        )
        or observed.get("private_evidence_raw_sha256") != private_raw
        or observed.get("private_evidence_semantic_sha256") != private_semantic
    ):
        raise OSError("public replicate summary verification failed")
    return {
        "replicate_id": outcome.replicate_id,
        "private_raw_sha256": private_raw,
        "private_semantic_sha256": private_semantic,
        "public_raw_sha256": public_raw,
        "public_semantic_sha256": summary["summary_sha256"],
        "verified": True,
    }


def _build_campaign_summary(
    *,
    outcomes: tuple[ReplicateOutcomeV3, ReplicateOutcomeV3],
    bindings: tuple[dict[str, Any], dict[str, Any]],
    implementation_commit: str,
    implementation_tree: str,
    preregistration_sha256: str,
    probe_evidence_sha256: str,
    provider_probe_calls: int,
    observed_provider_calls: int,
) -> dict[str, Any]:
    complete = all(
        isinstance(item, ProviderProtocolCapabilityReportV3) for item in outcomes
    )
    binding_equal = False
    if complete:
        left = outcomes[0]
        right = outcomes[1]
        assert isinstance(left, ProviderProtocolCapabilityReportV3)
        assert isinstance(right, ProviderProtocolCapabilityReportV3)
        binding_equal = (
            left.implementation_commit == right.implementation_commit
            and left.implementation_tree == right.implementation_tree
            and left.selected_mode is right.selected_mode
            and left.provider_probe.report_sha256 == right.provider_probe.report_sha256
            and left.controller_schema_sha256 == right.controller_schema_sha256
            and left.controller_identity_sha256s == right.controller_identity_sha256s
            and left.controller_prompt_sha256s == right.controller_prompt_sha256s
        )
    expected_provider_calls = provider_probe_calls + sum(
        item.provider_calls for item in outcomes
    )
    failure_taxonomy_by_replicate = {
        item.replicate_id: item.failure_taxonomy for item in outcomes
    }
    aggregate_failure_taxonomy = {
        failure.value: sum(
            item.failure_taxonomy[failure.value] for item in outcomes
        )
        for failure in ProviderProtocolFailureClassV3
    }
    both_passed = all(item.provider_gate_eligible for item in outcomes)
    verified = all(item.get("verified") is True for item in bindings)
    eligible = (
        both_passed
        and binding_equal
        and verified
        and observed_provider_calls == expected_provider_calls
        and sum(item.invalid_dispatches for item in outcomes) == 0
    )
    terminal = (
        "DTA_V22_PR_D_CONTROLLER_READY"
        if eligible
        else "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v22-pr-d-provider-protocol-v3-campaign-summary.v1",
        "goal_version": "dta-v22-p0-master-v1",
        "amendment_version": "dta-v22-pr-d-provider-protocol-replicated-gate-v1",
        "implementation_commit": implementation_commit,
        "implementation_tree": implementation_tree,
        "preregistration_sha256": preregistration_sha256,
        "probe_evidence_sha256": probe_evidence_sha256,
        "replicate_ids": [item.replicate_id for item in outcomes],
        "replicate_outcome_sha256s": [
            item.report_sha256
            if isinstance(item, ProviderProtocolCapabilityReportV3)
            else item.receipt_sha256
            for item in outcomes
        ],
        "replicate_terminals": [item.terminal.value for item in outcomes],
        "failure_taxonomy_by_replicate": failure_taxonomy_by_replicate,
        "aggregate_failure_taxonomy": aggregate_failure_taxonomy,
        "replicate_bindings": list(bindings),
        "both_replicates_independently_passed": both_passed,
        "implementation_and_controller_bindings_equal": binding_equal,
        "provider_probe_calls": provider_probe_calls,
        "replicate_provider_calls": [item.provider_calls for item in outcomes],
        "expected_provider_calls": expected_provider_calls,
        "observed_provider_calls": observed_provider_calls,
        "undeclared_provider_calls": max(
            0, observed_provider_calls - expected_provider_calls
        ),
        "provider_call_accounting_exact": (
            observed_provider_calls == expected_provider_calls
        ),
        "invalid_dispatches": sum(item.invalid_dispatches for item in outcomes),
        "agent_read_dispatches_executed": 0,
        "agent_write_calls": 0,
        "runbook_executions": 0,
        "docker_calls": 0,
        "held_out_executions": 0,
        "scenario_executions": 0,
        "fault_injections": 0,
        "http_auto_retry_count": 0,
        "campaign_gate_eligible": eligible,
        "terminal": terminal,
    }
    return {**payload, "campaign_sha256": semantic_sha256_v22(payload)}


def _persist_campaign_artifacts(
    *,
    campaign: dict[str, Any],
    private_path: Path,
    public_path: Path,
) -> dict[str, Any]:
    private_payload: dict[str, Any] = {
        "schema_version": "dta-v22-pr-d-private-provider-protocol-v3-campaign.v1",
        "persisted_at": datetime.now(UTC).isoformat(),
        "campaign": campaign,
    }
    private_semantic = semantic_sha256_v22(private_payload)
    private_payload["evidence_sha256"] = private_semantic
    private_text = _canonical_json(private_payload)
    private_raw = _write_verified_create_once(private_path, private_text, mode=0o600)
    public_payload = {
        **campaign,
        "private_evidence_raw_sha256": private_raw,
        "private_evidence_semantic_sha256": private_semantic,
        "private_evidence_location_class": "DTA_V22_PRIVATE_ROOT",
    }
    public_payload["campaign_sha256"] = semantic_sha256_v22(
        {
            key: value
            for key, value in public_payload.items()
            if key != "campaign_sha256"
        }
    )
    public_text = _canonical_json(public_payload)
    _write_verified_create_once(public_path, public_text, mode=0o644)
    observed = json.loads(public_path.read_text(encoding="utf-8"))
    if observed.get("campaign_sha256") != semantic_sha256_v22(
        {key: value for key, value in observed.items() if key != "campaign_sha256"}
    ):
        raise OSError("public campaign summary verification failed")
    return observed


def _persist_probe_artifact(
    *,
    path: Path,
    implementation_commit: str,
    implementation_tree: str,
    preregistration_sha256: str,
    provider_calls: int,
    probe: ProviderModeCapabilityReportV22 | None,
) -> str:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22-pr-d-private-provider-mode-probe.v1",
        "implementation_commit": implementation_commit,
        "implementation_tree": implementation_tree,
        "preregistration_sha256": preregistration_sha256,
        "executed_at": datetime.now(UTC).isoformat(),
        "model": PRIMARY_MODEL_V22,
        "provider_calls": provider_calls,
        "probe_succeeded": probe is not None,
        "probe": None if probe is None else probe.model_dump(mode="json"),
        "failure_classification": (
            None
            if probe is not None
            else ProviderProtocolFailureClassV3.PROVIDER_PROBE_FAILED.value
        ),
    }
    payload["evidence_sha256"] = semantic_sha256_v22(payload)
    text = _canonical_json(payload)
    _write_verified_create_once(path, text, mode=0o600)
    return str(payload["evidence_sha256"])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--implementation-commit", required=True)
    parser.add_argument("--implementation-tree", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = args.repository_root.resolve(strict=True)
    if _git_text(root, "status", "--porcelain"):
        raise ValueError("formal Provider protocol run requires a clean worktree")
    implementation_commit = _git_text(root, "rev-parse", "HEAD")
    implementation_tree = _git_text(root, "rev-parse", "HEAD^{tree}")
    if (
        implementation_commit != args.implementation_commit
        or implementation_tree != args.implementation_tree
    ):
        raise ValueError("formal Provider protocol run differs from frozen implementation")
    preregistration = _load_preregistration(root)
    preregistration_sha256 = str(preregistration["preregistration_sha256"])
    _verify_frozen_execution_identity(
        root=root,
        implementation_commit=implementation_commit,
        implementation_tree=implementation_tree,
        preregistration_sha256=preregistration_sha256,
    )
    private_paths, public_paths = _validate_campaign_paths(repository_root=root)
    _prepare_private_root(_PRIVATE_EVIDENCE_ROOT, repository_root=root)
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
    try:
        probe = probe_provider_output_mode_v22(probe=provider.probe_output_mode)
    except RuntimeError:
        probe = None
    probe_calls = provider.attempted_calls
    probe_evidence_sha256 = _persist_probe_artifact(
        path=private_paths["provider-mode-probe.json"],
        implementation_commit=implementation_commit,
        implementation_tree=implementation_tree,
        preregistration_sha256=preregistration_sha256,
        provider_calls=probe_calls,
        probe=probe,
    )

    def execute_replicate(replicate_id: str) -> ReplicateOutcomeV3:
        allowed_dirty_paths = (
            frozenset()
            if replicate_id == "A"
            else frozenset({_PUBLIC_REPLICATE_SUMMARY_RELATIVES["A"].as_posix()})
        )
        _verify_frozen_execution_identity(
            root=root,
            implementation_commit=implementation_commit,
            implementation_tree=implementation_tree,
            preregistration_sha256=preregistration_sha256,
            allowed_dirty_paths=allowed_dirty_paths,
        )
        typed_id: Literal["A", "B"] = "A" if replicate_id == "A" else "B"
        if probe is None:
            return _probe_failure_receipt(
                replicate_id=typed_id,
                implementation_commit=implementation_commit,
                implementation_tree=implementation_tree,
                preregistration_sha256=preregistration_sha256,
            )
        return run_provider_protocol_replicate_v3(
            provider_probe=probe,
            complete=provider.complete_controller_turn,
            attempted_calls=lambda: provider.attempted_calls,
            replicate_id=typed_id,
            implementation_commit=implementation_commit,
            implementation_tree=implementation_tree,
            preregistration_sha256=preregistration_sha256,
        )

    def persist_replicate(outcome: ReplicateOutcomeV3) -> dict[str, Any]:
        private_name = f"replicate-{outcome.replicate_id.lower()}.json"
        return _persist_replicate_artifacts(
            outcome=outcome,
            private_path=private_paths[private_name],
            public_path=public_paths[outcome.replicate_id],
            probe_evidence_sha256=probe_evidence_sha256,
        )

    def build_campaign(
        outcomes: tuple[ReplicateOutcomeV3, ReplicateOutcomeV3],
        bindings: tuple[dict[str, Any], dict[str, Any]],
    ) -> dict[str, Any]:
        _verify_frozen_execution_identity(
            root=root,
            implementation_commit=implementation_commit,
            implementation_tree=implementation_tree,
            preregistration_sha256=preregistration_sha256,
            allowed_dirty_paths=frozenset(
                relative.as_posix()
                for relative in _PUBLIC_REPLICATE_SUMMARY_RELATIVES.values()
            ),
        )
        return _build_campaign_summary(
            outcomes=outcomes,
            bindings=bindings,
            implementation_commit=implementation_commit,
            implementation_tree=implementation_tree,
            preregistration_sha256=preregistration_sha256,
            probe_evidence_sha256=probe_evidence_sha256,
            provider_probe_calls=probe_calls,
            observed_provider_calls=provider.attempted_calls,
        )

    campaign = _execute_fixed_schedule(
        execute_replicate=execute_replicate,
        persist_replicate=persist_replicate,
        cooldown=time.sleep,
        build_campaign=build_campaign,
        persist_campaign=lambda value: _persist_campaign_artifacts(
            campaign=value,
            private_path=private_paths["campaign.json"],
            public_path=public_paths["campaign"],
        ),
    )
    print(
        json.dumps(
            {
                "implementation_commit": implementation_commit,
                "implementation_tree": implementation_tree,
                "replicate_provider_calls": campaign["replicate_provider_calls"],
                "observed_provider_calls": campaign["observed_provider_calls"],
                "terminal": campaign["terminal"],
                "campaign_sha256": campaign["campaign_sha256"],
            },
            sort_keys=True,
        )
    )
    if campaign["terminal"] != "DTA_V22_PR_D_CONTROLLER_READY":
        raise RuntimeError("BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
