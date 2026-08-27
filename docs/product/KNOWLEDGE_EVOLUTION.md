# Environment-Local Knowledge Evolution

Knowledge evolution starts only after a diagnosis truthfully reaches the
Open-World lane. It cannot execute actions and cannot change the frozen core
ontology.

## Pipeline

1. Build a deterministic incident fingerprint from Runtime-owned observations.
2. Cluster similar fingerprints only inside the same environment.
3. Wait for the minimum occurrence and disjoint-evidence boundaries.
4. Record an explicit human family decision.
5. Build a multi-positive Predicate Matrix with `PRESENT`,
   `ABSENT_WITH_COMPLETE_COVERAGE`, `UNKNOWN`, and `SOURCE_FAILED`.
6. Select environment-specific known-core confusable negatives.
7. Mine bounded DNF candidate clauses with conjunction sizes 1, 2, and 3 and a
   deterministic beam-width cap of 20.
8. Reject core overlap, active-extension overlap, excessive complexity,
   incomplete positive coverage, and No-Incident false positives.
9. Allow an LLM to suggest only a display label and explanation.
10. Run the shadow evaluator against positives, confusable negatives,
    No-Incident controls, and disjoint recurrence evidence.
11. Require an explicit human promotion; revocation remains separate.

One incident cannot produce a promotion-critical rule. Missing observations
remain `UNKNOWN`, source failures remain `SOURCE_FAILED`, and neither state
counts as absence. Only `ABSENT_WITH_COMPLETE_COVERAGE` is negative evidence.
A family with too few positives or negatives stays `NEEDS_MORE_DATA` rather
than weakening the gate.

## Runtime use

Promoted registrations are adapted into a typed, versioned environment
extension registry. Diagnosis ordering remains:

```text
Core Known -> Environment Extension -> No-Incident -> Open-World
```

A disjoint recurrence that satisfies one active extension returns a typed
`EXTENSION_KNOWN` result without Open-World Provider synthesis. Multiple
matching extensions become a conflict. Every result retains
`action_authority = NONE`; no Candidate Filter or Runbook path is exposed.

## Human semantics

Human review is a real product boundary, not a model self-approval. Repository
tests and demos use `TEST_REVIEWER` and label all such decisions
`SIMULATED HUMAN REVIEW`.
