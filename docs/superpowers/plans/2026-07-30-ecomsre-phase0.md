# EcomSRE-Agent Phase 0 Implementation Plan

> **Required execution skill:** Use `executing-plans` task by task. Apply
> `test-driven-development` to every behavior change, `systematic-debugging` to
> failures, and `verification-before-completion` before any success claim.

**Goal:** Produce a real, repeatable, non-LLM Phase 0 closed loop on Apple
Silicon for OTel Astronomy Shop 3.0.0: owned environment lifecycle,
programmatic `adServiceFailure` injection/reset, three independently passing
measurement cycles, fresh Prometheus/probe/Jaeger/OpenSearch evidence, and a
complete evidence bundle.

**Authoritative inputs:** [`AGENTS.md`](../../../AGENTS.md),
[`PROJECT_CHARTER.md`](../../PROJECT_CHARTER.md),
[`ARCHITECTURE.md`](../../ARCHITECTURE.md),
[`DECISIONS.md`](../../DECISIONS.md),
[`SAFETY_BOUNDARIES.md`](../../SAFETY_BOUNDARIES.md),
[`PHASE_0_ACCEPTANCE.md`](../../PHASE_0_ACCEPTANCE.md),
[`OPEN_QUESTIONS.md`](../../OPEN_QUESTIONS.md), and
[`PHASE_0_GOAL_MODE_PROMPT.md`](../../PHASE_0_GOAL_MODE_PROMPT.md).

**Architecture:** A small Python package owns deterministic orchestration and
evidence generation. Make targets are thin, non-interactive adapters to a
single CLI. Pure domain modules handle state, thresholds, Wilson intervals,
run identity, and evidence validation. Environment adapters isolate Docker
Compose, upstream pinning, image locking, flag control, and four telemetry
surfaces. The acceptance runner is fail-closed and writes append-only evidence
before returning a stable terminal outcome.

**Technology:** Python 3.11, uv, Pydantic, pytest, standard library subprocess
and HTTP clients where sufficient, Docker Desktop Compose v2, OTel Demo 3.0.0.

**Execution boundary:** This plan authorizes project-local implementation and
the Docker operations expressly allowed by the Goal Mode Prompt. It does not
authorize Git commit/push/PR operations, broad Docker cleanup, host mutation,
Kubernetes, external services, or any Phase 1 work.

## Contract reconciliation

- Evidence step 13 is a freeze of all run and acceptance artifacts. A safe
  environment-down step may then append the shutdown result and terminal
  envelope before final content hashes are sealed. This preserves the
  acceptance sequence without omitting shutdown evidence.
- The deterministic request probe is an independent business observation, not
  the error-rate oracle. It must generate or observe attributable current-run
  `GetAds` traffic and preserve phase-local raw results; Prometheus remains the
  threshold source.
- Initial bootstrap may generate a candidate image lock from frozen upstream
  references. Later acceptance treats that lock as immutable and verifies it
  against local image metadata. The implementation must not claim the lock is
  Git-committed because commit authorization has not been granted.

## Task 1: Project metadata and executable contract

**Files:**

- Create: `pyproject.toml`
- Create: `uv.lock`
- Create: `.gitignore`
- Create: `Makefile`
- Create: `src/ecomsre/__init__.py`
- Create: `src/ecomsre/cli.py`
- Test: `tests/contract/test_make_contract.py`
- Test: `tests/contract/test_cli_contract.py`

**Steps:**

1. Write contract tests that assert the canonical Make targets
   `phase0-bootstrap`, `phase0-preflight`, `phase0-up`, `phase0-health`,
   `phase0-inject`, `phase0-reset`, `phase0-status`, `phase0-accept`, and
   `phase0-stop`; stable exit status mapping; a non-interactive CLI entry point;
   and absence of broad Docker cleanup commands. Short aliases are optional.
2. Run the two tests and confirm failure because the contract does not exist.
3. Add only the minimal package metadata, CLI skeleton with explicit command
   dispatch, Make target adapters, and ignore rules needed to satisfy them.
4. Generate and freeze `uv.lock` without adding dependencies outside the
   accepted Python/Pydantic/pytest set.
5. Run the contract tests and `uv sync --frozen`.

## Task 2: Pure Phase 0 domain model

**Files:**

- Create: `src/ecomsre/phase0/models.py`
- Create: `src/ecomsre/phase0/statistics.py`
- Create: `src/ecomsre/phase0/state_machine.py`
- Create: `tests/unit/test_statistics.py`
- Create: `tests/unit/test_state_machine.py`
- Create: `tests/unit/test_outcomes.py`

