"""Local-only CLI for practical v2.2 smoke and replay campaigns."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from pydantic import ValidationError

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v22.practical_campaign import run_practical_campaign_v22
from ecomsre.dta_v2.v22.practical_runner import PracticalCaseRunV22
from ecomsre.dta_v2.v22.practical_smoke import run_practical_provider_smoke_v22
from ecomsre.dta_v2.v22.simple_provider import SimpleProviderV22
from ecomsre.model.gateway import OpenAICompatibleConfig


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DTA v2.2 practical replay runner")
    parser.add_argument("mode", choices=("smoke", "development", "evaluation"))
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-set", type=Path)
    parser.add_argument("--truth", type=Path)
    parser.add_argument("--minimum-request-interval", type=float, default=6.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser


def _write_once(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(
            value,
            handle,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        handle.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        values = load_private_provider_env(args.provider_env)
        config = OpenAICompatibleConfig(
            base_url=values["ECOMSRE_LLM_BASE_URL"],
            api_key=values["ECOMSRE_LLM_API_KEY"],
            model=values["ECOMSRE_LLM_MODEL"],
        )
        prompt = args.prompt_file.read_text(encoding="utf-8").strip()
        provider = SimpleProviderV22(
            config=config,
            minimum_request_interval_seconds=args.minimum_request_interval,
            timeout_seconds=args.timeout,
            debug_root=args.repository_root / ".local/dta-v22-debug",
        )
        if args.mode == "smoke":
            result = run_practical_provider_smoke_v22(
                repository_root=args.repository_root,
                provider=provider,
                system_prompt=prompt,
            )
            _write_once(args.output, result.model_dump(mode="json"))
            safe_summary = {
                "mode": "smoke",
                "passed": result.passed,
                "post_repair_valid_outputs": result.post_repair_valid_outputs,
                "uncaught_exceptions": result.uncaught_exceptions,
                "agent_writes": result.agent_writes,
            }
            print(json.dumps(safe_summary, sort_keys=True))
            return 0 if result.passed else 1
        if args.case_set is None or args.truth is None:
            raise ValueError("campaign mode requires --case-set and --truth")
        partial_path = args.output.with_suffix(args.output.suffix + ".partial.jsonl")
        partial_path.parent.mkdir(parents=True, exist_ok=True)
        partial_handle = partial_path.open("x", encoding="utf-8")

        def observe_run(run: PracticalCaseRunV22) -> None:
            partial_handle.write(run.model_dump_json() + "\n")
            partial_handle.flush()

        try:
            campaign = run_practical_campaign_v22(
                case_set_path=args.case_set,
                truth_path=args.truth,
                repository_root=args.repository_root,
                provider=provider,
                system_prompt=prompt,
                run_observer=observe_run,
            )
        finally:
            partial_handle.close()
        _write_once(args.output, campaign.model_dump(mode="json"))
        safe_summary = {
            "mode": args.mode,
            "cases": campaign.cases_materialized,
            "flat_completion": campaign.flat_score.run_completion_rate,
            "planner_completion": campaign.planner_score.run_completion_rate,
            "uncaught_exceptions": campaign.flat_score.uncaught_exceptions
            + campaign.planner_score.uncaught_exceptions,
            "agent_writes": campaign.agent_writes,
        }
        print(json.dumps(safe_summary, sort_keys=True))
        return 0
    except Exception as error:
        validation_locations = (
            [
                {
                    "location": ".".join(str(item) for item in detail["loc"]),
                    "type": detail["type"],
                }
                for detail in error.errors(include_input=False, include_url=False)
            ]
            if isinstance(error, ValidationError)
            else []
        )
        print(
            json.dumps(
                {
                    "mode": args.mode,
                    "status": "FAILED",
                    "safe_error_code": type(error).__name__,
                    "validation_errors": validation_locations,
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
