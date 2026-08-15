"""No-fault read-only Smoke harness for an already-owned local Sandbox."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Literal, Protocol

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.contracts import DtaModel, RunId, Sha256, semantic_sha256
from ecomsre.dta_v2.read_tools import InvestigationReadTools, ReadBackend
from ecomsre.dta_v2.telemetry_adapters import (
    LocalSandboxReadBackend,
    _issue_owned_read_capability,
)
from ecomsre.dta_v2.tool_contracts import (
    MetricKind,
    ObservationStatus,
    ToolName,
    build_inspect_resource_usage_request,
    build_inspect_service_runtime_request,
    build_query_metrics_request,
    build_search_logs_request,
    build_trace_neighborhood_request,
)
from ecomsre_live_sandbox.contracts import write_private_json


class SmokeTerminal(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class SmokeToolResult(DtaModel):
    tool: ToolName
    investigation_run_id: RunId
    status: ObservationStatus
    dispatch_count: Literal[1]
    evidence_ref: str
    artifact_sha256: Sha256
    evidence_store_sha256: Sha256


class ReadOnlySmokeReport(DtaModel):
    schema_version: Literal["dta-v2.read-only-smoke-report.v1"]
    smoke_id: RunId
    terminal: SmokeTerminal
    no_fault: Literal[True]
    fault_injection_count: Literal[0]
    agent_call_count: Literal[0]
    provider_call_count: Literal[0]
    runbook_execution_count: Literal[0]
    forward_mutation_count: Literal[0]
    configuration_mutation_count: Literal[0]
    service_mutation_count: Literal[0]
    tool_results: tuple[SmokeToolResult, ...] = Field(min_length=5, max_length=5)
    total_dispatches: StrictInt = Field(ge=5, le=5)
    report_sha256: Sha256

    @model_validator(mode="after")
    def require_smoke_semantics(self) -> ReadOnlySmokeReport:
        expected_tools = tuple(ToolName)
        if tuple(item.tool for item in self.tool_results) != expected_tools:
            raise ValueError("read-only Smoke tool order or coverage differs")
        expected_terminal = (
            SmokeTerminal.PASS
            if all(
                item.status is ObservationStatus.SUCCESS
                for item in self.tool_results
            )
            else SmokeTerminal.FAIL
        )
        if self.terminal is not expected_terminal:
            raise ValueError("read-only Smoke terminal differs from tool results")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("read-only Smoke digest does not bind report")
        return self


def run_read_only_smoke(
    *,
    smoke_id: str,
    service: str,
    backend: ReadBackend,
    evidence_root: Path,
) -> ReadOnlySmokeReport:
    """Query all five adapters without exceeding any investigation's cap."""

    ended_at = datetime.now(timezone.utc)
    started_at = ended_at - timedelta(minutes=5)
    run_ids = {
        tool: hashlib.sha256(f"{smoke_id}:{tool.value}".encode("utf-8")).hexdigest()[:32]
        for tool in ToolName
    }
    requests = (
        build_query_metrics_request(
            run_id=run_ids[ToolName.QUERY_METRICS],
            service=service,
            started_at=started_at,
            ended_at=ended_at,
            metric_kinds=tuple(MetricKind),
            max_results=6,
        ),
        build_search_logs_request(
            run_id=run_ids[ToolName.SEARCH_LOGS],
            service=service,
            started_at=started_at,
            ended_at=ended_at,
            max_records=20,
        ),
        build_trace_neighborhood_request(
            run_id=run_ids[ToolName.QUERY_TRACE_NEIGHBORHOOD],
            service=service,
            started_at=started_at,
            ended_at=ended_at,
            max_spans=40,
        ),
        build_inspect_service_runtime_request(
            run_id=run_ids[ToolName.INSPECT_SERVICE_RUNTIME],
            services=(service,),
            max_results=1,
        ),
        build_inspect_resource_usage_request(
            run_id=run_ids[ToolName.INSPECT_RESOURCE_USAGE],
            services=(service,),
            sampling_window_seconds=2,
            sample_count=3,
        ),
    )
    tool_results: list[SmokeToolResult] = []
    root = Path(evidence_root)
    for request in requests:
        tools = InvestigationReadTools(run_id=request.run_id, backend=backend)
        observation = tools.dispatch(request)
        snapshot = tools.snapshot()
        snapshot.persist_create_once(root / f"{request.tool.value}.json")
        tool_results.append(
            SmokeToolResult(
                tool=request.tool,
                investigation_run_id=request.run_id,
                status=observation.status,
                dispatch_count=1,
                evidence_ref=observation.evidence_ref,
                artifact_sha256=observation.artifact_sha256,
                evidence_store_sha256=snapshot.evidence_store_sha256,
            )
        )
    terminal = (
        SmokeTerminal.PASS
        if all(item.status is ObservationStatus.SUCCESS for item in tool_results)
        else SmokeTerminal.FAIL
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.read-only-smoke-report.v1",
        "smoke_id": smoke_id,
        "terminal": terminal,
        "no_fault": True,
        "fault_injection_count": 0,
        "agent_call_count": 0,
        "provider_call_count": 0,
        "runbook_execution_count": 0,
        "forward_mutation_count": 0,
        "configuration_mutation_count": 0,
        "service_mutation_count": 0,
        "tool_results": tuple(tool_results),
        "total_dispatches": 5,
    }
    draft = ReadOnlySmokeReport.model_construct(**payload, report_sha256="0" * 64)
    report = ReadOnlySmokeReport.model_validate(
        {
            **payload,
            "report_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"report_sha256"})
            ),
        }
    )
    write_private_json(root / "read-only-smoke-report.json", report, create_once=True)
    return report


