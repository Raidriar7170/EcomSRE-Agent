# DTA v2.3.4.1 Registration-Assistance Error Analysis

## Frozen disposition

The single authorized 16-task by two-arm study completed all 32 runs exactly
once. Its frozen measured terminal is:

`DTA_V2341_REGISTRATION_ASSISTANCE_NOT_OBSERVED`

This is a valid negative measurement, not an engineering blocker and not a
reason to rerun the fixed study. The preserved predecessor remains
`BLOCKED_DTA_V234_PROVIDER`; the successor does not rewrite that history.

## What the successor repaired

Moving formal structure into the Runtime repaired the predecessor's protocol
failure mode:

- Provider schema validity: `14/14` (`1.000`);
- alias resolution and deterministic assembly: `14/14` (`1.000`);
- first-pass and post-repair parse rate: `1.000 / 1.000`;
- protocol repairs and transport retries: `0 / 0`;
- unknown aliases, catalog-coverage failures, and canonical-order failures:
  `0 / 0 / 0`;
- existing-format structural validity: `16/16` (`1.000`).

The study therefore shows that the six-field alias protocol is mechanically
reliable on this fixed set. It does not show that the Provider selected the
right semantics.

## Hidden-known reconstruction failures

All ten hidden-known treatments assembled a generic
`BOUNDED_FAULT_MECHANISM / bounded-fault-mechanism` identity instead of the
evaluator target. Mechanism identity accuracy was therefore `0/10`, although
the Runtime-owned broad domain was correct on `10/10`.

Predicate reuse precision was `0.900`, but recall was only `0.600`. Behavioral
clause equivalence passed on four tasks: the two configuration cases and the
two dependency-latency cases. It failed on both service-unavailable, both CPU,
and both memory cases, yielding `4/10` (`0.400`). The selected drafts also did
not meet the frozen confusable-negative coverage rule, yielding `0/10`.

This separates a protocol success from a semantic reconstruction success. The
Runtime could safely assemble and validate every selection, but a generic
mechanism concept and partial clause selection were insufficient to recover
the hidden canonical mechanism. All hidden-known drafts stayed non-promotable;
core-collision evidence was scored only in reconstruction context.

## Genuinely unregistered tasks

Three declarative-ready tasks (`rt-111` through `rt-113`) received the correct
mode, compiled successfully, and produced complete seven-file patch bundles.
`rt-114`, whose truth requires `ENGINEERING_REQUIRED`, was instead selected as
`DECLARATIVE_READY`; it compiled a structurally valid but semantically
misclassified draft. Consequently:

- correct new implementation modes: `3/4`;
- honest engineering-required selections: `0/1`;
- declarative compiler validity and patch-bundle completeness as frozen by the
  scorer: `0.750 / 0.750`.

This is the clearest new-registration semantic error: available catalog
options did not make the missing transport-ordering capability decisive enough
for the Provider to select the engineering-gap disposition.

## Controls and safety

The duplicate and insufficient-evidence controls were both non-promotable and
used zero Provider calls. Core-known regression, No-Incident regression,
extension overlap, remediation-registration violations, and action-authority
violations were all zero. Docker calls, live faults, Agent writes, and Runbook
executions were also zero.

## Why the measured terminal is negative

The positive threshold failed at least mechanism identity (`0.000 < 0.800`),
behavioral clause equivalence (`0.400 < 0.700`), and declarative compiler
validity (`0.750 < 0.850`). The mixed threshold also failed mechanism identity
(`0.000 < 0.600`) and behavioral clause equivalence (`0.400 < 0.500`). The
predeclared scorer therefore minted
`DTA_V2341_REGISTRATION_ASSISTANCE_NOT_OBSERVED`.

No threshold, task byte, Provider Prompt, catalog generator, alias schema,
assembler, validator, compiler, or scorer was changed after execution began.
The negative result was not optimized or rerun.

## Claim boundary

The supported engineering claim is narrow: Runtime-owned catalogs plus a
six-field alias response made the formal-draft protocol parseable,
deterministic, structurally valid, and safe on this one fixed study. The study
does not support registration-assistance quality effect, hidden-mechanism
reconstruction quality, statistical significance, generalization, production
autonomy, remediation authority, or training readiness.
