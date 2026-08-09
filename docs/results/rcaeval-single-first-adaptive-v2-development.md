# Single-first Adaptive v2 development result

Development state: `TERMINAL`

Verdict: `ADAPTIVE_V2_TUNE_GATE_NOT_PASSED`

Failure reason: none of the three bounded candidates passed the frozen TUNE
gate.

Claim boundary: `CONSUMED_OBSS_DEVELOPMENT_RESULT / DEVELOPMENT_VISIBLE / NOT_EXTERNAL_VALIDATION / NOT_PRIMARY_INFERENCE`.

## Current disposition

Adaptive v2 is implemented and locally verified. Candidate-1 terminalized all
60 scheduled TUNE
cases without completing Initial Diagnosis. Fifty-nine cases retained terminal
HTTP 429 failures and one retained a TLS transient after the single authorized
transport retry. Candidate-2 kept the Agent, model, retry policy, pacing, and
development gates unchanged; all 60 cases again failed before Initial Diagnosis,
this time with terminal HTTP 429. The operator then confirmed that API credit
had been exhausted and recharged it.

Candidate-3 preserved the same Agent, model, retry policy, pacing, and gates
after credit recovery. It completed 60/60 with zero Provider failures, proving
that the capacity blocker had cleared. Its Root Service result was 49/60 and
Pair was 25/60, with Damage 6 and Rescue 2. It therefore missed the unchanged
Root, Pair, Damage, and Damage Rate gates. No candidate is selected.

Candidates 1 and 2 are fixed-denominator Provider-capacity records, not Agent
quality estimates. Candidate-3 is the only algorithm-quality-evaluable v2 TUNE
result. It is consumed development evidence, not external validation.

## TUNE_SET

The historical Strong Single baseline is Root Service 51/60 and Pair 29/60. It
was reused and not rerun.

| Metric | Candidate-1 | Candidate-2 | Candidate-3 |
| --- | ---: | ---: | ---: |
| Role | initial candidate | capacity retry | post-credit-recovery TUNE |
| Scheduled / terminalized | 60 / 60 | 60 / 60 | 60 / 60 |
| Completed | 0 | 0 | 60 |
| Algorithm-quality evaluable | no | no | yes |
| Terminal Provider failures | 60 | 60 | 0 |
| HTTP 429 / TLS transient | 59 / 1 | 60 / 0 | 0 / 0 |
| Provider attempts / transport retries | 120 / 60 | 120 / 60 | 60 / 0 |
| Root Service / Pair | unavailable | unavailable | 49 / 25 |
| Damage / Rescue | unavailable | unavailable | 6 / 2 |
| Direct / Trace-bearing routes | unavailable | unavailable | 60 / 0 |
| Mean semantic operations, completed-only | unavailable | unavailable | 1.00 |
| Correct / Wrong Override | unavailable | unavailable | 0 / 0 |
| Known-token lower bound | 0 | 0 | 491,343 |
| Conservative token upper bound | 3,840,000 | 3,840,000 | 491,343 |
| Schema/privacy/schedule failures | 0 | 0 | 0 |
| Gate | `PROVIDER_CAPACITY_BLOCKED` | `PROVIDER_CAPACITY_BLOCKED` | `TUNE_GATE_NOT_PASSED` |

Candidate-3 met Completion, Direct Return, Mean Operations, Trace, Override, and
schema/privacy/schedule requirements. It failed Root Service 49 < 51, Pair
25 < 29, Damage 6 > Rescue 2, and Damage Rate 20.7% > 5%.

## Protected downstream stages

- No TUNE candidate has passed the frozen gate.
- The single 120-case consumed-data regression is not eligible and has not run.
- No Combined-180 result exists.
- A fresh external holdout plan is not eligible and has not been created.
- No fresh dataset was opened, downloaded, or executed.

All case identifiers, run identifiers, private paths, concrete evidence
references, credentials, and raw Provider outputs remain private.

## Terminal disposition

The bounded three-candidate loop is exhausted. Preserve all candidate terminals,
sidecars, and public aggregates unchanged. Do not run the 120-case regression,
prepare a fresh external holdout plan, modify the current candidate, or create a
fourth candidate under this Goal.
