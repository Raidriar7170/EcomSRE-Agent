"""One explicit development-control amendment; no independent experiment ledger."""

from datetime import UTC, datetime
import json
import hashlib
from pathlib import Path

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22 as sha
from ecomsre.product.investigation import closure_budget
from ecomsre.product.knowledge import selection_lock_v050, split_v050

SLOT = "D_CORE_FIX_01"
KEY = "control-repair"
VERSION = "observed-configuration-rollout-v1"


REPO = Path(__file__).resolve().parents[4]
AUTHORITY = "docs/goals/EcomSRE_v0.5_Development_Control_Repair_Amendment.md"
COLLECTION = "docs/results/product-v050/final-learning-closure/control-repair-plan.md"
SOURCES = (
    "src/ecomsre/product/knowledge/control_repair_v050.py",
    "src/ecomsre/product/knowledge/split_v050.py",
    "src/ecomsre/product/knowledge/capability_successor_v050.py",
    "scripts/product_v050/change_audit.py",
    "scripts/product_v050/final_closure.py",
    "scripts/product_v050/final_closure_live.py",
    "scripts/product_v050/live_environment.py",
)


def inputs():
    def digest(path):
        return hashlib.sha256((REPO / path).read_bytes()).hexdigest()

    return dict(
        authority_sha256=digest(AUTHORITY),
        collection_sha256=digest(COLLECTION),
        source_sha256={p: digest(p) for p in SOURCES},
    )


def verify_sources(value, execution_resume=None):
    expected = value
    if execution_resume is not None:
        if execution_resume["parent_contract_sha256"] != sha(value):
            raise ValueError("execution resume parent differs")
        expected = execution_resume
    if any(expected.get(k) != v for k, v in inputs().items()):
        raise ValueError("control repair source or collection identity differs")


RESUME_KEY = "control-repair-execution-resume"


