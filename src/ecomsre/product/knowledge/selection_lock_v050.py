"""Harness-only identity lock before independent data collection.

This seals selection; it does not decide or certify the Goal's development gate.
The closure runner must establish that gate before invoking seal(). No model tool
exposes this module, and no live evidence is created by it.
"""

import json
from datetime import UTC, datetime

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22 as sha
from ecomsre.product.knowledge import split_v050
from ecomsre.product.knowledge.candidates_v050 import CompiledKnowledge
from ecomsre.product.knowledge.shadow_controls_v050 import CONTROL_VERSION

TABLE = "knowledge_selection_lock_v050"


def load(connection, environment_id=None):
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)
    ).fetchone():
        return None
    rows = connection.execute(
        "SELECT payload_json FROM knowledge_selection_lock_v050"
    ).fetchall()
    for row in rows:
        value = json.loads(row[0])
        if value["sha256"] != sha({k: v for k, v in value.items() if k != "sha256"}):
            raise ValueError("selection lock digest differs")
        if environment_id is None or value["environment_id"] == environment_id:
            return value
    return None


def identity(connection, registration_id):
    from ecomsre.product.knowledge.evolution_v050 import evaluation_bindings

    row = connection.execute(
        "SELECT payload_json FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
        (registration_id,),
    ).fetchone()
    if row is None:
        raise ValueError("selection candidate missing")
    candidate = CompiledKnowledge.model_validate_json(row[0])
    development = connection.execute(
        "SELECT payload_json FROM knowledge_development_v050 WHERE registration_id=?",
        (registration_id,),
    ).fetchone()
    if development is None or json.loads(development[0])["state"] != "CHECKED":
        raise ValueError("selection requires retained completed development")
    report = json.loads(development[0])
    if report["candidate_sha256"] != candidate.compiled_sha256:
        raise ValueError("development candidate binding differs")
    source = None
    if candidate.source_request_key:
        source_row = connection.execute(
            "SELECT * FROM investigation_provider_calls_v050 WHERE call_key=?",
            (candidate.source_request_key,),
        ).fetchone()
        if source_row is None or source_row["state"] != "COMPLETED":
            raise ValueError("selection source is not completed")
        source = dict(source_row)
    return candidate, dict(
        candidate=candidate.model_dump(mode="json"),
        development=report,
        source=source,
        evaluator=evaluation_bindings(),
        split_sha256=split_v050.split_digest(connection, candidate.environment_id),
    )


def collection_range(plan):
    window = plan["time_range"]
    if not isinstance(window, dict) or set(window) != {"start", "end"}:
        raise ValueError("selection requires exact collection time range")
    start, end = (datetime.fromisoformat(window[k]) for k in ("start", "end"))
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("invalid selection collection time range")
    return start, end


def seal(store, registration_id, *, plan):
    """Irreversibly select one candidate; no replace/unlock method exists.

    plan binds exact future episode roles, collection procedure/limits, time range,
    development cohort/gate report, expression checks and deployment mapping.
    Opaque report fields are preserved, not treated as an automatically verified PASS.
    """
    expected = {
        "holdout_episodes",
        "recurrence_episode",
        "collection",
        "time_range",
        "development_gate_report",
        "compatibility_binding",
        "expression_checks",
        "derived_controls_version",
    }
    if set(plan) != expected or plan["derived_controls_version"] != CONTROL_VERSION:
        raise ValueError("selection protocol incomplete")
    strata = plan["holdout_episodes"]
    if len(strata) != 3 or set(strata.values()) != {
        "POSITIVE_INCIDENT",
        "NO_INCIDENT",
        "CONFUSABLE_CORE_KNOWN",
    }:
        raise ValueError("three independent planned strata required")
    if any(not plan[k] for k in expected - {"holdout_episodes"}):
        raise ValueError("selection plan must explicitly bind every protocol field")
    _, collection_end = collection_range(plan)
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        candidate, bindings = identity(c, registration_id)
        previous = load(c)
        if previous:
            if (
                previous["registration_id"] != registration_id
                or previous["plan"] != plan
                or previous["bindings"] != bindings
            ):
                raise ValueError("selection is immutable or bound identity changed")
            return previous
        if collection_end <= datetime.now(UTC):
            raise ValueError("selection collection window has already ended")
        state = c.execute(
            "SELECT state FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
            (registration_id,),
        ).fetchone()[0]
        if state != "DRAFT":
            raise ValueError("selection must precede data freeze")
        manifest = split_v050.effective_manifest(c, candidate.environment_id)
        if (
            manifest is None
            or any(manifest.get(e) != "HOLDOUT" for e in strata)
            or manifest.get(plan["recurrence_episode"]) != "REUSE"
        ):
            raise ValueError("selection roles differ from frozen split")
        occupied = {
            r[0]
            for r in c.execute(
                "SELECT episode_id FROM knowledge_episode_incidents_v050"
            )
        }
        if occupied & (set(strata) | {plan["recurrence_episode"]}):
            raise ValueError("selection must precede holdout and recurrence collection")
        value = dict(
            round_id=split_v050.FINAL_CLOSURE_ROUND,
            registration_id=registration_id,
            environment_id=candidate.environment_id,
            selected_at=datetime.now(UTC).isoformat(),
            plan=plan,
            bindings=bindings,
            preexisting_incident_ids=sorted(
                r[0] for r in c.execute("SELECT incident_id FROM incidents")
            ),
            claim="IDENTITY_LOCK_ONLY_NOT_DEVELOPMENT_GATE_ACCEPTANCE",
        )
        value["sha256"] = sha(value)
        c.execute(
            "CREATE TABLE IF NOT EXISTS knowledge_selection_lock_v050 (environment_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)"
        )
        c.execute(
            "INSERT INTO knowledge_selection_lock_v050 VALUES (?,?)",
            (candidate.environment_id, json.dumps(value, sort_keys=True)),
        )
        c.execute("COMMIT")
        return value


def require_frozen_identity(connection, candidate, cases, version):
    lock = load(connection)
    if lock is not None and (
        lock["environment_id"] != candidate.environment_id
        or lock["registration_id"] != candidate.registration_id
    ):
        raise ValueError("another candidate was globally selected")
    successor = split_v050._successor(connection, candidate.environment_id)
    if lock is None:
        if successor is not None:
            raise ValueError("closure successor requires selection lock before freeze")
        return None
    _, bindings = identity(connection, candidate.registration_id)
    if (
        lock["registration_id"] != candidate.registration_id
        or lock["bindings"] != bindings
    ):
        raise ValueError("selected candidate or evaluator identity changed")
    if version != lock["plan"]["derived_controls_version"]:
        raise ValueError("selected derived protocol changed")
    selected = {}
    for incident_id, stratum in cases.items():
        if incident_id in lock["preexisting_incident_ids"]:
            raise ValueError("heldout incident existed before selection")
        row = connection.execute(
            "SELECT episode_id,environment_id FROM knowledge_episode_incidents_v050 WHERE incident_id=?",
            (incident_id,),
        ).fetchone()
        if (
            row is None
            or row["environment_id"] != candidate.environment_id
            or row["episode_id"] in selected
        ):
            raise ValueError("holdout must use independent planned episodes")
        selected[row["episode_id"]] = stratum
    if selected != lock["plan"]["holdout_episodes"]:
        raise ValueError("holdout cohort differs from selection plan")
    return lock["sha256"]
