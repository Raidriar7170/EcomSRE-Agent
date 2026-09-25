"""One Goal's owned deployment continuation; no new environment or query policy."""

from copy import deepcopy
import json
import time

from scripts.product_v050.live_environment import (
    Owned,
    load,
    save,
    digest,
    resource_limit_values,
)
from scripts.product_v050.live_product import Campaign, DATA
from ecomsre.product.knowledge.capability_successor_v050 import DeploymentV050, install
from ecomsre.product.pilot.runtime_authority_v02 import (
    write_pilot_runtime_authority_v02,
)

ROOT = DATA / "live-final-closure-03"
ROUND = DATA / "live-final-closure-02"
FAILED = ROUND
OLD = DATA / "live-02/postgres-user-01"
DEPENDENCY = dict(
    template="RESOURCE_USAGE_SAMPLES",
    window_offset_seconds=30,
    query_window_seconds=30,
    sampling_window_seconds=10,
    sample_count=5,
)


def retained_owner():
    """Reauthenticate exact retained births, never discover ownership by name."""
    owner = object.__new__(Owned)
    owner.root = ROOT
    manifest = load("manifest.json", ROOT)
    admission = load("admitted-baseline.json", ROOT)
    if digest(admission) != manifest["admitted_baseline_sha256"]:
        raise ValueError("ADMITTED_BASELINE_BINDING")
    before = load("before.json", ROOT)
    if (
        before["inventory"] != admission["inventory"]
        or before["daemon_id"] != admission["daemon"]
    ):
        raise ValueError("RETAINED_OWNER_BASELINE_DIFFERS")
    owner.nonce, owner.labels = manifest["campaign"], manifest["labels"]
    owner.before, owner.daemon = admission["inventory"], admission["daemon"]
    owner.context, owner.expected_context = admission["context"], admission["endpoint"]
    owner.network_extra = admission["network_extra"]
    owner.plan, owner.images = (
        load("compose.json", ROOT),
        load("cached-images.json", ROOT),
    )
    owner.births = {k: {} for k in ("container", "network", "volume")}
    for path in sorted((ROOT / "births").glob("*.json")):
        kind = path.name.split("-", 1)[0]
        row = json.loads(path.read_text())
        owner.births[kind][row.get("Id", row.get("Name"))] = row
    owner.containers = {
        row["Config"]["Labels"]["com.docker.compose.service"]: cid
        for cid, row in owner.births["container"].items()
    }
    if set(owner.containers) != set(owner.plan["services"]):
        raise ValueError("RETAINED_OWNER_SERVICE_SET_DIFFERS")
    owner.verify()
    return owner


