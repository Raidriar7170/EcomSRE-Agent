from __future__ import annotations

import argparse
import hashlib
from itertools import permutations
from pathlib import Path

from ecomsre_rcaeval.artifacts import (
    canonical_json_bytes,
    read_json_object,
    schedule_payload,
    sha256_bytes,
    sha256_file,
    sha256_tree,
    write_json_create_once,
)
from ecomsre_rcaeval.contracts import (
    Architecture,
    CommanderDecision,
    Diagnosis,
    GroundTruth,
    ScheduledRun,
    SpecialistAssessment,
)
from ecomsre_rcaeval.dataset import (
    DevCase,
    DevSystem,
    discover_dev_cases,
)
from ecomsre_rcaeval.execution import run_schedule
from ecomsre_rcaeval.freeze import current_runtime_bindings, repository_base_commit
from ecomsre_rcaeval.scoring import score_terminal_records
from scripts.rcaeval.common import CONFIG_ROOT, provider_from_lock, verify_prompt_lock


def _selected(cases: tuple[DevCase, ...], limit: int) -> tuple[DevCase, ...]:
    by_stratum: dict[tuple[str, str], DevCase] = {}
    for case in cases:
        by_stratum.setdefault((case.root_cause_service, case.fault), case)
    selected = tuple(by_stratum[key] for key in sorted(by_stratum))
    if len(selected) != 30:
        raise ValueError("development pilot requires all 30 service-fault strata")
    return selected[:limit]


def _schedule(cases: tuple[DevCase, ...], seed: int) -> tuple[ScheduledRun, ...]:
    architectures = tuple(Architecture)
    orders = tuple(permutations(architectures))
    records: list[ScheduledRun] = []
    for case in cases:
        rank = int.from_bytes(
            hashlib.sha256(f"{seed}\0{case.case_id}".encode()).digest()[:8],
            "big",
        )
        for position, architecture in enumerate(orders[rank % len(orders)], start=1):
            run_id = hashlib.sha256(
                f"dev\0{seed}\0{case.case_id}\0{architecture.value}".encode()
            ).hexdigest()[:32]
            records.append(
                ScheduledRun(
                    run_id=run_id,
                    case_id=case.case_id,
                    architecture=architecture,
                    call_position=position,
                    schedule_seed=seed,
                )
            )
    return tuple(records)


class HeuristicSmokeProvider:
    @staticmethod
    def _indicator(name: str) -> str:
        lowered = name.casefold()
        return next(
            (
                value
                for marker, value in (
                    ("disk", "diskio"),
                    ("lat", "latency"),
                    ("socket", "socket"),
                    ("mem", "mem"),
                    ("cpu", "cpu"),
                )
                if marker in lowered
            ),
            "cpu",
        )

    def specialize(self, incident, context, source):
        del incident
        prefix = {"metrics": "metric:", "logs": "log:", "traces": "trace:"}[
            source
        ]
        evidence = next(
            (item for item in context.evidence if item.evidence_id.startswith(prefix)),
            None,
        )
        if evidence is None:
            return SpecialistAssessment(
                source=source,
                observation_status="SOURCE_UNAVAILABLE",
                confidence=0.0,
                evidence_refs=(),
                summary="The source is unavailable in this development smoke.",
            )
        return SpecialistAssessment(
            source=source,
            observation_status="AVAILABLE",
            candidate_service=evidence.service,
            candidate_indicator=self._indicator(evidence.name),
            confidence=0.5,
            evidence_refs=(evidence.evidence_id,),
            summary="Deterministic source-isolated wiring smoke assessment.",
        )

    def plan_followup(self, incident, context, metrics_assessment):
        del incident, context, metrics_assessment
        return CommanderDecision(
            selected_sources=("logs", "traces"),
            rationale="Exercise both bounded follow-up source contracts in smoke.",
        )

    def diagnose(self, incident, context, architecture):
        del incident, architecture
        if not context.evidence:
            raise ValueError("synthetic smoke context contains no evidence")
        evidence = context.evidence[0]
        return Diagnosis(
            root_cause_service=evidence.service,
            root_cause_indicator=self._indicator(evidence.name),
            evidence_refs=(evidence.evidence_id,),
            explanation="Deterministic heuristic used only for wiring smoke.",
        )


