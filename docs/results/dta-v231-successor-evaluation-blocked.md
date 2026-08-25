# DTA v2.3.1 Independent Successor — Blocked Result

Terminal: `BLOCKED_DTA_V231_REPOSITORY_ACCEPTANCE`

This was one independent successor execution with new fixed bytes. It was not a rerun of the consumed `BLOCKED_DTA_V231_EVALUATION_DATA` study.

## Exact execution state

- Execution count: `1`
- Planned cases / arms: `24 / 48`
- Completed cases / arms: `12 / 24`
- Completed case IDs: `vx-101` through `vx-112`
- STARTED sentinel SHA-256: `00dae62d32b74e63fac71fe4d70f74e17e171dc3ca91a61709704359d5ca9506`
- Partial JSONL SHA-256: `4c9722da29eabeb09892a4fc088ebea5c3a3a4e54bc8560616b84e59503abfe1`
- Final evaluation JSON / Markdown: not created
- COMPLETE sentinel: not written
- Reruns after failure: `0`

## Runtime blocker

`vx-113` aborted in `V23_STRICT_CONFLICT_GATE` before its treatment arm or truth-shard unlock. The frozen v2.3 strict Novelty Gate indexes `_INTERPRETATION_DOMAIN_V23[item.kind]`, but its mapping omits `LOG_ERROR_CLUSTER`. The case's memory-pressure log produces that generic anomaly, causing `KeyError: LOG_ERROR_CLUSTER` at `src/ecomsre/dta_v2/v23/novelty_gate.py:81`.

The algorithm and evaluation data were frozen before execution. They were not repaired after this terminal, and the existing STARTED sentinel and partial file make another invocation fail closed.

## Claim boundary

No 24-case metrics or measured-result terminal exist. In particular, this run does not support `EFFECT_OBSERVED`, `MIXED_RESULT`, `NOT_OBSERVED`, or `DTA_V231_CONFLICT_AWARE_DISCOVERY_COMPLETE`. The partial dispositions are diagnostic evidence only and are not a scored comparison.

No Docker command, live fault, Runbook execution, or Agent write authority was used.
