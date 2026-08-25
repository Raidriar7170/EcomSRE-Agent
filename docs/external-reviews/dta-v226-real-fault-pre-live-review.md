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

## Review 3

Pending on the exact second post-fix commit. Docker admission remains closed
until the independent result is exactly `Must Fix: 0 / Claim Accuracy: PASS`.
