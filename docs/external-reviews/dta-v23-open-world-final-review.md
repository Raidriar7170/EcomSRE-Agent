# DTA v2.3 Open-World Discovery — Independent Final Review

- Reviewed head: `19a60852ba9182a1cb83fd1797ef205dd3ea848a`
- Exact base: `f17688f4c313b1483bfb7c56675c429605faf489`
- Must Fix: `3`
- Claim Accuracy: `FAIL`
- Repository disposition: `Draft / REVIEW_REQUIRED`
- Blocker: `BLOCKED_DTA_V23_ONTOLOGY_ISOLATION`

## Must Fix 1 — Invalid closed-arm contract

The common pre-arm context builds Generic Anomalies, a Residual Evidence Graph,
and a Novelty Gate decision. `run_closed_world_arm_v23` consumes that v2.3
state. Known terminals are projected from `KnownTerminalCandidateV23` rather
than the existing admitted v2.2 Diagnosis path. The Goal assigned graph, gate,
and generic discovery only to `OPEN_WORLD_DISCOVERY` and required shared known
Diagnosis admission, so the frozen arms are not the approved comparison.

## Must Fix 2 — Novelty and conflict rules differ

The implemented single-source strong-anomaly path requires both healthy
runtime and a contrastive target; the Goal requires either condition. Runtime
code also does not derive and pass conflicting evidence into the gate, so the
approved conflict terminal is not reachable from actual development/evaluation
state.

## Must Fix 3 — Counterfactual pairs are not proven

Tests prove that four pair labels each occur twice, but do not prove equivalent
observations, equal candidate cardinality, or an exchanged target. Only two
pairs are clear two-service mirrors; the other two labels do not establish the
required service-target counterfactual construction.

## Passing evidence

- Four vertical increments are present and the working tree was clean at the
  reviewed head.
- The v2.2 Diagnosis/controller/filter implementation was not modified. The
  v2.2.6 CI verifier retains frozen result, Prompt, scorer, terminalizer,
  capture, and terminal checks while recognizing the exact squash commit.
- The immutable artifact is internally consistent: 24 pairs, 48 unique runs,
  `execution_count = 1`, matching partial journal, recomputed metrics, and
  `DTA_V23_OPEN_WORLD_DISCOVERY_MIXED_RESULT` under its implemented scorer.
- Human Review examples use `TEST_REVIEWER` with `simulation = true`; Shadow
  Registry, registration draft, deterministic top-3 matching, and `NONE`
  action authority are present.
- Fresh verification at the reviewed head: v2.3 `45 passed`; key v2.2/v2.2.6
  `42 passed`; full repository `4915 passed, 6 skipped`; Ruff PASS; mypy PASS;
  `git diff --check` PASS; GitHub CI subsequently passed both required checks.

## Conclusion

The required final gate is not met: `Must Fix 0 / Claim Accuracy PASS` cannot
be issued. The measured bytes must be preserved, but they cannot support the
Goal-defined comparison or `DTA_V23_OPEN_WORLD_DISCOVERY_MVP_COMPLETE`.