**Steps:**

1. Write failing tests for observed-call denominators, minimum 200 calls,
   180-second deadline, configurable 30-second stabilization, thresholds,
   Wilson interval calculation, three independent cycles, and terminal exit
   codes.
2. Write failing tests for legal state transitions:
   preflight, startup, readiness, stabilization, baseline, injection,
   stabilization, fault, reset, stabilization, recovery, telemetry readiness,
   evidence freeze, optional shutdown, terminal result.
3. Implement immutable Pydantic models, pure statistics functions, and an
   explicit fail-closed state machine.
4. Run the focused unit tests and verify invalid transitions and ambiguous
   outcomes fail closed.

## Task 3: Run identity, ownership, and evidence integrity

**Files:**

- Create: `src/ecomsre/evidence/models.py`
- Create: `src/ecomsre/evidence/store.py`
- Create: `src/ecomsre/evidence/hashes.py`
- Create: `src/ecomsre/environment/ownership.py`
- Create: `tests/unit/test_run_identity.py`
- Create: `tests/unit/test_ownership.py`
- Create: `tests/unit/test_evidence_store.py`
- Create: `tests/contract/test_evidence_schema.py`

**Steps:**

1. Write failing tests for unique run IDs, owned project namespace/labels,
   unknown-resource rejection, append-only failure preservation, command-log
   redaction, content hashes, and atomic terminal manifest creation.
2. Write a schema test covering every required evidence field from the
   acceptance document.
3. Implement ownership checks and an evidence store rooted at
   `artifacts/phase0/<run_id>/`, with raw responses separate from summaries.
4. Ensure a failed cycle can never be overwritten by a successful retry.
5. Run the focused unit and contract tests.

## Task 4: Preflight and local dependency freeze

**Files:**

- Create: `src/ecomsre/environment/preflight.py`
- Create: `src/ecomsre/environment/manifests.py`
- Create: `config/phase0/image-lock.json`
- Create: `tests/unit/test_preflight.py`
- Create: `tests/contract/test_manifests.py`
- Create: `tests/contract/test_image_lock.py`

**Steps:**

1. Write failing tests for Apple Silicon architecture, Docker/Compose
   availability, resource/disk requirements, port conflicts, upstream commit,
   Compose hash, cached image digest/platform verification, and `--pull never`
   readiness.
2. Implement read-only preflight collection and stable outcome classification.
3. Implement candidate image-lock generation for bootstrap and immutable lock
   validation for formal acceptance.
4. Ensure unknown ports/resources or a digest/platform mismatch stops before
   environment mutation.
5. Run focused tests using recorded command fixtures only.

## Task 5: Frozen upstream and owned Compose lifecycle

**Files:**

- Create: `.gitmodules`
- Create: `third_party/opentelemetry-demo/` as the read-only pinned submodule
- Create: `config/phase0/compose.phase0.yaml`
- Create: `src/ecomsre/environment/upstream.py`
- Create: `src/ecomsre/environment/lifecycle.py`
- Create: `tests/contract/test_upstream_pin.py`
- Create: `tests/contract/test_compose_contract.py`
- Create: `tests/integration/test_environment_lifecycle.py`

**Steps:**

1. Write failing contract tests for tag/commit `3.0.0` /
   `1755859a9de82c2e5e225be68abc401a5ebf2b4f`, read-only use, allowed Compose
   files, fixed project namespace, labels, `--pull never`, and scoped down.
2. Add the submodule metadata and initialize it only during authorized
   bootstrap; verify exact commit before use.
3. Implement Compose command construction with explicit files, project name,
   environment manifest, and ownership proof.
4. Implement safe startup/status/down adapters. Down must refuse any project
   whose ownership cannot be proven.
5. Run offline contract tests, then the lifecycle integration test only after
   preflight proves the host safe.

## Task 6: Programmatic fault control and hidden-truth separation

**Files:**

- Create: `src/ecomsre/scenarios/ad_service_failure.py`
- Create: `src/ecomsre/scenarios/ground_truth.py`
- Create: `tests/unit/test_scenario_state.py`
- Create: `tests/contract/test_hidden_ground_truth.py`
- Create: `tests/integration/test_ad_service_failure.py`

**Steps:**

1. Write failing tests for inject/reset idempotency, positive read-back,
   evaluator-only ground-truth location, observer input denial, and transition
   evidence.
