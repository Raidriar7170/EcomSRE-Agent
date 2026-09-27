"""One explicitly authorized revalidation of an unchanged, revoked model rule.

Not a new proposal: the only changed compiled field is the test registration ID.
Old selection, development, freeze, evaluation and registration rows are retained.
"""

from datetime import UTC, datetime
import json

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22 as sha
from ecomsre.product.knowledge.candidates_v050 import CompiledKnowledge
from ecomsre.product.knowledge import split_v050
from ecomsre.product.knowledge.selection_lock_v050 import collection_range
from ecomsre.product.knowledge.shadow_controls_v050 import CONTROL_VERSION
from ecomsre.product.investigation.closure_budget import ledger

BATCH = "fixed-rule-independent-validation-20260927"
TABLE = "knowledge_validation_batch_v050"


def load(c, registration_id=None):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone():
        return None
    row = c.execute(
        "SELECT payload_json FROM knowledge_validation_batch_v050 WHERE batch_id=?",
        (BATCH,),
    ).fetchone()
    if row is None:
        return None
    value = json.loads(row[0])
    if value["sha256"] != sha({k: v for k, v in value.items() if k != "sha256"}):
        raise ValueError("validation batch digest differs")
    return (
        value
        if registration_id is None or value["registration_id"] == registration_id
        else None
    )


def retained(c, parent_id):
    tables = (
        "knowledge_candidate_pool_v050",
        "knowledge_development_v050",
        "knowledge_shadow_details_v050",
        "environment_extension_registrations",
    )
    result = {
        t: [
            dict(r)
            for r in c.execute(
                f"SELECT * FROM {t} WHERE registration_id=?", (parent_id,)
            )
        ]
        for t in tables
    }
    result["selection"] = [
        dict(r) for r in c.execute("SELECT * FROM knowledge_selection_lock_v050")
    ]
    result["provider"] = ledger(c)
    return result


