# DTA v2.2.2 Gap-Aware Routing Error Analysis

## Frozen study boundary

This analysis covers the single final 16-case × 4-combination execution. The
evaluation set was newly generated from explicit synthetic/derived replay
fixtures: 10 incident cases, three No-Incident controls, and three legitimate
abstention controls. All 10 incidents had an evaluator-audited one-read
admissible path. Gap top-4 recall was 10/10 at turn zero and 64/64 across
feasible post-first-read states. Truth was loaded only after the four runs for
each case completed. The study executed exactly once.

The preregistered measured result terminal is
`DTA_V22_2_GAP_ROUTING_QUALITY_EFFECT_OBSERVED`. That measured terminal is valid
under the fixed rule, but it does not erase the absolute failures described
below. The separate engineering completion terminal is
`DTA_V22_2_GAP_ROUTING_STUDY_COMPLETE`.

## Combination results

| Metric | Flat Broad | Flat Gap | Planner Broad | Planner Gap |
| --- | ---: | ---: | ---: | ---: |
| Exact completion | 6/16 | 10/16 | 6/16 | 8/16 |
| Incident root/mechanism accuracy | 0/10 | 4/10 | 0/10 | 2/10 |
| Mechanism Macro-F1 | 0.0000 | 0.4000 | 0.0000 | 0.2667 |
| Diagnosis after read | 0.0000 | 0.4444 | 0.0000 | 0.1538 |
| Predicate-yield read rate | 0.0000 | 0.2000 | 0.0000 | 0.0588 |
| Empty-read rate | 1.0000 | 0.8000 | 1.0000 | 0.9412 |
| Oracle-path action hit rate | 0.0000 | 0.4000 | 0.0000 | 0.2000 |
| No-Incident accuracy | 3/3 | 3/3 | 3/3 | 3/3 |
| Abstention accuracy | 3/3 | 3/3 | 3/3 | 3/3 |
| Provider calls | 37 | 36 | 38 | 52 |
| Total tokens | 50,104 | 44,810 | 52,828 | 66,723 |
| Protocol repairs | 0 | 0 | 0 | 2 |
| Transport retries | 1 | 2 | 1 | 0 |
| Protocol failures / uncaught exceptions / Agent writes | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |

## Why the quality terminal was minted

Planner Gap met every fixed quality-effect condition against Planner Broad:

- exact completion improved by exactly two cases, 6/16 to 8/16;
- mechanism Macro-F1 improved by 0.2667, exceeding the 0.15 alternative;
- diagnosis-after-read improved by 0.1538, exceeding the 0.15 threshold;
- combined No-Incident/abstention accuracy stayed at 1.0, so the drop was 0;
- Agent writes remained 0.

The separate pooled routing-only terminal was not satisfied: pooled Gap versus
Broad improved predicate yield by 0.1111 and reduced empty reads by 0.1111,
both below the required 0.20. Planner interaction was also false. Flat Gap was
better than Planner Gap on both diagnosis-after-read and Macro-F1, and Flat's
Broad-to-Gap improvement exceeded Planner's.

## Error cluster 1: premature No-Incident on unread resource evidence

Cases `e05`–`e08` contained CPU or memory evidence behind a one-read Resources
path. The runtime nevertheless exposed `NO_INCIDENT` from healthy bootstrap
runtime plus supported request/error metrics before resource evidence was read.
All 16 executions across those four incident cases ended as `NO_INCIDENT`.

This is the largest validity limitation. Source availability correctly said
Resources was captured, and the Predicate Gap Graph correctly identified a
one-read path, but No-Incident coverage did not require relevant captured
source gaps to be closed. The short prompt then correctly followed its frozen
instruction to prefer an admitted non-ABSTAIN terminal. The failure is an
admission-boundary mismatch, not a missing replay path.

No post-study fix was applied because changing admission after the frozen run
would require a new evaluation. The measured bytes and terminal remain intact.

## Error cluster 2: Provider selection did not equal top-4 feasibility

Both configuration cases (`e01`, `e02`) had a one-read Trace path, and the
offline Gap top-4 audit contained a shortest-path action. All eight executions
still ended ABSTAIN. Gap runs selected empty Changes and Resources reads rather
than the useful Trace action; Broad runs selected empty Changes and Logs.

This separates three claims that must not be conflated:

1. a feasible evidence path existed;
2. the deterministic router exposed a useful action in top 4;
3. the Provider selected and used that action.

The first two were proven offline. The final study shows the third remained
weak under short aliases and the frozen prompt.

## Error cluster 3: Gap helped Flat more consistently than Planner

Gap produced correct diagnoses in six incident runs: Flat Gap on `e03`,
`e04`, `e09`, and `e10`, plus Planner Gap on `e04` and `e10`, with `e04` and
`e10` counted once per arm. Broad produced no incident diagnosis. Planner Gap
missed `e03` and `e09`, where Flat Gap succeeded, after choosing empty actions.

Planner Gap also performed unnecessary reads on healthy controls `e11` and
`e12`, used one repair in each, and consumed 10 more control Provider calls and
10,658 more control tokens than Planner Broad. Control accuracy did not fall,
but unnecessary-read rate increased by 0.3333. Therefore no Planner-specific
interaction claim is allowed.

## Error cluster 4: empty reads dominated

Broad reads were 100% empty. Gap reduced the pooled empty-read rate only to
0.8889. Negative Coverage correctly recorded empty source-target pairs without
treating them as contradictions, and every run terminated safely, but the
model frequently moved to another empty action instead of the useful one.

The Post-Read Delta and negative ledger were therefore behaviorally active but
not sufficient to make alias selection reliably gap-closing.

## Protocol, transport, and safety

There were two bounded protocol repairs, both in Planner Gap controls. All
logical turns were valid after at most one repair, so post-repair protocol
success was 1.0 for all combinations. Four exact-request transport retries
were recorded and all recovered within the frozen maximum of three retries per
request. There were no protocol-failed runs, transport-failed runs, runner
exceptions, Docker calls, Runbook executions, or Agent writes.

After the study, exact-head validation exposed one historical CI-only defect:
the v2.2.1 verifier still required its feature implementation commit to be an
ancestor of HEAD even though PR #62 had been squash-merged. The verifier now
proves the squash mainline descends from the frozen base and that every bound
implementation byte at HEAD equals the frozen feature-commit byte. This changed
no v2.2.2 routing, admission, Provider, case, truth, score, or measured run; the
final study rerun count remains zero.

Independent review then found that the evaluated Post-Read Delta compared
ephemeral T aliases rather than stable terminal IDs and built the next action
frontier before recording the just-completed read in Negative Coverage. The
post-study implementation now compares terminal IDs, projects their current T
aliases, records the read before rebuilding routing/admission, and has
regressions for T-alias reuse and negative-penalty ordering. This is a real
post-study behavior repair and is not represented as evaluated: the 64 frozen
runs, Provider responses, numeric scores, and measured terminal were not
changed or rerun. Review also corrected the result schema and reports to call
the three effect markers measured result terminals, reserving
`DTA_V22_2_GAP_ROUTING_STUDY_COMPLETE` for engineering completion.

## Defensible conclusion

Gap routing improved contemporaneous exact completion and diagnosis after read
enough to satisfy the preregistered quality terminal. The effect is bounded to
this model, prompt, synthetic/derived 16-case replay set, and one execution.
Absolute incident accuracy was only 4/10 for Flat Gap and 2/10 for Planner Gap.
The study supports a narrow routing-quality effect and identifies an admission
counterexample; it does not support general RCA quality, Planner superiority,
live evidence acquisition, remediation, or production readiness.