class SmokeFailureCode(str, Enum):
    ENVIRONMENT_ADMISSION_FAILED = "ENVIRONMENT_ADMISSION_FAILED"
    AUTHORITY_ADMISSION_FAILED = "AUTHORITY_ADMISSION_FAILED"
    START_FAILED = "START_FAILED"
    READINESS_FAILED = "READINESS_FAILED"
    BASELINE_READ_FAILED = "BASELINE_READ_FAILED"
    READ_TOOL_FAILED = "READ_TOOL_FAILED"
    EVIDENCE_PERSISTENCE_FAILED = "EVIDENCE_PERSISTENCE_FAILED"
    POST_READ_BASELINE_MISMATCH = "POST_READ_BASELINE_MISMATCH"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    CLEANUP_BLOCKED = "CLEANUP_BLOCKED"


class SmokeAttemptStage(str, Enum):
    CREATED = "CREATED"
    ENVIRONMENT_ADMITTED = "ENVIRONMENT_ADMITTED"
    START_REQUESTED = "START_REQUESTED"
    READY = "READY"
    AUTHORITY_ADMITTED = "AUTHORITY_ADMITTED"
    BASELINE_VERIFIED = "BASELINE_VERIFIED"
    READS_COMPLETED = "READS_COMPLETED"
    POST_READ_BASELINE_VERIFIED = "POST_READ_BASELINE_VERIFIED"
    CLEANUP_ATTEMPTED = "CLEANUP_ATTEMPTED"
    CLOSED = "CLOSED"


class SmokeAttemptEvent(DtaModel):
    ordinal: StrictInt = Field(ge=1, le=16)
    stage: SmokeAttemptStage
    status: Literal["PASS", "FAIL"]
    failure_code: SmokeFailureCode | None

    @model_validator(mode="after")
    def require_event(self) -> SmokeAttemptEvent:
        if (self.status == "FAIL") != (self.failure_code is not None):
            raise ValueError("Smoke journal event status differs from failure code")
        return self


class CleanupObservation(DtaModel):
    verdict: Literal["CLEAN", "BLOCKED"]
    owned_containers: StrictInt | None = Field(default=None, ge=0)
    owned_networks: StrictInt | None = Field(default=None, ge=0)
    owned_volumes: StrictInt | None = Field(default=None, ge=0)
    non_owned_resources_changed: bool | None

    @model_validator(mode="after")
    def require_cleanup(self) -> CleanupObservation:
        if self.verdict == "CLEAN" and (
            self.owned_containers != 0
            or self.owned_networks != 0
            or self.owned_volumes != 0
            or self.non_owned_resources_changed is not False
        ):
            raise ValueError("CLEAN cleanup must prove exact zero and no drift")
        return self

    @classmethod
    def clean(cls) -> CleanupObservation:
        return cls(
            verdict="CLEAN",
            owned_containers=0,
            owned_networks=0,
            owned_volumes=0,
            non_owned_resources_changed=False,
        )

    @classmethod
    def non_owned_drift(cls) -> CleanupObservation:
        return cls(
            verdict="BLOCKED",
            owned_containers=0,
            owned_networks=0,
            owned_volumes=0,
            non_owned_resources_changed=True,
        )

    @classmethod
    def unknown_blocked(cls) -> CleanupObservation:
        return cls(
            verdict="BLOCKED",
            owned_containers=None,
            owned_networks=None,
            owned_volumes=None,
            non_owned_resources_changed=None,
        )


