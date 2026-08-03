"""Evaluator-only frozen-suite runner and metric aggregation."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ecomsre.phase1.contracts import (
    AgentRunReport,
    EvidenceSource,
    FaultMechanism,
    RCADecision,
)
from ecomsre.phase1.validator import revalidate_phase1_model

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GROUND_TRUTH_ROOT = PROJECT_ROOT / "eval/phase1/ground-truth"
EVALUATION_CASE_IDS = (
    "ad-partial-failure-complete",
    "ad-partial-failure-without-logs",
    "ad-partial-failure-frontend-decoy",
    "ad-change-with-normal-sli",
    "telemetry-insufficient",
    "no-real-incident",
    "recommendation-cache-failure",
)
_GROUND_TRUTH_KEYS = {
    "schema_version",
    "case_id",
    "expected_decision",
    "expected_root_service",
    "expected_fault_mechanism",
    "decoys",
}
_DECOY_KEYS = {"source", "service", "observation_type"}
_MAX_GROUND_TRUTH_BYTES = 64 * 1024
_MAX_AGENT_REPORT_BYTES = 4 * 1024 * 1024
_AGENT_SUBPROCESS_TIMEOUT_SECONDS = 60.0
_SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
_EXPECTED_RATE_DENOMINATORS = {
    "Decision Accuracy": 7,
    "Schema Valid Rate": 7,
    "Root Service Accuracy": 4,
    "Fault Mechanism Accuracy": 4,
    "Evidence Reference Validity": 7,
    "Abstention Accuracy": 3,
    "Decoy Resistance": 1,
}


@dataclass(frozen=True, slots=True)
class DecoySelector:
    source: EvidenceSource
    service: str
    observation_type: str


@dataclass(frozen=True, slots=True)
class GroundTruth:
    case_id: str
    expected_decision: RCADecision
    expected_root_service: str | None
    expected_fault_mechanism: FaultMechanism | None
    decoys: tuple[DecoySelector, ...]


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _semantic_sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _stat_signature(details: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def _open_ground_truth_root(root: Path) -> tuple[int, tuple[int, int, int, int, int]]:
    if not root.is_absolute() or any(part in {"", ".", ".."} for part in root.parts[1:]):
        raise ValueError("ground-truth root must be an absolute capability path")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if not isinstance(nofollow, int) or not isinstance(directory, int):
        raise ValueError("platform lacks safe ground-truth directory capability")
    flags = os.O_RDONLY | nofollow | directory
    close_on_exec = getattr(os, "O_CLOEXEC", None)
    if isinstance(close_on_exec, int):
        flags |= close_on_exec
    directory_fd = os.open(root.anchor, flags)
    try:
        for component in root.parts[1:]:
            try:
                next_fd = os.open(
                    component,
                    flags,
                    dir_fd=directory_fd,
                )
            except OSError as error:
                raise ValueError(
                    "ground-truth directory capability is unsafe"
                ) from error
            try:
                details = os.fstat(next_fd)
            except Exception:
                os.close(next_fd)
                raise
            if not stat.S_ISDIR(details.st_mode):
                os.close(next_fd)
                raise ValueError("ground-truth parent is not a directory")
            os.close(directory_fd)
            directory_fd = next_fd
        root_details = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(root_details.st_mode)
            or root_details.st_uid != os.geteuid()
        ):
            raise ValueError("ground-truth root must be an owned directory")
        signature = _stat_signature(root_details)
        return directory_fd, signature
    except Exception:
        os.close(directory_fd)
        raise


def _read_ground_truth_snapshot(
    path: Path,
    *,
    allowed_root: Path | None = None,
) -> bytes:
    root = path.parent if allowed_root is None else Path(allowed_root)
    if path.parent != root or path.name in {"", ".", ".."}:
        raise ValueError("ground truth must be a direct child of its root")
    root_fd, root_signature = _open_ground_truth_root(root)
    try:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if not isinstance(nofollow, int):
            raise ValueError("platform lacks no-follow ground-truth capability")
        flags = os.O_RDONLY | nofollow
        close_on_exec = getattr(os, "O_CLOEXEC", None)
        if isinstance(close_on_exec, int):
            flags |= close_on_exec
        try:
            file_descriptor = os.open(path.name, flags, dir_fd=root_fd)
        except OSError as error:
            raise ValueError(
                "ground truth cannot be opened as a regular file"
            ) from error
        try:
            before = os.fstat(file_descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
            ):
                raise ValueError("ground truth must be an owned regular file")
            if before.st_size > _MAX_GROUND_TRUTH_BYTES:
                raise ValueError("ground truth exceeds the evaluator limit")
            chunks: list[bytes] = []
            remaining = _MAX_GROUND_TRUTH_BYTES + 1
            while remaining > 0:
                chunk = os.read(file_descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            if len(content) > _MAX_GROUND_TRUTH_BYTES:
                raise ValueError("ground truth exceeds the evaluator limit")
            after = os.fstat(file_descriptor)
            if _stat_signature(before) != _stat_signature(after):
                raise ValueError("ground truth changed while it was read")
            if len(content) != after.st_size:
                raise ValueError("ground truth size changed while it was read")
        finally:
            os.close(file_descriptor)
        try:
            path_after = os.stat(
                path.name,
                dir_fd=root_fd,
                follow_symlinks=False,
            )
        except OSError as error:
            raise ValueError(
                "ground truth was replaced while it was read"
            ) from error
        root_after = os.fstat(root_fd)
        if (
            not stat.S_ISREG(path_after.st_mode)
            or _stat_signature(path_after) != _stat_signature(after)
            or _stat_signature(root_after) != root_signature
        ):
            raise ValueError("ground truth was replaced while it was read")
        return content
    finally:
        os.close(root_fd)


def _load_ground_truth(
    path: Path,
    case_id: str,
    *,
    allowed_root: Path | None = None,
) -> GroundTruth:
    payload = json.loads(
        _read_ground_truth_snapshot(
            path,
            allowed_root=allowed_root,
        ).decode("utf-8", errors="strict"),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(payload, dict) or set(payload) != _GROUND_TRUTH_KEYS:
        raise ValueError("ground truth fields are not exact")
    if (
        payload["schema_version"] != "phase1.ground-truth.v1"
        or payload["case_id"] != case_id
    ):
        raise ValueError("ground truth identity is invalid")
    expected_decision = RCADecision(payload["expected_decision"])
    expected_root = payload["expected_root_service"]
    if expected_root is not None and (
        not isinstance(expected_root, str) or not expected_root
    ):
        raise ValueError("expected root service is invalid")
    mechanism_value = payload["expected_fault_mechanism"]
    expected_mechanism = (
        FaultMechanism(mechanism_value)
        if mechanism_value is not None
        else None
    )
    if expected_decision is RCADecision.RCA_CONFIRMED:
        if expected_root is None or expected_mechanism is None:
            raise ValueError("confirmed ground truth requires root and mechanism")
    elif expected_root is not None or expected_mechanism is not None:
        raise ValueError("non-confirming ground truth cannot claim a root")
    raw_decoys = payload["decoys"]
    if not isinstance(raw_decoys, list):
        raise ValueError("ground truth decoys must be a list")
    decoys: list[DecoySelector] = []
    for raw_decoy in raw_decoys:
        if not isinstance(raw_decoy, dict) or set(raw_decoy) != _DECOY_KEYS:
            raise ValueError("decoy selector fields are not exact")
        service = raw_decoy["service"]
        observation_type = raw_decoy["observation_type"]
        if (
            not isinstance(service, str)
            or not service
            or not isinstance(observation_type, str)
            or not observation_type
        ):
            raise ValueError("decoy selector values are invalid")
        decoys.append(
            DecoySelector(
                source=EvidenceSource(raw_decoy["source"]),
                service=service,
                observation_type=observation_type,
            )
        )
    return GroundTruth(
        case_id=case_id,
        expected_decision=expected_decision,
        expected_root_service=expected_root,
        expected_fault_mechanism=expected_mechanism,
        decoys=tuple(decoys),
    )


def _agent_worker_request(
    project_root: Path,
    request: dict[str, object],
) -> object:
    worker_command = _agent_worker_command(project_root)
    environment = dict(os.environ)
    for name in (
        "PYTHONPATH",
        "PYTHONHOME",
        "ECOMSRE_LLM_BASE_URL",
        "ECOMSRE_LLM_API_KEY",
        "ECOMSRE_LLM_MODEL",
    ):
        environment.pop(name, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        worker_command,
        input=(_canonical_json(request) + "\n").encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=project_root,
        env=environment,
        timeout=_AGENT_SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("isolated replay Agent subprocess failed")
    if len(completed.stdout) > _MAX_AGENT_REPORT_BYTES:
        raise ValueError("isolated replay Agent response exceeds size limit")
    try:
        return json.loads(
            completed.stdout.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("isolated replay Agent response is invalid") from error


def _sbpl_literal(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _sandbox_profile(project_root: Path) -> str:
    evaluator_root = (project_root / "eval/phase1").resolve(strict=True)
    literal = _sbpl_literal(str(evaluator_root))
    return "\n".join(
        (
            "(version 1)",
            "(allow default)",
            f"(deny file-read* (subpath {literal}))",
            f"(deny file-write* (subpath {literal}))",
            "(deny network*)",
        )
    )


def _verified_sandbox_exec() -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("no proven isolated replay sandbox backend")
    details = os.stat(_SANDBOX_EXEC, follow_symlinks=False)
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_mode & 0o111 == 0
    ):
        raise RuntimeError("sandbox backend is not a root-owned executable")
    return _SANDBOX_EXEC


def _agent_worker_command(project_root: Path) -> list[str]:
    sandbox = _verified_sandbox_exec()
    worker = project_root / "src/ecomsre/phase1/replay_worker.py"
    return [
        str(sandbox),
        "-p",
        _sandbox_profile(project_root),
        sys.executable,
        "-I",
        str(worker),
    ]


def _run_os_sandbox_probe(project_root: Path) -> object:
    sandbox = _verified_sandbox_exec()
    profile = _sandbox_profile(project_root)
    target = (
        project_root
        / "eval/phase1/ground-truth/ad-partial-failure-complete.json"
    )
    ground_truth_root = target.parent
    probe_source = """
