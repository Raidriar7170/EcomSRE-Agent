# Final learning closure — BLOCKED_SAFETY

[Active Goal](../../../goals/EcomSRE_v0.5_Final_Learning_Closure_Goal.md), same Draft
[PR #104](https://github.com/Raidriar7170/EcomSRE-Agent/pull/104), base `29c195762b7bf6f4951ca778b6a0a034f2be94fd`.
This is an interrupted Phase A audit with two fixture-tested interface slices,
**not engineering completion or learning acceptance**.

Fresh read-only precheck found a new non-owned container and changed default bridge
membership against the retained post-cleanup inventory. Independent review saw
successive short-lived `hermes-asi-executor:v1` containers without project ownership
labels. Observations are timestamp-bound; no particular container ID is asserted
still present. Under Goal §0.6 no new baseline, campaign, Provider request, or live
episode was started. No resource was stopped or removed. The external task must
finish and resource continuity must be rechecked; this package does not authorize
accepting drift or taking over that task.

[precheck.json](precheck.json) freshly reconstructs all five old candidate outcomes
from retained SQLite/CAS and the existing evaluator: e04 TRUE, all others FALSE;
original Development **1/2**, all seen **1/5**. Historical e04 resource dependency
remains `UNKNOWN / NOT_COLLECTED`, never backfilled. All old candidates, failures,
event roles, CHECKED records, split and ledger remain unchanged.

New consumption: **0 requests / USD 0 / 0 live episodes / 0 semantic attempts**.
Cumulative: **57 requests / USD 0.918501 committed / 5 episodes**. Four unknown-usage
calls retain USD 0.095449; invoice unknown. Effective unused caps remain
40 requests / USD 8 / 7 episodes / 6 semantic attempts, subject to fresh cumulative
accounting at resumption. No fresh price verification or paid dispatch occurred.

Level A loop: **NOT_RUN**. Level B acceptance: **false / NOT_RUN**. No new candidate,
selection lock, freeze, independent batch, promotion, recurrence, or revocation.
The historical `NO_VALIDATED_LLM_KNOWLEDGE` result is preserved; this attempted
continuation is `ECOMSRE_PRODUCT_V050_BLOCKED_SAFETY`.

## Fixed next-run plan and outstanding interfaces

Primary objective is Level B, subject to same-semantics data feasibility established
before proposer use. N1 target positive, N2 healthy, N3 confusable (existing Payment
control; similar service degradation, not a claim of matching queue-lag mechanism)
are development; N4 target, N5 healthy, N6 confusable/Core are independent holdout;
N7 is post-promotion recurrence. No N4–N7 slot may fund development retries. These
are planned roles, not created episodes or an installed split successor.

Level A gate remains original e04/e05 2/2 and all original events at least 4/5,
with complete negative controls FALSE. Level B forward cohort includes e05/N1 and
all old target episodes with the same complete dependency (currently e01); minimum
75% coverage, e05/N1 both TRUE, original e04/e05 base conditions TRUE, all complete
negative controls FALSE, expression numerically consequential. Report e04 UNKNOWN
and all original five separately. No actual candidate has been tested under these
new gates. Goal §C4 remains normative; this summary changes no gate.

Implemented offline only (not installed into the live ledger):

- Append-only split successor preserving original bytes, unused e06/e07, roles and
  global exposure; rejects old incident relabeling and post-freeze extension.
  Historical readers still select only the original manifest's five events.
- Derived controls use actual evaluator output and fresh memory/coverage/references.
  Target evidence withholding checks cross-service non-borrowing only, not complete
  target attribution. It requires retained successful, nontruncated other-service
  evidence from a required source; otherwise the control is NOT_AVAILABLE and the
  gate refuses. Source failures preserve typed authoritative observations. UNKNOWN
  remains raw UNKNOWN, and original negative UNKNOWN refuses new-protocol validation.
  Independent originals and derived controls have separate counts.

Before any new data consumption, still implement and fixture-test:

- Explicit same-environment capability successor, only `verified_at` excluded from
  structural comparison, exact old/new objects retained; separate birth-bound
  deployment identity and actual query/resource-limit equivalence. Shared check in
  development, Shadow and matcher; no broad capability bypass.
- Candidate selection lock before N4–N6 capture, including source/protocol/evaluator,
  cohort, transforms, deployment mapping and thresholds. Lock forbids proposer.
- Fixture-only full admission → development → lock → freeze → evaluate → test
  promotion → normal API/Worker dependency acquisition → revoke. The existing
  isolated fixture insertion test does not establish this full governance chain.
- New proposal wire feedback with full old semantics, every FALSE/UNKNOWN and
  negative control; no supplied winning clause. Stable new keys, parent relations,
  six semantic slots and original ledger sub-budget guards.

After mechanical checks, fresh ownership continuity and same-environment binding,
collect development data, stop selection at the first qualifying primary candidate,
lock, collect N4–N6, freeze all raw inputs, run one unchanged Shadow gate. Require
positive recall ≥0.75, FPR ≤0.10, zero healthy/Core overlap, target consistency ≥0.80,
all source failures closed, reference validity/reachability 1.0 and zero authority
violations. Only then normal governance may promote to the enrolled isolated test
registry. N7 uses normal API/Worker with Provider disabled and ledger deltas; revoke
and explicitly replay N7 afterward. None of these future steps is claimed complete.

## Verification and review

Executed read-only commands from this worktree:

```sh
PYTHONPATH=src:. .venv/bin/python -m scripts.product_v050.final_closure_precheck
PYTHONPATH=src:. .venv/bin/python -m scripts.ci.verify_product_v050_docker_stability
```

The first prints current safe projections and never accepts a baseline. Its retained
snapshot is time-specific. The historical verifier passed all three predecessor
packages, with old feasibility source hashes checked against retained baseline
`29c195762b7bf6f4951ca778b6a0a034f2be94fd`; it does not validate this new learning loop. Independent read-only reviewer
recomputed budget and old CAS outcomes and confirmed ongoing non-owned drift.
Independent review of the offline slices reports Must Fix 0. The complete v0.5
fixture suite passes 183 tests; these tests are not live learning evidence. Candidate
selection lock, capability successor and the full mechanical chain remain incomplete.
The current read-only follow-up snapshot is [offline-precheck.json](offline-precheck.json);
its 2026-09-19 04:04 UTC observation still shows one new non-owned container and
changed default bridge membership. The initial precheck is preserved separately.
