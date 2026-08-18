PROJECT_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PYTHONPATH := $(PROJECT_ROOT)/src
UV_CACHE_ROOT := $(PROJECT_ROOT)/.ecomsre-cache
UV_CACHE_DIR := $(PROJECT_ROOT)/.ecomsre-cache/uv
TMPDIR := $(PROJECT_ROOT)/.ecomsre-tmp
export UV_CACHE_DIR TMPDIR

BOOTSTRAP_CLI := env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync python -m ecomsre.cli phase0
PHASE0_CLI := env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync python -m ecomsre.cli phase0
export ECOMSRE_RUN_ID := $(value RUN_ID)

.PHONY: phase0-prerequisites phase0-bootstrap phase0-preflight phase0-up phase0-health \
	phase0-inject phase0-reset phase0-status phase0-accept phase0-smoke phase0-stop \
	phase0-cleanup-owned-volumes

phase0-prerequisites:
	@for path in "$(UV_CACHE_ROOT)" "$(UV_CACHE_DIR)" "$(TMPDIR)"; do \
		test ! -e "$$path" || { test -d "$$path" && test ! -L "$$path" && test -O "$$path"; } || exit 40; \
	done
	@mkdir -p "$(UV_CACHE_ROOT)" "$(UV_CACHE_DIR)" "$(TMPDIR)"
	@chmod 700 "$(UV_CACHE_ROOT)" "$(UV_CACHE_DIR)" "$(TMPDIR)"

phase0-bootstrap: phase0-prerequisites
	$(BOOTSTRAP_CLI) bootstrap

phase0-preflight: phase0-prerequisites
	$(PHASE0_CLI) preflight

phase0-up: phase0-prerequisites
	$(PHASE0_CLI) up

phase0-health: phase0-prerequisites
	$(PHASE0_CLI) health

phase0-inject: phase0-prerequisites
	$(PHASE0_CLI) inject

phase0-reset: phase0-prerequisites
	$(PHASE0_CLI) reset

phase0-status: phase0-prerequisites
	$(PHASE0_CLI) status

phase0-accept: phase0-prerequisites
	$(PHASE0_CLI) accept

phase0-smoke: phase0-prerequisites
	$(PHASE0_CLI) smoke

phase0-stop: phase0-prerequisites
	$(PHASE0_CLI) stop

phase0-cleanup-owned-volumes: phase0-prerequisites
	$(PHASE0_CLI) cleanup-owned-volumes

PHASE1_CLI := env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync python -m ecomsre.phase1.cli

.PHONY: phase1-prerequisites phase1-replay-smoke phase1-eval phase1-test phase1-provider-smoke

phase1-prerequisites:
	@for path in "$(UV_CACHE_ROOT)" "$(UV_CACHE_DIR)" "$(TMPDIR)"; do \
		test ! -e "$$path" || { test -d "$$path" && test ! -L "$$path" && test -O "$$path"; } || exit 40; \
	done
	@mkdir -p "$(UV_CACHE_ROOT)" "$(UV_CACHE_DIR)" "$(TMPDIR)"
	@chmod 700 "$(UV_CACHE_ROOT)" "$(UV_CACHE_DIR)" "$(TMPDIR)"

phase1-replay-smoke: phase1-prerequisites
	$(PHASE1_CLI) replay-smoke

phase1-eval: phase1-prerequisites
	$(PHASE1_CLI) eval

phase1-test: phase1-prerequisites
	env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync pytest tests/phase1 -q

phase1-provider-smoke: phase1-prerequisites
	$(PHASE1_CLI) provider-smoke

PHASE2_CLI := env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync python -m ecomsre.phase2.cli
PHASE2_REPORT ?= $(PROJECT_ROOT)/artifacts/phase2/comparison/comparison-report.json
PHASE2_PROVIDER_CASE_ROOT ?= $(PROJECT_ROOT)/artifacts/phase2/provider-smoke/cases
PHASE2_PROVIDER_REQUIREMENT ?=

.PHONY: phase2-compare phase2-verify phase2-test phase2-provider-smoke \
	phase2-provider-smoke-case phase2-provider-smoke-aggregate

phase2-compare: phase1-prerequisites
	$(PHASE2_CLI) compare --output "$(PHASE2_REPORT)"

