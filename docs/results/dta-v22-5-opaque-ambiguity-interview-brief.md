# DTA v2.2.5 Interview Brief

## 30-second version

An earlier ambiguity study was invalid because Provider-visible service names
leaked evaluator truth, incomplete evidence could fail open, and the preflight
did not bind the whole execution surface. I built a new successor with opaque
mechanism-independent identities, a read-time coverage ledger with typed
fail-closed set closure, and a manifest binding 71 runtime files, 16 new case
files, the scorer, Prompt, model, schedule, pacing, retries, and outputs. After
an independent Must Fix 0 review, I ran exactly one 64-run study. TARGET_SET and
BUNDLE_ONE reached 16/16 exact, BUNDLE_SET 15/16, and fail-open/forgotten counts
were zero. The pooled preregistered thresholds still did not pass, so I retained
the honest terminal `DTA_V22_5_NO_AMBIGUITY_EFFECT_OBSERVED`.

## Personal contribution

- Preserved closed PR #65 as an unmerged `INVALID` predecessor and selectively
  ported only its useful ambiguity/bundle implementation.
- Generated opaque service, operation, and change identities before mechanism
  assignment and linted every exact rendered Provider payload before transport.
- Removed internal hypothesis/action/set identifiers from Provider projections.
- Recorded coverage at read time, rebuilt new ambiguity sets from prior reads,
  and derived `forgotten_preclosure_read_count` from the ledger rather than a
  constant.
- Made incomplete-set insufficient-budget and source-failure paths expose typed
  `ABSTAIN` without reopening `NO_INCIDENT`.
- Defined all denominators from frozen evaluator strata, independent of
  treatment-produced ambiguity fields.
- Bound the complete runtime/evaluation/schedule/model/Prompt/pacing/retry/output
  surface in a two-commit, non-self-referential manifest and fail-closed
  preflight.
- Preserved the one observed transport failure and negative measured terminal
  without tuning or rerunning the final study.

## Architecture to explain

```text
new opaque replay bytes
  -> mechanism-independent service/operation/change IDs
  -> Salient Memory + predicates + Gap Graph
  -> target actions or one contrastive bundle action
  -> read-time ambiguity coverage ledger
  -> [one-target | complete-set] NO_INCIDENT closure
  -> opaque Provider terminal-selection payload + pre-transport lint
  -> frozen evaluator truth and strata loaded after four case-local runs
  -> fixed-denominator factorial scorer
```

Only action granularity and closure scope vary:

```text
TARGET_ONE
TARGET_SET
BUNDLE_ONE
BUNDLE_SET
```

Model snapshot, Prompt, case bytes, action/routing policy, schedule, pacing,
repair/retry limits, output paths, and truth-isolation rule are shared.

## Strongest evidence

- Complete accounting: 16 cases, 64 unique scheduled runs, execution count 1,
  64-line partial journal, zero uncaught exceptions, zero Agent writes.
- Identity boundary: 64 exact runtime payloads plus bootstrap/repair linted, zero
  forbidden identities, case IDs, or evaluator metadata.
- Fail-closed boundary: zero fail-open `NO_INCIDENT` and zero forgotten
  pre-closure reads; offline typed budget/source-failure regressions pass.
- Quality: TARGET_ONE 12/16, TARGET_SET 16/16, BUNDLE_ONE 16/16, BUNDLE_SET
  15/16; all control accuracies are 1.0.
- Ambiguity: TARGET_SET recovered all 4 wrong-target-first resource incidents;
  bundle reads localized all 8 resource incident predicates in one read.
- Reliability: zero protocol failures/repairs, two bounded transport retries,
  and one preserved terminal-selection transport failure.

## Why the result stayed negative

The measured terminal is based on pooled factor thresholds, not the best arm.
Closure improved resource accuracy by 0.1875 and premature `NO_INCIDENT` by
0.25, below the joint 0.25/0.50 threshold. Bundle granularity reduced pooled
Resources reads by only 0.3, did not reduce Provider calls, and increased pooled
tokens and latency. The sole BUNDLE_SET transport failure also left that arm at
7/8 resource exact rather than 8/8. These observed values mint the preregistered
no-effect terminal even though the engineering safety boundary held.

## Safe claims and non-claims

Safe: opaque Provider identity projection on the bound payload surface,
independently reviewed complete preflight, read-time coverage preservation,
typed fail-closed closure, fixed evaluator denominators, exact one-study
accounting, and a preserved negative factorial result.

Do not claim: a positive ambiguity effect, statistical or generalization
evidence, robust Provider reliability, live SRE operation, remediation,
Docker/runtime validation, or production readiness.
