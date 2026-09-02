"""Live traffic-preflight and formal-freeze contracts for Product v0.2.3.3."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.pilot.healthy_traffic_v0232 import HealthyTrafficExecutionV0232


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
ALLOWED_REPAIR_SURFACES_V0233 = (
    "scripts/product_v0233/run_traffic_preflight.py",
    "src/ecomsre/product/pilot/traffic_preflight_v0233.py",
)
TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0233: Literal[
    "ECOMSRE_PRODUCT_V0233_TRAFFIC_PREFLIGHT_ATTEMPT_PASS"
] = (
    "ECOMSRE_PRODUCT_V0233_TRAFFIC_PREFLIGHT_ATTEMPT_PASS"
)
TRAFFIC_PREFLIGHT_ATTEMPT_BLOCKED_V0233: Literal[
    "BLOCKED_ECOMSRE_PRODUCT_V0233_TRAFFIC_PREFLIGHT_ATTEMPT"
] = (
    "BLOCKED_ECOMSRE_PRODUCT_V0233_TRAFFIC_PREFLIGHT_ATTEMPT"
)
TRAFFIC_PREFLIGHT_PASS_V0233: Literal[
    "ECOMSRE_PRODUCT_V0233_TRAFFIC_PREFLIGHT_PASS"
] = "ECOMSRE_PRODUCT_V0233_TRAFFIC_PREFLIGHT_PASS"


class DemoCleanupV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: Literal["CLEAN", "BLOCKED"]
    owned_containers: int = Field(ge=0)
    owned_networks: int = Field(ge=0)
    owned_volumes: int = Field(ge=0)
    non_owned_resources_changed: bool

    @property
    def clean(self) -> bool:
        return (
            self.verdict == "CLEAN"
            and self.owned_containers == 0
            and self.owned_networks == 0
            and self.owned_volumes == 0
            and not self.non_owned_resources_changed
        )


class ProductCleanupV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: Literal["CLEAN", "BLOCKED"]
    owned_host_processes: int = Field(ge=0)
    database_owner_count_before: int = Field(ge=0)
    database_owner_count_after: int = Field(ge=0)
    product_api_port_available: bool
    non_owned_resources_changed: bool

    @property
    def clean(self) -> bool:
        return (
            self.verdict == "CLEAN"
            and self.owned_host_processes == 0
            and self.database_owner_count_before == 0
            and self.database_owner_count_after == 0
            and self.product_api_port_available
            and not self.non_owned_resources_changed
        )


class TrafficRepairSurfaceSnapshotV0233(ProductModelV1):
    """Exact post-attempt surface used to prove a later targeted repair."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.traffic-repair-surface.v0233"] = (
        "ecomsre.product.traffic-repair-surface.v0233"
    )
    phase: Literal["POST_ATTEMPT_PRE_REPAIR"] = "POST_ATTEMPT_PRE_REPAIR"
    attempt_ordinal: int = Field(ge=1)
    attempt_sha256: str = Field(pattern=_SHA256_PATTERN)
    allowed_surface_paths: tuple[str, ...]
    source_sha256_by_path: dict[str, str]
    snapshot_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_surface_and_seal(self) -> TrafficRepairSurfaceSnapshotV0233:
        expected_paths = ALLOWED_REPAIR_SURFACES_V0233
        if (
            self.allowed_surface_paths != expected_paths
            or tuple(self.source_sha256_by_path) != expected_paths
            or any(
                len(value) != 64 for value in self.source_sha256_by_path.values()
            )
        ):
            raise ValueError("Product v0.2.3.3 repair surface differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"snapshot_sha256"})
        )
        if self.snapshot_sha256 != expected:
            raise ValueError("Product v0.2.3.3 repair surface digest differs")
        return self


