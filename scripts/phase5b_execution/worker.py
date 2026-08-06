"""Truth-free instance loading boundary for one scheduled execution run."""

from __future__ import annotations

from collections.abc import Mapping
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from typing import cast

from ecomsre.backends.replay import ReplayCase, load_replay_case
from ecomsre.phase5b.hidden_pack import load_agent_visible_instance

from scripts.phase5b_execution.contracts import (
    RawScoredRunRecord,
    ScoredRunRequest,
    canonical_json_bytes,
)
from scripts.phase5b_execution.public_instances import (
    PUBLIC_ANCHOR_ROOTS,
    materialize_public_instance,
)


_DENIED_ENVIRONMENT_MARKERS = (
    "GROUND_TRUTH",
    "HIDDEN_PACK_ROOT",
    "EVALUATOR_TRUTH",
    "BUILDER",
)


def sanitized_worker_environment(
    environment: Mapping[str, str],
) -> dict[str, str]:
    return {
        key: value
        for key, value in environment.items()
        if not any(marker in key.upper() for marker in _DENIED_ENVIRONMENT_MARKERS)
    }


def load_worker_instance(
    *,
    project_root: Path,
    request: ScoredRunRequest,
    environment: Mapping[str, str],
    materialized_root: Path,
) -> ReplayCase:
    if request.template_id in PUBLIC_ANCHOR_ROOTS:
        destination = materialized_root / request.template_id / request.seed_id
        if not destination.exists():
            destination = materialize_public_instance(
                project_root,
                materialized_root,
                request.template_id,
                request.seed_id,
            )
        return load_replay_case(destination.parent, destination.name)
    if not request.template_id.startswith("hidden-"):
        raise ValueError("scheduled template is neither public nor hidden")
    visible_value = environment.get("PHASE5B_AGENT_VISIBLE_ROOT")
    if not isinstance(visible_value, str) or not visible_value:
        raise ValueError("hidden worker requires PHASE5B_AGENT_VISIBLE_ROOT")
    return load_agent_visible_instance(
        Path(visible_value),
        request.template_id,
        request.seed_id,
    )


def _isolated_runner(project_root: Path) -> ModuleType:
    module_name = "_ecomsre_phase5b_execution_worker_runner"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    source = project_root / "eval/phase5b_execution/runner.py"
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise ImportError("Phase 5B isolated worker runner cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class IsolatedScheduledExecutor:
    """Invoke exactly one actual scored run inside the isolated worker."""

    def __init__(
        self,
        *,
        project_root: Path,
        environment: Mapping[str, str],
    ) -> None:
        self.project_root = Path(project_root).resolve(strict=True)
        self.environment = dict(environment)
        self.call_order: list[str] = []

    def __call__(self, request: ScoredRunRequest) -> RawScoredRunRecord:
        payload = {
            "mode": "run",
            "run_id": request.run_id,
            "template_id": request.template_id,
            "seed_id": request.seed_id,
            "variant": request.variant,
        }
        response = cast(
            dict[str, object],
            _isolated_runner(self.project_root).worker_request(
                self.project_root,
                payload,
                environment=self.environment,
            ),
        )
        record = parse_actual_worker_record(response)
        record.verify_record_sha256()
        if record.evidence_class != "ACTUAL_SCORED":
            raise ValueError("isolated actual worker returned non-scored evidence")
        self.call_order.append(request.run_id)
        return record


class IsolatedCanaryExecutor:
    """Invoke the one public unscored canary inside the same isolated worker."""

    def __init__(
        self,
        *,
        project_root: Path,
        environment: Mapping[str, str],
    ) -> None:
        self.project_root = Path(project_root).resolve(strict=True)
        self.environment = dict(environment)

    def __call__(self, request: ScoredRunRequest) -> RawScoredRunRecord:
        payload = {
            "mode": "canary",
            "run_id": request.run_id,
            "template_id": request.template_id,
            "seed_id": request.seed_id,
            "variant": request.variant,
        }
        response = cast(
            dict[str, object],
            _isolated_runner(self.project_root).worker_request(
                self.project_root,
                payload,
                environment=self.environment,
            ),
        )
        record = parse_actual_worker_record(response)
        if record.evidence_class != "UNSCORED_PROVIDER_CANARY":
            raise ValueError("isolated canary returned scored evidence")
        return record


def parse_actual_worker_record(
    response: dict[str, object],
) -> RawScoredRunRecord:
    record = RawScoredRunRecord.model_validate_json(
        canonical_json_bytes(response),
        strict=True,
    )
    record.verify_record_sha256()
    return record
