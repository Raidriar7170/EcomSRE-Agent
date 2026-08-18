"""Append-only PR-F Compose identity and retry reconciliation contracts."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Callable, Literal, Mapping, Sequence

from pydantic import Field, StrictInt, model_validator
from pydantic_core import to_jsonable_python
from typing_extensions import Self

from ecomsre.dta_v2.v21.contracts import DtaModelV21, Sha256V21, semantic_sha256
from ecomsre.dta_v2.v21.live_contracts import LiveReadinessV21, LiveReadinessV2
from ecomsre_live_sandbox.contracts import (
    canonical_json_bytes,
    ensure_private_directory,
    load_bundle,
    verify_private_tree_permissions,
    write_private_json,
)
from ecomsre_live_sandbox.environment import SandboxEnvironment


COMPOSE_NORMALIZATION_POLICY_ID_V1 = (
    "DTA_V21_PRF_ATTEMPT_LOCAL_FLAGD_BIND_SOURCE_V1"
)
FLAGD_BIND_SENTINEL_V1: Literal[
    "private://dta-v21-prf/attempt-local-flagd"
] = "private://dta-v21-prf/attempt-local-flagd"
DECISION_ID_V1 = "DEC-045"
AMENDMENT_RAW_SHA256_V1 = (
    "ea6740bce0ba63e093cda2807aea886d4ca48907702a2bf41ad1eedd0e2ab164"
)
BLOCKED_CODE_HEAD_V1 = "422f015451fd0a37f1442aa770fcffff75336aaa"
BLOCKED_ATTEMPT_ID_V1 = "dta-v21-prf-01-no-fault-422f015451fd"
AD_PROTOCOL_SHA256_V1 = (
    "c983b9be95b532cdbb8fb5358af92055e633fd767693e9dc65743b3e80a77517"
)

_IMMUTABLE_RAW_SHA256 = {
    "readiness": "fdd353be56e60e223bfb9272347f6a57076f929a97bdce12fc2eda0d946dad4c",
    "attempt_claim": "b86bac065080a651ff3abfd9aba3177a123606d6e254560928911f545dab3dcd",
    "attempt_terminal": "e84fa96855e298c5fe3a76f6d00031e53013735092e29a6f5466e809bdf8ec5c",
    "preflight_compose": "d2aa95d3ca241e4a92226aaf4faaca538c24ed6e3b50f2738c6155a92bba4441",
    "attempt_compose": "ddf040821a833f1068cda6a109ef45ce66557a6f10b40c05c6cf5799eba5f157",
    "master_authorization": "08ec561eeec8ee9a366b7290620a6c535e9fdc0dc1556c4bd2b5cf78106b71a1",
    "protocol_freeze": "9beaf16669e755773687c2125593751a9be7f4f7b95b6cec0947a4f269707080",
}
_IMMUTABLE_SEMANTIC_SHA256 = {
    "readiness": "d2f7b0136d7b6e5377c7cb1528f51fb10e353351a9e3987c0e1c74946aeca9d0",
    "attempt_claim": "cae09395bf2571850e6e437fcbed34ae9ebcef20f89b6aab466c6c305c31bffc",
    "attempt_terminal": "3ca6611e82d9c9801f6286eb17f007f59448d99705d23565597229cf7f735248",
    "master_authorization": "e817258c66f9a86325892e8f6c22f976895845d937031c9521a594a8899007c6",
    "protocol_freeze": "42a5bce4cc9fc1b9bef0979cc8d1b6a7439192da48b023d45bc7382953ee787a",
}
_FORBIDDEN_HISTORICAL_ARTIFACTS = (
    "baseline-evidence.json",
    "fault-impact.json",
    "agent-result.json",
    "current-state.json",
    "operational-admission.json",
    "run-authorization.json",
    "step-dispatch-intent.json",
    "post-write-state.json",
    "step-receipt.json",
    "recovery-result.json",
    "environment-admission.json",
)


class NormalizedComposeBindingV1(DtaModelV21):
    service: Literal["flagd", "flagd-ui"]
    mount_type: Literal["bind"]
    target: Literal["/etc/flagd", "/app/data"]
    read_only: bool
    json_pointer: str = Field(pattern=r"^/services/(?:flagd|flagd-ui)/volumes/[0-9]+/source$")
    normalized_source: Literal[
        "private://dta-v21-prf/attempt-local-flagd"
    ]


class ResolvedComposeIdentityV1(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f-resolved-compose-identity.v1"]
    raw_compose_sha256: Sha256V21
    execution_compose_sha256: Sha256V21
    normalization_policy_id: Literal[
        "DTA_V21_PRF_ATTEMPT_LOCAL_FLAGD_BIND_SOURCE_V1"
    ]
    normalized_bind_count: Literal[2]
    normalized_bindings: tuple[
        NormalizedComposeBindingV1, NormalizedComposeBindingV1
    ]
    raw_flagd_source_sha256: Sha256V21
    raw_flagd_ui_source_sha256: Sha256V21
    resolved_service_inventory_sha256: Sha256V21
    identity_sha256: Sha256V21

    @model_validator(mode="after")
    def require_closed_identity(self) -> Self:
        if tuple(item.service for item in self.normalized_bindings) != (
            "flagd",
            "flagd-ui",
        ):
            raise ValueError("closed-world Compose bindings differ")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"identity_sha256"})
        )
        if self.identity_sha256 != expected:
            raise ValueError("Compose identity SHA-256 mismatch")
        return self


RawContractVerifier = Callable[[Mapping[str, object]], None]


def _semantic(value: object) -> str:
    return semantic_sha256(to_jsonable_python(value))


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_flagd_directory(
    *,
    expected: Path,
    accepted_private_prf_root: Path,
    repository_root: Path,
) -> Path:
    lexical = Path(expected)
    if lexical.is_symlink() or not lexical.is_dir():
        raise ValueError("expected flag directory must be a regular non-symlink directory")
    resolved = lexical.resolve(strict=True)
    accepted = Path(accepted_private_prf_root).resolve(strict=True)
    repository = Path(repository_root).resolve(strict=True)
    if not resolved.is_relative_to(accepted):
        raise ValueError("expected flag directory is outside accepted private PR-F root")
    if resolved.is_relative_to(repository):
        raise ValueError("expected flag directory is inside the repository")
    if resolved == Path("/") or resolved == Path("/var/run/docker.sock"):
        raise ValueError("expected flag directory is an unsafe host path")
    if not stat.S_ISDIR(resolved.stat().st_mode):
        raise ValueError("expected flag directory is not a directory")
    if stat.S_IMODE(resolved.stat().st_mode) != 0o700:
        raise ValueError("expected flag directory permissions differ from 0700")
    return resolved


def _require_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _normalize_binding(
    normalized: dict[str, object],
    *,
    service: Literal["flagd", "flagd-ui"],
    target: Literal["/etc/flagd", "/app/data"],
    expected_source: str,
    expected_read_only: bool,
) -> NormalizedComposeBindingV1:
    services = _require_mapping(normalized.get("services"), label="Compose services")
    service_value = _require_mapping(
        services.get(service), label=f"Compose service {service}"
    )
    mounts = service_value.get("volumes")
    if not isinstance(mounts, list):
        raise ValueError("closed-world flag bind volumes are missing")
    matches: list[tuple[int, dict[str, object]]] = []
    for index, value in enumerate(mounts):
        if isinstance(value, dict) and value.get("target") == target:
            matches.append((index, value))
    if len(matches) != 1:
        raise ValueError("closed-world flag bind target is missing or duplicated")
    index, mount = matches[0]
    read_only = mount.get("read_only") is True
    if (
        mount.get("type") != "bind"
        or mount.get("source") != expected_source
        or read_only is not expected_read_only
    ):
        raise ValueError("closed-world flag bind shape differs")
    mount["source"] = FLAGD_BIND_SENTINEL_V1
    return NormalizedComposeBindingV1(
        service=service,
        mount_type="bind",
        target=target,
        read_only=read_only,
        json_pointer=f"/services/{service}/volumes/{index}/source",
        normalized_source=FLAGD_BIND_SENTINEL_V1,
    )


def build_resolved_compose_identity_v1(
    raw_compose: Mapping[str, object],
    *,
    expected_flagd_directory: Path,
    accepted_private_prf_root: Path,
    repository_root: Path,
    raw_contract_verifier: RawContractVerifier,
) -> ResolvedComposeIdentityV1:
    """Build the closed-world identity only after the full raw contract passes."""

    if not isinstance(raw_compose, Mapping):
        raise ValueError("raw resolved Compose must be an object")
    raw_contract_verifier(raw_compose)
    expected = _validate_flagd_directory(
        expected=expected_flagd_directory,
        accepted_private_prf_root=accepted_private_prf_root,
        repository_root=repository_root,
    )
    normalized_value = copy.deepcopy(dict(raw_compose))
    if not isinstance(normalized_value, dict):
        raise ValueError("raw resolved Compose must be an object")
    bindings = (
        _normalize_binding(
            normalized_value,
            service="flagd",
            target="/etc/flagd",
            expected_source=str(expected),
            expected_read_only=True,
        ),
        _normalize_binding(
            normalized_value,
            service="flagd-ui",
            target="/app/data",
            expected_source=str(expected),
            expected_read_only=False,
        ),
    )
    services = _require_mapping(raw_compose.get("services"), label="Compose services")
    payload = {
        "schema_version": "dta-v21.pr-f-resolved-compose-identity.v1",
        "raw_compose_sha256": semantic_sha256(dict(raw_compose)),
        "execution_compose_sha256": semantic_sha256(normalized_value),
        "normalization_policy_id": COMPOSE_NORMALIZATION_POLICY_ID_V1,
        "normalized_bind_count": 2,
        "normalized_bindings": tuple(item.model_dump(mode="json") for item in bindings),
        "raw_flagd_source_sha256": _sha_text(str(expected)),
        "raw_flagd_ui_source_sha256": _sha_text(str(expected)),
        "resolved_service_inventory_sha256": semantic_sha256(tuple(sorted(services))),
    }
    return ResolvedComposeIdentityV1.model_validate(
        {**payload, "identity_sha256": semantic_sha256(payload)}
    )


def _structural_diff(
    first: object, second: object, *, pointer: str = ""
) -> tuple[tuple[str, object, object], ...]:
    if isinstance(first, Mapping) and isinstance(second, Mapping):
        result: list[tuple[str, object, object]] = []
        for key in sorted(set(first) | set(second), key=str):
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            child = f"{pointer}/{escaped}"
            if key not in first:
                result.append((child, None, second[key]))
            elif key not in second:
                result.append((child, first[key], None))
            else:
                result.extend(_structural_diff(first[key], second[key], pointer=child))
        return tuple(result)
    if isinstance(first, Sequence) and not isinstance(first, (str, bytes)) and isinstance(
        second, Sequence
    ) and not isinstance(second, (str, bytes)):
        if len(first) != len(second):
            return ((pointer, first, second),)
        result = []
        for index, (left, right) in enumerate(zip(first, second, strict=True)):
            result.extend(_structural_diff(left, right, pointer=f"{pointer}/{index}"))
        return tuple(result)
    return () if first == second else ((pointer, first, second),)


def verify_cross_context_compose_identity_v1(
    *,
    first_raw: Mapping[str, object],
    first_identity: ResolvedComposeIdentityV1,
    first_expected_flagd_directory: Path,
    second_raw: Mapping[str, object],
    second_identity: ResolvedComposeIdentityV1,
    second_expected_flagd_directory: Path,
) -> None:
    """Require equality after, and difference only within, the closed policy."""

    if first_identity.execution_compose_sha256 != second_identity.execution_compose_sha256:
        raise ValueError("cross-context execution Compose identity differs")
    if first_identity.normalized_bindings != second_identity.normalized_bindings:
        raise ValueError("cross-context normalized Compose bindings differ")
    expected_pointers = {
        item.json_pointer for item in first_identity.normalized_bindings
    }
    differences = _structural_diff(first_raw, second_raw)
    if len(differences) != 2 or {item[0] for item in differences} != expected_pointers:
        raise ValueError("cross-context raw Compose diff exceeds closed-world sources")
    first_expected = str(Path(first_expected_flagd_directory).resolve(strict=True))
    second_expected = str(Path(second_expected_flagd_directory).resolve(strict=True))
    if any(left != first_expected or right != second_expected for _, left, right in differences):
        raise ValueError("cross-context raw Compose sources differ from exact contexts")


class CurrentResourceQuiescenceV1(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f-current-resource-quiescence.v1"]
    observed_at: datetime
    docker_boundary: Literal["LOCAL_UNIX_DOCKER"]
    owned_container_count: Literal[0]
    owned_network_count: Literal[0]
    owned_volume_count: Literal[0]
    required_ports_available: Literal[True]
    execution_lease_held: Literal[False]
    private_permissions_verified: Literal[True]
    source_worktree_clean: Literal[True]
    pr_d_frozen_bindings_verified: Literal[True]
    pr_e_frozen_bindings_verified: Literal[True]
    observation_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {
            "schema_version": "dta-v21.pr-f-current-resource-quiescence.v1",
            **values,
        }
        return cls.model_validate(
            {**payload, "observation_sha256": _semantic(payload)}
        )

    @model_validator(mode="after")
    def require_quiescence(self) -> Self:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("quiescence observation timestamp requires UTC")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"observation_sha256"})
        )
        if self.observation_sha256 != expected:
            raise ValueError("quiescence observation SHA-256 mismatch")
        return self


class PostTerminalReconciliationV1(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f-post-terminal-reconciliation.v1"]
    amendment_raw_sha256: Literal[
        "ea6740bce0ba63e093cda2807aea886d4ca48907702a2bf41ad1eedd0e2ab164"
    ]
    decision_id: Literal["DEC-045"]
    blocked_code_head: Literal["422f015451fd0a37f1442aa770fcffff75336aaa"]
    blocked_attempt_id: Literal["dta-v21-prf-01-no-fault-422f015451fd"]
    blocked_attempt_claim_raw_sha256: Sha256V21
    blocked_attempt_claim_semantic_sha256: Sha256V21
    blocked_attempt_terminal_raw_sha256: Sha256V21
    blocked_attempt_terminal_semantic_sha256: Sha256V21
    blocked_readiness_raw_sha256: Sha256V21
    blocked_readiness_semantic_sha256: Sha256V21
    preflight_resolved_compose_file_raw_sha256: Sha256V21
    blocked_attempt_resolved_compose_file_raw_sha256: Sha256V21
    preflight_raw_resolved_compose_sha256: Sha256V21
    blocked_attempt_raw_resolved_compose_sha256: Sha256V21
    preflight_execution_compose_sha256: Sha256V21
    blocked_attempt_execution_compose_sha256: Sha256V21
    preflight_compose_identity_sha256: Sha256V21
    blocked_attempt_compose_identity_sha256: Sha256V21
    master_authorization_raw_sha256: Sha256V21
    master_authorization_semantic_sha256: Sha256V21
    master_authorization_sha256: Sha256V21
    protocol_freeze_raw_sha256: Sha256V21
    protocol_freeze_semantic_sha256: Sha256V21
    ad_protocol_sha256: Literal[
        "c983b9be95b532cdbb8fb5358af92055e633fd767693e9dc65743b3e80a77517"
    ]
    current_quiescence_observation_sha256: Sha256V21
    compose_diff_json_pointers: tuple[
        Literal["/services/flagd/volumes/0/source"],
        Literal["/services/flagd-ui/volumes/0/source"],
    ]
    classification: Literal["PRE_BASELINE_HARNESS_IDENTITY_MISMATCH"]
    historical_attempt_status: Literal["BLOCKED"]
    historical_cleanup_status: Literal["BLOCKED"]
    historical_baseline_restoration_proven: Literal[False]
    historical_fault_observed: Literal[False]
    historical_provider_called: Literal[False]
    historical_forward_action_observed: Literal[False]
    historical_remaining_owned_resources: Literal[0]
    historical_non_owned_change_observed: Literal[False]
    historical_artifact_absence_proven: Literal[True]
    closed_world_compose_difference_proven: Literal[True]
    current_resource_quiescence_proven: Literal[True]
    retry_eligible: Literal[True]
    reconciliation_sha256: Sha256V21

    @model_validator(mode="after")
    def require_reconciliation(self) -> Self:
        if (
            self.preflight_execution_compose_sha256
            != self.blocked_attempt_execution_compose_sha256
        ):
            raise ValueError("reconciled execution Compose hashes differ")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"reconciliation_sha256"})
        )
        if self.reconciliation_sha256 != expected:
            raise ValueError("post-terminal reconciliation SHA-256 mismatch")
        return self


@dataclass(frozen=True)
class HistoricalBlockerEligibilityV1:
    readiness_raw_sha256: str
    readiness_semantic_sha256: str
    attempt_claim_raw_sha256: str
    attempt_claim_semantic_sha256: str
    attempt_terminal_raw_sha256: str
    attempt_terminal_semantic_sha256: str
    preflight_compose_raw_file_sha256: str
    attempt_compose_raw_file_sha256: str
    preflight_identity: ResolvedComposeIdentityV1
    attempt_identity: ResolvedComposeIdentityV1
    preflight_raw: dict[str, object]
    attempt_raw: dict[str, object]
    preflight_flagd_directory: Path
    attempt_flagd_directory: Path
    master_raw_sha256: str
    master_semantic_sha256: str
    master_authorization_sha256: str
    protocol_freeze_raw_sha256: str
    protocol_freeze_semantic_sha256: str


def _raw_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("immutable historical artifact is missing or unsafe")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("immutable historical artifact is missing or unsafe")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("immutable historical artifact is not an object")
    return value


def _require_immutable_hash(
    *, label: str, path: Path, value: Mapping[str, object]
) -> tuple[str, str]:
    raw = _raw_sha256(path)
    semantic = semantic_sha256(dict(value))
    if raw != _IMMUTABLE_RAW_SHA256[label] or semantic != _IMMUTABLE_SEMANTIC_SHA256[label]:
        raise ValueError(f"immutable historical {label} hash differs")
    return raw, semantic


def verify_historical_blocker_eligibility_v1(
    *,
    repository_root: Path,
    private_root: Path,
    require_only_historical_attempt: bool = False,
) -> HistoricalBlockerEligibilityV1:
    """Re-prove the one exact historical blocker without modifying it."""

    repository = Path(repository_root).resolve(strict=True)
    private = Path(private_root).resolve(strict=True)
    prf = private / "pr-f"
    verify_private_tree_permissions(prf)
    attempts_root = prf / "attempts"
    attempt_root = attempts_root / BLOCKED_ATTEMPT_ID_V1
    if not attempts_root.is_dir() or not attempt_root.is_dir() or attempt_root.is_symlink():
        raise ValueError("historical blocked attempt is missing, renamed, or unsafe")
    if require_only_historical_attempt and {
        item.name for item in attempts_root.iterdir()
    } != {BLOCKED_ATTEMPT_ID_V1}:
        raise ValueError("pre-retry attempt set differs from the exact blocker")

    readiness_path = prf / "readiness" / BLOCKED_CODE_HEAD_V1 / "readiness.json"
    readiness_value = _read_json_object(readiness_path)
    readiness = LiveReadinessV21.model_validate(readiness_value)
    readiness_raw, readiness_semantic = _require_immutable_hash(
        label="readiness", path=readiness_path, value=readiness_value
    )
    readiness_copy_path = (
        prf
        / "readiness"
        / BLOCKED_CODE_HEAD_V1
        / "attempts"
        / readiness.readiness_attempt_id
        / "readiness.json"
    )
    if readiness_copy_path.read_bytes() != readiness_path.read_bytes():
        raise ValueError("historical readiness pointer and attempt copy differ")

    claim_path = attempt_root / "attempt-claim.json"
    terminal_path = attempt_root / "attempt-terminal.json"
    claim = _read_json_object(claim_path)
    terminal = _read_json_object(terminal_path)
    claim_raw, claim_semantic = _require_immutable_hash(
        label="attempt_claim", path=claim_path, value=claim
    )
    terminal_raw, terminal_semantic = _require_immutable_hash(
        label="attempt_terminal", path=terminal_path, value=terminal
    )
    if claim != {
        "schema_version": "dta-v21.live-attempt-claim.v1",
        "attempt_id": BLOCKED_ATTEMPT_ID_V1,
        "scenario": "NO_FAULT",
        "ordinal": 1,
        "code_head": BLOCKED_CODE_HEAD_V1,
        "master_authorization_sha256": (
            "fca0e8646806aeb164b0778ff129bb9b316fa5791b52fd049b9c590c4664dc97"
        ),
        "protocol_sha256": AD_PROTOCOL_SHA256_V1,
        "live_config_sha256": (
            "bbb17dd522c8190ad23ab40d7696ec981e5d4fad77dd9e66977228940046959a"
        ),
        "readiness_sha256": readiness.readiness_sha256,
    }:
        raise ValueError("historical attempt claim differs")
    cleanup = terminal.get("cleanup")
    if terminal != {
        "schema_version": "dta-v21.live-attempt-failure.v1",
        "attempt_id": BLOCKED_ATTEMPT_ID_V1,
        "scenario": "NO_FAULT",
        "stage": "READY",
        "terminal": "BLOCKED_DTA_V21_PRF_SAFETY",
        "baseline_restored": False,
        "cleanup": cleanup,
        "failure_type": "RuntimeError",
        "raw_error_retained": False,
        "restoration_operation_failed": False,
    } or cleanup != {
        "baseline_restored": False,
        "owned_containers": 0,
        "owned_networks": 0,
        "owned_volumes": 0,
        "non_owned_resources_changed": False,
        "verdict": "BLOCKED",
    }:
        raise ValueError("historical blocked terminal truth differs")
    if any((attempt_root / name).exists() or (attempt_root / name).is_symlink() for name in _FORBIDDEN_HISTORICAL_ARTIFACTS):
        raise ValueError("historical blocker contains a forbidden later-stage artifact")

    preflight_root = readiness_copy_path.parent / "owned-preflight"
    preflight_compose_path = preflight_root / "control/resolved-compose.json"
    attempt_owned_root = attempt_root / "owned-sandbox"
    attempt_compose_path = attempt_owned_root / "control/resolved-compose.json"
    preflight_raw = _read_json_object(preflight_compose_path)
    attempt_raw = _read_json_object(attempt_compose_path)
    preflight_raw_file_sha = _raw_sha256(preflight_compose_path)
    attempt_raw_file_sha = _raw_sha256(attempt_compose_path)
    if (
        preflight_raw_file_sha != _IMMUTABLE_RAW_SHA256["preflight_compose"]
        or attempt_raw_file_sha != _IMMUTABLE_RAW_SHA256["attempt_compose"]
    ):
        raise ValueError("historical resolved Compose bytes differ")
    bundle = load_bundle(
        repository / "config/live-telemetry-controlled-remediation-v1"
    )
    preflight_flagd = preflight_root / "runtime/flagd"
    attempt_flagd = attempt_owned_root / "runtime/flagd"
    preflight_environment = SandboxEnvironment(
        repository_root=repository, bundle=bundle, flagd_directory=preflight_flagd
    )
    attempt_environment = SandboxEnvironment(
        repository_root=repository, bundle=bundle, flagd_directory=attempt_flagd
    )
    preflight_identity = build_resolved_compose_identity_v1(
        preflight_raw,
        expected_flagd_directory=preflight_flagd,
        accepted_private_prf_root=prf,
        repository_root=repository,
        raw_contract_verifier=preflight_environment._verify_resolved_contract,
    )
    attempt_identity = build_resolved_compose_identity_v1(
        attempt_raw,
        expected_flagd_directory=attempt_flagd,
        accepted_private_prf_root=prf,
        repository_root=repository,
        raw_contract_verifier=attempt_environment._verify_resolved_contract,
    )
    verify_cross_context_compose_identity_v1(
        first_raw=preflight_raw,
        first_identity=preflight_identity,
        first_expected_flagd_directory=preflight_flagd,
        second_raw=attempt_raw,
        second_identity=attempt_identity,
        second_expected_flagd_directory=attempt_flagd,
    )
    if (
        preflight_identity.raw_compose_sha256
        != "b5c9077cda26572e27ccda7de03802fb7a2ed8414f0e539bc3f34257bfcd9176"
        or attempt_identity.raw_compose_sha256
        != "e6c5ea3c6720d30fb69b385324df5cd5cec4728e09f672224714ee2c3b3a4024"
    ):
        raise ValueError("historical raw Compose semantic hashes differ")

    master_path = prf / "master-authorization.json"
    protocol_path = prf / "protocol-freeze.json"
    master = _read_json_object(master_path)
    protocol = _read_json_object(protocol_path)
    master_raw, master_semantic = _require_immutable_hash(
        label="master_authorization", path=master_path, value=master
    )
    protocol_raw, protocol_semantic = _require_immutable_hash(
        label="protocol_freeze", path=protocol_path, value=protocol
    )
    if (
        master.get("authorization_sha256") != claim["master_authorization_sha256"]
        or protocol.get("protocol_sha256") != AD_PROTOCOL_SHA256_V1
    ):
        raise ValueError("historical standing authorization or protocol differs")
    return HistoricalBlockerEligibilityV1(
        readiness_raw_sha256=readiness_raw,
        readiness_semantic_sha256=readiness_semantic,
        attempt_claim_raw_sha256=claim_raw,
        attempt_claim_semantic_sha256=claim_semantic,
        attempt_terminal_raw_sha256=terminal_raw,
        attempt_terminal_semantic_sha256=terminal_semantic,
        preflight_compose_raw_file_sha256=preflight_raw_file_sha,
        attempt_compose_raw_file_sha256=attempt_raw_file_sha,
        preflight_identity=preflight_identity,
        attempt_identity=attempt_identity,
        preflight_raw=preflight_raw,
        attempt_raw=attempt_raw,
        preflight_flagd_directory=preflight_flagd,
        attempt_flagd_directory=attempt_flagd,
        master_raw_sha256=master_raw,
        master_semantic_sha256=master_semantic,
        master_authorization_sha256=str(master["authorization_sha256"]),
        protocol_freeze_raw_sha256=protocol_raw,
        protocol_freeze_semantic_sha256=protocol_semantic,
    )


def build_post_terminal_reconciliation_v1(
    *,
    eligibility: HistoricalBlockerEligibilityV1,
    quiescence: CurrentResourceQuiescenceV1,
) -> PostTerminalReconciliationV1:
    payload: dict[str, object] = {
        "schema_version": "dta-v21.pr-f-post-terminal-reconciliation.v1",
        "amendment_raw_sha256": AMENDMENT_RAW_SHA256_V1,
        "decision_id": DECISION_ID_V1,
        "blocked_code_head": BLOCKED_CODE_HEAD_V1,
        "blocked_attempt_id": BLOCKED_ATTEMPT_ID_V1,
        "blocked_attempt_claim_raw_sha256": eligibility.attempt_claim_raw_sha256,
        "blocked_attempt_claim_semantic_sha256": eligibility.attempt_claim_semantic_sha256,
        "blocked_attempt_terminal_raw_sha256": eligibility.attempt_terminal_raw_sha256,
        "blocked_attempt_terminal_semantic_sha256": eligibility.attempt_terminal_semantic_sha256,
        "blocked_readiness_raw_sha256": eligibility.readiness_raw_sha256,
        "blocked_readiness_semantic_sha256": eligibility.readiness_semantic_sha256,
        "preflight_resolved_compose_file_raw_sha256": eligibility.preflight_compose_raw_file_sha256,
        "blocked_attempt_resolved_compose_file_raw_sha256": eligibility.attempt_compose_raw_file_sha256,
        "preflight_raw_resolved_compose_sha256": eligibility.preflight_identity.raw_compose_sha256,
        "blocked_attempt_raw_resolved_compose_sha256": eligibility.attempt_identity.raw_compose_sha256,
        "preflight_execution_compose_sha256": eligibility.preflight_identity.execution_compose_sha256,
        "blocked_attempt_execution_compose_sha256": eligibility.attempt_identity.execution_compose_sha256,
        "preflight_compose_identity_sha256": eligibility.preflight_identity.identity_sha256,
        "blocked_attempt_compose_identity_sha256": eligibility.attempt_identity.identity_sha256,
        "master_authorization_raw_sha256": eligibility.master_raw_sha256,
        "master_authorization_semantic_sha256": eligibility.master_semantic_sha256,
        "master_authorization_sha256": eligibility.master_authorization_sha256,
        "protocol_freeze_raw_sha256": eligibility.protocol_freeze_raw_sha256,
        "protocol_freeze_semantic_sha256": eligibility.protocol_freeze_semantic_sha256,
        "ad_protocol_sha256": AD_PROTOCOL_SHA256_V1,
        "current_quiescence_observation_sha256": quiescence.observation_sha256,
        "compose_diff_json_pointers": (
            "/services/flagd/volumes/0/source",
            "/services/flagd-ui/volumes/0/source",
        ),
        "classification": "PRE_BASELINE_HARNESS_IDENTITY_MISMATCH",
        "historical_attempt_status": "BLOCKED",
        "historical_cleanup_status": "BLOCKED",
        "historical_baseline_restoration_proven": False,
        "historical_fault_observed": False,
        "historical_provider_called": False,
        "historical_forward_action_observed": False,
        "historical_remaining_owned_resources": 0,
        "historical_non_owned_change_observed": False,
        "historical_artifact_absence_proven": True,
        "closed_world_compose_difference_proven": True,
        "current_resource_quiescence_proven": True,
        "retry_eligible": True,
    }
    return PostTerminalReconciliationV1.model_validate(
        {**payload, "reconciliation_sha256": semantic_sha256(payload)}
    )


def write_post_terminal_reconciliation_v1(
    *,
    repository_root: Path,
    private_root: Path,
    quiescence: CurrentResourceQuiescenceV1,
) -> PostTerminalReconciliationV1:
    eligibility = verify_historical_blocker_eligibility_v1(
        repository_root=repository_root,
        private_root=private_root,
        require_only_historical_attempt=True,
    )
    record = build_post_terminal_reconciliation_v1(
        eligibility=eligibility, quiescence=quiescence
    )
    root = (
        Path(private_root)
        / "pr-f/reconciliations"
        / BLOCKED_ATTEMPT_ID_V1
    )
    ensure_private_directory(root)
    write_private_json(root / "quiescence.v1.json", quiescence, create_once=True)
    write_private_json(root / "reconciliation.v1.json", record, create_once=True)
    return record


def verify_post_terminal_reconciliation_v1(
    *, repository_root: Path, private_root: Path
) -> tuple[PostTerminalReconciliationV1, CurrentResourceQuiescenceV1]:
    root = (
        Path(private_root)
        / "pr-f/reconciliations"
        / BLOCKED_ATTEMPT_ID_V1
    )
    quiescence = CurrentResourceQuiescenceV1.model_validate_json(
        (root / "quiescence.v1.json").read_text(encoding="utf-8")
    )
    observed = PostTerminalReconciliationV1.model_validate_json(
        (root / "reconciliation.v1.json").read_text(encoding="utf-8")
    )
    eligibility = verify_historical_blocker_eligibility_v1(
        repository_root=repository_root, private_root=private_root
    )
    expected = build_post_terminal_reconciliation_v1(
        eligibility=eligibility, quiescence=quiescence
    )
    if observed != expected:
        raise ValueError("stored post-terminal reconciliation differs")
    return observed, quiescence


class IndependentRetryReviewV1(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f-independent-retry-review.v1"]
    code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    reviewer: str = Field(min_length=1, max_length=128)
    reviewed_at: datetime
    must_fix_count: Literal[0]
    should_fix_count: Literal[0]
    claim_accuracy: Literal["PASS"]
    review_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {
            "schema_version": "dta-v21.pr-f-independent-retry-review.v1",
            **values,
        }
        return cls.model_validate({**payload, "review_sha256": _semantic(payload)})

    @model_validator(mode="after")
    def require_review(self) -> Self:
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() != timedelta(0):
            raise ValueError("independent review timestamp requires UTC")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"review_sha256"})
        )
        if self.review_sha256 != expected:
            raise ValueError("independent retry review SHA-256 mismatch")
        return self


class RetryAdmissionV1(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f-retry-admission.v1"]
    verdict: Literal["ALLOW_ONE_NEW_CAMPAIGN"]
    resume_mode: Literal["NEW_CAMPAIGN_FROM_SLOT_1"]
    blocked_attempt_reused: Literal[False]
    historical_attempt_immutable: Literal[True]
    maximum_new_campaigns: Literal[1]
    maximum_retry_campaigns_after_consumption: Literal[0]
    blocked_code_head: Literal["422f015451fd0a37f1442aa770fcffff75336aaa"]
    new_code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    amendment_sha256: Literal[
        "ea6740bce0ba63e093cda2807aea886d4ca48907702a2bf41ad1eedd0e2ab164"
    ]
    decision_id: Literal["DEC-045"]
    reconciliation_sha256: Sha256V21
    exact_head_ci_run_id: StrictInt = Field(ge=1)
    exact_head_ci_run_url: str = Field(pattern=r"^https://github\.com/.+")
    independent_review_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    independent_review_sha256: Sha256V21
    independent_review_must_fix_count: Literal[0]
    independent_review_should_fix_count: Literal[0]
    independent_review_claim_accuracy: Literal["PASS"]
    v2_readiness_sha256: Sha256V21
    master_authorization_sha256: Sha256V21
    admitted_first_scenario: Literal["NO_FAULT"]
    admission_sha256: Sha256V21

    @model_validator(mode="after")
    def require_admission(self) -> Self:
        if (
            self.new_code_head == self.blocked_code_head
            or self.independent_review_head != self.new_code_head
        ):
            raise ValueError("retry admission exact-head binding differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"admission_sha256"})
        )
        if self.admission_sha256 != expected:
            raise ValueError("retry admission SHA-256 mismatch")
        return self


class RetryConsumptionV1(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f-retry-consumption.v1"]
    status: Literal["CONSUMED"]
    reconciliation_sha256: Sha256V21
    retry_admission_sha256: Sha256V21
    consumed_by_code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    consumed_for_campaign: Literal["FOUR_SLOT_PRF_CAMPAIGN_FROM_SLOT_1"]
    first_scenario: Literal["NO_FAULT"]
    historical_attempt_reused: Literal[False]
    maximum_additional_campaigns: Literal[0]
    consumed_at: datetime
    consumption_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {
            "schema_version": "dta-v21.pr-f-retry-consumption.v1",
            **values,
        }
        return cls.model_validate(
            {**payload, "consumption_sha256": _semantic(payload)}
        )

    @model_validator(mode="after")
    def require_consumption(self) -> Self:
        if self.consumed_at.tzinfo is None or self.consumed_at.utcoffset() != timedelta(0):
            raise ValueError("retry consumption timestamp requires UTC")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"consumption_sha256"})
        )
        if self.consumption_sha256 != expected:
            raise ValueError("retry consumption SHA-256 mismatch")
        return self


def build_retry_admission_v1(
    *,
    new_code_head: str,
    reconciliation: PostTerminalReconciliationV1,
    readiness: LiveReadinessV2,
    review: IndependentRetryReviewV1,
) -> RetryAdmissionV1:
    if (
        not reconciliation.retry_eligible
        or readiness.code_head != new_code_head
        or review.code_head != new_code_head
        or readiness.exact_head_ci_success is not True
    ):
        raise ValueError("retry admission prerequisite differs")
    payload: dict[str, object] = {
        "schema_version": "dta-v21.pr-f-retry-admission.v1",
        "verdict": "ALLOW_ONE_NEW_CAMPAIGN",
        "resume_mode": "NEW_CAMPAIGN_FROM_SLOT_1",
        "blocked_attempt_reused": False,
        "historical_attempt_immutable": True,
        "maximum_new_campaigns": 1,
        "maximum_retry_campaigns_after_consumption": 0,
        "blocked_code_head": BLOCKED_CODE_HEAD_V1,
        "new_code_head": new_code_head,
        "amendment_sha256": AMENDMENT_RAW_SHA256_V1,
        "decision_id": DECISION_ID_V1,
        "reconciliation_sha256": reconciliation.reconciliation_sha256,
        "exact_head_ci_run_id": readiness.exact_head_ci_run_id,
        "exact_head_ci_run_url": readiness.exact_head_ci_run_url,
        "independent_review_head": review.code_head,
        "independent_review_sha256": review.review_sha256,
        "independent_review_must_fix_count": review.must_fix_count,
        "independent_review_should_fix_count": review.should_fix_count,
        "independent_review_claim_accuracy": review.claim_accuracy,
        "v2_readiness_sha256": readiness.readiness_sha256,
        "master_authorization_sha256": readiness.master_authorization_sha256,
        "admitted_first_scenario": "NO_FAULT",
    }
    return RetryAdmissionV1.model_validate(
        {**payload, "admission_sha256": semantic_sha256(payload)}
    )


def write_independent_retry_review_v1(
    *, private_root: Path, review: IndependentRetryReviewV1
) -> None:
    path = Path(private_root) / "pr-f/reviews" / review.code_head / "review.v1.json"
    write_private_json(path, review, create_once=True)


def _expected_retry_admission_v1(
    *,
    repository_root: Path,
    private_root: Path,
    new_code_head: str,
) -> RetryAdmissionV1:
    reconciliation, _ = verify_post_terminal_reconciliation_v1(
        repository_root=repository_root, private_root=private_root
    )
    readiness = LiveReadinessV2.model_validate_json(
        (
            Path(private_root)
            / "pr-f/readiness"
            / new_code_head
            / "readiness.json"
        ).read_text(encoding="utf-8")
    )
    review = IndependentRetryReviewV1.model_validate_json(
        (
            Path(private_root)
            / "pr-f/reviews"
            / new_code_head
            / "review.v1.json"
        ).read_text(encoding="utf-8")
    )
    return build_retry_admission_v1(
        new_code_head=new_code_head,
        reconciliation=reconciliation,
        readiness=readiness,
        review=review,
    )


def write_retry_admission_v1(
    *,
    repository_root: Path,
    private_root: Path,
    new_code_head: str,
) -> RetryAdmissionV1:
    admission = _expected_retry_admission_v1(
        repository_root=repository_root,
        private_root=private_root,
        new_code_head=new_code_head,
    )
    path = (
        Path(private_root)
        / "pr-f/retry-admissions"
        / new_code_head
        / "retry-admission.v1.json"
    )
    write_private_json(path, admission, create_once=True)
    return admission


def verify_retry_admission_v1(
    *, repository_root: Path, private_root: Path, new_code_head: str
) -> RetryAdmissionV1:
    expected = _expected_retry_admission_v1(
        repository_root=repository_root,
        private_root=private_root,
        new_code_head=new_code_head,
    )
    path = (
        Path(private_root)
        / "pr-f/retry-admissions"
        / new_code_head
        / "retry-admission.v1.json"
    )
    observed = RetryAdmissionV1.model_validate_json(path.read_text(encoding="utf-8"))
    if observed != expected:
        raise ValueError("stored retry admission differs")
    return observed


def _write_exclusive_private_json(path: Path, value: object) -> None:
    ensure_private_directory(path.parent)
    payload = canonical_json_bytes(value)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise RuntimeError("BLOCKED_DTA_V21_PRF_RETRY_EXHAUSTED") from error
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def consume_retry_admission_v1(
    *,
    repository_root: Path,
    private_root: Path,
    new_code_head: str,
    consumed_at: datetime,
) -> RetryConsumptionV1:
    admission = verify_retry_admission_v1(
        repository_root=repository_root,
        private_root=private_root,
        new_code_head=new_code_head,
    )
    attempts_root = Path(private_root) / "pr-f/attempts"
    if {item.name for item in attempts_root.iterdir()} != {BLOCKED_ATTEMPT_ID_V1}:
        raise RuntimeError("BLOCKED_DTA_V21_PRF_RETRY_EXHAUSTED")
    consumption = RetryConsumptionV1.build(
        status="CONSUMED",
        reconciliation_sha256=admission.reconciliation_sha256,
        retry_admission_sha256=admission.admission_sha256,
        consumed_by_code_head=new_code_head,
        consumed_for_campaign="FOUR_SLOT_PRF_CAMPAIGN_FROM_SLOT_1",
        first_scenario="NO_FAULT",
        historical_attempt_reused=False,
        maximum_additional_campaigns=0,
        consumed_at=consumed_at,
    )
    path = (
        Path(private_root)
        / "pr-f/retry-consumptions"
        / f"{admission.reconciliation_sha256}.json"
    )
    _write_exclusive_private_json(path, consumption)
    return consumption


def verify_retry_consumption_v1(
    *, repository_root: Path, private_root: Path, new_code_head: str
) -> RetryConsumptionV1:
    admission = verify_retry_admission_v1(
        repository_root=repository_root,
        private_root=private_root,
        new_code_head=new_code_head,
    )
    path = (
        Path(private_root)
        / "pr-f/retry-consumptions"
        / f"{admission.reconciliation_sha256}.json"
    )
    value = RetryConsumptionV1.model_validate_json(path.read_text(encoding="utf-8"))
    if (
        value.retry_admission_sha256 != admission.admission_sha256
        or value.consumed_by_code_head != new_code_head
    ):
        raise ValueError("retry consumption binding differs")
    return value


__all__ = (
    "AD_PROTOCOL_SHA256_V1",
    "AMENDMENT_RAW_SHA256_V1",
    "BLOCKED_ATTEMPT_ID_V1",
    "BLOCKED_CODE_HEAD_V1",
    "COMPOSE_NORMALIZATION_POLICY_ID_V1",
    "CurrentResourceQuiescenceV1",
    "DECISION_ID_V1",
    "FLAGD_BIND_SENTINEL_V1",
    "IndependentRetryReviewV1",
    "NormalizedComposeBindingV1",
    "PostTerminalReconciliationV1",
    "ResolvedComposeIdentityV1",
    "RetryAdmissionV1",
    "RetryConsumptionV1",
    "build_post_terminal_reconciliation_v1",
    "build_resolved_compose_identity_v1",
    "build_retry_admission_v1",
    "consume_retry_admission_v1",
    "verify_historical_blocker_eligibility_v1",
    "verify_post_terminal_reconciliation_v1",
    "verify_cross_context_compose_identity_v1",
    "verify_retry_admission_v1",
    "verify_retry_consumption_v1",
    "write_independent_retry_review_v1",
    "write_post_terminal_reconciliation_v1",
    "write_retry_admission_v1",
)
