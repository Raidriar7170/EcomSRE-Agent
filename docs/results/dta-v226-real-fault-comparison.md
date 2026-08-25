# DTA v2.2.6 Real-Fault Transfer and Acquisition Comparison

## Result

The one authorized owned campaign and the one frozen paired execution completed
without a protocol, runner, or transport failure.

```text
DTA_V226_CURRENT_REAL_FAULT_TRANSFER_SUPPORTED
CURRENT_RUNTIME_ACQUISITION_ADVANTAGE
```

The repository-wide engineering marker is intentionally not minted in this
execution report; it additionally requires final review, exact-head CI, and the
Draft PR to be squash merged.

## Boundary

The comparison arms were:

- `MODEL_DIRECTED_RETRIEVAL`: v2-style free source/target selection with
  canonical runtime requests and shared terminal admission; not the exact
  frozen original v2 Agent.
- `CURRENT_RUNTIME_BUNDLE`: runtime-guided Resource Comparison Set followed by
  one target-complete contrastive Resources bundle and the same terminal
  admission protocol.

Both arms received identical opaque capture bytes per case, the same candidate
aliases, common Runtime/Metrics bootstrap, baseline profile, Provider model,
hypothesis ontology, Prompt, terminalizer, truth boundary, and scorer.

`MODEL_DIRECTED_RETRIEVAL` is not the exact frozen original v2 Agent.

## Physical campaign

- Accepted live campaigns: `1`
- Physical states: one healthy baseline and one verified Ad CPU-saturation state
- Comparator: `recommendation`
- Public aliases: `svc-20e1bc90a8`, `svc-d9ca249b54`
- Maps: two exact identity swaps over the same physical states
- Live baseline: `VALID_TERMINAL / NO_INCIDENT`
- Live fault: `VALID_TERMINAL / DIAGNOSED / CPU_SATURATION`
- Both live shadows: one physical two-target Resources request, all candidates covered
- Baseline restored: `true`
- Cleanup: `CLEAN`
- Non-owned changes: `0`
- Agent writes / ActionProposals / Runbooks: `0 / 0 / 0`

## Fixed paired execution

The counterbalanced schedule ran exactly once: four cases by two arms, eight
arm-runs total, one attempt per ordinal, with truth loaded only after both arms
for each case.

| Metric | MODEL_DIRECTED_RETRIEVAL | CURRENT_RUNTIME_BUNDLE |
|---|---:|---:|
| Valid terminals | 4 / 4 | 4 / 4 |
| Exact | 0 / 4 | 4 / 4 |
| Fault exact | 0 / 2 | 2 / 2 |
| Baseline exact | 0 / 2 | 2 / 2 |
| Evidence clauses valid | 0 / 4 | 4 / 4 |
| Resources selected | 0 / 4 | 4 / 4 |
| Multi-target Resources reads | 0 / 4 | 4 / 4 |
| Semantic evidence actions | 8 | 4 |
| Target-equivalent reads | 8 | 8 |
| Provider calls | 12 | 4 |
| Total tokens | 9,116 | 2,562 |
| Protocol repairs | 0 | 0 |
| Transport retries | 0 | 0 |

Model-directed retrieval used two empty evidence actions in each case and then
selected the admitted `ABSTAIN` terminal. Those are protocol-valid but
semantically inexact outcomes; they were preserved without retry. Current used
one target-complete Resources bundle per case, even though strict ambiguity was
false in all four cases, and reached the exact fault or No-Incident terminal.

The comparison is descriptive over four paired opaque renderings of two
physical states. No statistical significance test was performed, and the
result is not a claim about the exact historical v2 Agent or faults outside the
owned Ad CPU episode.

Machine-readable authority:
`docs/results/dta-v226-real-fault-comparison.json`.