def deployment(root, environment, service_ids):
    """Full private births retain paths/IDs; compare executed service semantics."""
    births = {k: {} for k in ("container", "network", "volume")}
    for path in sorted((root / "births").glob("*.json")):
        row = json.loads(path.read_text())
        births[path.name.split("-", 1)[0]][row.get("Id", row.get("Name"))] = row
    containers = {
        r["Config"]["Labels"]["com.docker.compose.service"]: r
        for r in births["container"].values()
    }
    plan = load("compose.json", root)
    if set(containers) != set(plan["services"]):
        raise ValueError("DEPLOYMENT_BIRTHS_INCOMPLETE")
    for name, row in containers.items():
        if row["Name"] != "/" + plan["services"][name]["container_name"]:
            raise ValueError("DEPLOYMENT_SELECTOR_DIFFERS")
    queries, semantics, runtime = {}, {}, {}
    for c in environment.connector_configs:
        settings = deepcopy(c.settings)
        if c.kind.value == "PILOT_RUNTIME":
            runtime = {k: settings.pop(k) for k in ("snapshot_ref", "authority_sha256")}
        semantics[c.name] = dict(
            kind=c.kind.value,
            endpoint=c.endpoint,
            settings=settings,
            credential_refs=c.credential_refs,
        )
        for key, template in settings.get("query_templates", {}).items():
            for service in service_ids:
                queries[c.name + ":" + key + ":" + service] = template.replace(
                    "{service}", service
                )
    limit_keys = (
        "Memory",
        "MemorySwap",
        "NanoCpus",
        "CpuPeriod",
        "CpuQuota",
        "CpusetCpus",
        "PidsLimit",
        "ShmSize",
        "Ulimits",
        "OomKillDisable",
    )
    trust_keys = (
        "Privileged",
        "PidMode",
        "ReadonlyRootfs",
        "CapDrop",
        "CapAdd",
        "SecurityOpt",
        "PortBindings",
    )
    trust = {}
    for name, row in containers.items():
        trust[name] = {k: row["HostConfig"].get(k) for k in trust_keys}
        trust[name]["user"] = row["Config"].get("User")
        trust[name]["mounts"] = sorted(
            [
                dict(type=m["Type"], target=m["Destination"], writable=m["RW"])
                for m in row["Mounts"]
            ],
            key=lambda m: m["target"],
        )
    return DeploymentV050(
        mode="OWNED_LOCAL",
        deployment_id=load("manifest.json", root)["campaign"],
        environment=environment,
        resource_births=births,
        runtime_binding=runtime,
        images={name: row["Image"] for name, row in containers.items()},
        service_mapping={
            **service_ids,
            **{"selector:" + name: row["Name"] for name, row in containers.items()},
        },
        actual_queries=queries,
        units={"cpu_percent": "percent", "memory_bytes": "bytes"},
        windows_and_sampling={"fixed_resource_dependency": DEPENDENCY},
        resource_limits={
            name: resource_limits(row["HostConfig"], limit_keys)
            for name, row in containers.items()
        },
        trust_boundary=trust,
        connector_semantics=semantics,
    )


def resource_limits(host, keys):
    return resource_limit_values(host, keys)


def rebind_runtime(store, environment_id, previous, settings):
    if set(previous) != set(settings) or any(
        previous[k] != settings[k]
        for k in previous
        if k not in {"snapshot_ref", "authority_sha256"}
    ):
        raise ValueError("RUNTIME_ONLY_IDENTITY_CHANGE_REQUIRED")
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT connector_config_id,settings_json FROM connector_configs WHERE environment_id=? AND kind='PILOT_RUNTIME'",
            (environment_id,),
        ).fetchone()
        if row is None or json.loads(row["settings_json"]) != previous:
            raise ValueError("RUNTIME_BINDING_COMPARE_AND_SWAP_FAILED")
        connection.execute(
            "UPDATE connector_configs SET settings_json=? WHERE connector_config_id=?",
            (json.dumps(settings, sort_keys=True), row["connector_config_id"]),
        )
        connection.execute("COMMIT")