def install(evo, parent_id, *, plan, authorization_sha256, episode_ledger):
    from ecomsre.product.knowledge.evolution_v050 import evaluation_bindings
    from ecomsre.product.knowledge.control_qualification_v050 import VERSION
    from ecomsre.product.knowledge.selection_lock_v050 import load as original_lock

    if len(authorization_sha256) != 64:
        raise ValueError("explicit authorization digest required")
    if set(plan) != {
        "holdout_episodes",
        "recurrence_episode",
        "collection",
        "time_range",
        "derived_controls_version",
    }:
        raise ValueError("fixed validation plan required")
    if (
        len(plan["holdout_episodes"]) != 3
        or set(plan["holdout_episodes"].values())
        != {"POSITIVE_INCIDENT", "NO_INCIDENT", "CONFUSABLE_CORE_KNOWN"}
        or plan["derived_controls_version"] != CONTROL_VERSION
    ):
        raise ValueError("three fixed original strata required")
    start, end = collection_range(plan)
    if end <= datetime.now(UTC) or not plan["collection"]:
        raise ValueError("future bounded collection required")
    with evo.store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        if load(c) is not None:
            raise ValueError("one validation batch only; no retries")
        old = retained(c, parent_id)
        rows = old["knowledge_candidate_pool_v050"]
        if len(rows) != 1 or rows[0]["state"] != "REVOKED":
            raise ValueError("retained revoked parent required")
        parent = CompiledKnowledge.model_validate_json(rows[0]["payload_json"])
        if (
            "ingestion" in plan["collection"]
            and parent.origin == "LLM"
            and plan["collection"].get("target_service") != parent.proposal.target
        ):
            raise ValueError("future preparation must bind fixed target service")
        if parent.origin not in {"LLM", "FIXTURE_ONLY"}:
            raise ValueError("original model or fixture provenance required")
        lock = original_lock(c)
        gate = c.execute(
            "SELECT payload_json FROM knowledge_closure_runner_v050 WHERE entry_key=?",
            ("development:" + parent_id,),
        ).fetchone()
        if lock is None or lock["registration_id"] != parent_id or gate is None:
            raise ValueError("original selected development required")
        gate = json.loads(gate[0])
        if (
            gate["sha256"] != sha(gate["value"])
            or not gate["value"]["passed"]
            or gate["value"]["candidate_sha256"] != parent.compiled_sha256
            or lock["bindings"]["candidate"] != parent.model_dump(mode="json")
        ):
            raise ValueError("original complete development gate required")
        if parent.origin == "LLM" and (
            len(episode_ledger) != 13
            or not any(
                r["call_key"] == parent.source_request_key and r["state"] == "COMPLETED"
                for r in ledger(c)
            )
        ):
            raise ValueError(
                "exact retained live ledger and completed model source required"
            )
        old_manifest = split_v050.effective_manifest(c, parent.environment_id)
        additions = {k: "HOLDOUT" for k in plan["holdout_episodes"]} | {
            plan["recurrence_episode"]: "REUSE"
        }
        if len(additions) != 4 or set(additions) & set(old_manifest):
            raise ValueError("four new event identities required")
        payload = parent.model_dump(mode="json", exclude={"compiled_sha256"})
        payload["registration_id"] = (
            "registration-validation-" + sha(dict(parent=parent_id, batch=BATCH))[:24]
        )
        candidate = CompiledKnowledge.model_validate(
            payload | {"compiled_sha256": sha(payload)}
        )
        development = json.loads(old["knowledge_development_v050"][0]["payload_json"])
        if (
            development["state"] != "CHECKED"
            or development["candidate_sha256"] != parent.compiled_sha256
        ):
            raise ValueError("parent development identity differs")
        development = development | dict(
            candidate_sha256=candidate.compiled_sha256,
            parent_registration_id=parent_id,
            parent_development_sha256=sha(development),
            claim="INHERITED_UNCHANGED_RULE_DEVELOPMENT_NOT_NEW_MODEL_GENERATION",
        )
        value = dict(
            batch_id=BATCH,
            registration_id=candidate.registration_id,
            parent_registration_id=parent_id,
            environment_id=parent.environment_id,
            parent_retained_sha256=sha(old),
            parent_compiled_sha256=parent.compiled_sha256,
            rule_semantic_sha256=sha(parent.proposal.model_dump(mode="json")),
            source_request_key=parent.source_request_key,
            authorization_sha256=authorization_sha256,
            plan=plan,
            additions=additions,
            parent_split_sha256=sha(old_manifest),
            selected_at=datetime.now(UTC).isoformat(),
            preexisting_incident_ids=sorted(
                r[0] for r in c.execute("SELECT incident_id FROM incidents")
            ),
            evaluator=evaluation_bindings(),
            qualification_version=VERSION,
            provider_ledger=ledger(c),
            cumulative_live_limit=17,
            round_live_limit=12,
            original_episode_ledger=episode_ledger,
            new_live_limit=4,
            new_provider_limit=0,
            new_semantic_limit=0,
            candidate=candidate.model_dump(mode="json"),
        )
        value["sha256"] = sha(value)
        c.execute(
            "CREATE TABLE IF NOT EXISTS knowledge_validation_batch_v050 (batch_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL)"
        )
        c.execute(
            "INSERT INTO knowledge_validation_batch_v050 VALUES (?,?)",
            (BATCH, json.dumps(value, sort_keys=True)),
        )
        c.execute(
            "INSERT INTO knowledge_candidate_pool_v050 VALUES (?,?,?,?,'DRAFT',NULL,NULL)",
            (
                candidate.registration_id,
                candidate.environment_id,
                candidate.compiled_sha256,
                candidate.model_dump_json(),
            ),
        )
        c.execute(
            "INSERT INTO knowledge_development_v050 VALUES (?,?)",
            (candidate.registration_id, json.dumps(development, sort_keys=True)),
        )
        c.execute("COMMIT")
        return candidate


def verify(c, value):
    if c.execute(
        "SELECT 1 FROM knowledge_closure_runner_v050 WHERE entry_key=?",
        (BATCH + ":stop",),
    ).fetchone():
        raise ValueError("validation batch stopped; no retry")
    from ecomsre.product.knowledge.evolution_v050 import evaluation_bindings

    if (
        sha(retained(c, value["parent_registration_id"]))
        != value["parent_retained_sha256"]
        or ledger(c) != value["provider_ledger"]
    ):
        raise ValueError("retained history or zero Provider bound changed")
    if evaluation_bindings() != value["evaluator"]:
        raise ValueError("validation execution version changed")