class OwnedSmokeLifecycle(Protocol):
    def admit(self) -> None: ...

    def start(self) -> None: ...

    def wait_ready(self) -> None: ...

    def authorize_reads(self) -> ReadBackend: ...

    def read_baseline_sha256(self) -> str: ...

    def cleanup_owned(self, *, baseline_unchanged: bool) -> CleanupObservation: ...


class OwnedReadOnlySmokeClosure(DtaModel):
    schema_version: Literal["dta-v2.owned-read-only-smoke-closure.v1"]
    smoke_id: RunId
    terminal: SmokeTerminal
    failure_code: SmokeFailureCode | None
    primary_failure_code: SmokeFailureCode | None
    cleanup_failure_code: SmokeFailureCode | None
    authority_sha256: Sha256 | None
    read_only_report_sha256: Sha256 | None
    read_tool_terminal: SmokeTerminal | None
    baseline_unchanged: bool | None
    cleanup_attempted: bool
    cleanup_verdict: Literal["CLEAN", "BLOCKED", "NOT_ATTEMPTED"]
    owned_containers_after: StrictInt | None = Field(default=None, ge=0)
    owned_networks_after: StrictInt | None = Field(default=None, ge=0)
    owned_volumes_after: StrictInt | None = Field(default=None, ge=0)
    non_owned_resources_changed: bool | None
    journal: tuple[SmokeAttemptEvent, ...] = Field(min_length=2, max_length=16)
    fault_injection_count: Literal[0]
    agent_call_count: Literal[0]
    provider_call_count: Literal[0]
    runbook_execution_count: Literal[0]
    forward_mutation_count: Literal[0]
    configuration_mutation_count: Literal[0]
    service_mutation_count: Literal[0]
    closure_sha256: Sha256

    @model_validator(mode="after")
    def require_closure(self) -> OwnedReadOnlySmokeClosure:
        passed = (
            self.failure_code is None
            and self.authority_sha256 is not None
            and self.read_only_report_sha256 is not None
            and self.read_tool_terminal is SmokeTerminal.PASS
            and self.cleanup_attempted
            and self.cleanup_verdict == "CLEAN"
            and self.baseline_unchanged
            and not self.non_owned_resources_changed
            and self.owned_containers_after == 0
            and self.owned_networks_after == 0
            and self.owned_volumes_after == 0
        )
        if (self.terminal is SmokeTerminal.PASS) != passed:
            raise ValueError("owned read-only Smoke terminal differs from cleanup")
        if self.terminal is SmokeTerminal.FAIL and self.failure_code is None:
            raise ValueError("failed owned Smoke requires a typed failure code")
        expected_terminal_failure = (
            self.cleanup_failure_code or self.primary_failure_code
        )
        if self.failure_code is not expected_terminal_failure:
            raise ValueError("owned Smoke terminal failure loses stage-specific cause")
        stage_order = {stage: index for index, stage in enumerate(SmokeAttemptStage)}
        nonclosed_stages = tuple(item.stage for item in self.journal[:-1])
        if (
            tuple(item.ordinal for item in self.journal)
            != tuple(range(1, len(self.journal) + 1))
            or self.journal[0].stage is not SmokeAttemptStage.CREATED
            or self.journal[-1].stage is not SmokeAttemptStage.CLOSED
            or len(nonclosed_stages) != len(set(nonclosed_stages))
            or tuple(stage_order[item] for item in nonclosed_stages)
            != tuple(sorted(stage_order[item] for item in nonclosed_stages))
            or self.journal[-1].failure_code is not self.failure_code
        ):
            raise ValueError("owned Smoke journal is not canonical or closed")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"closure_sha256"})
        )
        if self.closure_sha256 != expected:
            raise ValueError("owned read-only Smoke digest does not bind closure")
        return self


