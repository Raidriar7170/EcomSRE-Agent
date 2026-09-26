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


def verify_sources(value):
    if any(value.get(k) != v for k, v in inputs().items()):
        raise ValueError("control repair source or collection identity differs")


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
    value = dict(
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