def bind_existing(campaign):
    """Reverify the same environment, preserving old objects and exact queries."""
    campaign.env = load("environment.json", OLD)["environment_id"]
    app = campaign.app
    before = app.state.environments.get(campaign.env)
    current_capability = app.state.capabilities.get(campaign.env)
    if before.model_dump(mode="json") != load(
        "environment.json", FAILED
    ) or current_capability.model_dump(mode="json") != load(
        "capabilities.json", FAILED
    ):
        raise ValueError("RETAINED_FAILED_DEPLOYMENT_CHANGED")
    from ecomsre.product.contracts import EnvironmentRecordV1
    from ecomsre.product.environment.capabilities import EnvironmentCapabilityMatrixV1

    original_environment = EnvironmentRecordV1.model_validate(
        load("environment.json", OLD)
    )
    original_capability = EnvironmentCapabilityMatrixV1.model_validate(
        load("capabilities.json", OLD)
    )
    campaign.service_ids = {
        s.logical_service: s.service_id
        for s in app.state.services.get_map(campaign.env).services
    }
    old = deployment(OLD, original_environment, campaign.service_ids)
    save(
        ROOT / "original-environment.json", original_environment.model_dump(mode="json")
    )
    save(
        ROOT / "original-capabilities.json", original_capability.model_dump(mode="json")
    )
    save(ROOT / "original-deployment.json", old.model_dump(mode="json"))
    campaign.controller.read("BASELINE")
    campaign.traffic("initial", 3, 60001)
    time.sleep(30)
    campaign.runtime("initial")
    authority = campaign.authority(campaign.env)
    write_pilot_runtime_authority_v02(campaign.authority_path, authority)
    runtime = next(
        c for c in before.connector_configs if c.kind.value == "PILOT_RUNTIME"
    )
    settings = dict(
        runtime.settings,
        snapshot_ref=str(campaign.snapshot_path.relative_to(DATA)),
        authority_sha256=authority.connector_binding_sha256,
    )
    rebind_runtime(app.state.store, campaign.env, runtime.settings, settings)
    campaign.runtime("environment")
    campaign.work("/v1/environments/" + campaign.env + "/verify-jobs")
    after = app.state.environments.get(campaign.env)
    capability = app.state.capabilities.get(campaign.env)
    campaign.owner.verify()
    new = deployment(ROOT, after, campaign.service_ids)
    save(ROOT / "environment.json", after.model_dump(mode="json"))
    save(ROOT / "capabilities.json", capability.model_dump(mode="json"))
    save(ROOT / "deployment.json", new.model_dump(mode="json"))
    successor = install(
        app.state.store,
        old=original_capability,
        new=capability,
        old_deployment=old,
        new_deployment=new,
        unused_predecessor_sha256=load("successor.json", FAILED)["sha256"],
    )
    save(
        ROOT / "successor.json",
        {
            "sha256": successor,
            "actual_queries_unchanged": old.actual_queries == new.actual_queries,
        },
    )
    campaign.build_baseline("baseline", 60002)


def start():
    owner = Owned(ROOT)
    try:
        owner.start()
        campaign = Campaign(owner)
        try:
            bind_existing(campaign)
        finally:
            campaign.client.__exit__(None, None, None)
    except BaseException:
        owner.cleanup()
        raise


def attach(*, provider_enabled=False):
    campaign = Campaign(
        retained_owner(), continuation=True, provider_enabled=provider_enabled
    )
    campaign.env = load("environment.json", ROOT)["environment_id"]
    campaign.service_ids = {
        s.logical_service: s.service_id
        for s in campaign.app.state.services.get_map(campaign.env).services
    }
    return campaign


def runner_for(campaign):
    from scripts.product_v050.final_closure import ClosureRunner

    runner = ClosureRunner(campaign.evo, campaign.env, episode_root=DATA)
    path = ROUND / "closure-plan.json"
    if not path.is_file():
        raise ValueError("ORIGINAL_CLOSURE_PLAN_MISSING")
    runner.initialize(load("closure-plan.json", ROUND))
    if runner._get("feasibility") is None:
        runner.declare_level_a_feasibility(
            dict(
                n1_result_sha256=digest(load("result.json", ROUND / "episodes/N1")),
                cleanup_sha256=digest(load("cleanup.json", ROUND)),
                resource_window="NOT_COLLECTED_IRRECOVERABLE",
                incident_created=False,
                failure=load("result.json", ROUND / "episodes/N1")["error"],
            )
        )
    authorization = ROUND / "test-promotion-authorization.json"
    if not authorization.exists():
        import hashlib
        from scripts.product_v050.live_environment import REPO

        save(
            authorization,
            dict(
                status="PREAUTHORIZED_TEST_PROMOTION",
                environment_id=campaign.env,
                goal_sha256=hashlib.sha256(
                    (
                        REPO / "docs/goals/EcomSRE_v0.5_Final_Learning_Closure_Goal.md"
                    ).read_bytes()
                ).hexdigest(),
                authority="USER_ACTIVATED_FINAL_LEARNING_CLOSURE_GOAL",
                condition="only the selected real-model candidate after unchanged independent Shadow PASS",
                scope="existing enrolled isolated test registry only",
                model_tool_exposed=False,
            ),
        )
    return runner