def run_owned_read_only_smoke_attempt(
    *,
    private_root: Path,
    smoke_id: str,
    service: str,
    lifecycle: OwnedSmokeLifecycle,
    read_runner: Callable[..., ReadOnlySmokeReport] = run_read_only_smoke,
) -> OwnedReadOnlySmokeClosure:
    private = Path(private_root).resolve()
    if private == Path("/"):
        raise ValueError("read-only Smoke private root may not be filesystem root")
    from ecomsre_live_sandbox.contracts import ensure_private_directory

    ensure_private_directory(private)
    events: list[SmokeAttemptEvent] = []

    def record(
        stage: SmokeAttemptStage, failure: SmokeFailureCode | None = None
    ) -> None:
        events.append(
            SmokeAttemptEvent(
                ordinal=len(events) + 1,
                stage=stage,
                status="FAIL" if failure is not None else "PASS",
                failure_code=failure,
            )
        )

    record(SmokeAttemptStage.CREATED)
    failure: SmokeFailureCode | None = None
    primary_failure: SmokeFailureCode | None = None
    cleanup_failure: SmokeFailureCode | None = None
    authority_sha256 = None
    read_report = None
    baseline_before = None
    baseline_unchanged: bool | None = None
    cleanup_attempted = False
    cleanup: CleanupObservation | None = None
    start_requested = False
    backend = None
    try:
        try:
            lifecycle.admit()
            record(SmokeAttemptStage.ENVIRONMENT_ADMITTED)
        except (OSError, PermissionError, FileExistsError):
            failure = SmokeFailureCode.EVIDENCE_PERSISTENCE_FAILED
            record(SmokeAttemptStage.ENVIRONMENT_ADMITTED, failure)
        except Exception:
            failure = SmokeFailureCode.ENVIRONMENT_ADMISSION_FAILED
            record(SmokeAttemptStage.ENVIRONMENT_ADMITTED, failure)
        if failure is None:
            start_requested = True
            try:
                lifecycle.start()
                record(SmokeAttemptStage.START_REQUESTED)
            except Exception:
                failure = SmokeFailureCode.START_FAILED
                record(SmokeAttemptStage.START_REQUESTED, failure)
        if failure is None:
            try:
                lifecycle.wait_ready()
                record(SmokeAttemptStage.READY)
            except Exception:
                failure = SmokeFailureCode.READINESS_FAILED
                record(SmokeAttemptStage.READY, failure)
        if failure is None:
            try:
                backend = lifecycle.authorize_reads()
                authority_sha256 = backend.authority.authority_sha256
                record(SmokeAttemptStage.AUTHORITY_ADMITTED)
            except Exception:
                failure = SmokeFailureCode.AUTHORITY_ADMISSION_FAILED
                record(SmokeAttemptStage.AUTHORITY_ADMITTED, failure)
        if failure is None:
            try:
                baseline_before = lifecycle.read_baseline_sha256()
                record(SmokeAttemptStage.BASELINE_VERIFIED)
            except Exception:
                failure = SmokeFailureCode.BASELINE_READ_FAILED
                record(SmokeAttemptStage.BASELINE_VERIFIED, failure)
        if failure is None:
            assert backend is not None
            try:
                read_report = read_runner(
                    smoke_id=smoke_id,
                    service=service,
                    backend=backend,
                    evidence_root=private / "evidence",
                )
                if read_report.terminal is not SmokeTerminal.PASS:
                    failure = SmokeFailureCode.READ_TOOL_FAILED
                    record(SmokeAttemptStage.READS_COMPLETED, failure)
                else:
                    record(SmokeAttemptStage.READS_COMPLETED)
            except (OSError, PermissionError, FileExistsError):
                failure = SmokeFailureCode.EVIDENCE_PERSISTENCE_FAILED
                record(SmokeAttemptStage.READS_COMPLETED, failure)
            except Exception:
                failure = SmokeFailureCode.READ_TOOL_FAILED
                record(SmokeAttemptStage.READS_COMPLETED, failure)
        if failure is None:
            try:
                baseline_after = lifecycle.read_baseline_sha256()
                baseline_unchanged = baseline_before == baseline_after
                if not baseline_unchanged:
                    failure = SmokeFailureCode.POST_READ_BASELINE_MISMATCH
                    record(SmokeAttemptStage.POST_READ_BASELINE_VERIFIED, failure)
                else:
                    record(SmokeAttemptStage.POST_READ_BASELINE_VERIFIED)
            except Exception:
                failure = SmokeFailureCode.BASELINE_READ_FAILED
                record(SmokeAttemptStage.POST_READ_BASELINE_VERIFIED, failure)
    finally:
        primary_failure = failure
        if start_requested:
            cleanup_attempted = True
            try:
                cleanup = CleanupObservation.model_validate(
                    lifecycle.cleanup_owned(
                        baseline_unchanged=baseline_unchanged is True
                    ).model_dump()
                )
                if cleanup.verdict != "CLEAN":
                    cleanup_failure = SmokeFailureCode.CLEANUP_BLOCKED
                    failure = cleanup_failure
                    record(SmokeAttemptStage.CLEANUP_ATTEMPTED, failure)
                else:
                    record(SmokeAttemptStage.CLEANUP_ATTEMPTED)
            except Exception:
                cleanup = CleanupObservation.unknown_blocked()
                cleanup_failure = SmokeFailureCode.CLEANUP_FAILED
                failure = cleanup_failure
                record(SmokeAttemptStage.CLEANUP_ATTEMPTED, failure)
    if cleanup is None:
        cleanup = CleanupObservation.unknown_blocked()
    record(SmokeAttemptStage.CLOSED, failure)
    terminal = SmokeTerminal.PASS if failure is None else SmokeTerminal.FAIL
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.owned-read-only-smoke-closure.v1",
        "smoke_id": smoke_id,
        "terminal": terminal,
        "failure_code": failure,
        "primary_failure_code": primary_failure,
        "cleanup_failure_code": cleanup_failure,
        "authority_sha256": authority_sha256,
        "read_only_report_sha256": (
            None if read_report is None else read_report.report_sha256
        ),
        "read_tool_terminal": None if read_report is None else read_report.terminal,
        "baseline_unchanged": baseline_unchanged,
        "cleanup_attempted": cleanup_attempted,
        "cleanup_verdict": cleanup.verdict if cleanup_attempted else "NOT_ATTEMPTED",
        "owned_containers_after": cleanup.owned_containers,
        "owned_networks_after": cleanup.owned_networks,
        "owned_volumes_after": cleanup.owned_volumes,
        "non_owned_resources_changed": cleanup.non_owned_resources_changed,
        "journal": tuple(events),
        "fault_injection_count": 0,
        "agent_call_count": 0,
        "provider_call_count": 0,
        "runbook_execution_count": 0,
        "forward_mutation_count": 0,
        "configuration_mutation_count": 0,
        "service_mutation_count": 0,
    }
    draft = OwnedReadOnlySmokeClosure.model_construct(
        **payload, closure_sha256="0" * 64
    )
    closure = OwnedReadOnlySmokeClosure.model_validate(
        {
            **payload,
            "closure_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"closure_sha256"})
            ),
        }
    )
    write_private_json(
        private / "owned-read-only-smoke-closure.json", closure, create_once=True
    )
    return closure


