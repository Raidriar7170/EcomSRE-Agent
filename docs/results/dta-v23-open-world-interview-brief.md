# DTA v2.3 Open-World Discovery — Interview Brief

Evaluation acceptance: `VALID / FINAL_REVIEW_PENDING`

## 30-second version

DTA v2.3 adds a separate, non-actionable open-world discovery lane beside the
unchanged v2.2 Diagnosis path. It filters an active ontology view, extracts
mechanism-independent anomalies, builds a Residual Evidence Graph, applies a
fail-closed Novelty Gate, performs at most three generic reads, and may emit a
typed `ProvisionalIncidentReportV23`. A human-review CLI can save an accepted
pattern only to `ShadowFaultRegistryV23` and produce a `RegistrationDraftV23`;
it never edits the formal ontology or authorizes a Runbook.

The valid 24-case, 48-run comparison completed once. It preserved all four
known controls, produced evidence-valid reports, and had zero action-authority
violations, but novelty recall was only 6/14. The frozen measured terminal is
therefore `DTA_V23_OPEN_WORLD_DISCOVERY_NOT_OBSERVED`, not a success claim.

## Personal contribution

- Defined `ActiveOntologyViewV23` without changing the frozen v2.2 Mechanism
  enum or support policy.
- Built Generic Anomalies, Residual Evidence Graph accounting, fail-closed
  Novelty Gate, bounded generic routing, and Negative Coverage.
- Bound every known terminal to a real `AdmittedDiagnosisV22` produced by the
  existing `admit_diagnosis_v22` path.
- Enforced the arm boundary structurally: closed results cannot contain Graph,
  Gate, Negative Coverage, generic reads, or provisional reports.
- Implemented typed provisional reports with evidence-ref and residual-anomaly
  validation and `action_authority = NONE`.
- Implemented the persisted five-decision Human Review CLI, Shadow Registry,
  registration drafts, and deterministic top-3 shadow matching.
- Constructed four data-level counterfactual TARGET/CONTROL pairs and a lazy
  evaluator-only truth boundary.
- Froze source, scorer, Prompt, data, and ontology bytes in manifest v2 and
  preserved invalid/protocol-blocked predecessors instead of overwriting them.

## Measured result

| Metric | Frozen value |
| --- | ---: |
| Novelty recall | 6/14 (`0.429`) |
| Root localization | 6/14 (`0.429`) |
| Broad-domain accuracy | 5/14 (`0.357`) |
| Evidence-ref validity | `1.000` |
| False-novel rate | 1/10 (`0.100`) |
| Registered-known closed/open | 4/4 → 4/4 |
| No-Incident closed/open | 2/3 → 2/3 |
| Provider calls / repairs / retries | 7 / 0 / 0 |
| Agent writes / Runbooks / Docker / new live faults | 0 / 0 / 0 / 0 |

## What failed and why

The stricter runtime conflict derivation protected against unsupported novelty
claims, but it over-triggered after one bounded read. It classified both hidden
configuration cases, both hidden dependency cases, and all four genuinely
unregistered concurrency cases as `CONFLICTING_EVIDENCE`. That reduced novelty
recall below the frozen mixed-result threshold. One insufficient control also
became false novelty.

This is a useful negative result: the implementation preserved known-world
accuracy and safety, yet the chosen evidence-to-conflict rule was not selective
enough to demonstrate discovery benefit on the fixed set.

## Claim boundary

The project supports a bounded human-registration workflow and valid negative
evaluation evidence. It does not support claims of autonomous ontology
learning, production remediation, general live-fault discovery, or measured
open-world effect. The v2.2.6 real CPU capture remains only the Increment-1
development demo and is not part of the final evaluation.

Automated review fixtures use `TEST_REVIEWER` and are simulations; no real
human acceptance is claimed.