phase2-verify: phase1-prerequisites
	$(PHASE2_CLI) verify --report "$(PHASE2_REPORT)"

phase2-test: phase1-prerequisites
	env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync pytest tests/phase2 -q

phase2-provider-smoke: phase1-prerequisites
	$(PHASE2_CLI) provider-smoke

phase2-provider-smoke-case: phase1-prerequisites
	@test -n "$(PHASE2_PROVIDER_REQUIREMENT)" || { echo "PHASE2_PROVIDER_REQUIREMENT is required" >&2; exit 2; }
	$(PHASE2_CLI) provider-smoke-case \
		--requirement "$(PHASE2_PROVIDER_REQUIREMENT)" \
		--output "$(PHASE2_PROVIDER_CASE_ROOT)/$(PHASE2_PROVIDER_REQUIREMENT).json"

phase2-provider-smoke-aggregate: phase1-prerequisites
	$(PHASE2_CLI) provider-smoke-aggregate --case-root "$(PHASE2_PROVIDER_CASE_ROOT)"

PHASE3_CLI := env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync python -m ecomsre.phase3.cli
PHASE3_REPORT ?= $(PROJECT_ROOT)/artifacts/phase3/replay/minimum-evaluation-report.json

.PHONY: phase3-replay phase3-verify phase3-test

phase3-replay: phase1-prerequisites
	$(PHASE3_CLI) replay --output "$(PHASE3_REPORT)"

phase3-verify: phase1-prerequisites
	$(PHASE3_CLI) verify --report "$(PHASE3_REPORT)"

phase3-test: phase1-prerequisites
	env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync pytest tests/phase3 -q

PHASE4_CLI := env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync python -m ecomsre.phase4.cli
PHASE4_REPORT ?= $(PROJECT_ROOT)/artifacts/phase4/comparison/domain-comparison-report.json
PHASE4_DEMO_REPORT ?= $(PROJECT_ROOT)/artifacts/phase4/demo/domain-demo-report.json
PHASE4_PROVIDER_REPORT ?= $(PROJECT_ROOT)/artifacts/phase4/provider-smoke/provider-smoke-report.json

.PHONY: phase4-test phase4-compare phase4-verify phase4-demo phase4-provider-smoke

phase4-test: phase1-prerequisites
	env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync pytest tests/phase4 -q

phase4-compare: phase1-prerequisites
	$(PHASE4_CLI) compare --output "$(PHASE4_REPORT)"

phase4-verify: phase1-prerequisites
	$(PHASE4_CLI) verify --report "$(PHASE4_REPORT)"

phase4-demo: phase1-prerequisites
	$(PHASE4_CLI) demo --output "$(PHASE4_DEMO_REPORT)"

phase4-provider-smoke: phase1-prerequisites
	$(PHASE4_CLI) provider-smoke --output "$(PHASE4_PROVIDER_REPORT)"

PHASE5A_CLI := env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync python -m ecomsre.phase5a.cli
PHASE5A_REPORT ?= $(PROJECT_ROOT)/artifacts/phase5a/comparison/capability-parity-report.json
PHASE5A_DEMO_REPORT ?= $(PROJECT_ROOT)/artifacts/phase5a/demo/diagnosis-quality-demo.json
PHASE5A_PROVIDER_REPORT ?= $(PROJECT_ROOT)/artifacts/phase5a/provider-pilot/provider-pilot-report.json
PHASE5A_PROVIDER_SHAPE_REPORT ?= $(PROJECT_ROOT)/artifacts/phase5a/provider-diagnostics/request-shape-summary.json
PHASE5A_PROVIDER_ORDER_REPORT ?= $(PROJECT_ROOT)/artifacts/phase5a/provider-diagnostics/order-isolation-report.json

.PHONY: phase5a-test phase5a-compare phase5a-verify phase5a-demo \
	phase5a-provider-pilot phase5a-provider-request-shapes \
	phase5a-provider-order-isolation

phase5a-test: phase1-prerequisites
	env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync pytest tests/phase5a -q

phase5a-compare: phase1-prerequisites
	$(PHASE5A_CLI) compare --output "$(PHASE5A_REPORT)"

phase5a-verify: phase1-prerequisites
	$(PHASE5A_CLI) verify --report "$(PHASE5A_REPORT)"

