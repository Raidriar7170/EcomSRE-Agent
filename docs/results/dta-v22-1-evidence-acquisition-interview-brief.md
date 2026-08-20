# DTA v2.2.1 Evidence-Acquisition Interview Brief

## 30-second summary

The merged DTA v2.2 controller almost never read beyond bootstrap, so I added
one narrow runtime rule: reject the first ABSTAIN only while a bounded evidence
action and budget remain. The rule never chooses a tool, never pre-admits the
rejected decision, and allows only one extra policy-feedback call. After an
8-case gated check passed, I froze and ran one 12-case × 4-combination study.
Gate variants read more, but no final-study redirect fired and no read produced
a correct Diagnosis. The honest result is
`DTA_V22_1_NO_EVIDENCE_ACQUISITION_EFFECT_OBSERVED`, with zero Agent writes.

## What changed technically

- `LEGACY` preserves the merged behavior.
- `MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN` inspects only runtime state: proposed
  decision, admitted read count, executable Action Catalog, remaining evidence
  budget, and whether its one redirect was used.
- The policy returns only `ALLOW` or `PREMATURE_ABSTENTION`; it cannot select an
  action.
- A rejected ABSTAIN never enters the controller session or Belief Ledger.
- Policy feedback is distinct from semantic repair. A repeated premature
  ABSTAIN fails once as `PREMATURE_ABSTENTION_REPEATED` without a loop.
- Calls, token usage, latency, retries, read sources, and read outcomes are
  recorded even on bounded failure paths.

## Fixed study

| Metric | Flat Legacy | Flat Gate | Planner Legacy | Planner Gate |
| --- | ---: | ---: | ---: | ---: |
| Exact completion | 3/12 | 2/12 | 1/12 | 0/12 |
| Valid terminal | 11/12 | 9/12 | 4/12 | 6/12 |
| Cases with a read | 4/12 | 10/12 | 3/12 | 6/12 |
| Bootstrap-insufficient cases with a read | 2/8 | 8/8 | 3/8 | 4/8 |
| Policy redirects | 0 | 0 | 0 | 0 |
| Diagnosis after read | 0 | 0 | 0 | 0 |
| Mechanism Macro-F1 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Total tokens | 24,720 | 35,188 | 31,851 | 40,306 |
| Agent writes | 0 | 0 | 0 | 0 |

The four-position balanced schedule prevented a fixed earlier/later policy
bias. All 48 runs used the same case bytes, truth was loaded only after all
executions, and the full study executed exactly once.

## Why the effect terminal is negative

The rule required both gated arms to read at least half of the eight
bootstrap-insufficient cases, improve by at least 0.30 over matching Legacy,
reach at least 0.75 redirect compliance, keep repeated abstention at or below
0.25, and keep writes at zero.

Flat passed the read-rate thresholds, but Planner improved only 0.125. No
runtime redirect occurred, so redirect compliance was 0 rather than at least
0.75. The joint effect claim was therefore not allowed.

## What the negative result teaches

Evidence acquisition and evidence use are separate capabilities. The
policy-aware prompt made the model read more, but 17/25 read events were empty
and the remaining evidence was never converted into a Diagnosis. Additional
exploration also cost 10,468 tokens for Flat and 8,455 for Planner and reduced
combined control accuracy by 0.25 in both arms.

The result does not prove that forced exploration can never work. It shows that
this narrow gate, Provider, prompt, controller, and fixed small replay set did
not meet the preregistered effect or quality rules.

## Historical boundary

The previous merged v2.2 Practical study remains byte-preserved. It reported
Flat 1/12 and Planner 3/12 with zero reads. The v2.2.1 study is a new stochastic
2 × 2 run and does not rewrite or rescore that baseline. Only within-study
Legacy-versus-Gate comparisons support the new conclusions.

## Five likely interviewer questions

1. **Why not force a specific read?** The policy is an authority check, not a
   planner. Choosing a tool would confound exploration with an oracle action.
2. **Did the runtime gate work?** Focused tests and the development campaign
   exercised it, including same-input-hash and no-ledger-mutation checks. The
   final Provider read proactively, so no final redirect was triggered.
3. **Did more evidence improve diagnosis?** No. No read-bearing final run ended
   in Diagnosis, and all mechanism Macro-F1 values were zero.
4. **Was Planner better at using evidence?** No. Its diagnosis-after-read and
   Macro-F1 were equal to Flat Gate at zero, so no Planner interaction claim is
   allowed.
5. **Was safety weakened?** No. The policy has no write authority; Docker,
   Runbooks, transport failures, uncaught runner exceptions, and Agent writes
   were all zero in the final study.

## Engineering claim

The defensible accomplishment is a bounded, pre-admission runtime policy and a
truth-isolated, cost-accounted 2 × 2 evaluation that preserved a negative
result. It is not a Planner-superiority, RCA-quality, generalization, or
production-autonomy claim.
