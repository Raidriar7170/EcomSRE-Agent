"""Offline CLI for the DTA v2.3 discovery lane."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Sequence

from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre.dta_v2.v23.discovery_runtime import (
    run_cpu_development_demo_v23,
    run_development_leave_one_out_v23,
)
from ecomsre.dta_v2.v23.contracts import ProvisionalIncidentReportV23
from ecomsre.dta_v2.v23.review_registry import (
    HumanReviewDecisionV23,
    LocalReviewStoreV23,
    ReviewQueueItemV23,
    match_shadow_queue_item_v23,
    match_shadow_report_v23,
)
from ecomsre.dta_v2.v23.review_registry_v231 import (
    LocalReviewStoreV231,
    render_review_display_v231,
)
from ecomsre.dta_v2.v23.core_ontology_snapshot_v234 import (
    build_core_ontology_schema_snapshot_v234,
)
from ecomsre.dta_v2.v23.ontology_expansion_v234 import (
    LocalOntologyExpansionStoreV234,
)
from ecomsre.dta_v2.v23.evaluation import (
    OpenAICompatibleDiscoveryTransportV23,
    _build_common_context_v23,
    render_evaluation_markdown_v23,
    run_fixed_evaluation_once_v23,
)
from ecomsre.dta_v2.v23.conflict_model_v231 import audit_historical_conflicts_v231
from ecomsre.dta_v2.v23.evaluation_v231 import (
    EvaluationCaseSpecV231,
    EvaluationArmRunV231,
    EvaluationOntologyViewSpecV231,
    EvaluationPolicyV231,
    OpenAICompatibleDiscoveryTransportV231,
    load_evaluation_case_set_v231,
    load_evaluation_views_v231,
    run_evaluation_policy_v231,
    run_fixed_evaluation_once_v231,
    materialize_evaluation_case_v231,
)
from ecomsre.dta_v2.v23.domain_audit_v233 import project_development_case_v233
from ecomsre.dta_v2.v23.discovery_provider_v233 import (
    OpenAICompatibleDiscoveryTransportV233,
)
from ecomsre.dta_v2.v23.evaluation_data_v232 import (
    AdmissionStratumV232,
    load_evaluation_cases_v232,
    load_evaluation_views_v232,
)
from ecomsre.dta_v2.v23.witness_audit_v233 import audit_case_witness_v233
from ecomsre.dta_v2.v23.evaluation_data_v233 import (
    load_evaluation_cases_v233,
    load_evaluation_views_v233,
)
from ecomsre.dta_v2.v23.evaluation_study_v233 import (
    EvaluationCaseComparisonV233,
    run_fixed_evaluation_once_v233,
)
from ecomsre.dta_v2.v23.evaluation_v233 import (
    run_combined_arm_v233,
    run_domain_bound_arm_v233,
)


DEFAULT_LOCAL_ROOT_V23 = Path(".local/dta-v23")
DEFAULT_LOCAL_ROOT_V234 = Path(".local/dta-v234")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecomsre-dta-v23")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo = subparsers.add_parser("demo")
    demo.add_argument("demo_name", choices=("hidden-cpu",))
    demo.add_argument("--repository-root", type=Path, default=Path.cwd())
    diagnose = subparsers.add_parser("diagnose")
    diagnose.add_argument("--case", required=True)
    diagnose.add_argument(
        "--hide-mechanism",
        choices=tuple(item.value for item in MechanismV22 if item not in {
            MechanismV22.NO_INCIDENT,
            MechanismV22.UNKNOWN,
        }),
    )
    diagnose.add_argument("--repository-root", type=Path, default=Path.cwd())
    diagnose.add_argument("--conflict-policy", choices=("strict", "competing"))
    diagnose.add_argument(
        "--policy",
        choices=("domain-bound", "domain-bound-witness-guard"),
    )
    domain_project = subparsers.add_parser("domain-project")
    domain_project.add_argument("--case", required=True)
    domain_project.add_argument("--repository-root", type=Path, default=Path.cwd())
    conflict_witness = subparsers.add_parser("conflict-witness")
    conflict_witness.add_argument("--case", required=True)
    conflict_witness.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
    )
    conflict_audit = subparsers.add_parser("conflict-audit")
    conflict_audit.add_argument("--repository-root", type=Path, default=Path.cwd())
    conflict_audit.add_argument(
        "--split",
        choices=("v23-fixed",),
        default="v23-fixed",
    )
    conflict_audit.add_argument(
        "--result",
        type=Path,
        default=Path("docs/results/dta-v23-open-world-evaluation.json"),
    )
    conflict = subparsers.add_parser("conflict")
    conflict_commands = conflict.add_subparsers(
        dest="conflict_command",
        required=True,
    )
    conflict_show = conflict_commands.add_parser("show")
    conflict_show.add_argument("--case", required=True)
    conflict_show.add_argument("--repository-root", type=Path, default=Path.cwd())
    review = subparsers.add_parser("review")
    review_commands = review.add_subparsers(dest="review_command", required=True)
    review_list = review_commands.add_parser("list")
    review_list.add_argument("--local-root", type=Path, default=DEFAULT_LOCAL_ROOT_V23)
    review_show = review_commands.add_parser("show")
    review_show.add_argument("report_id")
    review_show.add_argument("--local-root", type=Path, default=DEFAULT_LOCAL_ROOT_V23)
    review_decide = review_commands.add_parser("decide")
    review_decide.add_argument("report_id")
    review_decide.add_argument(
        "--decision",
        required=True,
        choices=tuple(item.value for item in HumanReviewDecisionV23),
    )
    review_decide.add_argument("--reviewer", required=True)
    review_decide.add_argument("--label")
    review_decide.add_argument("--merge-target")
    review_decide.add_argument("--request-observation", action="append", default=[])
    review_decide.add_argument("--note", required=True)
    review_decide.add_argument("--local-root", type=Path, default=DEFAULT_LOCAL_ROOT_V23)
    shadow = subparsers.add_parser("shadow")
    shadow_commands = shadow.add_subparsers(dest="shadow_command", required=True)
    shadow_match = shadow_commands.add_parser("match")
    shadow_match.add_argument("--report", type=Path, required=True)
    shadow_match.add_argument("--local-root", type=Path, default=DEFAULT_LOCAL_ROOT_V23)
    ontology = subparsers.add_parser("ontology")
    ontology_commands = ontology.add_subparsers(
        dest="ontology_command",
        required=True,
    )
    ontology_list = ontology_commands.add_parser("list")
    ontology_list.add_argument(
        "--local-root",
        type=Path,
        default=DEFAULT_LOCAL_ROOT_V234,
    )
    ontology_authorize = ontology_commands.add_parser("authorize-draft")
    ontology_authorize.add_argument("shadow_fault_id")
    ontology_authorize.add_argument("--reviewer", required=True)
    ontology_authorize.add_argument("--note", required=True)
    ontology_authorize.add_argument(
        "--local-root",
        type=Path,
        default=DEFAULT_LOCAL_ROOT_V234,
    )
    ontology_commands.add_parser("snapshot")
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument(
        "--split",
        choices=("development", "fixed", "v231-fixed", "v233-fixed"),
        required=True,
    )
    evaluate.add_argument("--repository-root", type=Path, default=Path.cwd())
    evaluate.add_argument("--provider-env", type=Path)
    evaluate.add_argument(
        "--output-json",
        type=Path,
        default=Path("docs/results/dta-v23-open-world-evaluation.json"),
    )
    evaluate.add_argument(
        "--output-markdown",
        type=Path,
        default=Path("docs/results/dta-v23-open-world-evaluation.md"),
    )
    evaluate.add_argument("--minimum-request-interval", type=float, default=6.0)
    evaluate.add_argument("--timeout", type=float, default=120.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "demo" and args.demo_name == "hidden-cpu":
        demo_result = run_cpu_development_demo_v23(
            repository_root=args.repository_root.resolve(),
            hide_cpu=True,
        )
        print(demo_result.model_dump_json(indent=2))
        return 0
    if args.command == "domain-project":
        repository_root = args.repository_root.resolve()
        case_path = Path(args.case)
        if case_path.is_file():
            domain_spec = EvaluationCaseSpecV231.model_validate_json(
                case_path.read_bytes()
            )
            domain_view = EvaluationOntologyViewSpecV231(
                case_id=domain_spec.case_id,
                hidden_mechanism=None,
            )
        else:
            domain_cases = load_evaluation_cases_v232(
                repository_root / "config/dta-v232/evaluation/cases.json"
            )
            domain_views = load_evaluation_views_v232(
                repository_root / "config/dta-v232/evaluation/ontology-views.json"
            )
            domain_spec = domain_cases.require(args.case)
            domain_view = domain_views.require(domain_spec.case_id)
        projection, _memory, _reads = project_development_case_v233(
            repository_root=repository_root,
            spec=domain_spec,
            view_spec=domain_view,
        )
        print(projection.model_dump_json(indent=2))
        return 0
    if args.command == "conflict-witness":
        witness_repository_root = args.repository_root.resolve()
        witness_case_path = Path(args.case)
        if witness_case_path.is_file():
            witness_spec = EvaluationCaseSpecV231.model_validate_json(
                witness_case_path.read_bytes()
            )
            witness_view = EvaluationOntologyViewSpecV231(
                case_id=witness_spec.case_id,
                hidden_mechanism=None,
            )
            witness_stratum = AdmissionStratumV232.NOVEL_HIDDEN
        else:
            witness_cases = load_evaluation_cases_v232(
                witness_repository_root / "config/dta-v232/evaluation/cases.json"
            )
            witness_views = load_evaluation_views_v232(
                witness_repository_root
                / "config/dta-v232/evaluation/ontology-views.json"
            )
            witness_spec = witness_cases.require(args.case)
            witness_view = witness_views.require(witness_spec.case_id)
            witness_stratum = AdmissionStratumV232.INSUFFICIENT_IRRECONCILABLE
        witness_entry = audit_case_witness_v233(
            repository_root=witness_repository_root,
            spec=witness_spec,
            view_spec=witness_view,
            stratum=witness_stratum,
        )
        print(witness_entry.model_dump_json(indent=2))
        return 0
    if args.command == "diagnose":
        if args.policy is not None:
            repository_root = args.repository_root.resolve()
            cases_v233 = load_evaluation_cases_v233(
                repository_root / "config/dta-v233/evaluation/cases.json"
            )
            views_v233 = load_evaluation_views_v233(
                repository_root / "config/dta-v233/evaluation/ontology-views.json"
            )
            case_path = Path(args.case)
            if case_path.is_file():
                policy_spec = EvaluationCaseSpecV231.model_validate_json(
                    case_path.read_bytes()
                )
                policy_view = EvaluationOntologyViewSpecV231(
                    case_id=policy_spec.case_id,
                    hidden_mechanism=None,
                )
            else:
                policy_spec = cases_v233.require(args.case)
                policy_view = views_v233.require(policy_spec.case_id)
            context = _build_common_context_v23(
                case=materialize_evaluation_case_v231(
                    repository_root=repository_root,
                    spec=policy_spec,
                ),
                hidden_mechanism=policy_view.hidden_mechanism,
            )
            selected_v233 = (
                run_domain_bound_arm_v233(
                    context=context,
                    provider_transport=None,
                )
                if args.policy == "domain-bound"
                else run_combined_arm_v233(
                    repository_root=repository_root,
                    context=context,
                    provider_transport=None,
                )
            )
            print(selected_v233.model_dump_json(indent=2))
            return 0
        if args.conflict_policy is not None:
            repository_root = args.repository_root.resolve()
            cases = load_evaluation_case_set_v231(
                repository_root / "config/dta-v231/evaluation/cases.json"
            )
            case_path = Path(args.case)
            matches = tuple(
                item for item in cases.cases if item.case_id == args.case
            )
            spec: EvaluationCaseSpecV231 | None = (
                EvaluationCaseSpecV231.model_validate_json(case_path.read_bytes())
                if case_path.is_file()
                else matches[0]
                if matches
                else None
            )
            if spec is None:
                raise ValueError("policy diagnosis requires a fixed vx case ID or case JSON")
            views = load_evaluation_views_v231(
                repository_root / "config/dta-v231/evaluation/ontology-views.json"
            )
            selected = run_evaluation_policy_v231(
                repository_root=repository_root,
                spec=spec,
                view_spec=views.require(spec.case_id),
                policy=(
                    EvaluationPolicyV231.V23_STRICT_CONFLICT_GATE
                    if args.conflict_policy == "strict"
                    else EvaluationPolicyV231.V231_CONFLICT_AWARE_GATE
                ),
                provider_transport=None,
            )
            print(selected.model_dump_json(indent=2))
            return 0
        case_value = Path(args.case)
        case_id = case_value.stem if case_value.suffix == ".json" else args.case
        diagnosis_result = run_development_leave_one_out_v23(
            repository_root=args.repository_root.resolve(),
            case_id=case_id,
            hidden_mechanism=(
                MechanismV22(args.hide_mechanism)
                if args.hide_mechanism is not None
                else None
            ),
        )
        print(diagnosis_result.model_dump_json(indent=2))
        return 0
    if args.command == "conflict-audit":
        repository_root = args.repository_root.resolve()
        result = args.result if args.result.is_absolute() else repository_root / args.result
        print(audit_historical_conflicts_v231(result).model_dump_json(indent=2))
        return 0
    if args.command == "conflict" and args.conflict_command == "show":
        repository_root = args.repository_root.resolve()
        cases = load_evaluation_case_set_v231(
            repository_root / "config/dta-v231/evaluation/cases.json"
        )
        case_path = Path(args.case)
        case_matches = tuple(
            item for item in cases.cases if item.case_id == args.case
        )
        spec = (
            EvaluationCaseSpecV231.model_validate_json(case_path.read_bytes())
            if case_path.is_file()
            else case_matches[0]
            if case_matches
            else None
        )
        if spec is None:
            raise ValueError("v2.3.1 conflict case is absent")
        views = load_evaluation_views_v231(
            repository_root / "config/dta-v231/evaluation/ontology-views.json"
        )
        treatment = run_evaluation_policy_v231(
            repository_root=repository_root,
            spec=spec,
            view_spec=views.require(spec.case_id),
            policy=EvaluationPolicyV231.V231_CONFLICT_AWARE_GATE,
            provider_transport=None,
        )
        if not isinstance(treatment, EvaluationArmRunV231):
            raise TypeError("v2.3.1 conflict display requires the treatment policy")
        print(treatment.conflict_assessment.model_dump_json(indent=2))
        return 0
    if args.command == "review":
        store = LocalReviewStoreV23(args.local_root)
        store_v231 = LocalReviewStoreV231(args.local_root)
        if args.review_command == "list":
            print(
                json.dumps(
                    tuple(sorted((*store.list_report_ids(), *store_v231.list_report_ids()))),
                    indent=2,
                )
            )
            return 0
        if args.review_command == "show":
            if args.report_id.startswith("report-v231-"):
                print(
                    json.dumps(
                        render_review_display_v231(store_v231.load_item(args.report_id)),
                        indent=2,
                    )
                )
            else:
                print(store.load_item(args.report_id).model_dump_json(indent=2))
            return 0
        if args.review_command == "decide":
            decide_store = (
                store_v231.decide
                if args.report_id.startswith("report-v231-")
                else store.decide
            )
            result = decide_store(
                report_id=args.report_id,
                decision=HumanReviewDecisionV23(args.decision),
                reviewer=args.reviewer,
                review_note=args.note,
                canonical_label=args.label,
                merge_target=args.merge_target,
                requested_observations=tuple(args.request_observation),
                reviewed_at=datetime.now(timezone.utc),
            )
            print(result.model_dump_json(indent=2))
            return 0
    if args.command == "shadow" and args.shadow_command == "match":
        raw = args.report.read_bytes()
        store = LocalReviewStoreV23(args.local_root)
        try:
            item = ReviewQueueItemV23.model_validate_json(raw)
        except ValueError:
            report = ProvisionalIncidentReportV23.model_validate_json(raw)
            shadow_matches = match_shadow_report_v23(
                report=report,
                registry=store.load_registry(),
            )
        else:
            shadow_matches = match_shadow_queue_item_v23(
                item=item,
                registry=store.load_registry(),
            )
        print(
            json.dumps(
                [item.model_dump(mode="json") for item in shadow_matches],
                indent=2,
            )
        )
        return 0
    if args.command == "ontology":
        if args.ontology_command == "list":
            store_v234 = LocalOntologyExpansionStoreV234(args.local_root)
            print(
                json.dumps(
                    [
                        item.model_dump(mode="json")
                        for item in store_v234.list_shadow_faults()
                    ],
                    indent=2,
                )
            )
            return 0
        if args.ontology_command == "authorize-draft":
            authorization = LocalOntologyExpansionStoreV234(
                args.local_root
            ).authorize_draft_generation(
                shadow_fault_id=args.shadow_fault_id,
                reviewer=args.reviewer,
                authorization_note=args.note,
                authorized_at=datetime.now(timezone.utc),
            )
            print(authorization.model_dump_json(indent=2))
            return 0
        if args.ontology_command == "snapshot":
            print(build_core_ontology_schema_snapshot_v234().model_dump_json(indent=2))
            return 0
    if args.command == "evaluate":
        repository_root = args.repository_root.resolve()
        if args.split == "development":
            development_cases = (
                ("d01", MechanismV22.CONFIGURATION_ERROR),
                ("d02", MechanismV22.SERVICE_UNAVAILABLE),
                ("d03", MechanismV22.MEMORY_LEAK),
                ("d04", MechanismV22.CPU_SATURATION),
                ("d06", MechanismV22.DEPENDENCY_LATENCY),
            )
            results = tuple(
                run_development_leave_one_out_v23(
                    repository_root=repository_root,
                    case_id=case_id,
                    hidden_mechanism=hidden,
                )
                for case_id, hidden in development_cases
            )
            print(
                json.dumps(
                    [
                        {
                            "case_id": item.case_id,
                            "hidden_mechanism": item.hidden_mechanism.value
                            if item.hidden_mechanism is not None
                            else None,
                            "discovery_reads": item.discovery_reads_used,
                            "final_disposition": item.final_disposition.value,
                        }
                        for item in results
                    ],
                    indent=2,
                )
            )
            return 0
        if args.provider_env is None:
            raise ValueError("fixed evaluation requires --provider-env")
        output_json = (
            args.output_json
            if args.output_json.is_absolute()
            else repository_root / args.output_json
        )
        output_markdown = (
            args.output_markdown
            if args.output_markdown.is_absolute()
            else repository_root / args.output_markdown
        )
        if args.split == "v231-fixed":
            if args.output_json == Path("docs/results/dta-v23-open-world-evaluation.json"):
                output_json = repository_root / "docs/results/dta-v231-conflict-aware-evaluation.json"
            if args.output_markdown == Path("docs/results/dta-v23-open-world-evaluation.md"):
                output_markdown = repository_root / "docs/results/dta-v231-conflict-aware-evaluation.md"
        if args.split == "v233-fixed":
            if args.output_json == Path("docs/results/dta-v23-open-world-evaluation.json"):
                output_json = repository_root / "docs/results/dta-v233-domain-guard-evaluation.json"
            if args.output_markdown == Path("docs/results/dta-v23-open-world-evaluation.md"):
                output_markdown = repository_root / "docs/results/dta-v233-domain-guard-evaluation.md"
        if output_markdown.exists():
            raise FileExistsError(
                f"write-once evaluation markdown exists: {output_markdown}"
            )
        values = load_private_provider_env(args.provider_env)
        config = OpenAICompatibleConfig(
            base_url=values["ECOMSRE_LLM_BASE_URL"],
            api_key=values["ECOMSRE_LLM_API_KEY"],
            model=values["ECOMSRE_LLM_MODEL"],
        )
        provider = (
            OpenAICompatibleDiscoveryTransportV231(
                config=config,
                minimum_request_interval_seconds=args.minimum_request_interval,
                timeout_seconds=args.timeout,
            )
            if args.split == "v231-fixed"
            else OpenAICompatibleDiscoveryTransportV233(
                config=config,
                minimum_request_interval_seconds=args.minimum_request_interval,
                timeout_seconds=args.timeout,
            )
            if args.split == "v233-fixed"
            else OpenAICompatibleDiscoveryTransportV23(
                config=config,
                minimum_request_interval_seconds=args.minimum_request_interval,
                timeout_seconds=args.timeout,
            )
        )

        if args.split == "v231-fixed":
            def observe_v231(pair: object) -> None:
                from ecomsre.dta_v2.v23.evaluation_v231 import EvaluationCasePairV231

                if not isinstance(pair, EvaluationCasePairV231):
                    raise TypeError("v2.3.1 observer received an invalid pair")
                print(
                    json.dumps(
                        {
                            "case_id": pair.case_id,
                            "strict": pair.strict.final_disposition,
                            "treatment": pair.treatment.final_disposition,
                            "strict_reads": pair.strict.discovery_read_count,
                            "treatment_reads": pair.treatment.discovery_read_count,
                            "provider_calls": (
                                pair.strict.provider_cost.provider_calls
                                + pair.treatment.provider_cost.provider_calls
                            ),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )

            if not isinstance(provider, OpenAICompatibleDiscoveryTransportV231):
                raise TypeError("v2.3.1 fixed evaluation requires its transport")
            artifact_v231 = run_fixed_evaluation_once_v231(
                repository_root=repository_root,
                cases_path=repository_root / "config/dta-v231/evaluation/cases.json",
                truth_path=repository_root / "config/dta-v231/evaluation/truth.json",
                ontology_views_path=repository_root
                / "config/dta-v231/evaluation/ontology-views.json",
                manifest_path=repository_root
                / "config/dta-v231/evaluation/manifest.json",
                output_path=output_json,
                output_markdown_path=output_markdown,
                provider_transport=provider,
                observer=observe_v231,
            )
            print(
                json.dumps(
                    {
                        "execution_count": artifact_v231.execution_count,
                        "case_count": artifact_v231.case_count,
                        "run_count": artifact_v231.run_count,
                        "measured_result_terminal": (
                            artifact_v231.measured_result_terminal.value
                        ),
                        "artifact_sha256": artifact_v231.artifact_sha256,
                    },
                    sort_keys=True,
                )
            )
            return 0

        if args.split == "v233-fixed":
            if not isinstance(provider, OpenAICompatibleDiscoveryTransportV233):
                raise TypeError("v2.3.3 fixed evaluation requires its transport")

            def observe_v233(comparison: EvaluationCaseComparisonV233) -> None:
                print(
                    json.dumps(
                        {
                            "case_id": comparison.case_id,
                            "arm_order": [item.value for item in comparison.arm_order],
                            "dispositions": {
                                item.policy.value: item.final_disposition
                                for item in comparison.runs
                            },
                            "provider_calls": sum(
                                item.provider_cost.provider_calls
                                for item in comparison.runs
                            ),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )

            artifact_v233 = run_fixed_evaluation_once_v233(
                repository_root=repository_root,
                evaluation_root=repository_root / "config/dta-v233/evaluation",
                manifest_path=repository_root
                / "config/dta-v233/evaluation/manifest.json",
                independent_review_path=repository_root
                / "docs/external-reviews/dta-v233-pre-execution-review.md",
                provider_smoke_path=repository_root
                / "docs/analysis/dta-v233-provider-smoke.json",
                output_path=output_json,
                output_markdown_path=output_markdown,
                provider_transport=provider,
                observer=observe_v233,
            )
            print(
                json.dumps(
                    {
                        "execution_count": artifact_v233.execution_count,
                        "case_count": artifact_v233.case_count,
                        "run_count": artifact_v233.run_count,
                        "measured_result_terminal": (
                            artifact_v233.measured_result_terminal.value
                        ),
                        "artifact_sha256": artifact_v233.artifact_sha256,
                    },
                    sort_keys=True,
                )
            )
            return 0

        def observe(pair: object) -> None:
            from ecomsre.dta_v2.v23.evaluation import EvaluationCasePairV23

            if not isinstance(pair, EvaluationCasePairV23):
                raise TypeError("fixed evaluation observer received an invalid pair")
            print(
                json.dumps(
                    {
                        "case_id": pair.case_id,
                        "closed": pair.closed_world.final_disposition,
                        "open": pair.open_world.final_disposition,
                        "discovery_reads": pair.open_world.discovery_read_count,
                        "provider_calls": pair.open_world.provider_cost.provider_calls,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

        artifact = run_fixed_evaluation_once_v23(
            repository_root=repository_root,
            cases_path=repository_root / "config/dta-v23/evaluation/cases.json",
            truth_path=repository_root / "config/dta-v23/evaluation/truth.json",
            ontology_views_path=repository_root
            / "config/dta-v23/evaluation/ontology-views.json",
            manifest_path=repository_root / "config/dta-v23/evaluation/manifest.json",
            output_path=output_json,
            provider_transport=provider,
            observer=observe,
        )
        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        with output_markdown.open("x", encoding="utf-8") as handle:
            handle.write(render_evaluation_markdown_v23(artifact))
        print(
            json.dumps(
                {
                    "execution_count": artifact.execution_count,
                    "case_count": artifact.case_count,
                    "run_count": artifact.run_count,
                    "measured_result_terminal": artifact.measured_result_terminal.value,
                    "artifact_sha256": artifact.artifact_sha256,
                },
                sort_keys=True,
            )
        )
        return 0
    raise AssertionError("unreachable v2.3 CLI command")


if __name__ == "__main__":
    raise SystemExit(main())
