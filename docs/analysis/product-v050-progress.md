# Product v0.5 progress

Active Goal: [contract](../goals/EcomSRE_v0.5_Codex_Goal.md).

## Phase A — in progress

- Starting main/HEAD: 550a564d29954e6f3c2395790294e23800231ac4; tree:
  43b61e29c036b4b2062d8cbb9c10fe36d7bd54db. Remote refreshed successfully
  with a command-local empty Git proxy after configured localhost:1097 failed.
- Branch: codex/product-v050-llm-investigation-knowledge; isolated worktree.
  Original Phase 3 checkout has unrelated dirty docs and untracked files, untouched.
- Upstream gitlink: 1755859a9de82c2e5e225be68abc401a5ebf2b4f.
  Runtime image identity not inspected yet; no Docker mutation.
- No OpenSpec configuration exists; implementation follows the active Goal.
- Provider environment variables absent in current process. Credential availability
  and dated pricing remain unresolved; no paid dispatch authorized by configuration.
- Provider requests: 0/200; spent USD 0 (no requests); live episodes: 0/12.
- Next: baseline Product tests, capability mapping, durable investigation protocol.

## Authority reconciliation and scopes

Global invariants: owned local resources, read-only fixed upstream, separate
observer/evaluator data, no arbitrary model shell, no historical evidence edits.
Phase 0 smoke allowance and DTA-specific output protocols remain historical-only.
DEC-064 scopes the newly authorized Product investigation/knowledge path;
DEC-062 ambiguous-root admission remains unchanged. Previous v0.4/v0.4.1 Goals
are historical context, not renewed write or merge authority.

Read scope: repository source/config/tests/docs/CI and project-owned runtime
metadata; no evaluator truth in model inputs. Write scope: src/ecomsre/product,
tests/product_v050, scripts/product_v050, scripts/ci/verify_product_v050.py,
config/product-v050, docs/goals/EcomSRE_v0.5_Codex_Goal.md,
docs/analysis/product-v050*, docs/results/product-v050, docs/product,
docs/interview/PROJECT_PITCH.md, docs/DECISIONS.md, AGENTS.md, README.md,
.github/workflows/agent-mainline.yml. Final repository scope: complete tracked
delta against starting main. Frozen: all historical results/manifests/Goals,
dta_v2 code, upstream and historical runtime locks. Local logs/caches are not
public evidence unless explicitly projected into the v0.5 result package.

Implementation map: existing model.gateway transport/config; Product job Worker
and SQLite store; incidents read backend/connectors/CAS; knowledge compiler,
repository and extension matcher; separate preview module under remediation.

## Implemented slices (in progress; no acceptance terminal)

- Baseline Product/v040/v041 regression: 316 passed (Python 3.12 local runtime).
- Additive SQLite investigation journal/CAS, durable request reservations and
  unknown-outcome refusal, default-off API/Worker investigation jobs.
- Reuse existing gateway config and redirect-rejecting transport, no implicit
  retries; dated same-model pricing required. Raw responses remain local CAS.
- Runtime action catalog over actual connector capabilities; two bounded query
  windows, initial evidence projection, query deduplication, hypothesis target,
  actual coverage and claim-window validation. Model-facing logs omit free text.
- Reproduced strict Python-vs-JSON timestamp validation mismatch and repaired
  only new snapshot adapter with model_validate_json. Failed test output retained
  in this task; initial 2 failures subsequently pass.
- Read-only review identified and fixed initial-evidence, coverage, window,
  complete-discovery holdout exclusion, source-view binding and candidate fencing.
  Follow-up review confirmation still pending.
- Level B resource aggregate/ratio DSL, normal Extension matcher/loader and
  capability invalidation. Fixture-only direct registry insertion proves matching
  and revocation; it is NOT a promotion or real learned rule.
- Shared model/miner candidate representation and existing Shadow gate adapter;
  test-only fresh enrollment, freeze/consume, promotion/revocation methods.
  End-to-end governance tests still being added.
- Preview-only field restoration proposals and deterministic checks. No executor
  dependency or new executable route.
- v050 focused tests: 30 passed. Earlier combined Product/v050 slice: 159 passed.
- Actual local daemon: desktop-linux, Linux aarch64, 29.6.1. No running container
  project listed at inspection. Frozen upstream checkout initialized successfully.
- Provider requests 0; no configured credentials or price schedule yet; user was
  asked for project provider path and dated price source while offline work proceeds.
- Case-plan contains 18 logical slots, explicitly PLANNED_NOT_FROZEN. No live
  episodes, holdout executions, learning acceptance or cleanup success is claimed.

## Continued integration checks

