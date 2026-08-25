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
from ecomsre.dta_v2.v23.evaluation import (
    OpenAICompatibleDiscoveryTransportV23,
    render_evaluation_markdown_v23,
    run_fixed_evaluation_once_v23,
)


DEFAULT_LOCAL_ROOT_V23 = Path(".local/dta-v23")


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
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--split", choices=("development", "fixed"), required=True)
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
    if args.command == "diagnose":
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
    if args.command == "review":
        store = LocalReviewStoreV23(args.local_root)
        if args.review_command == "list":
            print(json.dumps(store.list_report_ids(), indent=2))
            return 0
        if args.review_command == "show":
            print(store.load_item(args.report_id).model_dump_json(indent=2))
            return 0
        if args.review_command == "decide":
            result = store.decide(
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
            matches = match_shadow_report_v23(
                report=report,
                registry=store.load_registry(),
            )
        else:
            matches = match_shadow_queue_item_v23(
                item=item,
                registry=store.load_registry(),
            )
        print(json.dumps([item.model_dump(mode="json") for item in matches], indent=2))
        return 0
    if args.command == "evaluate":
        repository_root = args.repository_root.resolve()
        if args.split == "development":
            cases = (
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
                for case_id, hidden in cases
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
        provider = OpenAICompatibleDiscoveryTransportV23(
            config=config,
            minimum_request_interval_seconds=args.minimum_request_interval,
            timeout_seconds=args.timeout,
        )

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
