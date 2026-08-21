# DTA v2.2.4 Interview Brief

## 30-second version

v2.2.3 failed three CPU/memory cases because two targets were runtime-symmetric
but the controller read one target and then admitted No-Incident. I did not add
another ranking tie-break to guess the target. I made replay completeness
explicit, represented healthy targets with normal resource records, grouped
symmetric targets into an Evidence Ambiguity Set, and compared sequential
per-target reads with a one-read all-candidate Resources bundle. In one frozen
16×4 study, TARGET_ONE completed 12/16 while TARGET_SET, BUNDLE_ONE, and
BUNDLE_SET completed 16/16. BUNDLE_SET localized all 8 resource incidents with
one Resources read per resource case and zero Agent writes. The result is
bounded replay evidence, not a live-SRE or production claim.

## The engineering decision

The tempting fix was a deterministic tie-break that picks one symmetric
service. That cannot solve counterfactual cases whose visible state is the same
but whose hidden fault target reverses. The implementation instead makes the
uncertainty first-class:

1. Per-source metadata declares `TARGET_COMPLETE` or `TARGET_PARTIAL`.
2. Every target-complete resource case carries a real normal or anomalous
   `ResourceUsageRecord` for every candidate.
3. Runtime-visible target signatures form a stable Evidence Ambiguity Set;
   signatures exclude truth, case IDs, fixture modifiers, and future outcomes.
4. `AMBIGUITY_SET_COMPLETE` withholds No-Incident until the active set is
   covered, while Diagnosis can open immediately after predicate yield.
5. PER_TARGET dispatch selects the highest-ranked uncovered target; bundle mode
   dispatches one bounded all-candidate Resources action.
6. Provider input contains terminal aliases only. Evidence dispatch remains
   deterministic runtime work.

## Evidence

| Arm | Exact | Resource ambiguity | Premature NO_INCIDENT | Resources reads/case | Controls |
|---|---:|---:|---:|---:|---:|
| TARGET_ONE | 12/16 | 4/8 | 4/8 | 1.000 | 1.000 |
| TARGET_SET | 16/16 | 8/8 | 0/8 | 1.600 | 1.000 |
| BUNDLE_ONE | 16/16 | 8/8 | 0/8 | 1.000 | 1.000 |
| BUNDLE_SET | 16/16 | 8/8 | 0/8 | 1.000 | 1.000 |

The preregistered measured terminal is
`DTA_V22_4_COMBINED_AMBIGUITY_FIX_EFFECT_OBSERVED`. All 64 final runs are
represented once; runner exceptions and Agent writes are zero. The one
transport retry is recorded.

## Honest interpretation

- Set closure and bundle granularity each solved the fixed ambiguity cases on
  their own. The interaction was negative because both reached the ceiling;
  this is not evidence of positive synergy.
- Bundles reduced Resources reads versus TARGET_SET and had lower observed
  latency, but Provider calls were equal and tokens increased. Do not claim a
  general cost reduction.
- Explicit normal records improve replay semantics; they do not prove a live
  telemetry collector is complete.
- No Docker, Runbook, Agent write, remediation, training, deployment, or
  production environment was used.

## Likely follow-ups

**Why not just choose service A first?**  Because the counterfactual pairs keep
the runtime signature symmetric and reverse the fault. A fixed tie-break must
be wrong on one member.

**Does the bundle leak truth?**  No. It queries all candidate targets in
canonical order. Only returned measurements distinguish them.

**Why can Diagnosis open before full set completion?**  Predicate yield is
positive evidence for an incident. The coverage constraint protects a negative
No-Incident conclusion, not an already-supported Diagnosis.

**What would you test next?**  A separately authorized live acquisition study
would need to validate collector completeness and bundle transport semantics.
That is outside this replay result.
