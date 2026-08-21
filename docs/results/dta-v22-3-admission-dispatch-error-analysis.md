# DTA v2.2.3 Admission / Dispatch Error Analysis

## Frozen conclusion

The single 16-case × 4-combination evaluation minted
`DTA_V22_3_NO_FIX_EFFECT_OBSERVED`. All 64 runs are represented, with zero
protocol failures, transport retries, uncaught runner exceptions, or Agent
writes. This is a negative quality result with a real cost reduction in the
automatic-dispatch arms; it is not a generalization, live-operation, or
production claim.

## Required questions

### Did evidence closure remove premature No-Incident on CPU/memory cases?

No. It enforced the intended protocol boundary—one current gap-relevant read
occurred before `NO_INCIDENT` in every Closed resource-silent run—but it did not
make an ambiguous single-target read reliably informative. Resource-silent
accuracy rose from `0/4` to `1/4`, while premature `NO_INCIDENT` fell only from
`4/4` to `3/4`. The three remaining failures (d05, d06, d08) read the wrong
resource target, recorded `EMPTY_CAPTURED`, then lawfully reopened
`NO_INCIDENT` under the one-step contract.

### Did deterministic Top-1 select the oracle path more often than Model Top-4?

No. The pooled dispatch main effect on first-action oracle-path hit was `0.00`.
`MODEL_LEGACY` and `AUTO_LEGACY` were both `0.60`; `MODEL_CLOSED` and
`AUTO_CLOSED` were both `0.70`. Runtime Top-1 preserved exact completion rather
than improving it.

### Did AUTO_CLOSED improve correctness or only increase reads?

It improved one case over `MODEL_LEGACY`: exact completion moved from `12/16`
to `13/16`, Macro-F1 from `0.6000` to `0.7333`, and resource-silent accuracy
from `0.00` to `0.25`. That gain was below every registered effect threshold.
Mean reads rose from `0.75` to `1.1875`; closure newly made all three healthy
`NO_INCIDENT` controls read-bearing, while the three insufficient/`ABSTAIN`
controls were already read-bearing under Legacy.

### How often did closure add an unnecessary read on healthy controls?

Every healthy `NO_INCIDENT` control: `3/3` in each Closed arm had at least one
read, for the scorer's unnecessary-control-read rate of `1.0`; pooled across
both Closed arms, that is `6/6` healthy control runs. The three `ABSTAIN`
controls in each arm also had reads, but were already read-bearing under Legacy
and are outside that rate's denominator. All six control terminals per arm
remained correct, so the cost increased without a control regression.

### Did automatic dispatch reduce Provider calls and tokens?

Yes. Pooled Auto versus Model reduced Provider calls by `46` and tokens by
`33,866`. `AUTO_CLOSED` used 16 Provider calls and 13,924 tokens versus
`MODEL_CLOSED` at 46 calls and 34,857 tokens. This is a cost/reliability effect,
not a quality effect.

### Which mechanisms remain weak?

CPU saturation and memory leak remain weak under action ambiguity. Three of
four resource-silent incidents ended in false `NO_INCIDENT` in both Closed
arms. Configuration error, service unavailable, and dependency latency retained
the read-then-Diagnosis path on this frozen set.

### Were the v2.2.2 fixes behaviorally active?

Yes. Source-aware action masking, the current Gap Graph, Negative Coverage,
Post-Read Delta, terminal admission, and bounded short selection protocol all
executed in the common runner. Top-4 oracle-path recall was `1.0` in every arm,
all read-bearing runs had a terminal candidate afterward, and post-repair
protocol success was `1.0`. Their presence did not guarantee the new Top-1
target would be useful.

### Was the effect admission, dispatch, or interaction?

Primarily admission, but below threshold. The pooled admission effect improved
resource-silent accuracy by `0.25` and reduced premature `NO_INCIDENT` by
`0.25`; the admission-only threshold required a `0.50` decrease. Dispatch had
no exact, oracle-hit, or diagnosis-after-read improvement. Exact-rate
interaction was `0.00`.

## Frozen error classification

Across the 64 runs:

- premature `NO_INCIDENT` on resource-silent incidents: 14 treatment-case
  outcomes (4 in each Legacy arm, 3 in each Closed arm);
- closure read predicate yield: 2;
- closure read empty: 12;
- closure source failure: 0;
- Model selected wrong gap-relevant action: 3 (d05, d06, d08 in
  `MODEL_CLOSED`);
- Auto Top-1 selected wrong gap-relevant action: 3 (the same case IDs in
  `AUTO_CLOSED`);
- correct terminal available but wrong terminal selected: 0 observed;
- read then correct Diagnosis: 26;
- read then wrong Diagnosis: 0;
- read then `ABSTAIN`: 12;
- read then `NO_INCIDENT`: 12;
- protocol failure: 0;
- transport failure: 0.

No result byte from v2.2, v2.2.1, or v2.2.2 was modified.