phase5a-demo: phase1-prerequisites
	$(PHASE5A_CLI) demo --output "$(PHASE5A_DEMO_REPORT)"

phase5a-provider-pilot: phase1-prerequisites
	$(PHASE5A_CLI) provider-pilot --output "$(PHASE5A_PROVIDER_REPORT)"

phase5a-provider-request-shapes: phase1-prerequisites
	$(PHASE5A_CLI) provider-request-shapes --output "$(PHASE5A_PROVIDER_SHAPE_REPORT)"

phase5a-provider-order-isolation: phase1-prerequisites
	$(PHASE5A_CLI) provider-order-isolation --output "$(PHASE5A_PROVIDER_ORDER_REPORT)"

PHASE5B_CLI := env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync python -m ecomsre.phase5b.cli
PHASE5B_CONTROL_PLANE_PYTHONPATH := $(PROJECT_ROOT):$(PYTHONPATH)
PHASE5B_SEAL_CLI := env PYTHONPATH="$(PHASE5B_CONTROL_PLANE_PYTHONPATH)" uv run --frozen --no-sync python -m scripts.phase5b_hidden_pack.seal_cli
PHASE5B_DRY_RUN_REPORT ?= $(PROJECT_ROOT)/artifacts/phase5b/mock-protocol-dry-run.json
PHASE5B_HIDDEN_PACK_ROOT ?=
PHASE5B_EXECUTION_CLI := env PYTHONPATH="$(PHASE5B_CONTROL_PLANE_PYTHONPATH)" uv run --frozen --no-sync python -m scripts.phase5b_execution.cli
PHASE5B_EXECUTION_MOCK_ROOT ?= $(PROJECT_ROOT)/artifacts/phase5b-execution/mock-rehearsal
PHASE5B_EXECUTION_ROOT ?= $(HOME)/.ecomsre-private/phase5b-v1-execution

.PHONY: phase5b-test phase5b-preflight phase5b-protocol-verify phase5b-schedule \
	phase5b-dry-run phase5b-dry-run-verify phase5b-hidden-pack-contract-test \
	phase5b-hidden-pack-verify phase5b-hidden-pack-seal-verify

phase5b-test: phase1-prerequisites
	env PYTHONPATH="$(PHASE5B_CONTROL_PLANE_PYTHONPATH)" uv run --frozen --no-sync pytest tests/phase5b -q

phase5b-preflight: phase1-prerequisites
	$(PHASE5B_CLI) preflight

phase5b-protocol-verify: phase1-prerequisites
	$(PHASE5B_CLI) protocol-verify

phase5b-schedule: phase1-prerequisites
	$(PHASE5B_CLI) schedule

phase5b-dry-run: phase1-prerequisites
	$(PHASE5B_CLI) dry-run --output "$(PHASE5B_DRY_RUN_REPORT)"

phase5b-dry-run-verify: phase1-prerequisites
	$(PHASE5B_CLI) dry-run-verify --report "$(PHASE5B_DRY_RUN_REPORT)"

phase5b-hidden-pack-contract-test: phase1-prerequisites
	env PYTHONPATH="$(PHASE5B_CONTROL_PLANE_PYTHONPATH)" uv run --frozen --no-sync pytest tests/phase5b/test_hidden_pack_seal.py -q

phase5b-hidden-pack-verify: phase1-prerequisites
	@test -n "$(strip $(PHASE5B_HIDDEN_PACK_ROOT))" || { \
		echo "PHASE5B_HIDDEN_PACK_ROOT is required" >&2; exit 2; \
	}
	@$(PHASE5B_SEAL_CLI) verify-pack --pack-root "$(PHASE5B_HIDDEN_PACK_ROOT)"

phase5b-hidden-pack-seal-verify: phase1-prerequisites
	@$(PHASE5B_SEAL_CLI) verify-seal

.PHONY: phase5b-execution-test phase5b-execution-preflight \
	phase5b-execution-freeze-verify phase5b-execution-mock-rehearsal \
	phase5b-execution-mock-verify phase5b-provider-preflight \
	phase5b-provider-canary phase5b-enter-execution phase5b-execute-main \
	phase5b-execute-ablation phase5b-seal-execution \
	phase5b-execution-report-verify phase5b-ablation-report-verify \
	phase5b-unblind phase5b-unblinding-verify phase5b-final-analysis \
	phase5b-final-report-verify

