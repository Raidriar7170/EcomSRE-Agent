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
