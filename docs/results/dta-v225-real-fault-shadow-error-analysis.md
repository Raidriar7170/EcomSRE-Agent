# DTA v2.2.5 Real-Fault Shadow Error Analysis

## Frozen conclusion

The single accepted owned live campaign produced one baseline capture and one
verified Ad CPU-saturation capture, then restored the exact frozen baseline and
completed `CLEAN` owned cleanup with zero non-owned changes. The four opaque
cases and eight scheduled snapshot arm-runs are represented exactly once.

The transfer result is:

```text
DTA_V225_REAL_FAULT_TRANSFER_NOT_SUPPORTED
```

All eight snapshot runs ended `PROTOCOL_FAILED`; neither arm returned an exact
fault Diagnosis or exact No-Fault terminal. The current arm also failed in its
one live baseline shadow and one live fault shadow without a successfully
recorded Resources bundle read. This is a preserved negative model/runtime
result, not an infrastructure blocker and not a reason to rerun the fault
episode.

## Required questions

### Did the current evidence runtime transfer to real telemetry?

No. `CURRENT_RUNTIME_BUNDLE` was 0/4 exact on frozen real captures. Its live
baseline and live fault shadows also returned `PROTOCOL_FAILED / FAILED` with
zero recorded Resources reads and zero Provider calls. The transfer-pass
conditions therefore failed even though the live backend, restoration, and
cleanup boundaries executed safely.

### Did the v2-style Flat arm read Resources for one target, both targets, or not at all?

It read Resources in all four cases. It selected one target on `fault-map-a`
and `baseline-map-a`, and both targets on `fault-map-b` and `baseline-map-b`.
This used 10 semantic actions and 12 target-equivalent reads in total. Only the
multi-target `fault-map-b` read covered the correct fault target; no final Flat
terminal was protocol-valid.

### Did either arm depend on opaque alias order?

The normalized predictions were consistent only because every prediction was
the same `FAILED` terminal. Flat's acquisition behavior did depend on the map:
MAP_A produced single-target Resources reads, while MAP_B produced multi-target
reads. Current recorded no adaptive read under either map. The result does not
support an alias-robust diagnostic-quality claim.

### Did Current detect and close the resource ambiguity in one bundle?

No. All four current snapshot runs and both current live shadows failed without
a successfully recorded bundle read. The frozen run artifact records
`bundle_resources_reads=0`, `resources_requested=false`, and
`all_candidates_covered=false`. The artifact does not persist a narrower
internal exception code, so no more specific read substage is claimed.

### Did either arm produce premature NO_INCIDENT on the real fault capture?

No. Neither arm emitted `NO_INCIDENT`; both fault cases ended `FAILED`. This
avoids fail-open behavior but is not a correct Diagnosis.

### Did Current reduce Provider calls, tokens, or latency?

Numerically yes: Current recorded 0 calls, 0 tokens, and 0 ms, compared with
Flat's 14 calls, 27,166 tokens, and 19.496 seconds. The reduction came from
fail-closed termination before Provider selection and without a recorded
adaptive read, not from a more efficient
correct Diagnosis. It is used only by the preregistered descriptive
disposition rule.

### How many target-equivalent backend reads did the bundle represent?

Zero in the observed study because no bundle read was successfully recorded.
The accounting contract would charge a successful two-target bundle as one
semantic action and two target-equivalent reads, but that path was not
exercised. Shared live capture acquisition separately used 16 semantic actions
and 20 target-equivalent reads.

### Did the live online shadow agree with the frozen-capture current result?

Yes only at the failure-terminal level. Live baseline, live fault, and all four
frozen current cases ended `PROTOCOL_FAILED / FAILED` without a recorded
Resources read and before Provider selection. This agreement does not
constitute successful live transfer.

### Were any differences caused by Provider transport rather than Diagnosis?

No observed run had a transport failure or transport retry. Flat failures were
protocol failures after 2 or 4 calls per case; Current failures occurred before
a Provider call. No completed wrong Diagnosis was retried.

### Did the baseline/no-fault control regress?

Yes at the diagnostic contract: both arms were 0/2 exact on baseline cases.
Flat ended protocol-failed after adaptive reads, and Current recorded no bundle
read. There were no false-positive fault Diagnoses because neither arm
produced a valid terminal.

### What remains untested because the physical fault target was always Ad?

The study does not test CPU faults on another physical service, physical target
counterfactuals, a second fault episode, unknown-fault generalization, or
remediation. Alias swapping tested only Provider-visible identity/order over
the same Ad-versus-Recommendation physical pair.

## Frozen error classification

Across the eight paired snapshot runs:

- correct fault Diagnosis: 0;
- wrong root: 0 valid terminals;
- wrong mechanism: 0 valid terminals;
- premature `NO_INCIDENT` on fault: 0;
- `ABSTAIN` on fault: 0;
- false-positive fault on baseline: 0;
- single-target Resources read: 2, both Flat MAP_A;
- multi-target Resources read: 2, both Flat MAP_B;
- no Resources read: 4, all Current;
- empty read: 3, all Flat;
- predicate-yield read: 0;
- protocol failure: 8;
- transport failure: 0;
- transport retries: 0;
- live/snapshot disagreement: 0 at the terminal class, with all Current paths
  failed;
- baseline restoration failure: 0;
- cleanup failure: 0;
- Agent writes / ActionProposals / Runbook executions: `0 / 0 / 0`.

## Why the descriptive disposition favors Current

Exact counts were tied at 0/4, baseline exact was tied at 0/2, and Current had
fewer Provider calls, fewer tokens, and no more target-equivalent reads. That
matches the preregistered rule for:

```text
CURRENT_RUNTIME_DESCRIPTIVE_ADVANTAGE
```

Because the cost reduction is inseparable from no-read protocol failure, the
disposition must not be restated as quality, transfer, causal, or production
advantage.