phase5b-execution-test: phase1-prerequisites
	env PYTHONPATH="$(PHASE5B_CONTROL_PLANE_PYTHONPATH)" uv run --frozen --no-sync pytest tests/phase5b_execution -q

phase5b-execution-preflight: phase1-prerequisites
	$(PHASE5B_EXECUTION_CLI) preflight

phase5b-execution-freeze-verify: phase1-prerequisites
	$(PHASE5B_EXECUTION_CLI) freeze-verify

phase5b-execution-mock-rehearsal: phase1-prerequisites
	$(PHASE5B_EXECUTION_CLI) mock-rehearsal --output-root "$(PHASE5B_EXECUTION_MOCK_ROOT)"

phase5b-execution-mock-verify: phase1-prerequisites
	$(PHASE5B_EXECUTION_CLI) mock-verify --output-root "$(PHASE5B_EXECUTION_MOCK_ROOT)"

phase5b-provider-preflight: phase1-prerequisites
	$(PHASE5B_EXECUTION_CLI) provider-preflight

phase5b-provider-canary: phase1-prerequisites
	$(PHASE5B_EXECUTION_CLI) provider-canary --output-root "$(PHASE5B_EXECUTION_ROOT)"

phase5b-enter-execution: phase1-prerequisites
	$(PHASE5B_EXECUTION_CLI) enter-execution --output-root "$(PHASE5B_EXECUTION_ROOT)"

phase5b-execute-main: phase1-prerequisites
	$(PHASE5B_EXECUTION_CLI) execute-main --output-root "$(PHASE5B_EXECUTION_ROOT)"

phase5b-execute-ablation: phase1-prerequisites
	$(PHASE5B_EXECUTION_CLI) execute-ablation --output-root "$(PHASE5B_EXECUTION_ROOT)"

phase5b-seal-execution: phase1-prerequisites
	$(PHASE5B_EXECUTION_CLI) seal-execution --output-root "$(PHASE5B_EXECUTION_ROOT)"

phase5b-execution-report-verify: phase1-prerequisites
	$(PHASE5B_EXECUTION_CLI) report-verify --output-root "$(PHASE5B_EXECUTION_ROOT)"

phase5b-ablation-report-verify: phase1-prerequisites
	$(PHASE5B_EXECUTION_CLI) ablation-report-verify --output-root "$(PHASE5B_EXECUTION_ROOT)"

phase5b-unblind: phase1-prerequisites
	$(PHASE5B_EXECUTION_CLI) unblind --output-root "$(PHASE5B_EXECUTION_ROOT)"

phase5b-unblinding-verify: phase1-prerequisites
	$(PHASE5B_EXECUTION_CLI) unblinding-verify --output-root "$(PHASE5B_EXECUTION_ROOT)"

phase5b-final-analysis: phase1-prerequisites
	$(PHASE5B_EXECUTION_CLI) final-analysis --output-root "$(PHASE5B_EXECUTION_ROOT)"

phase5b-final-report-verify: phase1-prerequisites
	$(PHASE5B_EXECUTION_CLI) final-report-verify --output-root "$(PHASE5B_EXECUTION_ROOT)"

AGENT_DEMO_CLI := env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync python -m ecomsre.demo
AGENT_DEMO_REPORT ?= $(PROJECT_ROOT)/artifacts/demo/agent-mainline-v1-report.json

.PHONY: agent-demo

agent-demo: phase1-prerequisites
	$(AGENT_DEMO_CLI) run --output "$(AGENT_DEMO_REPORT)"