def fixed_resource(campaign, iid):
    from ecomsre.product.incidents.read_backend import ProductReadBackendV1
    from ecomsre.product.connectors.registry import ConnectorRegistryV1
    from ecomsre.product.connectors.credentials import CredentialResolverV1
    from ecomsre.product.telemetry.metrics import ProductMetricsV1
    from ecomsre.product.investigation.reads import InvestigationReads
    from ecomsre.product.knowledge.observations_v050 import dependency_for

    app = campaign.app
    incident = app.state.incidents.get(iid)
    reads = InvestigationReads(
        incident=incident,
        environment=app.state.environments.get(campaign.env),
        identities=app.state.services.get_map(campaign.env),
        capabilities=app.state.capabilities.get(campaign.env),
        backend=ProductReadBackendV1(
            connectors=ConnectorRegistryV1(
                credential_resolver=CredentialResolverV1(),
                timeout_seconds=15,
                data_root=DATA,
            ),
            changes=app.state.changes,
            metrics=ProductMetricsV1(app.state.store),
            pilot_runtime_authority=campaign.authority(campaign.env),
        ),
        objects=app.state.object_store,
    )
    keys = [
        key
        for key, (a, w) in reads.entries.items()
        if a.target_services == ("fraud-detection",)
        and dependency_for(incident, a, w) == DEPENDENCY
    ]
    if len(keys) != 1:
        raise ValueError("EXACT_FIXED_RESOURCE_DEPENDENCY_UNAVAILABLE")
    campaign.owner.verify()
    return reads.read(keys[0])


def incident_payload(campaign, episode_id, start, end):
    from ecomsre.product.incidents.contracts import IncidentCreateV1
    from scripts.product_v050.live_product import SERVICES

    return IncidentCreateV1.model_validate(
        dict(
            environment_id=campaign.env,
            external_incident_key=episode_id,
            alert_name="service-observation",
            summary="Investigate available service observations.",
            started_at=start,
            ended_at=end,
            candidate_service_ids=sorted(campaign.service_ids[s] for s in SERVICES),
        )
    ).model_dump(mode="json")


