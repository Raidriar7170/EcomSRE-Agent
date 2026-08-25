# DTA v2.3 Open-World Discovery — Interview Brief

## 30-second version

DTA v2.3 adds a separate open-world lane beside the unchanged v2.2
closed-world Diagnosis path. It removes one mechanism from an active ontology
view, extracts mechanism-independent anomalies, builds a residual evidence
graph, performs at most three generic discovery reads, and can emit a typed
`ProvisionalIncidentReportV23` with no action authority. A file/CLI workflow
lets a human review that report and create only a Shadow Registry entry and a
registration draft; it never edits the formal ontology automatically.

In one fixed 24-case, 48-run comparison executed exactly once, the open-world
lane detected and localized 10/14 novelty incidents while preserving registered
and No-Incident behavior. Evidence references were 100% valid and safety
counters were zero, but broad-domain accuracy was only 1/14 and one conflict
control became false novelty. The honest terminal is
`DTA_V23_OPEN_WORLD_DISCOVERY_MIXED_RESULT`.

## What I built

- `ActiveOntologyViewV23`, Generic Anomalies, Residual Evidence Graph, and a
  fail-closed Novelty Gate.
- A bounded discovery router with Negative Coverage, no equivalent repeat, at
  most three reads, two protocol repairs, and three exact transport retries.
- `ProvisionalIncidentReportV23`, always with `action_authority = NONE`, and
  type barriers that keep it out of Candidate Filter and Runbook paths.
- A persisted Human Review CLI supporting five decisions, plus
  `ShadowFaultRegistryV23`, `RegistrationDraftV23`, and deterministic top-3
  shadow matching.
- A frozen evaluator-only truth boundary and one exact 24-case x 2-arm study.

## Key engineering boundary

The v2.2 runner, support policy, Mechanism enum, Candidate Filter, and Runbook
authority remain unchanged. The v2.3 lane reuses stable evidence and known
Diagnosis admission components but owns new package types and terminals. A
provisional report is evidence for human registration work, not a diagnosis
that can authorize remediation.

Automated review examples use `TEST_REVIEWER` and are explicitly simulated.
No real human acceptance is claimed; a real reviewer name requires an explicit
CLI action.

## Measured result

| Metric | Frozen value |
| --- | ---: |
| Novelty recall | 10/14 (`0.714`) |
| Root localization | 10/14 (`0.714`) |
| Broad-domain accuracy | 1/14 (`0.071`) |
| Evidence-ref validity | `1.000` |
| False-novel rate | 1/10 (`0.100`) |
| Registered-known closed/open | 3/4 → 4/4 |
| No-Incident closed/open | 2/3 → 2/3 |
| Provider calls / repairs / retries | 13 / 2 / 0 |
| Agent writes / Runbooks / Docker / new live faults | 0 / 0 / 0 / 0 |

## What did not work

The lane frequently found the correct service without understanding the fault
domain. Four novelty cases remained fail-closed insufficient, nine of ten
generated reports missed the evaluator broad domain, and one conflicting
control produced false novelty. These failures are why the result is mixed and
why the project claims discovery assistance—not autonomous ontology learning
or remediation.

## Reproducibility and claim boundary

The fixed cases, truth, ontology views, manifest, Prompt, and source case-set
hashes were bound before execution. Truth loaded only after both arms completed
for each case. The study produced 48 runs and `execution_count = 1`; it was not
rerun after seeing the metrics. The v2.2.6 real CPU capture was used only for
the Increment-1 development demo and is absent from the fixed evaluation set.