# BEGIN DTA_V21_SUCCESSOR_TARGETS
DTA_V21_HISTORICAL_BINDINGS_CLI := env PYTHONPATH="$(PROJECT_ROOT):$(PYTHONPATH)" uv run --frozen --no-sync python -m scripts.ci.verify_dta_v2_historical_bindings
DTA_V21_EVALUATION_CLI := env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync python -m ecomsre.dta_v2.v21.evaluation_cli
DTA_V21_EVALUATION_VERIFY_CLI := env PYTHONPATH="$(PROJECT_ROOT):$(PYTHONPATH)" uv run --frozen --no-sync python -m scripts.ci.verify_dta_v21_evaluation_freeze
DTA_V21_HELD_OUT_CLI := env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync python -m ecomsre.dta_v2.v21.held_out_cli
DTA_V21_HELD_OUT_VERIFY_CLI := env PYTHONPATH="$(PROJECT_ROOT):$(PYTHONPATH)" uv run --frozen --no-sync python -m scripts.ci.verify_dta_v21_held_out
DTA_V21_PR_F_PROTOCOL_VERIFY_CLI := env PYTHONPATH="$(PROJECT_ROOT):$(PYTHONPATH)" uv run --frozen --no-sync python -m scripts.ci.verify_dta_v21_pr_f_protocol
DTA_V21_LIVE_CLI := env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync python -m ecomsre.dta_v2.v21.live_cli
DTA_V21_LIVE_VERIFY_CLI := env PYTHONPATH="$(PROJECT_ROOT):$(PYTHONPATH)" uv run --frozen --no-sync python -m scripts.ci.verify_dta_v21_live
DTA_V21_EVALUATION_ROOT := $(PROJECT_ROOT)/config/dta-v21/evaluation

.PHONY: dta-v21-historical-verify dta-v21-test dta-v21-replay-verify \
	dta-v21-development-eval dta-v21-development-verify \
	dta-v21-held-out-execute dta-v21-held-out-score \
	dta-v21-held-out-report-verify dta-v21-pr-f-protocol-verify \
	dta-v21-pr-f-protocol-private-verify dta-v21-live-preflight \
	dta-v21-live-reconcile dta-v21-live-record-retry-review \
	dta-v21-live-retry-admit dta-v21-pr-f-reconciliation-private-verify \
	dta-v21-live-demo dta-v21-live-report dta-v21-live-finalize \
	dta-v21-live-closeout \
	dta-v21-live-verify \
	dta-v21-demo dta-v21-verify

dta-v21-historical-verify: phase1-prerequisites
	$(DTA_V21_HISTORICAL_BINDINGS_CLI)

dta-v21-test: dta-v21-historical-verify
	env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync pytest tests/dta_v21 -q

dta-v21-replay-verify: dta-v21-historical-verify
	env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync pytest tests/dta_v21/test_v21_replay_execution.py -q

dta-v21-development-eval: phase1-prerequisites
	@test -n "$(DTA_V21_PROVIDER_ENV)" || { echo "DTA_V21_PROVIDER_ENV is required" >&2; exit 2; }
	@test -n "$(DTA_V21_DEVELOPMENT_ROOT)" || { echo "DTA_V21_DEVELOPMENT_ROOT is required" >&2; exit 2; }
	@test -n "$(DTA_V21_ATTEMPTS_ROOT)" || { echo "DTA_V21_ATTEMPTS_ROOT is required" >&2; exit 2; }
	@test -n "$(DTA_V21_ATTEMPT_ID)" || { echo "DTA_V21_ATTEMPT_ID is required" >&2; exit 2; }
	$(DTA_V21_EVALUATION_CLI) \
		--repository-root "$(PROJECT_ROOT)" \
		--provider-env "$(DTA_V21_PROVIDER_ENV)" \
		--development-root "$(DTA_V21_DEVELOPMENT_ROOT)" \
		--private-attempts-root "$(DTA_V21_ATTEMPTS_ROOT)" \
		--attempt-id "$(DTA_V21_ATTEMPT_ID)" \
		--public-manifest "$(DTA_V21_EVALUATION_ROOT)/public-case-bindings.v1.json" \
		--schedule "$(DTA_V21_EVALUATION_ROOT)/schedule.v1.json" \
		--preregistration "$(DTA_V21_EVALUATION_ROOT)/preregistration.v1.json" \
		--public-report "$(PROJECT_ROOT)/docs/results/dta-v21-development-evaluation.json" \
		--public-disposition "$(PROJECT_ROOT)/docs/review-evidence/dta-v21-evaluation-freeze/current-disposition.json"

dta-v21-development-verify: dta-v21-historical-verify
	$(DTA_V21_EVALUATION_VERIFY_CLI) --project-root "$(PROJECT_ROOT)"

