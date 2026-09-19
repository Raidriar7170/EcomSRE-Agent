"""Harness-owned, immutable episode split and append-only exposure ledger."""

import json
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22


def initialize(store):
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS knowledge_split_v050 (
          environment_id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS knowledge_episode_incidents_v050 (
          incident_id TEXT PRIMARY KEY REFERENCES incidents(incident_id),
          environment_id TEXT NOT NULL, episode_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS knowledge_exposure_v050 (
          incident_id TEXT NOT NULL, purpose TEXT NOT NULL,
          PRIMARY KEY(incident_id,purpose));
        CREATE TABLE IF NOT EXISTS knowledge_split_successor_v050 (
          environment_id TEXT PRIMARY KEY REFERENCES knowledge_split_v050(environment_id),
          payload_json TEXT NOT NULL);
        """)


FINAL_CLOSURE_ROUND = "ecomsre-v050-final-learning-closure-v1"


def _successor(connection, environment_id):
    # Legacy read-only CAS/SQLite audits must not run a schema migration.
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='knowledge_split_successor_v050'"
    ).fetchone():
        return None
    row = connection.execute(
        "SELECT payload_json FROM knowledge_split_successor_v050 WHERE environment_id=?",
        (environment_id,),
    ).fetchone()
    if row is None:
        return None
    payload = json.loads(row[0])
    if payload["sha256"] != semantic_sha256_v22(
        {k: v for k, v in payload.items() if k != "sha256"}
    ):
        raise ValueError("split successor digest differs")
    return payload


def effective_manifest(connection, environment_id):
    row = connection.execute(
        "SELECT manifest_json FROM knowledge_split_v050 WHERE environment_id=?",
        (environment_id,),
    ).fetchone()
    if row is None:
        return None
    original = json.loads(row[0])
    successor = _successor(connection, environment_id)
    if successor is None:
        return original
    if (
        successor["parent_sha256"] != semantic_sha256_v22(original)
        or set(original) & set(successor["additions"])
        or successor["round_id"] != FINAL_CLOSURE_ROUND
    ):
        raise ValueError("split successor parent or scope differs")
    return original | successor["additions"]


def append_final_closure_split(store, environment_id, additions, *, parent_sha256):
    """Harness-only one-round successor; original bytes and exposure stay intact.

    New slots must precede their incidents. The snapshot of existing identities
    prevents an already observed, but not yet split-bound, incident being relabeled
    as a future event. This does not authorize collection or candidate selection.
    """
    if (
        not additions
        or len(additions) > 7
        or any(not isinstance(k, str) or not k for k in additions)
        or any(v not in {"DEVELOPMENT", "HOLDOUT", "REUSE"} for v in additions.values())
        or any(
            list(additions.values()).count(role) > limit
            for role, limit in (("DEVELOPMENT", 3), ("HOLDOUT", 3), ("REUSE", 1))
        )
    ):
        raise ValueError("invalid final closure episode allocation")
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        original = c.execute(
            "SELECT manifest_json FROM knowledge_split_v050 WHERE environment_id=?",
            (environment_id,),
        ).fetchone()
        if (
            original is None
            or semantic_sha256_v22(json.loads(original[0])) != parent_sha256
        ):
            raise ValueError("split successor parent differs")
        prior = _successor(c, environment_id)
        if prior is not None:
            if (
                prior["additions"] != additions
                or prior["parent_sha256"] != parent_sha256
            ):
                raise ValueError("split successor is immutable")
            effective_manifest(c, environment_id)
            return prior["sha256"]
        if c.execute(
            "SELECT 1 FROM knowledge_candidate_pool_v050 WHERE environment_id=? AND freeze_json IS NOT NULL",
            (environment_id,),
        ).fetchone():
            raise ValueError("cannot extend a frozen evaluation split")
        used = set()
        for row in c.execute("SELECT environment_id FROM knowledge_split_v050"):
            used.update(effective_manifest(c, row[0]))
        used.update(
            r[0]
            for r in c.execute(
                "SELECT episode_id FROM knowledge_episode_incidents_v050"
            )
        )
        if used & set(additions):
            raise ValueError("existing episode identities and roles are immutable")
        payload = {
            "round_id": FINAL_CLOSURE_ROUND,
            "parent_sha256": parent_sha256,
            "additions": additions,
            "preexisting_incident_ids": sorted(
                r[0] for r in c.execute("SELECT incident_id FROM incidents")
            ),
        }
        digest = semantic_sha256_v22(payload)
        c.execute(
            "INSERT INTO knowledge_split_successor_v050 VALUES (?,?)",
            (environment_id, json.dumps(payload | {"sha256": digest}, sort_keys=True)),
        )
        c.execute("COMMIT")
        return digest


def freeze_split(store, environment_id, episodes):
    """Controller calls before proposer; opaque episode IDs, no truth labels."""
    if not episodes or any(
        v not in {"DISCOVERY", "DEVELOPMENT", "HOLDOUT", "REUSE"}
        for v in episodes.values()
    ):
        raise ValueError("invalid episode split")
    payload = json.dumps(episodes, sort_keys=True)
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        old = c.execute(
            "SELECT manifest_json FROM knowledge_split_v050 WHERE environment_id=?",
            (environment_id,),
        ).fetchone()
        if old:
            if old[0] != payload:
                raise ValueError("episode split is immutable")
            return
        if c.execute(
            "SELECT 1 FROM knowledge_exposure_v050 x JOIN incidents i USING(incident_id) WHERE i.environment_id=?",
            (environment_id,),
        ).fetchone():
            raise ValueError("split must precede exposure")
        c.execute(
            "INSERT INTO knowledge_split_v050 VALUES (?,?)", (environment_id, payload)
        )
        c.execute("COMMIT")


def bind_episode(store, incident, episode_id):
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        manifest = effective_manifest(c, incident.environment_id)
        if manifest is None or episode_id not in manifest:
            raise ValueError("episode is not in frozen split")
        old = c.execute(
            "SELECT environment_id,episode_id FROM knowledge_episode_incidents_v050 WHERE incident_id=?",
            (incident.incident_id,),
        ).fetchone()
        if old:
            if tuple(old) != (incident.environment_id, episode_id):
                raise ValueError("incident episode binding is immutable")
            return
        from ecomsre.product.knowledge.selection_lock_v050 import load as selection_lock

        lock = selection_lock(c, incident.environment_id)
        if lock and manifest[episode_id] in {"HOLDOUT", "REUSE"}:
            planned = set(lock["plan"]["holdout_episodes"]) | {
                lock["plan"]["recurrence_episode"]
            }
            if (
                episode_id not in planned
                or incident.incident_id in lock["preexisting_incident_ids"]
            ):
                raise ValueError(
                    "selection requires a new incident in the planned episode"
                )
            if c.execute(
                "SELECT 1 FROM knowledge_episode_incidents_v050 WHERE episode_id=?",
                (episode_id,),
            ).fetchone():
                raise ValueError(
                    "planned episode already has its one immutable incident"
                )
            from datetime import datetime
            from ecomsre.product.knowledge.selection_lock_v050 import collection_range

            start, end = collection_range(lock["plan"])
            if (
                manifest[episode_id] == "HOLDOUT"
                and not max(start, datetime.fromisoformat(lock["selected_at"]))
                <= incident.started_at
                <= end
            ):
                raise ValueError(
                    "heldout incident outside selected collection time range"
                )
        successor = _successor(c, incident.environment_id)
        if (
            successor is not None
            and episode_id in successor["additions"]
            and incident.incident_id in successor["preexisting_incident_ids"]
        ):
            raise ValueError("successor requires a new incident, not relabeled history")
        c.execute(
            "INSERT INTO knowledge_episode_incidents_v050 VALUES (?,?,?)",
            (incident.incident_id, incident.environment_id, episode_id),
        )
        c.execute("COMMIT")


def require_roles(connection, incident_ids, allowed):
    for iid in incident_ids:
        row = connection.execute(
            "SELECT episode_id,environment_id FROM knowledge_episode_incidents_v050 WHERE incident_id=?",
            (iid,),
        ).fetchone()
        if (
            row is None
            or (effective_manifest(connection, row["environment_id"]) or {}).get(
                row["episode_id"]
            )
            not in allowed
        ):
            raise ValueError("missing or incompatible frozen episode split")


def exposed_incidents(connection):
    seen = {
        r[0]
        for r in connection.execute("SELECT incident_id FROM knowledge_exposure_v050")
    }
    # Import exposure from older persisted candidates/development, never erase it.
    for row in connection.execute(
        "SELECT payload_json FROM knowledge_candidate_pool_v050"
    ):
        seen.update(json.loads(row[0])["discovery_incident_ids"])
    for row in connection.execute(
        "SELECT payload_json FROM knowledge_development_v050"
    ):
        seen.update(json.loads(row[0])["incident_ids"])
    bindings = list(
        connection.execute(
            "SELECT incident_id,episode_id FROM knowledge_episode_incidents_v050"
        )
    )
    episodes = {r["episode_id"] for r in bindings if r["incident_id"] in seen}
    return seen | {r["incident_id"] for r in bindings if r["episode_id"] in episodes}


def expose(connection, incident_ids, purpose):
    connection.executemany(
        "INSERT OR IGNORE INTO knowledge_exposure_v050 VALUES (?,?)",
        [(i, purpose) for i in incident_ids],
    )


def split_digest(connection, environment_id):
    manifest = effective_manifest(connection, environment_id)
    return None if manifest is None else semantic_sha256_v22(manifest)


def require_independent(connection, incident_ids, minimum=2):
    episodes = []
    for iid in incident_ids:
        row = connection.execute(
            "SELECT episode_id FROM knowledge_episode_incidents_v050 WHERE incident_id=?",
            (iid,),
        ).fetchone()
        if row is None:
            raise ValueError("independent episode identity missing")
        episodes.append(row[0])
    if len(set(episodes)) != len(episodes) or len(set(episodes)) < minimum:
        raise ValueError(
            "independent episode denominator would be duplicated or insufficient"
        )
