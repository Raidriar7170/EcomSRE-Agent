"""Deterministic paired-seed materialization for frozen public anchors."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from ecomsre.backends.replay import ReplayCase, load_replay_case

from scripts.phase5b_execution.contracts import canonical_json_bytes


PUBLIC_ANCHOR_ROOTS = {
    "ad-partial-failure-complete": Path(
        "config/phase1/replay-cases/agent-visible"
    ),
    "ad-partial-failure-without-logs": Path(
        "config/phase1/replay-cases/agent-visible"
    ),
    "ad-partial-failure-frontend-decoy": Path(
        "config/phase1/replay-cases/agent-visible"
    ),
    "recommendation-cache-failure": Path(
        "config/phase1/replay-cases/agent-visible"
    ),
    "recommendation-feature-evidence-insufficient": Path(
        "config/phase4/replay-cases/agent-visible"
    ),
    "ranking-change-with-normal-search-sli": Path(
        "config/phase4/replay-cases/agent-visible"
    ),
}
_DATA_FILENAMES = (
    "incident.json",
    "metrics.json",
    "logs.json",
    "traces.json",
    "changes.json",
)
_OPAQUE_IDENTIFIER_KEYS = frozenset(
    {
        "incident_id",
        "change_id",
        "request_id",
        "trace_id",
        "span_id",
        "deployment_id",
        "correlation_id",
    }
)
_TIMESTAMP_KEYS = frozenset({"started_at", "ended_at"})
_DERIVED_EVIDENCE_IDENTITY_KEYS = frozenset(
    {"raw_artifact_sha256", "raw_index", "raw_artifact_indices"}
)


def _seed_digest(template_id: str, seed_id: str) -> bytes:
    return hashlib.sha256(
        b"phase5b.v1\0"
        + template_id.encode("utf-8")
        + b"\0"
        + seed_id.encode("utf-8")
    ).digest()


def _opaque_identifier(
    *,
    key: str,
    original: str,
    template_id: str,
    seed_id: str,
) -> str:
    digest = hashlib.sha256(
        b"phase5b.v1\0opaque\0"
        + template_id.encode("utf-8")
        + b"\0"
        + seed_id.encode("utf-8")
        + b"\0"
        + key.encode("utf-8")
        + b"\0"
        + original.encode("utf-8")
    ).hexdigest()
    return f"opaque-{digest[:24]}"


def _shift_timestamp(value: str, offset_seconds: int) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("public anchor timestamps must be UTC")
    shifted = parsed + timedelta(seconds=offset_seconds)
    return shifted.isoformat().replace("+00:00", "Z")


def _transform_value(
    value: object,
    *,
    template_id: str,
    seed_id: str,
    offset_seconds: int,
    parent_key: str | None = None,
) -> object:
    if isinstance(value, dict):
        transformed: dict[str, object] = {}
        for key, item in value.items():
            if key in _OPAQUE_IDENTIFIER_KEYS and isinstance(item, str):
                transformed[key] = _opaque_identifier(
                    key=key,
                    original=item,
                    template_id=template_id,
                    seed_id=seed_id,
                )
            elif key in _TIMESTAMP_KEYS and isinstance(item, str):
                transformed[key] = _shift_timestamp(item, offset_seconds)
            else:
                transformed[key] = _transform_value(
                    item,
                    template_id=template_id,
                    seed_id=seed_id,
                    offset_seconds=offset_seconds,
                    parent_key=key,
                )
        return transformed
    if isinstance(value, list):
        transformed_items = [
            _transform_value(
                item,
                template_id=template_id,
                seed_id=seed_id,
                offset_seconds=offset_seconds,
                parent_key=parent_key,
            )
            for item in value
        ]
        if parent_key == "observations" and len(transformed_items) > 1:
            rotation = _seed_digest(template_id, seed_id)[4] % len(
                transformed_items
            )
            transformed_items = (
                transformed_items[rotation:] + transformed_items[:rotation]
            )
        return transformed_items
    return value


def _load_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("public anchor JSON must be an object")
    return payload


def materialize_public_instance(
    project_root: Path,
    destination_root: Path,
    template_id: str,
    seed_id: str,
) -> Path:
    try:
        relative_source_root = PUBLIC_ANCHOR_ROOTS[template_id]
    except KeyError as error:
        raise ValueError("template is not a frozen public anchor") from error
    if seed_id not in {f"seed-{index:02d}" for index in range(5)}:
        raise ValueError("seed is not in the frozen paired set")
    source_root = Path(project_root) / relative_source_root
    load_replay_case(source_root, template_id)
    destination = Path(destination_root) / template_id / seed_id
    if destination.exists():
        raise FileExistsError(destination)
    destination.mkdir(parents=True)
    digest = _seed_digest(template_id, seed_id)
    offset_seconds = int.from_bytes(digest[:4], "big") % 86_400
    hashes: dict[str, str] = {}
    for filename in _DATA_FILENAMES:
        source_payload = _load_object(source_root / template_id / filename)
        transformed = _transform_value(
            source_payload,
            template_id=template_id,
            seed_id=seed_id,
            offset_seconds=offset_seconds,
        )
        content = canonical_json_bytes(transformed)
        (destination / filename).write_bytes(content)
        hashes[filename] = hashlib.sha256(content).hexdigest()
    manifest = {
        "case_id": seed_id,
        "files": hashes,
        "schema_version": "phase1.replay-manifest.v1",
    }
    (destination / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    load_replay_case(destination.parent, destination.name)
    return destination


def _semantic_value(value: object, *, parent_key: str | None = None) -> object:
    if isinstance(value, dict):
        if (
            parent_key == "attributes"
            and value.get("name") in _OPAQUE_IDENTIFIER_KEYS
            and "value" in value
        ):
            return {**value, "value": "<opaque>"}
        projected: dict[str, object] = {}
        for key, item in value.items():
            if (
                key == "case_id"
                or key in _OPAQUE_IDENTIFIER_KEYS
                or key in _DERIVED_EVIDENCE_IDENTITY_KEYS
            ):
                projected[key] = "<opaque>"
            elif key in _TIMESTAMP_KEYS:
                projected[key] = "<timestamp>"
            else:
                projected[key] = _semantic_value(item, parent_key=key)
        return projected
    if isinstance(value, list):
        items = [_semantic_value(item, parent_key=parent_key) for item in value]
        if parent_key == "observations":
            return sorted(
                items,
                key=lambda item: json.dumps(
                    item,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        return items
    return value


def semantic_projection(replay_case: ReplayCase) -> object:
    return _semantic_value(replay_case.model_dump(mode="json"))
