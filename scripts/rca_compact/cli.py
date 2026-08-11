"""Bounded commands for the one-shot compact retrieval development experiment."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Literal, cast

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_rca100.lifecycle import RCA100Schedule, tree_sha256
from ecomsre_rcaeval_adaptive.v2_runner import RequestPacer
from ecomsre_rcaeval_v2.dev3_execution import provider_config_from_env_file
from ecomsre_rcaeval_v2.dev3_token_accounting import AttemptBudget
from ecomsre_rca_unified.compact_contracts import (
    CompactBaseContext,
    CompactCandidateContext,
    CompactEdge,
    CompactEntity,
    CompactEvidence,
    CompactRetrievalSource,
    EvidenceSource,
)
from ecomsre_rca_unified.contracts import CanonicalEntityLayer
from ecomsre_rca_unified.compact_evaluation import (
    AdmissibilityCase,
    admissibility_aggregate,
    scan_public_payloads,
)
from ecomsre_rca_unified.compact_projection import (
    assert_model_context_private,
    build_obss_compact_inputs,
    build_rca100_compact_inputs,
    discover_label_blind_dev_cases,
)
from ecomsre_rca_unified.compact_prompt import (
    build_request_payload,
    estimate_input_tokens,
    prompt_hashes,
)
from ecomsre_rca_unified.compact_retrieval import build_compact_candidate_context
from ecomsre_rca_unified.compact_runtime import (
    Arm,
    CaseRef,
    CompactTerminalRecord,
    CompactTerminalStatus,
    EVALUATION_VERSION,
    SCHEDULE_SEED,
    ScheduledArm,
    execute_scheduled_arm,
    paired_schedule,
    schedule_payload,
    terminal_status_counts,
    write_create_once,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PROJECT_ROOT / "config" / "rca-compact-evidence-retrieval-v1"
CONTRACT_PATH = CONFIG_ROOT / "contract.json"
PUBLIC_ADMISSIBILITY_JSON = (
    PROJECT_ROOT / "docs" / "analysis" / "rca-compact-retrieval-admissibility.json"
)
PUBLIC_ADMISSIBILITY_MD = (
    PROJECT_ROOT / "docs" / "analysis" / "rca-compact-retrieval-admissibility.md"
)
PUBLIC_ADMISSIBILITY_HUMAN_BRIEF = (
    PROJECT_ROOT
    / "docs"
    / "analysis"
    / "rca-compact-retrieval-admissibility-human-brief.md"
)
PUBLIC_TUNE_JSON = (
    PROJECT_ROOT / "docs" / "results" / "compact-evidence-retrieval-live-tune.json"
)
PUBLIC_TUNE_MD = (
    PROJECT_ROOT / "docs" / "results" / "compact-evidence-retrieval-live-tune.md"
)
PUBLIC_HUMAN_BRIEF = (
    PROJECT_ROOT / "docs" / "results" / "compact-evidence-retrieval-live-human-brief.md"
)
CORE_PATHS = (
    "config/rca-compact-evidence-retrieval-v1/contract.json",
    "scripts/rca_compact/__init__.py",
    "scripts/rca_compact/cli.py",
    "scripts/rca_compact/evaluator.py",
    "src/ecomsre_rca_unified/compact_contracts.py",
    "src/ecomsre_rca_unified/compact_evaluation.py",
    "src/ecomsre_rca_unified/compact_projection.py",
    "src/ecomsre_rca_unified/compact_prompt.py",
    "src/ecomsre_rca_unified/compact_retrieval.py",
    "src/ecomsre_rca_unified/compact_runtime.py",
    "tests/analysis/test_rca_compact_evidence_retrieval.py",
)


def _load_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required JSON is not a regular file: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"required JSON is not an object: {path.name}")
    return value


def _required_int(value: object, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    return value


def _required_number(value: object, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    return float(value)


def _contract() -> dict[str, object]:
    value = _load_object(CONTRACT_PATH)
    if value.get("version") != EVALUATION_VERSION:
        raise ValueError("compact contract version differs from runtime")
    hashes = value.get("prompt_hashes")
    if not isinstance(hashes, Mapping) or dict(hashes) != prompt_hashes():
        raise ValueError("compact prompt/schema hash lock differs from runtime")
    if hashes.get("b0_system_prompt_sha256") != (
        "6b64c9e43f25029ca2f76f491faf98906c70fe888270284bf4bd3ff47e564049"
    ):
        raise ValueError("B0 prompt hash differs from the preserved baseline")
    exact = {
        "architecture": "COMPACT_EVIDENCE_RETRIEVAL_STRONG_SINGLE",
        "concurrency": 1,
        "fallback": "NO_FALLBACK",
        "max_completion_tokens": 2048,
        "minimum_request_spacing_seconds": 5.0,
        "model": "gpt-5.4-mini-2026-03-17",
        "prompt_token_reservation": 29952,
        "schema_retry": "FORBIDDEN",
        "semantic_retry": "FORBIDDEN",
        "temperature": 0.0,
        "timeout_seconds": 30.0,
        "top_p": 1.0,
        "transport_retry": "ONE_ALLOWLISTED_BYTE_IDENTICAL_REQUEST_RETRY",
        "transport_retry_policy_sha256": (
            "7fd010103f83a1cb99b0c478ddafdf6e9fd0dc349a4297e7bb55c9b4157c202b"
        ),
    }
    if any(value.get(key) != expected for key, expected in exact.items()):
        raise ValueError("compact model, budget, or retry contract differs")
    retry_path = (
        PROJECT_ROOT / "config" / "rcaeval-re2-v2-dev3" / "transport-retry-policy.json"
    )
    if _sha_file(retry_path) != value["transport_retry_policy_sha256"]:
        raise ValueError("allowlisted transport retry policy differs")
    return value


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_digest(root: Path) -> dict[str, object]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("frozen input root must be a real directory")
    digest = hashlib.sha256()
    file_count = 0
    byte_count = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise ValueError("frozen input tree contains a symlink")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        stat = path.stat()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(stat.st_size.to_bytes(8, "big"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        file_count += 1
        byte_count += stat.st_size
    return {
        "absolute_root": str(root.resolve(strict=True)),
        "file_count": file_count,
        "byte_count": byte_count,
        "sha256": digest.hexdigest(),
    }


def _git(*args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _require_private_root(path: Path) -> Path:
    resolved_parent = path.parent.resolve(strict=True)
    resolved = resolved_parent / path.name
    project = PROJECT_ROOT.resolve(strict=True)
    if resolved == project or project in resolved.parents:
        raise ValueError("private evaluation root must be outside the repository")
    resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved.chmod(0o700)
    return resolved


def _write_public(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _markdown_bytes(title: str, value: object) -> bytes:
    return (
        f"# {title}\n\n"
        "Classification: `CONSUMED DEVELOPMENT EVALUATION`, "
        "`ONE ARCHITECTURE CANDIDATE`, `NO RERUN`, "
        "`NOT EXTERNAL VALIDATION`.\n\n"
        "```json\n"
        + json.dumps(
            value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True
        )
        + "\n```\n"
    ).encode("utf-8")


def _consumed_obss_refs(
    root: Path, *, ob_root: Path, ss_root: Path
) -> tuple[CaseRef, ...]:
    observed_tree, observed_files = tree_sha256(root)
    if (
        observed_tree
        != "d3e8aba8514b8f688107f1d5728dd4c5e476b28dc3b6a86fc9a2a8b4f43e9363"
        or observed_files != 60
    ):
        raise ValueError(
            "consumed OB/SS TUNE terminal tree differs from the frozen source"
        )
    obss_cases = discover_label_blind_dev_cases(
        ob_root, system="RE2-OB"
    ) + discover_label_blind_dev_cases(ss_root, system="RE2-SS")
    index = {case.case_id: case for case in obss_cases}
    if len(index) != 180:
        raise ValueError("label-blind OB/SS index must contain 180 cases")
    paths = tuple(sorted(root.glob("*.json")))
    if len(paths) != 60:
        raise ValueError("consumed OB/SS TUNE terminal denominator differs")
    refs: list[CaseRef] = []
    for path in paths:
        raw = _load_object(path)
        case_id = raw.get("case_id")
        system = raw.get("system")
        if (
            raw.get("status") != "COMPLETED"
            or raw.get("split") != "TUNE_SET"
            or not isinstance(case_id, str)
            or case_id not in index
            or system not in {"RE2-OB", "RE2-SS"}
            or index[case_id].system != system
        ):
            raise ValueError("consumed OB/SS TUNE terminal is invalid")
        refs.append(CaseRef(source="OBSS", source_key=case_id))
    if len(set(refs)) != 60:
        raise ValueError("consumed OB/SS TUNE identities are not unique")
    return tuple(refs)


def _rca_refs(schedule_path: Path) -> tuple[CaseRef, ...]:
    schedule = RCA100Schedule.model_validate_json(
        schedule_path.read_text(encoding="utf-8")
    )
    refs = tuple(
        CaseRef(source="RCA100", source_key=item.source_task_id)
        for item in schedule.records
    )
    if len(refs) != 103 or len(set(refs)) != 103:
        raise ValueError("RCA100 consumed schedule denominator differs")
    return refs


def _paired_cases(records: Sequence[ScheduledArm]) -> tuple[ScheduledArm, ...]:
    selected = tuple(item for item in records if item.arm_position == 1)
    if len(selected) * 2 != len(records):
        raise ValueError("compact schedule is not paired")
    return selected


def _prepare_contexts(
    *,
    records: tuple[ScheduledArm, ...],
    rca_cases_root: Path,
    rca_schedule_path: Path,
    ob_root: Path,
    ss_root: Path,
    methodology: Mapping[str, object],
    model: str,
    max_completion_tokens: int,
) -> tuple[dict[str, object], ...]:
    rca_schedule = RCA100Schedule.model_validate_json(
        rca_schedule_path.read_text(encoding="utf-8")
    )
    rca_ordinals = {
        item.source_task_id: index
        for index, item in enumerate(rca_schedule.records, start=1)
    }
    obss_cases = discover_label_blind_dev_cases(ob_root, system="RE2-OB") + (
        discover_label_blind_dev_cases(ss_root, system="RE2-SS")
    )
    obss_index = {item.case_id: item for item in obss_cases}
    if len(obss_index) != 180:
        raise ValueError("label-blind OB/SS index must contain 180 cases")
    output: list[dict[str, object]] = []
    for index, record in enumerate(_paired_cases(records), start=1):
        if record.source == "RCA100":
            ordinal = rca_ordinals.get(record.source_key)
            if ordinal is None:
                raise ValueError("RCA100 schedule source is absent")
            base, source = build_rca100_compact_inputs(
                rca_cases_root / record.source_key,
                projection_case_number=ordinal,
                methodology=methodology,
            )
        else:
            case = obss_index.get(record.source_key)
            if case is None:
                raise ValueError("OB/SS consumed case is absent from label-blind input")
            base, source = build_obss_compact_inputs(case)
        candidates = build_compact_candidate_context(base, source)
        assert_model_context_private(
            base,
            record.source_key,
            candidate_payload=candidates.model_visible_dump(),
        )
        b0_payload = build_request_payload(
            model=model,
            base=base,
            arm="B0",
            candidates=None,
            max_completion_tokens=max_completion_tokens,
        )
        c1_payload = build_request_payload(
            model=model,
            base=base,
            arm="C1",
            candidates=candidates,
            max_completion_tokens=max_completion_tokens,
        )
        output.append(
            {
                "ordinal": index,
                "source": record.source,
                "source_key": record.source_key,
                "opaque_case_id": record.opaque_case_id,
                "base": base.model_dump(mode="json"),
                "source_projection": source.model_dump(mode="json"),
                "candidates": candidates.model_dump(mode="json"),
                "estimated_b0_input": estimate_input_tokens(b0_payload),
                "estimated_c1_input": estimate_input_tokens(c1_payload),
            }
        )
    if len(output) != 163:
        raise ValueError("compact context audit denominator differs")
    return tuple(output)


def _truth_score_admissibility(
    prepared: tuple[dict[str, object], ...],
    *,
    rca_cases_root: Path,
    rca_answer_root: Path,
    ob_root: Path,
    ss_root: Path,
) -> tuple[AdmissibilityCase, ...]:
    # Evaluator imports occur only after the label-free context tree is sealed.
    from ecomsre_rca100.entity import load_entity_catalog
    from ecomsre_rca100.evaluator import load_answer_key, prediction_correct
    from ecomsre_rcaeval.dataset import DevSystem, discover_dev_cases

    truths = load_answer_key(rca_answer_root)
    dev_cases = discover_dev_cases(ob_root, DevSystem.RE2_OB) + discover_dev_cases(
        ss_root, DevSystem.RE2_SS
    )
    dev_index = {item.case_id: item for item in dev_cases}
    rows: list[AdmissibilityCase] = []
    for raw in prepared:
        source_name = raw["source"]
        source_key = raw["source_key"]
        candidates = CompactCandidateContext.model_validate_json(
            json.dumps(raw["candidates"], allow_nan=False)
        )
        exact_rank: int | None = None
        service_rank: int | None = None
        if source_name == "RCA100":
            if not isinstance(source_key, str):
                raise ValueError("private RCA100 source identity is invalid")
            truth = truths[source_key]
            catalog = load_entity_catalog(rca_cases_root / source_key / "topology.json")
            exact_rank = next(
                (
                    index
                    for index, card in enumerate(candidates.candidates, start=1)
                    if prediction_correct(card.entity_ref, truth, catalog)
                ),
                None,
            )
            truth_services = {
                (
                    entity.entity_ref
                    if normalize_layer(entity.type) == "SERVICE"
                    else entity.parent_service_ref_or_none
                )
                for entity in catalog.by_ref.values()
                if prediction_correct(entity.entity_ref, truth, catalog)
            }
            truth_services.discard(None)
            service_rank = next(
                (
                    index
                    for index, card in enumerate(candidates.candidates, start=1)
                    if prediction_correct(card.entity_ref, truth, catalog)
                    or card.entity_ref in truth_services
                    or card.service_ancestor_or_none in truth_services
                ),
                None,
            )
        else:
            if not isinstance(source_key, str) or source_key not in dev_index:
                raise ValueError("private OB/SS source identity is invalid")
            truth_ref = (
                f"apm|apm.service|{dev_index[source_key].root_cause_service.casefold()}"
            )
            exact_rank = next(
                (
                    index
                    for index, card in enumerate(candidates.candidates, start=1)
                    if card.entity_ref == truth_ref
                ),
                None,
            )
            service_rank = exact_rank
        visible_refs = {
            item.evidence_ref
            for item in CompactBaseContext.model_validate_json(
                json.dumps(raw["base"], allow_nan=False)
            ).evidence
        }
        candidate_ids = tuple(item.candidate_id for item in candidates.candidates)
        rows.append(
            AdmissibilityCase(
                source=cast(str, source_name),
                candidate_count=len(candidates.candidates),
                exact_gt_rank=exact_rank,
                service_gt_rank=service_rank,
                estimated_b0_input=_required_int(
                    raw["estimated_b0_input"], "estimated B0 input"
                ),
                estimated_c1_input=_required_int(
                    raw["estimated_c1_input"], "estimated C1 input"
                ),
                duplicate_candidate_ids=len(candidate_ids) - len(set(candidate_ids)),
                invalid_refs=sum(
                    not set(item.evidence_refs).issubset(visible_refs)
                    for item in candidates.candidates
                ),
                allocation_buckets=tuple(
                    item.allocation_bucket for item in candidates.candidates
                ),
                visible_sources=tuple(
                    source
                    for item in candidates.candidates
                    for source in item.visible_sources
                ),
            )
        )
    return tuple(rows)


def normalize_layer(entity_type: str) -> str:
    from ecomsre_rca_unified.hierarchy import normalize_entity_layer

    return normalize_entity_layer(entity_type).value


def audit_retrieval(args: argparse.Namespace) -> None:
    contract = _contract()
    private_root = _require_private_root(Path(args.private_root))
    audit_path = private_root / "audit" / "retrieval-contexts.json"
    score_path = private_root / "audit" / "retrieval-case-audit.json"
    gate_path = private_root / "audit" / "admissibility-aggregate.json"
    if any(path.exists() for path in (audit_path, score_path, gate_path)):
        raise ValueError("the one admissibility audit has already been created")
    rca_cases_root = Path(args.rca_cases_root)
    rca_schedule_path = Path(args.rca_schedule_path)
    rca_answer_root = Path(args.rca_answer_root)
    ob_root = Path(args.ob_root)
    ss_root = Path(args.ss_root)
    consumed_root = Path(args.consumed_tune_terminals_root)
    methodology = _load_object(Path(args.methodology))
    model = str(contract["model"])
    max_completion_tokens = _required_int(
        contract["max_completion_tokens"], "maximum completion tokens"
    )
    records = paired_schedule(
        (
            *_rca_refs(rca_schedule_path),
            *_consumed_obss_refs(consumed_root, ob_root=ob_root, ss_root=ss_root),
        )
    )
    schedule_sha = write_create_once(
        private_root / "schedule" / "tune.json", schedule_payload(records)
    )
    input_binding_path = private_root / "locks" / "input-bindings.json"
    created_at_utc: object = datetime.now(timezone.utc).isoformat()
    if input_binding_path.exists():
        created_at_utc = _load_object(input_binding_path).get("created_at_utc")
        if not isinstance(created_at_utc, str):
            raise ValueError("partial input binding timestamp is invalid")
    fresh_bindings = {
        "schema_version": "compact-retrieval.input-bindings.v1",
        "created_at_utc": created_at_utc,
        "trees": {
            "rca100": _tree_digest(rca_cases_root),
            "obss_ob": _tree_digest(ob_root),
            "obss_ss": _tree_digest(ss_root),
            "consumed_tune_terminals": _tree_digest(consumed_root),
            "rca100_answers": _tree_digest(rca_answer_root),
        },
        "files": {
            "rca100_schedule_sha256": _sha_file(rca_schedule_path),
            "methodology_sha256": _sha_file(Path(args.methodology)),
        },
        "schedule_sha256": schedule_sha,
    }
    binding_sha = write_create_once(input_binding_path, fresh_bindings)
    prepared = _prepare_contexts(
        records=records,
        rca_cases_root=rca_cases_root,
        rca_schedule_path=rca_schedule_path,
        ob_root=ob_root,
        ss_root=ss_root,
        methodology=methodology,
        model=model,
        max_completion_tokens=max_completion_tokens,
    )
    context_sha = write_create_once(
        audit_path,
        {
            "schema_version": "compact-retrieval.private-context-audit.v1",
            "evaluation_version": EVALUATION_VERSION,
            "schedule_sha256": schedule_sha,
            "input_bindings_sha256": binding_sha,
            "records": list(prepared),
        },
    )
    rows = _truth_score_admissibility(
        prepared,
        rca_cases_root=rca_cases_root,
        rca_answer_root=rca_answer_root,
        ob_root=ob_root,
        ss_root=ss_root,
    )
    write_create_once(
        score_path,
        {
            "schema_version": "compact-retrieval.private-case-audit.v1",
            "context_audit_sha256": context_sha,
            "records": [
                {
                    "source": item.source,
                    "candidate_count": item.candidate_count,
                    "exact_gt_rank": item.exact_gt_rank,
                    "service_gt_rank": item.service_gt_rank,
                    "estimated_b0_input": item.estimated_b0_input,
                    "estimated_c1_input": item.estimated_c1_input,
                    "duplicate_candidate_ids": item.duplicate_candidate_ids,
                    "invalid_refs": item.invalid_refs,
                    "allocation_buckets": list(item.allocation_buckets),
                    "visible_sources": list(item.visible_sources),
                }
                for item in rows
            ],
        },
    )
    legacy = _load_object(
        PROJECT_ROOT
        / "docs"
        / "analysis"
        / "rca100-propagation-visibility-attribution.json"
    )
    funnel = legacy.get("root_visibility_funnel")
    if not isinstance(funnel, Mapping):
        raise ValueError("PR #24 legacy visibility attribution is absent")
    visible = funnel.get("ground_truth_in_any_model_visible_evidence")
    if (
        not isinstance(visible, Mapping)
        or visible.get("denominator") != 103
        or visible.get("numerator") != 44
    ):
        raise ValueError("PR #24 legacy exact visibility value differs")
    aggregate = admissibility_aggregate(rows, legacy_exact_visible=44)
    write_create_once(gate_path, aggregate)
    public_json = _json_bytes(aggregate)
    public_md = _markdown_bytes("Compact Retrieval Admissibility Audit", aggregate)
    outputs = {
        PUBLIC_ADMISSIBILITY_JSON: public_json,
        PUBLIC_ADMISSIBILITY_MD: public_md,
    }
    scan_public_payloads(outputs)
    for path, payload in outputs.items():
        _write_public(path, payload)
    print(json.dumps(aggregate["gate"], sort_keys=True))


def _synthetic_context() -> tuple[CompactBaseContext, CompactCandidateContext]:
    left_ref = "apm|apm.service|synthetic-upstream"
    right_ref = "apm|apm.service|synthetic-alert"
    entities = (
        CompactEntity(
            entity_ref=left_ref,
            display_name="synthetic-upstream",
            layer=CanonicalEntityLayer.SERVICE,
            service_ancestor_or_none=left_ref,
        ),
        CompactEntity(
            entity_ref=right_ref,
            display_name="synthetic-alert",
            layer=CanonicalEntityLayer.SERVICE,
            service_ancestor_or_none=right_ref,
        ),
    )
    base = CompactBaseContext(
        alert_title="Synthetic service alert",
        prompt_text="Select the causal root using only the synthetic bounded evidence.",
        alert_entity_ref=right_ref,
        entities=entities,
        evidence=(
            CompactEvidence(
                evidence_ref="metric:0001",
                source="METRICS",
                entity_ref=left_ref,
                name="synthetic latency",
                started_at=1.0,
                ended_at=2.0,
                score=2.0,
                summary="synthetic upstream anomaly",
            ),
            CompactEvidence(
                evidence_ref="log:0001",
                source="LOGS",
                entity_ref=right_ref,
                name="synthetic error",
                started_at=3.0,
                ended_at=3.0,
                score=1.0,
                summary="synthetic downstream error",
            ),
        ),
        source_status={
            "METRICS": "AVAILABLE",
            "LOGS": "AVAILABLE",
            "TRACES": "SOURCE_UNAVAILABLE",
        },
    )
    source_visibility: dict[str, frozenset[EvidenceSource]] = {
        left_ref: frozenset({"METRICS"}),
        right_ref: frozenset({"LOGS", "ALERTS"}),
    }
    source_occurrences: dict[str, dict[EvidenceSource, int]] = {
        left_ref: {"METRICS": 1},
        right_ref: {"LOGS": 1, "ALERTS": 1},
    }
    source = CompactRetrievalSource(
        entities=entities,
        edges=(
            CompactEdge(
                source_entity_ref=left_ref,
                target_entity_ref=right_ref,
                edge_type="DIRECTED_TOPOLOGY",
            ),
        ),
        source_visibility=source_visibility,
        source_occurrences=source_occurrences,
        first_anomaly_time={left_ref: 1.0, right_ref: 3.0},
        metrics_ranking=(left_ref,),
        metrics_scores={left_ref: 2.0},
        alert_entities=(right_ref,),
    )
    return base, build_compact_candidate_context(base, source)


def _provider_limits(contract: Mapping[str, object]) -> tuple[str, float, int, int]:
    return (
        str(contract["model"]),
        _required_number(contract["timeout_seconds"], "Provider timeout"),
        _required_int(contract["max_completion_tokens"], "completion budget"),
        _required_int(contract["prompt_token_reservation"], "prompt reservation"),
    )


def provider_preflight(args: argparse.Namespace) -> None:
    contract = _contract()
    private_root = _require_private_root(Path(args.private_root))
    _implementation, implementation_sha = _verify_implementation(private_root)
    aggregate = _load_object(private_root / "audit" / "admissibility-aggregate.json")
    gate = aggregate.get("gate")
    if not isinstance(gate, Mapping) or gate.get("passed") is not True:
        raise ValueError("COMPACT_RETRIEVAL_ADMISSIBILITY_NOT_PASSED_KEEP_A0")
    generation = int(args.generation)
    if generation not in {1, 2}:
        raise ValueError("Provider preflight generation must be 1 or 2")
    if (
        generation == 2
        and not (private_root / "preflight" / "generation-1" / "summary.json").exists()
    ):
        raise ValueError("second preflight requires a preserved first generation")
    generation_root = private_root / "preflight" / f"generation-{generation}"
    if (generation_root / "summary.json").exists():
        raise ValueError("Provider preflight generation already exists")
    model, timeout, max_completion, prompt_reservation = _provider_limits(contract)
    config = provider_config_from_env_file(Path(args.env_file))
    if config.model != model:
        raise ValueError("Provider preflight model differs from compact lock")
    base, candidates = _synthetic_context()
    schedule_sha = _sha_file(private_root / "schedule" / "tune.json")
    retry_sha = str(contract["transport_retry_policy_sha256"])
    budget = AttemptBudget(
        max_provider_attempts=4,
        max_retry_attempts=2,
        prompt_token_reservation=prompt_reservation,
        max_completion_tokens=max_completion,
        max_conservative_tokens=4 * (prompt_reservation + max_completion),
    )
    pacer = RequestPacer(
        _required_number(
            contract["minimum_request_spacing_seconds"], "Provider spacing"
        )
    )
    records = tuple(
        ScheduledArm(
            split="PREFLIGHT",
            pair_position=1,
            arm_position=index,
            opaque_case_id="case-"
            + hashlib.sha256(f"preflight-{generation}".encode()).hexdigest()[:20],
            source="RCA100",
            source_key="synthetic",
            arm=arm,
            run_id=hashlib.sha256(
                f"preflight-{generation}-{arm.value}".encode()
            ).hexdigest()[:32],
        )
        for index, arm in enumerate((Arm.B0, Arm.C1), start=1)
    )
    terminals = tuple(
        execute_scheduled_arm(
            record,
            base=base,
            candidates=candidates if record.arm is Arm.C1 else None,
            journal_root=generation_root / "journal",
            output_root=generation_root / "output",
            schedule_sha256=schedule_sha,
            implementation_lock_sha256=implementation_sha,
            provider_config=config,
            expected_model=model,
            timeout_seconds=timeout,
            max_completion_tokens=max_completion,
            prompt_token_reservation=prompt_reservation,
            pacer=pacer,
            budget=budget,
            retry_policy_sha256=retry_sha,
        )
        for record in records
    )
    summary = {
        "schema_version": "compact-retrieval.provider-preflight.v1",
        "generation": generation,
        "generic_repair_used": generation == 2,
        "arms": {
            item.arm.value: {
                "status": item.status.value,
                "provider_attempts": item.provider_attempts,
                "transport_retries": item.transport_retries,
                "known_usage": item.input_tokens_if_known is not None,
                "input_tokens": item.input_tokens_if_known,
                "output_tokens": item.output_tokens_if_known,
                "candidate_id_valid": (
                    True
                    if item.arm is Arm.B0
                    else bool(
                        item.diagnosis is not None
                        and item.diagnosis.root_candidate_id is not None
                    )
                ),
            }
            for item in terminals
        },
        "http_429": sum(item.failure_code == "HTTP_429" for item in terminals),
        "invalid_schema": sum(
            item.status is CompactTerminalStatus.INVALID_SCHEMA for item in terminals
        ),
    }
    passed = bool(
        all(item.status is CompactTerminalStatus.COMPLETED for item in terminals)
        and all(
            item.input_tokens_if_known is not None
            and item.output_tokens_if_known is not None
            and item.input_tokens_if_known + item.output_tokens_if_known > 0
            for item in terminals
        )
        and summary["http_429"] == 0
        and summary["invalid_schema"] == 0
    )
    summary["passed"] = passed
    summary["verdict"] = (
        "COMPACT_PROVIDER_PREFLIGHT_PASSED"
        if passed
        else "BLOCKED_COMPACT_PROVIDER_PREFLIGHT"
    )
    write_create_once(generation_root / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))


def freeze_implementation(args: argparse.Namespace) -> None:
    contract = _contract()
    private_root = _require_private_root(Path(args.private_root))
    lock_path = private_root / "locks" / "implementation-lock.json"
    if lock_path.exists():
        raise ValueError("compact implementation is already frozen")
    if _git("status", "--porcelain"):
        raise ValueError("compact implementation freeze requires a clean worktree")
    head = _git("rev-parse", "HEAD")
    base = _git("merge-base", "HEAD", "origin/main")
    changed = tuple(
        line
        for line in _git("diff", "--name-only", f"{base}..HEAD").splitlines()
        if line
    )
    required = set(CORE_PATHS)
    if not required.issubset(set(changed)):
        missing = sorted(required - set(changed))
        raise ValueError(f"compact implementation surface is incomplete: {missing}")
    protected = {
        path: _sha_file(PROJECT_ROOT / path)
        for path in sorted(changed)
        if (PROJECT_ROOT / path).is_file()
    }
    audit_path = private_root / "audit" / "admissibility-aggregate.json"
    gate = _load_object(audit_path).get("gate")
    if not isinstance(gate, Mapping) or gate.get("passed") is not True:
        raise ValueError("cannot freeze an inadmissible compact implementation")
    preflight_summaries = tuple(
        sorted((private_root / "preflight").glob("generation-*/summary.json"))
    )
    if preflight_summaries:
        raise ValueError("implementation must freeze before Provider preflight")
    lock = {
        "schema_version": "compact-retrieval.implementation-lock.v1",
        "evaluation_version": EVALUATION_VERSION,
        "implementation_commit": head,
        "base_commit": base,
        "changed_paths": list(changed),
        "protected_sha256": protected,
        "contract_sha256": _sha_file(CONTRACT_PATH),
        "schedule_sha256": _sha_file(private_root / "schedule" / "tune.json"),
        "admissibility_sha256": _sha_file(audit_path),
        "ci": {
            "status": args.ci_status,
            "reference": args.ci_reference,
        },
        "review": {
            "status": args.review_status,
            "reference": args.review_reference,
        },
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": contract["model"],
    }
    if args.ci_status != "SUCCESS" or args.review_status != "PASS":
        raise ValueError("implementation freeze requires successful CI and review")
    sha = write_create_once(lock_path, lock)
    print(
        json.dumps({"implementation_lock_sha256": sha, "commit": head}, sort_keys=True)
    )


def _verify_implementation(private_root: Path) -> tuple[dict[str, object], str]:
    lock_path = private_root / "locks" / "implementation-lock.json"
    lock = _load_object(lock_path)
    if _git("status", "--porcelain"):
        raise ValueError("Provider admission requires a clean implementation")
    if _git("rev-parse", "HEAD") != lock.get("implementation_commit"):
        raise ValueError("Provider admission implementation commit differs")
    protected = lock.get("protected_sha256")
    if not isinstance(protected, Mapping):
        raise ValueError("implementation lock protected paths are absent")
    for raw_path, expected in protected.items():
        if (
            not isinstance(raw_path, str)
            or _sha_file(PROJECT_ROOT / raw_path) != expected
        ):
            raise ValueError("frozen compact implementation content differs")
    if _sha_file(CONTRACT_PATH) != lock.get("contract_sha256"):
        raise ValueError("compact contract hash differs from implementation lock")
    return lock, _sha_file(lock_path)


def _load_prepared(private_root: Path) -> dict[tuple[str, str], dict[str, object]]:
    value = _load_object(private_root / "audit" / "retrieval-contexts.json")
    records = value.get("records")
    if not isinstance(records, list) or len(records) != 163:
        raise ValueError("private compact context audit differs")
    output: dict[tuple[str, str], dict[str, object]] = {}
    for raw in records:
        if not isinstance(raw, dict):
            raise ValueError("private compact context record is invalid")
        key = (str(raw.get("source")), str(raw.get("source_key")))
        if key in output:
            raise ValueError("private compact context identity repeats")
        output[key] = raw
    return output


def _load_schedule(private_root: Path) -> tuple[ScheduledArm, ...]:
    value = _load_object(private_root / "schedule" / "tune.json")
    raw_records = value.get("records")
    if (
        value.get("seed") != SCHEDULE_SEED
        or not isinstance(raw_records, list)
        or len(raw_records) != 326
    ):
        raise ValueError("private compact schedule differs")
    output = tuple(
        ScheduledArm(
            split="TUNE",
            pair_position=_required_int(raw["pair_position"], "pair position"),
            arm_position=_required_int(raw["arm_position"], "arm position"),
            opaque_case_id=str(raw["opaque_case_id"]),
            source=cast(Literal["RCA100", "OBSS"], raw["source"]),
            source_key=str(raw["source_key"]),
            arm=Arm(str(raw["arm"])),
            run_id=str(raw["run_id"]),
        )
        for raw in raw_records
        if isinstance(raw, Mapping)
    )
    if len(output) != 326:
        raise ValueError("private compact schedule record schema differs")
    return output


def run_tune(args: argparse.Namespace) -> None:
    contract = _contract()
    private_root = _require_private_root(Path(args.private_root))
    if (private_root / "runtime" / "tune" / "execution-summary.json").exists():
        raise ValueError("the one live TUNE has already terminated")
    _implementation, implementation_sha = _verify_implementation(private_root)
    preflights = tuple(
        sorted((private_root / "preflight").glob("generation-*/summary.json"))
    )
    if not preflights:
        raise ValueError("BLOCKED_COMPACT_PROVIDER_PREFLIGHT")
    preflight = _load_object(preflights[-1])
    if preflight.get("passed") is not True:
        raise ValueError("BLOCKED_COMPACT_PROVIDER_PREFLIGHT")
    model, timeout, max_completion, prompt_reservation = _provider_limits(contract)
    config: OpenAICompatibleConfig = provider_config_from_env_file(Path(args.env_file))
    if config.model != model:
        raise ValueError("live TUNE Provider model differs from compact lock")
    records = _load_schedule(private_root)
    prepared = _load_prepared(private_root)
    schedule_sha = _sha_file(private_root / "schedule" / "tune.json")
    tune_journal_root = private_root / "runtime" / "tune" / "journal"
    existing_run_roots = tuple(
        sorted(
            path
            for path in (tune_journal_root / "runs").glob("*")
            if path.is_dir() and not path.is_symlink()
        )
    )
    budget = AttemptBudget.restore(
        existing_run_roots,
        max_provider_attempts=652,
        max_retry_attempts=326,
        prompt_token_reservation=prompt_reservation,
        max_completion_tokens=max_completion,
        max_conservative_tokens=20_864_000,
    )
    pacer = RequestPacer(
        _required_number(
            contract["minimum_request_spacing_seconds"], "Provider spacing"
        )
    )
    terminals: list[CompactTerminalRecord] = []
    for index, record in enumerate(records, start=1):
        raw = prepared[(record.source, record.source_key)]
        base = CompactBaseContext.model_validate_json(
            json.dumps(raw["base"], allow_nan=False)
        )
        candidates = CompactCandidateContext.model_validate_json(
            json.dumps(raw["candidates"], allow_nan=False)
        )
        assert_model_context_private(
            base,
            record.source_key,
            candidate_payload=candidates.model_visible_dump(),
        )
        terminal = execute_scheduled_arm(
            record,
            base=base,
            candidates=candidates if record.arm is Arm.C1 else None,
            journal_root=tune_journal_root,
            output_root=private_root / "runtime" / "tune" / "output",
            schedule_sha256=schedule_sha,
            implementation_lock_sha256=implementation_sha,
            provider_config=config,
            expected_model=model,
            timeout_seconds=timeout,
            max_completion_tokens=max_completion,
            prompt_token_reservation=prompt_reservation,
            pacer=pacer,
            budget=budget,
            retry_policy_sha256=str(contract["transport_retry_policy_sha256"]),
        )
        terminals.append(terminal)
        print(
            json.dumps(
                {
                    "completed_arms": index,
                    "planned_arms": 326,
                    "arm": record.arm.value,
                    "status": terminal.status.value,
                    "provider_attempts": terminal.provider_attempts,
                    "transport_retries": terminal.transport_retries,
                },
                sort_keys=True,
            ),
            flush=True,
        )
    summary = {
        "schema_version": "compact-retrieval.tune-execution.v1",
        "evaluation_version": EVALUATION_VERSION,
        "planned_arms": 326,
        "terminal_count": len(terminals),
        "status_counts": terminal_status_counts(tuple(terminals)),
        "provider_attempts": sum(item.provider_attempts for item in terminals),
        "transport_retries": sum(item.transport_retries for item in terminals),
        "semantic_model_operations": sum(
            item.semantic_model_operations for item in terminals
        ),
        "specialist_calls": 0,
        "fusion_calls": 0,
        "http_429": sum(item.failure_code == "HTTP_429" for item in terminals),
        "schedule_sha256": schedule_sha,
        "implementation_lock_sha256": implementation_sha,
        "no_rerun": True,
    }
    write_create_once(
        private_root / "runtime" / "tune" / "execution-summary.json", summary
    )
    print(json.dumps(summary, sort_keys=True))


def _human_brief_bytes(aggregate: Mapping[str, object]) -> bytes:
    gate = aggregate.get("gate")
    rca = aggregate.get("rca100")
    obss = aggregate.get("obss")
    combined = aggregate.get("combined")
    cost = aggregate.get("cost")
    execution = aggregate.get("execution")
    if not all(
        isinstance(item, Mapping)
        for item in (gate, rca, obss, combined, cost, execution)
    ):
        raise ValueError("compact aggregate lacks Human Brief dimensions")
    assert isinstance(gate, Mapping)
    assert isinstance(rca, Mapping)
    assert isinstance(obss, Mapping)
    assert isinstance(combined, Mapping)
    assert isinstance(cost, Mapping)
    assert isinstance(execution, Mapping)
    return (
        "# Compact Evidence-Retrieval Strong Single — Human Brief\n\n"
        f"**Verdict:** `{gate.get('verdict')}`\n\n"
        "这是一次已消耗的 development evaluation：只评估一个架构候选、只执行一次 paired live run、"
        "不重跑，也不构成 external validation。\n\n"
        "## 结果摘要\n\n"
        f"- RCA100 Exact Root B0/C1: {rca.get('b0_root_correct')}/103 → "
        f"{rca.get('c1_root_correct')}/103；Net Rescue "
        f"{rca.get('root_net_rescue')}。\n"
        f"- OB/SS Root B0/C1: {obss.get('b0_root_correct')}/60 → "
        f"{obss.get('c1_root_correct')}/60；Net Rescue "
        f"{obss.get('root_net_rescue')}。\n"
        f"- Combined Root Net Rescue: {combined.get('root_net_rescue')}。\n"
        f"- C1 completed: {execution.get('c1_completed')}/163；"
        f"INVALID_SCHEMA: {execution.get('c1_invalid_schema')}；"
        f"HTTP 429: {execution.get('http_429')}。\n\n"
        "A0 仍保留为工程 fallback。本 Goal 不运行 Regression、不 merge、不 release。\n"
    ).encode("utf-8")


def evaluate_tune_result(args: argparse.Namespace) -> None:
    private_root = _require_private_root(Path(args.private_root))
    _implementation, implementation_sha = _verify_implementation(private_root)
    execution = _load_object(
        private_root / "runtime" / "tune" / "execution-summary.json"
    )
    if execution.get("terminal_count") != 326:
        raise ValueError("the one live TUNE did not terminalize all planned arms")
    result_paths = (
        private_root / "results" / "private-case-scores.json",
        private_root / "results" / "aggregate.json",
        private_root / "locks" / "result-lock.json",
        PUBLIC_TUNE_JSON,
        PUBLIC_TUNE_MD,
        PUBLIC_HUMAN_BRIEF,
    )
    if any(path.exists() for path in result_paths):
        raise ValueError("compact TUNE result has already been evaluated")
    terminal_binding = _tree_digest(
        private_root / "runtime" / "tune" / "output" / "terminals"
    )
    from scripts.rca_compact.evaluator import case_scores_payload, evaluate_tune

    admissibility = _load_object(
        private_root / "audit" / "admissibility-aggregate.json"
    )
    aggregate, scores = evaluate_tune(
        schedule_path=private_root / "schedule" / "tune.json",
        terminals_root=private_root / "runtime" / "tune" / "output",
        rca_cases_root=Path(args.rca_cases_root),
        ob_root=Path(args.ob_root),
        ss_root=Path(args.ss_root),
        answer_root=Path(args.rca_answer_root),
        implementation_lock_sha256=implementation_sha,
        candidate_recall={
            "rca100": admissibility["rca100"],
            "obss": admissibility["obss"],
        },
    )
    private_scores_sha = write_create_once(
        private_root / "results" / "private-case-scores.json",
        case_scores_payload(scores),
    )
    aggregate_sha = write_create_once(
        private_root / "results" / "aggregate.json", aggregate
    )
    write_create_once(
        private_root / "locks" / "result-lock.json",
        {
            "schema_version": "compact-retrieval.result-lock.v1",
            "implementation_lock_sha256": implementation_sha,
            "schedule_sha256": _sha_file(private_root / "schedule" / "tune.json"),
            "terminal_tree": terminal_binding,
            "private_case_scores_sha256": private_scores_sha,
            "aggregate_sha256": aggregate_sha,
            "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    public_outputs = {
        PUBLIC_TUNE_JSON: _json_bytes(aggregate),
        PUBLIC_TUNE_MD: _markdown_bytes(
            "Compact Evidence-Retrieval Strong Single Live TUNE", aggregate
        ),
        PUBLIC_HUMAN_BRIEF: _human_brief_bytes(aggregate),
    }
    scan_public_payloads(public_outputs)
    for path, payload in public_outputs.items():
        _write_public(path, payload)
    print(json.dumps(aggregate["gate"], sort_keys=True))


def verify_tune_result(args: argparse.Namespace) -> None:
    private_root = _require_private_root(Path(args.private_root))
    lock = _load_object(private_root / "locks" / "implementation-lock.json")
    result_lock = _load_object(private_root / "locks" / "result-lock.json")
    implementation_sha = _sha_file(private_root / "locks" / "implementation-lock.json")
    protected = lock.get("protected_sha256")
    if not isinstance(protected, Mapping):
        raise ValueError("implementation lock protected paths are absent")
    for raw_path, expected in protected.items():
        if (
            not isinstance(raw_path, str)
            or _sha_file(PROJECT_ROOT / raw_path) != expected
        ):
            raise ValueError("frozen implementation changed after live admission")
    if (
        result_lock.get("implementation_lock_sha256") != implementation_sha
        or result_lock.get("schedule_sha256")
        != _sha_file(private_root / "schedule" / "tune.json")
        or result_lock.get("terminal_tree")
        != _tree_digest(private_root / "runtime" / "tune" / "output" / "terminals")
    ):
        raise ValueError("compact result lock binding differs")
    from scripts.rca_compact.evaluator import case_scores_payload, evaluate_tune

    admissibility = _load_object(
        private_root / "audit" / "admissibility-aggregate.json"
    )
    aggregate, scores = evaluate_tune(
        schedule_path=private_root / "schedule" / "tune.json",
        terminals_root=private_root / "runtime" / "tune" / "output",
        rca_cases_root=Path(args.rca_cases_root),
        ob_root=Path(args.ob_root),
        ss_root=Path(args.ss_root),
        answer_root=Path(args.rca_answer_root),
        implementation_lock_sha256=implementation_sha,
        candidate_recall={
            "rca100": admissibility["rca100"],
            "obss": admissibility["obss"],
        },
    )
    if (
        _json_bytes(case_scores_payload(scores))
        != (private_root / "results" / "private-case-scores.json").read_bytes()
    ):
        raise ValueError("private compact case scores do not recompute exactly")
    if (
        _json_bytes(aggregate)
        != (private_root / "results" / "aggregate.json").read_bytes()
    ):
        raise ValueError("private compact aggregate does not recompute exactly")
    if result_lock.get("private_case_scores_sha256") != _sha_file(
        private_root / "results" / "private-case-scores.json"
    ) or result_lock.get("aggregate_sha256") != _sha_file(
        private_root / "results" / "aggregate.json"
    ):
        raise ValueError("compact result artifact hash differs")
    public_outputs = {
        PUBLIC_TUNE_JSON: PUBLIC_TUNE_JSON.read_bytes(),
        PUBLIC_TUNE_MD: PUBLIC_TUNE_MD.read_bytes(),
        PUBLIC_HUMAN_BRIEF: PUBLIC_HUMAN_BRIEF.read_bytes(),
    }
    if public_outputs[PUBLIC_TUNE_JSON] != _json_bytes(aggregate):
        raise ValueError("public compact aggregate differs from canonical recompute")
    scan_public_payloads(public_outputs)
    print(
        json.dumps(
            {
                "canonical_aggregate_recompute": "PASS",
                "private_case_scores_recompute": "PASS",
                "public_leakage_scan": "PASS",
            },
            sort_keys=True,
        )
    )


def verify_public(args: argparse.Namespace) -> None:
    outputs = {
        path: path.read_bytes()
        for path in (
            PUBLIC_ADMISSIBILITY_JSON,
            PUBLIC_ADMISSIBILITY_MD,
            PUBLIC_ADMISSIBILITY_HUMAN_BRIEF,
            PUBLIC_TUNE_JSON,
            PUBLIC_TUNE_MD,
            PUBLIC_HUMAN_BRIEF,
        )
        if path.exists()
    }
    scan_public_payloads(outputs)
    print(
        json.dumps(
            {"public_outputs": len(outputs), "leakage_scan": "PASS"}, sort_keys=True
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.rca_compact.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit-retrieval")
    audit.add_argument("--private-root", required=True)
    audit.add_argument("--rca-cases-root", required=True)
    audit.add_argument("--rca-schedule-path", required=True)
    audit.add_argument("--rca-answer-root", required=True)
    audit.add_argument("--ob-root", required=True)
    audit.add_argument("--ss-root", required=True)
    audit.add_argument("--consumed-tune-terminals-root", required=True)
    audit.add_argument("--methodology", required=True)
    audit.set_defaults(handler=audit_retrieval)

    preflight = subparsers.add_parser("provider-preflight")
    preflight.add_argument("--private-root", required=True)
    preflight.add_argument("--env-file", required=True)
    preflight.add_argument("--generation", type=int, default=1)
    preflight.set_defaults(handler=provider_preflight)

    freeze = subparsers.add_parser("freeze-implementation")
    freeze.add_argument("--private-root", required=True)
    freeze.add_argument("--ci-status", required=True)
    freeze.add_argument("--ci-reference", required=True)
    freeze.add_argument("--review-status", required=True)
    freeze.add_argument("--review-reference", required=True)
    freeze.set_defaults(handler=freeze_implementation)

    tune = subparsers.add_parser("run-tune")
    tune.add_argument("--private-root", required=True)
    tune.add_argument("--env-file", required=True)
    tune.set_defaults(handler=run_tune)

    for command, handler in (
        ("evaluate-tune", evaluate_tune_result),
        ("verify-result", verify_tune_result),
    ):
        result = subparsers.add_parser(command)
        result.add_argument("--private-root", required=True)
        result.add_argument("--rca-cases-root", required=True)
        result.add_argument("--rca-answer-root", required=True)
        result.add_argument("--ob-root", required=True)
        result.add_argument("--ss-root", required=True)
        result.set_defaults(handler=handler)

    verify = subparsers.add_parser("verify-public")
    verify.set_defaults(handler=verify_public)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.handler(args)


if __name__ == "__main__":
    main()
