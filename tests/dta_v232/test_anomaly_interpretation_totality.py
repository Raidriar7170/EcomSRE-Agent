from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from ecomsre.dta_v2.v22.memory import (
    LogCategoryV22,
    LogSalientPayloadV22,
    SalientEvidenceMemoryV22,
    SignalStrengthV22,
)
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22
from ecomsre.dta_v2.v23.anomaly_interpretation_v232 import (
    AnomalyInterpretationContractErrorV232,
    DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232,
    InterpretationSourceV232,
)
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    _build_anomaly,
)
from ecomsre.dta_v2.v23.evaluation_v231 import (
    EvaluationPolicyV231,
    run_evaluation_policy_v231,
)
from ecomsre.dta_v2.v23.evaluation_successor_v231 import (
    load_successor_case_set_v231,
    load_successor_views_v231,
)


ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "config/dta-v231-successor/evaluation/cases.json"
VIEWS = ROOT / "config/dta-v231-successor/evaluation/ontology-views.json"


def _log_interpretation(category: LogCategoryV22 | None):
    evidence_ref = "e:test:logs:0:000000000000"
    anomaly = _build_anomaly(
        kind=GenericAnomalyKindV23.LOG_ERROR_CLUSTER,
        source=EvidenceSourceV22.LOGS,
        service="svc-a",
        related_services=(),
        strength=SignalStrengthV22.STRONG,
        summary="svc-a has an error log cluster",
        evidence_refs=(evidence_ref,),
        observed_values={"count": 1},
    )
    facts = ()
    if category is not None:
        facts = (
            SimpleNamespace(
                evidence_refs=(evidence_ref,),
                payload=LogSalientPayloadV22(
                    schema_version="dta-v22.salient-log.v1",
                    normalized_template="opaque log template",
                    category=category,
                    severity="ERROR",
                    count=1,
                    downstream_service=None,
                ),
            ),
        )
    memory = cast(
        SalientEvidenceMemoryV22,
        SimpleNamespace(salient_facts=facts, predicates=()),
    )
    return DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232.interpret(
        anomaly=anomaly,
        memory=memory,
    )


def test_registry_is_exhaustive_over_every_current_anomaly_kind() -> None:
    registry = DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232

    assert set(registry.supported_kinds) == set(GenericAnomalyKindV23)
    with pytest.raises(
        AnomalyInterpretationContractErrorV232,
        match="unmapped anomaly kind",
    ):
        registry.require_supported_kind("FUTURE_KIND")


@pytest.mark.parametrize(
    ("category", "domain"),
    (
        (LogCategoryV22.CONFIGURATION_ERROR, ProvisionalFaultDomainV23.CONFIGURATION),
        (LogCategoryV22.DEPENDENCY_TIMEOUT, ProvisionalFaultDomainV23.DEPENDENCY),
        (LogCategoryV22.MEMORY_PRESSURE, ProvisionalFaultDomainV23.RESOURCE),
    ),
)
def test_log_error_cluster_resolves_bound_log_category(
    category: LogCategoryV22,
    domain: ProvisionalFaultDomainV23,
) -> None:
    interpretation = _log_interpretation(category)

    assert interpretation.candidate_domains == (domain,)
    assert interpretation.primary_domain is domain
    assert interpretation.interpretation_source is InterpretationSourceV232.BOUND_LOG_CATEGORY


def test_unresolved_log_error_cluster_is_typed_unknown_not_exception() -> None:
    interpretation = _log_interpretation(None)

    assert interpretation.candidate_domains == (ProvisionalFaultDomainV23.UNKNOWN,)
    assert interpretation.primary_domain is ProvisionalFaultDomainV23.UNKNOWN
    assert interpretation.reason_codes == ("LOG_CATEGORY_UNRESOLVED",)


def _vx_113() -> tuple[object, object]:
    cases = load_successor_case_set_v231(CASES)
    views = load_successor_views_v231(VIEWS)
    return cases.require("vx-113"), views.require("vx-113")


def test_preserved_vx_113_reproduces_log_error_cluster_keyerror() -> None:
    spec, view = _vx_113()

    with pytest.raises(KeyError, match="LOG_ERROR_CLUSTER"):
        run_evaluation_policy_v231(
            repository_root=ROOT,
            spec=spec,
            view_spec=view,
            policy=EvaluationPolicyV231.V23_STRICT_CONFLICT_GATE,
            provider_transport=None,
        )


def test_repaired_vx_113_reaches_a_typed_terminal_without_keyerror() -> None:
    from ecomsre.dta_v2.v23.evaluation_v232 import (
        EvaluationPolicyV232,
        run_evaluation_policy_v232,
    )

    spec, view = _vx_113()
    run = run_evaluation_policy_v232(
        repository_root=ROOT,
        spec=spec,
        view_spec=view,
        policy=EvaluationPolicyV232.V23_STRICT_CONFLICT_GATE_TOTAL,
        provider_transport=None,
    )

    assert run.final_disposition in {
        "CONFLICTING_EVIDENCE",
        "INSUFFICIENT_EVIDENCE",
        "KNOWN_INCIDENT",
        "NO_INCIDENT",
        "UNREGISTERED_INCIDENT_SUSPECTED",
        "KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY",
    }
    assert any(
        anomaly.kind.value == "LOG_ERROR_CLUSTER"
        for anomaly in run.residual_graph.generic_anomalies
    )


def test_conflict_aware_vx_113_uses_the_total_interpretation_layer() -> None:
    from ecomsre.dta_v2.v23.evaluation_v232 import (
        EvaluationPolicyV232,
        run_evaluation_policy_v232,
    )

    spec, view = _vx_113()
    run = run_evaluation_policy_v232(
        repository_root=ROOT,
        spec=spec,
        view_spec=view,
        policy=EvaluationPolicyV232.V231_CONFLICT_AWARE_GATE_TOTAL,
        provider_transport=None,
    )

    assert run.final_disposition != "PROVIDER_FAILED"
    assert any(
        ProvisionalFaultDomainV23.RESOURCE in cluster.broad_domains
        for cluster in run.conflict_assessment.interpretation_clusters
    )
