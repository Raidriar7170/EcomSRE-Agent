# DTA v2.2 Practical Fixed Evaluation

## Result

The fixed evaluation ran exactly once on 12 cases with Flat Canonical and
Planner-Lite. All 24 arm-runs are represented. Case bytes were identical across
arms, truth was loaded after both arms, and there were zero transport failures,
uncaught runner exceptions, duplicate reads, or Agent writes.

The strict research PR #60 remained blocked and was not merged. This practical
successor used a simplified Provider boundary and ran no Docker, Runbook, Agent
write, or live remediation.

## Primary comparison

`run completion` is end-to-end exact success: correct terminal and, when
applicable, correct root, mechanism, evidence reference, and semantic clause.

| Metric | Flat Canonical | Planner-Lite |
| --- | ---: | ---: |
| End-to-end run completion | 0.0833 (1/12) | 0.2500 (3/12) |
| Operational valid-terminal rate | 0.7500 (9/12) | 0.8333 (10/12) |
| First-pass protocol success | 0.7857 | 0.8462 |
| Post-repair protocol success | 1.0000 | 1.0000 |
| Repair rate | 0.2143 | 0.1538 |
| Root-service accuracy (8 incidents) | 0.0000 | 0.1250 |
| Mechanism accuracy (8 incidents) | 0.0000 | 0.1250 |
| Mechanism Macro-F1 | 0.0000 | 0.1333 |
| No-Incident accuracy (2 cases) | 0.0000 | 0.5000 |
| Abstention accuracy (2 cases) | 0.5000 | 0.5000 |
| Evidence-ref validity (8 incidents) | 0.0000 | 0.1250 |
| Semantic clause validity (8 incidents) | 0.0000 | 0.1250 |
| Mean adaptive reads | 0.0000 | 0.0000 |
| Duplicate read attempts | 0 | 0 |
| Mean Provider turns | 1.2500 | 1.1667 |
| Input / output / total tokens | 17,173 / 710 / 17,883 | 19,539 / 662 / 20,201 |
| Mean latency | 2,173.84 ms | 1,802.95 ms |
| Transport retries | 0 | 0 |
| Uncaught exceptions / Agent writes | 0 / 0 | 0 / 0 |

## Planner advantage decision

The preregistered practical rule was satisfied:

- Mechanism Macro-F1 delta was +0.1333, meeting the +0.10 threshold.
- Mean reads were equal at 0.0, so Planner did not use more reads.
- Post-repair protocol success was 1.0, above 0.90.

Therefore Planner advantage was observed under this practical rule. The
absolute result is weak—one of eight incident mechanisms was correct for
Planner and zero for Flat—and the 12-case replay includes three explicitly
synthetic or counterfactual-derived cases. This is an interview portfolio
experiment, not a research, held-out-generalization, or production claim.

## All fixed cases

| Case | Truth | Flat | Planner-Lite |
| --- | --- | --- | --- |
| E01 | payment / configuration | `SEMANTICALLY_WRONG` | `SEMANTICALLY_WRONG` |
| E02 | payment / configuration | `PROTOCOL_FAILED` | `SEMANTICALLY_WRONG` |
| E03 | recommendation / unavailable | `SEMANTICALLY_WRONG` | `COMPLETED_CORRECT` |
| E04 | product-catalog / unavailable | `SEMANTICALLY_WRONG` | `SEMANTICALLY_WRONG` |
| E05 | email / memory | `SEMANTICALLY_WRONG` | `SEMANTICALLY_WRONG` |
| E06 | ad / CPU | `SEMANTICALLY_WRONG` | `SEMANTICALLY_WRONG` |
| E07 | shipping / dependency | `SEMANTICALLY_WRONG` | `SEMANTICALLY_WRONG` |
| E08 | payment / dependency | `PROTOCOL_FAILED` | `PROTOCOL_FAILED` |
| E09 | no incident | `SEMANTICALLY_WRONG` | `SEMANTICALLY_WRONG` |
| E10 | no incident | `SEMANTICALLY_WRONG` | `COMPLETED_CORRECT` |
| E11 | missing/conflicting | `PROTOCOL_FAILED` | `PROTOCOL_FAILED` |
| E12 | missing/insufficient | `COMPLETED_CORRECT` | `COMPLETED_CORRECT` |

## Frozen inputs and single-run evidence

| Artifact | SHA-256 |
| --- | --- |
| Manifest | `39c9a271be2e4136e9522f492e9cb8f1421ac2350394f50c3eb1987447791a4c` |
| Prompt | `bedb72395325800fe2c069f09ece1ca58777090fe049e373cc79a79dd254d3fe` |
| Case set | `243e7231dbd9e4aa5ed59cddfed2fb354a2c2bb552c02bf279cefddbec7f3204` |
| Truth set | `c1597f11627b89cd15fa6ca22d558b3f431ccbd338f3725e0ed1a899fbfb2ed3` |
| Ignored local raw result | `2b1dfbe1b4e550630a29cb6d3697be28c1e0f2eb3a507f410688843175b9cbf2` |

The evaluation executed at commit `e492b2e384d7ee8b310a9a8ab85b10b189855a76`
with `gpt-5.4-mini-2026-03-17`. The later scorer definition correction
recomputed exact completion, cited-evidence applicability, semantic-clause
applicability, and repair-inclusive Provider turns from those same immutable
run records without a Provider rerun.

The 16,000-byte Provider-visible hard cap was enforced, but this campaign did
not persist per-turn visible-state byte counts, so the 10,000-byte mean target
cannot be claimed. The committed token totals are the measured context-cost
surface.

See the [machine-readable report](dta-v22-practical-evaluation.json) and
[error analysis](dta-v22-practical-error-analysis.md).
