"""Candidate-independent qualification of the three original closure strata.

A planned label is not an observation. This protocol never consumes a candidate
match and never changes the historical Shadow formula or stored diagnoses.
"""

from ecomsre.dta_v2.v22.predicates import evaluate_no_incident_v22
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22 as sha

VERSION = "v050-observed-control-qualification-v1"


def qualify(
    *, stratum, target, incident, diagnosis, runtime, observations, evidence_objects
):
    """Pure judgment over normal Product evidence, without candidate predicates."""
    if (
        diagnosis.incident_id != incident.incident_id
        or runtime.case_id != incident.incident_id
    ):
        raise ValueError("control qualification incident binding differs")
    reasons = []
    refs = {r.evidence_ref: r for r in runtime.memory.evidence_refs}
    strong = [a for a in runtime.generic_anomalies if a.strength.value == "STRONG"]

    def covered(service, source):
        return any(
            o["source"] == source
            and o["status"] == "SUCCESS_NONEMPTY"
            and not o["truncated"]
            and service in o["covered_services"]
            and any(r.get("service") == service for r in o["records"])
            for o in observations
        )

    def supported(evidence_refs):
        return bool(evidence_refs) and all(r in refs for r in evidence_refs)

    terminal = diagnosis.terminal.value
    health = None
    if stratum == "NO_INCIDENT":
        health = evaluate_no_incident_v22(
            memory=runtime.memory,
            candidate_services=runtime.candidate_services,
        ).model_dump(mode="json")
        # Match the existing bridge's filtered-anomaly interpretation, while
        # independently retaining its coverage requirements and actual diagnosis.
        coverage_reasons = set(health["denial_reasons"]) - {"STRONG_ANOMALY_PRESENT"}
        if terminal != "NO_INCIDENT":
            reasons.append("HEALTHY_NORMAL_DIAGNOSIS_REQUIRED")
        if strong:
            reasons.append("HEALTHY_STRONG_ANOMALY_PRESENT")
        if coverage_reasons or any(
            not covered(s, source)
            for s in runtime.candidate_services
            for source in ("METRICS", "RUNTIME")
        ):
            reasons.append("HEALTHY_SOURCE_COVERAGE_INCOMPLETE")
    elif stratum == "CONFUSABLE_CORE_KNOWN":
        if terminal != "CORE_KNOWN":
            reasons.append("CORE_NORMAL_DIAGNOSIS_REQUIRED")
        if not diagnosis.root_service_ids or not supported(
            diagnosis.supporting_evidence_refs
        ):
            reasons.append("CORE_DIAGNOSIS_SUPPORT_INCOMPLETE")
        # A genuine normal Core diagnosis has source-backed predicates. Bind
        # those exact references; a candidate FALSE cannot establish this role.
        predicates = [
            p
            for p in runtime.memory.predicates
            if set(p.evidence_refs) & set(diagnosis.supporting_evidence_refs)
        ]
        if not predicates or any(
            not covered(p.service, p.source.value) for p in predicates
        ):
            reasons.append("CORE_SOURCE_COVERAGE_INCOMPLETE")
    elif stratum == "POSITIVE_INCIDENT":
        from ecomsre.product.knowledge.compiler import _predicate_parts

        target_anomalies = [a for a in strong if a.service == target]
        if terminal != "OPEN_WORLD":
            reasons.append("TARGET_OPEN_WORLD_DIAGNOSIS_REQUIRED")
        if not any(
            supported(a.evidence_refs)
            and covered(target, _predicate_parts("ga:" + a.kind.value)[1].value)
            for a in target_anomalies
        ):
            reasons.append("TARGET_INDEPENDENT_ANOMALY_SUPPORT_REQUIRED")
    else:
        reasons.append("ORIGINAL_CONTROL_STRATUM_UNSUPPORTED")
    result = dict(
        protocol=VERSION,
        planned_stratum=stratum,
        target=target,
        qualified=not reasons,
        reason_codes=sorted(reasons),
        normal_terminal=terminal,
        normal_mechanism=diagnosis.mechanism,
        strong_anomalies=[a.model_dump(mode="json") for a in strong],
        health_coverage=health,
        source_coverage=[s.model_dump(mode="json") for s in runtime.source_coverage],
        bindings=dict(
            incident_id=incident.incident_id,
            incident_sha256=incident.incident_sha256,
            diagnosis_id=diagnosis.diagnosis_id,
            diagnosis_sha256=diagnosis.result_sha256,
            runtime_input_sha256=runtime.runtime_input_sha256,
            memory_sha256=runtime.memory.memory_sha256,
            source_capability_sha256=incident.source_capability_sha256,
            baseline_sha256=incident.baseline_sha256,
            evidence_objects=sorted(set(evidence_objects)),
            observations_sha256=sha(observations),
        ),
        candidate_match_used=False,
    )
    return result | {"qualification_sha256": sha(result)}
