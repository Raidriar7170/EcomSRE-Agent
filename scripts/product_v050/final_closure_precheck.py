"""Read-only retained-input and non-owned-resource precheck; never admits a baseline.

Print only safe projections. No lifecycle constructors, Provider, migrations,
candidate checks, or Docker mutations are invoked.
"""

from datetime import UTC, datetime
import json

from scripts.product.minimal_payment_acceptance_v040.owned import command, inventory
from scripts.product_v050.knowledge_feasibility import DATA, material, audit
from scripts.product_v050.preflight import inspect


def check():
    retained = json.loads(
        (DATA / "knowledge-feasibility/d-precheck-private.json").read_text()
    )
    admission = json.loads(
        (DATA / "live-02/postgres-user-01/admitted-baseline.json").read_text()
    )
    cleanup = json.loads((DATA / "live-02/postgres-user-01/cleanup.json").read_text())
    context = command("docker", "context", "show")
    endpoint = json.loads(command("docker", "context", "inspect", context))[0][
        "Endpoints"
    ]["docker"]["Host"]
    daemon = json.loads(command("docker", "info", "--format", "{{json .}}"))["ID"]
    current = inventory()
    previous = retained["inventory"]
    changes = {
        kind: {
            "added": len(set(rows) - set(previous[kind])),
            "removed": len(set(previous[kind]) - set(rows)),
            "changed": sum(
                rows[key] != previous[kind][key]
                for key in set(rows) & set(previous[kind])
            ),
        }
        for kind, rows in current.items()
    }
    networks = json.loads(command("docker", "network", "inspect", *current["network"]))
    network_extra = {
        row["Id"]: {k: row[k] for k in ("Attachable", "EnableIPv6", "Ingress", "Scope")}
        for row in networks
    }
    safety = {
        "counts": {k: len(v) for k, v in current.items()},
        "changes": changes,
        "inventory_unchanged": current == previous,
        "network_extra_unchanged": network_extra == retained["network_extra"],
        "context_unchanged": context == admission["context"],
        "endpoint_unchanged": endpoint == admission["endpoint"]
        and endpoint.startswith("unix://"),
        "daemon_unchanged": daemon == admission["daemon"],
        "old_owned_cleanup": cleanup["result"]["clean"],
        "new_baseline_admitted": False,
        "docker_mutations": 0,
    }
    safe = all(
        safety[k]
        for k in (
            "inventory_unchanged",
            "network_extra_unchanged",
            "context_unchanged",
            "endpoint_unchanged",
            "daemon_unchanged",
            "old_owned_cleanup",
        )
    )
    evo, roster, discovery = material()
    facts = audit(evo, roster, discovery)
    aliases = {r["incident_id"]: f"e{n:02}" for n, r in enumerate(roster, 1)}
    # Recompute prior candidate on its original inputs without consuming CHECKED.
    from ecomsre.product.knowledge.candidates_v050 import (
        CompiledKnowledge,
        candidate_components,
        evaluate_candidate,
        snapshot_observations,
    )
    from ecomsre.product.knowledge.observations_v050 import load_observations

    with evo.store.connect() as c:
        row = c.execute(
            "SELECT payload_json FROM knowledge_candidate_pool_v050 WHERE json_extract(payload_json,'$.source_request_key')=?",
            ("knowledge-draft-v050.2:proposal:1",),
        ).fetchone()
    candidate = CompiledKnowledge.model_validate_json(row[0])
    results = []
    for member in roster:
        iid = member["incident_id"]
        runtime = evo.knowledge._shadow_runtime_material(iid)
        evidence = evo.knowledge._evidence(
            iid, evo.knowledge._diagnosis(iid).diagnosis_id
        )
        observations = snapshot_observations(
            [e.payload for e in evidence.objects if "connector_result" in e.payload],
            runtime.runtime_input.memory,
        ) + load_observations(runtime.incident, evo.investigations.objects)
        arguments = dict(
            memory=runtime.runtime_input.memory,
            anomalies=runtime.runtime_input.generic_anomalies,
            observations=observations,
            incident_end=runtime.incident.diagnosis_observed_at,
        )
        outcome = evaluate_candidate(
            candidate, target=candidate.proposal.target, **arguments
        )
        results.append(
            dict(
                episode=aliases[iid],
                role=member["role"],
                status=outcome.status,
                reason=outcome.reason,
                components=candidate_components(candidate, **arguments),
            )
        )
    ledger = inspect(DATA)
    episodes = len(list(DATA.glob("live-*/**/episodes/*/started.json")))
    return {
        "schema_version": "ecomsre.product.final-learning-precheck.v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "boundary": "READ_ONLY_PRECHECK_NOT_MECHANICAL_OR_LEARNING_ACCEPTANCE",
        "safety": safety,
        "resource_continuity_passed": safe,
        "live_start_permitted": False,
        "admission": "NOT_EVALUATED_BY_THIS_COMMAND",
        "input_snapshot_sha256": facts["snapshot_sha256"],
        "old_candidate_sha256": candidate.compiled_sha256,
        "old_candidate_recomputed": results,
        "provider_requests": ledger["provider_request_count"],
        "committed_microusd": ledger["committed_upper_microusd"],
        "reported_token_cost_microusd": ledger["reported_cost_microusd"],
        "unknown_usage_requests": ledger["unknown_usage_requests"],
        "invoice_known": False,
        "live_episodes": episodes,
        "effective_new_caps": {
            "requests": min(40, 200 - ledger["provider_request_count"]),
            "committed_microusd": min(
                8_000_000, 20_000_000 - ledger["committed_upper_microusd"]
            ),
            "live_episodes": min(7, 12 - episodes),
            "semantic_attempts": 6,
        },
        "new_requests": 0,
        "new_live_episodes": 0,
        "new_semantic_attempts": 0,
        "proposer_feedback_dispatched": False,
    }


if __name__ == "__main__":
    print(json.dumps(check(), indent=2, sort_keys=True))