def require_identity(c, candidate, cases, version):
    value = load(c, candidate.registration_id)
    if value is None:
        return None
    verify(c, value)
    if (
        candidate.model_dump(mode="json") != value["candidate"]
        or version != value["plan"]["derived_controls_version"]
    ):
        raise ValueError("fixed validation rule or protocol changed")
    collection_receipts(c, value, cases)
    selected = {}
    for iid, stratum in cases.items():
        row = c.execute(
            "SELECT episode_id,environment_id FROM knowledge_episode_incidents_v050 WHERE incident_id=?",
            (iid,),
        ).fetchone()
        if (
            iid in value["preexisting_incident_ids"]
            or row is None
            or row["environment_id"] != candidate.environment_id
            or row["episode_id"] in selected
        ):
            raise ValueError("new independent validation incidents required")
        selected[row["episode_id"]] = stratum
    if selected != value["plan"]["holdout_episodes"]:
        raise ValueError("validation cases differ from fixed plan")
    return value["sha256"]


def install_deployment(store, *, new, new_deployment):
    """Append one owned deployment binding without changing the old successor."""
    from ecomsre.product.knowledge.capability_successor_v050 import (
        load as successor,
        validate_pair,
    )
    from ecomsre.product.environment.repository import EnvironmentRepositoryV1

    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        value = load(c)
        if value is None:
            raise ValueError("validation authorization must precede deployment")
        verify(c, value)
        if c.execute(
            "SELECT 1 FROM knowledge_closure_runner_v050 WHERE entry_key LIKE ?",
            (BATCH + ":episode:%",),
        ).fetchone():
            raise ValueError("deployment must precede slot reservation")
        if c.execute(
            "SELECT 1 FROM knowledge_episode_incidents_v050 WHERE episode_id IN (?,?,?,?)",
            tuple(value["additions"]),
        ).fetchone():
            raise ValueError("deployment must precede all new observations")
        prior = successor(c, value["environment_id"])
        if prior is None:
            raise ValueError("retained deployment successor required")
        pairs = (
            [
                dict(matrix=prior["old"], deployment=prior["old_deployment"]),
                dict(matrix=prior["new"], deployment=prior["new_deployment"]),
            ]
            + prior.get("prior_developments", [])
            + [
                prior[k]
                for k in ("retained_development", "earlier_development")
                if k in prior
            ]
        )
        for pair in pairs:
            validate_pair(pair["matrix"], new, pair["deployment"], new_deployment)
        current = json.loads(
            c.execute(
                "SELECT payload_json FROM environment_capability_matrices WHERE environment_id=?",
                (value["environment_id"],),
            ).fetchone()[0]
        )
        if (
            current != new.model_dump(mode="json")
            or EnvironmentRepositoryV1(store).get(value["environment_id"])
            != new_deployment.environment
        ):
            raise ValueError("actual verified deployment binding required")
        record = dict(
            batch_sha256=value["sha256"],
            prior_successor_sha256=prior["sha256"],
            new=new.model_dump(mode="json"),
            deployment=new_deployment.model_dump(mode="json"),
            retained_pairs=pairs,
        )
        record["sha256"] = sha(record)
        c.execute(
            "CREATE TABLE IF NOT EXISTS knowledge_validation_deployment_v050 (batch_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL)"
        )
        c.execute(
            "INSERT INTO knowledge_validation_deployment_v050 VALUES (?,?)",
            (BATCH, json.dumps(record, sort_keys=True)),
        )
        c.execute("COMMIT")
        return record


