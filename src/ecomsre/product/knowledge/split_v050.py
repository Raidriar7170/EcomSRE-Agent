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
        """)


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
        row = c.execute(
            "SELECT manifest_json FROM knowledge_split_v050 WHERE environment_id=?",
            (incident.environment_id,),
        ).fetchone()
        if row is None or episode_id not in json.loads(row[0]):
            raise ValueError("episode is not in frozen split")
        old = c.execute(
            "SELECT environment_id,episode_id FROM knowledge_episode_incidents_v050 WHERE incident_id=?",
            (incident.incident_id,),
        ).fetchone()
        if old:
            if tuple(old) != (incident.environment_id, episode_id):
                raise ValueError("incident episode binding is immutable")
            return
        c.execute(
            "INSERT INTO knowledge_episode_incidents_v050 VALUES (?,?,?)",
            (incident.incident_id, incident.environment_id, episode_id),
        )
        c.execute("COMMIT")


def require_roles(connection, incident_ids, allowed):
    for iid in incident_ids:
        row = connection.execute(
            "SELECT b.episode_id,s.manifest_json FROM knowledge_episode_incidents_v050 b JOIN knowledge_split_v050 s USING(environment_id) WHERE incident_id=?",
            (iid,),
        ).fetchone()
        if (
            row is None
            or json.loads(row["manifest_json"])[row["episode_id"]] not in allowed
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
    row = connection.execute(
        "SELECT manifest_json FROM knowledge_split_v050 WHERE environment_id=?",
        (environment_id,),
    ).fetchone()
    return None if row is None else semantic_sha256_v22(json.loads(row[0]))


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
