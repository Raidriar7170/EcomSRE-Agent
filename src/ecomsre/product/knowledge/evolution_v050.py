"""Model/miner candidate pool and independent test-only knowledge governance.

These governance methods are not model tools. Evaluation reconstructs evidence
from Product repositories and uses the existing Shadow gate without relaxation.
"""

import hashlib
import json
from pathlib import Path
from datetime import UTC, datetime
from typing import Any, Literal

from ecomsre.product.knowledge import split_v050
from ecomsre.product.investigation.contracts import StrictModel

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.errors import ProductError
from ecomsre.product.ids import new_product_id
from ecomsre.product.jobs.fencing import require_live_job_fence
from ecomsre.product.investigation.repository import InvestigationRepository
from ecomsre.product.knowledge.candidates_v050 import (
    CompiledKnowledge,
    KnowledgeProposal,
    evaluate_candidate,
    candidate_components,
    snapshot_observations,
)
from ecomsre.product.knowledge.contracts import (
    ShadowEvaluationV1,
    ShadowCaseOutcomeV1,
    ShadowCaseOriginV1,
    ShadowEvaluationStratumV1,
)
from ecomsre.product.knowledge.runtime import (
    evaluate_shadow_gate_v1,
    mine_candidate_clauses_v1,
    build_predicate_matrix_v1,
)
from ecomsre.product.knowledge.compiler import _CORE_SOURCE, _ANOMALY_SOURCE


def proposal_observation_view(observation: dict[str, Any]) -> dict[str, Any]:
    """Keep incomplete-source diagnostics without sending inadmissible rows.

    Full CAS observations remain intact and are verified before this projection.
    A truncated result never supplies candidate evidence references.
    """
    if not observation["truncated"]:
        return observation
    return {
        **observation,
        "records": [],
        "record_count": len(observation["records"]),
        "records_omitted": "TRUNCATED_NOT_CANDIDATE_EVIDENCE",
    }


def evaluation_bindings() -> dict[str, str]:
    product = Path(__file__).resolve().parents[1]
    paths = [
        "knowledge/evolution_v050.py",
        "knowledge/candidates_v050.py",
        "knowledge/repository.py",
        "knowledge/metric_coverage.py",
        "incidents/anomaly_policy.py",
        "incidents/queue_action.py",
        "knowledge/expressions.py",
        "knowledge/runtime.py",
        "knowledge/compiler.py",
        "investigation/contracts.py",
        "investigation/provider.py",
        "investigation/reads.py",
        "knowledge/observations_v050.py",
        "knowledge/split_v050.py",
        "knowledge/shadow_controls_v050.py",
        "knowledge/selection_lock_v050.py",
        "investigation/closure_budget.py",
        "investigation/repository.py",
        "knowledge/drafts_v050.py",
        "incidents/extensions.py",
    ]
    return {
        path: hashlib.sha256((product / path).read_bytes()).hexdigest()
        for path in paths
    }


class IncompleteValidation(StrictModel):
    schema_version: Literal["ecomsre.product.incomplete-validation.v050"] = (
        "ecomsre.product.incomplete-validation.v050"
    )
    gate_passed: Literal[False] = False
    reason_codes: tuple[str, ...]
    outcomes: tuple[ShadowCaseOutcomeV1, ...]




