# RCA100 Metrics Arbitration v1 Evaluator Repair Decision

- Decision code: `POST_LOCK_MAPPING_ENVELOPE_REPAIR`
- Repair protocol: `rca100-metrics-arbitration-v1-evaluator-repair.1`
- Classification: `POST_LOCK_EVALUATOR_ONLY_REPAIR`
- Predictions: `IMMUTABLE_PREDICTIONS`
- Provider access: `NO_PROVIDER_CALLS`
- Evaluation method: `EXTERNAL_SCORING_WITH_REPAIR_DISCLOSURE`

## Context and original defect

The original one-shot RCA100 execution generated and locked 103 answer-blind
predictions before answer material was acquired. Its frozen evaluator then
loaded `mapping.json` and iterated the top-level object as though that object
were the 103-task mapping:

```python
mapping_value = load_strict_json(answer_root / "mapping.json")
for key, value in mapping_value.items():
    ...
```

The frozen official source instead uses an envelope. The task mapping is the
`task_to_case_id` object inside that envelope. The original protocol therefore
remains permanently stopped as `BLOCKED_PROTOCOL_DRIFT`; this decision does not
rewrite or resume it.

## Authorized repair

The only authorized evaluator semantic change is to validate the exact frozen
top-level envelope schema, extract `mapping_value["task_to_case_id"]`, and pass
that inner object to the existing exact 103-task coverage and Ground Truth
loading path.

The repaired loader accepts only the frozen official envelope shape. It rejects
legacy flat mappings, missing or extra top-level fields, changed top-level JSON
types, missing or extra task keys, non-string case identifiers, and empty case
identifiers. It does not guess a mapping field, recursively search for task
keys, inspect Ground Truth to repair the mapping, or write a normalized mapping
artifact.

## Explicitly unchanged boundaries

The repair does not change:

- `parse_ground_truth()`;
- `prediction_correct()`;
- `fault_correct()`;
- `evaluate_terminals()` or `RCA100CaseScore`;
- `paired_inference()`, bootstrap replicates, bootstrap seed, percentile
  interval, or exact McNemar calculation;
- entity normalization, `same_as` handling, fault-text normalization, subgroup
  definitions, or the fixed denominator of 103;
- M3, Prompt, Provider construction, terminal records, private schedule,
  projections, aliases, or the public leakage policy.

No real case identifier, root entity, fault type, per-task special case, or
official mapping value may be added to tracked code, tests, or public evidence.

## Claim boundary

The original PR #22 disposition remains `BLOCKED_PROTOCOL_DRIFT`. Any result
produced by this separately authorized repair must be marked
`POST_LOCK_EVALUATOR_REPAIR_DISCLOSED` and must state that predictions remained
immutable and answer-blind.

Required disclosure:

> Predictions were generated and locked in a one-shot, answer-blind RCA100
> execution. After terminal lock, the frozen evaluator was found to misread the
> official `mapping.json` envelope. A separately authorized evaluator-only
> repair unwrapped the frozen `task_to_case_id` field. No Provider call,
> prediction rerun, M3 change, case replacement, entity-alias change, or
> scoring-rule change was performed.

The repaired result must not claim that the preregistered evaluator ran
unchanged end-to-end.
