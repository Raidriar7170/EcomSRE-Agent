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

## Continuation 01 — activated 2026-09-17 (in progress)

- Active supplement: `docs/goals/EcomSRE_v0.5_PR104_Review_and_Continuation.md`.
  Start HEAD cd086826b23b728c6527b8b6981aeffef489c4e7; same Draft PR #104,
  same worktree and `.local/product-v050` data root; no budget reset.
- Prior acceptance/preflight/checks remain unchanged historical zero-call evidence.
- R1 reproduced: two failed assertions showed cross-hypothesis support splicing
  and missing supported IDs. Runtime now binds support and numeric test refs to
  the same hypothesis. FALSE/UNKNOWN/wrong scope cannot claim support.
- R2 regression removes initial resource acquisition, then follows selected read,
  CAS persistence, candidate dependency, development and normal zero-call reuse.
  Runtime now binds supplemental observations to incident/environment/capability,
  canonical query/window and optional parent diagnosis; the same finite dependency
  drives deterministic new-event reads. Fixture insertion tests matcher plumbing,
  not actual governance promotion. Negative/tamper coverage continues.
- R3 numeric FALSE counterexamples retain exact scope and refs; empty, failed,
  truncated or wrong-window observations stay UNKNOWN. No complete log-absence
  claim is supported by these query templates.
- R4 cross-candidate prior exposure counterexample failed before repair. Existing
  SQLite now holds immutable harness episode splits, immutable incident bindings,
  exposure before proposer dispatch, global discovery/development/consumed-holdout
  exclusion and independent episode denominator checks. Failure/restart retains
  exposure. Split roles never enter model inputs.
- Independent review identified missing supplemental commit fence, target-less
  cache lookup and repeated-episode denominator; repaired with focused regressions.
  Current v050 tests: 62 passed; focused mypy: 16 source files. Follow-up review
  pending. Initial adapter iterations also retained failures: inconsistent fixture
  snapshot/memory removal, window selection mismatch, uncommitted split transaction,
  and misplaced fence signature; fixed before any paid call.
- Explicit project provider.env exists/readable and contains all three Provider
  variables. Current shell had not loaded them. Exact configured model remains
  gpt-5.4-mini-2026-03-17 on official direct API; no credential was printed.
  Added literal allowlisted dotenv loader; no shell evaluation or other credentials.
- Official model page checked 2026-09-17 lists snapshot and USD0.75 input / USD4.50
  output per million tokens; cache input USD0.075. Standard non-regional text only,
  no hosted tools; upper accounting conservatively charges all input uncached and
  is not an invoice. Explicit price file added; standard service tier pinned.
- Read-only smoke preflight passes; API/Worker same-process environment and feature
  switches verified. Before dispatch: requests 0/200, cost commitment USD0/20,
  live episodes 0/12. Smoke uses replay fixtures, never live-incident denominators.

### Retained real Provider attempts

- Chat smoke attempt 1: PROVIDER_TRANSPORT_FAILED before read/decision; added safe
  HTTP classification, not raw response logging. Attempt 2 on a new replay event:
  HTTP404. Both terminal sessions and unknown reservations retained.
- Exact configured-model metadata GET returned HTTP200 and matching identity;
  conservatively recorded as one more request, not an inference or incident.
- Official Responses function-calling contract checked; explicit bounded adapter
  added with same model, rates, ledger, store=false and no hidden reasoning. New
  attempt 3 also returned HTTP404. CLI choice rejection before this dispatch is
  retained as an engineering failure with zero additional requests.
- Cumulative 4 requests: 3 generation attempts + 1 metadata; all usage unknown,
  upper commitment USD0.095449, remaining USD19.904551 / 196 requests / 12 live
  episodes. No successful smoke => no paid batch or local live campaign started.
- Precise API generation-access/configuration clarification requested while
  completing independent offline closure. No silent model switch, key leakage,
  new campaign budget, session overwrite or external Product write.
