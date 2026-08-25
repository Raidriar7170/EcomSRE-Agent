# DTA v2.2.6 Independent Pre-Live Review

## Review 1 — `f0e683d7af094d049e6372c4e48fc0a473a3f395`

Scope: read-only. Docker and Provider calls: `0 / 0`.

Fresh evidence:

- v2.2.6 focused tests: `42 passed`
- deterministic old PR #67 capture gate: `PASS`
- private Provider iteration-03 SHA matched its public binding and recorded `8 / 8 VALID_TERMINAL`
- Ruff, scoped mypy, and `git diff --check`: `PASS`

Findings:

1. Current bootstrap failures were mis-staged because action construction, dispatch, conversion, baseline construction, and memory construction ran inside one helper while `BOOTSTRAP_ACTION_BUILD` remained active.
2. The production live wrapper accepted any `ReadBackend` while unconditionally claiming `LocalSandboxReadBackend`; pre-resource failures also displaced the typed arm result.
3. The v2.2.6 final truth-late execution, scorer, and no-score-driven-retry contracts were absent.

Seven-question disposition:

1. Resource Comparison Set instead of exact metric equality: `YES`.
2. Shared terminalizer/baseline/ontology/Prompt/truth/scorer: `NO — scorer absent`.
3. Accurate Model-directed naming: `YES`.
4. PR #67 deterministic and real-Provider development: `YES`.
5. Accurate typed stage and safe code for every failure: `NO`.
6. One physical two-target read without writes: `MECHANICALLY YES — backend identity claim insufficient`.
7. One no-score-retry campaign sufficient: `SCIENTIFICALLY YES — executable enforcement absent`.

```text
Must Fix:
3

Claim Accuracy:
FAIL
```

## Review 2 — `9670de64f197e2ae33f7581978313794385a66e7`

Scope: read-only. Docker and Provider calls: `0 / 0`.

Fresh evidence:

- v2.2.6 focused tests: `49 passed`
- deterministic old PR #67 capture gate: `PASS`
- private Provider iteration-03 SHA matched its public binding and recorded `8 / 8 VALID_TERMINAL`
- Ruff, scoped mypy, and `git diff --check`: `PASS`

The three Review 1 blockers were substantially repaired. Two further fail-closed
gaps remained:

1. Baseline-profile construction still ran while `BOOTSTRAP_MEMORY_BUILD` was
   active, so an injected builder failure was misclassified as
   `MEMORY_CONSTRUCTION_FAILED` instead of `BASELINE_PROFILE_INVALID`.
2. The scorer did not bind its live arguments to physical-state roles; swapping
   the baseline and fault live-shadow arguments could still mint the supported
   transfer terminal.

Seven-question disposition:

1. Resource Comparison Set instead of exact metric equality: `YES`.
2. Shared terminalizer/baseline/ontology/Prompt/truth/scorer: `NO — scorer live-state roles were unbound`.
3. Accurate Model-directed naming: `YES`.
4. PR #67 deterministic and real-Provider development: `YES`.
5. Accurate typed stage and safe code for every failure: `NO — baseline construction was mis-staged`.
6. One physical two-target read without writes: `YES`.
7. One no-score-retry campaign sufficient: `YES`.

```text
Must Fix:
2

Claim Accuracy:
FAIL
```

## Review 3 — `02f6512dfda2f25824dd8b8d4a51c6e66d73b163`

Scope: read-only. Docker and Provider/network calls: `0 / 0`.

Fresh evidence:

- exact clean HEAD: `02f6512dfda2f25824dd8b8d4a51c6e66d73b163`
- v2.2.6 focused tests: `52 passed`
- deterministic old-capture gate: `DTA_V226_DETERMINISTIC_OLD_CAPTURE_GATE_PASS`
- frozen Provider iteration-03 SHA-256 matched its public binding and retained
  `8 / 8 VALID_TERMINAL` with zero protocol, runner, or transport failures
- Ruff, scoped mypy, and `git diff --check`: `PASS`

Both Review 2 blockers are closed. Baseline construction failures now retain
`BOOTSTRAP_DISPATCH` as the last completed stage and fail at
`BASELINE_PROFILE_BUILD / BASELINE_PROFILE_INVALID`; the successful trace still
reaches `COMPLETE`. Live-shadow models bind `fault-*` and `baseline-*` case IDs
to their admitted physical-state roles, and the scorer independently rejects
swapped live arguments before scoring.

Seven-question disposition:

1. Resource Comparison Set instead of exact metric equality: `YES`.
2. Shared terminalizer/baseline/ontology/Prompt/truth/scorer: `YES`.
3. Accurate Model-directed naming: `YES`.
4. PR #67 deterministic and real-Provider development: `YES`.
5. Accurate typed stage and safe code for every reviewed failure path: `YES`.
6. One physical two-target read without writes: `YES`.
7. One no-score-retry campaign sufficient: `YES`.

Docker admission may open only for the already authorized bounded v2.2.6 live
campaign under the Goal's ownership, restoration, cleanup, and no-write
constraints.

```text
Must Fix:
0

Claim Accuracy:
PASS
```

## Review 4 — live orchestration addendum

Pending on the exact commit that adds the write-once v2.2.6 manifest,
preflight, and live-sequence orchestration. This addendum does not reopen the
Review 3 acquisition/terminalization result; Docker will remain unopened until
the new lifecycle wrapper is also reviewed for ownership, restoration, cleanup,
single-execution, and no-write enforcement.