class _SandboxOwnedSmokeLifecycle:
    def __init__(
        self, *, repository_root: Path, private_root: Path, stabilization_seconds: int
    ) -> None:
        self.repository_root = repository_root
        self.private_root = private_root
        self.stabilization_seconds = stabilization_seconds
        self.environment: Any = None
        self.controller: Any = None
        self.bundle: Any = None
        self.admitted_resolved_sha256: str | None = None
        self.flag_file: Path | None = None
        self.baseline_document: Mapping[str, object] | None = None
        self.fault_document: Mapping[str, object] | None = None
        self.baseline_read_count = 0

    def admit(self) -> None:
        from ecomsre_live_sandbox.contracts import (
            ensure_private_directory,
            load_bundle,
        )
        from ecomsre_live_sandbox.control import (
            build_flag_documents,
        )
        from ecomsre_live_sandbox.environment import SandboxEnvironment

        root = self.repository_root
        runtime_root = self.private_root / "runtime"
        control_root = self.private_root / "control"
        for directory in (runtime_root, control_root):
            ensure_private_directory(directory)
        self.bundle = load_bundle(
            root / "config/live-telemetry-controlled-remediation-v1"
        )
        upstream_flag = json.loads(
            (
                root / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
            ).read_text(encoding="utf-8")
        )
        if not isinstance(upstream_flag, Mapping):
            raise ValueError("upstream baseline flag document is invalid")
        baseline, fault = build_flag_documents(upstream_flag, self.bundle)
        flag_directory = runtime_root / "flagd"
        ensure_private_directory(flag_directory)
        flag_file = flag_directory / "demo.flagd.json"
        write_private_json(flag_file, baseline, create_once=True)
        self.flag_file = flag_file
        self.baseline_document = baseline
        self.fault_document = fault
        self.environment = SandboxEnvironment(
            repository_root=root,
            bundle=self.bundle,
            flagd_directory=flag_directory,
        )
        docker = self.environment.verify_local_docker()
        self.environment.verify_upstream()
        resolved, raw_compose = self.environment.resolve()
        self.admitted_resolved_sha256 = semantic_sha256(
            resolved.model_dump(mode="json")
        )
        write_private_json(
            control_root / "resolved-compose.json", raw_compose, create_once=True
        )
        self.environment.verify_cached_images(resolved, control_root)
        del docker

    def start(self) -> None:
        self.environment.start()

    def wait_ready(self) -> None:
        self.environment.wait_healthy()
        if self.stabilization_seconds:
            time.sleep(self.stabilization_seconds)

    def authorize_reads(self) -> ReadBackend:
        from ecomsre_live_sandbox.control import SandboxFaultController

        if (
            self.admitted_resolved_sha256 is None
            or self.flag_file is None
            or self.baseline_document is None
            or self.fault_document is None
        ):
            raise RuntimeError("owned read lifecycle lacks admitted resolve state")
        capability = _issue_owned_read_capability(
            environment=self.environment,
            bundle=self.bundle,
            admitted_resolved_sha256=self.admitted_resolved_sha256,
            timeout_seconds=5.0,
        )
        self.controller = SandboxFaultController(
            endpoints=capability.resolved_sandbox.endpoints,
            bundle=self.bundle,
            flag_file=self.flag_file,
            baseline_document=self.baseline_document,
            fault_document=self.fault_document,
        )
        return LocalSandboxReadBackend._from_owned_capability(capability)

    def read_baseline_sha256(self) -> str:
        observed = self.controller.read_current()
        expected = self.bundle.scenario.baseline_document_sha256
        self.baseline_read_count += 1
        if self.baseline_read_count == 1 and observed.document_sha256 != expected:
            raise RuntimeError("read-only Smoke baseline differs")
        return str(observed.document_sha256)

    def cleanup_owned(self, *, baseline_unchanged: bool) -> CleanupObservation:
        if self.environment is None or self.environment._baseline_snapshot is None:
            return CleanupObservation.unknown_blocked()
        cleanup = self.environment.cleanup(baseline_restored=baseline_unchanged)
        return CleanupObservation(
            verdict=cleanup.verdict,
            owned_containers=cleanup.owned_containers,
            owned_networks=cleanup.owned_networks,
            owned_volumes=cleanup.owned_volumes,
            non_owned_resources_changed=cleanup.non_owned_resources_changed,
        )


