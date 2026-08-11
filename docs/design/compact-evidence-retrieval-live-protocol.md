# Compact Evidence-Retrieval Live Protocol

## Classification

This is one consumed development evaluation of one architecture candidate. It is not external validation. PR #26 remains the preserved negative H1 experiment; this protocol does not rerun or reclassify it.

## Frozen inputs and implementation

Before Provider admission, the implementation commit, changed-file hashes, retrieval policy, B0/C1 prompts and schemas, model, schedule, gates, budgets, input bindings, and the passed admissibility aggregate are locked outside the repository. B0 must retain system-prompt SHA-256 `6b64c9e43f25029ca2f76f491faf98906c70fe888270284bf4bd3ff47e564049`.

The label-free runtime prepares and seals all 163 base and candidate contexts before evaluator truth is imported. The model payload contains no benchmark identity, source identity, truth, case score, paired-arm outcome, or candidate correctness. Case-level schedules, contexts, outputs, scores, and canonical candidate mappings remain private.

## Provider policy and preflight

The model and request policy are locked by `config/rca-compact-evidence-retrieval-v1/contract.json`: temperature 0, top-p 1, concurrency 1, minimum spacing 5 seconds, no fallback, no semantic retry, no schema retry, and at most one allowlisted byte-identical transport retry.

There is no live Smoke. Provider admission consists of exactly two synthetic non-case requests: one B0-shaped request and one C1 compact-candidate request. Both must complete with known usage, zero HTTP 429, zero invalid schema, and a valid C1 candidate ID. One generic preflight-only Prompt/Schema interface repair is permitted before any real case is admitted; a second failure terminates as `BLOCKED_COMPACT_PROVIDER_PREFLIGHT`.

## One paired TUNE

The frozen schedule uses seed `20260814` and contains 103 RCA100 pairs plus 60 consumed OB/SS TUNE pairs: 163 pairs and 326 semantic calls. Case order is shuffled once. Odd pairs run B0 then C1; even pairs run C1 then B0. Terminal and attempt artifacts are create-once. An interrupted admitted request is sealed without reissue.

After the first real admission, no retrieval, Prompt, schema, gate, budget, or schedule edit is allowed. There is no rerun, Regression, new candidate, or post-result tuning.

## Frozen success gate

C1 must terminalize 163/163, complete at least 160/163, produce at most two invalid schemas, and produce zero HTTP 429 and privacy/schedule failures. RCA100 requires exact rescue greater than damage, net rescue at least +3, damage at most two, C1 exact greater than B0, nonnegative service net, and no increase in downstream selection or layer mismatch. OB/SS requires nonnegative root net, damage at most two, C1 root no lower than B0, and nonnegative pair net. Combined root net must be positive; invalid candidate IDs, Specialist calls, and Fusion calls must be zero; semantic operations must total 326. Mean C1 input tokens must be at most 1.20 times B0 and mean C1 latency at most 1.30 times B0.

Passing ends as `COMPACT_EVIDENCE_RETRIEVAL_LIVE_DEV_PASSED_READY_FOR_MERGE_REVIEW`. Any failed live gate ends as `COMPACT_EVIDENCE_RETRIEVAL_LIVE_DEV_NOT_PASSED_KEEP_A0`. Neither disposition authorizes merge, release, tag, or Regression.

## Evidence publication

Public outputs contain aggregate metrics and the required development-only classification. They must not contain case IDs, run IDs, case-level entities or answers, candidate mappings, raw Provider output, private paths, credentials, or Provider endpoints. After the run, the evaluator locks the terminal tree, writes private case scores, publishes aggregate JSON/Markdown plus a Human Brief, recomputes the canonical aggregate, and runs the public leakage scanner.

## Activation disposition

This live protocol was not activated. The one offline admissibility audit ended as `COMPACT_RETRIEVAL_ADMISSIBILITY_NOT_PASSED_KEEP_A0`, so no Provider preflight, live Smoke, real-case admission, paired TUNE, or Regression was run.
