# RCAEval Metrics Arbitration v1

## Decision lineage and boundary

This runtime implements the PR #20 `METRICS_ARBITRATION` decision frozen at
`59ace4d`. The source decision measured positive M3 Root rescue with zero damage
across Candidate-3, Candidate-4, and Candidate-5 preserved Initial fixtures.
Metrics Arbitration v1 is a new independent evaluation/runtime, not
Candidate-6, and it does not modify the Adaptive v2 Gate, specialist, Fusion,
or indicator contracts.

The evidence class is consumed OB/SS development data. It is not external
validation, does not access RE2-TT, and cannot support a production-
generalization claim.

## Runtime

```text
bounded Metrics + Logs + Traces tools
                |
                v
Strong Single ArchitectureContext -> Diagnosis   (one model call)
                |
                +-----------------------+
                |                       |
                v                       v
        Initial Diagnosis      deterministic F0 Metrics ranking
                |                       |
                +-----------+-----------+
                            v
                  deterministic M3 Root arbiter
                            |
                            v
         Final Root + exact Initial Indicator
```

The runtime constructs only the frozen v1 reference Provider and the dev3
transport proxy. It never constructs an Adaptive specialist Provider. Every
completed case therefore has exactly one semantic operation with role
`INITIAL_DIAGNOSIS`, three deterministic tool calls, zero Specialist calls, and
zero Fusion-model calls.

## Exact M3 rule

For a score-ordered service ranking, define:

```text
initial_rank_condition := Initial rank is None or Initial rank > 2
margin := (Top1 score - Top2 score) / max(abs(Top1 score), 1e-12)
margin_condition := margin >= 0.25
```

For a one-service ranking, `margin = 1.0`. The action is
`OVERRIDE_METRICS_TOP1` only when both conditions pass and Metrics Top-1 differs
from Initial. Otherwise it is `KEEP_INITIAL`.

KEEP preserves the exact immutable Initial Diagnosis, including model
confidence, evidence, and explanation. OVERRIDE changes only Root service,
preserves the Initial indicator, uses only legal `metric:NNNN` references for
the selected service, emits a deterministic explanation, sets changed-Root
confidence to null, and records `DETERMINISTIC_METRICS_M3` Root provenance.
Ground truth is not available to this decision path.

## Frozen inputs and replay

Before any Provider call, the production M3 function is replayed against the
three Git-external Candidate-3/4/5 frozen Initial fixture trees with their exact
tree hashes. Provider environment variables must be absent. The hard gate is:

| Fixture | Completed | Initial Root | M3 Final Root | Override | Rescue | Damage | Net |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Candidate-3 | 60 | 49 | 57 | 8 | 8 | 0 | +8 |
| Candidate-4 | 59 | 51 | 57 | 6 | 6 | 0 | +6 |
| Candidate-5 | 60 | 45 | 57 | 12 | 12 | 0 | +12 |

Any mismatch yields `M3_FIXTURE_REPLAY_MISMATCH` and forbids Provider Smoke.
Only aggregate replay results enter Git; case-level rows remain private.

## Provider and execution controls

The synthetic preflight uses a non-case manifest and synthetic evidence. It
must return a valid typed Diagnosis, known usage, zero observed HTTP 429, and no
schema error. It does not read case data or count toward TUNE. Failure yields
`BLOCKED_PROVIDER_CAPACITY_PREFLIGHT`.

Live execution is sequential with concurrency one, five-second minimum request
spacing, Retry-After support, and at most one allowlisted byte-identical
transport retry. Each semantic operation retains dev3 start, attempt, retry,
usage, and terminal sidecars. Run IDs use the independent
`metrics-arbitration-v1` domain and bind the split plus opaque case identity.
All terminals and case-level outcomes are create-once and Git-external.

Smoke selects the 12 Strong Single identities from the frozen smoke schedule.
TUNE selects 60 from the frozen design schedule and reuses those 12 terminals
under the same run namespace. Regression selects 120 from the consumed
dev-validation schedule only after a passing TUNE. Implementation and config
hashes bind every phase; only non-runtime documentation commits may occur
between TUNE and Regression.

## Gates

Smoke requires 12/12 terminalized, at least 11 completed, exactly one semantic
operation per completed case, zero Specialist/Fusion calls, zero schema,
privacy, or schedule failures, valid typed M3 decisions, computable same-run
metrics, and at most one terminal HTTP 429.

TUNE requires at least 58/60 completed, at most three terminal HTTP 429s, no
disqualifying failure, Final Root at least 51/60, Final Pair at least 27/60,
Root rescue greater than damage, Root net rescue at least +1, Root damage at
most two, nonnegative Pair net rescue with rescue not below damage, mean
semantic operations exactly one over completed cases, and zero
Specialist/Fusion calls.

Regression requires at least 114/120 completed, at most six terminal HTTP 429s,
no disqualifying failure, Final Root at least 97/120, Final Pair at least
50/120, nonnegative Root and Pair net rescue with rescue not below damage, Root
damage rate at most 5%, mean semantic operations exactly one over completed
cases, and zero Specialist calls. Regression is one-shot; its result cannot be
used to tune or rerun M3.

## Evaluation authority and public projection

The primary algorithm evidence is the paired Initial→Final comparison within
each Metrics Arbitration run. TUNE Strong Single Root `51/60`, Pair `29/60`,
and Regression Strong Single Root `99/120`, Pair `55/120`, are historical
`CROSS_RUN_CONTEXTUAL_BASELINE` context only.

The public projector reads only private aggregates and rejects concrete case
or run identifiers, service names, evidence references, private paths, raw
Provider output, and credentials. Public JSON, Markdown, and the Human Brief
preserve the exact terminal marker instead of smoothing a failed or pending
gate into success.
