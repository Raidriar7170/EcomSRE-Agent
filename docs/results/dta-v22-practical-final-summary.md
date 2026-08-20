# DTA v2.2 Practical Completion Summary

## Delivered boundary

The practical successor recovered only the v2.2 controller core and completed
a runnable Flat Canonical versus Planner-Lite replay experiment through a
simple Provider adapter. It did not merge PR #60 or port the v3/v4/v5 Provider
gates, identity manifests, attempt artifacts, private evidence machinery, or
campaign verifiers.

The controller owns bootstrap, canonical Action Catalog filtering, runtime
Belief Ledger state, one repair, exact read/outcome binding, typed terminal
admission, and budgets. The Provider sees only static H/A/E aliases and compact
facts; Flat has no ledger view and Planner-Lite has a compact one.

## Measured result

- Smoke passed 8/8 after at most one repair.
- Development completed 8 cases × 2 arms with zero uncaught exceptions and
  zero Agent writes.
- The fixed evaluation executed exactly once for 12 cases × 2 arms.
- End-to-end exact completion was 1/12 for Flat and 3/12 for Planner-Lite.
- Mechanism Macro-F1 was 0.0000 versus 0.1333.
- Post-repair protocol success was 1.0000 for both; mean reads were 0.0 for
  both; transport retries, duplicate reads, uncaught exceptions, and Agent
  writes were all zero.
- Planner met the practical advantage threshold, but absolute quality was low
  and this is not a research or generalization claim.

## Safety and provenance truth

- Strict research PR #60 remained `BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE`, was
  not merged, and is preserved as negative evidence.
- The practical path ran no Docker, Runbook, Agent write, live remediation, or
  external mutation.
- Evaluation inputs were frozen by a simple filename/SHA-256 manifest. Nine
  fixed cases are public real replays and three are visibly marked synthetic or
  counterfactual-derived.
- Public legacy replays depend on three explicit practical admission
  compatibility clauses (configuration, memory, and bounded No-Incident); this
  successor does not claim equivalence to the frozen PR-C research policy.
- Two historical PR-B/PR-C CI verifiers received a narrow successor-ancestry
  repair because their exact-HEAD assumptions rejected legitimate later
  commits. No PR-D campaign verifier or new provenance gate was introduced.
- Provider results remain ignored local artifacts; committed reports contain
  measured aggregates and case outcomes, not credentials or private evidence.

## Completion boundary

At this report freeze, local implementation and evaluation are complete. The
repository terminal is minted only after PR #61 passes CI, receives the
independent read-only review, is marked Ready, and is squash-merged. PR #60 is
closed only after that practical CI succeeds.

Supporting artifacts:

- [development report](dta-v22-practical-development.md)
- [fixed evaluation](dta-v22-practical-evaluation.md)
- [error analysis](dta-v22-practical-error-analysis.md)
- [interview brief](dta-v22-practical-interview-brief.md)
