# Product v0.5 continuation 01 — review repairs and failed Provider smoke

This append-only successor begins at `cd086826b23b728c6527b8b6981aeffef489c4e7`
on the same Draft [PR #104](https://github.com/Raidriar7170/EcomSRE-Agent/pull/104).
The [original acceptance](../acceptance.json) remains the historical zero-call
stage; it has not been rewritten as real-model evidence.

## Current evidence

- R1: reproduced cross-hypothesis support splicing, then bound support and checked
  prediction references to the same runtime hypothesis ID. Numeric success does
  not establish causality; alternatives remain visible.
- R2: finite resource-query dependencies bind supplemental CAS observations to
  incident, environment, capability, target and window. Development and holdout
  consume verified persisted observations; normal diagnosis performs the same
  deterministic query on the new event with zero LLM calls. The end-to-end test
  starts without initial Resource snapshots. Its direct fixture registry insertion
  proves matcher plumbing, not actual model learning or governance promotion.
- R3: nonempty numeric counterexamples remain scoped FALSE predictions. Empty,
  timeout, truncated, missing coverage and wrong-window observations remain
  UNKNOWN. Complete log-absence semantics are unsupported by these templates.
- R4: immutable harness split/episode bindings and global append-only exposure
  protect candidates and revisions. Failed dispatch retains exposure; aliases
  within one episode cannot inflate the independent denominator.

The project `provider.env` exists and contains the three Provider values, but they
were not loaded in the original process. The new literal allowlist loader never
executes shell or prints credentials. API and Worker use the same process
configuration in this smoke; no claim is made about an unrelated running service.
The exact configured model remains `gpt-5.4-mini-2026-03-17`.

[Official model documentation](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
checked on 2026-09-17 lists that snapshot and standard input USD0.75 / output USD4.50
per million tokens (cached input USD0.075). The dated price profile uses the
uncached input upper rate, standard tier, non-regional direct API and no hosted
tools. Usage-based upper estimates are not invoices.

## Actual attempts, including failure

[provider-attempts.json](provider-attempts.json) retains all four dispatches:

1. Chat Completions: transport failure; no usage returned.
2. New replay incident, same ledger: HTTP404; no structured error code or usage.
3. Read-only exact-model metadata: HTTP200, exact identity confirmed. Conservatively
   included in the request ledger; no generation performed.
4. New replay incident, explicit Responses adapter: HTTP404; no usage returned.

The [Responses function-calling contract](https://developers.openai.com/api/docs/guides/function-calling)
was checked before adding that explicit adapter. There is no automatic endpoint
or model fallback. The Responses request uses `store=false`; hidden reasoning is
not retained. All old sessions and reservations remain intact.

**No real model decision or model-selected tool roundtrip succeeded.** No knowledge
proposer, live incident, frozen live holdout, model-rule promotion or learned-rule
recurrence was executed. Each protocol attempt uses the same existing fixture
capture, not independent accidents. Live episode count and Product external writes
remain zero. No Docker runtime was created; cleanup is not required, not live CLEAN.

Cumulative ledger: **4 requests / 200**, unknown usage **4**, retained cost
commitment **USD0.095449 / 20**, actual invoice cost **unknown**. Remaining request
budget **196**; remaining commitment budget **USD19.904551**. No budget reset.
The precise missing condition is a working generation interface for the configured
project credential/model: model metadata works, both generation interfaces return
404. No alternate model/account or credentials from another application were used.

## Verification

Run from the retained worktree using its locked virtual environment:

```bash
PYTHONPATH=src:. .venv/bin/python -m scripts.product_v050.provider_smoke
PYTHONPATH=src:. .venv/bin/python -m scripts.ci.verify_product_v050
PYTHONPATH=src:. .venv/bin/python -m scripts.ci.verify_product_v050_history
PYTHONPATH=src:. .venv/bin/python -m scripts.ci.verify_product_v050_continuation
```

The smoke command without `--execute` is read-only and does not request inference.
Consumed attempts are create-once; do not rerun them or delete markers. The first
verifier anchors the historical package to its published commit; the continuation
verifier checks current source-bound tests and appended failed-request evidence.

## Bounded terminal and verification boundary

`ECOMSRE_PRODUCT_V050_ENGINEERING_COMPLETE_WITH_LIMITATIONS` preserves the original
limited terminal; it is not acceptance PASS. Missing real generation access stops
smoke before paid batches, independent live events, proposer, holdout or promotion.
Unblock requires a working generation interface for this exact authorized project
credential/model, then resume the same retained ledger and budget.

[checks.json](checks.json) distinguishes clean pre-followup full-suite evidence
(6608 passed, 21 skipped) from the current 67 source-bound fixture checks. Final
repairs also reject wrong-source results before projection, verify nonempty parent
diagnoses at save/load, and serialize development checks plus durable reservation
against competing freeze writers. The no-parent pre-diagnosis path remains valid.
The independent reviewer reports no open Must Fix in the reviewed boundaries.
Final exact-head CI must be green on Draft PR #104 before task closure; CI identity
is linked at the PR rather than recursively embedded in its own commit.