def install_execution_resume(
    runner, *, prior_sources, cleanup, predecessor_sha256, rejection
):
    """One zero-dispatch 05-to-06 continuation, not another acquisition slot."""
    import ast

    parent = runner._get(KEY)
    request = runner._get("request:0")
    if parent is None or request is None:
        raise ValueError("one prepared-request execution resume required")
    existing = runner._get(RESUME_KEY)
    if existing is not None:
        expected = dict(
            parent_contract_sha256=sha(parent),
            prepared_request_sha256=sha(request),
            prior_sources=prior_sources,
            cleanup=cleanup,
            predecessor_sha256=predecessor_sha256,
            predispatch_rejection=rejection,
            **inputs(),
        )
        if any(existing.get(k) != v for k, v in expected.items()):
            raise ValueError("execution resume is immutable")
        return existing
    if (
        rejection.get("error") != "PROVIDER_INPUT_TOO_LARGE"
        or rejection.get("dispatch_occurred") is not False
        or rejection.get("unchanged_limit_bytes") != 192000
        or not 0 < rejection.get("full_wire_payload_bytes_after", 0) <= 192000
    ):
        raise ValueError(
            "bounded pre-dispatch rejection and projection repair required"
        )
    if set(prior_sources) != set(SOURCES) or any(
        hashlib.sha256(prior_sources[p].encode()).hexdigest()
        != parent["source_sha256"][p]
        for p in SOURCES
    ):
        raise ValueError("original collection source bytes required")
    current = inputs()
    if any(current[k] != parent[k] for k in ("authority_sha256", "collection_sha256")):
        raise ValueError("execution resume cannot change collection contract")

    # The successful D operation is the collection version subsequently used by
    # N6. Only deployment plumbing and lossless model projection may change.
    live = "scripts/product_v050/final_closure_live.py"
    before, after = ast.parse(prior_sources[live]), ast.parse((REPO / live).read_text())
    if ast.dump(before) != ast.dump(after):
        for name, previous, following in (("ROOT", "05", "06"), ("FAILED", "04", "05")):
            for tree, suffix in ((before, previous), (after, following)):
                assignments = [
                    n
                    for n in tree.body
                    if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)
                ]
                expected = ast.parse(
                    f'{name} = DATA / "live-final-closure-{suffix}"'
                ).body[0]
                if len(assignments) != 1 or ast.dump(assignments[0]) != ast.dump(
                    expected
                ):
                    raise ValueError("exact execution deployment path change required")
                assignments[0].value = ast.Constant(value="RETAINED_EXECUTION_IDENTITY")
        if ast.dump(before) != ast.dump(after):
            raise ValueError(
                "validated collection module changed beyond deployment paths"
            )
    for path in (
        "scripts/product_v050/change_audit.py",
        "src/ecomsre/product/knowledge/split_v050.py",
    ):
        if current["source_sha256"][path] != parent["source_sha256"][path]:
            raise ValueError("validated change audit or split changed")
    if not cleanup.get("result", {}).get("clean") or any(
        cleanup["result"]["remaining"].values()
    ):
        raise ValueError("previous owned cleanup must be clean")
    if runner.evo.knowledge._diagnosis(runner.incident(SLOT)).terminal.value not in {
        "CORE_KNOWN",
        "EXTENSION_KNOWN",
    }:
        raise ValueError("resume requires established development control")
    with runner.store.connect() as c:
        budget = closure_budget.load(c)
        if budget is None or closure_budget.ledger(c) != budget["baseline"]:
            raise ValueError("execution resume requires zero new Provider dispatch")
        if selection_lock_v050.load(c) is not None or runner._get("selection-pending"):
            raise ValueError("execution resume must precede selection")
        if c.execute(
            "SELECT 1 FROM knowledge_closure_runner_v050 WHERE entry_key LIKE 'attempt:%' OR (entry_key LIKE 'request:%' AND entry_key != 'request:0')"
        ).fetchone():
            raise ValueError("execution resume requires only first prepared request")
        for slot in ("N4", "N5", "N6", "N7"):
            if (
                runner._get("episode:" + slot)
                or c.execute(
                    "SELECT 1 FROM knowledge_episode_incidents_v050 WHERE episode_id=?",
                    (runner.plan["slots"][slot],),
                ).fetchone()
            ):
                raise ValueError("execution resume requires unexposed holdout")
        from ecomsre.product.knowledge.capability_successor_v050 import load

        mapping = load(c, runner.environment_id)
        if mapping is None or mapping["sha256"] != predecessor_sha256:
            raise ValueError("execution predecessor differs")
    return runner._keep(
        RESUME_KEY,
        dict(
            version="zero-dispatch-owned-05-to-06-v1",
            environment_id=runner.environment_id,
            parent_contract_sha256=sha(parent),
            prepared_request_sha256=sha(request),
            prior_sources=prior_sources,
            cleanup=cleanup,
            predispatch_rejection=rejection,
            predecessor_sha256=predecessor_sha256,
            from_root="live-final-closure-05",
            to_root="live-final-closure-06",
            validated_collection_functions_unchanged=True,
            created_at=datetime.now(UTC).isoformat(),
            **current,
        ),
    )


def read_execution_resume(connection, environment_id):
    row = connection.execute(
        "SELECT payload_json FROM knowledge_closure_runner_v050 WHERE entry_key=?",
        (RESUME_KEY,),
    ).fetchone()
    if row is None:
        return None
    wrapped = json.loads(row[0])
    value = wrapped["value"]
    parent = read(connection, environment_id)
    request = connection.execute(
        "SELECT payload_json FROM knowledge_closure_runner_v050 WHERE entry_key='request:0'"
    ).fetchone()
    if (
        wrapped["sha256"] != sha(value)
        or parent is None
        or value["parent_contract_sha256"] != sha(parent)
        or value["environment_id"] != environment_id
        or value["version"] != "zero-dispatch-owned-05-to-06-v1"
        or request is None
        or sha(json.loads(request[0])["value"]) != value["prepared_request_sha256"]
    ):
        raise ValueError("execution resume binding differs")
    return value