class FormalClonePlanV0233(ProductModelV1):
    """Complete clone-only plan frozen before the one formal clone exists."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-clone-plan.v0233"] = (
        "ecomsre.product.formal-clone-plan.v0233"
    )
    campaign_id: Literal["product-v0233-fresh-formal-nofault"]
    source_selection_sha256: str = Field(pattern=_SHA256_PATTERN)
    destination_locator: Literal[
        ".local/product-v0233/formal-state/"
        "product-v0233-fresh-formal-nofault/product"
    ]
    clone_method: Literal["SQLITE_ONLINE_BACKUP_AND_OBJECT_COPY"]
    clone_limit: Literal[1] = 1
    post_migration_schema_version: Literal[9] = 9
    formal_clone_count: Literal[0] = 0
    plan_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_seal(self) -> FormalClonePlanV0233:
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"plan_sha256"})
        )
        if self.plan_sha256 != expected:
            raise ValueError("Product v0.2.3.3 formal clone plan digest differs")
        return self

    @classmethod
    def build(cls, *, source_selection_sha256: str) -> FormalClonePlanV0233:
        body = {
            "schema_version": "ecomsre.product.formal-clone-plan.v0233",
            "campaign_id": "product-v0233-fresh-formal-nofault",
            "source_selection_sha256": source_selection_sha256,
            "destination_locator": (
                ".local/product-v0233/formal-state/"
                "product-v0233-fresh-formal-nofault/product"
            ),
            "clone_method": "SQLITE_ONLINE_BACKUP_AND_OBJECT_COPY",
            "clone_limit": 1,
            "post_migration_schema_version": 9,
            "formal_clone_count": 0,
        }
        return cls.model_validate(
            {**body, "plan_sha256": semantic_sha256_v22(body)}
        )


class DiagnosisSemanticSourceManifestV0233(ProductModelV1):
    """Explicit static source closure for the ordinary Diagnosis path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "ecomsre.product.diagnosis-semantic-source-manifest.v0233"
    ] = "ecomsre.product.diagnosis-semantic-source-manifest.v0233"
    entry_point_paths: tuple[str, ...] = Field(min_length=1)
    source_sha256_by_path: dict[str, str]
    source_count: int = Field(ge=1)
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_complete_sorted_seal(self) -> DiagnosisSemanticSourceManifestV0233:
        required = {
            "src/ecomsre/product/api.py",
            "src/ecomsre/product/jobs/worker.py",
            "src/ecomsre/product/jobs/handlers.py",
            "src/ecomsre/product/incidents/diagnosis_pipeline_v02322.py",
            "src/ecomsre/product/incidents/diagnosis_stage_journal_v02322.py",
            "src/ecomsre/product/incidents/diagnosis_bridge.py",
            "src/ecomsre/product/incidents/read_backend.py",
            "src/ecomsre/product/incidents/repository.py",
            "src/ecomsre/product/pilot/nofault_acceptance_v0232.py",
        }
        paths = tuple(self.source_sha256_by_path)
        if (
            self.entry_point_paths != tuple(sorted(set(self.entry_point_paths)))
            or paths != tuple(sorted(set(paths)))
            or not required.issubset(paths)
            or not set(self.entry_point_paths).issubset(paths)
            or self.source_count != len(paths)
            or any(len(value) != 64 for value in self.source_sha256_by_path.values())
        ):
            raise ValueError("Product v0.2.3.3 Diagnosis source manifest differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected:
            raise ValueError("Product v0.2.3.3 Diagnosis source manifest digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        entry_point_paths: tuple[str, ...],
        source_sha256_by_path: dict[str, str],
    ) -> DiagnosisSemanticSourceManifestV0233:
        body = {
            "schema_version": (
                "ecomsre.product.diagnosis-semantic-source-manifest.v0233"
            ),
            "entry_point_paths": sorted(set(entry_point_paths)),
            "source_sha256_by_path": dict(sorted(source_sha256_by_path.items())),
            "source_count": len(source_sha256_by_path),
        }
        return cls.model_validate(
            {**body, "manifest_sha256": semantic_sha256_v22(body)}
        )


class TrafficPreflightAttemptV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.traffic-preflight-attempt.v0233"] = (
        "ecomsre.product.traffic-preflight-attempt.v0233"
    )
    terminal: Literal[
        "ECOMSRE_PRODUCT_V0233_TRAFFIC_PREFLIGHT_ATTEMPT_PASS",
        "BLOCKED_ECOMSRE_PRODUCT_V0233_TRAFFIC_PREFLIGHT_ATTEMPT",
    ]
    attempt_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,79}$")
    attempt_ordinal: int = Field(ge=1)
    prior_attempt_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    changed_surface: str | None = Field(default=None, min_length=1, max_length=240)
    changed_surface_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    campaign_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_selection_sha256: str = Field(pattern=_SHA256_PATTERN)
    profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    engine_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    typed_request_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    flagd_bind_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_continuity_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    resolved_compose_sha256: str = Field(pattern=_SHA256_PATTERN)
    read_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    pilot_runtime_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    checkout_state: Literal["RUNNING"]
    checkout_healthy: Literal[True]
    checkout_restart_count: Literal[0]
    execution: HealthyTrafficExecutionV0232
    queue_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    queue_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    outer_baseline_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    outer_baseline_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_state_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_state_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_incident_count: int = Field(ge=0)
    source_diagnosis_count: int = Field(ge=0)
    demo_cleanup: DemoCleanupV0233
    product_cleanup: ProductCleanupV0233
    formal_clone_count: Literal[0] = 0
    formal_execution_count: Literal[0] = 0
    new_incident_count: Literal[0] = 0
    new_diagnosis_count: Literal[0] = 0
    measured_result_count: Literal[0] = 0
    fault_attempt_count: Literal[0] = 0
    provider_calls: Literal[0] = 0
    agent_writes: Literal[0] = 0
    runbook_executions: Literal[0] = 0
    action_authority: Literal["NONE"] = "NONE"
    attempt_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_attempt_and_seal(self) -> TrafficPreflightAttemptV0233:
        changed = (
            self.prior_attempt_sha256,
            self.changed_surface,
            self.changed_surface_sha256,
        )
        if self.attempt_ordinal == 1:
            if changed != (None, None, None):
                raise ValueError("first traffic attempt changed surface differs")
        elif any(value is None for value in changed):
            raise ValueError("later traffic attempt requires changed surface")
        run = self.execution.run
        passed = (
            run.role == "PREFLIGHT"
            and run.profile_sha256 == self.engine_profile_sha256
            and run.contract_sha256 == self.traffic_contract_sha256
            and run.planned_transactions == 10
            and run.completed_transactions == 10
            and run.successful_transactions == 10
            and run.failed_transactions == 0
            and run.transport_retry_count == 0
            and not run.stage_failure_counts
            and self.queue_before_sha256 == self.queue_after_sha256
            and self.outer_baseline_before_sha256
            == self.outer_baseline_after_sha256
            and self.source_state_before_sha256 == self.source_state_after_sha256
            and self.demo_cleanup.clean
            and self.product_cleanup.clean
        )
        expected_terminal = (
            TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0233
            if passed
            else TRAFFIC_PREFLIGHT_ATTEMPT_BLOCKED_V0233
        )
        if self.terminal != expected_terminal:
            raise ValueError("Product v0.2.3.3 traffic Attempt disposition differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"attempt_sha256"})
        )
        if self.attempt_sha256 != expected:
            raise ValueError("Product v0.2.3.3 traffic Attempt digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> TrafficPreflightAttemptV0233:
        execution = payload["execution"]
        if not isinstance(execution, HealthyTrafficExecutionV0232):
            execution = HealthyTrafficExecutionV0232.model_validate(execution)
        run = execution.run
        clean = DemoCleanupV0233.model_validate(payload["demo_cleanup"]).clean and (
            ProductCleanupV0233.model_validate(payload["product_cleanup"]).clean
        )
        passed = (
            run.role == "PREFLIGHT"
            and run.profile_sha256 == payload["engine_profile_sha256"]
            and run.contract_sha256 == payload["traffic_contract_sha256"]
            and run.passed
            and run.planned_transactions == 10
            and run.completed_transactions == 10
            and run.successful_transactions == 10
            and run.failed_transactions == 0
            and run.transport_retry_count == 0
            and not run.stage_failure_counts
            and payload["queue_before_sha256"] == payload["queue_after_sha256"]
            and payload["outer_baseline_before_sha256"]
            == payload["outer_baseline_after_sha256"]
            and payload["source_state_before_sha256"]
            == payload["source_state_after_sha256"]
            and clean
        )
        body = {
            "schema_version": "ecomsre.product.traffic-preflight-attempt.v0233",
            **payload,
            "terminal": (
                TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0233
                if passed
                else TRAFFIC_PREFLIGHT_ATTEMPT_BLOCKED_V0233
            ),
            "execution": execution,
            "demo_cleanup": DemoCleanupV0233.model_validate(
                payload["demo_cleanup"]
            ),
            "product_cleanup": ProductCleanupV0233.model_validate(
                payload["product_cleanup"]
            ),
        }
        normalized = cls.model_construct(
            **body, attempt_sha256="0" * 64
        ).model_dump(mode="json", exclude={"attempt_sha256"})
        return cls.model_validate(
            {**normalized, "attempt_sha256": semantic_sha256_v22(normalized)}
        )


class TrafficPreflightBlockedAttemptV0233(ProductModelV1):
    """Append-only fail-closed record for any non-passing preflight attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "ecomsre.product.traffic-preflight-blocked-attempt.v0233"
    ] = "ecomsre.product.traffic-preflight-blocked-attempt.v0233"
    terminal: Literal[
        "BLOCKED_ECOMSRE_PRODUCT_V0233_TRAFFIC_PREFLIGHT_ATTEMPT"
    ] = TRAFFIC_PREFLIGHT_ATTEMPT_BLOCKED_V0233
    attempt_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,79}$")
    attempt_ordinal: int = Field(ge=1)
    prior_attempt_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    changed_surface: str | None = Field(default=None, min_length=1, max_length=240)
    changed_surface_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    attempt_consumed: bool
    failure_stage: str = Field(min_length=1, max_length=80)
    safe_error_type: str = Field(min_length=1, max_length=120)
    campaign_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_selection_sha256: str = Field(pattern=_SHA256_PATTERN)
    profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_state_before_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    source_state_after_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    demo_cleanup: DemoCleanupV0233 | None = None
    product_cleanup: ProductCleanupV0233 | None = None
    formal_clone_count: Literal[0] = 0
    formal_execution_count: Literal[0] = 0
    new_incident_count: Literal[0] = 0
    new_diagnosis_count: Literal[0] = 0
    measured_result_count: Literal[0] = 0
    fault_attempt_count: Literal[0] = 0
    provider_calls: Literal[0] = 0
    agent_writes: Literal[0] = 0
    runbook_executions: Literal[0] = 0
    action_authority: Literal["NONE"] = "NONE"
    attempt_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_chain_and_seal(self) -> TrafficPreflightBlockedAttemptV0233:
        changed = (
            self.prior_attempt_sha256,
            self.changed_surface,
            self.changed_surface_sha256,
        )
        if self.attempt_ordinal == 1:
            if changed != (None, None, None):
                raise ValueError("first traffic attempt changed surface differs")
        elif any(value is None for value in changed):
            raise ValueError("later traffic attempt requires changed surface")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"attempt_sha256"})
        )
        if self.attempt_sha256 != expected:
            raise ValueError("Product v0.2.3.3 blocked Attempt digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> TrafficPreflightBlockedAttemptV0233:
        body = {
            "schema_version": (
                "ecomsre.product.traffic-preflight-blocked-attempt.v0233"
            ),
            **payload,
            "terminal": TRAFFIC_PREFLIGHT_ATTEMPT_BLOCKED_V0233,
            "formal_clone_count": 0,
            "formal_execution_count": 0,
            "new_incident_count": 0,
            "new_diagnosis_count": 0,
            "measured_result_count": 0,
            "fault_attempt_count": 0,
            "provider_calls": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
            "action_authority": "NONE",
        }
        if body.get("demo_cleanup") is not None:
            body["demo_cleanup"] = DemoCleanupV0233.model_validate(
                body["demo_cleanup"]
            )
        if body.get("product_cleanup") is not None:
            body["product_cleanup"] = ProductCleanupV0233.model_validate(
                body["product_cleanup"]
            )
        normalized = cls.model_construct(
            **body, attempt_sha256="0" * 64
        ).model_dump(mode="json", exclude={"attempt_sha256"})
        return cls.model_validate(
            {**normalized, "attempt_sha256": semantic_sha256_v22(normalized)}
        )


class TrafficPreflightLedgerV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.traffic-preflight-ledger.v0233"] = (
        "ecomsre.product.traffic-preflight-ledger.v0233"
    )
    attempts: tuple[
        TrafficPreflightAttemptV0233 | TrafficPreflightBlockedAttemptV0233, ...
    ]
    attempt_count: int = Field(ge=1)
    terminal_attempt_sha256: str = Field(pattern=_SHA256_PATTERN)
    ledger_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_append_only_chain(self) -> TrafficPreflightLedgerV0233:
        if (
            self.attempt_count != len(self.attempts)
            or tuple(item.attempt_ordinal for item in self.attempts)
            != tuple(range(1, len(self.attempts) + 1))
            or len({item.attempt_id for item in self.attempts}) != len(self.attempts)
            or self.terminal_attempt_sha256 != self.attempts[-1].attempt_sha256
            or any(
                current.prior_attempt_sha256 != previous.attempt_sha256
                for previous, current in zip(
                    self.attempts, self.attempts[1:], strict=False
                )
            )
        ):
            raise ValueError("Product v0.2.3.3 traffic ledger chain differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"ledger_sha256"})
        )
        if self.ledger_sha256 != expected:
            raise ValueError("Product v0.2.3.3 traffic ledger digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        attempts: tuple[
            TrafficPreflightAttemptV0233 | TrafficPreflightBlockedAttemptV0233,
            ...,
        ],
    ) -> TrafficPreflightLedgerV0233:
        body = {
            "schema_version": "ecomsre.product.traffic-preflight-ledger.v0233",
            "attempts": [item.model_dump(mode="json") for item in attempts],
            "attempt_count": len(attempts),
            "terminal_attempt_sha256": attempts[-1].attempt_sha256,
        }
        return cls.model_validate(
            {**body, "ledger_sha256": semantic_sha256_v22(body)}
        )


class TrafficPreflightPassV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.traffic-preflight.v0233"] = (
        "ecomsre.product.traffic-preflight.v0233"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V0233_TRAFFIC_PREFLIGHT_PASS"] = (
        TRAFFIC_PREFLIGHT_PASS_V0233
    )
    attempt_sha256: str = Field(pattern=_SHA256_PATTERN)
    ledger_sha256: str = Field(pattern=_SHA256_PATTERN)
    campaign_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_selection_sha256: str = Field(pattern=_SHA256_PATTERN)
    preflight_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    engine_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_execution_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_clone_count: Literal[0] = 0
    formal_execution_count: Literal[0] = 0
    new_incident_count: Literal[0] = 0
    new_diagnosis_count: Literal[0] = 0
    measured_result_count: Literal[0] = 0
    action_authority: Literal["NONE"] = "NONE"
    preflight_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_seal(self) -> TrafficPreflightPassV0233:
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"preflight_sha256"})
        )
        if self.preflight_sha256 != expected:
            raise ValueError("Product v0.2.3.3 traffic preflight digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        attempt: TrafficPreflightAttemptV0233,
        ledger_sha256: str,
        formal_profile_sha256: str,
    ) -> TrafficPreflightPassV0233:
        if attempt.terminal != TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0233:
            raise ValueError("traffic preflight PASS requires a passing Attempt")
        body = {
            "schema_version": "ecomsre.product.traffic-preflight.v0233",
            "terminal": TRAFFIC_PREFLIGHT_PASS_V0233,
            "attempt_sha256": attempt.attempt_sha256,
            "ledger_sha256": ledger_sha256,
            "campaign_sha256": attempt.campaign_sha256,
            "source_selection_sha256": attempt.source_selection_sha256,
            "preflight_profile_sha256": attempt.profile_sha256,
            "engine_profile_sha256": attempt.engine_profile_sha256,
            "formal_profile_sha256": formal_profile_sha256,
            "traffic_execution_sha256": attempt.execution.execution_sha256,
            "formal_clone_count": 0,
            "formal_execution_count": 0,
            "new_incident_count": 0,
            "new_diagnosis_count": 0,
            "measured_result_count": 0,
            "action_authority": "NONE",
        }
        return cls.model_validate(
            {**body, "preflight_sha256": semantic_sha256_v22(body)}
        )


class FormalContractFreezeV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-contract-freeze.v0233"] = (
        "ecomsre.product.formal-contract-freeze.v0233"
    )
    campaign_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_preflight_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_selection_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_clone_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    preflight_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    preflight_profile_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_profile_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_continuity_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    flagd_bind_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    resolved_compose_sha256: str = Field(pattern=_SHA256_PATTERN)
    read_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    pilot_runtime_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_baseline_sha256: str = Field(pattern=_SHA256_PATTERN)
    stage_journal_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    private_failure_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    diagnosis_semantic_source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    nofault_scorer_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    prepared_repository_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_clone_count: Literal[0] = 0
    formal_execution_count: Literal[0] = 0
    new_incident_count: Literal[0] = 0
    new_diagnosis_count: Literal[0] = 0
    measured_result_count: Literal[0] = 0
    action_authority: Literal["NONE"] = "NONE"
    freeze_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_complete_sorted_seal(self) -> FormalContractFreezeV0233:
        body = self.model_dump(mode="json", exclude={"freeze_sha256"})
        if self.freeze_sha256 != semantic_sha256_v22(body):
            raise ValueError("Product v0.2.3.3 formal freeze digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> FormalContractFreezeV0233:
        body = {
            "schema_version": "ecomsre.product.formal-contract-freeze.v0233",
            **payload,
            "formal_clone_count": 0,
            "formal_execution_count": 0,
            "new_incident_count": 0,
            "new_diagnosis_count": 0,
            "measured_result_count": 0,
            "action_authority": "NONE",
        }
        return cls.model_validate(
            {**body, "freeze_sha256": semantic_sha256_v22(body)}
        )


__all__ = (
    "TRAFFIC_PREFLIGHT_ATTEMPT_BLOCKED_V0233",
    "TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0233",
    "TRAFFIC_PREFLIGHT_PASS_V0233",
    "ALLOWED_REPAIR_SURFACES_V0233",
    "DiagnosisSemanticSourceManifestV0233",
    "DemoCleanupV0233",
    "FormalClonePlanV0233",
    "FormalContractFreezeV0233",
    "ProductCleanupV0233",
    "TrafficPreflightAttemptV0233",
    "TrafficPreflightBlockedAttemptV0233",
    "TrafficPreflightLedgerV0233",
    "TrafficPreflightPassV0233",
    "TrafficRepairSurfaceSnapshotV0233",
)
