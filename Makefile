PYTHONPATH := src
BOOTSTRAP_CLI := PYTHONPATH=$(PYTHONPATH) uv run python -m ecomsre.cli phase0
PHASE0_CLI := PYTHONPATH=$(PYTHONPATH) uv run --frozen --no-sync python -m ecomsre.cli phase0
export ECOMSRE_RUN_ID := $(value RUN_ID)

.PHONY: phase0-bootstrap phase0-preflight phase0-up phase0-health \
	phase0-inject phase0-reset phase0-status phase0-accept phase0-stop

phase0-bootstrap:
	$(BOOTSTRAP_CLI) bootstrap

phase0-preflight:
	$(PHASE0_CLI) preflight

phase0-up:
	$(PHASE0_CLI) up

phase0-health:
	$(PHASE0_CLI) health

phase0-inject:
	$(PHASE0_CLI) inject

phase0-reset:
	$(PHASE0_CLI) reset

phase0-status:
	$(PHASE0_CLI) status

phase0-accept:
	$(PHASE0_CLI) accept

phase0-stop:
	$(PHASE0_CLI) stop
