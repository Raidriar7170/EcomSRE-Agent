"""Persisted LLM-selected read loop, separate from authoritative diagnosis."""

import json
import os
from pathlib import Path
from typing import Any

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre.product.errors import ProductError
from ecomsre.product.ids import new_product_id
from ecomsre.product.investigation.contracts import (
    InvestigationConfig,
    InvestigationDecision,
    PriceSchedule,
)
from ecomsre.product.investigation.provider import StructuredProvider
from ecomsre.product.investigation.repository import InvestigationRepository
from ecomsre.product.jobs.contracts import JobLeaseFenceV1


def configured_provider(repository: InvestigationRepository) -> StructuredProvider:
    config = OpenAICompatibleConfig.from_environment()
    price_path = os.environ.get("ECOMSRE_PRODUCT_PROVIDER_PRICE_FILE")
    if config is None or not price_path:
        raise ProductError(
            "PROVIDER_NOT_CONFIGURED", "Provider and dated price schedule are required."
        )
    prices = PriceSchedule.model_validate_json(Path(price_path).read_bytes())
    return StructuredProvider(config, prices, repository,
                              api_style=os.environ.get("ECOMSRE_PRODUCT_PROVIDER_API_STYLE", "chat_completions"))


def validate_hypothesis_evidence(hypothesis, refs):
    for ref in hypothesis.support + hypothesis.against:
        if ref not in refs or hypothesis.target not in refs[ref]["targets"]:
            raise ValueError("EVIDENCE_TARGET_MISMATCH")
        if (
            refs[ref]["status"] != "SUCCESS_NONEMPTY"
            or refs[ref]["truncated"]
            or hypothesis.target not in refs[ref]["covered_services"]
            or not any(
                r.get("service") == hypothesis.target for r in refs[ref]["records"]
            )
        ):
            raise ValueError("EVIDENCE_INCOMPLETE")
        if hypothesis.claim_window.model_dump(mode="json") != refs[ref]["window"]:
            raise ValueError("EVIDENCE_WINDOW_MISMATCH")


def check_predictions(hypotheses, observations):
    from ecomsre.product.knowledge.expressions import (
        Aggregate,
        DerivedExpression,
        evaluate_expression,
    )

    checked = []
    for hypothesis in hypotheses:
        test = hypothesis.get("prediction_test")
        if test is None:
            checked.append(
                {
                    "hypothesis_id": hypothesis["hypothesis_id"],
                    "status": "NOT_CHECKED",
                    "reason": "NO_COMPILED_TEST",
                    "claim_kind": "MODEL_INFERENCE",
                }
            )
            continue
        operand = Aggregate(field=test["field"], operator=test["operator"])
        expression = DerivedExpression(
            numerator=operand,
            comparator=test["comparator"],
            threshold=test["threshold"],
            threshold_unit=operand.unit,
            threshold_provenance="MODEL_PROPOSED_TEST_NOT_VALIDATED_KNOWLEDGE",
            window_seconds=test["window_seconds"],
            minimum_samples=test["minimum_samples"],
        )
        matching = [
            o for o in observations if o["window"] == hypothesis["claim_window"]
        ]
        result = evaluate_expression(
            expression, target=hypothesis["target"], observations=matching
        )
        checked.append(
            {
                "hypothesis_id": hypothesis["hypothesis_id"],
                **result.model_dump(mode="json"),
                "claim_kind": "OBSERVED_NUMERIC_TEST",
            }
        )
    return checked


def supported_hypotheses(hypotheses, checked):
    """Support and the checked observable prediction belong to the same claim.

    A TRUE number is not causal confirmation. Only referenced supporting evidence
    may establish that hypothesis; alternatives remain part of the result.
    """
    predictions = {p["hypothesis_id"]: p for p in checked}
    return [
        h["hypothesis_id"] for h in hypotheses
        if h["support"]
        and (p := predictions[h["hypothesis_id"]])["status"] == "TRUE"
        and p.get("evidence_refs")
        and set(p["evidence_refs"]).issubset(h["support"])
    ]


