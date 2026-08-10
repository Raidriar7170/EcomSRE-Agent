# Fresh External Holdout Plan for Metrics Arbitration v1

Status: `PLAN_ONLY_AWAITING_HUMAN_AUTHORIZATION`

This document is a preregistration plan only. This Goal does not authorize
dataset acquisition, download, inspection, Provider execution, or publication
of a new external-validation claim.

## Freshness and isolation

The holdout must be acquired after this plan is frozen and must not contain or
derive from RE2-OB, RE2-SS, or RE2-TT. Its source, license, collection window,
incident construction procedure, service vocabulary, fault taxonomy, and
deduplication method must be recorded before execution.

The builder/evaluator partition must keep case truth unavailable to the Agent,
Strong Single Provider, deterministic Metrics ranking, and M3 arbiter. The
final case count and all inclusion/exclusion criteria must be frozen before the
first model call. No development result may be used to remove, replace, or
relabel a holdout case.

## Primary paired endpoint

Each case uses one Strong Single model call and one deterministic M3 decision:

```text
same-run Strong Single Initial
              |
              v
deterministic root-only M3 Final
```

The primary endpoint is the paired Initial-to-Final change within that same
run. Report exact counts and denominators for:

- Initial and Final Root correctness;
- Initial and Final Pair correctness;
- Root Damage, Rescue, Net Rescue, and Damage Rate;
- Pair Damage, Rescue, and Net Rescue;
- KEEP and OVERRIDE counts, including correct and wrong overrides;
- completion, terminal failure, semantic-operation, retry, and token-accounting
  coverage.

Indicator remains the exact Initial indicator. Each completed case must have
one semantic model operation, zero Specialist calls, and zero Fusion-model
calls. The M3 rule, threshold, model lock, F0 ranking, prompt, retry policy,
and public projection must be byte-frozen before execution.

## Secondary robustness arm

An optional smaller arm may compare an independent Strong Single run with an
independent Metrics Arbitration run. It must be paired by case, alternate arm
order according to a preregistered balanced schedule, and use the same bounded
time window and Provider configuration. This arm is secondary robustness
evidence only; it cannot replace the same-run primary endpoint.

## Capacity and one-shot controls

- concurrency: exactly 1;
- minimum request spacing: the frozen five-second pacing contract;
- Retry-After: respected;
- transport retry: at most one allowlisted byte-identical retry;
- synthetic capacity preflight: required immediately before case admission;
- preflight Gate: valid response, known usage, zero HTTP 429, and no schema
  error;
- HTTP 429 abort rule: the first case-stage HTTP 429 stops admission of new
  cases, seals the current terminal, and requires human review before any
  separately authorized continuation;
- schema, privacy, schedule, identity, config, or implementation-lineage
  failure: immediate fail-closed stop;
- rerun or replacement of a completed/failed scheduled case: forbidden.

All case-level inputs, terminals, Provider sidecars, and evaluator truth remain
Git-external and create-once. Public artifacts are aggregate-only and must pass
the existing privacy guard.

## Authorization boundary

Before any future execution, a human must separately approve the dataset,
license, frozen sample size, acquisition manifest, primary and secondary
analysis plan, capacity budget, exact schedule hashes, abort policy, private
roots, and public claim language. Until then the only valid terminal marker is
`PLAN_ONLY_AWAITING_HUMAN_AUTHORIZATION`.