def collect(campaign, runner, slot):
    from datetime import UTC, datetime
    import httpx
    from scripts.product_v050.live_product import stamp
    from ecomsre.product.pilot.baseline_readiness_v021 import (
        BoundedHealthyCheckoutTrafficV021,
        HealthyTrafficProfileV021,
    )

    document = {
        "N1": "QUEUE",
        "N2": "BASELINE",
        "N3": "PAYMENT",
        "N4": "QUEUE",
        "N5": "BASELINE",
        "N6": "PAYMENT",
        "N7": "QUEUE",
    }[slot]
    now = datetime.now(UTC).isoformat()
    incident_payload(campaign, runner.plan["slots"][slot], now, now)
    campaign.owner.verify()
    campaign.controller.read("BASELINE")
    campaign.runtime(slot + "-before")
    if campaign.lag()["lag"] >= 20:
        raise ValueError("EPISODE_HEALTHY_START_NOT_ESTABLISHED")
    reservation = runner.reserve_episode(slot)
    root = runner.episode_root / runner.plan["campaign"] / "episodes" / slot
    record = dict(
        slot=slot,
        start=stamp(),
        episode_id=reservation["episode_id"],
        status="STARTED",
        product_recovery_writes=0,
    )
    try:
        campaign.owner.verify()
        save(root / "control-intent.json", {"at": stamp(), "document": document})
        record["activated"] = campaign.controller.apply(document)
        campaign.owner.verify()
        with httpx.Client() as client:
            traffic = BoundedHealthyCheckoutTrafficV021(client=client).run(
                endpoint="http://127.0.0.1:18080/api/checkout",
                profile=HealthyTrafficProfileV021(
                    request_seed=61000 + int(slot[1:]),
                    maximum_request_count=3,
                    requests_per_second=1,
                    error_budget=3,
                ),
            )
        save(root / "traffic.json", traffic.model_dump(mode="json"))
        if traffic.attempted != 3 or (document != "PAYMENT" and traffic.failed):
            raise ValueError("EPISODE_TRAFFIC_INCOMPLETE")
        deadline = time.monotonic() + 120
        samples = []
        while time.monotonic() < deadline:
            samples.append(campaign.lag())
            time.sleep(min(10, max(0, deadline - time.monotonic())))
        save(root / "lag-during.json", samples)
        if (
            document == "QUEUE"
            and len({r["source_timestamp"] for r in samples if r["lag"] >= 20}) < 3
        ):
            raise ValueError("TARGET_EPISODE_NOT_OBSERVED")
        campaign.runtime(slot + "-during")
        payload = incident_payload(
            campaign,
            reservation["episode_id"],
            record["start"]["utc"],
            datetime.now(UTC).isoformat(),
        )
        save(root / "incident-request.json", payload)
        response = campaign.client.post("/v1/incidents", json=payload)
        save(
            root / "incident-response.json",
            dict(status_code=response.status_code, body=response.json()),
        )
        if response.status_code != 201:
            raise ValueError("INCIDENT_CREATE:" + str(response.status_code))
        iid = response.json()["incident_id"]
        record["incident_id"] = iid
        runner.bind_episode(slot, iid)
        campaign.work("/v1/incidents/" + iid + "/diagnosis-jobs")
        record["diagnosis"] = campaign.client.get(
            "/v1/incidents/" + iid + "/diagnosis"
        ).json()
        if slot != "N7":
            record["fixed_resource"] = fixed_resource(campaign, iid)
        if slot == "N1":
            campaign.work("/v1/incidents/" + iid + "/investigation-jobs")
            record["investigation"] = campaign.client.get(
                "/v1/incidents/" + iid + "/investigation"
            ).json()
        record["status"] = "OBSERVED"
    except BaseException as exc:
        record.update(
            status="FAILED", error_type=type(exc).__name__, error=str(exc)[:240]
        )
        raise
    finally:
        try:
            campaign.owner.verify()
            save(root / "restore-intent.json", {"at": stamp(), "document": "BASELINE"})
            record["restored"] = campaign.controller.apply("BASELINE")
            deadline = time.monotonic() + 360
            while True:
                lag = campaign.lag()
                if (
                    lag["lag"] < 20
                    and lag["source_timestamp"]
                    >= datetime.fromisoformat(
                        record["restored"]["observed_at"]
                    ).timestamp()
                ):
                    break
                if time.monotonic() > deadline:
                    raise ValueError("QUEUE_NOT_DRAINED")
                time.sleep(5)
            campaign.runtime(slot + "-after")
            record["healthy_restored"] = True
        except BaseException as exc:
            record.update(healthy_restored=False, recovery_error=type(exc).__name__)
            raise
        finally:
            record["end"] = stamp()
            record["accounting"] = campaign.repo.accounting()
            save(root / "result.json", record)
            runner.finish_episode(
                slot,
                succeeded=record["status"] == "OBSERVED"
                and record.get("healthy_restored", False),
                reason=record["status"],
            )
    print(
        json.dumps(
            {
                "slot": slot,
                "status": record["status"],
                "terminal": record["diagnosis"]["terminal"],
            }
        ),
        flush=True,
    )
    return iid


