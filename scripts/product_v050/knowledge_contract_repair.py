"""Bounded seen-episode development replay. Never starts Docker or live reads."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22 as sha
from ecomsre.product.app import create_app
from ecomsre.product.errors import ProductError
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.investigation.repository import InvestigationRepository
from ecomsre.product.investigation.runtime import configured_provider
from ecomsre.product.knowledge.evolution_v050 import (
    KnowledgeEvolutionV050,
    evaluation_bindings,
)
from ecomsre.product.knowledge.drafts_v050 import (
    TASK,
    PROTOCOL,
    KnowledgeDraft,
    compile_draft,
    draft_view,
    strict_schema,
)
from scripts.product_v050.project_environment import load_project_environment
from scripts.product_v050.preflight import inspect

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / ".local/product-v050"
OUT = DATA / "knowledge-contract-repair"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write("\n")


def sources():
    return evaluation_bindings() | {
        p: hashlib.sha256((REPO / p).read_bytes()).hexdigest()
        for p in (
            "scripts/product_v050/knowledge_contract_repair.py",
            "src/ecomsre/product/investigation/repository.py",
        )
    }


def protected(repo):
    with repo.store.connect() as c:
        calls = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM investigation_provider_calls_v050 WHERE call_key NOT LIKE 'knowledge-draft-v050.1:%' ORDER BY call_key"
            )
        ]
        sessions = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM investigation_sessions_v050 ORDER BY session_id"
            )
        ]
        episodes = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM knowledge_episode_incidents_v050 ORDER BY incident_id"
            )
        ]
        rejections = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM knowledge_rejections_v050 WHERE source_request_key NOT LIKE 'knowledge-draft-v050.1:%' ORDER BY source_request_key"
            )
        ]
    return {
        name: sha(value)
        for name, value in dict(
            calls=calls, sessions=sessions, episodes=episodes, rejections=rejections
        ).items()
    }


def material():
    if not (DATA / "product.sqlite3").is_file():
        raise ValueError("ORIGINAL_LEDGER_MISSING")
    app = create_app(ProductSettingsV1(data_root=DATA))
    repo = InvestigationRepository(app.state.store, app.state.object_store)
    evo = KnowledgeEvolutionV050(app.state.knowledge, repo)
    with repo.store.connect() as c:
        # Recover exact original campaign from its retained environment record.
        env = json.loads(
            (DATA / "live-02/postgres-user-01/environment.json").read_text()
        )
        environment_id = env["environment_id"]
        rows = c.execute(
            "SELECT incident_id,episode_id FROM knowledge_episode_incidents_v050 WHERE environment_id=? ORDER BY episode_id",
            (environment_id,),
        ).fetchall()
        roles = json.loads(
            c.execute(
                "SELECT manifest_json FROM knowledge_split_v050 WHERE environment_id=?",
                (environment_id,),
            ).fetchone()[0]
        )
    ids = [
        r["incident_id"]
        for r in rows
        if roles[r["episode_id"]] in {"DISCOVERY", "DEVELOPMENT"}
    ]
    dev = [r["incident_id"] for r in rows if roles[r["episode_id"]] == "DEVELOPMENT"]
    if len(ids) != 5 or len(dev) != 2:
        raise ValueError("ORIGINAL_EPISODE_ROSTER_DIFFERS")
    discovery = evo.discovery_view(environment_id, ids)
    return repo, evo, environment_id, ids, dev, discovery


def require_next_attempt(repo, revision, repair, output):
    # A truncated response has no recoverable semantic anchor. This runner does
    # not implement format-only repair; every dispatch consumes a semantic slot.
    if repair:
        raise ValueError("FORMAT_REPAIR_UNSUPPORTED_WITHOUT_SEMANTIC_ANCHOR")
    prior = [json.loads(p.read_text()) for p in output.glob("revision-*-repair-*.json")]
    if any(p.get("independent_validation_eligible") for p in prior):
        raise ValueError("DEVELOPMENT_PASSED_NO_FURTHER_SAMPLING")
    with repo.store.connect() as c:
        count = c.execute(
            "SELECT COUNT(*) FROM investigation_provider_calls_v050 WHERE call_key LIKE 'knowledge-draft-v050.1:%'"
        ).fetchone()[0]
    if count >= 3:
        raise ValueError("SEMANTIC_ATTEMPTS_EXHAUSTED")
    if revision != count:
        raise ValueError("SEMANTIC_ATTEMPTS_MUST_BE_MONOTONIC")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--revision", type=int, choices=range(3), default=0)
    parser.add_argument("--repair", type=int, choices=range(2), default=0)
    args = parser.parse_args()
    os.umask(0o077)
    repo, evo, env, ids, dev, discovery = material()
    view = draft_view(discovery)
    protocol = dict(
        protocol=PROTOCOL,
        sources=sources(),
        schema_sha256=sha(strict_schema(KnowledgeDraft)),
        discovery_sha256=discovery["snapshot_sha256"],
        episode_ids=ids,
        development_ids=dev,
        protected=protected(repo),
        request_cap=6,
        committed_cap_microusd=1_000_000,
        max_semantic_revisions=2,
        max_format_repairs_per_candidate=1,
        development_gate="ALL_TWO_DECLARED_DEVELOPMENT_EVENTS_TRUE; independent controls still required",
        telemetry_mode="LIVE_PROVIDER_REPLAY_TELEMETRY",
        new_live_episodes=0,
    )
    if not args.execute:
        print(
            json.dumps(
                dict(
                    view_bytes=len(json.dumps(view)),
                    evidence_rows=len(view["evidence_catalog"]),
                    dependency_rows=len(view["dependency_catalog"]),
                    budget=inspect(DATA),
                    protocol=protocol,
                ),
                indent=2,
            )
        )
        return
    require_next_attempt(repo, args.revision, args.repair, OUT)
    protocol_path = OUT / "protocol.json"
    if protocol_path.exists():
        if json.loads(protocol_path.read_text()) != protocol:
            raise ValueError("FROZEN_REPLAY_PROTOCOL_OR_HISTORY_DRIFT")
    else:
        if args.revision != 0 or args.repair != 0:
            raise ValueError("INITIAL_REQUEST_REQUIRED")
        save(protocol_path, protocol)
    feedback = None
    if args.revision or args.repair:
        if args.repair:
            previous = OUT / f"revision-{args.revision}-repair-0.json"
        else:
            previous = OUT / f"revision-{args.revision - 1}-repair-1.json"
            if not previous.exists():
                previous = OUT / f"revision-{args.revision - 1}-repair-0.json"
        prior = json.loads(previous.read_text())
        if prior.get("independent_validation_eligible"):
            raise ValueError("DEVELOPMENT_PASSED_NO_FURTHER_SAMPLING")
        if args.repair and prior["failure_layer"] != "PROTOCOL":
            raise ValueError("FORMAT_REPAIR_CANNOT_CHANGE_SEMANTICS")
        if not args.repair and prior["failure_layer"] not in {
            "ADMISSION",
            "DEVELOPMENT",
            "OBSERVATION",
            "EXPRESSIVENESS",
            "PROTOCOL",
        }:
            raise ValueError("NO_RETRY_BASIS")
        feedback = prior["feedback"]
    view = draft_view(discovery, feedback)
    key = f"{PROTOCOL}:revision-{args.revision}:repair-{args.repair}"
    result_path = OUT / f"revision-{args.revision}-repair-{args.repair}.json"
    if result_path.exists():
        raise ValueError("ATTEMPT_ALREADY_RECORDED")
    load_project_environment(Path.home() / ".config/ecomsre/provider.env")
    os.environ["ECOMSRE_PRODUCT_PROVIDER_API_STYLE"] = "responses"
    os.environ.setdefault(
        "ECOMSRE_PRODUCT_PROVIDER_PRICE_FILE",
        str(REPO / "config/product-v050/openai-gpt54-mini-prices-20260917.json"),
    )
    provider = configured_provider(repo)
    if (
        provider.config.model != "gpt-5.4-mini-2026-03-17"
        or provider.config.base_url.rstrip("/") != "https://api.openai.com/v1"
    ):
        raise ValueError("PROVIDER_CONFIG_DRIFT")
    result = dict(
        key=key,
        protocol=PROTOCOL,
        started_at=datetime.now(UTC).isoformat(),
        telemetry_mode="LIVE_PROVIDER_REPLAY_TELEMETRY",
        new_live_episodes=0,
        schema_valid=False,
        admission_passed=False,
        canonical_reconstructed=False,
        independent_validation_eligible=False,
        failure_layer="PROTOCOL",
    )
    # Private CAS retains the exact lawful input, separate from raw draft and canonical.
    result["view_object_sha256"] = repo.objects.put_json(view).object_sha256
    try:
        draft = provider.complete(
            key=key, task=TASK, view=view, schema=KnowledgeDraft, reasoning="high"
        )
        result["schema_valid"] = True
        result["draft_object_sha256"] = repo.objects.put_json(
            draft.model_dump(mode="json")
        ).object_sha256
        result["disposition"] = draft.disposition
        result["failure_layer"] = "ADMISSION"
        if draft.disposition != "CANDIDATE":
            result["failure_layer"] = (
                "OBSERVATION"
                if draft.disposition == "NEEDS_OBSERVATION"
                else "EXPRESSIVENESS"
            )
        proposal, context = compile_draft(draft, view)
        candidate = evo.add_candidate(
            environment_id=env,
            proposal=proposal,
            origin="LLM",
            source_request_key=key,
            discovery=discovery,
            draft_view_binding=view,
        )
        result.update(
            admission_passed=True,
            canonical_reconstructed=True,
            registration_id=candidate.registration_id,
            candidate_sha256=candidate.compiled_sha256,
            level="B" if proposal.expression else "A",
            comparison_context_count=len(context),
        )
        result["failure_layer"] = "DEVELOPMENT"
        development = evo.check_development(candidate.registration_id, ids)
        result["development"] = development
        target = [o for o in development["outcomes"] if o["incident_id"] in dev]
        result["independent_validation_eligible"] = len(target) == 2 and all(
            o["outcome"]["status"] == "TRUE" for o in target
        )
        result["feedback"] = {
            "layer": "DEVELOPMENT",
            "outcomes": development["outcomes"],
            "negative_controls": "NOT_YET_EVALUATED_NO_FPR_CLAIM",
        }
        if result["independent_validation_eligible"]:
            result["failure_layer"] = None
    except ProductError as exc:
        result["error_code"] = exc.code
        result["feedback"] = {"layer": result["failure_layer"], "error_code": exc.code}
        with repo.store.connect() as c:
            row = c.execute(
                "SELECT payload_json FROM investigation_provider_calls_v050 WHERE call_key=?",
                (key,),
            ).fetchone()
        if row:
            payload = json.loads(row[0])
            result["feedback"]["schema_errors"] = payload.get(
                "schema_validation_errors", []
            )
            result["feedback"]["parameters"] = payload.get(
                "draft_parameter_diagnostics"
            )
    except ValueError as exc:
        from pydantic import ValidationError

        code = (
            "SEMANTIC_SCHEMA_INVALID" if isinstance(exc, ValidationError) else str(exc)
        )
        result["error_code"] = code
        result["feedback"] = {"layer": result["failure_layer"], "error_code": code}
        if isinstance(exc, ValidationError):
            result["feedback"]["schema_errors"] = [
                {"location": e["loc"], "type": e["type"]}
                for e in exc.errors(
                    include_input=False, include_context=False, include_url=False
                )
            ]
        with repo.store.connect() as c:
            c.execute(
                "INSERT OR IGNORE INTO knowledge_rejections_v050 VALUES (?,?,?,?,?)",
                (
                    key,
                    env,
                    discovery["snapshot_sha256"],
                    code[:240],
                    datetime.now(UTC).isoformat(),
                ),
            )
    result["budget_after"] = inspect(DATA)
    result["protected_unchanged"] = protected(repo) == protocol["protected"]
    save(result_path, result)
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"development", "feedback"}},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
