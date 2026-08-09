# RCAEval Single-first Adaptive Agent v2

Version: `single-first-adaptive-v2`

Claim boundary: `OBSS_DEVELOPMENT_POOL / DEVELOPMENT_VISIBLE / NOT_EXTERNAL_HOLDOUT / NOT_PRIMARY_INFERENCE`.

## Intent

Adaptive v2 preserves the Strong Single diagnosis as the primary inference and
adds only selective verification around it:

1. Run the existing Strong Single Provider with the same prompt, bounded
   Metrics/Logs/Traces context, normalization, and Diagnosis contract.
2. Evaluate deterministic instability and cross-source conflict signals.
3. Return the Strong Single diagnosis directly by default, or selectively call
   the existing Logs verifier, Trace verifier, or both.
4. Apply deterministic keep-by-default Fusion. There is no Fusion Provider
   call.
5. Keep the Strong Single indicator unless the inherited deterministic
   candidate has a frozen strong margin.

RE2-SS exposes Traces as typed unavailable. Adaptive v2 does not add a
Commander, Reviewer, specialist, remediation path, model fallback, external
write, or hidden-label read.

## Strong Single-compatible Initial

The Initial operation directly reuses the frozen Strong Single reference
Provider implementation. It receives the full bounded architecture context:

- Metrics;
- Logs;
- Traces when available;
- the same visible services and evidence authority;
- the same normalized Root Service and Indicator output contract.

Adaptive v2 does not maintain a second Initial prompt. An Initial Provider
failure terminalizes the case; it is never replaced with a Metrics heuristic or
retried for semantic reasons.

## Deterministic Gate

The Gate consumes only the Strong Single diagnosis and deterministic/runtime
features. `DIRECT_RETURN` is the conservative default.

| Route | Trigger | Semantic operations |
| --- | --- | ---: |
| `DIRECT_RETURN` | No authorized instability trigger | 1 |
| `VERIFY_LOGS` | Explicit Logs opposition, or Metrics conflict plus weak diagnosis evidence | 2 |
| `VERIFY_TRACES` | Trace available, predicted indicator is latency/socket, and propagation conflict is present | 2 |
| `VERIFY_BOTH` | Low confidence, Metrics conflict, explicit Logs opposition, and the strict Trace trigger all hold | 3 |

CPU, memory, and disk-I/O predictions do not independently authorize a Trace
call. Missing indicator candidates are recorded but do not independently force
escalation.

The frozen candidate thresholds are loaded from
`config/rcaeval-adaptive-v2/agent.json`. Gate output is deterministic and records
the route, reason codes, Metrics rank/margin, instability flag, and typed Trace
trigger result.

## Selective verifiers

Metrics remains a deterministic anchor and creates no model call. The existing
Logs and Trace specialists remain source-isolated:

- they may support or contradict the Initial service;
- they may label an authorized service as a root candidate, propagated symptom,
  or uncertain;
- they may not issue the final Diagnosis;
- their evidence references remain bounded by the selected source.

No new specialist is introduced.

## Deterministic Fusion

Fusion defaults to `KEEP_INITIAL`. `OVERRIDE_INITIAL` is authorized only when
all of the following hold:

1. the Gate marked Initial unstable;
2. exactly one alternative service is present in the Metrics top two;
3. a Specialist marks that alternative as `ROOT_CANDIDATE` with valid support;
4. a Specialist explicitly contradicts the Initial service or marks it as a
   supported propagated symptom;
5. the alternative clears the frozen support threshold;
6. no competing authorized alternative remains.

Otherwise Fusion keeps the Initial service with a machine-readable reason.
There is no LLM Fusion operation or result-driven second attempt.

## Indicator policy

The Strong Single indicator remains authoritative by default. A deterministic
override is allowed only when the inherited candidate targets the final Root
Service and clears the frozen strong-margin threshold. Every result records the
model indicator, deterministic candidate and margin, final indicator, and
action:

- `KEEP_STRONG_SINGLE_INDICATOR`;
- `DETERMINISTIC_OVERRIDE_STRONG_MARGIN`;
- `KEEP_WITH_UNCERTAINTY`.

## Provider and evidence policy

- concurrency is one;
- requests have a fixed five-second minimum interval;
- a semantic operation permits at most one inherited, byte-identical retry for
  an allowlisted transport failure;
- Retry-After is respected;
- schema and semantic retries are forbidden;
- each run has one create-once terminal and append-only operation/attempt
  sidecars;
- known token usage and a conservative unknown-usage upper bound are retained;
- case identifiers, run identifiers, raw Provider output, private paths,
  credentials, and concrete evidence references are private-only.

The historical Strong Single development baselines are reused and are not
rerun. New paired external evaluation, if separately authorized in the future,
must interleave cases and alternate arm order rather than run one arm in bulk.

## Consumed-data development protocol

The 60 former DESIGN cases are `TUNE_SET`; the 120 former DEV_VALIDATION cases
are `REGRESSION_SET`. Both are consumed development data. The execution record
budget is five: candidate-1 and candidate-2 are preserved capacity-only records,
while candidate-3 through candidate-5 are the only three real algorithm
candidates. Candidate-3 is the first algorithm candidate, so only candidate-4
and candidate-5 remain available. A candidate may enter the single permitted
120-case regression only after all frozen TUNE gates pass.

The TUNE gates require completion at least 58/60, at most three terminal HTTP
429 failures, final Root Service at least 51/60, and final Pair at least 29/60.
Within the same run, Root Rescue must be strictly greater than Root Damage,
Root Damage must be at most two, Root net Rescue must be at least one, Pair
Rescue must be no less than Pair Damage, and Pair net Rescue must be
non-negative. The route/cost gates require 36 through 48 direct returns, mean
semantic operations at most 1.8, no more than 12 Trace-bearing routes, Wrong
Override no greater than Correct Override, and zero schema/privacy/schedule
failures. Historical cross-run Damage/Rescue is contextual and confounded by
model-run variability; it does not decide this gate.

The single regression requires completion at least 114/120, final Root Service
at least 97/120, final Pair at least 53/120, and at most six terminal HTTP 429
failures. Within the same run, Root and Pair Rescue must each be no less than
the corresponding Damage, both net Rescue values must be non-negative, and the
Root Damage Rate must be at most 5%. It also requires at least 72 direct returns,
mean semantic operations at most 1.8, no more than 24 Trace-bearing routes,
Wrong Override no greater than Correct Override, and zero
schema/privacy/schedule failures.

No regression result may modify the selected candidate or trigger a second
regression. A fresh external holdout plan is eligible only after the regression
gate passes; this design neither accesses nor executes a fresh holdout.

## Terminal development disposition

Candidate-4 exercised the repaired Gate and finished with 43 Direct and 16 Logs
routes, zero Trace-bearing routes, and mean semantic operations 1.25. It did not
pass TUNE: one record had a schema failure, Final Pair was 27/60, and same-run
Root Damage / Rescue / Net was 0 / 0 / 0 rather than the required strict Rescue
gain.

Candidate-5 was not executed because none of the bounded Work Package F
conditions authorized a supported single change. Direct and escalation Recall
were already in range; no correct Specialist Root alternative was suppressed
by Fusion; Pair did not degrade through indicator override; and tightening Gate
could not create the missing Root rescue. The schema failure was not an
authorized algorithm direction. Candidate iteration therefore stops without a
candidate-6, regression, or fresh holdout plan, with verdict
`ADAPTIVE_V2_TUNE_GATE_NOT_PASSED_AFTER_REAL_ALGORITHM_ITERATIONS`.
