# DTA v2.2.5 Opaque Ambiguity Error Analysis

## Frozen conclusion

The single frozen 16-case x 4-combination evaluation minted
`DTA_V22_5_NO_AMBIGUITY_EFFECT_OBSERVED`. All 64 scheduled runs are represented
exactly once, the independent partial journal has 64 lines and matches the
final artifact, and `execution_count` is 1. There were zero protocol repairs,
zero uncaught runner exceptions, zero fail-open `NO_INCIDENT` outcomes, zero
forgotten pre-closure reads, and zero Agent writes.

The result is negative because neither preregistered pooled factor threshold
passed. It is not a failure of the opaque/fail-closed engineering boundary, and
it is not evidence of generalization, live SRE operation, or production
readiness.

## Required questions

### Did opaque identifiers remove mechanism/control leakage?

Yes, on the complete bound Provider surface. Service, operation, and change
identities were replaced before mechanism assignment with opaque identifiers.
Internal hypothesis, action, and ambiguity-set IDs remain in evaluator/runtime
artifacts but are omitted from the Provider projection. The pre-transport lint
rendered all 64 exact evaluation payloads plus bootstrap and repair payloads;
all 66 reports contained zero forbidden identities, case IDs, or evaluator
metadata. The independent pre-execution reviewer rebuilt the same report and
found no raw `h:`, `a:`, or `eas:` identifiers in any Provider payload.

### Did complete set closure eliminate fail-open NO_INCIDENT?

Yes on this fixed set. Both Set arms had zero premature `NO_INCIDENT` outcomes
on the eight resource incidents, and all four arms had zero fail-open outcomes
under the typed budget/source-failure definition. `TARGET_ONE` retained four
premature `NO_INCIDENT` outcomes after partial resource coverage; that arm is
the preregistered one-target baseline rather than the complete-set treatment.

### Were pre-closure reads preserved?

Yes. Every persisted run recomputed
`forgotten_preclosure_read_count` from the read-time coverage ledger and its
ambiguity set. The aggregate count was zero in every arm. The independent
review also demonstrated that a successful read omitted from represented
coverage produces count 1, rebuilding from the ledger produces 0, and a
tampered persisted count is rejected.

### Did TARGET_SET recover after a first normal target?

Yes. Four of eight resource incidents selected the wrong target first in
`TARGET_SET`; all four continued to the remaining target and recovered the
correct Diagnosis. Resource ambiguity exact accuracy was 8/8, compared with
4/8 in `TARGET_ONE`. The price was 1.6 Resources reads per fixed resource case
versus 1.0 in `TARGET_ONE`.

### Did BUNDLE_SET localize the anomalous target in one read?

The contrastive bundle read localized the predicate-bearing target in one read
for all eight resource incidents. Seven completed with an exact Diagnosis. The
e03 CPU run completed the bundle read and coverage, then ended
`TRANSPORT_FAILED` before terminal selection; it is the sole BUNDLE_SET
resource error and remains preserved rather than retried. BUNDLE_SET resource
ambiguity accuracy was therefore 7/8 and mean Resources reads were 1.0 per
fixed resource case.

### Did all-normal bundle controls reopen NO_INCIDENT correctly?

Yes. Each Bundle arm had two all-normal resource controls, and all 2/2 reopened
the correct `NO_INCIDENT` terminal after complete two-target coverage. Across
`TARGET_SET`, `BUNDLE_ONE`, and `BUNDLE_SET`, six normal-control runs reached
`NO_INCIDENT` only after complete set coverage.

### Did fixed denominators differ from treatment-produced ambiguity fields?

No. Every arm used the frozen evaluator strata: 12 incidents, 8 resource
ambiguity incidents, 10 resource cases, 2 normal controls, and 2 abstention
controls. Independent mutation of treatment-produced ambiguity fields did not
change the 8/10 resource denominators.

### Did bundle actions reduce reads, calls, tokens, or latency?

Only the direct Set comparison reduced reads: BUNDLE_SET used 1.0 Resources
reads per fixed resource case versus 1.6 for TARGET_SET, a reduction of 0.6.
The pooled Bundle main effect was only a 0.3-read decrease because TARGET_ONE
and BUNDLE_ONE were already at 1.0. Every arm still used 16 Provider calls, so
the call reduction was 0. Pooled Bundle tokens increased by 11.4% and latency
increased by 32.9%; the preregistered artifact represents those as negative
token and latency decrease fractions. There were zero bundle schema failures.

### Did any nonresource mechanism regress?

No. Configuration error, service unavailable, and dependency latency remained
exact in every arm, for zero nonresource regression outcomes.

### Was the measured effect closure, bundle granularity, or interaction?

No preregistered factor effect passed. Pooled Set closure improved resource
ambiguity accuracy by 0.1875 and reduced premature `NO_INCIDENT` by 0.25, below
the required 0.25 and 0.50 joint thresholds, while adding 0.3 mean Resources
reads. Pooled Bundle granularity improved resource ambiguity accuracy by
0.1875 and reduced mean Resources reads by 0.3, but did not reduce calls and
increased tokens and latency. Exact-rate interaction was -0.3125. The observed
combined BUNDLE_SET arm met most absolute safety/quality conditions, but its
0.875 resource accuracy improved on TARGET_ONE by 0.375 rather than the
required 0.50.

## Frozen error classification

Across the 64 runs:

- truth-bearing identity lint failure: 0;
- wrong target first: 8 (4 TARGET_ONE, 4 TARGET_SET);
- partial ambiguity-set coverage on fixed resource cases: 14;
- complete ambiguity-set coverage on fixed resource cases: 26;
- budget insufficient closure state: 0;
- source failure closure state: 0;
- forgotten pre-closure read: 0;
- bundle predicate yield: 16;
- bundle all-normal: 4;
- bundle schema failure: 0;
- premature `NO_INCIDENT` on resource incidents: 4, all TARGET_ONE;
- read then correct Diagnosis: 43;
- read then wrong Diagnosis: 0;
- `NO_INCIDENT` after complete normal set coverage: 6;
- `ABSTAIN` after incomplete evidence: 8;
- protocol failure: 0;
- transport failure: 1 (e03/BUNDLE_SET);
- bounded transport retries: 2 in successful runs;
- Provider calls: 64;
- Agent writes: 0.

The transport failure is part of the frozen result. It was not repaired,
replayed, or selectively omitted.
