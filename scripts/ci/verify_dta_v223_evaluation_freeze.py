"""Verify the frozen v2.2.3 evaluation portfolio and single-run boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Mapping, Sequence, cast

from ecomsre.dta_v2.v22.action_catalog import StaticTopologyV22
from ecomsre.dta_v2.v22.admission_dispatch_campaign_v223 import (
    SHARED_SELECTION_SYSTEM_PROMPT_V223,
    StudyCombinationV223,
    balanced_combination_order_v223,
)
from ecomsre.dta_v2.v22.admission_dispatch_cli_v223 import (
    AdmissionDispatchStudyArtifactV223,
)
from ecomsre.dta_v2.v22.admission_dispatch_scorer_v223 import (
    score_admission_dispatch_study_v223,
)
from ecomsre.dta_v2.v22.effective_policy_v222 import (
    evaluate_replay_no_incident_coverage_v222,
)
from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import (
    EvidenceUtilityAuditReportV222,
    ShortestAdmissiblePathV222,
    audit_case_set_v222,
)
from ecomsre.dta_v2.v22.memory import build_memory_views_v22
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.practical_dataset import (
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.practical_runner import _baseline, _bootstrap
from ecomsre.dta_v2.v22.predicates import evaluate_no_incident_v22
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22


DEFAULT_MANIFEST = Path("config/dta-v22-3/evaluation/manifest.json")
FINAL_JSON = Path("docs/results/dta-v22-3-admission-dispatch-evaluation.json")
FINAL_MARKDOWN = Path("docs/results/dta-v22-3-admission-dispatch-evaluation.md")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bound_path(root: Path, item: object, name: str) -> Path:
    if not isinstance(item, Mapping):
        raise ValueError(f"v2.2.3 manifest lacks {name}")
    relative = Path(str(item.get("path")))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"v2.2.3 frozen {name} path escapes the repository")
    path = (root / relative).resolve()
    if root != path and root not in path.parents:
        raise ValueError(f"v2.2.3 frozen {name} path escapes the repository")
    if not path.is_file() or item.get("sha256") != _sha256(path):
        raise ValueError(f"v2.2.3 frozen {name} differs")
    return path


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verify_evaluation_freeze_v223(
    *, repository_root: Path, manifest_path: Path, require_pre_execution: bool
) -> dict[str, object]:
    root = repository_root.resolve()
    manifest = cast(dict[str, object], json.loads(manifest_path.read_bytes()))
    if manifest.get("schema_version") != "dta-v22.3.evaluation-manifest.v1":
        raise ValueError("v2.2.3 evaluation manifest schema differs")
    if manifest.get("expected_cases") != 16 or manifest.get("expected_runs") != 64:
        raise ValueError("v2.2.3 evaluation cardinality differs")
    if manifest.get("full_study_execution_count") != 1:
        raise ValueError("v2.2.3 full-study execution contract differs")
    if (
        manifest.get("single_execution_rule")
        != "EXACTLY_ONE_FULL_STUDY_EXECUTION"
        or manifest.get("schedule_rule")
        != "DETERMINISTIC_BALANCED_ROTATION_INTERLEAVED_BY_CASE"
        or manifest.get("truth_isolation_rule")
        != "LOAD_ONLY_AFTER_ALL_FOUR_CASE_RUNS"
        or manifest.get("maximum_protocol_repairs_per_case") != 2
        or manifest.get("maximum_transport_retries_per_exact_request") != 3
        or manifest.get("execution_state") != "NOT_STARTED"
        or manifest.get("docker_calls") != 0
        or manifest.get("runbook_calls") != 0
        or manifest.get("agent_writes") != 0
        or manifest.get("combinations")
        != [item.value for item in StudyCombinationV223]
        or manifest.get("prompt_sha256")
        != _text_sha256(SHARED_SELECTION_SYSTEM_PROMPT_V223)
    ):
        raise ValueError("v2.2.3 evaluation protocol binding differs")
    case_path = _bound_path(root, manifest.get("case_set"), "case set")
    truth_path = _bound_path(root, manifest.get("truth_set"), "truth set")
    utility_path = _bound_path(root, manifest.get("utility_audit"), "utility audit")
    _bound_path(root, manifest.get("predicate_yield_prior"), "predicate prior")
    _bound_path(root, manifest.get("development_result"), "development result")
    _bound_path(root, manifest.get("historical_results_manifest"), "historical results")
    for index, item in enumerate(cast(list[object], manifest.get("agent_visible_sources"))):
        _bound_path(root, item, f"agent-visible source {index}")
    for index, item in enumerate(cast(list[object], manifest.get("implementation_sources"))):
        _bound_path(root, item, f"implementation source {index}")

    implementation_commit = str(manifest.get("implementation_commit"))
    object_check = subprocess.run(
        ["git", "cat-file", "-e", f"{implementation_commit}^{{commit}}"],
        cwd=root,
        check=False,
    )
    if object_check.returncode != 0:
        raise ValueError("v2.2.3 frozen implementation commit is absent")
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", implementation_commit, "HEAD"],
        cwd=root,
        check=False,
    )
    if ancestry.returncode != 0:
        # GitHub squash-merges do not retain feature commits as ancestors. The
        # bindings above are the content proof in that topology; still require
        # the manifest's exact base to be in the current first-party history.
        base_commit = str(manifest.get("base_commit"))
        base_ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", base_commit, "HEAD"],
            cwd=root,
            check=False,
        )
        if base_ancestry.returncode != 0:
            raise ValueError(
                "v2.2.3 frozen implementation is neither ancestral nor content-bound"
            )

    case_set = load_practical_case_set_v22(case_path)
    truth_set = load_practical_truth_set_v22(truth_path)
    utility = EvidenceUtilityAuditReportV222.model_validate_json(utility_path.read_bytes())
    if len(case_set.cases) != 16 or len(truth_set.truths) != 16:
        raise ValueError("v2.2.3 frozen case or truth count differs")
    truth_by_id = {item.case_id: item for item in truth_set.truths}
    if set(truth_by_id) != {item.case_id for item in case_set.cases}:
        raise ValueError("v2.2.3 frozen case/truth IDs differ")
    composition: dict[str, int] = {}
    for truth in truth_set.truths:
        label: str = truth.expected_terminal
        if label == "DIAGNOSED":
            label = cast(str, truth.expected_mechanism)
        composition[label] = composition.get(label, 0) + 1
    if composition != manifest.get("composition"):
        raise ValueError("v2.2.3 frozen composition differs")
    feasible = sum(
        item.expected_terminal == "DIAGNOSED"
        and item.shortest_admissible_path
        in {ShortestAdmissiblePathV222.ONE, ShortestAdmissiblePathV222.TWO}
        for item in utility.cases
    )
    ambiguous = sum(
        item.expected_terminal == "DIAGNOSED"
        and any(action.support_clause_became_admissible for action in item.actions)
        and any(action.read_status == "SUCCESS_EMPTY" for action in item.actions)
        for item in utility.cases
    )
    if feasible != 10 or ambiguous < 4:
        raise ValueError("v2.2.3 frozen utility properties differ")
    regenerated_utility = audit_case_set_v222(
        repository_root=root,
        case_set_path=case_path,
        truth_path=truth_path,
    )
    if regenerated_utility != utility:
        raise ValueError("v2.2.3 frozen utility audit does not reproduce")

    resource_silent = 0
    for spec in case_set.cases:
        truth = truth_by_id[spec.case_id]
        if truth.expected_mechanism not in {"CPU_SATURATION", "MEMORY_LEAK"}:
            continue
        case = materialize_practical_case_v22(spec=spec, repository_root=root)
        outcomes, _, _, _ = _bootstrap(
            case=case,
            topology=StaticTopologyV22.build(
                services=case.candidate_services,
                edges=case.topology_edges,
            ),
            run_id="0" * 32,
        )
        memory, _ = build_memory_views_v22(
            outcomes=outcomes,
            baseline=_baseline(case),
            observed_at=case.capture.captured_at,
            top_k=64,
        )
        accepted = evaluate_no_incident_v22(
            memory=memory,
            candidate_services=case.candidate_services,
        ).accepted or evaluate_replay_no_incident_coverage_v222(
            memory=memory,
            candidate_services=case.candidate_services,
        )
        resource_silent += int(accepted)
    if resource_silent != 4:
        raise ValueError("v2.2.3 resource-silent bootstrap property differs")

    final_json = root / FINAL_JSON
    final_markdown = root / FINAL_MARKDOWN
    if final_json.exists() != final_markdown.exists():
        raise ValueError("v2.2.3 final output pair is incomplete")
    if require_pre_execution and final_json.exists():
        raise ValueError("v2.2.3 final study was already executed")
    represented_runs = 0
    if final_json.exists():
        artifact = AdmissionDispatchStudyArtifactV223.model_validate_json(
            final_json.read_bytes()
        )
        campaign = artifact.campaign
        represented_runs = len(campaign.runs)
        if (
            artifact.phase != "EVALUATION"
            or artifact.execution_count != 1
            or artifact.development_iteration is not None
            or artifact.provider_model != manifest.get("provider_model")
            or artifact.prompt_sha256 != manifest.get("prompt_sha256")
            or artifact.case_set_sha256 != _sha256(case_path)
            or artifact.truth_set_sha256 != _sha256(truth_path)
            or artifact.predicate_yield_prior_sha256
            != cast(Mapping[str, object], manifest["predicate_yield_prior"]).get(
                "sha256"
            )
            or artifact.manifest_sha256 != _sha256(manifest_path)
            or represented_runs != 64
            or campaign.cases_materialized != 16
            or len(campaign.schedule) != 64
            or campaign.combinations_per_case != 4
            or campaign.truth_load_count != 1
            or not campaign.same_case_bytes_all_combinations
            or not campaign.truth_loaded_after_all_four_runs_per_case
            or campaign.truths != truth_set.truths
            or campaign.uncaught_exceptions != 0
            or campaign.agent_writes != 0
            or artifact.uncaught_exceptions != 0
            or artifact.agent_writes != 0
        ):
            raise ValueError("v2.2.3 final execution artifact differs")

        case_hashes = {
            spec.case_id: semantic_sha256_v22(
                materialize_practical_case_v22(
                    spec=spec,
                    repository_root=root,
                ).model_dump(mode="json")
            )
            for spec in case_set.cases
        }
        if any(
            run.case_bytes_sha256 != case_hashes.get(run.case_id)
            for run in campaign.runs
        ):
            raise ValueError("v2.2.3 final run case bytes differ")
        if any(
            len(
                {
                    run.case_bytes_sha256
                    for run in campaign.runs
                    if run.case_id == spec.case_id
                }
            )
            != 1
            for spec in case_set.cases
        ):
            raise ValueError("v2.2.3 final factorial case bytes differ")

        expected_schedule = tuple(
            (spec.case_id, position, combination)
            for case_index, spec in enumerate(case_set.cases)
            for position, combination in enumerate(
                balanced_combination_order_v223(case_index), 1
            )
        )
        actual_schedule = tuple(
            (item.case_id, item.execution_position, item.combination)
            for item in campaign.schedule
        )
        if actual_schedule != expected_schedule or any(
            (scheduled.case_id, scheduled.combination)
            != (run.case_id, run.combination)
            for scheduled, run in zip(campaign.schedule, campaign.runs, strict=True)
        ):
            raise ValueError("v2.2.3 final schedule differs")

        maximum_repairs = cast(int, manifest["maximum_protocol_repairs_per_case"])
        for run in campaign.runs:
            if (
                run.dispatch_mode is not run.combination.dispatch_mode
                or run.closure_mode is not run.combination.closure_mode
                or run.protocol_repairs > maximum_repairs
                # The frozen study observed no retries. Requiring zero avoids
                # pretending the aggregate run counter can prove a per-request
                # retry bound when no per-request retry ledger is persisted.
                or run.transport_retry_count != 0
                or run.uncaught_exceptions != 0
                or run.agent_writes != 0
            ):
                raise ValueError("v2.2.3 final run invariant differs")
            automatic = run.combination.dispatch_mode.value == "RUNTIME_TOP1"
            events = run.adaptive_read_events
            if automatic:
                valid_dispatch = (
                    run.model_action_selections == 0
                    and run.automatic_top1_dispatches == len(events)
                    and all(
                        event.automatic_dispatch
                        and event.rank_at_dispatch == 1
                        and bool(event.ranking_action_ids_at_dispatch)
                        and event.action_id == event.ranking_action_ids_at_dispatch[0]
                        for event in events
                    )
                )
            else:
                valid_dispatch = (
                    run.automatic_top1_dispatches == 0
                    and run.model_action_selections == len(events)
                    and not any(event.automatic_dispatch for event in events)
                )
            if not valid_dispatch:
                raise ValueError("v2.2.3 final dispatch provenance differs")

        rescored = score_admission_dispatch_study_v223(
            runs=campaign.runs,
            truths=campaign.truths,
            utility_audit=utility,
            include_development_gate=False,
            include_interpretation=True,
        )
        if rescored != artifact.scores or rescored.interpretation is None:
            raise ValueError("v2.2.3 final scores do not reproduce")
        terminal = rescored.interpretation.measured_result_terminal
        if terminal not in final_markdown.read_text(encoding="utf-8"):
            raise ValueError("v2.2.3 measured terminal is absent from the report")

    return {
        "status": "DTA_V22_3_EVALUATION_FREEZE_VERIFIED",
        "cases": 16,
        "feasible_incidents": feasible,
        "action_ambiguity_incidents": ambiguous,
        "resource_silent_incidents": resource_silent,
        "final_runs_represented": represented_runs,
        "pre_execution": not final_json.exists(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify DTA v2.2.3 evaluation freeze")
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--pre-execution", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    print(
        json.dumps(
            verify_evaluation_freeze_v223(
                repository_root=args.repository_root,
                manifest_path=args.manifest,
                require_pre_execution=args.pre_execution,
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