def recurrence(campaign, runner):
    from types import SimpleNamespace
    from ecomsre.product.knowledge.selection_lock_v050 import load as selection
    from ecomsre.product.incidents.extensions import ProductExtensionMatcherV1
    from ecomsre.product.knowledge.observations_v050 import load_observations
    from ecomsre.product.investigation import runtime as investigation_runtime

    with campaign.app.state.store.connect() as c:
        lock = selection(c)
        if lock is not None:
            row = c.execute(
                "SELECT state,payload_json FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
                (lock["registration_id"],),
            ).fetchone()
            if row is None or row["state"] != "ACTIVE":
                raise ValueError("RECURRENCE_REQUIRES_ACTIVE_VALIDATED_CANDIDATE")
            compiled = json.loads(row["payload_json"])
            compiled_sha = compiled["compiled_sha256"]
            dependency = compiled["proposal"].get("resource_dependency")
    if lock is None:
        raise ValueError("NO_SELECTED_CANDIDATE")
    registration_id = lock["registration_id"]
    before = campaign.repo.accounting()
    proof = dict(
        registration_id=registration_id,
        before=before,
        passed=False,
        provider_explicitly_disabled=True,
        replay_after_revocation=True,
    )

    def deny_provider(*args, **kwargs):
        raise ValueError("RECURRENCE_PROVIDER_FORBIDDEN")

    investigation_runtime.configured_provider = deny_provider
    try:
        iid = collect(campaign, runner, "N7")
        diagnosis = campaign.client.get(f"/v1/incidents/{iid}/diagnosis").json()
        evidence = campaign.client.get(f"/v1/incidents/{iid}/evidence").json()
        bindings = [
            b
            for obj in evidence["objects"]
            for b in obj["payload"].get("learned_match_bindings_v050", [])
        ]
        proof.update(
            incident_id=iid,
            normal_api_result=diagnosis,
            matched_bindings=bindings,
            after=campaign.repo.accounting(),
        )
        if (
            diagnosis["terminal"] != "EXTENSION_KNOWN"
            or not bindings
            or {b["registration_id"] for b in bindings} != {registration_id}
            or {b["incident_id"] for b in bindings} != {iid}
            or {b["candidate_sha256"] for b in bindings} != {compiled_sha}
            or diagnosis["provider_calls"] != 0
            or proof["after"] != before
            or campaign.repo.get(iid) is not None
            or (
                dependency is not None
                and not load_observations(
                    campaign.app.state.incidents.get(iid),
                    campaign.app.state.object_store,
                )
            )
        ):
            raise ValueError("NORMAL_RECURRENCE_REQUIREMENTS_FAILED")
        material = campaign.evo.knowledge._shadow_runtime_material(iid)

        def replay(capability=None, environment=None):
            matcher = ProductExtensionMatcherV1(
                campaign.app.state.knowledge.active_extensions(campaign.env),
                derived_registrations=campaign.app.state.knowledge.active_investigation_extensions(
                    campaign.env
                ),
                capability_sha256=capability
                or material.incident.source_capability_sha256,
                compatibility_store=campaign.app.state.store,
                environment_id=environment or campaign.env,
                supplemental_reads=SimpleNamespace(
                    incident=material.incident,
                    objects=campaign.app.state.object_store,
                    entries={},
                ),
            )
            return matcher.match(
                case_id=iid,
                candidate_services=material.runtime_input.candidate_services,
                topology_edges=material.runtime_input.adjacent_services,
                baseline=material.runtime_input.baseline,
                memory=material.runtime_input.memory,
                generic_anomalies=material.runtime_input.generic_anomalies,
                raw_outcomes=material.raw_outcomes,
                snapshots=tuple(
                    o["payload"]
                    for o in evidence["objects"]
                    if "connector_result" in o["payload"]
                ),
            )

        if {m.registration_id for m in replay()} != {registration_id}:
            raise ValueError("RECURRENCE_REPLAY_BINDING_FAILED")
        if replay(capability="0" * 64) or replay(
            environment="env-unregistered-closure-control"
        ):
            raise ValueError("RECURRENCE_MISMATCH_CONTROL_FAILED")
        campaign.evo.revoke(registration_id)
        proof["revoked"] = True
        if (
            replay()
            or campaign.client.get(f"/v1/incidents/{iid}/diagnosis").json() != diagnosis
        ):
            raise ValueError("REVOCATION_REPLAY_FAILED")
        proof.update(
            passed=True,
            provider_call_delta=0,
            event_llm_calls=0,
            revocation_check=True,
            capability_mismatch_check=True,
            environment_mismatch_check=True,
        )
    except BaseException as exc:
        proof["failure"] = type(exc).__name__ + ":" + str(exc)[:240]
        raise
    finally:
        if not proof.get("revoked"):
            campaign.evo.revoke(registration_id)
            proof["revoked"] = True
        save(ROOT / "recurrence-proof.json", proof)