dta-v21-held-out-execute: dta-v21-development-verify
	@test -n "$(DTA_V21_PROVIDER_ENV)" || { echo "DTA_V21_PROVIDER_ENV is required" >&2; exit 2; }
	@test -n "$(DTA_V21_HELD_OUT_PACK_ROOT)" || { echo "DTA_V21_HELD_OUT_PACK_ROOT is required" >&2; exit 2; }
	@test -n "$(DTA_V21_PRIVATE_EXECUTION_ROOT)" || { echo "DTA_V21_PRIVATE_EXECUTION_ROOT is required" >&2; exit 2; }
	@test -n "$(DTA_V21_EXECUTION_ID)" || { echo "DTA_V21_EXECUTION_ID is required" >&2; exit 2; }
	@test -n "$(DTA_V21_EXECUTION_CODE_HEAD)" || { echo "DTA_V21_EXECUTION_CODE_HEAD is required" >&2; exit 2; }
	$(DTA_V21_HELD_OUT_CLI) execute \
		--repository-root "$(PROJECT_ROOT)" \
		--provider-env "$(DTA_V21_PROVIDER_ENV)" \
		--held-out-pack-root "$(DTA_V21_HELD_OUT_PACK_ROOT)" \
		--private-execution-root "$(DTA_V21_PRIVATE_EXECUTION_ROOT)" \
		--execution-id "$(DTA_V21_EXECUTION_ID)" \
		--execution-code-head "$(DTA_V21_EXECUTION_CODE_HEAD)" \
		--git-audit-root "$(DTA_V21_PRIVATE_EXECUTION_ROOT)-git-audit" \
		--freeze-manifest "$(DTA_V21_EVALUATION_ROOT)/manifest.json" \
		--schedule "$(DTA_V21_EVALUATION_ROOT)/schedule.v1.json" \
		--preregistration "$(DTA_V21_EVALUATION_ROOT)/preregistration.v1.json" \
		--held-out-pack-seal "$(DTA_V21_HELD_OUT_PACK_ROOT)/held-out-seal.v1.json"

dta-v21-held-out-score: phase1-prerequisites
	@test -n "$(DTA_V21_HELD_OUT_PACK_ROOT)" || { echo "DTA_V21_HELD_OUT_PACK_ROOT is required" >&2; exit 2; }
	@test -n "$(DTA_V21_PRIVATE_EXECUTION_ROOT)" || { echo "DTA_V21_PRIVATE_EXECUTION_ROOT is required" >&2; exit 2; }
	@test -n "$(DTA_V21_PRIVATE_UNBLINDING_ROOT)" || { echo "DTA_V21_PRIVATE_UNBLINDING_ROOT is required" >&2; exit 2; }
	@test -n "$(DTA_V21_DEVELOPMENT_ATTEMPT_ROOT)" || { echo "DTA_V21_DEVELOPMENT_ATTEMPT_ROOT is required" >&2; exit 2; }
	@test -n "$(DTA_V21_DEVELOPMENT_DATASET_ROOT)" || { echo "DTA_V21_DEVELOPMENT_DATASET_ROOT is required" >&2; exit 2; }
	$(DTA_V21_HELD_OUT_CLI) score \
		--repository-root "$(PROJECT_ROOT)" \
		--held-out-pack-root "$(DTA_V21_HELD_OUT_PACK_ROOT)" \
		--private-execution-root "$(DTA_V21_PRIVATE_EXECUTION_ROOT)" \
		--private-unblinding-root "$(DTA_V21_PRIVATE_UNBLINDING_ROOT)" \
		--development-attempt-root "$(DTA_V21_DEVELOPMENT_ATTEMPT_ROOT)" \
		--development-dataset-root "$(DTA_V21_DEVELOPMENT_DATASET_ROOT)" \
		--public-development-report "$(PROJECT_ROOT)/docs/results/dta-v21-development-evaluation.json" \
		--freeze-manifest "$(DTA_V21_EVALUATION_ROOT)/manifest.json" \
		--schedule "$(DTA_V21_EVALUATION_ROOT)/schedule.v1.json" \
		--preregistration "$(DTA_V21_EVALUATION_ROOT)/preregistration.v1.json" \
		--held-out-pack-seal "$(DTA_V21_HELD_OUT_PACK_ROOT)/held-out-seal.v1.json" \
		--public-evaluation-json "$(PROJECT_ROOT)/docs/results/dta-v21-evaluation.json" \
		--public-evaluation-markdown "$(PROJECT_ROOT)/docs/results/dta-v21-evaluation.md" \
		--public-ablation-json "$(PROJECT_ROOT)/docs/results/dta-v21-ablation.json" \
		--public-ablation-markdown "$(PROJECT_ROOT)/docs/results/dta-v21-ablation.md" \
		--public-disposition "$(PROJECT_ROOT)/docs/review-evidence/dta-v21-held-out/current-disposition.json"

