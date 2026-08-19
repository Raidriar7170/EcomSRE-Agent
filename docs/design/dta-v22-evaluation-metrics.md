# DTA v2.2 P0 Evaluation and Scoring Specification

Status: `PR_A_PROTOCOL / NO_EVALUATION_EXECUTED`

This document defines scoring before dataset freeze. It contains no held-out
answers and no empirical result. Later code and manifests must use versioned
machine-readable equivalents of these definitions.

## Arms and comparison roles

Primary paired comparison:

- `FLAT_CANONICAL_SALIENT`
- `PLANNER_LITE_SALIENT`

They share the model, `ControllerDecisionV22`, bootstrap, action catalog,
Salient Memory, read and correction budgets, Diagnosis admission, CandidateSet,
and Action Selection. The Planner alone receives a persistent runtime-owned
belief-ledger view.

Secondary anchors:

- `DETERMINISTIC_ROUTER_SALIENT`
- `ONE_SHOT_ORACLE_CONTEXT`

`ONE_SHOT_ORACLE_CONTEXT` is an `ORACLE_CONTEXT_UPPER_BOUND`, not a tool
planner. Its tool-source and target-selection scores are `N/A`; all context
materialization costs are still counted.

Development-only factorial arms are `FLAT_CANONICAL_FULL` and
`PLANNER_LITE_FULL`.

## Dataset and budgets

Each split has 24 cases: four Configuration, four Service Unavailable, two CPU,
two Memory Leak, four Dependency Latency, four No-Incident, and four
missing/conflicting-evidence cases. At least 16 cases per split must satisfy the
frozen evaluator-only planning-required definition, and at least eight
counterfactual pairs must share alert/candidate/bootstrap structure.

Budgets:

- common bootstrap evidence cost: 1;
- adaptive reads after bootstrap: at most 3;
- total evidence actions: at most 4;
- Provider investigation turns: at most 5;
- no-tool corrections: at most 1;
- Action Selection turns: at most 1.

Versioned action weights are 1 for runtime/core metrics/changes and 2 for
logs/resources/traces. Bootstrap, correction, and oracle materialization costs
are never omitted.

## Protocol metrics

- `first_pass_protocol_acceptance`: first controller response is structurally
  and semantically admissible.
- `post_correction_protocol_acceptance`: first response or the single bounded
  correction is admissible.
- `correction_rate`: runs using the one correction divided by all runs.
- `failure_code_distribution`: mutually visible counts of first and terminal
  protocol codes.
- `invalid_action_dispatches`: invalid/stale/out-of-budget choices that reached
  a read backend. Required value: zero.
- `duplicate_read_dispatches`: repeated or dominated choices that reached a
  read backend. Required value: zero.

Conditional semantic accuracy may diagnose protocol effects but never replaces
end-to-end scoring.

## End-to-end Diagnosis

`end_to_end_exact_success` requires every applicable condition:

1. protocol acceptance;
2. correct terminal;
3. correct root/domain/mechanism for a fault;
4. cited refs are resolvable;
5. semantic predicates satisfy an acceptable frozen evidence clause;
6. No-Incident or Abstain semantics are correct when applicable;
7. disposition and Runbook are correct when action is applicable.

Also report root exact match, domain accuracy, mechanism accuracy, Mechanism
Macro-F1, No-Incident false-positive/true-negative rates, incident false
negative rate, and abstention accuracy. `UNKNOWN` cannot score as a diagnosed
fault.

No-Incident denominators are fixed:

- false-positive rate = fault Diagnosis on `NO_INCIDENT` truth / all
  `NO_INCIDENT` cases;
- true-negative rate = correct `NO_INCIDENT` / all `NO_INCIDENT` cases;
- false-negative incident rate = `NO_INCIDENT` on fault truth / all fault cases.

Correct `NO_ACTION` safety is reported separately and cannot make an incorrect
Diagnosis count as a correct No-Incident result.

## Evidence metrics

- reference validity;
- predicate support validity;
- acceptable-clause satisfaction;
- unsupported citation count;
- source availability/status correctness.

Source-name inclusion alone is not semantic evidence validity.

## Tool-policy metrics