def install(runner, *, authority_sha256, collection_sha256, source_sha256):
    """Append the child before data, preserving original budget/plan/cohort bytes."""
    if any(
        len(v) != 64 or any(x not in "0123456789abcdef" for x in v)
        for v in [authority_sha256, collection_sha256, *source_sha256.values()]
    ):
        raise ValueError("explicit amendment and source digests required")
    verify_sources(
        dict(
            authority_sha256=authority_sha256,
            collection_sha256=collection_sha256,
            source_sha256=source_sha256,
        )
    )
    previous = runner._get(KEY)
    if previous is not None:
        if any(
            previous[k] != v
            for k, v in {
                "authority_sha256": authority_sha256,
                "collection_sha256": collection_sha256,
                "source_sha256": source_sha256,
            }.items()
        ):
            raise ValueError("control repair contract is immutable")
        return previous
    if runner.primary_level != "A" or runner._get("selection-pending"):
        raise ValueError("repair requires preselection Level A")
    if any(runner._get("episode:" + s) for s in ("N4", "N5", "N6", "N7")):
        raise ValueError("repair cannot follow independent collection")
    if any(
        runner._get(f"{kind}:{i}") for kind in ("request", "attempt") for i in range(6)
    ):
        raise ValueError("repair must precede proposal preparation")
    parent = runner.freeze_cohort()
    if (
        runner.evo.knowledge._diagnosis(runner.incident("N3")).terminal.value
        != "INSUFFICIENT_EVIDENCE"
    ):
        raise ValueError("repair requires retained insufficient N3")
    with runner.store.connect() as c:
        budget = closure_budget.load(c)
        if (
            budget is None
            or closure_budget.ledger(c) != budget["baseline"]
            or selection_lock_v050.load(c)
        ):
            raise ValueError("repair must precede new Provider work and selection")
        manifest = split_v050.effective_manifest(c, runner.environment_id)
        if c.execute(
            "SELECT 1 FROM knowledge_episode_incidents_v050 WHERE episode_id IN (?,?,?,?)",
            tuple(runner.plan["slots"][s] for s in ("N4", "N5", "N6", "N7")),
        ).fetchone():
            raise ValueError("repair requires unconsumed holdout and recurrence")
        existing_ids = sorted(
            r[0] for r in c.execute("SELECT incident_id FROM incidents")
        )
    ledger = runner.episode_ledger()
    if len(ledger) != runner._get("plan")["baseline_episodes"] + 3:
        raise ValueError("repair requires exactly original N1-N3 starts")
    # Resumption gets a new pre-blind calendar window with the original duration;
    # incident query/sampling windows and all old plan bytes remain unchanged.
    window_start = datetime.now(UTC)
    original_range = runner.plan["time_range"]
    duration = datetime.fromisoformat(original_range["end"]) - datetime.fromisoformat(
        original_range["start"]
    )
    if duration.total_seconds() <= 0:
        raise ValueError("invalid original collection duration")
    value = dict(
        time_range={
            "start": window_start.isoformat(),
            "end": (window_start + duration).isoformat(),
        },
        version=VERSION,
        environment_id=runner.environment_id,
        authority_sha256=authority_sha256,
        collection_sha256=collection_sha256,
        source_sha256=source_sha256,
        parent_plan_sha256=sha(runner.plan),
        parent_cohort_sha256=sha(parent),
        parent_split_sha256=sha(manifest),
        parent_budget_sha256=budget["sha256"],
        old_results={
            s: {k: runner._get(k + ":" + s) for k in ("episode", "binding", "terminal")}
            for s in ("N1", "N2", "N3")
        },
        baseline_ledger_sha256=sha(ledger),
        slot=SLOT,
        episode_id=runner.plan["slots"]["N3"] + "-repair-01",
        role="DEVELOPMENT",
        cumulative_live_limit=13,
        round_live_limit=8,
        unchanged_future={s: runner.plan["slots"][s] for s in ("N4", "N5", "N6", "N7")},
        preexisting_incident_ids=existing_ids,
        created_at=datetime.now(UTC).isoformat(),
    )
    return runner._keep(KEY, value)


def read(connection, environment_id):
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='knowledge_closure_runner_v050'"
    ).fetchone():
        return None
    row = connection.execute(
        "SELECT payload_json FROM knowledge_closure_runner_v050 WHERE entry_key=?",
        (KEY,),
    ).fetchone()
    if row is None:
        return None
    retained = json.loads(row[0])
    value = retained["value"]
    if retained["sha256"] != sha(value) or value["environment_id"] != environment_id:
        raise ValueError("repair contract binding differs")
    if (
        value["version"],
        value["slot"],
        value["role"],
        value["cumulative_live_limit"],
        value["round_live_limit"],
    ) != (VERSION, SLOT, "DEVELOPMENT", 13, 8):
        raise ValueError("repair contract scope differs")
    return value