def deployment_admits(c, store, *, environment_id, expected, actual):
    """None means no new deployment; False must never fall back to old mapping."""
    if not c.execute(
        "SELECT 1 FROM sqlite_master WHERE name='knowledge_validation_deployment_v050'"
    ).fetchone():
        return None
    row = c.execute(
        "SELECT payload_json FROM knowledge_validation_deployment_v050 WHERE batch_id=?",
        (BATCH,),
    ).fetchone()
    if row is None:
        return None
    from ecomsre.product.environment.repository import EnvironmentRepositoryV1

    v = json.loads(row[0])
    b = load(c)
    if (
        v["sha256"] != sha({k: x for k, x in v.items() if k != "sha256"})
        or b is None
        or v["batch_sha256"] != b["sha256"]
    ):
        raise ValueError("validation deployment binding differs")
    permitted = {p["matrix"]["capability_sha256"] for p in v["retained_pairs"]} | {
        v["new"]["capability_sha256"]
    }
    current = c.execute(
        "SELECT payload_json FROM environment_capability_matrices WHERE environment_id=?",
        (environment_id,),
    ).fetchone()
    return (
        environment_id == b["environment_id"]
        and {expected, actual} <= permitted
        and current is not None
        and json.loads(current[0]) == v["new"]
        and EnvironmentRepositoryV1(store).get(environment_id).model_dump(mode="json")
        == v["deployment"]["environment"]
    )


def receipt(c, slot, kind):
    row = c.execute(
        "SELECT payload_json FROM knowledge_closure_runner_v050 WHERE entry_key=?",
        (BATCH + ":" + kind + ":" + slot,),
    ).fetchone()
    if row is None:
        raise ValueError("validation collection receipt missing: " + kind + ":" + slot)
    wrapped = json.loads(row[0])
    if wrapped["sha256"] != sha(wrapped["value"]):
        raise ValueError("validation collection receipt changed")
    return wrapped["value"]


def collection_receipts(c, value, cases):
    previous_collected = None
    strata = {
        "N4": "POSITIVE_INCIDENT",
        "N5": "NO_INCIDENT",
        "N6": "CONFUSABLE_CORE_KNOWN",
    }
    for slot in ("N4", "N5", "N6"):
        reservation = receipt(c, slot, "episode")
        prep = receipt(c, slot, "preparation")
        binding = receipt(c, slot, "binding")
        terminal = receipt(c, slot, "terminal")
        collected = receipt(c, slot, "collection")
        if (
            binding["incident_id"] not in cases
            or not terminal["succeeded"]
            or collected["incident_id"] != binding["incident_id"]
            or collected["preparation_sha256"] != sha(prep)
            or collected["reservation_sha256"] != sha(reservation)
            or value["plan"]["holdout_episodes"].get(reservation["episode_id"])
            != strata[slot]
            or cases.get(binding["incident_id"]) != strata[slot]
        ):
            raise ValueError("validation collection boundary differs")
        if value["candidate"]["origin"] != "FIXTURE_ONLY":
            from scripts.product_v050.validation_capture import earliest

            computed = earliest(
                value["plan"]["collection"],
                last_recovery=prep["last_recovery"],
                last_change=prep["last_change"],
                ready_at=prep["ready_at"],
            )
            if computed.isoformat() != prep["earliest_legal_observation"]:
                raise ValueError("fixed isolation calculation differs")
            duration = datetime.fromisoformat(
                prep["deadline"]
            ) - datetime.fromisoformat(prep["start"]["utc"])
            if (
                duration.total_seconds()
                > value["plan"]["collection"]["preparation_cap_seconds"] + 1
            ):
                raise ValueError("preparation stop cap changed")
        reserved = datetime.fromisoformat(reservation["reserved_at"])
        if previous_collected is not None and reserved <= previous_collected:
            raise ValueError("validation event order differs")
        previous_collected = datetime.fromisoformat(collected["sealed_at"])
        if (
            not datetime.fromisoformat(prep["earliest_legal_observation"])
            <= reserved
            <= datetime.fromisoformat(prep["deadline"])
        ):
            raise ValueError("validation isolation/deadline not met")


