# Product v0.4 PR-D independent review

- Scope: PR-D worktree against `ded81cae5419d9bc950221c24f03cdee7bfa66df`.
- Reviewer: independent read-only Codex Reviewer; no tracked edits, Docker,
  Provider, live fault, or environment mutation.
- Final verdict: **PASS**.
- Must Fix: **0**.
- Should Fix: None.
- Nice to Have: None.
- Scope Creep Warning: None; changes stay inside the activated PR-D boundary.
- Claim Accuracy: **PASS**.

The review inspected authority separation, immediate pre-send state and lease
fences, duplicate dispatch, crash after mutation, receipt persistence, default
disabled execution, closed command surfaces, and two-window verification.

Initial findings covered missing lease fences, incomplete typed receipt evidence,
legacy upstream network bypass, exact timestamp equality, non-root volume
ownership, upstream flags-only read shape, and atomic configuration replacement.
Follow-up adversarial reproductions found persisted receipt dispatch substitution
and recovery-policy substitution. All were corrected. The final policy digest
is sealed into dispatch before the side effect and revalidated before mutation,
receipt/recovery reads and final evaluation; rehashed threshold changes fail.

A compatibility follow-up exercised the real Prometheus and OpenSearch connectors
through the proxy. Service discovery and bounded log search now work; arbitrary
DSL, origins, indexes and write operations remain denied. Independent follow-up
review: PASS / Must Fix 0 / Claim Accuracy PASS.

Independent final checks: **35 tests passed** across executor and gateway
regressions; offline development/history verifier PASS; `git diff --check` PASS.
Primary final Product checks: **268 passed / 15 warnings**, Ruff PASS, mypy PASS
for 239 source files. Factual metadata refresh is covered by the final claim
review and does not introduce further source changes.

Evidence Gaps: acceptance is limited to offline PR-D behavior. The real isolated-network denial
probe, actual Docker startup, fresh signed observer measurements and the one
bounded live Payment campaign remain PR-E gates. Exact-head GitHub CI, committed
content closure and squash merge remain integration requirements.

Recommended Next Step: complete committed-content closure and exact-head CI,
squash merge PR-D, then begin PR-E from the verified merged tree.
