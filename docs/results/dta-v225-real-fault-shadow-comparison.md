# DTA v2.2.5 Real-Fault Transfer and Paired Shadow Comparison

- Study: one bounded owned local real-fault capture campaign
- Captured physical states: 2 (`BASELINE`, `AD_CPU_FAULT`)
- Opaque paired cases: 4
- Snapshot arm-runs: 8
- Full paired-comparison execution count: 1
- Provider model: `gpt-5.4-mini-2026-03-17`
- Agent writes / ActionProposals / Runbook executions: `0 / 0 / 0`
- Baseline restored: `true`
- Owned cleanup: `CLEAN`
- Non-owned changes: `0`

## Frozen terminals

```text
DTA_V225_REAL_FAULT_TRANSFER_NOT_SUPPORTED
CURRENT_RUNTIME_DESCRIPTIVE_ADVANTAGE
```

The second terminal is the preregistered descriptive cost disposition, not a
diagnostic-quality or statistical-superiority claim. Both arms completed 0/4
cases exactly. `CURRENT_RUNTIME_BUNDLE` used fewer calls, tokens, and recorded
reads only because it failed closed without a successfully recorded bundle read
and before Provider selection on all four snapshot cases.

## Comparison boundary

The baseline arm is the **v2-style Flat Adaptive baseline using the v2.1
CPU-capable ontology**. It represents the earlier free Tool-Using architecture,
but it is not the exact frozen historical v2 Agent identity or Prompt.

The current arm is the v2.2.5 `CURRENT_RUNTIME_BUNDLE` path. The experiment is
diagnosis-only and read-only. It does not compare remediation, ActionProposal,
Runbook, or recovery policy.

Two public alias maps render the same two physical captures into four cases.
They are identity counterfactuals, not four independent live faults and not a
physical fault-target counterfactual. The physical CPU fault was always on Ad;
the healthy comparator selected before injection was Recommendation.

## Real capture evidence

The accepted `campaign-0002` reused the existing owned v2.1 Ad CPU lifecycle.
The baseline and fault captures came from that single lifecycle. In the public
opaque capture, the fault-target CPU samples rose from a baseline maximum of
`3.403%` to a fault range of `400.784%` to `406.107%`; the comparator remained
below `2.514%` in the fault capture. Both services remained running and healthy.

The evaluator then restored the frozen v2.1 capture baseline. Its canonical
semantic SHA-256 was
`14bd13734d46566828779fd61b16e654cc260274a0e30ae9948371a9dbba5beb`,
with `adHighCpu=off` and `loadGeneratorVUs=25`. The lifecycle verified the
private file and live flag-control readback before cleanup.

After cleanup, owned containers, networks, and volumes were all zero. The five
declared loopback ports were free, and the three pre-existing non-owned Docker
volume IDs and three system network IDs were unchanged.

## Paired snapshot results

| Arm | Exact | Fault exact | Baseline exact | Provider calls | Tokens | Latency | Semantic actions | Target-equivalent reads | Protocol failures | Transport failures |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `V2_STYLE_FLAT_ADAPTIVE` | 0/4 | 0/2 | 0/2 | 14 | 27,166 | 19.496 s | 10 | 12 | 4 | 0 |
| `CURRENT_RUNTIME_BUNDLE` | 0/4 | 0/2 | 0/2 | 0 | 0 | 0 s | 0 | 0 | 4 | 0 |

Flat requested Resources on all four cases. It used one target on both MAP_A
cases and both targets on both MAP_B cases. Only `fault-map-b` covered the
correct physical fault target, but its final output still failed the protocol.
Three Flat reads were classified empty and none yielded a support predicate.

Current failed closed without successfully recording a Resources bundle read.
Therefore the observed bundle cost was zero semantic actions and zero
target-equivalent reads; the usual two-target `BUNDLE_ONE` accounting of one
semantic action and two target-equivalent reads was never exercised in this
run. The frozen artifact does not carry a narrower internal error code, so it
does not prove the exact pre-read substage.

Both arms received the same opaque capture SHA-256 for each case. Truth was
loaded only after paired ordinals `2, 4, 6, 8`, and the exact counterbalanced
schedule was executed once.

## Shared capture cost

The one-time live capture acquisition cost is reported separately from adaptive
arm cost:

```text
shared physical captures: 2
shared capture semantic actions: 16
shared capture target-equivalent reads: 20
```

These shared reads are not charged to either comparison arm.

## Live shadow result

Exactly one optional live baseline shadow and exactly one live fault shadow ran
through `LocalSandboxReadBackend`, both with `CURRENT_RUNTIME_BUNDLE` and zero
write authority. Both ended `PROTOCOL_FAILED / FAILED` without a successfully
recorded bundle read and before Provider selection. Thus the live and frozen
current paths agree only at the failure-terminal level; they do not establish
successful live adapter transfer.

## Interpretation limits

No statistical significance test was performed on four paired cases. This
study does not establish production SRE capability, unknown-fault
generalization, Provider reliability, successful evidence routing on a real CPU
fault, or remediation quality. The prior frozen v2.2.5 terminal
`DTA_V22_5_NO_AMBIGUITY_EFFECT_OBSERVED` remains unchanged.
