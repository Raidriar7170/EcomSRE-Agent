# DTA v2.2.1 Evidence-Acquisition Study

## Frozen result

The single fixed study completed all 12 cases under all four preregistered
arm/policy combinations. The measured policy terminal is:

```text
DTA_V22_1_NO_EVIDENCE_ACQUISITION_EFFECT_OBSERVED
```

This is a completed negative study, not a blocked run. The completion boundary
does not require an accuracy improvement.

## Study contract

- Base commit: `fceadc924d4909ca1457b35f268429f0272427ce`
- Implementation commit: `6988a730763fc08506c8c70c76518e47f90b05e2`
- Provider model: `gpt-5.4-mini-2026-03-17`
- Development gate: passed on iteration 1 with 16/16 runs, 0 uncaught
  exceptions, and 0 Agent writes
- Final execution count: exactly 1
- Final representation: 12 cases × 4 combinations = 48/48 runs
- Order: deterministic case-interleaved four-position rotation; every
  combination occupied every position exactly three times
- Case binding: the four combinations received the same source and normalized
  case hashes for every case
- Truth isolation: evaluator truth was loaded only after all 48 executions
- Safety: Docker calls 0, Runbook executions 0, Agent writes 0

Independent review found one post-study accounting defect: a terminal transport
failure was counted in `transport_retry_count` but its logical Provider call
could be omitted because no response had been appended. The final study had
zero transport failures and zero retries, so none of its runs or aggregate
costs were affected. The accounting path was repaired and tested without
rerunning the fixed study; final-study execution count remains 1.

The [machine-readable result](dta-v22-1-evidence-acquisition-study.json) has
SHA-256
`047cab366a9f431a0eb097e79b0c48cdff1c143f63e88676601f9ba9e1f47a39`.

## Outcome and protocol metrics

| Metric | Flat Legacy | Flat Gate | Planner Legacy | Planner Gate |
| --- | ---: | ---: | ---: | ---: |
| End-to-end exact completion | 3/12 | 2/12 | 1/12 | 0/12 |
| Valid terminal | 11/12 | 9/12 | 4/12 | 6/12 |
| First-pass protocol success | 0.7368 | 0.8148 | 0.6522 | 0.7692 |
| Post-repair protocol success | 0.9474 | 1.0000 | 1.0000 | 1.0000 |
| Semantic repair rate | 0.2105 | 0.1852 | 0.3478 | 0.2308 |
| Policy redirect rate | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Policy redirect compliance | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Repeated premature abstention | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Root-service accuracy | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Mechanism accuracy | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Mechanism Macro-F1 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| No-Incident accuracy | 0.5000 | 0.5000 | 0.5000 | 0.0000 |
| Abstention accuracy | 1.0000 | 0.5000 | 0.0000 | 0.0000 |
| Evidence-ref validity | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Semantic evidence-clause validity | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

## Evidence-acquisition metrics

| Metric | Flat Legacy | Flat Gate | Planner Legacy | Planner Gate |
| --- | ---: | ---: | ---: | ---: |
| Cases with an adaptive read | 4/12 | 10/12 | 3/12 | 6/12 |
| Mean adaptive reads | 0.3333 | 0.8333 | 0.2500 | 0.6667 |
| Read-source distribution | Changes 4 | Changes 6; Logs 2; Traces 2 | Logs 1; Traces 2 | Changes 1; Logs 3; Runtime 2; Traces 2 |
| Successful-read rate | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| Diagnosis-after-read rate | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Duplicate read attempts | 0 | 0 | 0 | 0 |

`SUCCESS_EMPTY` is an executed read, so it is included in successful-read
rate; 17 of the 25 read events were empty. Empty and unavailable reads are
separated in the [error analysis](dta-v22-1-evidence-acquisition-error-analysis.md).

For the eight scorer-only `bootstrap_insufficient_expected=true` cases:

