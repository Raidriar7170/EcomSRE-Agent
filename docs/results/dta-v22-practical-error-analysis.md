# DTA v2.2 Practical Error Analysis

## Executive finding

The runtime and safety boundary completed, but diagnostic quality remained
weak. Planner-Lite improved the fixed result from one to three exact cases and
technically met the practical advantage rule; it did not establish robust
planning behavior. Both arms usually terminated from bootstrap instead of
reading additional evidence.

## Outcome taxonomy

| Outcome | Flat | Planner-Lite |
| --- | ---: | ---: |
| `COMPLETED_CORRECT` | 1 | 3 |
| `SEMANTICALLY_WRONG` | 8 | 7 |
| `PROTOCOL_FAILED` | 3 | 2 |
| `TRANSPORT_FAILED` | 0 | 0 |
| `RUNNER_EXCEPTION` | 0 | 0 |

No failed case was removed or rerun.

## Dominant error clusters

### 1. Premature abstention

Flat abstained on most incident cases; Planner abstained on all incident cases
except E03. This explains the near-zero root and mechanism accuracy even while
evidence-ref and semantic-clause validity remain near zero: applicability
scoring requires an admitted incident Diagnosis with actual cited refs. A
locally valid abstention can avoid a protocol failure, but it cannot receive
incident evidence credit.

At least eight fixed cases were designed so bootstrap alone was insufficient.
Nevertheless, both fixed arms averaged zero adaptive reads. The model generally
preferred a terminal abstention over an available bounded read, so the compact
Planner ledger had little opportunity to accumulate useful state.

### 2. Semantic admission failures after one repair

E08 failed in both arms and E11 failed in both arms after the single permitted
repair. Flat E02 also failed. Safe error codes were
`SEMANTIC_ADMISSION_FAILED` or `REPAIR_ALREADY_CONSUMED`; they caused zero read
dispatches and zero writes. These are protocol failures, not transport errors
and not silently scored as wrong Diagnoses.

### 3. Sparse exact wins

Planner correctly diagnosed E03 as recommendation service unavailable, admitted
E10 as No-Incident, and abstained on E12. Flat's only exact fixed result was the
E12 abstention. The Macro-F1 delta therefore comes from one correct incident
mechanism against zero, not broad mechanism coverage.

### 4. Context cost

Planner used 20,201 total tokens versus Flat's 17,883, about 13% more, because
its compact ledger was visible. It happened to have lower mean latency in this
single run, but one local campaign cannot support a latency advantage claim.

## What the experiment established

- The recovered controller core runs end to end through bootstrap, canonical
  catalogs, Provider alias projection, typed terminal admission, and replay.
- Flat and Planner receive the same case bytes; only Planner receives compact
  ledger state.
- One semantic repair is enforced across a case, exact-request transport retry
  is separate, and unsafe outputs cause no dispatch or write.
- The practical comparison is runnable and reports negative cases honestly.

## What it did not establish

- robust root-cause accuracy;
- a general Planner superiority result;
- live telemetry, Docker, remediation, Runbook, production, or online safety;
- independence for all 12 captures: nine are public real replays and three are
  explicitly synthetic or counterfactual-derived;
- value from multi-turn planning on this run, because fixed-set adaptive reads
  were zero for both arms;
- the Provider-visible mean-size target: the 16KB cap was enforced, but per-turn
  byte counts were not retained;
- equivalence to the frozen PR-C research admission policy: the practical
  successor explicitly added legacy config, memory, and bounded No-Incident
  compatibility clauses for public replay normalization.

The next scientifically useful change would be a larger independent replay set
and a development-only prompt study that increases evidence acquisition without
weakening terminal admission. It must not retroactively change this fixed run.
