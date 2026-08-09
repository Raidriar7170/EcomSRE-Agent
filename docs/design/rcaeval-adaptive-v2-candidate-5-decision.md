# Adaptive v2 Candidate-5 decision

Status: `CONSUMED / TERMINAL_TUNE_GATE_NOT_PASSED`

Decision code: `CASE_E_SPECIALIST_GENERATION_FAILURE`

## Evidence

Candidate-4 established that the Gate, rather than missing the observed Initial
errors, escalated all eight completed Initial-wrong cases. Across the completed
run it produced 44 Specialist hypotheses, but none was a correct alternative
for an Initial-wrong case. Candidate-4 therefore produced no same-run Root
rescue.

The zero-Provider post-hoc analysis of those eight cases found:

- Candidate-4 Initial Root wrong: 8
- Gate escalated Initial-wrong: 8/8
- Candidate-4 Specialist hypotheses: 44
- Correct Specialist alternatives for Initial-wrong: 0
- True Root Metrics Coverage@1 / @2 / @3 / @6: 6/8 / 8/8 / 8/8 / 8/8
- Deterministic Metrics alternative matched the True Root: 7/8 (87.5%)
- Truth-matching alternative also visible in bounded Logs: 0/8
- Initial and alternative both visible in bounded Logs: 0/8

The 7/8 alternative coverage satisfies the minimum technical-opportunity
condition. The zero bounded-Logs visibility counts make this a high-risk
hypothesis and do not predict Candidate-5 TUNE or Regression Gate passage.

Authoritative aggregate:
`docs/analysis/rcaeval-adaptive-v2-candidate4-metrics-alternative-analysis.json`.
The case-level table remains private and outside Git.

## Decision

Authorize exactly one final bounded algorithm candidate with this single
primary change:

1. Runtime deterministically selects the highest-ranked Metrics service that
   differs from the Initial service.
2. The Logs operation compares only `INITIAL` and `ALTERNATIVE` using bounded
   Logs evidence and returns a typed pairwise verification.
3. Deterministic Fusion consumes that verification. It makes no Provider call
   and does not use the old free-hypothesis score threshold on the pairwise
   Logs path.

Candidate-4 code and historical artifacts remain readable and unchanged. The
Candidate-5 `VERIFY_LOGS` path replaces, rather than supplements, the old
free-generation Logs specialist. Trace-only routing keeps the Candidate-4
Trace specialist and deterministic Fusion behavior. On `VERIFY_BOTH`, an
override requires the Logs pairwise verifier and the existing Trace specialist
to support the same Metrics alternative.

If no non-Initial Metrics alternative exists, Runtime keeps Initial with
`NO_METRICS_ALTERNATIVE` and does not call the Logs verifier. This is a normal
completed outcome, not a terminal failure. Ground Truth is never an input to
alternative selection or pairwise verification.

## Frozen invariants

Candidate-5 does not modify:

- `direct_confidence_threshold = 0.9`
- `low_confidence_threshold = 0.75`
- `metrics_conflict_rank = 3`
- `metrics_margin_threshold = 0.75`
- `risk_signal_threshold = 1`
- Trace policy: `LATENCY_OR_SOCKET_WITH_PROPAGATION_CONFLICT_ONLY`
- Indicator `deterministic_override_margin = 0.95`
- model, request pacing, retry policy, TUNE/Regression splits, or acceptance
  gates

The TUNE Gate remains the frozen execution, accuracy, same-run damage/rescue,
routing, and cost contract. A 120-case Regression is authorized only if the
one Candidate-5 60-case TUNE passes that Gate. Regression remains one-shot and
may not be followed by tuning.

## Candidate boundary

Candidate-5 is the final bounded algorithm candidate. No Candidate-6 is
authorized. If Candidate-5 does not pass TUNE, stop without Regression. If it
passes TUNE but not Regression, stop without another Regression or algorithm
revision.

Before any Candidate-5 Provider call, the analysis aggregate, this Decision
Record, and the Candidate-5 runtime and tests must be committed. Provider
capacity recovery does not weaken that commit-before-Provider boundary.

## Execution disposition

The authorized TUNE executed once from the committed Candidate-5 runtime. It
terminalized and completed 60/60 records with no HTTP 429, Provider failure, or
schema/privacy/schedule failure. The pairwise verifier completed 23 calls, but
deterministic Fusion authorized no override; same-run Root and Pair Damage /
Rescue / Net were both 0 / 0 / 0. Final Root was 45/60 and Final Pair was 23/60.

Candidate-5 therefore did not pass the frozen TUNE Gate. Per this decision,
Regression did not run and no Candidate-6 is authorized. Terminal state:
`ADAPTIVE_V2_TUNE_GATE_NOT_PASSED_AFTER_CANDIDATE5`.