| Process metric | Flat Legacy | Flat Gate | Planner Legacy | Planner Gate |
| --- | ---: | ---: | ---: | ---: |
| Cases with at least 1 adaptive read | 2/8 | 8/8 | 3/8 | 4/8 |
| Cases with at least 2 adaptive reads | 0/8 | 0/8 | 0/8 | 1/8 |
| Premature ABSTAIN proposals | 0 | 0 | 0 | 0 |
| Premature ABSTAIN redirects | 0 | 0 | 0 | 0 |
| Redirect to READ | 0 | 0 | 0 | 0 |
| Redirect to valid terminal | 0 | 0 | 0 | 0 |

The scorer-only bootstrap label was never projected to the Provider or policy.

## Cost and safety metrics

| Metric | Flat Legacy | Flat Gate | Planner Legacy | Planner Gate |
| --- | ---: | ---: | ---: | ---: |
| Mean Provider calls | 1.6667 | 2.2500 | 1.9167 | 2.1667 |
| Input tokens | 23,768 | 33,917 | 30,882 | 39,159 |
| Output tokens | 952 | 1,271 | 969 | 1,147 |
| Total tokens | 24,720 | 35,188 | 31,851 | 40,306 |
| Total latency (ms) | 36,369.97 | 48,881.18 | 49,201.00 | 53,011.33 |
| Mean latency (ms) | 3,030.83 | 4,073.43 | 4,100.08 | 4,417.61 |
| Transport retries | 0 | 0 | 0 | 0 |
| Uncaught exceptions | 0 | 0 | 0 | 0 |
| Agent writes | 0 | 0 | 0 | 0 |

Across all 12 cases, Flat Gate added 7 Provider calls, 10,468 tokens, and
12,511.21 ms relative to Flat Legacy. Planner Gate added 3 Provider calls,
8,455 tokens, and 3,810.33 ms relative to Planner Legacy.

## Control cost

The control subset contains two No-Incident and two legitimate
missing/conflicting-evidence abstention cases.

| Metric | Flat Gate vs Legacy | Planner Gate vs Legacy |
| --- | ---: | ---: |
| Legacy unnecessary-read rate | 0.5000 | 0.0000 |
| Gate unnecessary-read rate | 1.0000 | 0.0000 |
| Unnecessary-read increase | +0.5000 | +0.0000 |
| No-Incident regression | 0.0000 | 0.5000 |
| Abstention regression | 0.5000 | 0.0000 |
| Combined control-accuracy drop | 0.2500 | 0.2500 |
| Extra Provider calls | 3 | 1 |
| Extra tokens | 4,341 | 751 |

## Preregistered interpretation

Flat Gate read at least once on 8/8 bootstrap-insufficient cases versus 2/8
for Flat Legacy, an increase of 0.75. Planner Gate reached 4/8 versus 3/8, an
increase of only 0.125. Neither gated arm produced a runtime redirect, so the
redirect-compliance metric remained 0. The rule required both gated arms to
reach at least 0.50, increase by at least 0.30, reach redirect compliance of at
least 0.75, keep repeated abstention at or below 0.25, and keep writes at zero.
The joint rule therefore did not pass.

Quality is separate: Planner Gate decreased exact completion from 1 to 0 and
left mechanism Macro-F1 at 0. It did not satisfy the quality-improvement rule.
The Gate changed some exploration behavior without demonstrating better RCA
quality.

Planner-specific interaction was also not established: diagnosis-after-read
was 0 for both gated arms and mechanism Macro-F1 was 0 for both.

## Historical v2.2 boundary

The merged v2.2 Practical result remains historical and byte-identical. It
reported Flat 1/12 versus Planner-Lite 3/12, mechanism Macro-F1 0.0000 versus
0.1333, and mean reads 0 for both. This v2.2.1 report does not rescore or
replace that run. Because Provider output is stochastic, claims here use only
the within-run Legacy-versus-Gate comparisons in this new 2 × 2 study.

## Bottom line

The narrow runtime policy is implemented and exercised in development, the
fixed study is complete, and the safety boundary held. In the final study, the
policy-aware prompt increased reads, but the runtime redirect did not fire and
the additional evidence was not converted into a correct incident Diagnosis.
