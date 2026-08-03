# Phase 2 Closeout

## Status

- Phase 2 local diagnosis-replay implementation: `VERIFIED`.
- Phase 3 entered: `NO`.
- This closeout is not Phase 0 acceptance, a release or deployment, a
  production-readiness claim, or a superiority claim.
- Provider credentials, endpoint details, and raw provider responses are not
  included in this document or the Git history.

## Implemented boundary

- Fixed Specialist Workflow and Dynamic Multi-Agent replay execution use the
  admitted DAG topology rather than declaration order.
- Dependency finding IDs are passed explicitly between admitted nodes; the RCA
  Judge receives the canonical admitted finding order.
- Phase 2 supports a no-retry OpenAI-compatible backend with one required typed
  tool response per admitted model lease. Local schema, identity, evidence,
  action, and token-budget contracts remain authoritative.
- The real-provider gate can acquire four independently preserved requirements
  and aggregate them only when provider identity, model snapshot, token-policy
  core, and no-fallback state agree.
- Existing Single-Agent replay behavior remains the frozen Phase 1 baseline.

## Offline comparison

The fresh deterministic comparison covers seven replay cases for each of the
three variants. Its semantic SHA-256 is
`3734e5814a5a0bbe139f7e7ca346e06f0d139ec4f9947b4a97cb6a34c7af14b4`.

| Variant | Cases | Failed cases | Decision accuracy | Schema-valid rate | Average tool calls |
| --- | ---: | ---: | ---: | ---: | ---: |
| Single-Agent | 7 | 0 | 7/7 | 7/7 | 2.86 |
| Fixed Specialist Workflow | 7 | 1 | 2/7 | 6/7 | 3.71 |
| Dynamic Multi-Agent | 7 | 1 | 2/7 | 6/7 | 2.14 |

Both Phase 2 variants fail the scripted
`ad-partial-failure-without-logs` case. These results are retained as the
current baseline; the verified report is not a performance or superiority
claim.

## Real-provider smoke

- Provider contract: `openai-compatible`.
- Model snapshot: `gpt-5.4-mini-2026-03-17`.
- Token-policy core SHA-256:
  `387bf51f7ccbf563ec45df8e89db0ddad75b51247a15146e6bbd95b50cc8201d`.
- Successful provider calls represented by the aggregate: `20`.
- Scripted fallback: `NO`.

| Requirement | Variant | Case | Expected and observed | Result |
| --- | --- | --- | --- | --- |
| Fixed positive | Fixed Specialist Workflow | `ad-partial-failure-complete` | `RCA_CONFIRMED` | `PASSED` |
| Dynamic positive | Dynamic Multi-Agent | `ad-partial-failure-complete` | `RCA_CONFIRMED` | `PASSED` |
| Fixed negative | Fixed Specialist Workflow | `no-real-incident` | `ABSTAIN` | `PASSED` |
| Dynamic negative | Dynamic Multi-Agent | `no-real-incident` | `ABSTAIN` | `PASSED` |

Transport and provider-protocol failures encountered during acquisition were
not converted into passes. Content-distinct failed case reports remain
preserved in the local ignored evidence root. The final aggregate was produced
offline from the latest four independently passed case reports.

## Fresh validation

- `make phase2-test`: `374 passed`.
- `make phase2-compare` followed by `make phase2-verify`: `VERIFIED`.
- `make phase1-test`: `877 passed`.
- Repository test suite: `2088 passed`.
- Phase 2 Ruff: `PASSED`.
- Phase 2 mypy: `PASSED`.
- Fresh read-only closeout re-review: `PASS`; no remaining Must Fix, Should
  Fix, or Nice to Have findings.
- Public closeout scope secret scan: `PASSED` across 14 files and seven
  credential-pattern rules; `.env` remains ignored.
- Public absolute-path scan: `PASSED`.
- `git diff --check`: `PASSED` before staging and is repeated at the final
  publication boundary.

Repository-wide Ruff is not represented as clean: two tracked findings already
exist at the publication baseline in Phase 0 environment modules, and one
additional finding is in the ignored frozen upstream tree. They are outside
this Phase 2 write scope.

## Deferred backlog

1. Improve the Fixed and Dynamic scripted comparison results without changing
   the frozen evaluation truth after seeing outcomes.
2. Diagnose the shared `ad-partial-failure-without-logs` terminal path in a new
   explicitly scoped phase.
3. Repair the pre-existing repository-wide Ruff findings in their owning
   Phase 0 or upstream-maintenance scopes.
4. Keep restricted remediation and all Phase 3 work deferred until separately
   authorized.
