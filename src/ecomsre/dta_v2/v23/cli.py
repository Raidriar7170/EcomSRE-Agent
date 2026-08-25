"""Offline CLI for the DTA v2.3 discovery lane."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Sequence

from ecomsre.dta_v2.v22.predicates import MechanismV22
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
    raise AssertionError("unreachable v2.3 CLI command")


if __name__ == "__main__":
    raise SystemExit(main())
