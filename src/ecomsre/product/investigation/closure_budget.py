"""One immutable sub-budget over the original journal; harness-only activation.

Activation is not resource/safety admission. The closure runner must independently
pass ownership continuity before calling it. No provider or live work occurs here.
"""

import json

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22 as sha
from ecomsre.product.errors import ProductError

ROUND_ID = "ecomsre-v050-final-learning-closure-v1"
PROPOSAL_PREFIX = "knowledge-draft-v050.final:proposal:"


def load(connection):
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='investigation_closure_budget_v050'"
    ).fetchone():
        return None
    row = connection.execute(
        "SELECT payload_json FROM investigation_closure_budget_v050 WHERE round_id=?",
        (ROUND_ID,),
    ).fetchone()
    if row is None:
        return None
    payload = json.loads(row[0])
    if payload["sha256"] != sha({k: v for k, v in payload.items() if k != "sha256"}):
        raise ValueError("closure budget binding differs")
    return payload


def start(store, *, expected_ledger_sha256):
    """Seal original ledger identities once; never resets counters on restart."""
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        previous = load(c)
        if previous is not None:
            if previous["baseline_sha256"] != expected_ledger_sha256:
                raise ValueError("closure budget already bound to another baseline")
            return previous
        rows = ledger(c)
        if sha(rows) != expected_ledger_sha256:
            raise ValueError("closure budget ledger changed before activation")
        if any(r["state"] in {"DISPATCHING", "UNSAFE_COST_BOUND"} for r in rows):
            raise ValueError("unresolved active dispatch or unsafe cost bound")
        committed = sum(r["committed_microusd"] for r in rows)
        payload = dict(
            round_id=ROUND_ID,
            baseline_sha256=expected_ledger_sha256,
            baseline=rows,
            request_cap=max(0, min(40, 200 - len(rows))),
            committed_cap_microusd=max(0, min(8_000_000, 20_000_000 - committed)),
            semantic_cap=6,
        )
        payload["sha256"] = sha(payload)
        c.execute("""CREATE TABLE IF NOT EXISTS investigation_closure_budget_v050 (
            round_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)""")
        c.execute(
            "INSERT INTO investigation_closure_budget_v050 VALUES (?,?)",
            (ROUND_ID, json.dumps(payload, sort_keys=True)),
        )
        c.execute("COMMIT")
        return payload


def ledger(connection):
    return [
        dict(r)
        for r in connection.execute(
            "SELECT call_key,request_sha256,state,"
            "COALESCE(charged_microusd,reserved_microusd) AS committed_microusd "
            "FROM investigation_provider_calls_v050 ORDER BY call_key"
        )
    ]


def guard(connection, key, reserve, request):
    """Inside the existing reservation transaction, before any new dispatch."""
    payload = load(connection)
    if payload is None:
        if key.startswith(PROPOSAL_PREFIX):
            raise ProductError(
                "CLOSURE_ROUND_NOT_STARTED", "Closure budget is not activated."
            )
        return
    baseline = {r["call_key"]: r for r in payload["baseline"]}
    current = ledger(connection)
    # Settled historical rows are immutable; no deletes, rewrites or accounting
    # reductions can buy back this round's budget.
    retained = {r["call_key"]: r for r in current if r["call_key"] in baseline}
    if retained != baseline:
        raise ProductError("CLOSURE_LEDGER_DRIFT", "Original ledger binding changed.")
    new = [r for r in current if r["call_key"] not in baseline]
    if (
        len(new) >= payload["request_cap"]
        or sum(r["committed_microusd"] for r in new) + reserve
        > payload["committed_cap_microusd"]
    ):
        raise ProductError(
            "BUDGET_EXHAUSTED", "Final closure request or committed-cost cap reached."
        )
    # Existing proposal namespaces cannot revive an old round after activation.
    wire = request.get("payload", {})
    messages = wire.get("input", wire.get("messages", []))
    proposal_task = False
    for message in messages:
        if message.get("role") == "user":
            # Inspect the bound task envelope, never task-like text in observations.
            envelope = json.loads(message["content"])
            proposal_task |= envelope.get("task", "").startswith("propose_detection_")
    if proposal_task or key.startswith(("knowledge:", "knowledge-draft-")):
        if not key.startswith(PROPOSAL_PREFIX):
            raise ProductError(
                "CLOSURE_PROPOSAL_NAMESPACE",
                "New proposals require the closure namespace.",
            )
        attempts = sum(r["call_key"].startswith(PROPOSAL_PREFIX) for r in new)
        if attempts >= payload["semantic_cap"]:
            raise ProductError(
                "BUDGET_EXHAUSTED", "Final closure semantic slots exhausted."
            )
        if key != PROPOSAL_PREFIX + str(attempts):
            raise ProductError(
                "CLOSURE_SEMANTIC_ORDER", "Semantic slots must be monotonic."
            )
