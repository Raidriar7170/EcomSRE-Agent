"""Single authorized fixed-rule validation batch; no Provider or development."""

from datetime import UTC, datetime, timedelta
import json
import re
import time

from scripts.product_v050 import final_closure_live as previous
from scripts.product_v050.live_environment import Owned, load, save
from scripts.product_v050.live_product import Campaign, DATA, stamp
from scripts.product_v050.validation_capture import capture, earliest
from scripts.product_v050.validation_runner import ValidationRunner
from ecomsre.product.knowledge import validation_batch_v050 as batch
from ecomsre.product.knowledge.candidates_v050 import CompiledKnowledge
from ecomsre.product.connectors._http import BoundedHttpTransportV1
from ecomsre.product.connectors.credentials import CredentialResolverV1

ROOT = DATA / "live-final-closure-08"
PRIOR = DATA / "live-final-closure-07"
PARENT = "registration-ae3902fb32fd96d87608cb93"


def authorize_and_prepare():
    """One fresh read-only continuity check, then append authorization/owned plan."""
    import hashlib
    import uuid
    from scripts.product_v050.live_environment import command, inventory, prepare, REPO
    from scripts.product_v050.final_closure import ClosureRunner
    from scripts.product_v050.validation_capture import protocol
    from ecomsre.product.knowledge.shadow_controls_v050 import CONTROL_VERSION

    path = ROOT / "resumption-precheck.json"
    if path.exists():
        raise ValueError("one continuity check retained; inspect it, do not overwrite")
    old = load("admitted-baseline.json", PRIOR)
    context = command("docker", "context", "show")
    endpoint = json.loads(command("docker", "context", "inspect", context))[0][
        "Endpoints"
    ]["docker"]["Host"]
    daemon = json.loads(command("docker", "info", "--format", "{{json .}}"))["ID"]
    current = inventory()
    networks = json.loads(command("docker", "network", "inspect", *current["network"]))
    extra = {
        r["Id"]: {k: r[k] for k in ("Attachable", "EnableIPv6", "Ingress", "Scope")}
        for r in networks
    }
    ok = (
        current == old["inventory"]
        and extra == old["network_extra"]
        and context == old["context"]
        and endpoint == old["endpoint"]
        and daemon == old["daemon"]
        and load("cleanup.json", PRIOR)["result"]["clean"]
    )
    check = dict(
        observed_at=datetime.now(UTC).isoformat(),
        resource_continuity_passed=ok,
        inventory=current,
        previous_inventory=old["inventory"],
        network_extra=extra,
        context=context,
        endpoint=endpoint,
        daemon=daemon,
        docker_mutations=0,
    )
    save(path, check)
    if not ok:
        raise ValueError("NEW_RESOURCE_DRIFT_NO_LIVE_STARTED")
    evo = offline_evolution()
    with evo.store.connect() as c:
        parent = CompiledKnowledge.model_validate_json(
            c.execute(
                "SELECT payload_json FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
                (PARENT,),
            ).fetchone()[0]
        )
    original = ClosureRunner(evo, parent.environment_id, episode_root=DATA)
    queries = load("deployment.json", PRIOR)["actual_queries"]
    nonce = uuid.uuid4().hex[:12]
    episodes = ["ecomsre-v050-" + nonce + "-v" + str(i) for i in range(1, 5)]
    now = datetime.now(UTC)
    plan = dict(
        holdout_episodes=dict(
            zip(
                episodes[:3],
                ("POSITIVE_INCIDENT", "NO_INCIDENT", "CONFUSABLE_CORE_KNOWN"),
            )
        ),
        recurrence_episode=episodes[3],
        collection=protocol(queries),
        time_range=dict(
            start=now.isoformat(), end=(now + timedelta(hours=8)).isoformat()
        ),
        derived_controls_version=CONTROL_VERSION,
    )
    authority = (
        REPO
        / "docs/results/product-v050/final-learning-closure/fixed-validation-20260927-plan.md"
    )
    candidate = batch.install(
        evo,
        PARENT,
        plan=plan,
        authorization_sha256=hashlib.sha256(authority.read_bytes()).hexdigest(),
        episode_ledger=original.episode_ledger(),
    )
    with evo.store.connect() as c:
        value = batch.load(c)
    save(ROOT / "validation-authorization.json", value)
    save(ROOT / "fixed-candidate.json", candidate.model_dump(mode="json"))
    for name in (
        "admitted-baseline.json",
        "cached-images.json",
        "upstream-pinned-resolved.json",
    ):
        save(ROOT / name, load(name, PRIOR))
    prepare(ROOT)
    print(
        json.dumps(
            dict(
                precheck="PASS",
                registration_id=candidate.registration_id,
                new_live_limit=4,
                new_provider_limit=0,
            )
        ),
        flush=True,
    )


