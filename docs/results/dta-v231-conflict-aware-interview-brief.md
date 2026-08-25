# DTA v2.3.1 conflict-aware discovery — interview brief

## Thirty-second explanation

I extended a replay-only open-world diagnosis lane so that multiple plausible
interpretations are represented explicitly rather than collapsed into a hard
conflict. The implementation clusters evidence, distinguishes coherent,
resolvable, and irreconcilable conflict, routes at most one discriminating read
inside the existing three-read budget, and emits non-actionable competing
hypotheses compatible with Human Review and the Shadow Registry. The frozen
v2.3 negative result and the v2.2 closed-world Diagnosis path were unchanged.

## What the one-shot evaluation showed

- Treatment novelty recall improved from `0.429` to `0.643`, while
  conflict-prone recall reached only `0.375`.
- Three competing reports were emitted with `1.000` evidence-reference and
  competing-hypothesis evidence validity.
- Root localization was `0.571`, broad-domain accuracy was `0.214`, and there
  were zero action-authority violations.
- The frozen measured terminal was
  `DTA_V231_CONFLICT_AWARE_DISCOVERY_NOT_OBSERVED`.

## The important failure and how I handled it

The run revealed that four cases intended to be genuinely unregistered
accidentally satisfied the existing dependency-latency Diagnosis clause, while
three intended irreconcilable controls were intercepted by the known terminal.
Because the protocol allowed exactly one fixed execution, I did not repair the
data or rerun for a better result. I preserved the artifact and marked the
engineering status `BLOCKED_DTA_V231_EVALUATION_DATA`.

That distinction is the core evidence discipline: the measured terminal is a
real one-shot observation, but the dataset contract failure prevents a clean
causal claim and prevents minting the engineering completion terminal.

## Personal contribution boundary

I implemented the typed conflict model, hypothesis-pair-aware discriminating
router, conflict-aware novelty gate, bounded Provider schema/repair handling,
review and shadow compatibility, independent fixed-set tooling, write-once
truth boundary, expanded scorer, and safety/telemetry closure. The system does
not authorize remediation, edit the formal ontology, execute a Runbook, or
claim production autonomy.