- Existing Product/v040/v041 regression: 316 passed. Current v050: 63 passed.
  Product/new scripts mypy: 170 files. Final full repository/CI still pending.

### Continuation bounded closeout

- Clean commit eec6ef5: full repository 6608 passed / 21 skipped / 17 warnings
  (643.19 s); full mainline mypy 734 files; 24 CI verifier modules passed.
- Final independent inspection found the development check/reservation race and
  unchecked supplemental parent/source claims. Added competing-writer and wrong-
  source/parent regressions first (2 failing tests), then fixed with BEGIN IMMEDIATE
  through durable reservation, source equality before CAS/projection, and parent
  diagnosis ownership at save/load. Null parent remains valid before diagnosis.
  Follow-up continuation tests 19 passed; complete v050 67 passed; 2 changed typed
  source files and repository Ruff passed. Final full-suite/CI verification follows
  on the exact published head; no real experiment was repeated for these checks.
- Read/write scopes extend the original declaration to the continuation Goal,
  scripts/ci/verify_product_v050_history.py and verify_product_v050_continuation.py.
  Four previous v050 result files and the original Goal are frozen at cd086826;
  original 1330 historical assets remain protected against starting main.
- Terminal: ECOMSRE_PRODUCT_V050_ENGINEERING_COMPLETE_WITH_LIMITATIONS. R1/R2/R3/R4
  repaired within documented finite-query semantics; real generation remains
  blocked by HTTP404. No model-selected read, knowledge candidate, independent
  live case, holdout, promotion or learned-rule recurrence succeeded/was attempted.
  Same 4 cumulative requests and USD0.095449 reserved upper remain; actual invoice
  unknown. No Docker runtime created, cleanup NOT_REQUIRED, new Product writes 0.
- Same Draft PR #104 only. No merge, release, model switch, skill comparison,
  expanded recovery authority, evidence deletion or fresh campaign budget.

## Provider 404 unblock continuation (2026-09-17)

Active scoped contract: ../goals/EcomSRE_v0.5_Provider_404_Unblock.md; starting clean
HEAD fe57dee4895d966e6d9c270c4e6ec6cc00a57d72, same worktree/ledger/Draft PR #104.
Old attempts1–3 and continuation-01 preserved. No R1–R4/evaluator changes.

- Inspected original four ledger projections. Old body, Content-Type and request
  IDs are not recoverable. Current official base/path/model and dotenv/Worker
  configuration agree; related proxy environment variables absent. No secret hash
  or key printed. Historical credential byte identity was not retained.
- Added Product-only bounded single-read error projection, scrubbed JSON messages,
  allowed headers, method/path/payload shape, unknown-cost retention and dedicated
  no-incident diagnostic entry. Reused frozen transport; no retries/redirects.
- P1 Responses text succeeded before prompt changes; P2 ack boolean function also
  passed. Thus historical404 is not reproduced and its root cause remains unknown.
  Sub-budget2calls, gross reservation11244microusd, reported-token upper205microusd.
- Appended Product attempts4/5/6. Attempt4: prose refs and protocol errors; attempt5:
  mixed windows and incomplete evidence. Provider prompt clarifies existing field
  constraints; Runtime still rejects them. Attempt6: 6 accepted model-selected
  reads and4 complete read→observation→model-followup transitions. Both sessions
  ended at3callcap. Same fixture capture; no independent-incident denominator.
- Knowledge3calls:2protocol-invalid,1schema-valid CPU mean proposal rejected for
  absent verified deployable dependency. No accepted candidate or learning claim.
- Cumulative24requests, committed upper232264microusd, reported upper136815,
  unknown reservations95449; invoice unknown. Remaining176requests/USD19.767736/
  12liveepisodes. All20newrequests included, no cost reset or omitted failures.
