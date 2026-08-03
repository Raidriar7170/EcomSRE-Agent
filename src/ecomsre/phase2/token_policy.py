"""Offline, hash-bound tokenizer policy for Phase 2 model preflight."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
import threading
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from pydantic import (
    Field,
    JsonValue,
    StrictInt,
    TypeAdapter,
    ValidationError,
    model_validator,
)
from tiktoken import Encoding
from tiktoken.load import load_tiktoken_bpe

from ecomsre.model.gateway import PHASE1_SYSTEM_INSTRUCTION, _tool_definitions
from ecomsre.phase1.contracts import (
    Action,
    Evidence,
    EvidenceAttribute,
    EvidenceSource,
    Incident,
    MetricsAction,
    ModelFunctionName,
    ModelRequest,
    Phase1Model,
    RCAResult,
    RCADecision,
    ReadOnlyToolName,
    RecommendedNextAction,
    RemainingBudgets,
    Severity,
    ToolCallRecord,
    TracesAction,
)
from ecomsre.phase2.contracts import (
    MODEL_OPERATION_ACTION_KEYS,
    AdmittedInvestigationGraph,
    AdmittedRefinementFragment,
    AdditionalInvestigationRequest,
    BudgetSnapshot,
    COMPARISON_MAX_TOTAL_TOKENS,
    CommanderRequest,
    FindingHypothesis,
    FirstJudgeAction,
    InvestigationNode,
    InvestigationPlan,
    JudgeFinalResult,
    JudgeRequest,
    ModelAllowedActions,
    ModelInputEnvelope,
    ModelOperation,
    Phase2FailureCode,
    Phase2Model,
    Phase2Variant,
    ResolvedEvidenceView,
    SourceCapability,
    SpecialistFinding,
    SpecialistModelRequest,
    SpecialistRole,
    SpecialistTask,
    Sha256,
    build_initial_admitted_graph,
)


SCHEMA_VERSION = "phase2.token-policy-core.v1"
MODEL_SNAPSHOT: Literal["gpt-5.4-mini-2026-03-17"] = "gpt-5.4-mini-2026-03-17"
TIKTOKEN_VERSION = "0.13.0"
TIKTOKEN_RELEASE_URL = "https://github.com/openai/tiktoken/releases/tag/0.13.0"
TIKTOKEN_TAG_COMMIT = "fa8b65d062fb6a656ac3810c89efde4c8ab999e2"
ENCODING_NAME = "o200k_base"
ENCODING_SOURCE_URL = (
    "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
)
ENCODING_CONSTRUCTOR_SOURCE_URL = (
    "https://github.com/openai/tiktoken/blob/0.13.0/"
    "tiktoken_ext/openai_public.py#L95-L120"
)
POLICY_PATH = Path("config/phase2/token-policy-core.json")
ENCODING_ASSET_PATH = "config/phase2/tokenizers/o200k_base.tiktoken"
ENCODING_ASSET_BYTES = 3_613_922
ENCODING_ASSET_SHA256 = (
    "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
)
ENCODING_PAT_STR = (
    r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*"
    r"[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?|"
    r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+"
    r"[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?|"
    r"\p{N}{1,3}| ?[^\s\p{L}\p{N}]+[\r\n/]*|\s*[\r\n]+|"
    r"\s+(?!\S)|\s+"
)
TOKEN_POLICY_CORE_SHA256 = (
    "9ced287ec7a424bb274bc5b5a7b33ba60a8435113db8815a743b05762fb0ee90"
)

EXPECTED_MODEL_TO_ENCODING = {MODEL_SNAPSHOT: ENCODING_NAME}
EXPECTED_SPECIAL_TOKENS = {
    "<|endofprompt|>": 200018,
    "<|endoftext|>": 199999,
}
EXPECTED_MINIMUM_COMPLETION_TOKENS = {
    "COMMANDER_MODEL/PLAN_ONLY": 320,
    "FINAL_JUDGE_MODEL/FINAL_ONLY": 256,
    "FIRST_JUDGE_MODEL/FINAL_ONLY": 256,
    "FIRST_JUDGE_MODEL/FINAL_OR_REFINEMENT": 512,
    "SINGLE_AGENT_MODEL/PHASE1_ACTION_CATALOG": 512,
    "SPECIALIST_MODEL/FINDING_ONLY": 256,
}
EXPECTED_SYSTEM_INSTRUCTION_SHA256 = {
    "COMMANDER_MODEL/PLAN_ONLY": (
        "f4734dcf4d9c9b01d2c3a048ba4dd9bcfb9ea8c6a5b876ab370163045a36475a"
    ),
    "FINAL_JUDGE_MODEL/FINAL_ONLY": (
        "291b13047e48ef5d134ae017e6c0f6ad496b9a063a52e58d9b596b67c86c6211"
    ),
    "FIRST_JUDGE_MODEL/FINAL_ONLY": (
        "2f35d626afacd432cd5f6bea7562788f7987a4a7962faa3b6978c573a3bc9181"
    ),
    "FIRST_JUDGE_MODEL/FINAL_OR_REFINEMENT": (
        "2f35d626afacd432cd5f6bea7562788f7987a4a7962faa3b6978c573a3bc9181"
    ),
    "SINGLE_AGENT_MODEL/PHASE1_ACTION_CATALOG": (
        "4c6b8d2559cb50706e40b10b3a3575f97314b885bd40785627b9126a0d14ce2f"
    ),
    "SPECIALIST_MODEL/FINDING_ONLY": (
        "77ce79cc71405a3a02fdee76ec36d8b3fb25aa53f220f9029a4b7345e215d531"
    ),
}

TOKEN_GOLDEN_MANIFEST_PATH = Path("config/phase2/token-goldens.json")
TOKEN_GOLDEN_FIXTURE_DIR = Path("config/phase2/token-goldens")
EXPECTED_GOLDEN_KEYS = MODEL_OPERATION_ACTION_KEYS
COMMANDER_SYSTEM_INSTRUCTION = (
    "You are the Incident Commander for a bounded replay-only diagnosis. "
    "Use only the supplied Incident, source capabilities, allowed UTC window, "
    "and budget snapshot. Return exactly one valid InvestigationPlan with one "
    "to three read-only specialist nodes. Do not assume ground truth, invent "
    "Evidence, request write actions, or include prose outside the typed response."
)
SPECIALIST_SYSTEM_INSTRUCTION = (
    "You are the source-bound Specialist named by the validated task. Infer "
    "only from the supplied successful tool record, new Evidence, resolved "
    "dependency Evidence view, and budget snapshot. Return exactly one valid "
    "SpecialistFinding. Do not invent or allocate Evidence refs, call tools, "
    "contact another agent, or include prose outside the typed response."
)
FIRST_JUDGE_SYSTEM_INSTRUCTION = (
    "You are the RCA Judge for a bounded replay-only diagnosis. Compare every "
    "supplied finding and resolved Evidence item, including supporting, "
    "contradicting, and missing evidence. Return exactly one allowed "
    "FirstJudgeAction. Request refinement only when FINAL_OR_REFINEMENT is "
    "exposed and the typed gap justifies it; otherwise return a fail-closed or "
    "confirmed final RCA that satisfies Phase 1 evidence semantics. Do not "
    "invent refs or include prose outside the typed response."
)
FINAL_JUDGE_SYSTEM_INSTRUCTION = (
    "You are the final RCA Judge after one bounded investigation round. "
    "Compare every supplied finding and resolved Evidence item and return "
    "exactly one valid JudgeFinalResult. No further refinement is allowed. "
    "Apply Phase 1 evidence semantics, abstain when evidence is insufficient, "
    "do not invent refs, and include no prose outside the typed response."
)
SYSTEM_INSTRUCTIONS = {
    (ModelOperation.SINGLE_AGENT_MODEL, ModelAllowedActions.PHASE1_ACTION_CATALOG): (
        PHASE1_SYSTEM_INSTRUCTION
    ),
    (ModelOperation.COMMANDER_MODEL, ModelAllowedActions.PLAN_ONLY): (
        COMMANDER_SYSTEM_INSTRUCTION
    ),
    (ModelOperation.SPECIALIST_MODEL, ModelAllowedActions.FINDING_ONLY): (
        SPECIALIST_SYSTEM_INSTRUCTION
    ),
    (ModelOperation.FIRST_JUDGE_MODEL, ModelAllowedActions.FINAL_ONLY): (
        FIRST_JUDGE_SYSTEM_INSTRUCTION
    ),
    (
        ModelOperation.FIRST_JUDGE_MODEL,
        ModelAllowedActions.FINAL_OR_REFINEMENT,
    ): FIRST_JUDGE_SYSTEM_INSTRUCTION,
    (ModelOperation.FINAL_JUDGE_MODEL, ModelAllowedActions.FINAL_ONLY): (
        FINAL_JUDGE_SYSTEM_INSTRUCTION
    ),
}

_ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)
_FIRST_JUDGE_ACTION_ADAPTER: TypeAdapter[FirstJudgeAction] = TypeAdapter(
    FirstJudgeAction
)
_GOLDEN_MANIFEST_MAX_BYTES = 256 * 1024

_POLICY_MAX_BYTES = 128 * 1024
_DOWNLOAD_CHUNK_BYTES = 64 * 1024
_LOCAL_BPE_LOAD_LOCK = threading.Lock()


class TokenPolicyError(ValueError):
    """Typed fail-closed token-policy error."""

    def __init__(self, code: Phase2FailureCode, detail: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {detail}")


class TokenPolicyCore(Phase2Model):
    """Closed, non-circular authority for the offline tokenizer core."""

    schema_version: Literal["phase2.token-policy-core.v1"]
    model_snapshot: Literal["gpt-5.4-mini-2026-03-17"]
    model_to_encoding: dict[str, str]
    tiktoken_version: Literal["0.13.0"]
    tiktoken_release_url: Literal[
        "https://github.com/openai/tiktoken/releases/tag/0.13.0"
    ]
    tiktoken_tag_commit: Literal[
        "fa8b65d062fb6a656ac3810c89efde4c8ab999e2"
    ]
    encoding_name: Literal["o200k_base"]
    encoding_source_url: Literal[
        "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
    ]
    encoding_constructor_source_url: Literal[
        "https://github.com/openai/tiktoken/blob/0.13.0/"
        "tiktoken_ext/openai_public.py#L95-L120"
    ]
    encoding_asset_path: Literal[
        "config/phase2/tokenizers/o200k_base.tiktoken"
    ]
    encoding_asset_bytes: StrictInt
    encoding_asset_sha256: Literal[
        "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
    ]
    encoding_pat_str: str
    encoding_special_tokens: dict[str, StrictInt]
    allowed_special: tuple[str, ...]
    disallowed_special: Literal["all"]
    minimum_completion_tokens: dict[str, StrictInt]
    system_instruction_sha256: dict[str, str]

    @model_validator(mode="after")
    def require_exact_closed_policy(self) -> TokenPolicyCore:
        if self.model_to_encoding != EXPECTED_MODEL_TO_ENCODING:
            raise ValueError("model_to_encoding is not the frozen exact mapping")
        if self.encoding_asset_bytes != ENCODING_ASSET_BYTES:
            raise ValueError("encoding_asset_bytes is not the frozen exact size")
        if self.encoding_pat_str != ENCODING_PAT_STR:
            raise ValueError("encoding_pat_str is not the frozen constructor regex")
        if self.encoding_special_tokens != EXPECTED_SPECIAL_TOKENS:
            raise ValueError("encoding_special_tokens are not the frozen exact map")
        if self.allowed_special != ():
            raise ValueError("allowed_special must be empty")
        if self.minimum_completion_tokens != EXPECTED_MINIMUM_COMPLETION_TOKENS:
            raise ValueError("minimum_completion_tokens are not the frozen exact map")
        if self.system_instruction_sha256 != EXPECTED_SYSTEM_INSTRUCTION_SHA256:
            raise ValueError("system_instruction_sha256 is not the frozen exact map")
        return self


class TokenGoldenEntry(Phase2Model):
    """One derived golden bound one-way to the non-derived core policy."""

    operation: ModelOperation
    allowed_actions: ModelAllowedActions
    token_policy_core_sha256: Sha256
    system_instruction_sha256: Sha256
    response_schema_sha256: Sha256
    minimal_request_path: str = Field(min_length=1)
    minimal_request_sha256: Sha256
    minimal_response_path: str = Field(min_length=1)
    minimal_response_sha256: Sha256
    envelope_sha256: Sha256
    exact_input_tokens: StrictInt = Field(gt=0)
    minimal_response_tokens: StrictInt = Field(gt=0)
    minimum_completion_tokens: StrictInt = Field(gt=0)
    minimum_call_floor_tokens: StrictInt = Field(gt=0)

    @property
    def key(self) -> tuple[ModelOperation, ModelAllowedActions]:
        return self.operation, self.allowed_actions

    @model_validator(mode="after")
    def require_closed_derived_entry(self) -> TokenGoldenEntry:
        if self.key not in EXPECTED_GOLDEN_KEYS:
            raise ValueError("golden operation and allowed actions are not closed")
        expected_request, expected_response = _fixture_paths(self.key)
        if (
            self.minimal_request_path != expected_request.as_posix()
            or self.minimal_response_path != expected_response.as_posix()
        ):
            raise ValueError("golden fixture paths are not exact")
        if self.minimum_call_floor_tokens != (
            self.exact_input_tokens + self.minimum_completion_tokens
        ):
            raise ValueError("golden minimum call floor is inconsistent")
        if self.minimum_completion_tokens % 64 != 0:
            raise ValueError("golden completion floor is not a multiple of 64")
        if self.minimal_response_tokens + 64 > self.minimum_completion_tokens:
            raise ValueError("minimal response does not fit the frozen completion floor")
        return self


class TokenGoldenManifest(Phase2Model):
    """Closed six-entry manifest containing derived values only."""

    schema_version: Literal["phase2.token-golden-manifest.v1"]
    token_policy_core_sha256: Sha256
    entries: tuple[
        TokenGoldenEntry,
        TokenGoldenEntry,
        TokenGoldenEntry,
        TokenGoldenEntry,
        TokenGoldenEntry,
        TokenGoldenEntry,
    ]

    @model_validator(mode="after")
    def require_exact_entry_projection(self) -> TokenGoldenManifest:
        if tuple(entry.key for entry in self.entries) != EXPECTED_GOLDEN_KEYS:
            raise ValueError("golden manifest keys are incomplete or out of order")
        if any(
            entry.token_policy_core_sha256 != self.token_policy_core_sha256
            for entry in self.entries
        ):
            raise ValueError("golden entry core binding is inconsistent")
        return self


GoldenKey = tuple[ModelOperation, ModelAllowedActions]


@dataclass(frozen=True, slots=True)
class TokenAuthority:
    """Fully reproduced local token authority for model-call preflight."""

    core: TokenPolicyCore
    core_sha256: str
    goldens: TokenGoldenManifest
    encoding: Encoding
    minimal_requests: Mapping[GoldenKey, dict[str, JsonValue]]
    minimal_responses: Mapping[GoldenKey, dict[str, JsonValue]]
    envelopes: Mapping[GoldenKey, ModelInputEnvelope]

    def exact_input_tokens(self, envelope: ModelInputEnvelope) -> int:
        return _token_count(
            self.encoding,
            canonical_json_bytes(envelope.model_dump(mode="json")),
        )

    def golden(
        self,
        operation: ModelOperation,
        allowed_actions: ModelAllowedActions,
    ) -> TokenGoldenEntry:
        key = operation, allowed_actions
        for entry in self.goldens.entries:
            if entry.key == key:
                return entry
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH,
            "operation and allowed actions have no frozen golden",
        )


def canonical_json_bytes(value: object) -> bytes:
    """Return the one canonical UTF-8 JSON representation."""

    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_CANONICALIZATION_FAILED,
            "value cannot be represented as canonical JSON",
        ) from exc


def _load_policy_path(policy_path: Path) -> TokenPolicyCore:
    try:
        policy_stat = policy_path.lstat()
    except FileNotFoundError as exc:
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_POLICY_MISSING,
            "token policy core is missing",
        ) from exc
    if not stat.S_ISREG(policy_stat.st_mode) or policy_stat.st_size > _POLICY_MAX_BYTES:
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_POLICY_CORE_HASH_MISMATCH,
            "token policy core must be a bounded regular file",
        )
    try:
        raw = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_POLICY_CORE_HASH_MISMATCH,
            "token policy core is not valid UTF-8 JSON",
        ) from exc
    if not isinstance(raw, dict):
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_POLICY_CORE_HASH_MISMATCH,
            "token policy core must be a JSON object",
        )
    if raw.get("tiktoken_version") != TIKTOKEN_VERSION:
        raise TokenPolicyError(
            Phase2FailureCode.TOKENIZER_VERSION_MISMATCH,
            "token policy names a non-frozen tiktoken version",
        )
    if raw.get("model_to_encoding") != EXPECTED_MODEL_TO_ENCODING:
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_MODEL_MAPPING_MISMATCH,
            "token policy model mapping is not exact",
        )
    try:
        core = TokenPolicyCore.model_validate(raw)
    except ValidationError as exc:
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_POLICY_CORE_HASH_MISMATCH,
            "token policy core does not match the frozen contract",
        ) from exc
    if hashlib.sha256(canonical_json_bytes(core.model_dump())).hexdigest() != (
        TOKEN_POLICY_CORE_SHA256
    ):
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_POLICY_CORE_HASH_MISMATCH,
            "token policy canonical digest does not match",
        )
    try:
        installed_version = version("tiktoken")
    except PackageNotFoundError as exc:
        raise TokenPolicyError(
            Phase2FailureCode.TOKENIZER_VERSION_MISMATCH,
            "tiktoken is not installed",
        ) from exc
    if installed_version != TIKTOKEN_VERSION:
        raise TokenPolicyError(
            Phase2FailureCode.TOKENIZER_VERSION_MISMATCH,
            "installed tiktoken version is not frozen",
        )
    return core


def load_token_policy_core(project_root: Path) -> TokenPolicyCore:
    """Load and verify the frozen core from a project tree."""

    return _load_policy_path(project_root / POLICY_PATH)


def _require_regular_asset(asset_path: Path, expected_bytes: int) -> None:
    try:
        asset_stat = asset_path.lstat()
    except FileNotFoundError as exc:
        raise TokenPolicyError(
            Phase2FailureCode.TOKENIZER_ASSET_MISSING,
            "local tokenizer asset is missing",
        ) from exc
    if not stat.S_ISREG(asset_stat.st_mode):
        raise TokenPolicyError(
            Phase2FailureCode.TOKENIZER_ASSET_HASH_MISMATCH,
            "local tokenizer asset must be a regular file",
        )
    if asset_stat.st_size != expected_bytes:
        raise TokenPolicyError(
            Phase2FailureCode.TOKENIZER_ASSET_SIZE_MISMATCH,
            "local tokenizer asset size does not match",
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as stream:
        for chunk in iter(lambda: stream.read(_DOWNLOAD_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_asset_file(asset_path: Path, core: TokenPolicyCore) -> None:
    _require_regular_asset(asset_path, core.encoding_asset_bytes)
    if _sha256_file(asset_path) != core.encoding_asset_sha256:
        raise TokenPolicyError(
            Phase2FailureCode.TOKENIZER_ASSET_HASH_MISMATCH,
            "local tokenizer asset digest does not match",
        )


def build_local_encoding(core: TokenPolicyCore, project_root: Path) -> Encoding:
    """Construct o200k_base from verified local ranks without registry access."""

    asset_path = project_root / core.encoding_asset_path
    _require_regular_asset(asset_path, core.encoding_asset_bytes)
    with _LOCAL_BPE_LOAD_LOCK:
        previous_cache = os.environ.get("TIKTOKEN_CACHE_DIR")
        with tempfile.TemporaryDirectory(prefix="ecomsre-tiktoken-cache-") as cache_dir:
            os.environ["TIKTOKEN_CACHE_DIR"] = cache_dir
            try:
                mergeable_ranks = load_tiktoken_bpe(
                    str(asset_path), expected_hash=core.encoding_asset_sha256
                )
            except (OSError, ValueError) as exc:
                raise TokenPolicyError(
                    Phase2FailureCode.TOKENIZER_ASSET_HASH_MISMATCH,
                    "local tokenizer asset cannot be verified and parsed",
                ) from exc
            finally:
                if previous_cache is None:
                    os.environ.pop("TIKTOKEN_CACHE_DIR", None)
                else:
                    os.environ["TIKTOKEN_CACHE_DIR"] = previous_cache
    return Encoding(
        name="phase2_o200k_base_offline",
        pat_str=core.encoding_pat_str,
        mergeable_ranks=mergeable_ranks,
        special_tokens=dict(core.encoding_special_tokens),
    )


def load_offline_tokenizer(project_root: Path) -> Encoding:
    """Load the core and construct its verified offline tokenizer."""

    return build_local_encoding(load_token_policy_core(project_root), project_root)


def _key_string(key: GoldenKey) -> str:
    return f"{key[0].value}/{key[1].value}"


def _fixture_paths(key: GoldenKey) -> tuple[Path, Path]:
    stem = f"{key[0].value}--{key[1].value}".lower().replace("_", "-")
    return (
        TOKEN_GOLDEN_FIXTURE_DIR / f"{stem}.request.json",
        TOKEN_GOLDEN_FIXTURE_DIR / f"{stem}.response.json",
    )


def _dump_model(value: Phase1Model) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], value.model_dump(mode="json"))


def _response_schema(key: GoldenKey) -> dict[str, JsonValue]:
    operation, allowed_actions = key
    if key == (
        ModelOperation.SINGLE_AGENT_MODEL,
        ModelAllowedActions.PHASE1_ACTION_CATALOG,
    ):
        return cast(
            dict[str, JsonValue],
            {
                "schema_version": "phase2.phase1-function-catalog.v1",
                "dialect": "openai_chat_completions_tools",
                "tools": list(_tool_definitions()),
            },
        )
    schema_type = cast(type[Phase1Model] | None, {
        (ModelOperation.COMMANDER_MODEL, ModelAllowedActions.PLAN_ONLY): (
            InvestigationPlan
        ),
        (ModelOperation.SPECIALIST_MODEL, ModelAllowedActions.FINDING_ONLY): (
            SpecialistFinding
        ),
        (ModelOperation.FIRST_JUDGE_MODEL, ModelAllowedActions.FINAL_ONLY): (
            JudgeFinalResult
        ),
        (ModelOperation.FINAL_JUDGE_MODEL, ModelAllowedActions.FINAL_ONLY): (
            JudgeFinalResult
        ),
    }.get(key))
    if schema_type is not None:
        schema = schema_type.model_json_schema(mode="validation")
    elif key == (
        ModelOperation.FIRST_JUDGE_MODEL,
        ModelAllowedActions.FINAL_OR_REFINEMENT,
    ):
        schema = _FIRST_JUDGE_ACTION_ADAPTER.json_schema(mode="validation")
    else:
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH,
            f"unsupported golden key {operation.value}/{allowed_actions.value}",
        )
    return cast(
        dict[str, JsonValue],
        {
            "schema_version": "phase2.response-schema-envelope.v1",
            "dialect": "https://json-schema.org/draft/2020-12/schema",
            "schema": schema,
        },
    )


def _validate_request(
    key: GoldenKey,
    value: object,
) -> Phase1Model:
    request_type = cast(type[Phase1Model], {
        (ModelOperation.SINGLE_AGENT_MODEL, ModelAllowedActions.PHASE1_ACTION_CATALOG): (
            ModelRequest
        ),
        (ModelOperation.COMMANDER_MODEL, ModelAllowedActions.PLAN_ONLY): (
            CommanderRequest
        ),
        (ModelOperation.SPECIALIST_MODEL, ModelAllowedActions.FINDING_ONLY): (
            SpecialistModelRequest
        ),
        (ModelOperation.FIRST_JUDGE_MODEL, ModelAllowedActions.FINAL_ONLY): (
            JudgeRequest
        ),
        (
            ModelOperation.FIRST_JUDGE_MODEL,
            ModelAllowedActions.FINAL_OR_REFINEMENT,
        ): JudgeRequest,
        (ModelOperation.FINAL_JUDGE_MODEL, ModelAllowedActions.FINAL_ONLY): (
            JudgeRequest
        ),
    }[key])
    return request_type.model_validate(value)


def _validate_response(
    key: GoldenKey,
    value: object,
) -> Phase1Model:
    if key == (
        ModelOperation.SINGLE_AGENT_MODEL,
        ModelAllowedActions.PHASE1_ACTION_CATALOG,
    ):
        return cast(Phase1Model, _ACTION_ADAPTER.validate_python(value))
    response_type = cast(type[Phase1Model] | None, {
        (ModelOperation.COMMANDER_MODEL, ModelAllowedActions.PLAN_ONLY): (
            InvestigationPlan
        ),
        (ModelOperation.SPECIALIST_MODEL, ModelAllowedActions.FINDING_ONLY): (
            SpecialistFinding
        ),
        (ModelOperation.FIRST_JUDGE_MODEL, ModelAllowedActions.FINAL_ONLY): (
            JudgeFinalResult
        ),
        (ModelOperation.FINAL_JUDGE_MODEL, ModelAllowedActions.FINAL_ONLY): (
            JudgeFinalResult
        ),
    }.get(key))
    if response_type is not None:
        return response_type.model_validate(value)
    return cast(Phase1Model, _FIRST_JUDGE_ACTION_ADAPTER.validate_python(value))


def build_model_input_envelope(
    core: TokenPolicyCore,
    operation: ModelOperation,
    allowed_actions: ModelAllowedActions,
    request: object,
) -> ModelInputEnvelope:
    """Validate and canonicalize one exact operation-specific model input."""

    key = operation, allowed_actions
    if key not in EXPECTED_GOLDEN_KEYS:
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH,
            "operation and allowed actions are not in the frozen key set",
        )
    validated_request = _validate_request(key, request)
    instruction = SYSTEM_INSTRUCTIONS[key]
    instruction_hash = hashlib.sha256(instruction.encode("utf-8")).hexdigest()
    if instruction_hash != core.system_instruction_sha256[_key_string(key)]:
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_POLICY_CORE_HASH_MISMATCH,
            "system instruction conflicts with the core policy",
        )
    return ModelInputEnvelope(
        schema_version="phase2.model-input-envelope.v1",
        operation=operation,
        allowed_actions=allowed_actions,
        model_snapshot=core.model_snapshot,
        system_instruction=instruction,
        request=_dump_model(validated_request),
        response_schema=_response_schema(key),
    )


def validate_model_response(
    operation: ModelOperation,
    allowed_actions: ModelAllowedActions,
    response: object,
) -> Phase1Model:
    """Validate one provider response against its exact closed contract."""

    key = operation, allowed_actions
    if key not in EXPECTED_GOLDEN_KEYS:
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH,
            "response operation and allowed actions are not frozen",
        )
    try:
        return _validate_response(key, response)
    except ValidationError as exc:
        raise TokenPolicyError(
            Phase2FailureCode.PROVIDER_USAGE_INCONSISTENT,
            "provider response violates the exact response contract",
        ) from exc


_FIXTURE_STARTED_AT = datetime(2026, 8, 1, 1, 0, tzinfo=UTC)
_FIXTURE_ENDED_AT = datetime(2026, 8, 1, 1, 5, tzinfo=UTC)
_FIXTURE_RUN_ID = "a" * 32
_FIXTURE_INCIDENT_ID = "inc-001"
_FIXTURE_METRICS_REF = f"evidence://{_FIXTURE_RUN_ID}/metrics/0001"
_FIXTURE_TRACES_REF = f"evidence://{_FIXTURE_RUN_ID}/traces/0002"


def _minimal_incident() -> Incident:
    return Incident(
        schema_version="phase1.incident.v1",
        incident_id=_FIXTURE_INCIDENT_ID,
        alert_source_service="frontend",
        summary="Checkout latency exceeds the SLO.",
        started_at=_FIXTURE_STARTED_AT,
        ended_at=_FIXTURE_ENDED_AT,
        affected_sli="checkout p95 latency",
        severity=Severity.SEV2,
    )


def _minimal_snapshot(
    *,
    active_authorization_ids: tuple[str, ...] = (),
) -> BudgetSnapshot:
    reserved_model_calls = 1 if active_authorization_ids else 0
    reserved_tokens = 1_000 if active_authorization_ids else 0
    charged_tool_calls = 1 if active_authorization_ids else 0
    return BudgetSnapshot(
        schema_version="phase2.budget-snapshot.v1",
        snapshot_id="budget-snapshot-001",
        run_id=_FIXTURE_RUN_ID,
        variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
        case_id="case-001",
        max_model_calls=8,
        max_tool_calls=8,
        max_total_tokens=COMPARISON_MAX_TOTAL_TOKENS,
        charged_model_calls=0,
        charged_tool_calls=charged_tool_calls,
        cumulative_tokens=0,
        reserved_model_calls=reserved_model_calls,
        reserved_tool_calls=0,
        reserved_tokens=reserved_tokens,
        remaining_model_calls=8 - reserved_model_calls,
        remaining_tool_calls=8 - charged_tool_calls,
        remaining_tokens=COMPARISON_MAX_TOTAL_TOKENS - reserved_tokens,
        monotonic_elapsed_seconds=0.0,
        sequence=2 if active_authorization_ids else 0,
        active_capacity_slot_ids=(),
        active_specialist_authorization_ids=active_authorization_ids,
        active_lease_ids=(),
    )


def _minimal_source_capabilities() -> tuple[
    SourceCapability,
    SourceCapability,
    SourceCapability,
    SourceCapability,
]:
    bindings = (
        (
            EvidenceSource.METRICS,
            SpecialistRole.METRICS_AGENT,
            ReadOnlyToolName.QUERY_METRICS,
            "metrics",
        ),
        (
            EvidenceSource.LOGS,
            SpecialistRole.LOGS_AGENT,
            ReadOnlyToolName.SEARCH_LOGS,
            "logs",
        ),
        (
            EvidenceSource.TRACES,
            SpecialistRole.TRACE_AGENT,
            ReadOnlyToolName.SEARCH_TRACES,
            "traces",
        ),
        (
            EvidenceSource.CHANGES,
            SpecialistRole.CHANGE_AGENT,
            ReadOnlyToolName.LIST_CHANGES,
            "changes",
        ),
    )
    return cast(
        tuple[
            SourceCapability,
            SourceCapability,
            SourceCapability,
            SourceCapability,
        ],
        tuple(
            SourceCapability(
                source=source,
                specialist_role=role,
                tool_name=tool,
                action_type=cast(
                    Literal["metrics", "logs", "traces", "changes"], action
                ),
            )
            for source, role, tool, action in bindings
        ),
    )


def _minimal_node(
    node_id: str = "node-metrics-001",
    *,
    traces: bool = False,
    depends_on: tuple[str, ...] = (),
) -> InvestigationNode:
    if traces:
        source = EvidenceSource.TRACES
        role = SpecialistRole.TRACE_AGENT
        tool = ReadOnlyToolName.SEARCH_TRACES
        query: MetricsAction | TracesAction = TracesAction(
            action_type="traces",
            started_at=_FIXTURE_STARTED_AT,
            ended_at=_FIXTURE_ENDED_AT,
            service="checkoutservice",
        )
    else:
        source = EvidenceSource.METRICS
        role = SpecialistRole.METRICS_AGENT
        tool = ReadOnlyToolName.QUERY_METRICS
        query = MetricsAction(
            action_type="metrics",
            started_at=_FIXTURE_STARTED_AT,
            ended_at=_FIXTURE_ENDED_AT,
            service="checkoutservice",
        )
    return InvestigationNode(
        schema_version="phase2.investigation-node.v1",
        node_id=node_id,
        source=source,
        specialist_role=role,
        tool_name=tool,
        query=query,
        depends_on=depends_on,
        objective="Assess the bounded checkout latency observation.",
        query_started_at=_FIXTURE_STARTED_AT,
        query_ended_at=_FIXTURE_ENDED_AT,
        priority=1,
    )


def _minimal_plan() -> InvestigationPlan:
    return InvestigationPlan(
        schema_version="phase2.investigation-plan.v1",
        run_id=_FIXTURE_RUN_ID,
        incident_id=_FIXTURE_INCIDENT_ID,
        plan_id="plan-001",
        nodes=(_minimal_node(),),
        planning_rationale="Metrics can establish whether the alert is real.",
        budget_snapshot_id="budget-snapshot-001",
    )


def _minimal_evidence(*, traces: bool = False) -> Evidence:
    return Evidence(
        schema_version="phase1.evidence.v1",
        evidence_ref=_FIXTURE_TRACES_REF if traces else _FIXTURE_METRICS_REF,
        run_id=_FIXTURE_RUN_ID,
        source=EvidenceSource.TRACES if traces else EvidenceSource.METRICS,
        observation_type="trace_observation" if traces else "latency_observation",
        attributes=(
            EvidenceAttribute(
                name="duration_ms" if traces else "p95_ms",
                value=900.0,
            ),
        ),
        raw_artifact_ref="traces.json#0" if traces else "metrics.json#0",
        raw_artifact_sha256="0" * 64,
        limitations=(),
        summary="The bounded observation shows elevated latency.",
        started_at=_FIXTURE_STARTED_AT,
        ended_at=_FIXTURE_ENDED_AT,
        service="checkoutservice",
    )


def _minimal_finding(*, traces: bool = False) -> SpecialistFinding:
    return SpecialistFinding(
        schema_version="phase2.specialist-finding.v1",
        finding_id="f-traces" if traces else "f-metrics",
        run_id=_FIXTURE_RUN_ID,
        incident_id=_FIXTURE_INCIDENT_ID,
        plan_id="plan-001",
        node_id="node-traces-001" if traces else "node-metrics-001",
        source=EvidenceSource.TRACES if traces else EvidenceSource.METRICS,
        specialist_role=(
            SpecialistRole.TRACE_AGENT if traces else SpecialistRole.METRICS_AGENT
        ),
        evidence_refs=(),
        hypotheses=(
            FindingHypothesis(
                schema_version="phase2.finding-hypothesis.v1",
                hypothesis_id="hypothesis-001",
                root_service=None,
                fault_mechanism=None,
                claim="Latency is elevated.",
            ),
        ),
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
        missing_evidence=(),
        confidence=0.5,
        finding_rationale="The bounded source supports the hypothesis.",
    )


def _minimal_abstain() -> RCAResult:
    return RCAResult(
        schema_version="phase1.rca-result.v1",
        decision=RCADecision.ABSTAIN,
        root_service=None,
        fault_mechanism=None,
        causal_chain=(),
        affected_sli="checkout p95 latency",
        supporting_evidence=(_FIXTURE_METRICS_REF,),
        contradicting_evidence=(),
        missing_evidence=(),
        confidence=0.25,
        decision_rationale="No confirmed incident is established by the evidence.",
        recommended_next_action=(
            RecommendedNextAction.CONTINUE_MONITORING_AFFECTED_SLI
        ),
    )


def _graph_with_refinement() -> AdmittedInvestigationGraph:
    plan = _minimal_plan()
    fragment = AdmittedRefinementFragment(
        schema_version="phase2.admitted-refinement-fragment.v1",
        request_id="refinement-001",
        parent_plan_id=plan.plan_id,
        nodes=(
            _minimal_node(
                "node-traces-001",
                traces=True,
                depends_on=("node-metrics-001",),
            ),
        ),
    )
    all_nodes = plan.nodes + fragment.nodes
    edges = tuple(
        (dependency, node.node_id)
        for node in all_nodes
        for dependency in node.depends_on
    )
    projection = {
        "schema_version": "phase2.admitted-investigation-graph.v1",
        "run_id": plan.run_id,
        "incident_id": plan.incident_id,
        "initial_plan": plan.model_dump(mode="json"),
        "refinement_fragment": fragment.model_dump(mode="json"),
        "all_nodes": [node.model_dump(mode="json") for node in all_nodes],
        "dependency_edges": [list(edge) for edge in edges],
    }
    return AdmittedInvestigationGraph(
        schema_version="phase2.admitted-investigation-graph.v1",
        run_id=plan.run_id,
        incident_id=plan.incident_id,
        initial_plan=plan,
        refinement_fragment=fragment,
        all_nodes=all_nodes,
        dependency_edges=edges,
        graph_sha256=hashlib.sha256(canonical_json_bytes(projection)).hexdigest(),
    )


def _minimal_fixture_models(
    core_sha256: str,
) -> Mapping[GoldenKey, tuple[Phase1Model, Phase1Model]]:
    incident = _minimal_incident()
    plan = _minimal_plan()
    metrics_evidence = _minimal_evidence()
    metrics_query = cast(MetricsAction, plan.nodes[0].query)
    single_request = ModelRequest(
        schema_version="phase1.model-request.v1",
        request_id="request-single-001",
        run_id=_FIXTURE_RUN_ID,
        agent_id="single-agent",
        incident_id=_FIXTURE_INCIDENT_ID,
        task_id="single-task-001",
        model_name=MODEL_SNAPSHOT,
        incident=incident,
        transcript=(),
        evidence=(),
        remaining_budgets=RemainingBudgets(
            model_calls=8,
            tool_calls=8,
            total_tokens=COMPARISON_MAX_TOTAL_TOKENS,
        ),
        allowed_actions=tuple(ModelFunctionName),
        temperature=0.0,
        timeout_seconds=60.0,
    )
    commander_request = CommanderRequest(
        schema_version="phase2.commander-request.v1",
        run_id=_FIXTURE_RUN_ID,
        incident=incident,
        source_capabilities=_minimal_source_capabilities(),
        allowed_started_at=_FIXTURE_STARTED_AT,
        allowed_ended_at=_FIXTURE_ENDED_AT,
        budget_snapshot=_minimal_snapshot(),
        model_snapshot=MODEL_SNAPSHOT,
        token_policy_core_sha256=core_sha256,
    )
    task = SpecialistTask(
        schema_version="phase2.specialist-task.v1",
        run_id=_FIXTURE_RUN_ID,
        incident_id=_FIXTURE_INCIDENT_ID,
        plan_id=plan.plan_id,
        node_id=plan.nodes[0].node_id,
        source=EvidenceSource.METRICS,
        specialist_role=SpecialistRole.METRICS_AGENT,
        tool_name=ReadOnlyToolName.QUERY_METRICS,
        query=metrics_query,
        objective=plan.nodes[0].objective,
        dependency_finding_ids=(),
        dependency_evidence_refs=(),
        tool_authorization_id="tool-auth-001",
        model_capacity_slot_id="slot-specialist-001",
    )
    record = ToolCallRecord(
        schema_version="phase1.tool-call-record.v1",
        call_id="tool-call-001",
        run_id=_FIXTURE_RUN_ID,
        agent_id=SpecialistRole.METRICS_AGENT.value,
        incident_id=_FIXTURE_INCIDENT_ID,
        task_id=task.node_id,
        tool_name=ReadOnlyToolName.QUERY_METRICS,
        action=metrics_query,
        evidence=(metrics_evidence,),
        evidence_refs=(metrics_evidence.evidence_ref,),
        started_at=_FIXTURE_STARTED_AT,
        ended_at=_FIXTURE_ENDED_AT,
        monotonic_duration_seconds=0.1,
        budget_consumed=True,
        dispatched=True,
        evidence_quarantined=False,
        usable=True,
        status="OK",
        error_code=None,
    )
    specialist_request = SpecialistModelRequest(
        schema_version="phase2.specialist-model-request.v1",
        task=task,
        tool_call_record=record,
        new_evidence=(metrics_evidence,),
        dependency_finding_ids=(),
        resolved_dependency_evidence_view=ResolvedEvidenceView(
            schema_version="phase2.resolved-evidence-view.v1",
            run_id=_FIXTURE_RUN_ID,
            evidence=(),
        ),
        budget_snapshot=_minimal_snapshot(
            active_authorization_ids=(task.tool_authorization_id,)
        ),
    )
    finding = _minimal_finding()
    initial_graph = build_initial_admitted_graph(plan)
    first_final_request = JudgeRequest(
        schema_version="phase2.judge-request.v1",
        judge_request_id="judge-first-final-001",
        run_id=_FIXTURE_RUN_ID,
        incident=incident,
        admitted_graph=initial_graph,
        finding_ids=(finding.finding_id,),
        findings=(finding,),
        available_evidence_refs=(),
        resolved_evidence_view=ResolvedEvidenceView(
            schema_version="phase2.resolved-evidence-view.v1",
            run_id=_FIXTURE_RUN_ID,
            evidence=(),
        ),
        budget_snapshot=_minimal_snapshot(),
        refinement_round=0,
        allowed_actions=ModelAllowedActions.FINAL_ONLY,
        conditional_refinement_bundle_id=None,
    )
    first_union_request = JudgeRequest.model_validate(
        {
            **first_final_request.model_dump(mode="json"),
            "judge_request_id": "judge-first-union-001",
            "allowed_actions": ModelAllowedActions.FINAL_OR_REFINEMENT,
            "conditional_refinement_bundle_id": "bundle-001",
        }
    )
    final_result = JudgeFinalResult(
        schema_version="phase2.judge-final-result.v1",
        action_type="FINAL_RCA",
        run_id=_FIXTURE_RUN_ID,
        incident_id=_FIXTURE_INCIDENT_ID,
        rca_result=_minimal_abstain(),
        finding_ids_considered=(finding.finding_id,),
        refinement_used=False,
        judge_request_id=first_final_request.judge_request_id,
    )
    additional_request = AdditionalInvestigationRequest(
        schema_version="phase2.additional-investigation-request.v1",
        action_type="ADDITIONAL_INVESTIGATION",
        run_id=_FIXTURE_RUN_ID,
        incident_id=_FIXTURE_INCIDENT_ID,
        parent_plan_id=plan.plan_id,
        request_id="refinement-001",
        nodes=(
            _minimal_node(
                "node-traces-001",
                traces=True,
                depends_on=("node-metrics-001",),
            ),
        ),
        target_hypothesis_ids=("hypothesis-001",),
        reason="Trace evidence is missing for the bounded hypothesis.",
        conditional_refinement_bundle_id="bundle-001",
        fallback_rca_result=_minimal_abstain(),
    )
    refined_graph = _graph_with_refinement()
    traces_finding = _minimal_finding(traces=True)
    final_judge_request = JudgeRequest(
        schema_version="phase2.judge-request.v1",
        judge_request_id="judge-final-001",
        run_id=_FIXTURE_RUN_ID,
        incident=incident,
        admitted_graph=refined_graph,
        finding_ids=(finding.finding_id, traces_finding.finding_id),
        findings=(finding, traces_finding),
        available_evidence_refs=(),
        resolved_evidence_view=ResolvedEvidenceView(
            schema_version="phase2.resolved-evidence-view.v1",
            run_id=_FIXTURE_RUN_ID,
            evidence=(),
        ),
        budget_snapshot=_minimal_snapshot(),
        refinement_round=1,
        allowed_actions=ModelAllowedActions.FINAL_ONLY,
        conditional_refinement_bundle_id=None,
    )
    final_judge_result = JudgeFinalResult.model_validate(
        {
            **final_result.model_dump(mode="json"),
            "finding_ids_considered": (
                finding.finding_id,
                traces_finding.finding_id,
            ),
            "refinement_used": True,
            "judge_request_id": final_judge_request.judge_request_id,
        }
    )
    return MappingProxyType(
        {
            (
                ModelOperation.SINGLE_AGENT_MODEL,
                ModelAllowedActions.PHASE1_ACTION_CATALOG,
            ): (single_request, metrics_query),
            (ModelOperation.COMMANDER_MODEL, ModelAllowedActions.PLAN_ONLY): (
                commander_request,
                plan,
            ),
            (ModelOperation.SPECIALIST_MODEL, ModelAllowedActions.FINDING_ONLY): (
                specialist_request,
                finding,
            ),
            (ModelOperation.FIRST_JUDGE_MODEL, ModelAllowedActions.FINAL_ONLY): (
                first_final_request,
                final_result,
            ),
            (
                ModelOperation.FIRST_JUDGE_MODEL,
                ModelAllowedActions.FINAL_OR_REFINEMENT,
            ): (first_union_request, additional_request),
            (ModelOperation.FINAL_JUDGE_MODEL, ModelAllowedActions.FINAL_ONLY): (
                final_judge_request,
                final_judge_result,
            ),
        }
    )


def _token_count(encoding: Encoding, payload: bytes) -> int:
    return len(
        encoding.encode(
            payload.decode("utf-8"),
            allowed_special=set(),
            disallowed_special="all",
        )
    )


def _derive_golden_manifest(
    core: TokenPolicyCore,
    core_sha256: str,
    encoding: Encoding,
    requests: Mapping[GoldenKey, dict[str, JsonValue]],
    responses: Mapping[GoldenKey, dict[str, JsonValue]],
) -> tuple[TokenGoldenManifest, Mapping[GoldenKey, ModelInputEnvelope]]:
    entries: list[TokenGoldenEntry] = []
    envelopes: dict[GoldenKey, ModelInputEnvelope] = {}
    for key in EXPECTED_GOLDEN_KEYS:
        request = requests[key]
        response = responses[key]
        envelope = build_model_input_envelope(core, key[0], key[1], request)
        envelope_bytes = canonical_json_bytes(envelope.model_dump(mode="json"))
        if b"token_golden_manifest_sha256" in envelope_bytes:
            raise TokenPolicyError(
                Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH,
                "model envelope contains a circular manifest binding",
            )
        request_bytes = canonical_json_bytes(request)
        response_bytes = canonical_json_bytes(response)
        response_schema_bytes = canonical_json_bytes(envelope.response_schema)
        request_path, response_path = _fixture_paths(key)
        minimum_completion = core.minimum_completion_tokens[_key_string(key)]
        entry = TokenGoldenEntry(
            operation=key[0],
            allowed_actions=key[1],
            token_policy_core_sha256=core_sha256,
            system_instruction_sha256=hashlib.sha256(
                envelope.system_instruction.encode("utf-8")
            ).hexdigest(),
            response_schema_sha256=hashlib.sha256(
                response_schema_bytes
            ).hexdigest(),
            minimal_request_path=request_path.as_posix(),
            minimal_request_sha256=hashlib.sha256(request_bytes).hexdigest(),
            minimal_response_path=response_path.as_posix(),
            minimal_response_sha256=hashlib.sha256(response_bytes).hexdigest(),
            envelope_sha256=hashlib.sha256(envelope_bytes).hexdigest(),
            exact_input_tokens=_token_count(encoding, envelope_bytes),
            minimal_response_tokens=_token_count(encoding, response_bytes),
            minimum_completion_tokens=minimum_completion,
            minimum_call_floor_tokens=(
                _token_count(encoding, envelope_bytes) + minimum_completion
            ),
        )
        entries.append(entry)
        envelopes[key] = envelope
    return (
        TokenGoldenManifest(
            schema_version="phase2.token-golden-manifest.v1",
            token_policy_core_sha256=core_sha256,
            entries=cast(
                tuple[
                    TokenGoldenEntry,
                    TokenGoldenEntry,
                    TokenGoldenEntry,
                    TokenGoldenEntry,
                    TokenGoldenEntry,
                    TokenGoldenEntry,
                ],
                tuple(entries),
            ),
        ),
        MappingProxyType(envelopes),
    )


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


def _read_canonical_json(path: Path, *, maximum_bytes: int) -> dict[str, object]:
    try:
        file_stat = path.lstat()
    except FileNotFoundError as exc:
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH,
            f"required golden file is missing: {path.name}",
        ) from exc
    if not stat.S_ISREG(file_stat.st_mode) or not (0 < file_stat.st_size <= maximum_bytes):
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH,
            f"golden file must be a bounded regular file: {path.name}",
        )
    try:
        payload = path.read_bytes()
        parsed = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH,
            f"golden file is not strict UTF-8 JSON: {path.name}",
        ) from exc
    if not isinstance(parsed, dict) or canonical_json_bytes(parsed) != payload:
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH,
            f"golden file is not exact canonical JSON: {path.name}",
        )
    return parsed


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def regenerate_token_goldens(
    project_root: Path,
    *,
    expected_core_sha256: str,
    replace_drifted_manifest: bool = False,
) -> TokenGoldenManifest:
    """Generate the twelve canonical fixtures and their one-way manifest."""

    core = load_token_policy_core(project_root)
    core_sha256 = hashlib.sha256(canonical_json_bytes(core.model_dump())).hexdigest()
    if expected_core_sha256 != core_sha256:
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_POLICY_CORE_HASH_MISMATCH,
            "expected core digest does not match the loaded core",
        )
    encoding = build_local_encoding(core, project_root)
    fixture_models = _minimal_fixture_models(core_sha256)
    requests = MappingProxyType(
        {key: _dump_model(pair[0]) for key, pair in fixture_models.items()}
    )
    responses = MappingProxyType(
        {key: _dump_model(pair[1]) for key, pair in fixture_models.items()}
    )
    manifest, _ = _derive_golden_manifest(
        core,
        core_sha256,
        encoding,
        requests,
        responses,
    )
    outputs: dict[Path, bytes] = {}
    for key in EXPECTED_GOLDEN_KEYS:
        request_path, response_path = _fixture_paths(key)
        outputs[project_root / request_path] = canonical_json_bytes(requests[key])
        outputs[project_root / response_path] = canonical_json_bytes(responses[key])
    outputs[project_root / TOKEN_GOLDEN_MANIFEST_PATH] = canonical_json_bytes(
        manifest.model_dump(mode="json")
    )

    drifted: list[str] = []
    for path, payload in outputs.items():
        if path.exists() or path.is_symlink():
            try:
                file_stat = path.lstat()
                existing = path.read_bytes() if stat.S_ISREG(file_stat.st_mode) else b""
            except OSError as exc:
                raise TokenPolicyError(
                    Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH,
                    "existing golden output cannot be read safely",
                ) from exc
            if existing != payload:
                drifted.append(path.relative_to(project_root).as_posix())
    if drifted and not replace_drifted_manifest:
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH,
            f"golden outputs drifted and replacement was not authorized: {drifted[0]}",
        )
    for path, payload in outputs.items():
        if not path.exists() or path.read_bytes() != payload:
            _write_bytes_atomic(path, payload)
    return manifest


def load_token_authority(project_root: Path) -> TokenAuthority:
    """Reproduce and validate every local golden before any model call."""

    core = load_token_policy_core(project_root)
    core_sha256 = hashlib.sha256(canonical_json_bytes(core.model_dump())).hexdigest()
    manifest_payload = _read_canonical_json(
        project_root / TOKEN_GOLDEN_MANIFEST_PATH,
        maximum_bytes=_GOLDEN_MANIFEST_MAX_BYTES,
    )
    try:
        manifest = TokenGoldenManifest.model_validate(manifest_payload)
    except ValidationError as exc:
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH,
            "golden manifest does not match the closed schema",
        ) from exc
    if manifest.token_policy_core_sha256 != core_sha256:
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH,
            "golden manifest is not bound to the loaded core",
        )

    requests: dict[GoldenKey, dict[str, JsonValue]] = {}
    responses: dict[GoldenKey, dict[str, JsonValue]] = {}
    for entry in manifest.entries:
        request_path, response_path = _fixture_paths(entry.key)
        request_payload = _read_canonical_json(
            project_root / request_path,
            maximum_bytes=_POLICY_MAX_BYTES,
        )
        response_payload = _read_canonical_json(
            project_root / response_path,
            maximum_bytes=_POLICY_MAX_BYTES,
        )
        try:
            request = _validate_request(entry.key, request_payload)
            response = _validate_response(entry.key, response_payload)
        except ValidationError as exc:
            raise TokenPolicyError(
                Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH,
                "golden fixture violates its operation-specific contract",
            ) from exc
        requests[entry.key] = _dump_model(request)
        responses[entry.key] = _dump_model(response)

    encoding = build_local_encoding(core, project_root)
    reproduced, envelopes = _derive_golden_manifest(
        core,
        core_sha256,
        encoding,
        requests,
        responses,
    )
    if reproduced != manifest:
        raise TokenPolicyError(
            Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH,
            "golden hashes or token counts do not reproduce",
        )
    return TokenAuthority(
        core=core,
        core_sha256=core_sha256,
        goldens=manifest,
        encoding=encoding,
        minimal_requests=MappingProxyType(requests),
        minimal_responses=MappingProxyType(responses),
        envelopes=envelopes,
    )


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        raise TokenPolicyError(
            Phase2FailureCode.TOKENIZER_ASSET_HASH_MISMATCH,
            "tokenizer asset redirect is forbidden",
        )


def _verified_payload(core: TokenPolicyCore) -> dict[str, object]:
    return {
        "status": "VERIFIED",
        "source_url": core.encoding_source_url,
        "expected_bytes": core.encoding_asset_bytes,
        "observed_bytes": core.encoding_asset_bytes,
        "expected_sha256": core.encoding_asset_sha256,
        "observed_sha256": core.encoding_asset_sha256,
        "tiktoken_version": core.tiktoken_version,
        "tiktoken_tag_commit": core.tiktoken_tag_commit,
        "verified_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"))
            stream.write(b"\n")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def acquire_tokenizer_asset(
    policy_path: Path,
    destination: Path,
    evidence: Path | None = None,
) -> None:
    """Install only the exact official asset, without replacing any destination."""

    core = _load_policy_path(policy_path)
    expected_destination = policy_path.parents[2] / core.encoding_asset_path
    if destination != expected_destination:
        raise TokenPolicyError(
            Phase2FailureCode.TOKENIZER_ASSET_HASH_MISMATCH,
            "destination is not the policy-bound project path",
        )
    if destination.exists() or destination.is_symlink():
        _verify_asset_file(destination, core)
        if evidence is not None:
            _write_json_atomic(evidence, _verified_payload(core))
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        core.encoding_source_url,
        headers={"Accept": "application/octet-stream"},
        method="GET",
    )
    opener = urllib.request.build_opener(_RejectRedirectHandler())
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary_path = Path(temporary_name)
    observed_bytes = 0
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as stream, opener.open(
            request, timeout=60
        ) as response:
            if response.geturl() != core.encoding_source_url:
                raise TokenPolicyError(
                    Phase2FailureCode.TOKENIZER_ASSET_HASH_MISMATCH,
                    "tokenizer asset response URL is not exact",
                )
            while True:
                chunk = response.read(_DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                observed_bytes += len(chunk)
                if observed_bytes > core.encoding_asset_bytes:
                    raise TokenPolicyError(
                        Phase2FailureCode.TOKENIZER_ASSET_SIZE_MISMATCH,
                        "tokenizer asset exceeded the exact byte limit",
                    )
                digest.update(chunk)
                stream.write(chunk)
        if observed_bytes != core.encoding_asset_bytes:
            raise TokenPolicyError(
                Phase2FailureCode.TOKENIZER_ASSET_SIZE_MISMATCH,
                "downloaded tokenizer asset size does not match",
            )
        if digest.hexdigest() != core.encoding_asset_sha256:
            raise TokenPolicyError(
                Phase2FailureCode.TOKENIZER_ASSET_HASH_MISMATCH,
                "downloaded tokenizer asset digest does not match",
            )
        try:
            os.link(temporary_path, destination)
        except FileExistsError as exc:
            raise TokenPolicyError(
                Phase2FailureCode.TOKENIZER_ASSET_HASH_MISMATCH,
                "destination appeared during acquisition",
            ) from exc
    finally:
        temporary_path.unlink(missing_ok=True)
    if evidence is not None:
        _write_json_atomic(evidence, _verified_payload(core))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    acquire = subparsers.add_parser("acquire")
    acquire.add_argument("--policy", type=Path, required=True)
    acquire.add_argument("--destination", type=Path, required=True)
    acquire.add_argument("--evidence", type=Path)
    regenerate = subparsers.add_parser("regenerate-goldens")
    regenerate.add_argument("--project-root", type=Path, required=True)
    regenerate.add_argument("--expected-core-sha256", required=True)
    regenerate.add_argument("--replace-drifted-manifest", action="store_true")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--project-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "acquire":
        acquire_tokenizer_asset(args.policy, args.destination, args.evidence)
    elif args.command == "regenerate-goldens":
        regenerate_token_goldens(
            args.project_root,
            expected_core_sha256=args.expected_core_sha256,
            replace_drifted_manifest=args.replace_drifted_manifest,
        )
    elif args.command == "verify":
        load_token_authority(args.project_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