def configured():
    previous.ROOT = ROOT
    previous.FAILED = PRIOR


def attach():
    configured()
    return previous.attach(provider_enabled=False)


def bind(campaign):
    """Reuse normal reverify, exact deployment comparison and baseline builder."""
    configured()
    original = previous.install
    try:

        def append(store, **kw):
            return batch.install_deployment(
                store, new=kw["new"], new_deployment=kw["new_deployment"]
            )["sha256"]

        previous.install = append
        previous.bind_existing(campaign)
    finally:
        previous.install = original


def offline_evolution():
    from ecomsre.product.app import create_app
    from ecomsre.product.settings import ProductSettingsV1
    from ecomsre.product.investigation.repository import InvestigationRepository
    from ecomsre.product.knowledge.evolution_v050 import KnowledgeEvolutionV050

    app = create_app(
        ProductSettingsV1(
            data_root=DATA,
            investigation={"enabled": False},
            knowledge_proposer_enabled=False,
        )
    )
    return KnowledgeEvolutionV050(
        app.state.knowledge,
        InvestigationRepository(app.state.store, app.state.object_store),
    )


def failed_start(runner, exc):
    """A failed owned deployment consumes the target slot, never a free retry."""
    at = datetime.now(UTC).isoformat()
    value = dict(
        slot="N4",
        episode_id=runner.slots["N4"],
        reserved_at=at,
        disposition="OWNED_START_FAILED",
    )
    runner._keep("episode:N4", value)
    path = runner.episode_root / runner.plan["campaign"] / "episodes/N4/started.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x") as stream:
        json.dump(
            dict(
                episode_id=value["episode_id"],
                start={"utc": at},
                status="OWNED_START_FAILED",
                product_recovery_writes=0,
            ),
            stream,
        )
    path.chmod(0o600)
    runner.finish_episode("N4", succeeded=False, reason="OWNED_START_FAILED")
    runner._keep("stop", dict(stage="owned-start", error=type(exc).__name__, at=at))


def start():
    configured()
    evo = offline_evolution()
    with evo.store.connect() as c:
        value = batch.load(c)
        batch.verify(c, value)
    runner = ValidationRunner(evo, value["environment_id"], episode_root=DATA)
    owner = Owned(ROOT)
    marker = ROOT / "deployment-started.json"
    with marker.open("x") as stream:
        json.dump(
            dict(
                at=stamp(), batch_sha256=value["sha256"], preparation_not_incident=True
            ),
            stream,
        )
    try:
        owner.start()
        campaign = Campaign(owner, provider_enabled=False)
        try:
            with capture(
                campaign.app.state.object_store,
                ROOT / "raw-readiness.jsonl",
                occurrence="DEPLOYMENT_READINESS_NOT_EPISODE",
            ):
                bind(campaign)
            save(
                ROOT / "readiness-complete.json",
                dict(at=stamp(), normal_incident_created=False),
            )
        finally:
            campaign.client.__exit__(None, None, None)
    except BaseException as exc:
        try:
            failed_start(runner, exc)
        finally:
            owner.cleanup()
        raise