def verify_collection_objects(evo, candidate, cases):
    verify_ingestion_cases(evo, candidate, cases)
    with evo.store.connect() as c:
        value = load(c, candidate.registration_id)
        if value is None:
            return
        collection_receipts(c, value, cases)
        current = json.loads(
            c.execute(
                "SELECT payload_json FROM environment_capability_matrices WHERE environment_id=?",
                (candidate.environment_id,),
            ).fetchone()[0]
        )
        compatibility = deployment_admits(
            c,
            evo.store,
            environment_id=candidate.environment_id,
            expected=candidate.capability_sha256,
            actual=current["capability_sha256"],
        )
        if compatibility is False or (
            candidate.origin == "LLM" and compatibility is not True
        ):
            raise ValueError("new actual deployment binding required")
        for slot in ("N4", "N5", "N6"):
            collected = receipt(c, slot, "collection")
            retained_index = json.loads(
                evo.investigations.objects.read_bytes(collected["raw_index_sha256"])
            )
            if retained_index["mode"] == "FIXTURE_ONLY":
                if candidate.origin != "FIXTURE_ONLY":
                    raise ValueError("fixture capture cannot establish real collection")
            else:
                artifacts = retained_index.get("operation_artifacts", {})
                if not {
                    "result.json",
                    "control-intent.json",
                    "restore-intent.json",
                    "incident-request.json",
                    "incident-response.json",
                } <= set(artifacts):
                    raise ValueError("private operation/readback artifacts missing")
                for digest in artifacts.values():
                    evo.investigations.objects.read_bytes(digest)
                entries = retained_index["entries"]
                if (
                    not entries
                    or not collected.get(
                        "ingestion_receipt_sha256"
                        if "ingestion" in value["plan"]["collection"]
                        else "scrape_receipt_sha256",
                    )
                    or any(e["truncated"] for e in entries)
                ):
                    raise ValueError("complete raw acquisition required")
                from scripts.product_v050.validation_capture import verify_raw

                scrape = json.loads(
                    evo.investigations.objects.read_bytes(
                        collected["ingestion_receipt_sha256"]
                        if "ingestion" in value["plan"]["collection"]
                        else collected["scrape_receipt_sha256"]
                    )
                )
                snapshots = {
                    o.action_id: o.payload
                    for o in evo.knowledge._evidence(
                        collected["incident_id"],
                        evo.knowledge._diagnosis(collected["incident_id"]).diagnosis_id,
                    ).objects
                    if "connector_result" in o.payload
                }
                verify_raw(
                    entries,
                    occurrence=receipt(c, slot, "episode")["episode_id"],
                    incident_id=collected["incident_id"],
                    snapshots=snapshots.values(),
                    queries=value["plan"]["collection"]["actual_queries"],
                    scrape=scrape,
                    ingestion=value["plan"]["collection"].get("ingestion"),
                    read_bytes=evo.investigations.objects.read_bytes,
                )
                if "ingestion" not in value["plan"]["collection"]:
                    evo.investigations.objects.read_bytes(scrape["object_sha256"])
                for entry in entries:
                    evo.investigations.objects.read_bytes(
                        entry["response_object_sha256"]
                    )
            if (
                collected["diagnosis_sha256"]
                != evo.knowledge._diagnosis(collected["incident_id"]).result_sha256
            ):
                raise ValueError("normal diagnosis capture binding changed")


