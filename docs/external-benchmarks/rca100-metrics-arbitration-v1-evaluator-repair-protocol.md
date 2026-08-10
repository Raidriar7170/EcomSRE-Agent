# RCA100 Metrics Arbitration v1 — Evaluator Repair Protocol

Protocol ID: `rca100-metrics-arbitration-v1-evaluator-repair.1`

Method status: `POST_LOCK_EVALUATOR_REPAIR_DISCLOSED`

## Post-lock Evaluator Repair Disclosure

Predictions were generated and locked in a one-shot, answer-blind RCA100
execution. After terminal lock, the frozen evaluator was found to misread the
official `mapping.json` envelope. A separately authorized evaluator-only repair
unwrapped the frozen `task_to_case_id` field. No Provider call, prediction
rerun, M3 change, or case replacement was performed. Apart from the envelope
extraction, the scorer, entity matching, statistics, and fixed denominator were
unchanged.

The original PR #22 protocol remains permanently `BLOCKED_PROTOCOL_DRIFT`.
This repair neither rewrites nor resumes it. The repaired result may state that
external RCA100 predictions were generated answer-blind and scored after a
separately authorized evaluator-envelope repair. It must not state that the
preregistered evaluator executed unchanged.

## Frozen inputs and boundaries

- Original implementation: `7a0c22fa82a967730e238ac666f565cd935014ee`
- Original protocol freeze: `7d4684825216f4791d8dae4061bca95995381928beba6f504865854468ca5011`
- Original terminal tree: `a404226b0f79ac34887997bec230e8a1736cd16595299e68a8f166439eb8762c`
- Original run-attempt tree: `4f05d296ec3b66848d4d50b5222db7e67e6aea2ef9c52d46a5de19adfbeb9e7b`
- Original Provider-sidecar tree: `a3988a7719ccaeee80ba708592f4674f3a2bcd8382f0557ad5579bb93b5b67fe`
- Fixed denominator: 103
- Provider calls added by repair: 0
- Prediction reruns and case replacements: 0

The source snapshot is create-once and binds the exact official commit, the
complete answer-content tree, the mapping file hash, the exact top-level field
set and JSON types, the 103-entry task-key-set hash, and the original terminal
lock. Mapping values, per-case Ground Truth, predictions, evidence, reasoning,
private paths, credentials, and Provider endpoints are never public.

## Authorized loader repair

The loader accepts only the snapshot-frozen top-level envelope: two object
fields, one non-boolean integer metadata field, and one string metadata field.
It extracts only `task_to_case_id`, requires exactly 103 expected task keys and
non-empty string identifiers, and then reuses the unchanged Ground Truth parser.

Flat mappings, missing or extra fields, wrong JSON types, invalid coverage,
empty identifiers, and new parser incompatibilities fail closed. The protocol
does not guess fields, recursively search task keys, consume the reverse mapping
for scoring, or generate a normalized mapping artifact.

## Unchanged scoring and statistics

`parse_ground_truth()`, entity correctness, fault correctness,
`evaluate_terminals()`, paired bootstrap, exact McNemar, subgroup definitions,
M3, Prompt, projections, entity normalization, aliases, terminals, and schedule
remain bound to their original source hashes. Bootstrap uses 10,000 paired
replicates with seed 20260810 and a fixed denominator of 103.

The primary builder writes aggregate and private case-score evidence beneath
the separate repair control root. The independent verifier reloads the original
terminal lock, repair answer lock, frozen schedule, and frozen scorer, then
recomputes the complete case-score vector, headline counts, paired inference,
and subgroups without using the builder aggregate as input. Exact disagreement
blocks final report freeze.