def run_stage(stage):
    campaign = attach(provider_enabled=stage in {"development", "propose"})
    try:
        runner = runner_for(campaign)
        if stage in {"shadow", "reuse"}:
            from ecomsre.product.knowledge.selection_lock_v050 import (
                load as selected_lock,
                identity,
            )

            with campaign.app.state.store.connect() as c:
                selected = selected_lock(c)
                if (
                    selected is None
                    or identity(c, selected["registration_id"])[1]
                    != selected["bindings"]
                ):
                    raise ValueError(
                        "SELECTED_PROTOCOL_IDENTITY_CHANGED_BEFORE_COLLECTION"
                    )
        if stage == "development":
            for slot in ("N2", "N3"):
                collect(campaign, runner, slot)
            runner.freeze_cohort()
        elif stage == "propose":
            from ecomsre.product.investigation.runtime import configured_provider
            from ecomsre.product.knowledge.selection_lock_v050 import load as selection

            for _ in range(6):
                with campaign.app.state.store.connect() as c:
                    if selection(c) is not None:
                        break
                pending = runner._get("selection-pending")
                if pending is not None:
                    from ecomsre.product.knowledge.candidates_v050 import (
                        CompiledKnowledge,
                    )

                    with campaign.app.state.store.connect() as c:
                        row = c.execute(
                            "SELECT payload_json FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
                            (pending["registration_id"],),
                        ).fetchone()
                    runner.select(CompiledKnowledge.model_validate_json(row[0]))
                    break
                if runner._get("stop") is not None:
                    break
                result = runner.propose_next(configured_provider(campaign.repo))
                print(
                    json.dumps(
                        {
                            k: result[k]
                            for k in ("ordinal", "status", "error", "registration_id")
                        }
                    ),
                    flush=True,
                )
        elif stage == "shadow":
            from ecomsre.product.knowledge.selection_lock_v050 import load as selection
            from ecomsre.product.knowledge.candidates_v050 import CompiledKnowledge

            with campaign.app.state.store.connect() as c:
                lock = selection(c)
                if lock is None:
                    raise ValueError("NO_SELECTED_CANDIDATE")
                row = c.execute(
                    "SELECT payload_json FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
                    (lock["registration_id"],),
                ).fetchone()
                candidate = CompiledKnowledge.model_validate_json(row[0])
            for slot in ("N4", "N5", "N6"):
                collect(campaign, runner, slot)
            authorization = load("test-promotion-authorization.json", ROUND)
            if (
                authorization["status"] != "PREAUTHORIZED_TEST_PROMOTION"
                or authorization["environment_id"] != campaign.env
            ):
                raise ValueError("TEST_PROMOTION_AUTHORIZATION_DIFFERS")
            result = runner.evaluate_and_promote(candidate)
            save(ROOT / "shadow-result.json", result.model_dump(mode="json"))
            print("SHADOW_GATE", result.gate_passed, flush=True)
        elif stage == "reuse":
            recurrence(campaign, runner)
            campaign.owner.cleanup()
        else:
            raise ValueError("UNIMPLEMENTED_STAGE")
    except BaseException:
        if not (ROOT / "cleanup.json").exists():
            campaign.owner.cleanup()
        raise
    finally:
        campaign.client.__exit__(None, None, None)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        choices=("start", "development", "propose", "shadow", "reuse"),
        default="start",
    )
    stage = parser.parse_args().stage
    start() if stage == "start" else run_stage(stage)
