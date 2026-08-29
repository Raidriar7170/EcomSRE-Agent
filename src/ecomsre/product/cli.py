"""Repository Product command line surface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecomsre.product.cli")
    product = parser.add_subparsers(dest="product_version", required=True)
    v022 = product.add_parser("product-v022")
    commands = v022.add_subparsers(dest="command", required=True)
    commands.add_parser("history-verify")
    probe = commands.add_parser("opensearch-probe")
    probe.add_argument(
        "--config",
        type=Path,
        default=Path("config/product-v022/opensearch-probe/profile.json"),
    )
    probe.add_argument("--execute-live", action="store_true")
    v0221 = product.add_parser("product-v0221")
    commands_v0221 = v0221.add_subparsers(dest="command", required=True)
    probe_v0221 = commands_v0221.add_parser("opensearch-probe")
    probe_v0221.add_argument(
        "--config",
        type=Path,
        default=Path("config/product-v0221/opensearch-probe/profile.json"),
    )
    probe_v0221.add_argument("--execute-live", action="store_true")
    report_v0221 = commands_v0221.add_parser("opensearch-probe-report")
    report_v0221.add_argument("--session", required=True)
    v0222 = product.add_parser("product-v0222")
    commands_v0222 = v0222.add_subparsers(dest="command", required=True)
    candidates_v0222 = commands_v0222.add_parser("profile-candidates")
    candidates_v0222.add_argument("--candidate-set", type=Path, required=True)
    select_v0222 = commands_v0222.add_parser("profile-select")
    select_v0222.add_argument("--candidate-set", type=Path, required=True)
    select_v0222.add_argument("--candidate", required=True)
    select_v0222.add_argument("--reviewer", required=True)
    select_v0222.add_argument("--note", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    root = Path.cwd().resolve(strict=True)
    if arguments.product_version == "product-v022" and arguments.command == "history-verify":
        from scripts.ci.verify_product_v022_history import (
            verify_product_v022_history,
        )

        result = verify_product_v022_history(root)
    elif arguments.product_version == "product-v022" and arguments.command == "opensearch-probe":
        from scripts.product_v022.run_opensearch_schema_probe import main as probe_main

        forwarded = ["--project-root", str(root), "--config", str(arguments.config)]
        if arguments.execute_live:
            forwarded.append("--execute-live")
        return probe_main(forwarded)
    elif arguments.product_version == "product-v0221" and arguments.command == "opensearch-probe":
        from scripts.product_v0221.run_opensearch_schema_probe import (
            main as probe_v0221_main,
        )

        forwarded = ["--project-root", str(root), "--config", str(arguments.config)]
        if arguments.execute_live:
            forwarded.append("--execute-live")
        return probe_v0221_main(forwarded)
    elif arguments.product_version == "product-v0221" and arguments.command == "opensearch-probe-report":
        if arguments.session != "product-v0221-schema-discovery-1":
            raise ValueError("Product v0.2.2.1 schema session differs")
        report_path = root / "docs/analysis/product-v0221-schema-session.json"
        if not report_path.exists():
            raise ValueError("Product v0.2.2.1 schema report is unavailable")
        result = json.loads(report_path.read_text(encoding="utf-8"))
    elif arguments.product_version == "product-v0222":
        from ecomsre.product.connectors.opensearch_candidates_v0222 import (
            OpenSearchOperatorDecisionLedgerV0222,
            OpenSearchOperatorProfileDecisionV0222,
            OpenSearchProfileCandidateSetV0222,
            build_operator_profile_decision_v0222,
        )

        candidate_set = OpenSearchProfileCandidateSetV0222.model_validate_json(
            arguments.candidate_set.read_text(encoding="utf-8")
        )
        if arguments.command == "profile-candidates":
            result = candidate_set.model_dump(mode="json")
        elif arguments.command == "profile-select":
            output = root / "config/product-v0222/opensearch/operator-decision.json"
            previous: tuple[OpenSearchOperatorProfileDecisionV0222, ...] = ()
            if output.exists():
                retained = OpenSearchOperatorDecisionLedgerV0222.model_validate_json(
                    output.read_text(encoding="utf-8")
                )
                previous = retained.decisions
            decision = build_operator_profile_decision_v0222(
                candidate_set=candidate_set,
                selected_candidate_alias=arguments.candidate,
                reviewer=arguments.reviewer,
                review_note=arguments.note,
                previous_decisions=previous,
            )
            ledger = OpenSearchOperatorDecisionLedgerV0222.build(
                candidate_set_sha256=candidate_set.candidate_set_sha256,
                decisions=(*previous, decision),
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.parent / ".operator-decision.product-v0222.tmp"
            temporary.write_text(
                json.dumps(
                    ledger.model_dump(mode="json"),
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            temporary.replace(output)
            result = decision.model_dump(mode="json")
        else:
            raise ValueError("Product v0.2.2.2 command is unsupported")
    else:
        raise ValueError("Product command is unsupported")
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("main",)
