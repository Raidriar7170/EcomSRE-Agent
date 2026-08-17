"""Create-once real-Provider development evaluation for the frozen PR-D dataset."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, model_validator

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v21.agent import AgentProviderV21
from ecomsre.dta_v2.v21.agent_contracts import AgentArmV21, build_alert_context_v21
from ecomsre.dta_v2.v21.agent_provider import OpenAICompatibleDtaAgentProviderV21
from ecomsre.dta_v2.v21.contracts import DtaModelV21, Sha256V21, semantic_sha256
from ecomsre.dta_v2.v21.evaluation_agents import (
    EvaluationEntryResultV21,
    execute_evaluation_arm_v21,
    score_and_persist_evaluation_execution_v21,
)
from ecomsre.dta_v2.v21.evaluation_campaign import (
    DevelopmentEvaluationReportV21,
    EvaluationPreregistrationV21,
    EvaluationSchedulePhaseV21,
    EvaluationScheduleV21,
    build_development_report_v21,
)
from ecomsre.dta_v2.v21.evaluation_contracts import (
    AgentVisibleReplayCaseV21,
    EvaluationArmV21,
    EvaluatorCaseTruthV21,
    PublicEvaluationManifestV21,
)
from ecomsre.dta_v2.v21.evaluation_dataset import (
    write_public_model_create_once_v21,
)
from ecomsre.dta_v2.v21.evaluation_scenarios import (
    build_evaluation_scenario_registry_v21,
)
from ecomsre.dta_v2.v21.identity import build_three_arm_identities_v21
from ecomsre.dta_v2.v21.registry import load_default_runbook_registry
from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_live_sandbox.contracts import write_private_json


ProviderFactoryV21 = Callable[[AgentArmV21, OpenAICompatibleConfig], AgentProviderV21]


class DevelopmentAttemptManifestV21(DtaModelV21):
    schema_version: Literal["dta-v21.development-attempt-manifest.v1"]
    attempt_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    created_at: datetime
    model_id: str = Field(min_length=1, max_length=128)
    identity_sha256s: tuple[Sha256V21, Sha256V21, Sha256V21]
    public_case_manifest_sha256: Sha256V21
    schedule_sha256: Sha256V21
    preregistration_sha256: Sha256V21
    scorer_source_sha256: Sha256V21 | None = None
    reporting_source_sha256: Sha256V21 | None = None
    protocol_revision_sha256: Sha256V21
    manifest_sha256: Sha256V21

    @model_validator(mode="after")
    def require_manifest(self) -> DevelopmentAttemptManifestV21:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() != timedelta(
            0
        ):
            raise ValueError("development attempt time must use UTC")
        revision_payload: dict[str, object] = {
            "model_id": self.model_id,
            "identity_sha256s": self.identity_sha256s,
            "public_case_manifest_sha256": self.public_case_manifest_sha256,
            "schedule_sha256": self.schedule_sha256,
            "preregistration_sha256": self.preregistration_sha256,
        }
        if (self.scorer_source_sha256 is None) != (
            self.reporting_source_sha256 is None
        ):
            raise ValueError("development attempt scorer bindings are incomplete")
        if self.scorer_source_sha256 is not None:
            revision_payload.update(
                {
                    "scorer_source_sha256": self.scorer_source_sha256,
                    "reporting_source_sha256": self.reporting_source_sha256,
                }
            )
        revision = semantic_sha256(revision_payload)
        if self.protocol_revision_sha256 != revision:
            raise ValueError("development attempt protocol revision differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected:
            raise ValueError("development attempt manifest digest differs")
        return self


class DevelopmentAttemptReceiptV21(DtaModelV21):
    schema_version: Literal["dta-v21.development-attempt-receipt.v1"]
    attempt_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attempt_manifest_sha256: Sha256V21
    entry_count: Literal[40]
    entry_sha256s: tuple[Sha256V21, ...] = Field(min_length=40, max_length=40)
    report_sha256: Sha256V21
    receipt_sha256: Sha256V21

    @model_validator(mode="after")
    def require_receipt(self) -> DevelopmentAttemptReceiptV21:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"receipt_sha256"})
        )
        if self.receipt_sha256 != expected:
            raise ValueError("development attempt receipt digest differs")
        return self


class DevelopmentEvaluationDispositionV21(DtaModelV21):
    schema_version: Literal["dta-v21.development-evaluation-disposition.v1"]
    terminal: Literal["DTA_V21_DEVELOPMENT_EVALUATION_COMPLETE"]
    model_id: str = Field(min_length=1, max_length=128)
    report_sha256: Sha256V21
    primary_entry_count: Literal[36]
    ablation_entry_count: Literal[4]
    truth_isolation: Literal["PASS"]
    scorer_self_tests: Literal["PASS"]
    unsafe_writes: Literal[0]
    held_out_executed: Literal[False]
    disposition_sha256: Sha256V21

    @model_validator(mode="after")
    def require_disposition(self) -> DevelopmentEvaluationDispositionV21:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"disposition_sha256"})
        )
        if self.disposition_sha256 != expected:
            raise ValueError("development disposition digest differs")
        return self


def publish_development_report_v21(
    *,
    report: DevelopmentEvaluationReportV21,
    report_path: Path,
    disposition_path: Path,
) -> DevelopmentEvaluationDispositionV21:
    report = DevelopmentEvaluationReportV21.model_validate(
        report.model_dump(mode="python")
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v21.development-evaluation-disposition.v1",
        "terminal": "DTA_V21_DEVELOPMENT_EVALUATION_COMPLETE",
        "model_id": report.model_id,
        "report_sha256": report.report_sha256,
        "primary_entry_count": report.primary_entry_count,
        "ablation_entry_count": report.ablation_entry_count,
        "truth_isolation": report.truth_isolation,
        "scorer_self_tests": report.scorer_self_tests,
        "unsafe_writes": report.unsafe_writes,
        "held_out_executed": False,
    }
    draft = cast(Any, DevelopmentEvaluationDispositionV21).model_construct(
        **payload, disposition_sha256="0" * 64
    )
    disposition = DevelopmentEvaluationDispositionV21.model_validate(
        {
            **payload,
            "disposition_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"disposition_sha256"})
            ),
        }
    )
    write_public_model_create_once_v21(report_path, report)
    write_public_model_create_once_v21(disposition_path, disposition)
    return disposition


def _default_provider_factory(
    arm: AgentArmV21, config: OpenAICompatibleConfig
) -> AgentProviderV21:
    return OpenAICompatibleDtaAgentProviderV21(
        arm=arm,
        config=config,
        timeout_seconds=90.0,
        max_completion_tokens=1600,
    )


def _file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("development scorer source is missing or unsafe")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_development_evaluation_v21(
    *,
    repository_root: Path,
    provider_env_path: Path,
    development_root: Path,
    private_attempts_root: Path,
    attempt_id: str,
    public_manifest: PublicEvaluationManifestV21,
    schedule: EvaluationScheduleV21,
    preregistration: EvaluationPreregistrationV21,
    provider_factory: ProviderFactoryV21 = _default_provider_factory,
) -> tuple[DevelopmentEvaluationReportV21, DevelopmentAttemptReceiptV21]:
    """Run only development entries; no held-out path is accepted by this API."""

    values = load_private_provider_env(provider_env_path)
    config = OpenAICompatibleConfig.from_environment(values)
    if config is None:
        raise ValueError("Provider configuration is absent")
    if config.model != preregistration.model_id:
        raise ValueError("configured Provider model differs from preregistration")
    if schedule.schedule_sha256 != preregistration.schedule_sha256:
        raise ValueError("evaluation schedule differs from preregistration")
    identities = build_three_arm_identities_v21(
        model_id=config.model,
        max_completion_tokens=preregistration.max_completion_tokens,
    )
    identity_sha256s = tuple(item.identity_sha256 for item in identities)
    identity_by_arm = {item.arm: item for item in identities}
    development_bindings = {
        item.case_id: item for item in public_manifest.development_cases
    }
    scorer_source_sha256 = _file_sha256(
        Path(__file__).with_name("evaluation_contracts.py")
    )
    reporting_source_sha256 = _file_sha256(
        Path(__file__).with_name("evaluation_campaign.py")
    )
    revision = semantic_sha256(
        {
            "model_id": config.model,
            "identity_sha256s": identity_sha256s,
            "public_case_manifest_sha256": public_manifest.manifest_sha256,
            "schedule_sha256": schedule.schedule_sha256,
            "preregistration_sha256": preregistration.preregistration_sha256,
            "scorer_source_sha256": scorer_source_sha256,
            "reporting_source_sha256": reporting_source_sha256,
        }
    )
    _reject_repeated_revision(private_attempts_root, revision)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    manifest_payload: dict[str, object] = {
        "schema_version": "dta-v21.development-attempt-manifest.v1",
        "attempt_id": attempt_id,
        "created_at": now,
        "model_id": config.model,
        "identity_sha256s": identity_sha256s,
        "public_case_manifest_sha256": public_manifest.manifest_sha256,
        "schedule_sha256": schedule.schedule_sha256,
        "preregistration_sha256": preregistration.preregistration_sha256,
        "scorer_source_sha256": scorer_source_sha256,
        "reporting_source_sha256": reporting_source_sha256,
        "protocol_revision_sha256": revision,
    }
    manifest_draft = cast(Any, DevelopmentAttemptManifestV21).model_construct(
        **manifest_payload, manifest_sha256="0" * 64
    )
    manifest = DevelopmentAttemptManifestV21.model_validate(
        {
            **manifest_payload,
            "manifest_sha256": semantic_sha256(
                manifest_draft.model_dump(mode="json", exclude={"manifest_sha256"})
            ),
        }
    )
    attempt_root = private_attempts_root / attempt_id
    write_private_json(
        attempt_root / "attempt-manifest.json", manifest, create_once=True
    )

    scenario_registry = build_evaluation_scenario_registry_v21(repository_root)
    runbooks = load_default_runbook_registry(repository_root)
    entries: list[EvaluationEntryResultV21] = []
    truths: dict[str, EvaluatorCaseTruthV21] = {}
    development_schedule = tuple(
        item
        for item in schedule.entries
        if item.phase
        in (
            EvaluationSchedulePhaseV21.DEVELOPMENT_PRIMARY,
            EvaluationSchedulePhaseV21.DEVELOPMENT_ABLATION,
        )
    )
    if len(development_schedule) != 40:
        raise ValueError("development schedule does not contain exact 40 entries")
    for scheduled in development_schedule:
        case = AgentVisibleReplayCaseV21.model_validate_json(
            _read_regular(
                development_root / "agent-visible" / f"{scheduled.case_id}.json"
            )
        )
        truth = EvaluatorCaseTruthV21.model_validate_json(
            _read_regular(
                development_root / "evaluator-truth" / f"{scheduled.case_id}.json"
            )
        )
        if truth.case_id != case.case_id:
            raise ValueError("development case and truth differ")
        binding = development_bindings.get(case.case_id)
        if (
            binding is None
            or binding.case_sha256 != case.case_sha256
            or binding.truth_sha256 != truth.truth_sha256
            or truth.split.value != "DEVELOPMENT"
        ):
            raise ValueError("development bytes differ from the public binding")
        truths[case.case_id] = truth
        run_id = semantic_sha256(
            {
                "attempt_id": attempt_id,
                "ordinal": scheduled.ordinal,
                "case_id": case.case_id,
                "arm": scheduled.arm.value,
            }
        )[:32]
        context = build_alert_context_v21(
            scenario=scenario_registry.require(case.scenario_id),
            run_id=run_id,
            started_at=case.captured_started_at,
            ended_at=case.captured_ended_at,
        )
        provider_arm = (
            AgentArmV21.EVIDENCE_GUIDED_PLANNER
            if scheduled.arm
            in (
                EvaluationArmV21.EVIDENCE_GUIDED_PLANNER,
                EvaluationArmV21.EVIDENCE_GUIDED_PLANNER_NO_COMPACTION,
            )
            else AgentArmV21(scheduled.arm.value)
        )
        provider = provider_factory(provider_arm, config)
        if provider.identity != identity_by_arm[provider_arm]:
            raise ValueError(
                "development Provider identity differs from frozen runtime"
            )
        execution = execute_evaluation_arm_v21(
            case=case,
            context=context,
            arm=scheduled.arm,
            registry=runbooks,
            provider=provider,
        )
        execution_id = semantic_sha256(
            {"run_id": run_id, "case_sha256": case.case_sha256}
        )[:32]
        entry = score_and_persist_evaluation_execution_v21(
            execution=execution,
            truth=truth,
            execution_id=execution_id,
            private_root=(
                attempt_root
                / "entries"
                / f"{scheduled.ordinal:02d}-{case.case_id}-{scheduled.arm.value.lower()}"
            ),
        )
        entries.append(entry)

    report = build_development_report_v21(
        entries=tuple(entries), truths=truths, identities=identities
    )
    write_private_json(
        attempt_root / "development-report.json", report, create_once=True
    )
    receipt_payload: dict[str, object] = {
        "schema_version": "dta-v21.development-attempt-receipt.v1",
        "attempt_id": attempt_id,
        "attempt_manifest_sha256": manifest.manifest_sha256,
        "entry_count": 40,
        "entry_sha256s": tuple(item.entry_sha256 for item in entries),
        "report_sha256": report.report_sha256,
    }
    receipt_draft = cast(Any, DevelopmentAttemptReceiptV21).model_construct(
        **receipt_payload, receipt_sha256="0" * 64
    )
    receipt = DevelopmentAttemptReceiptV21.model_validate(
        {
            **receipt_payload,
            "receipt_sha256": semantic_sha256(
                receipt_draft.model_dump(mode="json", exclude={"receipt_sha256"})
            ),
        }
    )
    write_private_json(attempt_root / "attempt-receipt.json", receipt, create_once=True)
    return report, receipt


def _reject_repeated_revision(root: Path, revision: str) -> None:
    if not root.exists():
        return
    if root.is_symlink() or not root.is_dir():
        raise ValueError("development attempts root is unsafe")
    for child in root.iterdir():
        if child.is_symlink() or not child.is_dir():
            raise ValueError("development attempts root contains an unsafe entry")
        path = child / "attempt-manifest.json"
        if path.is_symlink() or not path.is_file():
            raise ValueError("development attempt manifest is missing or unsafe")
        prior = DevelopmentAttemptManifestV21.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        if prior.protocol_revision_sha256 == revision:
            raise ValueError("identical development protocol revision cannot be rerun")


def _read_regular(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("development evaluation input is missing or unsafe")
    return path.read_text(encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument("--development-root", type=Path, required=True)
    parser.add_argument("--private-attempts-root", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--public-manifest", type=Path, required=True)
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--public-report", type=Path)
    parser.add_argument("--public-disposition", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report, receipt = run_development_evaluation_v21(
        repository_root=args.repository_root.resolve(),
        provider_env_path=args.provider_env.resolve(),
        development_root=args.development_root.resolve(),
        private_attempts_root=args.private_attempts_root.resolve(),
        attempt_id=args.attempt_id,
        public_manifest=PublicEvaluationManifestV21.model_validate_json(
            _read_regular(args.public_manifest)
        ),
        schedule=EvaluationScheduleV21.model_validate_json(
            _read_regular(args.schedule)
        ),
        preregistration=EvaluationPreregistrationV21.model_validate_json(
            _read_regular(args.preregistration)
        ),
    )
    if (args.public_report is None) != (args.public_disposition is None):
        raise ValueError("public report and disposition must be requested together")
    if args.public_report is not None:
        assert args.public_disposition is not None
        publish_development_report_v21(
            report=report,
            report_path=args.public_report.resolve(),
            disposition_path=args.public_disposition.resolve(),
        )
    print(report.model_dump_json(indent=2))
    print(receipt.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "DevelopmentAttemptManifestV21",
    "DevelopmentAttemptReceiptV21",
    "DevelopmentEvaluationDispositionV21",
    "publish_development_report_v21",
    "run_development_evaluation_v21",
)