def run_owned_read_only_smoke(
    *,
    repository_root: Path,
    private_root: Path,
    smoke_id: str,
    service: str,
    stabilization_seconds: int = 30,
) -> OwnedReadOnlySmokeClosure:
    root = Path(repository_root).resolve()
    private = Path(private_root).resolve()
    if root == Path("/") or private == Path("/"):
        raise ValueError("read-only Smoke roots may not be filesystem root")
    if not 0 <= stabilization_seconds <= 120:
        raise ValueError("stabilization seconds must be between 0 and 120")
    return run_owned_read_only_smoke_attempt(
        private_root=private,
        smoke_id=smoke_id,
        service=service,
        lifecycle=_SandboxOwnedSmokeLifecycle(
            repository_root=root,
            private_root=private,
            stabilization_seconds=stabilization_seconds,
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run all five DTA v2 read adapters against an owned no-fault Sandbox"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    owned = commands.add_parser(
        "owned-lifecycle",
        help="admit, start, query, and clean the exact project-owned Sandbox",
    )
    owned.add_argument("--repository-root", required=True, type=Path)
    owned.add_argument("--private-root", required=True, type=Path)
    owned.add_argument("--smoke-id", required=True)
    owned.add_argument("--service", default="payment")
    owned.add_argument("--stabilization-seconds", type=int, default=30)
    arguments = parser.parse_args(argv)
    closure = run_owned_read_only_smoke(
        repository_root=arguments.repository_root,
        private_root=arguments.private_root,
        smoke_id=arguments.smoke_id,
        service=arguments.service,
        stabilization_seconds=arguments.stabilization_seconds,
    )
    print(closure.model_dump_json())
    return 0 if closure.terminal is SmokeTerminal.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