- source selection and target selection where applicable;
- weighted evidence cost;
- minimal-path regret;
- invalid/stale action selections;
- duplicate dispatches.

The evaluator minimal sufficient path is derived before output inspection from
frozen clauses, case-visible availability, catalog actions, and costs. It is not
manually selected after observing a run.

## Action metrics and applicability

Report CandidateSet accuracy given predicted Diagnosis, CandidateSet accuracy
given oracle Diagnosis, Action Selection accuracy given an oracle candidate
view, and end-to-end action accuracy.

Action accuracy uses only cases with an applicable expected disposition. An
`ABSTAIN` or `NEED_MORE_EVIDENCE` prediction cannot score action success via
null equality. Correct No-Incident `NO_ACTION` safety remains separate from
Diagnosis correctness.

## Stage-wise oracle metrics

The frozen decomposition reports each layer separately without replacing the
end-to-end result:

- tool policy against the frozen minimal sufficient path;
- final Diagnosis given the evidence actually selected by the controller;
- CandidateSet given the predicted Diagnosis;
- CandidateSet given an oracle Diagnosis;
- Action Selection given an oracle candidate view;
- end-to-end exact success and end-to-end action accuracy.

## Cost metrics

Separate bootstrap, dynamic-state, static prompt/schema, output, and total
tokens. Report tokens per protocol-accepted run, tokens per correct Diagnosis,
latency per correct Diagnosis, and weighted evidence cost per correct.

If an arm has zero correct Diagnoses, every per-correct cost is
`INFINITY / NOT_ESTIMABLE`; it is never omitted or replaced with zero.

## Memory metrics

Fixed-trajectory tests compare cumulative serialized bytes and estimated input
tokens without Provider calls or action changes. Visible development compares
the 2x2 controller x memory factorial. The held-out primary arms use the frozen
Salient configuration only.

The Salient Memory claim requires all of:

- fixed-trajectory cumulative input tokens <= Full x 0.75;
- fixed-trajectory serialized bytes <= Full x 0.70;
- visible-development end-to-end delta >= -0.05;
- unsupported citations not higher;
- first-pass protocol acceptance not lower by more than 0.05.

Otherwise the exact terminal is
`DTA_V22_NO_SALIENT_MEMORY_ADVANTAGE_SUPPORTED`.

## Required cross-tabs

Publish arm x family, arm x mechanism, arm x generalization slice,
arm x planning-required, and arm x protocol-failure-code tables. Report paired
Planner wins/losses, a paired bootstrap interval, and an exact paired test.
These do not license a production-general claim.

## Preregistered Planner gate

Only `PLANNER_LITE_SALIENT` versus `FLAT_CANONICAL_SALIENT` is eligible. Every
condition must pass:

- first-pass protocol acceptance >= 0.95 for both;
- post-correction protocol acceptance >= 0.98 for both;
- Planner exact success >= Flat + 0.10;
- paired Planner wins minus losses >= 3 cases;
- Planner weighted evidence cost per correct <= Flat x 1.05;
- Planner tokens per correct <= Flat x 1.15;
- Planner No-Incident false-positive rate <= Flat;
- invalid and duplicate dispatches = 0;
- unsafe proposals, arbitrary shell, and non-owned mutations = 0;
- truth isolation and scorer verification = PASS.

Passing yields `DTA_V22_PLANNER_LITE_ADVANTAGE_SUPPORTED`; any clean negative
result yields `DTA_V22_NO_PREREGISTERED_ADVANTAGE_SUPPORTED`. A negative result
is not a blocker and does not prevent the engineering terminal.

## Execution freeze

Visible development is at least 24 x 6 = 144 entries. Held-out is one sealed
24 x 4 = 96-entry execution, one unblinding, and no post-unblinding Prompt,
schema, scorer, threshold, or retry change. Protocol gates, query semantics,
truth isolation, scorer self-tests, fixed-trajectory memory checks, and case
validity must pass before the held-out seal is consumed.

Before held-out freeze, visible development includes at least two
preregistered Provider-stability measurements on identical visible inputs for
each Provider-backed arm. The report preserves both measurements and their
protocol, semantic, and exact-output agreement; it may not select the better
measurement or use repetition as retry-until-pass. Held-out remains one sealed
execution.