dta-v21-held-out-report-verify: dta-v21-historical-verify
	$(DTA_V21_HELD_OUT_VERIFY_CLI) --project-root "$(PROJECT_ROOT)"

dta-v21-pr-f-protocol-verify: dta-v21-historical-verify
	$(DTA_V21_PR_F_PROTOCOL_VERIFY_CLI) --project-root "$(PROJECT_ROOT)"

dta-v21-pr-f-protocol-private-verify: dta-v21-historical-verify
	@test -n "$(DTA_V21_ACCEPTED_PRIVATE_ROOT)" || { echo "DTA_V21_ACCEPTED_PRIVATE_ROOT is required" >&2; exit 2; }
	$(DTA_V21_PR_F_PROTOCOL_VERIFY_CLI) \
		--project-root "$(PROJECT_ROOT)" \
		--private-root "$(DTA_V21_ACCEPTED_PRIVATE_ROOT)"

dta-v21-live-preflight: dta-v21-pr-f-protocol-private-verify
	@test -n "$(DTA_V21_PROVIDER_ENV)" || { echo "DTA_V21_PROVIDER_ENV is required" >&2; exit 2; }
	@test -n "$(DTA_V21_EXACT_HEAD_CI_SHA)" || { echo "DTA_V21_EXACT_HEAD_CI_SHA is required" >&2; exit 2; }
	$(DTA_V21_LIVE_CLI) preflight \
		--repository-root "$(PROJECT_ROOT)" \
		--private-root "$(DTA_V21_ACCEPTED_PRIVATE_ROOT)" \
		--provider-env "$(DTA_V21_PROVIDER_ENV)" \
		--exact-head-ci-sha "$(DTA_V21_EXACT_HEAD_CI_SHA)"

dta-v21-live-reconcile: dta-v21-pr-f-protocol-private-verify
	$(DTA_V21_LIVE_CLI) reconcile \
		--repository-root "$(PROJECT_ROOT)" \
		--private-root "$(DTA_V21_ACCEPTED_PRIVATE_ROOT)"

dta-v21-live-record-retry-review: dta-v21-pr-f-protocol-private-verify
	@test -n "$(DTA_V21_REVIEWER)" || { echo "DTA_V21_REVIEWER is required" >&2; exit 2; }
	$(DTA_V21_LIVE_CLI) record-retry-review \
		--repository-root "$(PROJECT_ROOT)" \
		--private-root "$(DTA_V21_ACCEPTED_PRIVATE_ROOT)" \
		--reviewer "$(DTA_V21_REVIEWER)"

dta-v21-live-retry-admit: dta-v21-pr-f-protocol-private-verify
	$(DTA_V21_LIVE_CLI) retry-admit \
		--repository-root "$(PROJECT_ROOT)" \
		--private-root "$(DTA_V21_ACCEPTED_PRIVATE_ROOT)"

dta-v21-pr-f-reconciliation-private-verify: dta-v21-pr-f-protocol-private-verify
	$(DTA_V21_LIVE_CLI) verify-reconciliation \
		--repository-root "$(PROJECT_ROOT)" \
		--private-root "$(DTA_V21_ACCEPTED_PRIVATE_ROOT)"

