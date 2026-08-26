# DTA v2.3.3 Independent Final Review

- Reviewer identity: OpenAI Codex, independent read-only reviewer
- Reviewed exact head: `6e6dc45435552e7c1f2a812380bb7d591f1e1e97`
- Base commit: `447e7a8ed4c8b9d592c16d181f5709bdfdc3d4cb`
- PR: `#71` — Draft
- Remote head: `6e6dc45435552e7c1f2a812380bb7d591f1e1e97`
- Agent mainline CI run: `32932970599` — success
- RCAEval verify CI run: `32932970618` — success
- Review mode: independent, read-only, post-execution exact-head review

## Findings

1. PASS — Repository identity and cleanliness are exact. Local HEAD and `origin/codex/dta-v233-domain-witness-guard` both resolve to `6e6dc45435552e7c1f2a812380bb7d591f1e1e97`; the merge base is the required `447e7a8ed4c8b9d592c16d181f5709bdfdc3d4cb`; the worktree is clean.

2. PASS — Historical v2.3, v2.3.1, and v2.3.2 evidence remains byte-identical. All 37 active manifest bindings matched. The six direct v2.3.3 history bindings and all 18 transitive v2.3.2 history-ledger bindings matched their recorded sizes and SHA-256 values. No protected historical path differs from the required base.

3. PASS — The new fixed evaluation surface is valid and frozen. It contains 28 opaque cases with 28 unique source-byte hashes and no overlap with the v2.3.2 case hashes. Admission records 16/16 novelty, 4/4 registered-known, 3/3 No-Incident, 4/4 irreconcilable, and 1/1 insufficient-evidence cases passing under `DTA_V233_EVALUATION_DATA_PASS`.

4. PASS — The deterministic preflight is complete. It records 28 cases, three arms, and 84 paths under `DTA_V233_RUNTIME_PREFLIGHT_PASS`, with zero runtime exceptions, unmapped anomaly kinds, missing treatment projections, mechanical-field drift, witness failures, premature truth access, authority violations, Agent writes, or Runbook executions.

5. PASS — Provider smoke and pre-execution review gates are intact. The 12-case smoke completed once as `DTA_V233_PROVIDER_SMOKE_PASS` after exactly two bounded real fixes, with zero parse failures, protocol failures, root/domain/evidence drift, irreconcilable Provider calls, or authority violations. The frozen pre-execution review records `Must Fix: 0` and `Claim Accuracy: PASS`.

6. PASS — The write-once result chain is internally consistent. The sentinel is `COMPLETE` with execution count `1`; the partial journal contains exactly 28 comparisons and 84 unique arm runs; every case contains all three policies. The journal comparisons equal the final artifact comparisons. All run, comparison, and artifact semantic hashes recomputed successfully, and the sentinel’s JSON, Markdown, terminal, and artifact bindings match the result files.

7. PASS — Evaluator truth is loaded only after all three arms for each case complete. `_build_comparison` completes the counterbalanced three-arm list before invoking `LazyTruthStoreV233`; the truth gate requires the exact canonical policy set and completed run digests. The final artifact contains 28 truth loads for 28 completed comparisons.

8. PASS — The frozen scorer yields exactly `DTA_V233_DOMAIN_AND_GUARD_MIXED_RESULT`. Every mixed-result predicate passes. Positive effect correctly fails because combined broad-domain accuracy is `0.625`, below the frozen `0.650` absolute gate. No scorer or threshold relaxation was used.

9. PASS — P0-A changed measurably under the same new fixed comparison. The exact v2.3.2 baseline produced root localization `15/16` and broad-domain accuracy `2/16`; both v2.3.3 arms produced root localization `16/16` and broad-domain accuracy `10/16`, with top-two domain recall `14/16`. This is a real fixed-set improvement, while the six remaining `UNKNOWN` domains and the missed positive threshold remain disclosed.

10. PASS — P0-B changed measurably and is isolated by the third arm. Irreconcilable-control accuracy is `0/4` in both the v2.3.2 baseline and domain-only arm, then `4/4` in the combined witness-guard arm. Every combined control terminates `CONFLICTING_EVIDENCE / IRRECONCILABLE`, carries a typed strong blocking witness, uses one shared-budget guard-directed read, generates no report, and makes zero Provider calls. No novelty case is blocked.

11. PASS — The two baseline protocol failures remain frozen. `vx-316` and `vx-324` retain `PROTOCOL_FAILED` after two repairs each. They remain in the 84-run denominator and were not removed, repaired after measurement, or rerun. The baseline totals remain 30 Provider calls, 10 repairs, two protocol failures, and zero transport retries.

12. PASS — Runtime ownership is preserved. The Provider response remains limited to seven narrative fields. Across all 32 runtime-built v2.3.3 reports, report root and broad domain equal the runtime-selected values, evidence references remain within runtime memory, guard disposition is `OPEN`, and `action_authority` is `NONE`. Provider-domain drift is zero.

13. PASS — Safety and authority boundaries remain zero. The final artifact records zero runtime exceptions, action-authority violations, Agent writes, Runbook executions, Docker calls, new live faults, and transport retries. Irreconcilable states cannot be overridden by Provider synthesis.

14. PASS — Post-execution protected bytes remain frozen. Algorithm, evaluation data, Provider prompt, scorer, and thresholds still match the pre-execution manifest byte-for-byte. The historical compatibility-test change remains fail-closed: it expects the older successor preflight to reject the newer v2.3.3 CLI surface rather than weakening the older freeze contract. Post-result documentation does not alter measured behavior.

15. PASS — README, error analysis, and interview brief accurately preserve the claim boundary. They report mixed rather than positive effect, disclose the two baseline protocol failures, six remaining ambiguous domains, minimal narrative-synthesis limitation, and lack of statistical-significance, production-autonomy, remediation-safety, or live-fault claims.

16. PASS — Verification is sufficient for the reviewed exact head. Submitted local evidence records `5015 passed, 6 skipped`, 33 focused v2.3.3 tests passing, Ruff passing, scoped mypy passing, and the history verifier passing. Exact-head Agent mainline run `32932970599` independently completed `5015 passed, 6 skipped`, Ruff, and mypy over 465 source files. Exact-head RCAEval run `32932970618` completed 571 tests, Ruff, and mypy over 150 source files. `git diff --check` is clean.

17. PASS — PR #71 is technically merge-ready at the reviewed head: GitHub reports `MERGEABLE / CLEAN`, and both exact-head checks succeeded. The PR correctly remains Draft and the engineering terminal remains unminted because squash merge has not occurred. Landing this final-review document is a documentation-only successor change; if it creates a new PR head, required checks must pass on that exact head before marking Ready and squash merging.

Must Fix:
0

Claim Accuracy:
PASS