class KnowledgeEvolutionV050:
    def __init__(self, knowledge, investigations: InvestigationRepository):
        self.knowledge, self.investigations = knowledge, investigations
        self.store = investigations.store
        with self.store.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS knowledge_test_environments_v050 (
                    environment_id TEXT PRIMARY KEY REFERENCES environments(environment_id),
                    enrolled_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS knowledge_candidate_pool_v050 (
                    registration_id TEXT PRIMARY KEY, environment_id TEXT NOT NULL,
                    compiled_sha256 TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL,
                    state TEXT NOT NULL, freeze_json TEXT, evaluation_json TEXT);
                CREATE TABLE IF NOT EXISTS knowledge_development_v050 (
                    registration_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS knowledge_draft_provenance_v050 (
                    source_request_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS knowledge_rejections_v050 (
                    source_request_key TEXT PRIMARY KEY, environment_id TEXT NOT NULL,
                    discovery_sha256 TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS knowledge_shadow_details_v050 (
                    registration_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
            """)

        split_v050.initialize(self.store)

    def freeze_split(self, environment_id, episodes):
        split_v050.freeze_split(self.store, environment_id, episodes)

    def bind_episode(self, incident_id, episode_id):
        split_v050.bind_episode(self.store, self.knowledge._incident(incident_id), episode_id)

    def enroll_fresh_test_environment(self, environment_id: str) -> None:
        """Harness-only enrollment before the first incident or registration."""
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                if (
                    c.execute(
                        "SELECT 1 FROM incidents WHERE environment_id=?",
                        (environment_id,),
                    ).fetchone()
                    or c.execute(
                        "SELECT 1 FROM environment_extension_registrations WHERE environment_id=?",
                        (environment_id,),
                    ).fetchone()
                ):
                    raise ProductError(
                        "TEST_REGISTRY_NOT_FRESH",
                        "Enrollment requires an unused test environment.",
                    )
                c.execute(
                    "INSERT INTO knowledge_test_environments_v050 VALUES (?,?)",
                    (environment_id, datetime.now(UTC).isoformat()),
                )
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise

    def discovery_view(
        self, environment_id: str, incident_ids: list[str], *, record_exposure: bool = True
    ) -> dict[str, Any]:
        if len(set(incident_ids)) < 2 or len(incident_ids) > 12:
            raise ValueError("multiple distinct discovery incidents required")
        with self.store.connect() as c:
            if record_exposure:
                c.execute("BEGIN IMMEDIATE")
            freezes = c.execute(
                "SELECT freeze_json FROM knowledge_candidate_pool_v050 WHERE freeze_json IS NOT NULL"
            ).fetchall()
            heldout_ids = {i for row in freezes for i in json.loads(row[0])["cases"]}
            if set(incident_ids) & heldout_ids:
                raise ValueError("frozen evaluation events cannot enter discovery")
            # A split-bound event must never cross roles, even under a new candidate.
            bound = [i for i in incident_ids if c.execute(
                "SELECT 1 FROM knowledge_episode_incidents_v050 WHERE incident_id=?", (i,)
            ).fetchone()]
            split_v050.require_roles(c, bound, {"DISCOVERY", "DEVELOPMENT"})
            if record_exposure:
                split_v050.expose(c, incident_ids, "DISCOVERY_VIEW")
                c.execute("COMMIT")
        sessions = []
        for incident_id in sorted(set(incident_ids)):
            session = self.investigations.get(incident_id)
            if (
                session is None
                or session["environment_id"] != environment_id
                or session["status"] == "RUNNING"
            ):
                raise ValueError("completed same-environment investigations required")
            instance = self.knowledge._incident(incident_id)
            if session["parent_diagnosis_id"] != self.knowledge._diagnosis(incident_id).diagnosis_id:
                raise ValueError("investigation parent diagnosis differs")
            from ecomsre.product.knowledge.observations_v050 import load_observations
            bound_observations = {o["evidence_ref"]: o for o in load_observations(instance, self.investigations.objects)}
            for observation in session["observations"]:
                if observation["evidence_ref"].startswith("investigation:") and bound_observations.get(observation["evidence_ref"]) != observation:
                    raise ValueError("investigation projection differs from bound CAS observation")
            # Deliberate allowlist: no case labels, truth, private records or controller state.
            sessions.append(
                {
                    key: session[key]
                    for key in (
                        "incident_id",
                        "hypotheses",
                        "observations",
                        "decisions",
                        "status",
                    )
                }
            )
            sessions[-1]["observations"] = [
                proposal_observation_view(o) for o in session["observations"]
            ]
            from ecomsre.product.knowledge.observations_v050 import dependency_catalog
            sessions[-1]["dependency_catalog_status"] = (
                "RETAINED" if "read_catalog" in session else "NOT_RETAINED_LEGACY_SESSION"
            )
            sessions[-1]["deployable_resource_dependencies"] = dependency_catalog(
                instance, session.get("read_catalog", []), list(bound_observations.values())
            )
        payload = {
            "sessions": sessions,
            "predicate_catalog": sorted(
                ["core:" + k.value for k in _CORE_SOURCE]
                + ["ga:" + k.value for k in _ANOMALY_SOURCE]
            ),
            "proposal_constraints": "All supporting_refs AND counter_evidence_refs must cover the proposed target, be SUCCESS_NONEMPTY and not truncated; cross-target comparisons may be described in confusable_patterns but are not target evidence refs. Predicates and expression are conjunctive conditions, not a list of alternatives. PATTERN_ONLY; use snapshot_sha256 for threshold_provenance. Do not infer causality. Only BOUND_OBSERVATION dependencies supply supporting_refs. LEGAL_NOT_COLLECTED requires a new investigation; initial resource snapshots do not establish supplemental query bindings. Fields outside feature_catalog are unsupported.",
            "feature_catalog": {
                "source": "RESOURCES",
                "fields": {"cpu_percent": "PERCENT", "memory_bytes": "BYTES"},
                "operators": ["mean", "max", "delta", "rate"],
                "ratio": "same-unit operands only",
                "threshold_units": {
                    "mean_max_delta": "same as operand field unit: PERCENT or BYTES",
                    "rate": "PERCENT_PER_SECOND or BYTES_PER_SECOND",
                    "ratio": "RATIO; numerator and denominator units must match",
                },
                "evaluator_version": "resource-aggregates-v1",
            },
        }
        return {**payload, "snapshot_sha256": semantic_sha256_v22(payload)}

    def propose(
        self,
        *,
        environment_id: str,
        incident_ids: list[str],
        provider,
        key: str,
        fence=None,
    ):
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            split_v050.require_roles(c, incident_ids, {"DISCOVERY", "DEVELOPMENT"})
            split_v050.expose(c, incident_ids, "PROPOSER_DISPATCH")
            c.execute("COMMIT")
        view = self.discovery_view(environment_id, incident_ids)
        proposal = provider.complete(
            key=key,
            task="propose_detection_knowledge",
            view=view,
            schema=KnowledgeProposal,
            reasoning="high",
            fence=fence,
        )
        try:
            return self.add_candidate(
                environment_id=environment_id,
                proposal=proposal,
                origin="LLM",
                source_request_key=key,
                discovery=view,
                fence=fence,
            )
        except (ValueError, ProductError) as exc:
            # The paid response remains in CAS/ledger even when compilation rejects it.
            reason = exc.code if isinstance(exc, ProductError) else str(exc)
            with self.store.connect() as c:
                c.execute(
                    "INSERT OR IGNORE INTO knowledge_rejections_v050 VALUES (?,?,?,?,?)",
                    (
                        key,
                        environment_id,
                        view["snapshot_sha256"],
                        reason[:240],
                        datetime.now(UTC).isoformat(),
                    ),
                )
            raise ProductError(
                "KNOWLEDGE_CANDIDATE_REJECTED",
                "Candidate failed deterministic compilation; rejection retained.",
            ) from None

    def add_miner_candidates(self, *, family_id: str, incident_ids: list[str]):
        family = self.knowledge.get_family(family_id)
        discovery = self.discovery_view(family.environment_id, incident_ids)
        # Build only from explicitly visible discovery inputs. The legacy family
        # matrix scans all environment controls before filtering, so do not call it.
        from ecomsre.product.knowledge.repository import (
            _present_predicates,
            _predicate_source,
        )
        from ecomsre.product.knowledge.contracts import (
            PredicateCellStateV1,
            PredicateMatrixCellV1,
            PredicateMatrixRowV1,
            PredicateMatrixRowKindV1,
        )
        from ecomsre.product.incidents.contracts import DiagnosisTerminalV1

        positive_ids = set(family.member_incident_ids) & set(incident_ids)
        fingerprints = {
            i: self.knowledge.fingerprint_for(i) for i in sorted(set(incident_ids))
        }
        positives = tuple(fingerprints[i] for i in sorted(positive_ids))
        positive_incidents = tuple(
            self.knowledge._incident(i) for i in sorted(positive_ids)
        )
        predicate_ids = sorted({p for f in positives for p in _present_predicates(f)})
        kinds = {
            DiagnosisTerminalV1.CORE_KNOWN: PredicateMatrixRowKindV1.CORE_KNOWN_CONTROL,
            DiagnosisTerminalV1.NO_INCIDENT: PredicateMatrixRowKindV1.NO_INCIDENT_CONTROL,
            DiagnosisTerminalV1.EXTENSION_KNOWN: PredicateMatrixRowKindV1.OTHER_ACCEPTED_FAMILY,
            DiagnosisTerminalV1.INSUFFICIENT_EVIDENCE: PredicateMatrixRowKindV1.INSUFFICIENT_OR_CONFLICT_CONTROL,
            DiagnosisTerminalV1.CONFLICTING_EVIDENCE: PredicateMatrixRowKindV1.INSUFFICIENT_OR_CONFLICT_CONTROL,
        }
        rows = []
        for incident_id, fingerprint in fingerprints.items():
            kind = (
                PredicateMatrixRowKindV1.POSITIVE_FAMILY
                if incident_id in positive_ids
                else kinds.get(self.knowledge._diagnosis(incident_id).terminal)
            )
            if kind is None:
                continue
            if (
                incident_id not in positive_ids
                and not self.knowledge._eligible_negative_control(
                    positive_fingerprints=positives,
                    positive_incidents=positive_incidents,
                    control_fingerprint=fingerprint,
                    control_incident=self.knowledge._incident(incident_id),
                    result=self.knowledge._diagnosis(incident_id),
                )
            ):
                continue
            present = _present_predicates(fingerprint)
            cells = []
            for predicate_id in predicate_ids:
                source = _predicate_source(predicate_id)
                state = (
                    PredicateCellStateV1.PRESENT
                    if predicate_id in present
                    else PredicateCellStateV1.ABSENT_WITH_COMPLETE_COVERAGE
                    if source in fingerprint.source_coverage
                    else PredicateCellStateV1.SOURCE_FAILED
                    if source in fingerprint.evidence_sources
                    else PredicateCellStateV1.UNKNOWN
                )
                cells.append(
                    PredicateMatrixCellV1(
                        predicate_id=predicate_id, source=source, state=state
                    )
                )
            rows.append(
                PredicateMatrixRowV1(
                    row_id=kind.value.casefold() + ":" + incident_id,
                    incident_id=incident_id,
                    row_kind=kind,
                    cells=tuple(cells),
                )
            )
        matrix = build_predicate_matrix_v1(
            environment_id=family.environment_id, family_id=family_id, rows=tuple(rows)
        )
        mining = mine_candidate_clauses_v1(
            matrix,
            existing_clause_predicates=self.knowledge._existing_clause_predicates(
                family.environment_id
            ),
        )
        members = sorted(
            {
                row.incident_id
                for row in matrix.rows
                if row.row_kind.value == "POSITIVE_FAMILY"
            }
        )
        if not members:
            return ()
        first = self.knowledge._incident(members[0])
        diagnosis = self.knowledge._diagnosis(members[0])
        if len(diagnosis.root_service_ids) != 1:
            return ()
        from ecomsre.product.environment.services import ServiceCatalogRepositoryV1

        identities = ServiceCatalogRepositoryV1(self.store).get_map(
            first.environment_id
        )
        target = next(
            s.logical_service
            for s in identities.services
            if s.service_id == diagnosis.root_service_ids[0]
        )
        refs = sorted(
            {
                o["evidence_ref"]
                for session in discovery["sessions"]
                for o in session["observations"]
                if target in o["covered_services"] and not o["truncated"]
            }
        )[:24]
        result = []
        for clause in mining.candidates[:5]:
            proposal = KnowledgeProposal(
                name="mined-" + clause.candidate_id,
                kind="PATTERN_ONLY",
                target=target,
                broad_domain=diagnosis.broad_domain
                if diagnosis.broad_domain
                in {"RUNTIME", "RESOURCE", "CONFIGURATION", "DEPENDENCY", "APPLICATION"}
                else "UNKNOWN",
                member_incidents=members,
                predicates=list(clause.predicate_ids),
                expression=None,
                supporting_refs=refs,
                counter_evidence_refs=[],
                confusable_patterns=["Other same-symptom mechanisms"],
                prediction="The observed conjunction recurs in independent target events.",
                inapplicable_conditions=["Incomplete selected-source coverage"],
            )
            result.append(
                self.add_candidate(
                    environment_id=family.environment_id,
                    proposal=proposal,
                    origin="DETERMINISTIC_MINER",
                    source_request_key=None,
                    discovery=discovery,
                )
            )
        return tuple(result)

    def add_candidate(
        self,
        *,
        environment_id: str,
        proposal: KnowledgeProposal,
        origin: str,
        source_request_key: str | None,
        discovery: dict[str, Any],
        draft_view_binding: dict[str, Any] | None = None,
        fence=None,
    ):
        """Both candidate origins enter identical compilation and later Shadow paths."""
        full_discovery_ids = [s["incident_id"] for s in discovery["sessions"]]
        if self.discovery_view(environment_id, full_discovery_ids) != discovery:
            raise ValueError("discovery snapshot does not match persisted inputs")
        members = {s["incident_id"]: s for s in discovery["sessions"]}
        from ecomsre.product.environment.services import ServiceCatalogRepositoryV1

        identities = ServiceCatalogRepositoryV1(self.store).get_map(environment_id)
        target_id = next(
            (
                s.service_id
                for s in identities.services
                if s.logical_service == proposal.target
            ),
            None,
        )
        for member in proposal.member_incidents:
            diagnosis = self.knowledge._diagnosis(member)
            if target_id is None or diagnosis.root_service_ids != (target_id,):
                raise ValueError(
                    "ambiguous or mismatched discovery root cannot form a family"
                )
        if proposal.kind != "PATTERN_ONLY":
            raise ValueError(
                "mechanism claims require independent causal support not supplied by this adapter"
            )
        if set(proposal.member_incidents) - set(members):
            raise ValueError("proposal references non-discovery events")
        refs = {
            o["evidence_ref"]
            for s in members.values()
            for o in s["observations"]
            if proposal.target in o["covered_services"] and not o["truncated"]
        }
        if set(proposal.supporting_refs + proposal.counter_evidence_refs) - refs:
            raise ValueError("proposal references unavailable target evidence")
        if (
            proposal.expression is not None
            and proposal.expression.threshold_provenance != discovery["snapshot_sha256"]
        ):
            raise ValueError(
                "threshold provenance must bind the supplied discovery snapshot"
            )
        if proposal.expression is not None and proposal.resource_dependency is None and any(
            ref.startswith("investigation:") for ref in proposal.supporting_refs
        ):
            raise ValueError("supplemental resource expression requires a deployable dependency")
        if proposal.resource_dependency is not None:
            from ecomsre.product.knowledge.observations_v050 import load_observations, select_dependency
            for member in proposal.member_incidents:
                instance = self.knowledge._incident(member)
                bound = load_observations(instance, self.investigations.objects)
                selected = select_dependency(proposal.resource_dependency,
                                             incident_end=instance.diagnosis_observed_at, observations=bound, target=proposal.target)
                if not selected or not set(proposal.supporting_refs).intersection(o["evidence_ref"] for o in selected):
                    raise ValueError("candidate has no verified deployable observation dependency")
        if origin == "LLM":
            with self.store.connect() as c:
                row = c.execute(
                    "SELECT state,payload_json FROM investigation_provider_calls_v050 WHERE call_key=?",
                    (source_request_key,),
                ).fetchone()
            if row is None or row["state"] != "COMPLETED":
                raise ValueError("candidate lacks a completed model response")
            provenance = json.loads(row["payload_json"])
            task, bound_view = "propose_detection_knowledge", discovery
            if draft_view_binding is not None:
                from ecomsre.product.knowledge.drafts_v050 import (
                    TASK, PROTOCOL, KnowledgeDraft, compile_draft, draft_view,
                )
                task, bound_view = TASK, draft_view_binding
                protocol = PROTOCOL
                raw_draft: KnowledgeDraft
                from ecomsre.product.knowledge.drafts_v050 import (
                    SCOPED_PROTOCOL, SCOPED_TASK, ScopedKnowledgeDraft,
                    scoped_view, scoped_model_view, compile_scoped_draft,
                )
                if bound_view.get("protocol") == SCOPED_PROTOCOL:
                    protocol, task = SCOPED_PROTOCOL, SCOPED_TASK
                    if bound_view["request_key"] != source_request_key or scoped_view(
                        discovery, request_key=source_request_key,
                        target=bound_view["target"], members=list(bound_view["members"].values()),
                        feedback=bound_view.get("feedback"),
                    ) != bound_view:
                        raise ValueError("draft mapping differs from persisted discovery")
                    raw_draft = ScopedKnowledgeDraft.model_validate(provenance.get("proposal"))
                    reconstructed, context = compile_scoped_draft(raw_draft, bound_view)
                    bound_view = scoped_model_view(bound_view)
                else:
                    if draft_view(discovery, bound_view.get("feedback")) != bound_view:
                        raise ValueError("draft mapping differs from persisted discovery")
                    raw_draft = KnowledgeDraft.model_validate(provenance.get("proposal"))
                    reconstructed, context = compile_draft(raw_draft, bound_view)
                if reconstructed != proposal or provenance.get("prompt_version") != protocol:
                    raise ValueError("draft canonical reconstruction differs")
            elif provenance.get("proposal") != proposal.model_dump(mode="json"):
                raise ValueError("candidate lacks a matching completed model response")
            with self.store.connect() as c:
                split_v050.require_independent(c, proposal.member_incidents)
            provenance = json.loads(row["payload_json"])
            if provenance.get("evidence_mode") != "LIVE_PROVIDER" or provenance.get(
                "task_view_sha256"
            ) != semantic_sha256_v22(
                {"task": task, "view": bound_view}
            ):
                raise ValueError("model response lacks live discovery-view binding")
        incident = self.knowledge._incident(proposal.member_incidents[0])
        if incident.environment_id != environment_id:
            raise ValueError("candidate environment differs")
        payload = {
            "schema_version": "ecomsre.product.compiled-knowledge.v050",
            "registration_id": new_product_id("registration"),
            "environment_id": environment_id,
            "capability_sha256": incident.source_capability_sha256,
            "origin": origin,
            "source_request_key": source_request_key,
            "discovery_snapshot_sha256": discovery["snapshot_sha256"],
            "proposal": proposal.model_dump(mode="json"),
            "action_authority": "NONE",
            "discovery_incident_ids": tuple(sorted(members)),
        }
        candidate = CompiledKnowledge.model_validate(
            {**payload, "compiled_sha256": semantic_sha256_v22(payload)}
        )
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                require_live_job_fence(c, fence)
                existing = c.execute(
                    "SELECT payload_json FROM knowledge_candidate_pool_v050 WHERE environment_id=?",
                    (environment_id,),
                ).fetchall()
                for row in existing:
                    prior_candidate = CompiledKnowledge.model_validate_json(row[0])
                    prior = prior_candidate.proposal
                    if (
                        source_request_key is not None
                        and prior_candidate.source_request_key == source_request_key
                    ):
                        if (
                            prior != proposal
                            or prior_candidate.discovery_snapshot_sha256
                            != discovery["snapshot_sha256"]
                        ):
                            raise ValueError("candidate source request rebound")
                        c.execute("COMMIT")
                        return prior_candidate
                    if (set(prior.predicates), prior.expression, prior.target) == (
                        set(proposal.predicates),
                        proposal.expression,
                        proposal.target,
                    ):
                        raise ProductError(
                            "DUPLICATE_KNOWLEDGE_CANDIDATE",
                            "An equivalent candidate is already recorded.",
                        )
                from ecomsre.product.knowledge.selection_lock_v050 import load as selection_lock
                if selection_lock(c) is not None:
                    raise ValueError("candidate selection locked; no new candidate admission")
                if draft_view_binding is not None:
                    if origin != "LLM":
                        raise ValueError("draft provenance requires live model origin")
                    c.execute("INSERT INTO knowledge_draft_provenance_v050 VALUES (?,?)", (
                        source_request_key, json.dumps(dict(
                            protocol=protocol, raw_draft=raw_draft.model_dump(mode="json"),
                            draft_sha256=semantic_sha256_v22(raw_draft.model_dump(mode="json")),
                            mapping_snapshot_sha256=semantic_sha256_v22(draft_view_binding),
                            compiler_sha256=evaluation_bindings()["knowledge/drafts_v050.py"],
                            canonical_sha256=semantic_sha256_v22(proposal.model_dump(mode="json")),
                            comparison_context=context))))
                c.execute(
                    "INSERT INTO knowledge_candidate_pool_v050 VALUES (?,?,?,?,'DRAFT',NULL,NULL)",
                    (
                        candidate.registration_id,
                        environment_id,
                        candidate.compiled_sha256,
                        candidate.model_dump_json(),
                    ),
                )
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
        return candidate

    def check_development(self, registration_id: str, incident_ids: list[str]) -> dict:
        """One bounded development pass; its events can never become this holdout.

        This reports deterministic matches, not evaluator truth or causal proof.
        No threshold is fitted and no model request occurs in this method.
        """
        if not 1 <= len(set(incident_ids)) <= 12:
            raise ValueError("bounded distinct development cases required")
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT * FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
                (registration_id,),
            ).fetchone()
            if row is None or row["state"] != "DRAFT":
                raise ValueError("development requires a draft candidate")
            if c.execute(
                "SELECT 1 FROM knowledge_development_v050 WHERE registration_id=?",
                (registration_id,),
            ).fetchone():
                raise ValueError("development already consumed")
            frozen = c.execute(
                "SELECT freeze_json FROM knowledge_candidate_pool_v050 WHERE freeze_json IS NOT NULL"
            ).fetchall()
            if set(incident_ids) & {
                i for item in frozen for i in json.loads(item[0])["cases"]
            }:
                raise ValueError("holdout cannot enter development")
            candidate_for_split = CompiledKnowledge.model_validate_json(row["payload_json"])
            if candidate_for_split.origin == "LLM":
                split_v050.require_roles(c, incident_ids, {"DISCOVERY", "DEVELOPMENT"})
            # Reserve before evaluation; interrupted work remains consumed.
            c.execute(
                "INSERT INTO knowledge_development_v050 VALUES (?,?)",
                (
                    registration_id,
                    json.dumps(
                        {
                            "state": "EVALUATING",
                            "incident_ids": sorted(set(incident_ids)),
                        }
                    ),
                ),
            )
            c.execute("COMMIT")
        candidate = CompiledKnowledge.model_validate_json(row["payload_json"])
        outcomes = []
        for incident_id in sorted(set(incident_ids)):
            material = self.knowledge._shadow_runtime_material(incident_id)
            if (
                material.incident.environment_id != candidate.environment_id
                or material.incident.source_capability_sha256
                != candidate.capability_sha256
            ):
                raise ValueError("development environment or capability differs")
            evidence = self.knowledge._evidence(
                incident_id, self.knowledge._diagnosis(incident_id).diagnosis_id
            )
            snapshots = {
                item.action_id: item.payload
                for item in evidence.objects
                if "connector_result" in item.payload
            }
            from ecomsre.product.knowledge.observations_v050 import load_observations
            supplemental = load_observations(material.incident, self.investigations.objects)
            outcome = evaluate_candidate(
                candidate,
                incident_end=material.incident.diagnosis_observed_at,
                target=candidate.proposal.target,
                memory=material.runtime_input.memory,
                anomalies=material.runtime_input.generic_anomalies,
                observations=snapshot_observations(
                    snapshots.values(), material.runtime_input.memory
                ) + supplemental,
            )
            outcomes.append(
                {
                    "incident_id": incident_id,
                    "runtime_input_sha256": material.runtime_input.runtime_input_sha256,
                    "outcome": outcome.model_dump(mode="json"),
                    "components": candidate_components(candidate,
                        memory=material.runtime_input.memory,
                        anomalies=material.runtime_input.generic_anomalies,
                        incident_end=material.incident.diagnosis_observed_at,
                        observations=snapshot_observations(snapshots.values(), material.runtime_input.memory) + supplemental),
                }
            )
        result = {
            "state": "CHECKED",
            "incident_ids": sorted(set(incident_ids)),
            "candidate_sha256": candidate.compiled_sha256,
            "outcomes": outcomes,
            "claim": "DEVELOPMENT_ONLY_NOT_INDEPENDENT_VALIDATION",
        }
        with self.store.connect() as c:
            c.execute(
                "UPDATE knowledge_development_v050 SET payload_json=? WHERE registration_id=?",
                (json.dumps(result, sort_keys=True), registration_id),
            )
        return result

    def freeze(self, registration_id: str, cases: dict[str, str], *, derived_controls_version: str | None = None) -> None:
        """Freeze evaluator-only incident strata before evaluating any holdout."""
        from ecomsre.product.knowledge.shadow_controls_v050 import CONTROL_VERSION
        if derived_controls_version not in {None, CONTROL_VERSION}:
            raise ValueError("unsupported derived control protocol")
        if derived_controls_version is not None and any(
            label in {"TARGET_COUNTERFACTUAL", "SOURCE_FAILURE"} for label in cases.values()
        ):
            raise ValueError("derived controls cannot be registered as original episodes")
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute(
                    "SELECT * FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
                    (registration_id,),
                ).fetchone()
                if row is None or row["state"] != "DRAFT":
                    raise ValueError("candidate not draft")
                candidate = CompiledKnowledge.model_validate_json(row["payload_json"])
                from ecomsre.product.knowledge.selection_lock_v050 import require_frozen_identity
                selection_sha256 = require_frozen_identity(c, candidate, cases, derived_controls_version)
                if set(cases) & set(candidate.discovery_incident_ids):
                    raise ValueError("holdout overlaps discovery")
                development = c.execute(
                    "SELECT payload_json FROM knowledge_development_v050 WHERE registration_id=?",
                    (registration_id,),
                ).fetchone()
                if (
                    development is None
                    or json.loads(development[0])["state"] != "CHECKED"
                ):
                    raise ValueError(
                        "completed development check required before holdout"
                    )
                if set(cases) & set(json.loads(development[0])["incident_ids"]):
                    raise ValueError("holdout overlaps development")
                if set(cases) & split_v050.exposed_incidents(c):
                    raise ValueError("holdout overlaps globally exposed incident or episode")
                if candidate.origin == "LLM":
                    split_v050.require_roles(c, cases, {"HOLDOUT"})
                    split_v050.require_independent(c, cases)
                for incident_id, stratum in cases.items():
                    ShadowEvaluationStratumV1(stratum)
                    if (
                        self.knowledge._incident(incident_id).environment_id
                        != candidate.environment_id
                    ):
                        raise ValueError("holdout environment differs")
                source = c.execute(
                    "SELECT request_sha256 FROM investigation_provider_calls_v050 WHERE call_key=?",
                    (candidate.source_request_key,),
                ).fetchone()
                from ecomsre.product.knowledge.observations_v050 import load_observations
                supplemental_hashes = {
                    i: semantic_sha256_v22(load_observations(self.knowledge._incident(i), self.investigations.objects))
                    for i in cases
                }
                manifest = {
                    "supplemental_sha256": supplemental_hashes,
                    "split_sha256": split_v050.split_digest(c, candidate.environment_id),
                    "evaluator_and_protocol_sha256": evaluation_bindings(),
                    "source_request_sha256": None if source is None else source[0],
                    "diagnosis_hashes": {
                        i: self.knowledge._diagnosis(i).result_sha256 for i in cases
                    },
                    "candidate_sha256": candidate.compiled_sha256,
                    "cases": cases,
                    "incident_hashes": {
                        i: self.knowledge._incident(i).incident_sha256 for i in cases
                    },
                }
                if selection_sha256 is not None:
                    manifest["selection_lock_sha256"] = selection_sha256
                if derived_controls_version is not None:
                    manifest["derived_controls_version"] = derived_controls_version
                c.execute(
                    "UPDATE knowledge_candidate_pool_v050 SET state='FROZEN',freeze_json=? WHERE registration_id=?",
                    (json.dumps(manifest, sort_keys=True), registration_id),
                )
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise

    def evaluate(self, registration_id: str):
        with self.store.connect() as c:
            row = c.execute(
                "SELECT * FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
                (registration_id,),
            ).fetchone()
            if row is None or row["state"] != "FROZEN":
                raise ValueError("candidate must be frozen and unconsumed")
            # Consume before reading evaluator cases. A crash leaves the attempt retained.
            if (
                c.execute(
                    "UPDATE knowledge_candidate_pool_v050 SET state='EVALUATING' WHERE registration_id=? AND state='FROZEN'",
                    (registration_id,),
                ).rowcount
                != 1
            ):
                raise ValueError("evaluation already consumed")
        candidate = CompiledKnowledge.model_validate_json(row["payload_json"])
        manifest = json.loads(row["freeze_json"])
        if manifest["evaluator_and_protocol_sha256"] != evaluation_bindings():
            raise ValueError("frozen evaluator or protocol changed")
        with self.store.connect() as c:
            source = c.execute(
                "SELECT request_sha256 FROM investigation_provider_calls_v050 WHERE call_key=?",
                (candidate.source_request_key,),
            ).fetchone()
        if manifest["source_request_sha256"] != (None if source is None else source[0]):
            raise ValueError("frozen model request changed")
        with self.store.connect() as c:
            from ecomsre.product.knowledge.selection_lock_v050 import require_frozen_identity
            current_lock = require_frozen_identity(c, candidate, manifest["cases"], manifest.get("derived_controls_version"))
            if current_lock != manifest.get("selection_lock_sha256"):
                raise ValueError("selection lock changed after data freeze")
            if manifest["split_sha256"] != split_v050.split_digest(c, candidate.environment_id):
                raise ValueError("frozen episode split differs")
            split_v050.expose(c, manifest["cases"], "HOLDOUT_CONSUMED")
        outcomes = []
        raw_details = []
        for incident_id, label in sorted(manifest["cases"].items()):
            material = self.knowledge._shadow_runtime_material(incident_id)
            if (
                material.incident.incident_sha256
                != manifest["incident_hashes"][incident_id]
                or material.incident.source_capability_sha256
                != candidate.capability_sha256
                or manifest["candidate_sha256"] != candidate.compiled_sha256
                or self.knowledge._diagnosis(incident_id).result_sha256
                != manifest["diagnosis_hashes"][incident_id]
            ):
                raise ValueError("frozen incident, candidate or capability changed")
            evidence = self.knowledge._evidence(
                incident_id, self.knowledge._diagnosis(incident_id).diagnosis_id
            )
            snapshots = {
                item.action_id: item.payload
                for item in evidence.objects
                if "connector_result" in item.payload
            }
            from ecomsre.product.knowledge.observations_v050 import load_observations
            supplemental = load_observations(material.incident, self.investigations.objects)
            if semantic_sha256_v22(supplemental) != manifest["supplemental_sha256"][incident_id]:
                raise ValueError("frozen supplemental observations changed")
            observations = snapshot_observations(
                snapshots.values(), material.runtime_input.memory
            ) + supplemental
            outcome = evaluate_candidate(
                candidate,
                target=candidate.proposal.target,
                memory=material.runtime_input.memory,
                anomalies=material.runtime_input.generic_anomalies,
                incident_end=material.incident.diagnosis_observed_at,
                observations=observations,
            )
            stratum = ShadowEvaluationStratumV1(label)
            payload = {
                "schema_version": "ecomsre.product.shadow-case-outcome.v1",
                "case_id": incident_id,
                "incident_id": incident_id,
                "stratum": stratum.value,
                "origin": ShadowCaseOriginV1.PERSISTED_INCIDENT.value,
                "runtime_input_sha256": material.runtime_input.runtime_input_sha256,
                "expected_match": stratum
                is ShadowEvaluationStratumV1.POSITIVE_INCIDENT,
                "matched": outcome.status == "TRUE",
                "evaluated_target_services": (candidate.proposal.target,),
                "supporting_evidence_refs": outcome.evidence_refs
                if outcome.status == "TRUE"
                else (),
                "available_evidence_refs": tuple(
                    sorted(
                        {r.evidence_ref for r in material.runtime_input.memory.evidence_refs}
                        | {o["evidence_ref"] for o in observations}
                    )
                ),
                "required_sources": candidate.proposal.required_sources,
                "source_reachable": outcome.status != "UNKNOWN",
                "action_authority_violations": 0,
                "reason_code": None,
            }
            outcomes.append(
                ShadowCaseOutcomeV1.model_validate(
                    {**payload, "outcome_sha256": semantic_sha256_v22(payload)}
                )
            )
            raw_details.append(dict(
                case_id=incident_id, origin="PERSISTED_INCIDENT",
                raw_result=outcome.model_dump(mode="json"),
            ))
            if manifest.get("derived_controls_version") is not None and stratum is ShadowEvaluationStratumV1.POSITIVE_INCIDENT:
                from ecomsre.product.knowledge.shadow_controls_v050 import CONTROL_VERSION, derived_controls
                if manifest["derived_controls_version"] != CONTROL_VERSION:
                    raise ValueError("derived control protocol differs")
                derived, details = derived_controls(candidate, material, list(snapshots.values()), observations)
                outcomes.extend(derived)
                raw_details.extend(details)
        represented = {o.stratum for o in outcomes}
        present = {o.stratum for o in outcomes if o.origin is not ShadowCaseOriginV1.NOT_AVAILABLE}
        for stratum in ShadowEvaluationStratumV1:
            if stratum in represented:
                continue
            payload = {
                "schema_version": "ecomsre.product.shadow-case-outcome.v1",
                "case_id": "missing:" + stratum.value,
                "incident_id": None,
                "stratum": stratum.value,
                "origin": "NOT_AVAILABLE",
                "runtime_input_sha256": None,
                "expected_match": None,
                "matched": None,
                "evaluated_target_services": (),
                "supporting_evidence_refs": (),
                "available_evidence_refs": (),
                "required_sources": candidate.proposal.required_sources,
                "source_reachable": None,
                "action_authority_violations": 0,
                "reason_code": "CONTROL_NOT_AVAILABLE",
            }
            outcomes.append(
                ShadowCaseOutcomeV1.model_validate(
                    {**payload, "outcome_sha256": semantic_sha256_v22(payload)}
                )
            )
        required = {
            ShadowEvaluationStratumV1.POSITIVE_INCIDENT,
            ShadowEvaluationStratumV1.CONFUSABLE_CORE_KNOWN,
            ShadowEvaluationStratumV1.NO_INCIDENT,
            ShadowEvaluationStratumV1.TARGET_COUNTERFACTUAL,
            ShadowEvaluationStratumV1.SOURCE_FAILURE,
        }
        shadow: IncompleteValidation | ShadowEvaluationV1
        if missing := required - present:
            # Preserve the legacy strict Shadow contract: an incomplete dataset
            # is a rejected attempt, not a fabricated full ShadowEvaluation.
            shadow = IncompleteValidation(
                reason_codes=tuple(
                    sorted(s.value + "_CONTROL_MISSING" for s in missing)
                ),
                outcomes=tuple(outcomes),
            )
        else:
            shadow = evaluate_shadow_gate_v1(
                registration_id=registration_id, outcomes=tuple(outcomes)
            )
        # New protocol adds a determinate-negative gate, without changing the
        # legacy Shadow formula or inflating the original episode denominator.
        if manifest.get("derived_controls_version") is not None:
            indeterminate = [d["case_id"] for d in raw_details
                if d.get("origin") == "PERSISTED_INCIDENT"
                and manifest["cases"][d["case_id"]] != "POSITIVE_INCIDENT"
                and d["raw_result"]["status"] == "UNKNOWN"]
            if indeterminate:
                shadow = IncompleteValidation(
                    reason_codes=tuple(sorted(set(shadow.reason_codes) | {"ORIGINAL_NEGATIVE_CONTROL_UNKNOWN"})),
                    outcomes=tuple(outcomes),
                )
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "INSERT INTO knowledge_shadow_details_v050 VALUES (?,?)",
                (registration_id, json.dumps(dict(
                    original_episode_count=len(manifest["cases"]),
                    derived_control_counts={origin: sum(o.origin.value == origin for o in outcomes)
                                            for origin in ("DERIVED_COUNTERFACTUAL", "DERIVED_SOURCE_FAILURE")},
                    raw_details=raw_details,
                ), sort_keys=True)),
            )
            c.execute(
                "UPDATE knowledge_candidate_pool_v050 SET state=?,evaluation_json=? WHERE registration_id=?",
                (
                    "VALIDATED" if shadow.gate_passed else "REJECTED",
                    shadow.model_dump_json(),
                    registration_id,
                ),
            )
            c.execute("COMMIT")
        return shadow

    def promote(self, registration_id: str) -> None:
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute(
                    "SELECT * FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
                    (registration_id,),
                ).fetchone()
                if (
                    row is None
                    or row["state"] != "VALIDATED"
                    or not c.execute(
                        "SELECT 1 FROM knowledge_test_environments_v050 WHERE environment_id=?",
                        (row["environment_id"],),
                    ).fetchone()
                ):
                    raise ProductError(
                        "TEST_PROMOTION_DENIED",
                        "Independent validation and fresh test registry enrollment required.",
                    )
                if c.execute(
                    "SELECT 1 FROM environment_extension_registrations WHERE environment_id=? AND status='ACTIVE'",
                    (row["environment_id"],),
                ).fetchone():
                    raise ProductError(
                        "REGISTRY_CONFLICT_REVIEW_REQUIRED",
                        "Existing active knowledge requires a separate conflict review.",
                    )
                now = datetime.now(UTC).isoformat()
                c.execute(
                    "INSERT INTO environment_extension_registrations VALUES (?,?,?,'ACTIVE',?,?)",
                    (
                        registration_id,
                        row["environment_id"],
                        row["payload_json"],
                        now,
                        now,
                    ),
                )
                version = c.execute(
                    "SELECT COALESCE(MAX(registry_version),0)+1 FROM environment_extension_registry_versions WHERE environment_id=?",
                    (row["environment_id"],),
                ).fetchone()[0]
                c.execute(
                    "INSERT INTO environment_extension_registry_versions VALUES (?,?,?,'ACTIVE',?,?)",
                    (
                        row["environment_id"],
                        version,
                        registration_id,
                        row["payload_json"],
                        now,
                    ),
                )
                c.execute(
                    "INSERT INTO promotion_records VALUES (?,?,?,?)",
                    (
                        new_product_id("promotion"),
                        registration_id,
                        json.dumps(
                            {
                                "authorization": "PREAUTHORIZED_TEST_PROMOTION",
                                "compiled_sha256": row["compiled_sha256"],
                                "shadow_sha256": json.loads(row["evaluation_json"])[
                                    "evaluation_sha256"
                                ],
                            }
                        ),
                        now,
                    ),
                )
                c.execute(
                    "UPDATE knowledge_candidate_pool_v050 SET state='ACTIVE' WHERE registration_id=?",
                    (registration_id,),
                )
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise

    def revoke(self, registration_id: str) -> None:
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                if (
                    c.execute(
                        "UPDATE knowledge_candidate_pool_v050 SET state='REVOKED' WHERE registration_id=? AND state='ACTIVE'",
                        (registration_id,),
                    ).rowcount
                    != 1
                ):
                    raise ValueError("active candidate required")
                c.execute(
                    "UPDATE environment_extension_registrations SET status='REVOKED',updated_at=? WHERE registration_id=?",
                    (datetime.now(UTC).isoformat(), registration_id),
                )
                row = c.execute(
                    "SELECT environment_id,payload_json FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
                    (registration_id,),
                ).fetchone()
                version = c.execute(
                    "SELECT COALESCE(MAX(registry_version),0)+1 FROM environment_extension_registry_versions WHERE environment_id=?",
                    (row["environment_id"],),
                ).fetchone()[0]
                c.execute(
                    "INSERT INTO environment_extension_registry_versions VALUES (?,?,?,'REVOKED',?,?)",
                    (
                        row["environment_id"],
                        version,
                        registration_id,
                        row["payload_json"],
                        datetime.now(UTC).isoformat(),
                    ),
                )
                c.execute(
                    "INSERT INTO revocation_records VALUES (?,?,?,?)",
                    (
                        new_product_id("revocation"),
                        registration_id,
                        json.dumps({"authorization": "PREAUTHORIZED_TEST_REVOCATION"}),
                        datetime.now(UTC).isoformat(),
                    ),
                )
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