def _real_provider(_scheduled: ScheduledRun):
    return provider_from_lock()


def _heuristic_provider(_scheduled: ScheduledRun):
    return HeuristicSmokeProvider()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an OB/SS-only RCAEval development pilot")
    parser.add_argument("--ob-root", type=Path, required=True)
    parser.add_argument("--ss-root", type=Path, required=True)
    parser.add_argument("--journal-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases-per-system", type=int, default=30)
    parser.add_argument("--provider", choices=("real", "heuristic"), default="real")
    parser.add_argument("--seed", type=int, default=20_260_806)
    args = parser.parse_args()
    if not 1 <= args.cases_per_system <= 30:
        raise ValueError("development pilot cases per system must be between 1 and 30")
    cases = _selected(
        discover_dev_cases(args.ob_root, DevSystem.RE2_OB),
        args.cases_per_system,
    ) + _selected(
        discover_dev_cases(args.ss_root, DevSystem.RE2_SS),
        args.cases_per_system,
    )
    schedule = _schedule(cases, args.seed)
    schedule_sha = sha256_bytes(canonical_json_bytes(schedule_payload(schedule)))
    run_lock_path = args.journal_root.parent / "run-lock.json"
    run_lock = {
        "schema_version": "rcaeval-re2.dev-run-lock.v1",
        "provider_mode": args.provider,
        "repository_base_commit": repository_base_commit(),
        "development_schedule_sha256": schedule_sha,
        **current_runtime_bindings(),
    }
    if run_lock_path.exists():
        if read_json_object(run_lock_path) != run_lock:
            raise ValueError("existing development run lock differs from runtime")
    else:
        write_json_create_once(run_lock_path, run_lock)
    if args.provider == "real":
        provider_factory = _real_provider
    else:
        provider_factory = _heuristic_provider
    records = run_schedule(cases, schedule, provider_factory, args.journal_root)
    truth = {
        case.case_id: GroundTruth(
            case_id=case.case_id,
            root_cause_service=case.root_cause_service,
            fault=case.fault,
            instance=case.instance,
        )
        for case in cases
    }
    _, summaries = score_terminal_records(records, truth)
    attempts_root = (
        args.journal_root.parent / f"{args.journal_root.name}.attempts"
    )
    prompt_lock = verify_prompt_lock()
    write_json_create_once(
        args.output,
        {
            "schema_version": "rcaeval-re2.dev-pilot.v1",
            "provider_mode": args.provider,
            "development_only": True,
            "case_count": len(cases),
            "run_count": len(records),
            "completed_run_count": sum(
                item.terminal_status.value == "COMPLETED" for item in records
            ),
            "systems": sorted({case.system for case in cases}),
            "model": prompt_lock["model"],
            "prompt_lock_sha256": sha256_file(CONFIG_ROOT / "prompt-lock.json"),
            "budget_lock_sha256": sha256_file(CONFIG_ROOT / "budget-lock.json"),
            "run_lock_sha256": sha256_file(run_lock_path),
            "terminal_journal_sha256": sha256_tree(
                args.journal_root,
                include_suffixes=(".json",),
            ),
            "attempt_journal_sha256": sha256_tree(
                attempts_root,
                include_suffixes=(".json",),
            ),
            "development_schedule_sha256": schedule_sha,
            "architecture_semantics": {
                "single": "direct_sequential_sources_then_final",
                "fixed": "three_fixed_specialists_then_judge",
                "dynamic": "commander_staged_specialists_then_judge",
            },
            "architectures": {
                item.value: summaries[item].model_dump(mode="json")
                for item in Architecture
            },
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