def run_investigation(
    *,
    incident,
    diagnosis,
    knowledge_snapshot: str,
    reads,
    repository: InvestigationRepository,
    config: InvestigationConfig,
    fence: JobLeaseFenceV1,
    renew_lease,
    provider=None,
    residuals=(),
) -> dict[str, Any]:
    existing = repository.get(incident.incident_id)
    if existing is not None and existing["status"] != "RUNNING":
        return existing
    if not config.enabled:
        raise ProductError("INVESTIGATION_DISABLED", "Investigation is disabled.")
    catalog = reads.catalog()
    binding = semantic_sha256_v22(
        {
            "incident": incident.incident_sha256,
            "diagnosis": diagnosis.result_sha256,
            "knowledge": knowledge_snapshot,
            "config": config.model_dump(),
            "catalog": catalog,
            "residuals": residuals,
        }
    )
    if existing is not None and existing["binding"] != binding:
        raise ProductError(
            "INVESTIGATION_CONTEXT_DRIFT", "Frozen investigation context differs."
        )
    session = existing or {
        "session_id": new_product_id("inv"),
        "incident_id": incident.incident_id,
        "environment_id": incident.environment_id,
        "parent_diagnosis_id": diagnosis.diagnosis_id,
        "knowledge_snapshot": knowledge_snapshot,
        "binding": binding,
        "revision": -1,
        "status": "RUNNING",
        "hypotheses": [],
        "observations": list(reads.initial_observations),
        "read_catalog": catalog,
        "decisions": [],
        "provider_calls": 0,
        "read_count": 0,
        "no_progress": 0,
        "repair_count": 0,
        "pending_read": None,
        "stop_reason": None,
        "action_authority": "NONE",
        "unexplained_residuals": list(residuals),
    }

    def save():
        revision = session["revision"]
        session["revision"] += 1
        repository.save(session, expected_revision=revision, fence=fence)

    def stop(status: str, reason: str):
        session["checked_predictions"] = check_predictions(
            session["hypotheses"], session["observations"]
        )
        session["supported_hypothesis_ids"] = supported_hypotheses(
            session["hypotheses"], session["checked_predictions"]
        )
        session.update(status=status, stop_reason=reason)
        save()
        return session

    if existing is None:
        save()
    if diagnosis.terminal.value == "NO_INCIDENT" or (
        diagnosis.terminal.value in {"CORE_KNOWN", "EXTENSION_KNOWN"} and not residuals
    ):
        return stop("NOT_REQUIRED", "KNOWN_OR_HEALTHY_FAST_PATH")
    if not catalog:
        return stop("OBSERVABILITY_GAP", "NO_LEGAL_READS")
    try:
        provider = provider or configured_provider(repository)
    except (ProductError, ValueError, OSError):
        return stop("PROVIDER_FAILED", "PROVIDER_CONFIGURATION_UNAVAILABLE")
    while session["provider_calls"] < config.max_provider_calls:
        renew_lease()
        if session["pending_read"] is not None:
            # A pre-dispatch read intent survived a crash: do not invent its result.
            return stop("UNRESOLVED", "INTERRUPTED_READ_OUTCOME_UNKNOWN")
        used = {d["action_id"] for d in session["decisions"] if d["kind"] == "READ"}
        legal = [entry for entry in catalog if entry["action_id"] not in used]
        if session["read_count"] >= config.max_evidence_reads:
            legal = []
        if not legal and not session["observations"]:
            return stop("OBSERVABILITY_GAP", "NO_NEW_LEGAL_READS")
        view = {
            "formal_diagnosis": diagnosis.terminal.value,
            "targets": list(incident.candidate_logical_services),
            "capability_limitations": list(diagnosis.capability_limitations),
            "unexplained_residuals": session["unexplained_residuals"],
            "hypotheses": session["hypotheses"],
            "observations": session["observations"],
            "legal_reads": legal,
            "remaining_calls": config.max_provider_calls - session["provider_calls"],
            "last_validation_error": session.get("last_validation_error"),
        }
        key = session["session_id"] + ":" + str(session["provider_calls"])
        try:
            decision = provider.complete(
                key=key,
                task="investigate",
                view=view,
                schema=InvestigationDecision,
                reasoning=config.reasoning_effort,
                fence=fence,
            )
        except ProductError as exc:
            session["provider_calls"] += 1
            if (
                exc.code == "PROVIDER_PROTOCOL_INVALID"
                and session["repair_count"] < config.schema_repair_budget
            ):
                session["repair_count"] += 1
                session["last_validation_error"] = exc.code
                save()
                continue
            return stop(
                "BUDGET_EXHAUSTED"
                if exc.code == "BUDGET_EXHAUSTED"
                else "PROVIDER_FAILED",
                exc.code,
            )
        session["provider_calls"] += 1
        refs = {o["evidence_ref"]: o for o in session["observations"]}
        known_ids = {h["hypothesis_id"] for h in session["hypotheses"]}
        try:
            if decision.kind == "READ" and decision.action_id not in {
                e["action_id"] for e in legal
            }:
                raise ValueError("ILLEGAL_OR_DUPLICATE_READ")
            new_hypotheses = []
            for hypothesis in decision.hypotheses:
                if hypothesis.target not in incident.candidate_logical_services:
                    raise ValueError("TARGET_MISMATCH")
                if (
                    hypothesis.hypothesis_id is not None
                    and hypothesis.hypothesis_id not in known_ids
                ):
                    raise ValueError("UNKNOWN_HYPOTHESIS_ID")
                if set(hypothesis.support) & set(hypothesis.against):
                    raise ValueError("CONFLICTING_REFERENCE_ROLES")
                validate_hypothesis_evidence(hypothesis, refs)
                new_hypotheses.append(
                    {
                        **hypothesis.model_dump(mode="json"),
                        "hypothesis_id": hypothesis.hypothesis_id
                        or new_product_id("hyp"),
                    }
                )
            if len({h["hypothesis_id"] for h in new_hypotheses}) != len(new_hypotheses):
                raise ValueError("DUPLICATE_HYPOTHESIS_ID")
            if decision.result == "PROVISIONAL_SUPPORTED" and not supported_hypotheses(
                new_hypotheses,
                check_predictions(new_hypotheses, session["observations"]),
            ):
                raise ValueError("SAME_HYPOTHESIS_CHECKED_SUPPORT_REQUIRED")
            if len(new_hypotheses) > config.max_hypotheses:
                raise ValueError("HYPOTHESIS_LIMIT")
        except ValueError as exc:
            session["decisions"].append(
                {**decision.model_dump(mode="json"), "validation": str(exc)}
            )
            session["no_progress"] += 1
            session["last_validation_error"] = str(exc)
            save()
            if session["no_progress"] >= config.max_no_progress_turns:
                return stop("UNRESOLVED", "INVALID_DECISION_LIMIT")
            continue
        previous = json.dumps(session["hypotheses"], sort_keys=True)
        if new_hypotheses:
            session["hypotheses"] = new_hypotheses
        session["decisions"].append(
            {**decision.model_dump(mode="json"), "validation": "ACCEPTED"}
        )
        session["last_validation_error"] = None
        if decision.kind == "READ":
            session["pending_read"] = decision.action_id
            save()
            renew_lease()
            try:
                observation = reads.read(decision.action_id)
            except (ProductError, ValueError, OSError):
                return stop("UNRESOLVED", "READ_FAILED_WITHOUT_COMMITTED_OBSERVATION")
            session["observations"].append(observation)
            session["pending_read"] = None
            session["read_count"] += 1
            session["no_progress"] = (
                0 if observation["records"] else session["no_progress"] + 1
            )
        else:
            session["no_progress"] += int(
                previous == json.dumps(session["hypotheses"], sort_keys=True)
            )
        if decision.kind in {"CONCLUDE", "ABSTAIN"}:
            session["unresolved_alternatives"] = decision.unresolved_alternatives
            return stop(decision.result, "MODEL_TERMINAL")
        save()
        if session["no_progress"] >= config.max_no_progress_turns:
            return stop("UNRESOLVED", "NO_INFORMATION_GAIN")
    return stop("BUDGET_EXHAUSTED", "SESSION_CALL_CAP")
