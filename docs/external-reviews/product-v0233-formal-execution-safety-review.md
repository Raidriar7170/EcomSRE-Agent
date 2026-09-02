# Product v0.2.3.3 Formal Execution Safety Review

- Reviewed at: `2026-09-01T05:14:38Z`
- Review mode: third independent, read-only pre-execution review
- Review base and upstream HEAD: `de5af6821a043cd9d13ae21e737dafdd455ebf6f`
- Verdict: `PASS`
- Must Fix: `0`
- Claim Accuracy: `PASS`
- Live / formal / Docker actions during review: `NONE`

## Reviewed implementation snapshot

| Path | SHA-256 |
|---|---|
| `src/ecomsre/product/pilot/formal_live_v0233.py` | `bb246182b577982d07b1dbad545f744ed3896147e5e9b100f4875a8007fcc6e9` |
| `src/ecomsre/product/pilot/fresh_formal_source_v0233.py` | `2d0518db8117008fc6e265a9e914f4280e75b54e793d706faa43a4c29bce7b27` |
| `src/ecomsre/product/pilot/repository_state_v0233.py` | `ec6f2932229200e90a67888b58f50ee8d4fc845e83a0c0f7353a9bc96a9631a8` |
| `scripts/product_v0233/run_formal_nofault.py` | `5d933b7c2de84e3e5d5f7ca573b9f2032ee2cf518a4b6d8c1d2a7ba28d03fbfc` |
| `tests/product_v0233/test_increment4_formal_live_contracts.py` | `183ad3e4a5ea527cabdf8353dee172f88e2b9a338eb6d65c46915ade75bbdd1b` |

## Findings

1. A consumed formal reservation is protected by one fail-closed terminal domain. The public runner resumes only an existing terminal-publication intent or freezes a typed acceptance-artifacts blocker, `FORMAL_BLOCKED` repository manifest, blocked progress, and a new terminal-publication intent. It does not resume live or formal actions.
2. Reservation create-then-`chmod`/`stat` failures and post-finally construction failures are covered by public-runner fault-injection tests.
3. Terminal publication persists a sealed private intent before public mutation and is idempotently recoverable after an injected public-write failure.
4. Safety closure compares `baseline_count`, `active_baseline_count`, `baseline_job_count`, and `verify_job_count`, in addition to fault, knowledge, queue, and Diagnosis action counters.
5. Fault-attempt and Knowledge-Loop execution counts derive from a sealed action-event journal. An incomplete recovery journal is recorded as `UNAVAILABLE` with `safe=false`; successful acceptance explicitly requires both counts to be zero.
6. Mutation-capable dispatch sites are journaled before invocation. The reviewed runner contains no Fault or Knowledge-Loop dispatch and grants no Provider, Agent, Runbook, Fault, or Knowledge action authority.
7. The frozen semantic surface, exact source and destination bindings, untruncated terminal cardinalities, clone-zero blockers, source read-only access, and one-shot/no-retry contracts remain intact.
8. The first runner process invocation stopped before reservation because the current `ProductJobRecordV1` model could not reserialize the frozen v0.2.3 Job bytes identically. Reservation, clone, live execution, Incident, Diagnosis, and measured-result counts therefore remained zero; this was not a formal execution.
9. The repaired historical loader verifies the original v0.2.3 start, completion, ledger, and builder-Job seals without reserializing the frozen Job through the evolved model. Before parsing, it also requires the predecessor artifact's raw bytes and SHA-256 to equal the unique, non-symlink artifact in the current clean, pushed exact-head checkout.
10. The first narrow repair review correctly returned `BLOCKED`, `Must Fix: 1`, and `Claim Accuracy: FAIL` because self-consistent seals alone did not bind a dirty predecessor worktree to the exact-head frozen original. The reviewed snapshot closes that Must Fix and the follow-up verdict is `PASS`, `Must Fix: 0`, `Claim Accuracy: PASS`, and `Should Fix: 0`.

## Fresh checks

- Final narrow-review focused tests: `32 passed`
- Product v0.2.3.2.3 and v0.2.3.3 adjacent tests: `121 passed`
- Ruff lint and format: `PASS`
- Targeted Mypy: `Success: no issues found in 2 source files`
- Canonical pre-execution verifier: `PASS`
- History verifier: `PASS`
- `git diff --check`: `PASS`
- Formal reservation, private execution root, formal clone, closure, blocker, and measured result: `ABSENT`
- Exact-head raw-byte binding tests: positive admission plus 11 fully resealed dirty-predecessor drift classes `PASS`

The implementation is admitted to commit/push preparation. This review does not itself execute or authorize a second formal attempt, retry, Fault campaign, Knowledge Loop, Provider call, Agent write, Runbook execution, or any action beyond the active Goal.