import ctypes
import json
import os
import subprocess
import sys

target = os.fsencode(sys.argv[1])
root = sys.argv[2]
libc = ctypes.CDLL(None, use_errno=True)
descriptor = libc.open(target, os.O_RDONLY)
if descriptor >= 0:
    os.close(descriptor)
ctypes_status = "ALLOWED" if descriptor >= 0 else "DENIED"
cat = subprocess.run(
    ["/bin/cat", os.fsdecode(target)],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    check=False,
)
try:
    os.listdir(root)
except PermissionError:
    list_status = "DENIED"
else:
    list_status = "ALLOWED"
print(json.dumps({
    "ctypes_open": ctypes_status,
    "subprocess_cat": "DENIED" if cat.returncode != 0 else "ALLOWED",
    "os_listdir": list_status,
}, sort_keys=True, separators=(",", ":")))
"""
    completed = subprocess.run(
        [
            str(sandbox),
            "-p",
            profile,
            sys.executable,
            "-I",
            "-c",
            probe_source,
            str(target),
            str(ground_truth_root),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=_AGENT_SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("sandbox backend capability probe failed")
    return json.loads(
        completed.stdout.decode("utf-8", errors="strict"),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_json_constant,
    )


def _run_scripted_report(project_root: Path, case_id: str) -> AgentRunReport:
    raw_report = _agent_worker_request(
        Path(project_root),
        {
            "mode": "run",
            "project_root": str(Path(project_root).resolve(strict=True)),
            "case_id": case_id,
        },
    )
    report = AgentRunReport.model_validate(raw_report)
    return revalidate_phase1_model(report, AgentRunReport)


def _run_clean_agent_probe(project_root: Path) -> object:
    return _agent_worker_request(
        Path(project_root),
        {
            "mode": "probe",
            "project_root": str(Path(project_root).resolve(strict=True)),
        },
    )


def _rate(numerator: int, denominator: int) -> dict[str, int | float]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else 0.0,
    }


def _case_int(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an integer")
    return value


def _case_float(value: object, field_name: str) -> float:
    if type(value) is not float:
        raise TypeError(f"{field_name} must be a float")
    return value


def _independent_reference_validity(report: AgentRunReport) -> bool:
    if report.final_rca is None:
        return report.evidence_references_valid
    known = {item.evidence_ref for item in report.evidence_index}
    cited = {
        *report.final_rca.supporting_evidence,
        *report.final_rca.contradicting_evidence,
    }
    tool_refs = {
        reference
        for record in report.tool_call_records
        for reference in record.evidence_refs
    }
    return (
        report.evidence_references_valid
        and cited <= known
        and tool_refs <= known
    )


def _semantic_projection_from_payload(
    case_id: str,
    report_payload: dict[str, object],
) -> dict[str, object]:
    projected = deepcopy(report_payload)
    for field_name in (
        "started_at",
        "ended_at",
        "monotonic_duration_seconds",
    ):
        projected.pop(field_name, None)
    model_records = projected.get("model_call_records")
    if not isinstance(model_records, list):
        raise TypeError("model_call_records must be a list")
    for record in model_records:
        if not isinstance(record, dict):
            raise TypeError("model call record must be an object")
        for field_name in (
            "started_at",
            "ended_at",
            "monotonic_duration_seconds",
        ):
            record.pop(field_name, None)
        response = record.get("response")
        if response is not None:
            if not isinstance(response, dict):
                raise TypeError("model response must be an object")
            for field_name in (
                "started_at",
                "ended_at",
                "monotonic_duration_seconds",
            ):
                response.pop(field_name, None)
    tool_records = projected.get("tool_call_records")
    if not isinstance(tool_records, list):
        raise TypeError("tool_call_records must be a list")
    for record in tool_records:
        if not isinstance(record, dict):
            raise TypeError("tool call record must be an object")
        for field_name in (
            "started_at",
            "ended_at",
            "monotonic_duration_seconds",
        ):
            record.pop(field_name, None)
    return {
        "case_id": case_id,
        "agent_run_report": projected,
    }


def _case_semantic_projection(
    case_id: str,
    report: AgentRunReport,
) -> dict[str, object]:
    return _semantic_projection_from_payload(
        case_id,
        report.model_dump(mode="json"),
    )


def _evaluate_case(
    case_id: str,
    report: AgentRunReport,
    truth: GroundTruth,
) -> dict[str, object]:
    final = report.final_rca
    decision = final.decision if final is not None else None
    root = final.root_service if final is not None else None
    mechanism = final.fault_mechanism if final is not None else None
    decision_correct = decision is truth.expected_decision
    confirmed_ground_truth = (
        truth.expected_decision is RCADecision.RCA_CONFIRMED
    )
    non_confirming_ground_truth = not confirmed_ground_truth
    root_correct = (
        decision_correct and root == truth.expected_root_service
        if confirmed_ground_truth
        else None
    )
    mechanism_correct = (
        decision_correct and mechanism is truth.expected_fault_mechanism
        if confirmed_ground_truth
        else None
    )
    abstention_correct = (
        decision_correct if non_confirming_ground_truth else None
    )
    decoy_resistant: bool | None = None
    if truth.decoys:
        decoy_refs = {
            evidence.evidence_ref
            for evidence in report.evidence_index
            if any(
                evidence.source is decoy.source
                and evidence.service == decoy.service
                and evidence.observation_type == decoy.observation_type
                for decoy in truth.decoys
            )
        }
        decoy_resistant = (
            decision_correct
            and root == truth.expected_root_service
            and final is not None
            and bool(decoy_refs)
            and decoy_refs.isdisjoint(final.supporting_evidence)
        )
    projection = _case_semantic_projection(case_id, report)
    return {
        "case_id": case_id,
        "run_id": report.run_id,
        "terminal_status": report.terminal_status.value,
        "decision": decision.value if decision is not None else None,
        "root_service": root,
        "fault_mechanism": mechanism.value if mechanism is not None else None,
        "schema_valid": report.schema_valid,
        "evidence_references_valid": _independent_reference_validity(report),
        "decision_correct": decision_correct,
        "root_service_correct": root_correct,
        "fault_mechanism_correct": mechanism_correct,
        "abstention_correct": abstention_correct,
        "decoy_resistant": decoy_resistant,
        "supporting_evidence": (
            list(final.supporting_evidence) if final is not None else []
        ),
        "contradicting_evidence": (
            list(final.contradicting_evidence) if final is not None else []
        ),
        "evidence_count": len(report.evidence_index),
        "tool_calls": report.budget_snapshot.tool_calls,
        "token_usage": report.budget_snapshot.total_tokens,
        "wall_clock_latency_seconds": report.monotonic_duration_seconds,
        "tool_sequence": [
            record.action.action_type for record in report.tool_call_records
        ],
        "semantic_sha256": _semantic_sha256(projection),
        "semantic_projection": projection,
    }


def run_evaluation(project_root: Path = PROJECT_ROOT) -> dict[str, object]:
    """Run seven scripted cases, then load each evaluator answer in order."""

    root = Path(project_root)
    case_results: list[dict[str, object]] = []
    for case_id in EVALUATION_CASE_IDS:
        report = _run_scripted_report(root, case_id)
        # This ordering is a security boundary: evaluator-only data is opened
        # only after the Agent has returned a validated AgentRunReport.
        truth = _load_ground_truth(
            root / "eval/phase1/ground-truth" / f"{case_id}.json",
            case_id,
            allowed_root=root / "eval/phase1/ground-truth",
        )
        case_results.append(_evaluate_case(case_id, report, truth))

    decision_numerator = sum(
        bool(item["decision_correct"]) for item in case_results
    )
    schema_numerator = sum(
        bool(item["schema_valid"]) for item in case_results
    )
    evidence_numerator = sum(
        bool(item["evidence_references_valid"]) for item in case_results
    )
    root_results = [
        item["root_service_correct"]
        for item in case_results
        if item["root_service_correct"] is not None
    ]
    mechanism_results = [
        item["fault_mechanism_correct"]
        for item in case_results
        if item["fault_mechanism_correct"] is not None
    ]
    abstention_results = [
        item["abstention_correct"]
        for item in case_results
        if item["abstention_correct"] is not None
    ]
    decoy_results = [
        item["decoy_resistant"]
        for item in case_results
        if item["decoy_resistant"] is not None
    ]
    tool_per_case = {
        str(item["case_id"]): _case_int(item["tool_calls"], "tool_calls")
        for item in case_results
    }
    token_per_case = {
        str(item["case_id"]): _case_int(item["token_usage"], "token_usage")
        for item in case_results
    }
    latency_per_case = {
        str(item["case_id"]): _case_float(
            item["wall_clock_latency_seconds"],
            "wall_clock_latency_seconds",
        )
        for item in case_results
    }
    case_count = len(case_results)
    tool_total = sum(tool_per_case.values())
    token_total = sum(token_per_case.values())
    latency_total = sum(latency_per_case.values())
    metrics: dict[str, object] = {
        "Decision Accuracy": _rate(decision_numerator, case_count),
        "Schema Valid Rate": _rate(schema_numerator, case_count),
        "Root Service Accuracy": _rate(
            sum(bool(value) for value in root_results),
            len(root_results),
        ),
        "Fault Mechanism Accuracy": _rate(
            sum(bool(value) for value in mechanism_results),
            len(mechanism_results),
        ),
        "Evidence Reference Validity": _rate(
            evidence_numerator,
            case_count,
        ),
        "Abstention Accuracy": _rate(
            sum(bool(value) for value in abstention_results),
            len(abstention_results),
        ),
        "Decoy Resistance": _rate(
            sum(bool(value) for value in decoy_results),
            len(decoy_results),
        ),
        "Average Tool Calls": {
            "per_case": tool_per_case,
            "total": tool_total,
            "average": tool_total / case_count,
        },
        "Token Usage": {
            "per_case": token_per_case,
            "total": token_total,
            "average": token_total / case_count,
        },
        "Wall-clock Latency": {
            "per_case": latency_per_case,
            "total_seconds": latency_total,
            "average_seconds": latency_total / case_count,
        },
    }
    rate_names = (
        "Decision Accuracy",
        "Schema Valid Rate",
        "Root Service Accuracy",
        "Fault Mechanism Accuracy",
        "Evidence Reference Validity",
        "Abstention Accuracy",
        "Decoy Resistance",
    )
    passed = all(
        metrics[name]["numerator"] == metrics[name]["denominator"]  # type: ignore[index]
        and metrics[name]["denominator"] == _EXPECTED_RATE_DENOMINATORS[name]  # type: ignore[index]
        for name in rate_names
    ) and all(item["terminal_status"] == "COMPLETED" for item in case_results)
    deterministic_projection = {
        "schema_version": "phase1.evaluation-semantic-projection.v1",
        "adapter": "scripted-replay-v1",
        "case_results": [
            item["semantic_projection"] for item in case_results
        ],
        "rate_metrics": {name: metrics[name] for name in rate_names},
        "Average Tool Calls": metrics["Average Tool Calls"],
        "Token Usage": metrics["Token Usage"],
    }
    return {
        "schema_version": "phase1.evaluation-report.v1",
        "status": "PASSED" if passed else "FAILED",
        "adapter": "scripted-replay-v1",
        "case_count": case_count,
        "case_results": case_results,
        "metrics": metrics,
        "deterministic_semantic_projection": deterministic_projection,
        "deterministic_semantic_sha256": _semantic_sha256(
            deterministic_projection
        ),
        "timing_semantics": {
            "clock": "monotonic wall-clock",
            "deterministic": False,
            "excluded_from_semantic_fingerprint": True,
            "scope": "local scripted replay only",
        },
    }