- Added legacy-revocation 409 routing and authenticated v050 revocation; regression
  covers registry version plus normal diagnosis no longer matching revoked knowledge.
- Numeric hypothesis tests now use the deterministic expression evaluator; results
  distinguish TRUE/FALSE/UNKNOWN/NOT_CHECKED and model inference from observed tests.
- Known diagnoses with independent strong residual references can enter supplementary
  investigation without changing the formal diagnosis; explained known remains zero-call.
- Added persisted discovery -> candidate -> frozen evaluation regression. It exposed
  a legacy Shadow strict contract that refuses incomplete control strata. The v050
  adapter now preserves an explicit rejected incomplete-validation attempt; it does
  not weaken the legacy contract or fabricate missing cases.
- Current focused suite: 40 passed. Full repository pytest is running; the first
  mypy pass caught only a union annotation in the new rejection adapter, corrected.
- No real Provider requests or live episodes; project credential path and pricing
  remain missing. No acceptance terminal, promotion success or live cleanup claimed.

## Historical compatibility boundary

- Direct legacy v02323 verification rejected the newly added job enum bytes.
  Resolved with a Product successor model in jobs/contracts_v050.py and repository
  adapter; the historical jobs/contracts.py is restored byte-for-byte. Focused
  new/old knowledge and v02323 acceptance tests: 69 passed.
- v041's immutable manifest binds presentation documents and the CI file as well
  as evidence. Updating current docs therefore makes its direct current-tree hash
  check fail. New scripts/ci/verify_product_v050_history.py checks every immutable
  evidence byte and exact base/current bindings for seven presentation/CI paths,
  then invokes the unchanged old verifier over a temporary historical projection.
  Historical manifests, verifier source and result bytes are not modified.
- Extend write scope precisely to scripts/ci/verify_product_v050_history.py and
  config/product-v050/historical-successor-bindings.json. This is an explicit
  presentation successor, not a new live result or relaxed safety gate.
- Provider hidden-reasoning persistence and unbounded model-suffix acceptance were
  independently reproduced and fixed; follow-up reviewer reports Must Fix = 0
  for the investigated code boundaries, with 42 focused tests passing. Successor
  job/presentation adapters still require final review.

## Fixed-checkout regression preparation

- First full run: 6576 passed / 5 failed / 21 skipped, 800.39s. All failures are
  retained in checks.json. Two failures came from eagerly reading a new capability
  field even when no derived extensions existed; now only the derived lane reads
  it. Their original regressions plus v050: 45 passed.
- The local .venv was incomplete. Synced the existing frozen uv.lock with ci group
  and the repository's frozen pyarrow requirement; no global installation. Local
  Python is 3.12.2; CI remains 3.11. Local v050 + CLI import: 44 passed.
- Independent review confirms the fixed-base manifest anchor closes its P1. No
  open Must Fix remains in reviewed code boundaries; final package/CI still pending.
- Current scope includes the complete new evidence package and exact presentation
  successor bindings. Old verifiers/manifests/results and job contract unchanged.

## Engineering closeout

- Clean implementation commit 430b8a129ed083103134ab361e0674a2262ce3d8:
  6584 passed, 21 skipped, 17 warnings, 739.74 seconds. No test removed or weakened.
  Focused source-bound v050 suite: 43 passed. Full Agent mainline mypy: 731 files.
- PR #104 is Draft: https://github.com/Raidriar7170/EcomSRE-Agent/pull/104.
  RCAEval check passed at the implementation head; mainline CI continues through
  full tests. Final publication status is read from exact PR head checks externally.
- Machine-derived limited terminal:
  ECOMSRE_PRODUCT_V050_ENGINEERING_COMPLETE_WITH_LIMITATIONS.
  Real Provider requests 0/200, actual USD 0/20 because no requests, live episodes
  0/12. No real candidate, holdout, promotion or learned recurrence exists.
- Provider configuration and dated pricing remain unavailable. Case slots remain
  PLANNED_NOT_FROZEN, not a manufactured dataset. This is not ACCEPTANCE_PASS.
- Local Payment/Kafka/Fraud Detection images were read-only inspected as linux/arm64;
  identity observations are retained under .local/product-v050/validation.
  No runtime launch, fault injection, lock rotation or Docker mutation occurred.
  Cleanup is NOT_REQUIRED_NO_V050_RUNTIME_CREATED, not a live CLEAN claim.
- Original checkout retains its two pre-existing tracked document edits; fixed
  upstream HEAD remains 1755859a9de82c2e5e225be68abc401a5ebf2b4f. Worktree is retained
  for the Draft PR. No merge, tag, release or deployment.