2. Implement the programmatic flagd control path discovered from the pinned
   upstream. Do not require UI interaction.
3. Persist hidden physical/logical transition records outside observer-visible
   telemetry and enforce the read boundary in code.
4. Run offline tests, then real inject/read-back/reset integration only against
   the owned environment.

## Task 7: Deterministic probe and telemetry adapters

**Files:**

- Create: `src/ecomsre/telemetry/http.py`
- Create: `src/ecomsre/telemetry/prometheus.py`
- Create: `src/ecomsre/telemetry/jaeger.py`
- Create: `src/ecomsre/telemetry/opensearch.py`
- Create: `src/ecomsre/telemetry/probe.py`
- Create: `config/phase0/telemetry-queries-v3.0.0.json`
- Create: `tests/unit/test_time_windows.py`
- Create: `tests/unit/test_stale_data_rejection.py`
- Create: `tests/contract/test_telemetry_queries.py`
- Create: `tests/integration/test_telemetry_readiness.py`

**Steps:**

1. Write failing tests for phase windows, current-run freshness, service
   identity, raw query/response preservation, stale data rejection, and probe
   independence from hidden truth.
2. Implement small HTTP adapters with bounded timeouts and no SaaS/external API
   dependency.
3. Discover actual OTel Demo 3.0.0 `service.name`, metric, trace, and log fields
   from the owned running environment and freeze the confirmed queries.
4. Implement Prometheus `GetAds` attempts/errors as the primary statistics
   source; implement the probe as attributable current-run business evidence.
5. Require new Jaeger and OpenSearch data inside each run window.
6. Run fixture contract tests, then the real telemetry integration test.

## Task 8: Deterministic acceptance runner

**Files:**

- Create: `src/ecomsre/phase0/runner.py`
- Create: `tests/unit/test_runner_failures.py`
- Create: `tests/integration/test_single_cycle.py`
- Create: `tests/integration/test_three_cycles.py`

**Steps:**

1. Write failing tests for every stop condition, per-window deadline,
   independent cycle evaluation, failure artifact retention, and no
   success-only rerun reporting.
2. Implement orchestration through the explicit state machine and existing
   adapters; no behavior may bypass ownership or readiness gates.
3. Ensure interruption produces a durable incomplete/failed terminal envelope
   and preserves the environment unless safe owned shutdown is proven.
4. Run unit tests, then one real cycle, inspect evidence, then three real cycles.

## Task 9: Operator documentation and Phase 0 closure records

**Files:**

- Create: `README.md`
- Create: `docs/PHASE_0_RUNBOOK.md`
- Create: `docs/EVIDENCE_SCHEMA.md`
- Create: `docs/TROUBLESHOOTING.md`
- Modify: `docs/OPEN_QUESTIONS.md`
- Modify: `docs/PHASE_0_ACCEPTANCE.md` only if observed reality requires a
  clarification that does not change a frozen decision

**Steps:**

1. Document exact commands, exit codes, recovery, evidence layout, observed
   machine fingerprint, frozen telemetry fields, limitations, and safe cleanup.
2. Close OQ-001 through OQ-004 only with links to evidence from a real run.
3. Keep OQ-005 through OQ-008 deferred and non-blocking for Phase 0.
4. Check every internal Markdown link and ensure planning decisions are
   referenced rather than duplicated.

## Task 10: Full verification and closeout

**Files:**

- Create: `docs/human-briefs/2026-07-30-ecomsre-phase0.html`
- Produce: `artifacts/phase0/<run_id>/...`

**Steps:**

1. Run `uv sync --frozen`.
2. Run `uv run pytest`.
3. Run `git diff --check`.
4. Run `make phase0-preflight`.
5. Run `make phase0-health` against the owned environment.
6. Run `make phase0-accept` and require three complete independent cycles.
7. Run or retain `make phase0-stop` according to the selected terminal policy,
   then seal the final evidence hashes.
8. Ask an independent Reviewer to inspect the exact implementation diff,
   command logs, evidence hashes, acceptance results, safety boundaries, and
   OQ closure. Fix all critical issues and repeat affected verification.
9. Write the Chinese Human Brief from authoritative artifacts and label the
   result exactly `SUCCESS`, `BLOCKED`, `FAILED_ACCEPTANCE`, or `UNSAFE`.
10. Stop. Do not enter Phase 1 and do not perform Git publication actions.
