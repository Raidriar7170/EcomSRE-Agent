# DTA v2.3.3 Domain-Bound Witness-Guard Error Analysis

## Frozen result

The new fixed 28-case × 3-arm comparison executed exactly once and minted:

`DTA_V233_DOMAIN_AND_GUARD_MIXED_RESULT`

The artifact contains 28 cases, 84 arm runs, 28 post-three-arm truth loads, zero runtime exceptions, zero action-authority violations, and no Agent writes, Runbook executions, Docker calls, or new live faults. It was not optimized or rerun after measurement.

## What the two P0 repairs changed

The v2.3.3 domain package materially narrowed P0-A on the new 16-case novelty denominator:

- root localization improved from 15/16 (`0.938`) to 16/16 (`1.000`);
- broad-domain accuracy improved from 2/16 (`0.125`) to 10/16 (`0.625`);
- top-two domain recall improved from 2/16 (`0.125`) to 14/16 (`0.875`);
- Provider domain drift remained zero.

The witness guard closed P0-B on the new four-control denominator:

- irreconcilable-control accuracy improved from 0/4 in both non-guard arms to 4/4 (`1.000`) in the combined arm;
- every control stopped as `CONFLICTING_EVIDENCE / IRRECONCILABLE`;
- every irreconcilable control used the single shared-budget guard-directed read and then made zero Provider calls;
- strong-witness precision and recall were both `1.000`;
- no novelty case was blocked by the guard.

Registered-known and No-Incident accuracy remained 4/4 and 3/3 across all arms. Combined false-novel rate was `0.000`, evidence-ref validity was `1.000`, and action authority remained `NONE`.

## Why the result is mixed

Every frozen mixed-result predicate passed. The positive-effect contract failed because combined broad-domain accuracy was `0.625`, below the predeclared `0.650` threshold by one correct case (`0.025`). The observed improvement over baseline was `+0.500`, but the absolute accuracy gate remains binding.

The six remaining domain misses are concentrated in ambiguity:

- four concurrency cases (`vx-311` through `vx-314`) returned `UNKNOWN`; concurrency was the runner-up and remained inside the top two;
- the network case `vx-315` returned `UNKNOWN`;
- the external case `vx-316` returned `UNKNOWN`.

The first four therefore preserve useful candidate ranking without meeting exact broad-domain accuracy. Network and external are the two top-two misses. No post-run heuristic, data, Prompt, scorer, or threshold change was made.

## Provider and cost observations

The three arms made 63 Provider calls and consumed 252,399 total tokens. The v2.3.2 baseline accounted for 30 calls, 10 protocol repairs, two frozen `PROTOCOL_FAILED` terminals (`vx-316` and `vx-324`), and 173,070 tokens. Domain-only used 16 calls with no repair or protocol failure. The combined arm used 17 calls, one protocol repair, no protocol failure, and 40,761 tokens. Exact-request transport retries were zero in every arm.

The baseline failures remain part of the measured artifact. They were not excluded, repaired after the campaign, or rerun. The combined arm's unresolved-question completeness was `0.3125`; this is consistent with the intentionally minimal Provider synthesis boundary and is not presented as a resolved capability.

## Claim boundary

This fixed replay supports a mixed result: evidence-bound domain projection substantially improved broad-domain accuracy, and the typed witness guard completely separated the four irreconcilable controls without novelty regression. It does not satisfy the frozen positive-effect terminal because domain accuracy missed the absolute threshold. It does not establish statistical significance, production autonomy, remediation safety, or general live-fault performance.