- Offline repair iterations retained: initial mypy annotations, Ruff semicolon,
  HTTP200 overwrite and bool/int ack equality (review-found; fixed). System Python
  SQLite read-only inspection could not open the active DB; project .venv reader
  succeeded without changing DB permissions/settings. No extra Provider requests.
- Independent review additionally required deriving roundtrip rather than trusting
  report labels. Added request/decision/observation/followup bindings and tamper
  rejection for fabricated acceptance, zero reads, detached calls and missing data.
- Read-only Docker inspection found zero running containers. No Docker started.
  User clarification pending on section2 no-Docker scope versus original Goal's
  post-entry live authorization. Independent live/holdout/promotion/reuse remain
  NOT_ATTEMPTED; recovery preview only, new Product writes0, cleanup not required.
- Current source-bound checks and final fixed-HEAD full regression/CI follow.
  Final write scope additionally includes the unblock Goal, Product diagnostic
  module, probe/smoke scripts, their tests, new verifier and appended result package;
  prior result packages and historical assets stay frozen. No merge/release.

- Final focused source-bound suite: 91 passed; Ruff passed; focused mypy14files.
  Independent reviewer confirmed Must Fix0 and independently derived4 complete
  roundtrips with6tamper regressions. Final tracked source will now be committed
  before full regression, with no concurrent source or HEAD changes during tests.

## 2026-09-18 — activated Live Resume Amendment; prestart safety block

- Authority: user explicitly restored original owned local Docker/live scope;
  no Product LLM Docker socket/shell/recovery-write grant. Same PR #104 and
  original data root/ledger, starting at f91cb91.
- Read/write scope: Product investigation/knowledge projection, bounded v050
  harness/tests, current status/progress/CI and new live-resume evidence. Frozen:
  upstream1755859, historical DTA/Product results, original zero-call,
  continuation-01 and provider-unblock packages; no old marker/session edited.
  Final repository scope remains this worktree against original main550a564d.
- Actual dependency rejection: initial resource data existed (10 seconds), but
  both member sessions had zero supplemental reads. Claimed 30-second dependency
  was not acquired/bound. Added a finite learning input catalog distinguishing
  bound, incomplete and legal-uncollected reads; no candidate/evaluator relaxation.
  Initial new regression failed on missing catalog, then 25 focused checks passed.
- New owned22-service cachedARM64 subset prepared and created, never started.
  Retained prestart validator failures led to narrow Compose/creation/Desktop
  representation fixes, covered by five focused tests. Partial-create discovery
  and bounded startup timeout reviewed/fixed before mutation.
- Real blocker: default bridge Id/Created changed at02:47:02UTC and again by
  cleanup02:53:41UTC. Daemon identity and other bridge fields unchanged; cause
  unknown; bounded network event query empty. No automatic baseline refresh.
- Birth-bound cleanup removed22containers+1network+5volumes. Ownedremaining0/0/0;
  original3volumes unchanged; non_owned_unchanged=false, clean=false. No CLEAN claim.
- Provider requests added0; cumulative24; token-price upper136815microusd plus
  retained unknown95449 = commitment232264. Actual invoice unknown. Independent
  episodes0, faultinjection0, Product external/recovery writes0. No new candidate,
  independent validation, promotion or reuse. Prepared Product live driver unexecuted.
- Reviewer: dependency projection and prestart safety conclusion MustFix0;
  independently confirmed counts and drift from private records, no secret/raw
  inspection publication. User clarification about network recreation/fresh
  preflight pending. Terminal ECOMSRE_PRODUCT_V050_BLOCKED_SAFETY, not another
  engineering-completion or acceptance PASS. See results/product-v050/live-resume.

- Final-check attempt at `6400daf` stopped during pytest collection: the new test basename collided with `tests/product_v030/test_live_environment.py`. Renamed the v0.5 test module; retained `.local/product-v050/validation/live-resume-full-pytest.log`. This failed attempt is not green evidence.