def raw_support(campaign, entries, root, incident_id):
    """Fixed supplementary raw counters, never fed into the rule or diagnosis."""
    config = next(
        c
        for c in campaign.app.state.environments.get(campaign.env).connector_configs
        if c.kind.value == "PROMETHEUS"
    )
    http = BoundedHttpTransportV1(
        credential_resolver=CredentialResolverV1(),
        credential_refs=config.credential_refs,
        timeout_seconds=10,
        maximum_response_bytes=config.settings["maximum_response_bytes"],
    )
    requests = {}
    for entry in entries:
        if "/api/v1/query_range" not in entry["url"]:
            continue
        params = entry["params"]
        query = params["query"]
        for selector in re.findall(r"[a-zA-Z_:][a-zA-Z0-9_:]*\{[^{}]*\}", query):
            # Store raw counters over the full effective support interval.
            start = float(params["start"]) - 300
            requests[(selector, start, float(params["end"]), params["step"])] = dict(
                query=selector, start=start, end=params["end"], step=params["step"]
            )
    if len(requests) > 80:
        raise ValueError("RAW_SUPPORT_FIXED_REQUEST_CAP_EXCEEDED")
    try:
        for params in requests.values():
            http.request_json(
                "GET",
                config.endpoint.rstrip("/") + "/api/v1/query_range",
                params=params,
            )
        targets, _, _ = http.request_json(
            "GET", config.endpoint.rstrip("/") + "/api/v1/targets"
        )
        obj = campaign.app.state.object_store.put_json(targets)
        active = targets.get("data", {}).get("activeTargets", [])
        observed = datetime.now(UTC)
        scrape_ok = bool(active) and all(
            t.get("health") == "up"
            and 0
            <= (observed - datetime.fromisoformat(t["lastScrape"])).total_seconds()
            <= 30
            for t in active
        )
        save(
            root / "scrape-receipt.json",
            dict(
                observed_at=stamp(),
                incident_id=incident_id,
                scrape_recency_passed=scrape_ok,
                object_sha256=obj.object_sha256,
                raw_support_request_count=len(requests),
                ingestion_latency="NOT_DIRECTLY_EXPOSED_FIXED_30_SECOND_MARGIN",
            ),
        )
        if not scrape_ok:
            raise ValueError("FIXED_SCRAPE_RECENCY_FAILED")
    finally:
        http.close()


def collect(campaign, runner, slot):
    from ecomsre.product.knowledge.validation_batch_v050 import verify

    root = ROOT / "episodes" / slot
    with runner.store.connect() as c:
        verify(c, runner.batch)
    plan = runner.batch["plan"]["collection"]
    ready = load("readiness-complete.json", ROOT)["at"]["utc"]
    # Old N6 restoration is a retained factual Change, never overwritten.
    last_change = load("episodes/N6/result.json", previous.ROUND)["restored"][
        "observed_at"
    ]
    last_recovery = ready
    for prior in ("N4", "N5", "N6")[: ("N4", "N5", "N6", "N7").index(slot)]:
        record = load(f"episodes/{prior}/result.json", ROOT)
        if prior != "N5":
            last_recovery = record["restored"]["observed_at"]
        if prior == "N6":
            last_change = record["restored"]["observed_at"]
    due = earliest(
        plan, last_recovery=last_recovery, last_change=last_change, ready_at=ready
    )
    now = datetime.now(UTC)
    if (due - now).total_seconds() > plan["preparation_cap_seconds"]:
        raise ValueError("FIXED_PREPARATION_CAP_EXCEEDED")
    preparation = dict(
        start=stamp(),
        ready_at=ready,
        last_recovery=last_recovery,
        last_change=last_change,
        earliest_legal_observation=due.isoformat(),
        deadline=(now + timedelta(seconds=plan["preparation_cap_seconds"])).isoformat(),
        readiness_attempts=1,
        not_an_episode=True,
    )
    if (root / "preparation.json").exists():
        raise ValueError("PREPARATION_ALREADY_ATTEMPTED")
    runner._keep("preparation:" + slot, preparation)
    save(root / "preparation.json", preparation)
    while datetime.now(UTC) < due:
        time.sleep(min(30, max(0, (due - datetime.now(UTC)).total_seconds())))
        print(
            json.dumps(
                dict(slot=slot, state="FIXED_TIME_ISOLATION", until=due.isoformat())
            ),
            flush=True,
        )
    campaign.owner.verify()
    # One readiness check only; no diagnostic probing or adaptive waiting.
    campaign.controller.read("BASELINE")
    campaign.runtime(slot + "-isolated-ready")
    if campaign.lag()["lag"] >= 20:
        raise ValueError("FIXED_READINESS_FAILED_NO_RETRY")
    with capture(
        campaign.app.state.object_store,
        root / "raw-http.jsonl",
        occurrence=runner.slots[slot],
    ) as entries:
        iid = previous.collect(campaign, runner, slot)
        original_entries = list(entries)
        raw_support(campaign, original_entries, root, iid)
    if any(e["truncated"] for e in entries):
        raise ValueError("RAW_RESPONSE_RETENTION_TRUNCATED")
    save(
        root / "raw-capture-complete.json",
        dict(
            incident_id=iid,
            query_count=len(entries),
            index_path="raw-http.jsonl",
            raw_sensitive_private_only=True,
        ),
    )
    artifact_index = {
        p.name: campaign.app.state.object_store.put_json(
            json.loads(p.read_text())
        ).object_sha256
        for p in sorted(root.glob("*.json"))
    }
    save(root / "private-artifact-index.json", artifact_index)
    # This same boundary is mandatory for recurrence, before it can claim PASS.
    from scripts.product_v050.validation_capture import verify_raw

    with runner.store.connect() as c:
        batch.verify(c, runner.batch)
        actual = campaign.app.state.capabilities.get(campaign.env).capability_sha256
        if (
            batch.deployment_admits(
                c,
                runner.store,
                environment_id=campaign.env,
                expected=runner.batch["candidate"]["capability_sha256"],
                actual=actual,
            )
            is not True
        ):
            raise ValueError("CURRENT_VALIDATION_DEPLOYMENT_REQUIRED")
    evidence = campaign.evo.knowledge._evidence(
        iid, campaign.evo.knowledge._diagnosis(iid).diagnosis_id
    )
    snapshots = {
        o.action_id: o.payload
        for o in evidence.objects
        if "connector_result" in o.payload
    }
    verify_raw(
        entries,
        occurrence=runner.slots[slot],
        incident_id=iid,
        snapshots=snapshots.values(),
        queries=plan["actual_queries"],
        scrape=load("scrape-receipt.json", root),
    )
    for entry in entries:
        campaign.app.state.object_store.read_bytes(entry["response_object_sha256"])
    runner.seal_collection(
        slot,
        iid,
        raw_index=dict(
            mode="OWNED_LOCAL", entries=entries, operation_artifacts=artifact_index
        ),
        scrape_receipt=load("scrape-receipt.json", root),
    )
    return iid


