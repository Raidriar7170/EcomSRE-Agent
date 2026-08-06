# Phase 5B v2 Frozen Analysis-Only Results

**Status:** `PHASE5B_V2_FINAL_REPORT_FROZEN`
**Frozen claim classification:** `NO_PREREGISTERED_ADVANTAGE_SUPPORTED`

## Evaluation boundary

- Input execution: Phase 5B v1 frozen 180-run evidence.
- Analysis: Phase 5B v2 analysis-only metadata-contract repair over identical immutable execution evidence.
- Additional Provider calls: `0`.
- Agent or scored-run reruns: `0`.
- Diagnosis outputs modified: `NO`.
- Ground Truth decision/root/mechanism changes: `0`.
- Prompt, Agent runtime, execution schedule, budgets, Provider model, statistics, thresholds, and primary population modified: `NO`.
- Private difficult-subset metadata used: `NO`; secondary grouping comes only from the preregistered public template mapping.
- All terminal failures remain in their frozen denominators.

## Frozen integrity bindings

- v1 execution report SHA-256: `9b8763069df52d0ae66c3b75df3c578db15ed9789b2a69379893b5abaa78837f`
- v1 unblinding record SHA-256: `3f16f29cdd178ad39c199e40af09f9e068ef82b7d95235ff7622ecac630f47b4`
- Ground Truth pack SHA-256: `e9743db596bf580dd5a9b31488e502e472fa9169ea57cbbb8234285c617f6aa5`
- Immutable raw-record manifest SHA-256: `53b2f1a7d4b8c6a593d2a82babf19c119857805d4c8a6e81be7c26865064e88f`
- v2 scoring bundle SHA-256: `5c325620b0094fbcb94d8f16718d2618a6f488f7c7668170d71029b7fa845b53`
- v2 final report SHA-256: `0cd8bf334ed8dfa5ba5065051578377c770054d5a4faa8d95e04380b242c1a0d`
- v2 final disposition SHA-256: `16b423f385f8e4dc38b36b3e20701fedc54e971141290ab282b0b9316f72b0b1`

## Hidden-only primary

| Variant | Decision Accuracy | Average tool calls | Runtime completion | Evidence validity |
| --- | ---: | ---: | ---: | ---: |
| Single | 53.3% (30) | 4.00 | 80.0% | 56.7% |
| Fixed | 63.3% (30) | 4.00 | 80.0% | 43.3% |
| Dynamic | 63.3% (30) | 3.00 | 86.7% | 46.7% |

The primary paired comparison is Dynamic minus Single over 30 hidden template/seed pairing units:

- Decision Accuracy difference: `+10.0 pp`.
- 95% hierarchical paired CI: `[-16.7 pp, +36.7 pp]` from `10,000` replicates.
- Accuracy non-inferiority: `NOT PASSED`.
- Dynamic tool-call reduction: `25.0%`.
- 95% hierarchical paired CI: `[25.0%, 25.0%]` from `10,000` replicates.
- Cost-quality supported: `NO`.
- Frozen claim: `NO_PREREGISTERED_ADVANTAGE_SUPPORTED`.

## Full-suite secondary

| Variant | Decision Accuracy | Average tool calls | Runtime completion | Evidence validity |
| --- | ---: | ---: | ---: | ---: |
| Single | 50.0% (60) | 4.00 | 80.0% | 68.3% |
| Fixed | 70.0% (60) | 4.00 | 78.3% | 60.0% |
| Dynamic | 76.7% (60) | 2.83 | 90.0% | 70.0% |

## Public-anchor descriptive

| Variant | Decision Accuracy | Average tool calls | Runtime completion | Evidence validity |
| --- | ---: | ---: | ---: | ---: |
| Single | 46.7% (30) | 4.00 | 80.0% | 80.0% |
| Fixed | 76.7% (30) | 4.00 | 76.7% | 76.7% |
| Dynamic | 90.0% (30) | 2.67 | 93.3% | 93.3% |

## Difficult-subset aggregates

These are aggregate secondary groupings derived only from the preregistered public template mapping.

| Public subset | Pairing units | Single accuracy | Fixed accuracy | Dynamic accuracy |
| --- | ---: | ---: | ---: | ---: |
| `missing_telemetry` | 5 | 0.0% | 100.0% | 100.0% |
| `delayed_stale_telemetry` | 5 | 80.0% | 40.0% | 100.0% |
| `conflicting_evidence` | 5 | 40.0% | 60.0% | 0.0% |
| `decoy_confounded_change` | 10 | 50.0% | 60.0% | 90.0% |
| `cross_service_cascade` | 5 | 0.0% | 40.0% | 40.0% |
| `multi_service_anomaly` | 5 | 20.0% | 60.0% | 60.0% |
| `partial_tool_failure` | 5 | 80.0% | 80.0% | 80.0% |
| `required_abstention` | 10 | 70.0% | 60.0% | 90.0% |
| `safe_remediation` | 10 | 90.0% | 100.0% | 100.0% |
| `no_write_anomaly` | 10 | 70.0% | 70.0% | 80.0% |

## Failure and ablation boundary

- Main terminal failure count retained: `69` of `180`.
- Ablation slots: `38` frozen gap slots.
- Ablation implementation available: `NO`.
- Ablation model evidence available: `NO`.
- Ablation primary eligible: `NO`.
- Ablation disposition: `ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS`.
- These slots are not ablation results and are not model evidence.

## Resume-safe claim

On the frozen hidden-only primary population, Dynamic achieved 63.3% Decision Accuracy versus 53.3% for Single (+10.0 pp), while the preregistered 95% hierarchical paired CI [-16.7 pp, +36.7 pp] did not establish superiority or accuracy non-inferiority. The frozen classification is `NO_PREREGISTERED_ADVANTAGE_SUPPORTED`.
