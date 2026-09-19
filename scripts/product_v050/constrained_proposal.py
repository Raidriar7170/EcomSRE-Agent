"""One scoped v050.2 development round on retained seen evidence; no Docker."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path

from scripts.product_v050.knowledge_feasibility import DATA, REPO, material, audit
from scripts.product_v050.knowledge_contract_repair import save
from scripts.product_v050.preflight import inspect
from scripts.product_v050.project_environment import load_project_environment
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22 as sha
from ecomsre.product.knowledge.drafts_v050 import (
    SCOPED_TASK,
    SCOPED_PROTOCOL,
    ScopedKnowledgeDraft,
    scoped_view,
    scoped_model_view,
    scoped_schema,
    compile_scoped_draft,
    diagnose_scoped_draft,
)
from ecomsre.product.knowledge.evolution_v050 import evaluation_bindings

OUT = DATA / "knowledge-feasibility"


def history_digest(store):
    tables = (
        "investigation_provider_calls_v050",
        "investigation_sessions_v050",
        "knowledge_episode_incidents_v050",
        "knowledge_rejections_v050",
    )
    with store.connect() as c:
        return {
            table: sha(
                [
                    dict(r)
                    for r in c.execute("SELECT * FROM " + table + " ORDER BY 1")
                    if not any(
                        isinstance(v, str) and v.startswith(SCOPED_PROTOCOL + ":")
                        for v in dict(r).values()
                    )
                ]
            )
            for table in tables
        }


def scope_for(evo, roster):
    from ecomsre.product.environment.services import ServiceCatalogRepositoryV1

    env = evo.knowledge._incident(roster[0]["incident_id"]).environment_id
    identities = ServiceCatalogRepositoryV1(evo.store).get_map(env)
    services = {s.service_id: s.logical_service for s in identities.services}
    roots = [
        evo.knowledge._diagnosis(r["incident_id"]).root_service_ids for r in roster
    ]
    if any(len(r) != 1 for r in roots) or len({r[0] for r in roots}) != 1:
        raise ValueError("NO_SINGLE_SHARED_NORMAL_PRODUCT_ROOT")
    return env, services[roots[0][0]]


def prepare():
    evo, roster, discovery = material()
    report = audit(evo, roster, discovery)
    env, target = scope_for(evo, roster)
    ids = [r["incident_id"] for r in roster]
    dev = [r["incident_id"] for r in roster if r["role"] == "DEVELOPMENT"]
    if len(ids) != 5 or len(dev) != 2:
        raise ValueError("ORIGINAL_ROSTER_CHANGED")
    for row in report["rows"]:
        entry = next(t for t in row["targets"] if t["target"] == target)
        if len({o["source"] for o in entry["observations"] if o["usable"]}) < 2:
            raise ValueError("TARGET_INPUT_OBSERVATION_GAP")
    binding = scoped_view(
        discovery,
        request_key=SCOPED_PROTOCOL + ":proposal:0",
        target=target,
        members=ids,
    )
    # Explicit initial scope: only the feasible Level A path on ALL five events.
    if scoped_schema(binding)["$defs"]["CandidateDraft"]["properties"][
        "expression"
    ] != {"type": "null"}:
        raise ValueError("REASSESS_PROTOCOL_LEVEL_SCOPE")
    import tiktoken
    from ecomsre.product.investigation.provider import SYSTEM, TASK_CONTRACTS

    enc = tiktoken.get_encoding("o200k_base")
    def size(value):
        return len(enc.encode(json.dumps(value, separators=(",", ":"))))
    model_view = scoped_model_view(binding)
    stats = dict(
        tokenizer="o200k_base; local estimate, not Provider billed input",
        view_components={k: size(v) for k, v in model_view.items()},
        view_tokens=size(model_view),
        schema_tokens=size(scoped_schema(binding)),
        instructions_tokens=len(enc.encode(SYSTEM + TASK_CONTRACTS[SCOPED_TASK])),
        old_actual_input_tokens=[50189, 50209, 50216],
    )
    stats["estimated_total_tokens"] = (
        stats["view_tokens"] + stats["schema_tokens"] + stats["instructions_tokens"]
    )
    protocol = dict(
        protocol=SCOPED_PROTOCOL,
        start_head="3ae84ab4bda5054ccfbfde74e9c655a416e312e4",
        sources=evaluation_bindings()
        | {
            p: hashlib.sha256((REPO / p).read_bytes()).hexdigest()
            for p in (
                "scripts/product_v050/constrained_proposal.py",
                "scripts/product_v050/knowledge_feasibility.py",
                "src/ecomsre/product/investigation/repository.py",
            )
        },
        snapshot_sha256=discovery["snapshot_sha256"],
        environment_id=env,
        target=target,
        roster=roster,
        development_ids=dev,
        protected=history_digest(evo.store),
        model="gpt-5.4-mini-2026-03-17",
        api_style="responses",
        reasoning="medium",
        max_output_tokens=8192,
        request_cap=6,
        committed_cap_microusd=2_000_000,
        max_semantic_attempts=3,
        scope="LEVEL_A_ONLY_ALL_FIVE_SEEN_EVENTS; LEVEL_B_DEVELOPMENT_DEPENDENCY_GAP",
        development_gate="BOTH_ORIGINAL_DEVELOPMENT_EVENTS_TRUE; NO_FPR_OR_INDEPENDENT_READINESS_WITHOUT_CONTROLS",
        independent_validation_gate="ORIGINAL_GOAL_SHADOW_AND_CONTROL_REQUIREMENTS_UNCHANGED",
        budget_before=inspect(DATA),
        input_composition=stats,
        schema_sha256=sha(scoped_schema(binding)),
    )
    # Do not freeze budget_before again after a paid call.
    path = OUT / "protocol.json"
    if path.exists():
        prior = json.loads(path.read_text())
        protocol["budget_before"] = prior["budget_before"]
        if prior != protocol:
            raise ValueError("FROZEN_PROTOCOL_OR_OLD_HISTORY_CHANGED")
    return evo, roster, discovery, report, protocol


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--freeze", action="store_true")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--revision", type=int, choices=range(3), default=0)
    args = p.parse_args()
    os.umask(0o077)
    readonly, roster, discovery, report, protocol = prepare()
    if args.freeze:
        save(OUT / "protocol.json", protocol)
        save(OUT / "feasibility.json", report)
    if not args.execute:
        print(
            json.dumps(
                dict(
                    protocol=protocol["protocol"],
                    scope=protocol["scope"],
                    budget=protocol["budget_before"],
                    input_composition=protocol["input_composition"],
                ),
                indent=2,
            )
        )
        return
    if not (OUT / "protocol.json").is_file():
        raise ValueError("PROTOCOL_NOT_FROZEN")
    prefix = SCOPED_PROTOCOL + ":proposal:"
    with readonly.store.connect() as c:
        calls = c.execute(
            "SELECT * FROM investigation_provider_calls_v050 WHERE call_key LIKE ?",
            (prefix + "%",),
        ).fetchall()
    if len(calls) != args.revision:
        raise ValueError("MONOTONIC_SEMANTIC_ATTEMPT_REQUIRED")
    prior = [json.loads(path.read_text()) for path in sorted(OUT.glob("proposal-*.json"))]
    if any(r.get("development_passed") for r in prior):
        raise ValueError("DEVELOPMENT_EXIT_REACHED_NO_MORE_SAMPLING")
    feedback = None
    if args.revision:
        previous = json.loads((OUT / f"proposal-{args.revision - 1}.json").read_text())
        if previous.get("disposition") in {"NO_CANDIDATE", "NEEDS_OBSERVATION"}:
            raise ValueError("MODEL_ABSTAINED_NO_IDENTICAL_RETRY")
        feedback = previous["feedback"]
        if len(prior) >= 2 and prior[-1]["feedback"] == prior[-2]["feedback"]:
            raise ValueError("REPEATED_ERROR_NO_NEW_INFORMATION")
    from ecomsre.product.app import create_app
    from ecomsre.product.settings import ProductSettingsV1
    from ecomsre.product.investigation.repository import InvestigationRepository
    from ecomsre.product.investigation.runtime import configured_provider
    from ecomsre.product.knowledge.evolution_v050 import KnowledgeEvolutionV050
    from ecomsre.product.errors import ProductError

    app = create_app(ProductSettingsV1(data_root=DATA))
    repo = InvestigationRepository(app.state.store, app.state.object_store)
    evo = KnowledgeEvolutionV050(app.state.knowledge, repo)
    # Actual new development exposure is explicit and happens only here.
    observed = evo.discovery_view(
        protocol["environment_id"], [r["incident_id"] for r in roster]
    )
    if observed != discovery:
        raise ValueError("INPUT_CHANGED_AFTER_READONLY_AUDIT")
    load_project_environment(Path.home() / ".config/ecomsre/provider.env")
    os.environ["ECOMSRE_PRODUCT_PROVIDER_API_STYLE"] = "responses"
    os.environ.setdefault(
        "ECOMSRE_PRODUCT_PROVIDER_PRICE_FILE",
        str(REPO / "config/product-v050/openai-gpt54-mini-prices-20260917.json"),
    )
    provider = configured_provider(repo)
    if (
        provider.config.model != protocol["model"]
        or provider.config.base_url.rstrip("/") != "https://api.openai.com/v1"
    ):
        raise ValueError("PROVIDER_CONFIG_DRIFT")
    key = prefix + str(args.revision)
    binding = scoped_view(
        discovery,
        request_key=key,
        target=protocol["target"],
        members=[r["incident_id"] for r in roster],
        feedback=feedback,
    )
    model_view = scoped_model_view(binding)
    result = dict(
        protocol=SCOPED_PROTOCOL,
        key=key,
        started_at=datetime.now(UTC).isoformat(),
        schema_valid=False,
        admitted=False,
        canonical_reconstructed=False,
        development_evaluated=False,
        development_passed=False,
        independent_validation_eligible=False,
        layer="PROTOCOL",
        new_live_episodes=0,
        new_product_recovery_writes=0,
        binding_object_sha256=repo.objects.put_json(binding).object_sha256,
        input_object_sha256=repo.objects.put_json(model_view).object_sha256,
    )
    try:
        raw = provider.complete(
            key=key,
            task=SCOPED_TASK,
            view=model_view,
            schema=ScopedKnowledgeDraft,
            reasoning="medium",
            scoped_binding=binding,
            max_output_tokens=8192,
        )
        result.update(
            schema_valid=True,
            disposition=raw.disposition,
            raw_draft_object_sha256=repo.objects.put_json(
                raw.model_dump(mode="json")
            ).object_sha256,
            layer="ADMISSION",
        )
        result["diagnostics"] = diagnose_scoped_draft(
            raw.model_dump(mode="json"), binding
        )
        if raw.disposition != "CANDIDATE":
            result.update(
                layer="OBSERVATION"
                if raw.disposition == "NEEDS_OBSERVATION"
                else "MODEL_ABSTENTION",
                feedback={"disposition": raw.disposition, "reason": raw.reason},
            )
        else:
            proposal, context = compile_scoped_draft(raw, binding)
            candidate = evo.add_candidate(
                environment_id=protocol["environment_id"],
                proposal=proposal,
                origin="LLM",
                source_request_key=key,
                discovery=discovery,
                draft_view_binding=binding,
            )
            result.update(
                admitted=True,
                canonical_reconstructed=True,
                registration_id=candidate.registration_id,
                level="A",
                layer="DEVELOPMENT",
            )
            checked = evo.check_development(
                candidate.registration_id, [r["incident_id"] for r in roster]
            )
            outcomes = [
                r
                for r in checked["outcomes"]
                if r["incident_id"] in protocol["development_ids"]
            ]
            passed = len(outcomes) == 2 and all(
                r["outcome"]["status"] == "TRUE" for r in outcomes
            )
            result.update(
                development_evaluated=True,
                development_passed=passed,
                development=checked,
                layer="CONTROL_GAP" if passed else "DEVELOPMENT",
                feedback={
                    "layer": "DEVELOPMENT",
                    "outcomes": [
                        {
                            **r,
                            "incident_id": next(
                                k
                                for k, v in binding["members"].items()
                                if v == r["incident_id"]
                            ),
                        }
                        for r in checked["outcomes"]
                    ],
                    "controls": "NO_INDEPENDENT_SPECIFICITY_ESTIMATE",
                },
            )
    except (ProductError, ValueError) as exc:
        from pydantic import ValidationError

        code = (
            exc.code
            if isinstance(exc, ProductError)
            else "SEMANTIC_SCHEMA_INVALID"
            if isinstance(exc, ValidationError)
            else str(exc)
        )
        result.update(
            error_code=code,
            feedback={
                "layer": result["layer"],
                "error_code": code,
                "diagnostics": result.get("diagnostics", []),
            },
        )
        with repo.store.connect() as c:
            row = c.execute(
                "SELECT payload_json FROM investigation_provider_calls_v050 WHERE call_key=?",
                (key,),
            ).fetchone()
            if row:
                record = json.loads(row[0])
                result["feedback"].update(
                    diagnostics=record.get(
                        "draft_diagnostics", result.get("diagnostics", [])
                    ),
                    schema_errors=record.get("schema_validation_errors", []),
                )
            c.execute(
                "INSERT OR IGNORE INTO knowledge_rejections_v050 VALUES (?,?,?,?,?)",
                (
                    key,
                    protocol["environment_id"],
                    discovery["snapshot_sha256"],
                    code[:240],
                    datetime.now(UTC).isoformat(),
                ),
            )
    result["budget_after"] = inspect(DATA)
    result["old_history_unchanged"] = (
        history_digest(repo.store) == protocol["protected"]
    )
    save(OUT / f"proposal-{args.revision}.json", result)
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"feedback", "development"}},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
