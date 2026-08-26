# DTA v2.3.4 Registration-Assistance Blocked Error Analysis

## Frozen disposition

The exact terminal is:

`BLOCKED_DTA_V234_PROVIDER`

This is not a measured study result. The fixed 16-task × 2-arm evaluation did
not start, its execution count is zero, and no evaluation JSON, Markdown,
measured-result terminal, or engineering terminal exists.

## Gates that passed

The fresh evaluation set contains 16 tasks and passed
`DTA_V234_EVALUATION_DATA_PASS`. It includes ten hidden-known tasks, four
genuinely unregistered tasks, one duplicate control, and one insufficient-
evidence control while keeping truth evaluator-only.

The deterministic fixture preflight completed all 32 arm paths and passed
`DTA_V234_RUNTIME_PREFLIGHT_PASS` with zero runtime exceptions, invalid
authorization transitions, unmapped DSL rules, invalid clause references,
compiler exceptions, premature truth reads, or action-authority violations.

The independent engineering and claim audit recorded
`Must Fix 0 / Claim Accuracy PASS`, but explicitly did not authorize the fixed
study because the Provider-smoke pass gate was missing.

## Provider-smoke campaign

The single campaign covered every required role:

- `rt-001` and `rt-003`: hidden-known;
- `rt-011` and `rt-012`: genuinely new declarative-ready targets;
- `rt-014`: genuinely new engineering-required target;
- `rt-015`: duplicate control;
- `rt-016`: insufficient-evidence control.

It preserved 22 requests and 22 responses. Twelve requests were protocol
repairs. The campaign consumed exactly two real fixes, the permitted maximum:

1. `V234_PROTOCOL_FEEDBACK_AND_MODE_BINDING` added safe protocol feedback and
   cross-field mode binding.
2. `V234_SMOKE_RESUME_ISOLATION` separated local review state during the
   bounded resume.

The earlier manifests, diagnostics, partial journals, original STARTED
sentinel, and both repair records remain preserved. The active manifest SHA-256
is `742771eb3f59bddc1b8be4b38eae9614f800a0d984cb639982474636c571edf2`.

## Final role dispositions

- `rt-001` parsed and failed closed as a core collision, which is safe for the
  hidden-known role.
- `rt-003` exhausted protocol repairs.
- `rt-011` produced a formal draft whose predicate ordering was not canonical,
  so deterministic validation rejected it.
- `rt-012` exhausted protocol repairs.
- `rt-014` exhausted protocol repairs.
- `rt-015` was correctly non-promotable as an existing duplicate with zero
  Provider calls.
- `rt-016` was correctly non-promotable as insufficient evidence with zero
  Provider calls.

These failures are retained in the denominator of the smoke campaign. They
were not hidden, converted into fixture successes, or followed by a third fix.

## Why execution stopped

The Goal requires all expected smoke Provider calls to parse, repair limits to
hold, forbidden code and Runbook fields to remain absent, and the validator to
separate ready, engineering-required, duplicate, and insufficient outcomes.
The campaign did not satisfy the parse/validation portion, so no
`DTA_V234_PROVIDER_SMOKE_PASS` artifact was created. The maximum two real fixes
were already used. Starting the fixed study, repairing the frozen campaign, or
rerunning it would violate the active execution contract.

## Safety and claim boundary

Provider calls total 22. Docker calls, new live faults, Agent writes, Runbook
executions, remediation registrations, and action-authority violations are all
zero. The implementation remains replay-only and non-actionable.

The work supports an engineering artifact claim for human-authorized formal
drafting, deterministic compilation, shadow evaluation, and extension
diagnosis. It does not support any registration-assistance effect, measured
quality improvement, generalization, production autonomy, or remediation
claim. A separately authorized successor protocol is required for further
Provider work.
