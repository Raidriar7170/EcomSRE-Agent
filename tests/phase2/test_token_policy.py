"""Frozen offline tokenizer policy tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import socket
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
import tiktoken.load as tiktoken_load

from ecomsre.phase2.contracts import (
    ModelAllowedActions,
    ModelInputEnvelope,
    ModelOperation,
    Phase2FailureCode,
)
from ecomsre.phase2.token_policy import (
    ENCODING_ASSET_BYTES,
    ENCODING_ASSET_PATH,
    ENCODING_ASSET_SHA256,
    ENCODING_CONSTRUCTOR_SOURCE_URL,
    ENCODING_NAME,
    ENCODING_PAT_STR,
    ENCODING_SOURCE_URL,
    EXPECTED_MINIMUM_COMPLETION_TOKENS,
    EXPECTED_MODEL_TO_ENCODING,
    EXPECTED_SPECIAL_TOKENS,
    EXPECTED_SYSTEM_INSTRUCTION_SHA256,
    MODEL_SNAPSHOT,
    POLICY_PATH,
    SCHEMA_VERSION,
    TIKTOKEN_RELEASE_URL,
    TIKTOKEN_TAG_COMMIT,
    TIKTOKEN_VERSION,
    TOKEN_POLICY_CORE_SHA256,
    EXPECTED_GOLDEN_KEYS,
    TOKEN_GOLDEN_FIXTURE_DIR,
    TOKEN_GOLDEN_MANIFEST_PATH,
    TokenPolicyCore,
    TokenPolicyError,
    acquire_tokenizer_asset,
    build_local_encoding,
    canonical_json_bytes,
    load_token_authority,
    load_offline_tokenizer,
    load_token_policy_core,
    regenerate_token_goldens,
)
import tiktoken


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_golden_authority_has_six_closed_one_way_bound_keys() -> None:
    authority = load_token_authority(PROJECT_ROOT)

    assert tuple(entry.key for entry in authority.goldens.entries) == (
        EXPECTED_GOLDEN_KEYS
    )
    assert all(
        entry.token_policy_core_sha256 == authority.core_sha256
        for entry in authority.goldens.entries
    )
    assert len(authority.minimal_requests) == 6
    assert len(authority.minimal_responses) == 6
    assert all(
        b"token_golden_manifest_sha256" not in canonical_json_bytes(request)
        for request in authority.minimal_requests.values()
    )


def test_first_judge_capabilities_have_distinct_schema_and_goldens() -> None:
    authority = load_token_authority(PROJECT_ROOT)
    final_only = authority.golden(
        ModelOperation.FIRST_JUDGE_MODEL,
        ModelAllowedActions.FINAL_ONLY,
    )
    union = authority.golden(
        ModelOperation.FIRST_JUDGE_MODEL,
        ModelAllowedActions.FINAL_OR_REFINEMENT,
    )

    assert final_only.response_schema_sha256 != union.response_schema_sha256
    assert final_only.minimal_response_sha256 != union.minimal_response_sha256
    assert final_only.minimum_completion_tokens == 256
    assert union.minimum_completion_tokens == 512


def test_judge_instructions_expose_non_schema_phase1_decision_semantics() -> None:
    authority = load_token_authority(PROJECT_ROOT)
    judge_keys = (
        (ModelOperation.FIRST_JUDGE_MODEL, ModelAllowedActions.FINAL_ONLY),
        (
            ModelOperation.FIRST_JUDGE_MODEL,
            ModelAllowedActions.FINAL_OR_REFINEMENT,
        ),
        (ModelOperation.FINAL_JUDGE_MODEL, ModelAllowedActions.FINAL_ONLY),
    )

    for key in judge_keys:
        instruction = authority.envelopes[key].system_instruction
        assert "RCA_CONFIRMED requires" in instruction
        assert "two distinct Evidence sources" in instruction
        assert "empty missing_evidence" in instruction
        assert "NEED_MORE_EVIDENCE requires" in instruction
        assert "ABSTAIN requires" in instruction
        assert "Copy run_id, incident_id, judge_request_id" in instruction
        assert "finding_ids_considered exactly" in instruction


def test_specialist_instruction_exposes_identity_and_read_only_text_rules() -> None:
    authority = load_token_authority(PROJECT_ROOT)
    instruction = authority.envelopes[
        (ModelOperation.SPECIALIST_MODEL, ModelAllowedActions.FINDING_ONLY)
    ].system_instruction

    assert "Copy run_id, incident_id, plan_id, node_id" in instruction
    assert "evidence_refs may contain only exact visible refs" in instruction
    assert "Never put evidence:// strings" in instruction
    assert "tool or command names" in instruction


def test_all_twelve_fixtures_are_canonical_complete_and_hash_bound() -> None:
    authority = load_token_authority(PROJECT_ROOT)
    fixture_paths = {
        path
        for entry in authority.goldens.entries
        for path in (entry.minimal_request_path, entry.minimal_response_path)
    }

    assert len(fixture_paths) == 12
    assert fixture_paths == {
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / TOKEN_GOLDEN_FIXTURE_DIR).glob("*.json")
    }
    for relative_path in fixture_paths:
        payload = (PROJECT_ROOT / relative_path).read_bytes()
        parsed = json.loads(payload)
        assert payload == canonical_json_bytes(parsed)
        assert hashlib.sha256(payload).hexdigest() in {
            digest
            for entry in authority.goldens.entries
            for digest in (
                entry.minimal_request_sha256,
                entry.minimal_response_sha256,
            )
        }


def test_envelope_is_nested_exact_and_rejects_json_string_or_extra_field() -> None:
    authority = load_token_authority(PROJECT_ROOT)
    envelope = authority.envelopes[
        (ModelOperation.COMMANDER_MODEL, ModelAllowedActions.PLAN_ONLY)
    ]

    assert type(envelope.request) is dict
    assert type(envelope.response_schema) is dict
    assert envelope.response_schema == {
        "schema_version": "phase2.response-schema-envelope.v1",
        "dialect": "https://json-schema.org/draft/2020-12/schema",
        "schema": envelope.response_schema["schema"],
    }
    payload = envelope.model_dump(mode="json")
    with pytest.raises(ValidationError):
        ModelInputEnvelope.model_validate({**payload, "request": "{}"})
    with pytest.raises(ValidationError):
        ModelInputEnvelope.model_validate({**payload, "unexpected": True})


def test_regeneration_is_twice_identical_offline_and_refuses_silent_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_path = tmp_path / POLICY_PATH
    asset_path = tmp_path / ENCODING_ASSET_PATH
    policy_path.parent.mkdir(parents=True)
    asset_path.parent.mkdir(parents=True)
    shutil.copyfile(PROJECT_ROOT / POLICY_PATH, policy_path)
    shutil.copyfile(PROJECT_ROOT / ENCODING_ASSET_PATH, asset_path)

    def reject_network(*args: object, **kwargs: object) -> None:
        del args, kwargs
        pytest.fail("golden generation attempted network access")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(urllib.request, "urlopen", reject_network)
    monkeypatch.setattr(tiktoken, "get_encoding", reject_network)
    monkeypatch.setattr(tiktoken, "encoding_for_model", reject_network)

    regenerate_token_goldens(
        tmp_path,
        expected_core_sha256=TOKEN_POLICY_CORE_SHA256,
    )
    first = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in (
            tmp_path / TOKEN_GOLDEN_MANIFEST_PATH,
            *(tmp_path / TOKEN_GOLDEN_FIXTURE_DIR).glob("*.json"),
        )
    }
    regenerate_token_goldens(
        tmp_path,
        expected_core_sha256=TOKEN_POLICY_CORE_SHA256,
    )
    second = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in (
            tmp_path / TOKEN_GOLDEN_MANIFEST_PATH,
            *(tmp_path / TOKEN_GOLDEN_FIXTURE_DIR).glob("*.json"),
        )
    }
    assert first == second
    assert len(second) == 13
    load_token_authority(tmp_path)

    drifted = tmp_path / next(iter(first))
    drifted.write_bytes(drifted.read_bytes() + b" ")
    with pytest.raises(TokenPolicyError) as captured:
        regenerate_token_goldens(
            tmp_path,
            expected_core_sha256=TOKEN_POLICY_CORE_SHA256,
        )
    assert captured.value.code is Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH


def test_manifest_is_one_way_and_detects_entry_drift(tmp_path: Path) -> None:
    policy_path, asset_path = _write_policy_tree(tmp_path)
    del policy_path, asset_path
    shutil.copytree(
        PROJECT_ROOT / TOKEN_GOLDEN_FIXTURE_DIR,
        tmp_path / TOKEN_GOLDEN_FIXTURE_DIR,
    )
    manifest_path = tmp_path / TOKEN_GOLDEN_MANIFEST_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(PROJECT_ROOT / TOKEN_GOLDEN_MANIFEST_PATH, manifest_path)

    assert "token_golden_manifest_sha256" not in (
        PROJECT_ROOT / POLICY_PATH
    ).read_text(encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "token_golden_manifest_sha256" not in manifest
    manifest["entries"][0]["envelope_sha256"] = "0" * 64
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    with pytest.raises(TokenPolicyError) as captured:
        load_token_authority(tmp_path)
    assert captured.value.code is Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH


def _policy_payload() -> dict[str, Any]:
    return json.loads((PROJECT_ROOT / POLICY_PATH).read_text(encoding="utf-8"))


def _write_policy_tree(
    root: Path,
    *,
    copy_asset: bool = True,
) -> tuple[Path, Path]:
    policy_path = root / POLICY_PATH
    asset_path = root / ENCODING_ASSET_PATH
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        json.dumps(_policy_payload(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    asset_path.parent.mkdir(parents=True)
    if copy_asset:
        shutil.copyfile(PROJECT_ROOT / ENCODING_ASSET_PATH, asset_path)
    return policy_path, asset_path


def test_core_policy_exact_authorities_and_digest() -> None:
    core = load_token_policy_core(PROJECT_ROOT)

    assert core.schema_version == SCHEMA_VERSION == "phase2.token-policy-core.v1"
    assert core.model_snapshot == MODEL_SNAPSHOT == "gpt-5.4-mini-2026-03-17"
    assert core.model_to_encoding == EXPECTED_MODEL_TO_ENCODING == {
        MODEL_SNAPSHOT: "o200k_base"
    }
    assert core.tiktoken_version == TIKTOKEN_VERSION == "0.13.0"
    assert TIKTOKEN_RELEASE_URL == (
        "https://github.com/openai/tiktoken/releases/tag/0.13.0"
    )
    assert TIKTOKEN_TAG_COMMIT == "fa8b65d062fb6a656ac3810c89efde4c8ab999e2"
    assert core.encoding_name == ENCODING_NAME == "o200k_base"
    assert ENCODING_SOURCE_URL == (
        "https://openaipublic.blob.core.windows.net/encodings/"
        "o200k_base.tiktoken"
    )
    assert ENCODING_CONSTRUCTOR_SOURCE_URL == (
        "https://github.com/openai/tiktoken/blob/0.13.0/"
        "tiktoken_ext/openai_public.py#L95-L120"
    )
    assert core.encoding_asset_path == ENCODING_ASSET_PATH
    assert core.encoding_asset_bytes == ENCODING_ASSET_BYTES == 3_613_922
    assert core.encoding_asset_sha256 == ENCODING_ASSET_SHA256 == (
        "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
    )
    assert core.encoding_pat_str == ENCODING_PAT_STR
    assert core.encoding_special_tokens == EXPECTED_SPECIAL_TOKENS == {
        "<|endofprompt|>": 200018,
        "<|endoftext|>": 199999,
    }
    assert core.allowed_special == ()
    assert core.disallowed_special == "all"
    assert core.minimum_completion_tokens == EXPECTED_MINIMUM_COMPLETION_TOKENS
    assert core.system_instruction_sha256 == EXPECTED_SYSTEM_INSTRUCTION_SHA256
    assert hashlib.sha256(canonical_json_bytes(core.model_dump())).hexdigest() == (
        TOKEN_POLICY_CORE_SHA256
    )


def test_canonical_json_rule_is_exact_utf8_sorted_compact_and_finite() -> None:
    assert canonical_json_bytes({"z": "\u96ea", "a": 1}) == (
        '{"a":1,"z":"\u96ea"}'.encode()
    )
    with pytest.raises(TokenPolicyError) as captured:
        canonical_json_bytes({"bad": float("nan")})
    assert captured.value.code is Phase2FailureCode.TOKEN_CANONICALIZATION_FAILED


def test_core_policy_constructs_o200k_only_from_verified_local_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("runtime attempted network access"),
    )
    monkeypatch.setattr(
        tiktoken,
        "get_encoding",
        lambda *args, **kwargs: pytest.fail("runtime used tiktoken registry"),
    )
    monkeypatch.setattr(
        tiktoken,
        "encoding_for_model",
        lambda *args, **kwargs: pytest.fail("runtime inferred model mapping"),
    )

    policy = load_token_policy_core(PROJECT_ROOT)
    encoding = build_local_encoding(policy, PROJECT_ROOT)
    assert encoding.name == "phase2_o200k_base_offline"
    assert encoding.encode("bounded replay")


def test_parser_rejects_bytes_changed_after_detached_filesystem_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset_bytes = bytearray((PROJECT_ROOT / ENCODING_ASSET_PATH).read_bytes())
    asset_bytes[-1] ^= 1
    monkeypatch.setattr(
        tiktoken_load,
        "read_file",
        lambda path: bytes(asset_bytes),
    )

    policy = load_token_policy_core(PROJECT_ROOT)
    with pytest.raises(TokenPolicyError) as captured:
        build_local_encoding(policy, PROJECT_ROOT)
    assert captured.value.code is Phase2FailureCode.TOKENIZER_ASSET_HASH_MISMATCH


@pytest.mark.parametrize(
    ("mutation", "code"),
    (
        ("missing", Phase2FailureCode.TOKENIZER_ASSET_MISSING),
        ("size", Phase2FailureCode.TOKENIZER_ASSET_SIZE_MISMATCH),
        ("hash", Phase2FailureCode.TOKENIZER_ASSET_HASH_MISMATCH),
        ("version", Phase2FailureCode.TOKENIZER_VERSION_MISMATCH),
        ("model", Phase2FailureCode.TOKEN_MODEL_MAPPING_MISMATCH),
        ("constructor", Phase2FailureCode.TOKEN_POLICY_CORE_HASH_MISMATCH),
    ),
)
def test_policy_mismatch_fails_before_registry_or_network(
    mutation: str,
    code: Phase2FailureCode,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_path, asset_path = _write_policy_tree(
        tmp_path,
        copy_asset=mutation != "missing",
    )
    if mutation == "size":
        asset_path.write_bytes(asset_path.read_bytes()[:-1])
    elif mutation == "hash":
        asset_bytes = bytearray(asset_path.read_bytes())
        asset_bytes[-1] ^= 1
        asset_path.write_bytes(asset_bytes)
    elif mutation in {"version", "model", "constructor"}:
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
        if mutation == "version":
            payload["tiktoken_version"] = "0.12.0"
        elif mutation == "model":
            payload["model_to_encoding"][MODEL_SNAPSHOT] = "cl100k_base"
        else:
            payload["encoding_pat_str"] += "x"
        policy_path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("failure path attempted network"),
    )
    monkeypatch.setattr(
        tiktoken,
        "get_encoding",
        lambda *args, **kwargs: pytest.fail("failure path used registry"),
    )
    monkeypatch.setattr(
        tiktoken,
        "encoding_for_model",
        lambda *args, **kwargs: pytest.fail("failure path inferred mapping"),
    )

    with pytest.raises(TokenPolicyError) as captured:
        load_offline_tokenizer(tmp_path)
    assert captured.value.code is code


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("encoding_asset_bytes", True),
        ("encoding_asset_bytes", "3613922"),
        ("encoding_special_tokens", []),
        ("encoding_special_tokens", {"<|endoftext|>": True}),
        ("minimum_completion_tokens", []),
        ("minimum_completion_tokens", {"SINGLE_AGENT_MODEL/PHASE1_ACTION_CATALOG": "512"}),
        ("allowed_special", False),
        ("disallowed_special", True),
    ),
)
def test_core_policy_rejects_bool_string_and_container_coercion(
    field: str,
    value: object,
) -> None:
    payload = _policy_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        TokenPolicyCore.model_validate(payload)


def test_core_policy_rejects_extra_or_open_map_keys() -> None:
    payload = _policy_payload()
    payload["unexpected"] = "not allowed"
    with pytest.raises(ValidationError):
        TokenPolicyCore.model_validate(payload)

    payload = _policy_payload()
    payload["minimum_completion_tokens"]["UNDECLARED/KEY"] = 1
    with pytest.raises(ValidationError):
        TokenPolicyCore.model_validate(payload)


def test_symlink_asset_is_not_runtime_authority(tmp_path: Path) -> None:
    policy_path, asset_path = _write_policy_tree(tmp_path, copy_asset=False)
    del policy_path
    asset_path.symlink_to(PROJECT_ROOT / ENCODING_ASSET_PATH)
    with pytest.raises(TokenPolicyError) as captured:
        load_offline_tokenizer(tmp_path)
    assert captured.value.code is Phase2FailureCode.TOKENIZER_ASSET_HASH_MISMATCH


def test_acquisition_retains_matching_destination_and_writes_safe_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_path, destination = _write_policy_tree(tmp_path)
    evidence = tmp_path / "artifacts/phase2/tokenizer-bootstrap.json"
    original_inode = destination.stat().st_ino
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("idempotent acquisition downloaded"),
    )

    acquire_tokenizer_asset(policy_path, destination, evidence)

    assert destination.stat().st_ino == original_inode
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["status"] == "VERIFIED"
    assert payload["source_url"] == ENCODING_SOURCE_URL
    assert payload["expected_bytes"] == payload["observed_bytes"] == 3_613_922
    assert payload["expected_sha256"] == payload["observed_sha256"]
    assert payload["tiktoken_version"] == "0.13.0"
    assert payload["tiktoken_tag_commit"] == TIKTOKEN_TAG_COMMIT
    assert payload["verified_at_utc"].endswith("Z")
    assert "raidriar" not in evidence.read_text(encoding="utf-8")


def test_acquisition_never_replaces_mismatched_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_path, destination = _write_policy_tree(tmp_path, copy_asset=False)
    destination.write_bytes(b"do not replace")
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("mismatch acquisition downloaded"),
    )

    with pytest.raises(TokenPolicyError) as captured:
        acquire_tokenizer_asset(
            policy_path,
            destination,
            tmp_path / "artifacts/phase2/tokenizer-bootstrap.json",
        )
    assert captured.value.code is Phase2FailureCode.TOKENIZER_ASSET_SIZE_MISMATCH
    assert destination.read_bytes() == b"do not replace"