def verify_ingestion_preparation(evo, value):
    """Read-only validation of the future protocol's pre-fault readiness proof."""
    from scripts.product_v050.ingestion_evidence import verify_receipt

    collection = value["plan"]["collection"]
    if "ingestion" not in collection:
        return
    with evo.store.connect() as c:
        verify(c, value)
        row = c.execute(
            "SELECT payload_json FROM knowledge_closure_runner_v050 WHERE entry_key=?",
            (BATCH + ":ingestion-preparation",),
        ).fetchone()
        if row is None:
            raise ValueError("pre-fault ingestion preparation required")
        wrapped = json.loads(row[0])
        if sha(wrapped["value"]) != wrapped["sha256"]:
            raise ValueError("ingestion preparation digest differs")
        proof = wrapped["value"]
        attempt_row = c.execute(
            "SELECT payload_json FROM knowledge_closure_runner_v050 WHERE entry_key=?",
            (BATCH + ":ingestion-preparation-attempt",),
        ).fetchone()
        if attempt_row is None:
            raise ValueError("sealed preparation attempt missing")
        attempt = json.loads(attempt_row[0])
        if (
            attempt["sha256"] != sha(attempt["value"])
            or attempt["value"]["batch_sha256"] != value["sha256"]
        ):
            raise ValueError("preparation attempt binding differs")
    objects = evo.investigations.objects
    entries = json.loads(objects.read_bytes(proof["raw_index_sha256"]))
    requirements = proof["requirements"]
    required_keys = {
        k
        for k in collection["actual_queries"]
        if ":queue_lag:" not in k
        or collection.get("target_service") is None
        or k.endswith(":" + collection["target_service"])
    }
    if (
        not required_keys <= set(collection["preparation_query_keys"])
        or sorted(r["query_key"] for r in requirements)
        != collection["preparation_query_keys"]
    ):
        raise ValueError("preparation query coverage differs")
    if len({r["end"] for r in requirements}) != 1 or any(
        r["end"] - r["start"] != 300 for r in requirements
    ):
        raise ValueError("fixed preparation window differs")
    anchor = datetime.fromisoformat(attempt["value"]["at"]["utc"])
    start, end = collection_range(value["plan"])
    if not start <= anchor <= end or requirements[0]["end"] != anchor.timestamp():
        raise ValueError("preparation window is not this batch attempt")
    if (
        not entries
        or len(entries) > collection["ingestion"]["max_requests"]
        or any(
            not anchor
            <= datetime.fromisoformat(e["requested_at"])
            <= datetime.fromisoformat(e["received_at"])
            <= end
            or (datetime.fromisoformat(e["received_at"]) - anchor).total_seconds()
            > collection["ingestion"]["max_seconds"]
            for e in entries
        )
    ):
        raise ValueError("preparation request time/cap differs")
    first_request = min(
        datetime.fromisoformat(e["requested_at"]).timestamp() for e in entries
    )
    if (
        not 0
        <= first_request - requirements[0]["end"]
        <= collection["ingestion"]["max_sample_age_seconds"]
    ):
        raise ValueError("preparation evaluation time is not current")
    verify_receipt(
        proof,
        binding=collection["ingestion"],
        entries=entries,
        occurrence="INGESTION_READINESS_NOT_EPISODE",
        incident_id=None,
        queries=collection["actual_queries"],
        read_bytes=objects.read_bytes,
        requirements=requirements,
    )
    return proof


def verify_ingestion_cases(evo, candidate, cases):
    """v2 evidence is required even by direct qualification, before matching."""
    with evo.store.connect() as c:
        value = load(c, candidate.registration_id)
        if value is None or "ingestion" not in value["plan"]["collection"]:
            return
        verify(c, value)
    preparation = verify_ingestion_preparation(evo, value)
    objects = evo.investigations.objects
    from scripts.product_v050.validation_capture import verify_raw

    for iid in cases:
        with evo.store.connect() as c:
            slot = next(
                (
                    s
                    for s in ("N4", "N5", "N6", "N7")
                    if c.execute(
                        "SELECT 1 FROM knowledge_closure_runner_v050 WHERE entry_key=?",
                        (BATCH + ":binding:" + s,),
                    ).fetchone()
                    and receipt(c, s, "binding")["incident_id"] == iid
                ),
                None,
            )
            if slot is None:
                raise ValueError("ingestion event binding missing")
            collected = receipt(c, slot, "collection")
            reservation = receipt(c, slot, "episode")
        proof = json.loads(objects.read_bytes(collected["ingestion_receipt_sha256"]))
        entries = json.loads(objects.read_bytes(collected["raw_index_sha256"]))[
            "entries"
        ]
        prep_entries = json.loads(objects.read_bytes(preparation["raw_index_sha256"]))
        if max(
            datetime.fromisoformat(e["received_at"]) for e in prep_entries
        ) >= datetime.fromisoformat(reservation["reserved_at"]):
            raise ValueError("ingestion preparation must precede fault reservation")
        snapshots = [
            o.payload
            for o in evo.knowledge._evidence(
                iid, evo.knowledge._diagnosis(iid).diagnosis_id
            ).objects
            if "connector_result" in o.payload
        ]
        verify_raw(
            entries,
            occurrence=reservation["episode_id"],
            incident_id=iid,
            snapshots=snapshots,
            queries=value["plan"]["collection"]["actual_queries"],
            scrape=proof,
            ingestion=value["plan"]["collection"]["ingestion"],
            read_bytes=objects.read_bytes,
        )
