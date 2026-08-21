# DTA v2.2.4 Ambiguity-Set and Resources-Bundle Error Analysis

## Frozen boundary

- Study: one fixed 16-case × 4-combination replay study, 64 represented runs.
- Full-study execution count: `1`.
- Provider: `gpt-5.4-mini-2026-03-17`, terminal selection only.
- Docker / Runbook / Agent writes: `0 / 0 / 0`.
- Protocol repairs / transport retries / runner exceptions: `0 / 1 / 0`.
- Measured terminal: `DTA_V22_4_COMBINED_AMBIGUITY_FIX_EFFECT_OBSERVED`.

## What failed in v2.2.3

The frozen v2.2.3 wrong-target cases `d05`, `d06`, and `d08` had two symmetric
resource candidates but only one target was read before `NO_INCIDENT` became
admissible. The chosen target had no captured resource record and produced no
predicate. The Provider then selected a terminal that the runtime had already
admitted; it did not choose the evidence target. Counterfactual target reversal
left the runtime visibility signature unchanged.

The v2.2.4 evidence therefore supports a partial-target-coverage and closure
diagnosis, not a bad terminal-selection diagnosis. Explicit normal records
make a healthy target an observed normal result instead of an absent record.

## Required questions

### Did TARGET_SET recover after a normal first target?

Yes. The four resource incidents whose anomalous service was the second
candidate were all diagnosed after two sequential Resources reads. Across all
8 resource incidents, TARGET_SET exact accuracy was `1.000`; premature
`NO_INCIDENT` after partial coverage was `0.000`.

### Did BUNDLE_SET localize the anomalous target in one read?

Yes. BUNDLE_SET issued one all-candidate Resources action on every one of the
10 target-complete resource cases. It localized all 8/8 resource incidents and
returned all-normal per-service facts for both No-Incident controls.

### Did explicit normal data remove sparse-capture ambiguity?

Yes within this synthetic/derived replay contract. Every CPU, memory, and
resource-normal case contains exactly two valid `ResourceUsageRecord` objects,
one per candidate, and declares Resources `TARGET_COMPLETE`. This result is not
evidence about a live collector or incomplete production telemetry.

### Did bundles reduce reads, calls, tokens, and latency?

Not all four. Relative to TARGET_SET, BUNDLE_SET reduced mean Resources reads
per resource case from `1.600` to `1.000`. Both arms made `16` Provider calls.
Tokens increased from `11,941` to `13,705`; they did not improve. Observed
aggregate latency decreased from `54,336.6 ms` to `45,400.8 ms`, but this
single fixed study supports only a descriptive latency observation.

### Did set closure add unnecessary all-normal work?

For individual actions, yes: TARGET_SET read both targets on each all-normal
resource control, while TARGET_ONE stopped after one. The contrastive bundle
removed that extra read: BUNDLE_SET covered both targets in one action.

### Did nonresource mechanisms regress?

No measured regression occurred. Configuration, service-unavailable, and
dependency accuracy were each `1.000` in all four combinations, and each arm
had `0` nonresource regressions. Combined No-Incident/abstention control
accuracy was also `1.000` throughout.

### Which factor caused the effect?

Both isolated changes solved the fixed ambiguity cases: TARGET_SET and
BUNDLE_ONE each reached 16/16 exact completion. Pooled set closure improved
resource accuracy by `0.250` and reduced premature No-Incident by `0.250`;
pooled bundle granularity improved resource accuracy by `0.250` and reduced
mean Resources reads by `0.300`. Neither isolated pooled effect met the
preregistered partial-effect threshold. The exact-rate interaction was
`-0.250`, consistent with redundant paths and a ceiling, not positive synergy.
BUNDLE_SET nevertheless met every preregistered combined-treatment endpoint
against TARGET_ONE. No formal significance or generalization claim is made.

## Classification counts and rates

| Classification | TARGET_ONE | TARGET_SET | BUNDLE_ONE | BUNDLE_SET |
|---|---:|---:|---:|---:|
| Exact completion | 12/16 | 16/16 | 16/16 | 16/16 |
| Resource ambiguity exact | 4/8 | 8/8 | 8/8 | 8/8 |
| Wrong target first | 4/8 | 4/8 | 0/8 | 0/8 |
| Premature NO_INCIDENT after partial set | 4/8 | 0/8 | 0/8 | 0/8 |
| Complete ambiguity coverage | 0/10 | 6/10 | 10/10 | 10/10 |
| Mean Resources reads/resource case | 1.000 | 1.600 | 1.000 | 1.000 |
| Bundle schema failure | n/a | n/a | 0 | 0 |
| Nonresource regression | 0 | 0 | 0 | 0 |

The 6/10 TARGET_SET completion count consists of four second-target incidents
plus two all-normal controls. When the first target was already anomalous,
Diagnosis correctly opened without an unnecessary second read.

## Remaining limits

- Cases are synthetic/derived and replay-only.
- The bundle does not infer the correct target; it queries candidates
  symmetrically and compares observed per-service records.
- Provider calls did not decrease and token cost increased for bundle arms.
- No Docker, live telemetry acquisition, Runbook, remediation, or production
  behavior was exercised.
