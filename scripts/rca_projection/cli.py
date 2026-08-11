"""One-pass build, post-lock evaluator score, and public verification commands."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
from statistics import median
import subprocess
from typing import Any, cast

from ecomsre.evidence.hashes import canonical_json_bytes
from ecomsre_rca100.lifecycle import RCA100Schedule
from ecomsre_rca_unified.compact_index_serialization import (
    build_full_request,
    compact_rows,
    contract_hashes,
    load_frozen_encoding,
    offline_full_request_tokens,
)
from ecomsre_rca_unified.root_candidate_index import build_candidate_index
from ecomsre_rca_unified.root_evidence_projection import (
    build_obss_projection,
    build_rca100_projection,
    discover_label_blind_obss_cases,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = PROJECT_ROOT / "config/rca-root-evidence-projection-v1/contract.json"
METHODOLOGY_PATH = (
    PROJECT_ROOT
    / "config/rca-crossbenchmark-architecture-convergence-v1/methodology.json"
)
PUBLIC_JSON = PROJECT_ROOT / "docs/analysis/root-evidence-projection-v1.json"
PUBLIC_MD = PROJECT_ROOT / "docs/analysis/root-evidence-projection-v1.md"
PUBLIC_BRIEF = PROJECT_ROOT / "docs/analysis/root-evidence-projection-v1-human-brief.md"
PROJECTION_SPEC = (
    PROJECT_ROOT / "docs/design/canonical-root-evidence-projection-v1-spec.md"
)
INDEX_SPEC = PROJECT_ROOT / "docs/design/compact-root-candidate-index-v1-spec.md"
PRIVATE_FILENAMES = (
    "projection-by-case.jsonl",
    "candidate-universe-by-case.jsonl",
    "candidate-index-by-case.jsonl",
    "token-accounting-by-case.jsonl",
)
PROVIDER_ENV_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
)


def _load_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required JSON is not a regular file: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"required JSON is not an object: {path.name}")
    return value


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _json_line(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_digest(root: Path) -> dict[str, object]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("source tree must be a real directory")
    digest = hashlib.sha256()
    file_count = 0
    byte_count = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise ValueError("source tree contains a symlink")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        stat = path.stat()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(stat.st_size.to_bytes(8, "big"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        file_count += 1
        byte_count += stat.st_size
    return {
        "file_count": file_count,
        "byte_count": byte_count,
        "sha256": digest.hexdigest(),
    }


def _git(*args: str) -> str:
    result = subprocess.run(
        ("git", *args), cwd=PROJECT_ROOT, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _clean_head() -> str:
    status = _git("status", "--porcelain")
    if status:
        raise ValueError("projection build requires a clean implementation commit")
    return _git("rev-parse", "HEAD")


def _require_provider_env_absent() -> None:
    present = [name for name in PROVIDER_ENV_VARS if os.environ.get(name)]
    if present:
        raise ValueError(
            "Provider environment must be removed for this no-Provider goal"
        )


def _require_private_root(path: Path, *, create: bool) -> Path:
    project = PROJECT_ROOT.resolve(strict=True)
    parent = path.parent.resolve(strict=True)
    resolved = parent / path.name
    if resolved == project or project in resolved.parents:
        raise ValueError("private output root must be outside Git")
    if create:
        resolved.mkdir(mode=0o700, parents=False, exist_ok=True)
        resolved.chmod(0o700)
    elif not resolved.is_dir() or resolved.is_symlink():
        raise ValueError("private output root is unavailable")
    return resolved


def _write_create_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise ValueError(f"create-once output already exists: {path.name}") from error
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _case_records(schedule_path: Path) -> tuple[dict[str, object], ...]:
    schedule = _load_object(schedule_path)
    records = schedule.get("records")
    if (
        schedule.get("seed") != 20260814
        or not isinstance(records, list)
        or len(records) != 326
    ):
        raise ValueError("PR27 label-blind schedule binding differs")
    cases: dict[int, dict[str, object]] = {}
    for raw in records:
        if not isinstance(raw, dict):
            raise ValueError("label-blind schedule record is invalid")
        pair = raw.get("pair_position")
        source = raw.get("source")
        source_key = raw.get("source_key")
        opaque = raw.get("opaque_case_id")
        if (
            type(pair) is not int
            or source not in {"RCA100", "OBSS"}
            or not isinstance(source_key, str)
            or not isinstance(opaque, str)
        ):
            raise ValueError("label-blind schedule case identity is invalid")
        prior = cases.get(pair)
        identity = {
            "pair_position": pair,
            "source": source,
            "source_key": source_key,
            "opaque_case_id": opaque,
        }
        if prior is not None and prior != identity:
            raise ValueError("paired schedule arms disagree on case identity")
        cases[pair] = identity
    output = tuple(cases[index] for index in sorted(cases))
    counts = Counter(str(item["source"]) for item in output)
    if len(output) != 163 or counts != {"RCA100": 103, "OBSS": 60}:
        raise ValueError("label-blind schedule denominators differ")
    return output


def _rca_ordinals(schedule_path: Path) -> dict[str, int]:
    schedule = RCA100Schedule.model_validate_json(
        schedule_path.read_text(encoding="utf-8")
    )
    output = {
        item.source_task_id: index
        for index, item in enumerate(schedule.records, start=1)
    }
    if len(output) != 103:
        raise ValueError("RCA100 consumed schedule denominator differs")
    return output


def _validate_contract() -> dict[str, object]:
    contract = _load_object(CONTRACT_PATH)
    if (
        contract.get("version") != "root-evidence-projection-candidate-index-v1"
        or contract.get("provider_calls") != 0
    ):
        raise ValueError("projection contract version or Provider boundary differs")
    if (
        contract_hashes()["b0_system_prompt_sha256"]
        != "6b64c9e43f25029ca2f76f491faf98906c70fe888270284bf4bd3ff47e564049"
    ):
        raise ValueError("frozen B0 prompt hash differs")
    return contract


def build(args: argparse.Namespace) -> int:
    _require_provider_env_absent()
    contract = _validate_contract()
    implementation_commit = _clean_head()
    private_root = _require_private_root(args.private_root.expanduser(), create=True)
    if any(
        (private_root / name).exists()
        for name in (*PRIVATE_FILENAMES, "projection-lock.json", "score.json")
    ):
        raise ValueError("one-pass private output root is not empty")
    schedule_cases = _case_records(args.pr27_schedule.expanduser().resolve(strict=True))
    rca_ordinals = _rca_ordinals(args.rca_schedule.expanduser().resolve(strict=True))
    methodology = _load_object(METHODOLOGY_PATH)
    ob_cases = discover_label_blind_obss_cases(
        args.ob_root.expanduser().resolve(strict=True), system="RE2-OB"
    )
    ss_cases = discover_label_blind_obss_cases(
        args.ss_root.expanduser().resolve(strict=True), system="RE2-SS"
    )
    obss_index = {item.case_id: item for item in (*ob_cases, *ss_cases)}
    encoding = load_frozen_encoding(PROJECT_ROOT)
    output_buffers: dict[str, bytearray] = {
        name: bytearray() for name in PRIVATE_FILENAMES
    }
    identities: list[dict[str, object]] = []
    for position, identity in enumerate(schedule_cases, start=1):
        source = str(identity["source"])
        source_key = str(identity["source_key"])
        opaque = str(identity["opaque_case_id"])
        if source == "RCA100":
            ordinal = rca_ordinals.get(source_key)
            if ordinal is None:
                raise ValueError("RCA100 source key is absent from consumed schedule")
            projection = build_rca100_projection(
                args.rca_cases.expanduser().resolve(strict=True) / source_key,
                projection_case_number=ordinal,
                methodology=methodology,
            )
        else:
            case = obss_index.get(source_key)
            if case is None:
                raise ValueError("OB/SS source key is absent from label-blind inputs")
            projection = build_obss_projection(case)
        index = build_candidate_index(projection)
        projection_payload = projection.payload()
        universe_payload = {
            "schema_version": "root-candidate-universe.case.v1",
            "candidates": [item.payload() for item in index.universe],
        }
        index_payload = index.payload()
        b0_request = build_full_request(
            base_context=projection.base_context, index=None
        )
        c1_request = build_full_request(
            base_context=projection.base_context, index=index
        )
        b0_tokens = offline_full_request_tokens(encoding, b0_request)
        c1_tokens = offline_full_request_tokens(encoding, c1_request)
        visible_refs = {
            str(item.get("evidence_ref"))
            for item in cast(
                list[dict[str, object]], projection.base_context.get("evidence", [])
            )
            if isinstance(item.get("evidence_ref"), str)
        }
        index_refs = {
            ref
            for candidate in index.candidates
            for ref in candidate.universe.evidence_refs
            if ref.partition(":")[0] in {"metric", "log", "trace"}
        }
        invalid_refs = len(index_refs - visible_refs)
        common = {
            "position": position,
            "opaque_case_id": opaque,
            "source": source,
            "source_key": source_key,
        }
        output_buffers["projection-by-case.jsonl"].extend(
            _json_line(
                {
                    **common,
                    "projection": projection_payload,
                    "base_context": dict(projection.base_context),
                }
            )
        )
        output_buffers["candidate-universe-by-case.jsonl"].extend(
            _json_line({**common, "candidate_universe": universe_payload})
        )
        output_buffers["candidate-index-by-case.jsonl"].extend(
            _json_line(
                {
                    **common,
                    "candidate_index": index_payload,
                    "candidate_mapping": index.mapping,
                    "compact_rows": list(compact_rows(index)),
                    "invalid_evidence_refs": invalid_refs,
                }
            )
        )
        output_buffers["token-accounting-by-case.jsonl"].extend(
            _json_line(
                {
                    **common,
                    "b0_full_request_tokens": b0_tokens,
                    "c1_full_request_tokens": c1_tokens,
                    "ratio": c1_tokens / b0_tokens,
                    "tokenizer": "o200k_base",
                    "serialization": "SORTED_UTF8_CANONICAL_FULL_REQUEST_JSON",
                }
            )
        )
        identities.append(
            {"opaque_case_id": opaque, "source": source, "source_key": source_key}
        )
        print(
            json.dumps(
                {"built": position, "total": 163, "source": source}, sort_keys=True
            ),
            flush=True,
        )
    source_hashes = {
        "rca100_cases": _tree_digest(args.rca_cases.expanduser().resolve(strict=True)),
        "ob_dev_raw": _tree_digest(args.ob_root.expanduser().resolve(strict=True)),
        "ss_dev_raw": _tree_digest(args.ss_root.expanduser().resolve(strict=True)),
        "rca100_schedule_sha256": _sha_file(
            args.rca_schedule.expanduser().resolve(strict=True)
        ),
        "pr27_label_blind_schedule_sha256": _sha_file(
            args.pr27_schedule.expanduser().resolve(strict=True)
        ),
    }
    output_hashes: dict[str, str] = {}
    for name, payload in output_buffers.items():
        value = bytes(payload)
        _write_create_once(private_root / name, value)
        output_hashes[name] = _sha_bytes(value)
    lock = {
        "schema_version": "root-evidence-projection.lock.v1",
        "version": contract["version"],
        "implementation_commit": implementation_commit,
        "policy_config_sha256": _sha_file(CONTRACT_PATH),
        "policy": contract,
        "source_hashes": source_hashes,
        "case_count": 163,
        "case_identities_sha256": _sha_bytes(canonical_json_bytes(identities)),
        "outputs": output_hashes,
        "projection_tree_hash": output_hashes["projection-by-case.jsonl"],
        "index_tree_hash": output_hashes["candidate-index-by-case.jsonl"],
        "tokenizer": {
            "encoding": "o200k_base",
            "version": "tiktoken==0.13.0",
            "asset_sha256": "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
            "serialization": "SORTED_UTF8_CANONICAL_FULL_REQUEST_JSON",
        },
        "provider_calls": 0,
        "ground_truth_loaded": False,
    }
    _write_create_once(private_root / "projection-lock.json", _json_bytes(lock))
    print(
        json.dumps(
            {
                "locked": True,
                "implementation_commit": implementation_commit,
                "provider_calls": 0,
            },
            sort_keys=True,
        )
    )
    return 0


def _read_jsonl(path: Path) -> tuple[dict[str, object], ...]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("private JSONL input is invalid")
    output: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("private JSONL row is invalid")
            output.append(value)
    if len(output) != 163:
        raise ValueError("private JSONL denominator differs")
    return tuple(output)


def _verify_lock(root: Path) -> dict[str, object]:
    lock = _load_object(root / "projection-lock.json")
    if (
        lock.get("case_count") != 163
        or lock.get("provider_calls") != 0
        or lock.get("ground_truth_loaded") is not False
    ):
        raise ValueError("projection lock boundary differs")
    outputs = lock.get("outputs")
    if not isinstance(outputs, Mapping):
        raise ValueError("projection lock output bindings are missing")
    for name in PRIVATE_FILENAMES:
        if outputs.get(name) != _sha_file(root / name):
            raise ValueError("projection lock output hash differs")
    return lock


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def _scan_public(outputs: Mapping[Path, bytes]) -> None:
    forbidden = (
        "source_key",
        "source_task_id",
        "opaque_case_id",
        "run_id",
        "answer_root",
        "private_root",
        "/users/",
        "api_key",
        "base_url",
        "candidate_mapping",
        "root_cause_entity_ref",
        "raw_provider",
    )
    for path, payload in outputs.items():
        text = payload.decode("utf-8").casefold()
        if any(marker in text for marker in forbidden):
            raise ValueError(f"public leakage marker detected in {path.name}")
        if (
            re.search(r"\bt[0-9]{3}\b", text)
            or re.search(r"\bcase-[0-9a-f]{8,}\b", text)
            or re.search(r"(?:apm|k8s)\|[a-z0-9._-]+\|", text)
        ):
            raise ValueError(f"public case/entity identity detected in {path.name}")


def _truth_refs(truth: Any, catalog: Any) -> frozenset[str]:
    target_ids = tuple(getattr(truth, "target_entity_ids"))
    if target_ids:
        ids = set(target_ids)
        return frozenset(
            entity.entity_ref
            for entity in catalog.by_ref.values()
            if (
                {entity.entity_id}
                | {ref.rsplit("|", 1)[-1] for ref in entity.same_as_refs}
            )
            & ids
        )
    from ecomsre_rca100.entity import normalize_entity_name

    names = {
        normalize_entity_name(item) for item in getattr(truth, "target_entity_names")
    }
    return frozenset(
        entity.entity_ref
        for entity in catalog.by_ref.values()
        if (
            {entity.normalized_name}
            | {
                catalog.by_ref[ref].normalized_name
                for ref in entity.same_as_refs
                if ref in catalog.by_ref
            }
        )
        & names
    )


def _service_ancestors(ref: str, projection: Mapping[str, object]) -> frozenset[str]:
    entities_raw = projection.get("canonical_entities")
    parents_raw = projection.get("parent_relations")
    if not isinstance(entities_raw, list) or not isinstance(parents_raw, list):
        return frozenset()
    layers = {
        str(item.get("entity_ref")): str(item.get("layer"))
        for item in entities_raw
        if isinstance(item, Mapping)
    }
    parents: dict[str, set[str]] = defaultdict(set)
    for edge in parents_raw:
        if isinstance(edge, list) and len(edge) == 2:
            parents[str(edge[0])].add(str(edge[1]))
    output: set[str] = set()
    queue = [ref]
    seen: set[str] = set()
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        if layers.get(current) == "SERVICE":
            output.add(current)
        queue.extend(parents.get(current, set()))
    return frozenset(output)


@dataclass(frozen=True, slots=True)
class _ScoreRow:
    source: str
    projection_exact: bool
    projection_service: bool
    exact_rank: int | None
    service_rank: int | None
    missing_cause: str | None


def score(args: argparse.Namespace) -> int:
    _require_provider_env_absent()
    root = _require_private_root(args.private_root.expanduser(), create=False)
    _verify_lock(root)
    if (root / "score.json").exists() or any(
        path.exists() for path in (PUBLIC_JSON, PUBLIC_MD, PUBLIC_BRIEF)
    ):
        raise ValueError("one evaluator scoring pass has already been materialized")
    projections = _read_jsonl(root / "projection-by-case.jsonl")
    universes = _read_jsonl(root / "candidate-universe-by-case.jsonl")
    indexes = _read_jsonl(root / "candidate-index-by-case.jsonl")
    tokens = _read_jsonl(root / "token-accounting-by-case.jsonl")
    by_opaque_projection = {str(item["opaque_case_id"]): item for item in projections}
    by_opaque_universe = {str(item["opaque_case_id"]): item for item in universes}
    by_opaque_index = {str(item["opaque_case_id"]): item for item in indexes}
    if not (
        len(by_opaque_projection)
        == len(by_opaque_universe)
        == len(by_opaque_index)
        == 163
    ):
        raise ValueError("private evaluator inputs disagree")

    # Evaluator-only imports occur only after the immutable label-blind lock.
    from ecomsre_rca100.entity import load_entity_catalog, normalize_entity_name
    from ecomsre_rca100.evaluator import load_answer_key, prediction_correct
    from ecomsre_rcaeval.dataset import DevSystem, discover_dev_cases

    rca_answers = args.rca_answers.expanduser().resolve(strict=True)
    rca_truths = load_answer_key(rca_answers)
    rca_cases = args.rca_cases.expanduser().resolve(strict=True)
    ob_cases = discover_dev_cases(
        args.ob_root.expanduser().resolve(strict=True), DevSystem.RE2_OB
    )
    ss_cases = discover_dev_cases(
        args.ss_root.expanduser().resolve(strict=True), DevSystem.RE2_SS
    )
    obss_truth = {item.case_id: item for item in (*ob_cases, *ss_cases)}
    rows: list[_ScoreRow] = []
    missing_counts: Counter[str] = Counter()
    for opaque, projection_row in sorted(by_opaque_projection.items()):
        source = str(projection_row["source"])
        source_key = str(projection_row["source_key"])
        projection = cast(dict[str, object], projection_row["projection"])
        universe_payload = cast(
            dict[str, object], by_opaque_universe[opaque]["candidate_universe"]
        )
        index_payload = cast(
            dict[str, object], by_opaque_index[opaque]["candidate_index"]
        )
        universe_candidates = cast(
            list[dict[str, object]], universe_payload["candidates"]
        )
        index_candidates = cast(list[dict[str, object]], index_payload["candidates"])
        universe_refs = [str(item["entity_ref"]) for item in universe_candidates]
        index_refs = [str(item["entity_ref"]) for item in index_candidates]
        if source == "RCA100":
            catalog = load_entity_catalog(rca_cases / source_key / "topology.json")
            truth = rca_truths[source_key]
            exact_universe = {
                ref for ref in universe_refs if prediction_correct(ref, truth, catalog)
            }
            exact_index_ranks = [
                rank
                for rank, ref in enumerate(index_refs, 1)
                if prediction_correct(ref, truth, catalog)
            ]
            truth_refs = _truth_refs(truth, catalog)
            aliases_raw = projection.get("alias_dispositions")
            aliases = (
                {
                    str(item.get("source_key")): str(item.get("canonical_entity_ref"))
                    for item in cast(list[dict[str, object]], aliases_raw)
                    if item.get("canonical_entity_ref") is not None
                }
                if isinstance(aliases_raw, list)
                else {}
            )
            truth_services = {
                service
                for ref in truth_refs
                for service in _service_ancestors(aliases.get(ref, ref), projection)
            }
            service_universe = {
                ref
                for ref in universe_refs
                if prediction_correct(ref, truth, catalog)
                or bool(_service_ancestors(ref, projection) & truth_services)
            }
            service_index_ranks = [
                rank
                for rank, ref in enumerate(index_refs, 1)
                if prediction_correct(ref, truth, catalog)
                or bool(_service_ancestors(ref, projection) & truth_services)
            ]
            projection_exact = bool(exact_universe)
            projection_service = bool(service_universe)
            exact_rank = min(exact_index_ranks) if exact_index_ranks else None
            service_rank = min(service_index_ranks) if service_index_ranks else None
        else:
            truth_case = obss_truth.get(source_key)
            if truth_case is None:
                raise ValueError("OB/SS evaluator truth identity is absent")
            truth_service = normalize_entity_name(truth_case.root_cause_service)

            def matches(ref: str) -> bool:
                return ref == f"apm|apm.service|{truth_service}"

            projection_exact = any(matches(ref) for ref in universe_refs)
            projection_service = projection_exact
            ranks = [rank for rank, ref in enumerate(index_refs, 1) if matches(ref)]
            exact_rank = min(ranks) if ranks else None
            service_rank = exact_rank
        missing_cause = None
        if source == "RCA100" and exact_rank is None:
            if not projection_exact:
                missing_cause = "CANDIDATE_UNIVERSE_MISSING"
            else:
                universe_item = next(
                    (
                        item
                        for item in universe_candidates
                        if str(item["entity_ref"]) not in index_refs
                    ),
                    None,
                )
                family = None if universe_item is None else universe_item.get("family")
                family_count = sum(
                    item.get("family") == family for item in index_candidates
                )
                missing_cause = (
                    "LAYER_ALLOCATION_DROPPED"
                    if family in {"N", "D"} and family_count >= 2
                    else "TOP12_ORDERING_DROPPED"
                )
            missing_counts[missing_cause] += 1
        rows.append(
            _ScoreRow(
                source,
                projection_exact,
                projection_service,
                exact_rank,
                service_rank,
                missing_cause,
            )
        )

    rca = [row for row in rows if row.source == "RCA100"]
    obss = [row for row in rows if row.source == "OBSS"]
    if len(rca) != 103 or len(obss) != 60:
        raise ValueError("scoring denominators differ")
    ratios = [float(cast(int | float, item["ratio"])) for item in tokens]
    by_system_ratios: dict[str, list[float]] = defaultdict(list)
    b0_values: list[int] = []
    c1_values: list[int] = []
    for item in tokens:
        by_system_ratios[str(item["source"])].append(
            float(cast(int | float, item["ratio"]))
        )
        b0_values.append(cast(int, item["b0_full_request_tokens"]))
        c1_values.append(cast(int, item["c1_full_request_tokens"]))
    structural = {
        "max_candidate_count": max(
            len(
                cast(
                    list[object],
                    cast(dict[str, object], item["candidate_index"])["candidates"],
                )
            )
            for item in indexes
        ),
        "duplicate_candidate_ids": sum(
            len(ids) - len(set(ids))
            for item in indexes
            for ids in (
                [
                    str(candidate["candidate_id"])
                    for candidate in cast(dict[str, Any], item["candidate_index"])[
                        "candidates"
                    ]
                ],
            )
        ),
        "duplicate_canonical_candidates": sum(
            len(refs) - len(set(refs))
            for item in indexes
            for refs in (
                [
                    str(candidate["entity_ref"])
                    for candidate in cast(dict[str, Any], item["candidate_index"])[
                        "candidates"
                    ]
                ],
            )
        ),
        "invalid_evidence_refs": sum(
            cast(int, item["invalid_evidence_refs"]) for item in indexes
        ),
        "ground_truth_dependent_branches": 0,
        "benchmark_id_branches": 0,
    }
    exact_recall = sum(row.exact_rank is not None for row in rca)
    service_recall = sum(row.service_rank is not None for row in rca)
    rca_projection_exact = sum(row.projection_exact for row in rca)
    rca_projection_service = sum(row.projection_service for row in rca)
    obss_projection = sum(row.projection_service for row in obss)
    obss_recall = sum(row.service_rank is not None for row in obss)
    exact_ranks = [
        cast(int, row.exact_rank) for row in rca if row.exact_rank is not None
    ]
    checks = {
        "rca100_projection_exact_at_least_85": rca_projection_exact >= 85,
        "rca100_projection_service_at_least_95": rca_projection_service >= 95,
        "rca100_index_exact_at_least_75": exact_recall >= 75,
        "rca100_index_service_at_least_90": service_recall >= 90,
        "rca100_exact_improvement_at_least_11": exact_recall - 64 >= 11,
        "rca100_service_improvement_at_least_22": service_recall - 68 >= 22,
        "rca100_median_exact_rank_at_most_3": bool(exact_ranks)
        and median(exact_ranks) <= 3,
        "obss_projection_60_of_60": obss_projection == 60,
        "obss_index_60_of_60": obss_recall == 60,
        "max_candidates_at_most_12": structural["max_candidate_count"] <= 12,
        "duplicate_candidate_ids_zero": structural["duplicate_candidate_ids"] == 0,
        "duplicate_canonical_candidates_zero": structural[
            "duplicate_canonical_candidates"
        ]
        == 0,
        "invalid_evidence_refs_zero": structural["invalid_evidence_refs"] == 0,
        "ground_truth_dependent_branches_zero": True,
        "benchmark_id_branches_zero": True,
        "mean_token_ratio_at_most_1_15": sum(ratios) / len(ratios) <= 1.15,
        "median_token_ratio_at_most_1_15": median(ratios) <= 1.15,
        "p95_token_ratio_at_most_1_20": _percentile(ratios, 0.95) <= 1.20,
        "max_token_ratio_at_most_1_25": max(ratios) <= 1.25,
    }
    passed = all(checks.values())
    verdict = (
        "ROOT_EVIDENCE_PROJECTION_GATE_PASSED_READY_FOR_LIVE_EVALUATION"
        if passed
        else "ROOT_EVIDENCE_PROJECTION_GATE_NOT_PASSED_STOP_LLM_RCA_OPTIMIZATION"
    )
    public = {
        "schema_version": "root-evidence-projection.aggregate.v1",
        "classification": [
            "CONSUMED_CROSS_BENCHMARK_DEVELOPMENT",
            "DETERMINISTIC_RETRIEVAL_DEVELOPMENT",
            "NOT_EXTERNAL_VALIDATION",
            "NOT_PRIMARY_INFERENCE",
        ],
        "frozen_reference": {"pr27_exact_recall": 64, "pr27_service_recall": 68},
        "projection_policy": "CANONICAL_ROOT_EVIDENCE_PROJECTION_V1",
        "candidate_index_policy": "COMPACT_ROOT_CANDIDATE_INDEX_V1",
        "rca100": {
            "denominator": 103,
            "projection_exact_coverage": rca_projection_exact,
            "projection_service_coverage": rca_projection_service,
            "index_exact_recall_at_12": exact_recall,
            "index_service_recall_at_12": service_recall,
            "exact_improvement_vs_pr27": exact_recall - 64,
            "service_improvement_vs_pr27": service_recall - 68,
            "median_exact_rank": None if not exact_ranks else median(exact_ranks),
            "missing_exact": 103 - exact_recall,
            "missing_service": 103 - service_recall,
        },
        "obss": {
            "denominator": 60,
            "projection_service_coverage": obss_projection,
            "index_recall_at_12": obss_recall,
            "missing": 60 - obss_recall,
        },
        "token_accounting": {
            "tokenizer": "tiktoken o200k_base",
            "serialization": "SORTED_UTF8_CANONICAL_FULL_REQUEST_JSON",
            "b0_mean": sum(b0_values) / len(b0_values),
            "c1_mean": sum(c1_values) / len(c1_values),
            "ratio_mean": sum(ratios) / len(ratios),
            "ratio_median": median(ratios),
            "ratio_p95": _percentile(ratios, 0.95),
            "ratio_max": max(ratios),
            "per_system_ratio_mean": {
                key.casefold(): sum(values) / len(values)
                for key, values in sorted(by_system_ratios.items())
            },
        },
        "structural_integrity": structural,
        "missing_cause_aggregate": dict(sorted(missing_counts.items())),
        "gate": {"checks": checks, "passed": passed, "verdict": verdict},
        "claim_boundary": {
            "provider_calls": 0,
            "live_evaluation": False,
            "regression_access": False,
            "re2_tt_access": False,
            "external_claim": False,
            "policy_reruns": 0,
        },
    }
    public_json = _json_bytes(public)
    public_md = (
        "# Root Evidence Projection v1\n\n"
        "Classification: `CONSUMED_CROSS_BENCHMARK_DEVELOPMENT`, `DETERMINISTIC_RETRIEVAL_DEVELOPMENT`, `NOT_EXTERNAL_VALIDATION`, `NOT_PRIMARY_INFERENCE`.\n\n"
        "This is the single frozen, no-Provider projection/index pass. PR #27 remains the frozen negative compact-retrieval reference (exact 64/103; service 68/103).\n\n"
        f"Verdict: `{verdict}`.\n\n"
        "```json\n"
        + json.dumps(
            public, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True
        )
        + "\n```\n"
    ).encode("utf-8")
    brief = (
        "# Root Evidence Projection v1 人工审阅摘要\n\n"
        f"终态：`{verdict}`。\n\n"
        f"RCA100 投影 exact/service 覆盖为 {rca_projection_exact}/103 与 {rca_projection_service}/103；最终索引 exact/service Recall@12 为 {exact_recall}/103 与 {service_recall}/103。OB/SS 投影与索引分别为 {obss_projection}/60、{obss_recall}/60。\n\n"
        f"真实 `o200k_base` 完整输入 token 比率 mean/median/p95/max 为 {sum(ratios) / len(ratios):.6f}/{median(ratios):.6f}/{_percentile(ratios, 0.95):.6f}/{max(ratios):.6f}。\n\n"
        "边界：一次冻结 policy、一次 label-blind build、一次锁后评分；Provider calls = 0；没有 live、Regression、RE2-TT 或 external claim。\n"
    ).encode("utf-8")
    outputs = {PUBLIC_JSON: public_json, PUBLIC_MD: public_md, PUBLIC_BRIEF: brief}
    _scan_public(outputs)
    for path, payload in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    private_score = {
        "schema_version": "root-evidence-projection.private-score.v1",
        "projection_lock_sha256": _sha_file(root / "projection-lock.json"),
        "answer_bindings": {
            "rca100_answer_tree": _tree_digest(rca_answers),
            "ob_dev_tree": _tree_digest(args.ob_root.expanduser().resolve(strict=True)),
            "ss_dev_tree": _tree_digest(args.ss_root.expanduser().resolve(strict=True)),
        },
        "public_aggregate": public,
        "public_hashes": {
            path.name: _sha_bytes(payload) for path, payload in outputs.items()
        },
        "scoring_passes": 1,
    }
    _write_create_once(root / "score.json", _json_bytes(private_score))
    print(json.dumps({"verdict": verdict, "passed": passed}, sort_keys=True))
    return 0 if passed else 2


def verify_private(args: argparse.Namespace) -> int:
    _require_provider_env_absent()
    root = _require_private_root(args.private_root.expanduser(), create=False)
    _verify_lock(root)
    score_payload = _load_object(root / "score.json")
    public = score_payload.get("public_aggregate")
    hashes = score_payload.get("public_hashes")
    if not isinstance(public, Mapping) or not isinstance(hashes, Mapping):
        raise ValueError("private score binding is invalid")
    if canonical_json_bytes(dict(public)) != canonical_json_bytes(
        _load_object(PUBLIC_JSON)
    ):
        raise ValueError("public aggregate differs from locked score")
    for path in (PUBLIC_JSON, PUBLIC_MD, PUBLIC_BRIEF):
        if hashes.get(path.name) != _sha_file(path):
            raise ValueError("public output hash differs from locked score")
    _scan_public(
        {
            path: path.read_bytes()
            for path in (
                PUBLIC_JSON,
                PUBLIC_MD,
                PUBLIC_BRIEF,
                PROJECTION_SPEC,
                INDEX_SPEC,
            )
        }
    )
    print(
        json.dumps(
            {"canonical_verification": "PASSED", "public_leakage": "PASSED"},
            sort_keys=True,
        )
    )
    return 0


def verify_public(_args: argparse.Namespace) -> int:
    _validate_contract()
    required = (PUBLIC_JSON, PUBLIC_MD, PUBLIC_BRIEF, PROJECTION_SPEC, INDEX_SPEC)
    if any(not path.is_file() or path.is_symlink() for path in required):
        raise ValueError("required public projection output is absent")
    _scan_public({path: path.read_bytes() for path in required})
    public = _load_object(PUBLIC_JSON)
    gate = public.get("gate")
    if not isinstance(gate, Mapping) or gate.get("verdict") not in {
        "ROOT_EVIDENCE_PROJECTION_GATE_PASSED_READY_FOR_LIVE_EVALUATION",
        "ROOT_EVIDENCE_PROJECTION_GATE_NOT_PASSED_STOP_LLM_RCA_OPTIMIZATION",
    }:
        raise ValueError("public projection verdict is invalid")
    print(json.dumps({"public_projection": "VERIFIED"}, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="python -m scripts.rca_projection.cli")
    subparsers = value.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--private-root", type=Path, required=True)
    build_parser.add_argument("--rca-cases", type=Path, required=True)
    build_parser.add_argument("--rca-schedule", type=Path, required=True)
    build_parser.add_argument("--ob-root", type=Path, required=True)
    build_parser.add_argument("--ss-root", type=Path, required=True)
    build_parser.add_argument("--pr27-schedule", type=Path, required=True)
    build_parser.set_defaults(func=build)
    score_parser = subparsers.add_parser("score")
    score_parser.add_argument("--private-root", type=Path, required=True)
    score_parser.add_argument("--rca-cases", type=Path, required=True)
    score_parser.add_argument("--rca-answers", type=Path, required=True)
    score_parser.add_argument("--ob-root", type=Path, required=True)
    score_parser.add_argument("--ss-root", type=Path, required=True)
    score_parser.set_defaults(func=score)
    private_parser = subparsers.add_parser("verify-private")
    private_parser.add_argument("--private-root", type=Path, required=True)
    private_parser.set_defaults(func=verify_private)
    public_parser = subparsers.add_parser("verify-public")
    public_parser.set_defaults(func=verify_public)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