dta-v21-live-demo: dta-v21-historical-verify
	@test -n "$(DTA_V21_ACCEPTED_PRIVATE_ROOT)" || { echo "DTA_V21_ACCEPTED_PRIVATE_ROOT is required" >&2; exit 2; }
	@test -n "$(DTA_V21_PROVIDER_ENV)" || { echo "DTA_V21_PROVIDER_ENV is required" >&2; exit 2; }
	@test "$(DTA_V21_LIVE_EXECUTE)" = "USER_EXPLICIT_DTA_V21_PRF_RESOURCE_RECOVERY_AMENDMENT" || { echo "exact DTA_V21_LIVE_EXECUTE confirmation is required" >&2; exit 2; }
	@test "$(DTA_V21_RETRY_EXECUTE)" = "USER_EXPLICIT_DTA_V21_PRF_APPEND_ONLY_RECONCILIATION_AND_ONE_RETRY" || { echo "exact DTA_V21_RETRY_EXECUTE confirmation is required" >&2; exit 2; }
	$(DTA_V21_LIVE_CLI) execute \
		--repository-root "$(PROJECT_ROOT)" \
		--private-root "$(DTA_V21_ACCEPTED_PRIVATE_ROOT)" \
		--provider-env "$(DTA_V21_PROVIDER_ENV)"

dta-v21-live-report: dta-v21-historical-verify
	@test -n "$(DTA_V21_ACCEPTED_PRIVATE_ROOT)" || { echo "DTA_V21_ACCEPTED_PRIVATE_ROOT is required" >&2; exit 2; }
	$(DTA_V21_LIVE_CLI) report \
		--repository-root "$(PROJECT_ROOT)" \
		--private-root "$(DTA_V21_ACCEPTED_PRIVATE_ROOT)"

dta-v21-live-finalize: dta-v21-live-verify
	@test -n "$(DTA_V21_EXACT_HEAD_CI_SHA)" || { echo "DTA_V21_EXACT_HEAD_CI_SHA is required" >&2; exit 2; }
	@test -n "$(DTA_V21_INDEPENDENT_REVIEW_HEAD)" || { echo "DTA_V21_INDEPENDENT_REVIEW_HEAD is required" >&2; exit 2; }
	@test "$(DTA_V21_INDEPENDENT_REVIEW_CONFIRMATION)" = "MUST_FIX_0_CLAIM_ACCURACY_PASS" || { echo "exact independent review confirmation is required" >&2; exit 2; }
	@test -n "$(DTA_V21_ACTIVE_PR)" || { echo "DTA_V21_ACTIVE_PR is required" >&2; exit 2; }
	$(DTA_V21_LIVE_CLI) finalize \
		--repository-root "$(PROJECT_ROOT)" \
		--exact-head-ci-sha "$(DTA_V21_EXACT_HEAD_CI_SHA)" \
		--independent-review-head "$(DTA_V21_INDEPENDENT_REVIEW_HEAD)" \
		--independent-review-confirmation "$(DTA_V21_INDEPENDENT_REVIEW_CONFIRMATION)" \
		--active-pr "$(DTA_V21_ACTIVE_PR)"

dta-v21-live-closeout: dta-v21-live-verify
	@test -n "$(DTA_V21_EXACT_HEAD_CI_SHA)" || { echo "DTA_V21_EXACT_HEAD_CI_SHA is required" >&2; exit 2; }
	@test -n "$(DTA_V21_INDEPENDENT_REVIEW_HEAD)" || { echo "DTA_V21_INDEPENDENT_REVIEW_HEAD is required" >&2; exit 2; }
	@test "$(DTA_V21_INDEPENDENT_REVIEW_CONFIRMATION)" = "MUST_FIX_0_CLAIM_ACCURACY_PASS" || { echo "exact independent review confirmation is required" >&2; exit 2; }
	$(DTA_V21_LIVE_CLI) closeout \
		--repository-root "$(PROJECT_ROOT)" \
		--exact-head-ci-sha "$(DTA_V21_EXACT_HEAD_CI_SHA)" \
		--independent-review-head "$(DTA_V21_INDEPENDENT_REVIEW_HEAD)" \
		--independent-review-confirmation "$(DTA_V21_INDEPENDENT_REVIEW_CONFIRMATION)"

dta-v21-live-verify: dta-v21-pr-f-protocol-verify
	$(DTA_V21_LIVE_VERIFY_CLI) --project-root "$(PROJECT_ROOT)"

# Safe public surface: deterministic replay plus checked-in report verification.
# It never invokes the Provider or local Docker execution commands above.
dta-v21-demo: dta-v21-replay-verify dta-v21-live-verify

dta-v21-verify: dta-v21-test dta-v21-replay-verify \
	dta-v21-development-verify dta-v21-held-out-report-verify \
	dta-v21-pr-f-protocol-verify dta-v21-live-verify
# END DTA_V21_SUCCESSOR_TARGETS
