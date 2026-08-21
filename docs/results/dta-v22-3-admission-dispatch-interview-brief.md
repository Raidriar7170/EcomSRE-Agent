# DTA v2.2.3 Interview Brief

## 30-second version

The prior DTA study exposed a specific failure: healthy bootstrap coverage could
admit `NO_INCIDENT` before unread resource evidence, and the model often chose
low-yield actions. I implemented a one-step evidence-closure contract and a
separate deterministic Runtime Top-1 dispatch treatment, then preregistered and
ran one new 16-case × 4-arm replay study. The engineering worked—64/64 runs,
zero Agent writes/exceptions, bounded repairs, and Runtime Top-1 removed 46
Provider calls and 33,866 tokens—but the quality hypothesis did not. Only one of
four resource-silent incidents was recovered, so the honest terminal is
`DTA_V22_3_NO_FIX_EFFECT_OBSERVED`.

## Personal contribution

- Bound every historical v2.2/v2.2.1/v2.2.2 result byte before mutation.
- Audited the old development portfolio and froze a truth-independent ranking
  with a source/predicate Beta(1,1) prior and deterministic canonical tie order.
- Added `LEGACY` versus `ONE_GAP_RELEVANT_READ` admission without a generic
  forced-READ or early-ABSTAIN gate.
- Added `MODEL_TOP4` versus `RUNTIME_TOP1`, with terminal selection still owned
  by the Provider and no truth exposed to runtime.
- Built the shared case-interleaved 2×2 runner, scorer, bounded Provider
  protocol, offline oracle simulation, new frozen dataset, and single-execution
  verification.
- Preserved the negative measured result rather than tuning or rerunning the
  final study.

## Architecture to explain

```text
Replay bytes
  -> Salient Memory
  -> effective support policy + Gap Graph
  -> truth-independent action ranking
  -> [Model Top4 | Runtime Top1]
  -> read + Negative Coverage + Post-Read Delta
  -> [Legacy | one-step evidence-closed NO_INCIDENT]
  -> runtime-admissible T-only terminal selection
```

Only admission mode and dispatch mode vary. Model, prompt, case bytes, policy,
ranking, budgets, repairs, retries, terminal catalog, and truth-isolation rule
are shared.

## Strongest evidence

- Development: `AUTO_CLOSED` 16/16 exact versus `MODEL_LEGACY` 12/16; this was
  only a development gate.
- Frozen evaluation: `AUTO_CLOSED` 13/16 versus 12/16, resource-silent 1/4,
  premature `NO_INCIDENT` 3/4, controls 6/6.
- Runtime Top-1: no quality gain, but 46 fewer pooled Provider calls and 33,866
  fewer tokens.
- Reliability: post-repair protocol success 1.0, zero protocol/transport
  failures, zero exceptions, zero Agent writes.

## Why the result stayed negative

The closure contract guaranteed a relevant action class, not the correct target
inside a symmetric action surface. On d05, d06, and d08, the selected Resources
read was gap-relevant but targeted the peer service and returned empty. Because
the preregistered contract required exactly one attempt—not a generic forced
second read—`NO_INCIDENT` could reopen. The development tie order did not
generalize to rebound final service identities.

## Safe claims and non-claims

Safe: deterministic replay, truth isolation, exact one-run accounting, bounded
protocol handling, cost reduction, and a falsified quality hypothesis.

Do not claim: robust incident diagnosis, CPU/memory generalization, research
significance, live SRE operation, remediation, Docker validation, or production
readiness.
