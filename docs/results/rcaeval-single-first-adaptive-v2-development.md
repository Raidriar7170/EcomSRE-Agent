# Single-first Adaptive v2 development result

Development state: `INCOMPLETE`

Verdict: `BLOCKED`

Blocking condition: Provider capacity was unavailable throughout TUNE
candidate-1.

Claim boundary: `CONSUMED_OBSS_DEVELOPMENT_RESULT / DEVELOPMENT_VISIBLE / NOT_EXTERNAL_VALIDATION / NOT_PRIMARY_INFERENCE`.

## Current disposition

Adaptive v2 is implemented and locally verified, but the consumed-data
candidate loop is not complete. Candidate-1 terminalized all 60 scheduled TUNE
cases without completing Initial Diagnosis. Fifty-nine cases retained terminal
HTTP 429 failures and one retained a TLS transient after the single authorized
transport retry.

This is a fixed-denominator Provider-capacity result, not an estimate of Agent
quality. It does not show that Adaptive v2 is worse than Strong Single, and it
does not authorize threshold, prompt, model, retry, baseline, or development-gate
changes. Candidate-2 and candidate-3 have not started.

## TUNE_SET

The historical Strong Single baseline is Root Service 51/60 and Pair 29/60. It
was reused and not rerun.

| Metric | Candidate-1 |
| --- | ---: |
| Scheduled / terminalized | 60 / 60 |
| Completed | 0 |
| Algorithm-quality evaluable | no |
| Terminal Provider failures | 60 |
| HTTP 429 / TLS transient | 59 / 1 |
| Provider attempts / transport retries | 120 / 60 |
| Root Service / Pair | 0 / 0 |
| Damage / Rescue | 29 / 0 |
| Direct / Trace-bearing routes | 0 / 0 |
| Mean semantic operations, fixed denominator | 0.00 |
| Mean semantic operations, completed-only | unavailable |
| Correct / Wrong Override | 0 / 0 |
| Known-token lower bound | 0 |
| Conservative token upper bound | 3,840,000 |
| Schema/privacy/schedule failures | 0 |
| Gate | `PROVIDER_CAPACITY_BLOCKED` |

Damage treats every non-completed candidate endpoint as incorrect on the fixed
denominator. Because no Initial Diagnosis completed, Damage, Root, Pair, route,
operation, and override values do not describe successful Adaptive v2 behavior.

## Protected downstream stages

- No TUNE candidate has passed the frozen gate.
- The single 120-case consumed-data regression is not eligible and has not run.
- No Combined-180 result exists.
- A fresh external holdout plan is not eligible and has not been created.
- No fresh dataset was opened, downloaded, or executed.

All case identifiers, run identifiers, private paths, concrete evidence
references, credentials, and raw Provider outputs remain private.

## Resume condition

Continue the bounded candidate loop only after Provider quota/capacity is known
to be available. Preserve candidate-1 unchanged. Do not use a partial recovery,
result-driven retry, or development-gate modification to manufacture a pass.
