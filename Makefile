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

.PHONY: phase5a-test phase5a-compare phase5a-verify phase5a-demo \
	phase5a-provider-pilot

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

AGENT_DEMO_CLI := env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync python -m ecomsre.demo
AGENT_DEMO_REPORT ?= $(PROJECT_ROOT)/artifacts/demo/agent-mainline-v1-report.json

.PHONY: agent-demo

agent-demo: phase1-prerequisites
	$(AGENT_DEMO_CLI) run --output "$(AGENT_DEMO_REPORT)"
