"""Verify the frozen v2.2.3 evaluation portfolio and single-run boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Mapping, Sequence, cast

from ecomsre.dta_v2.v22.action_catalog import StaticTopologyV22
from ecomsre.dta_v2.v22.effective_policy_v222 import (
    evaluate_replay_no_incident_coverage_v222,
)
from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import (
    EvidenceUtilityAuditReportV222,
    ShortestAdmissiblePathV222,
)
from ecomsre.dta_v2.v22.memory import build_memory_views_v22
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.practical_dataset import (
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.practical_runner import _baseline, _bootstrap
from ecomsre.dta_v2.v22.predicates import evaluate_no_incident_v22


DEFAULT_MANIFEST = Path("config/dta-v22-3/evaluation/manifest.json")
FINAL_JSON = Path("docs/results/dta-v22-3-admission-dispatch-evaluation.json")
FINAL_MARKDOWN = Path("docs/results/dta-v22-3-admission-dispatch-evaluation.md")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bound_path(root: Path, item: object, name: str) -> Path:
    if not isinstance(item, Mapping):
        raise ValueError(f"v2.2.3 manifest lacks {name}")
    path = root / str(item.get("path"))
    if not path.is_file() or item.get("sha256") != _sha256(path):
        raise ValueError(f"v2.2.3 frozen {name} differs")
    return path


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
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", implementation_commit, "HEAD"],
        cwd=root,
        check=False,
    )
    if ancestry.returncode != 0:
        raise ValueError("v2.2.3 frozen implementation commit is not an ancestor")

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
        final = cast(dict[str, object], json.loads(final_json.read_bytes()))
        campaign = cast(dict[str, object], final.get("campaign"))
        represented_runs = len(cast(list[object], campaign.get("runs")))
        if final.get("execution_count") != 1 or represented_runs != 64:
            raise ValueError("v2.2.3 final execution artifact differs")

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
