# RCAEval RE2 v2-dev.1 Protocol

Status: `DEVELOPMENT_VISIBLE / DESIGN_SET / NOT_EXTERNAL_HOLDOUT / NOT_PRIMARY_INFERENCE`

Protocol ID: `rcaeval-re2-v2-dev.1`

This protocol supersedes the runtime boundary used by v2-dev-v1; it does not
rewrite that version's negative result. PR #14 and its 10 terminal records,
29 Provider operations, schedules, run identifiers, Gate documents, and
post-terminal reconstructed lock remain negative evidence only.

## Scope

- Systems: RE2-OB and RE2-SS only.
- Split: the inherited 60-case DESIGN set; 120 DEV_VALIDATION cases remain
  reserved and inaccessible in this task.
- Variants: three frozen v1 references plus `single_v2_dev1`,
  `fixed_v2_dev1`, and `dynamic_v2_dev1`.
- Runs: 72-run Smoke, followed only after a passing Smoke Gate by the exact
  360-run DESIGN schedule.
- Formula: inherited F0. Re-selection, F1/F2 comparison, and new formulas are
  forbidden.
- RE2-TT, external holdout claims, adaptive escalation, transport retry,
  release, tag, and merge are excluded.

## Privacy boundary

Agent-visible free text is sanitized before typed input construction,
persistence, or Provider serialization. Supported local-path forms include
POSIX absolute paths, Windows drive paths, file URIs, and home-relative paths.
Each matched path is replaced by a stable, non-reversible 12-hex SHA-256 token;
the basename is not retained. A post-sanitization leakage scan fails closed as
`AGENT_VISIBLE_PRIVATE_PATH_REMAINED` without persisting the matched value.

## Operation transaction

Every operation creates its attempt marker before input work. Append-only stage
markers then follow this order:

1. `INPUT_SANITIZATION`
2. `INPUT_CONSTRUCTION`
3. `INPUT_PERSISTENCE`
4. `PROVIDER_CALL`
5. `OUTPUT_VALIDATION`
6. `OUTPUT_PERSISTENCE`
7. `COMPLETED`

Pre-Provider failures have zero model calls and token deltas. Recovery reads the
last legal marker, terminalizes as `STARTED_OPERATION_WITHOUT_TERMINAL`, and
does not repeat the operation. Operation records and run traces hash-bind the
stage trace and completion marker.

## Provider authorization

The actual create-once evaluation lock lives in an external control root, not
Git. It is created only from a clean implementation commit after all three new
schedules exist. It binds the implementation commit, source trees, tracked
configuration, schedule hashes, and a hash-only identity for the private output
root. Provider construction fails closed until that lock and output-root marker
verify. The preflight records zero Provider calls and zero run attempts before
authorization.

## Final Judge contract

The Judge must return exactly one `JudgeServiceDecisionV2`. Its service must be
from the Agent-visible service set, evidence references must be non-empty and
visible, and additional fields are forbidden. Local normalization is limited to
trimming and case-folding the service plus stable evidence-reference
deduplication. Unknown services are never replaced with a ranked candidate, and
there is no retry or Ground Truth correction. The configured Provider has not
demonstrated strict function-schema support, so the lock records `false` and
local strict validation remains authoritative. Safe diagnostics retain only
exception class, field paths, constraint types, and error count.

## Gates and claims

Smoke requires 72/72 terminal records, at least 35/36 completed v2 runs,
zero Final Judge schema failures, complete run/operation observability, exact
failure-stage attribution, positive known token accounting, no leakage, and no
more than 240 Provider operations. DESIGN is capped at 1200 Provider operations
and requires 360/360 terminal records plus complete v2 observability.

DESIGN results are development-only candidate-freeze review evidence. They do
not support an external superiority, production-readiness, or generalization
claim. No DESIGN result authorizes DEV_VALIDATION.
