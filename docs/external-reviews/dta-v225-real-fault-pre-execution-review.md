# DTA v2.2.5 Real-Fault Shadow Pre-Execution Review

## Scope

- Review type: independent, read-only, pre-live
- Reviewed implementation HEAD: `22c8c5cc979141712cf3ed2b6eb0818f07b3fb39`
- Branch: `codex/dta-v225-real-fault-shadow`
- Docker calls: `0`
- Provider calls by reviewer: `0`
- Repository writes by reviewer: `0`

## Review history

The first review of `52391e6a36f8322568a59251cf8532bd54f1da75`
reported six Must Fix items: a truth-bearing Flat `scenario_id`, missing
post-capture manifest binding, unenforced campaign/execution consumption,
public physical alias mappings, incomplete real-text sanitization, and an
incorrect Flat first-useful-evidence ordinal.

The second review of `21229e02c65f62db384fdd5e6936d336df73ee7d`
confirmed those structural fixes and found two remaining fail-closed gaps:
concatenated service/private-path variants and replacement eligibility without
an infrastructure-only cause proof.

The third review of `6f3f179587defc684605143279f1b3e8f6f44cff`
confirmed the sanitizer closure and required replacement eligibility to use
explicit trusted failure types rather than stage inference.

The final review of `22c8c5cc979141712cf3ed2b6eb0818f07b3fb39`
found no remaining issue. Direct probes confirmed arbitrary `KeyError` and
`RuntimeError` produce no replacement authority; only explicit trusted local
Sandbox failures and `NoHealthyComparatorV225` at comparator selection can
mint the corresponding bounded cause. The fixed campaign gate additionally
requires no baseline capture, fault capture, Provider shadow, or paired result,
plus exact baseline restoration and `CLEAN` cleanup.

## Required seven-question audit

1. `V2_STYLE_FLAT_ADAPTIVE` is described as v2-style, not the exact frozen v2 identity: `PASS`.
2. Both arms share capture bytes, aliases, bootstrap, model, and truth boundary: `PASS`.
3. Provider payloads exclude physical/comparator identity and evaluator truth: `PASS`.
4. The live adapter has zero Agent-write, ActionProposal, and Runbook authority: `PASS`.
5. Semantic actions, target-equivalent reads, and shared capture cost are separate: `PASS`.
6. Restoration and cleanup are attempted on every terminal before paired execution: `PASS`.
7. Replacement is limited to an exact eligible pre-result infrastructure/telemetry failure: `PASS`.

## Verification

- Focused offline suite: `33 passed`
- `git diff --check`: `PASS`
- Reviewed worktree: exact and clean

## Final verdict

```text
Must Fix: 0
Claim Accuracy: PASS
```