def stop(campaign, runner, stage, exc):
    record = dict(
        stage=stage, error=type(exc).__name__ + ":" + str(exc)[:240], at=stamp()
    )
    try:
        if runner is not None:
            runner._keep("stop", record)
            with runner.store.connect() as c:
                state = c.execute(
                    "SELECT state FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
                    (runner.batch["registration_id"],),
                ).fetchone()[0]
            if state == "ACTIVE":
                campaign.evo.revoke(runner.batch["registration_id"])
        save(ROOT / "batch-stop.json", record)
    finally:
        campaign.owner.cleanup()


def run(slot):
    campaign = attach()
    runner = None
    try:
        runner = ValidationRunner(campaign.evo, campaign.env, episode_root=DATA)
        candidate = CompiledKnowledge.model_validate(runner.batch["candidate"])
        if slot == "N7":
            previous.recurrence(
                campaign,
                runner,
                registration_id=candidate.registration_id,
                collect_event=collect,
                output_root=ROOT,
            )
        else:
            iid = collect(campaign, runner, slot)
            qualification = runner.qualify(candidate, slot)
            save(ROOT / "episodes" / slot / "qualification.json", qualification)
            if not qualification["qualified"]:
                raise ValueError("CONTROL_QUALIFICATION_FAILED_NO_RETRY")
            print(
                json.dumps(dict(slot=slot, incident_id=iid, qualification=True)),
                flush=True,
            )
    except BaseException as exc:
        stop(campaign, runner, slot, exc)
        raise
    finally:
        campaign.client.__exit__(None, None, None)


def evaluate():
    campaign = attach()
    runner = None
    try:
        runner = ValidationRunner(campaign.evo, campaign.env, episode_root=DATA)
        candidate = CompiledKnowledge.model_validate(runner.batch["candidate"])
        result = runner.evaluate_and_promote(candidate)
        save(ROOT / "new-shadow-result.json", result.model_dump(mode="json"))
        if not result.gate_passed:
            raise ValueError("NEW_INDEPENDENT_VALIDATION_FAILED")
    except BaseException as exc:
        stop(campaign, runner, "evaluate", exc)
        raise
    finally:
        campaign.client.__exit__(None, None, None)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "stage",
        choices=["prepare", "start", "N4", "N5", "N6", "evaluate", "N7", "cleanup"],
    )
    args = parser.parse_args()
    if args.stage == "prepare":
        authorize_and_prepare()
    elif args.stage == "start":
        start()
    elif args.stage == "evaluate":
        evaluate()
    elif args.stage == "cleanup":
        configured()
        previous.retained_owner().cleanup()
    else:
        run(args.stage)
