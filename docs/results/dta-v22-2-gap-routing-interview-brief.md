# DTA v2.2.2 Gap-Aware Routing Interview Brief

## 30-second summary

The earlier controller could execute reads but did not reliably choose evidence
that completed a diagnosis clause. I first proved every development incident
had a feasible replay path, then added replay source masking, one shared support
policy, a Predicate Gap Graph, deterministic gap-aware top-4 routing, negative
coverage for empty reads, an explicit Post-Read Delta, runtime-admissible
terminal aliases, and a short A/T Provider schema. I froze a new 16-case set and
ran one 64-run 2×2 study. Planner Gap improved exact completion from 6/16 to
8/16 and met the fixed quality-effect rule, with zero Agent writes. Flat Gap was
stronger at 10/16, Planner interaction was false, and CPU/memory cases exposed
a premature No-Incident admission flaw, so the claim stays narrow.

## What changed technically

- Replay capabilities expose only whether a source was captured, never future
  record counts or truth.
- Routing and terminal admission use one effective support-policy object,
  preserving the frozen v2.2 clauses plus two declared practical clauses.
- The Predicate Gap Graph recomputes missing DNF requirements from current
  runtime-owned memory. Planner may contribute prior focus, not truth.
- Gap mode ranks actions deterministically by shortest clauses completable,
  distinct missing predicates observable, active hypotheses reduced, prior
  empty penalty, cost, and canonical action ID, then exposes top 4.
- Empty reads become Negative Coverage and never contradict a hypothesis.
- Every read produces a Post-Read Delta with outcome class, newly available T
  aliases, gap change, remaining gaps, and evidence aliases.
- The Provider returns only one `selection` alias and one `focus` alias. A is a
  read plus H focus; T is a runtime-admissible terminal plus `NONE`.
- Each turn permits at most two protocol repairs. A retryable transport failure
  resends the exact request at most three times with 5/15/30-second backoff.

## Fixed-study scorecard

| Metric | Flat Broad | Flat Gap | Planner Broad | Planner Gap |
| --- | ---: | ---: | ---: | ---: |
| Exact completion | 6/16 | 10/16 | 6/16 | 8/16 |
| Incident diagnosis | 0/10 | 4/10 | 0/10 | 2/10 |
| Mechanism Macro-F1 | 0.0000 | 0.4000 | 0.0000 | 0.2667 |
| Diagnosis after read | 0.0000 | 0.4444 | 0.0000 | 0.1538 |
| Empty-read rate | 1.0000 | 0.8000 | 1.0000 | 0.9412 |
| No-Incident + abstention accuracy | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| Total tokens | 50,104 | 44,810 | 52,828 | 66,723 |
| Agent writes | 0 | 0 | 0 | 0 |

The measured result terminal is
`DTA_V22_2_GAP_ROUTING_QUALITY_EFFECT_OBSERVED`. Planner Gap satisfied the
rule by gaining exactly two exact cases, improving Macro-F1 by 0.2667 and
diagnosis-after-read by 0.1538, with no control-accuracy drop.

## What I would say—and would not say

I can say:

- the router's top-4 surface contained a shortest-path action for 10/10
  incident turn-zero states and 64/64 feasible post-first-read states;
- the one fixed Provider study showed a contemporaneous Gap quality effect;
- all 64 runs are represented, truth isolation held, and the safety boundary
  remained read-only;
- the final study revealed a concrete admission flaw instead of hiding it.

I cannot say:

- that Planner was superior—Flat Gap had the best result and no Planner
  interaction was established;
- that top-4 recall means the model used the useful action;
- that 10/16 or 8/16 exact completion is strong general RCA quality;
- that synthetic/derived replay proves live, Docker, remediation, or production
  behavior;
- that the premature No-Incident issue was fixed in this study.

## Five likely interviewer questions

1. **Why prove feasibility before changing the router?** Without a one- or
   two-read admissible path, an abstention could be correct and routing would be
   impossible to evaluate. The audit found zero infeasible incident cases.
2. **How did you avoid leaking evaluator truth?** Case bytes and source
   availability were loaded before execution; truth and oracle utility were
   loaded only after all four case-local runs. Provider inputs contained only
   runtime memory, hypotheses, A/T/H/E aliases, gaps, and Post-Read Delta.
3. **Why did offline top-4 recall not guarantee diagnosis?** Recall proves
   exposure, not selection. The model often chose empty Changes, Logs, or
   Resources actions even when Trace was useful.
4. **What caused the premature No-Incident failures?** Healthy runtime plus
   request/error metric coverage opened a No-Incident T before relevant
   captured Resources evidence was read. The prompt then preferred that valid
   T. This is an admission-versus-unread-gap mismatch.
5. **Why keep the positive terminal despite that flaw?** The terminal is a
   preregistered within-study comparison, not an absolute-quality endorsement.
   Preserving the rule and reporting the counterexample is more credible than
   changing thresholds or rerunning.

## Engineering claim

The defensible contribution is a truth-isolated, gap-aware post-read pipeline
with deterministic routing, bounded protocol recovery, exact execution
accounting, and one preserved 2×2 replay result. It demonstrates a narrow Gap
quality effect and exposes a specific No-Incident admission defect. It is not a
Planner-superiority, generalization, live-SRE, remediation, or production claim.
