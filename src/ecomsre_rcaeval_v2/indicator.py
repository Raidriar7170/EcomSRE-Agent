"""Pre-registered deterministic indicator scoring for OB/SS development only."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean, median
from typing import Annotated, Literal

from pydantic import Field, StrictFloat, StrictInt, model_validator

from ecomsre_rcaeval_v2.contracts import (
    CanonicalIndicator,
    DevSystem,
    IndicatorResolutionV2,
    MetricServiceName,
    Sha256,
    V2Model,
)


_OB_SERVICE_PREFIXES = (
    "InboundPassthroughClusterIpv4",
    "PassthroughCluster",
    "adservice",
    "cartservice",
    "checkoutservice",
    "currencyservice",
    "emailservice",
    "frontend",
    "frontend-check",
    "frontend-external",
    "istio-init",
    "paymentservice",
    "productcatalogservice",
    "recommendationservice",
    "redis",
    "shippingservice",
)
_SS_SERVICE_PREFIXES = (
    "carts",
    "carts-db",
    "catalogue",
    "catalogue-db",
    "front-end",
    "istio-init",
    "orders",
    "orders-db",
    "payment",
    "queue-master",
    "rabbitmq",
    "rabbitmq-exporter",
    "session-db",
    "shipping",
    "user",
    "user-db",
)
_ROOT_SERVICE_ALLOWLISTS = {
    "RE2-OB": (
        "checkoutservice",
        "currencyservice",
        "emailservice",
        "productcatalogservice",
        "recommendationservice",
    ),
    "RE2-SS": ("carts", "catalogue", "orders", "payment", "user"),
}
_CANONICAL_SUFFIXES = {
    "_cpu": "cpu",
    "_diskio": "diskio",
    "_latency-50": "latency",
    "_latency-90": "latency",
    "_mem": "mem",
    "_socket": "socket",
}
_AUXILIARY_SUFFIXES = {"_error": "error", "_workload": "workload"}
_DATASET_SCHEMA_HASHES = {
    "RE2-OB": "1ec60a1a5c5fc95f56048d24c53c4c6ef671c98025e7a6cae13adf2a14b3105c",
    "RE2-SS": "2cbb47c37cb486892fd2fbb1d16482483220afec5ca6b83a8bb0f5ccbcddfcf8",
}
_FORMULA_ORDER: tuple[FormulaId, ...]


class FormulaId(str, Enum):
    F0 = "F0"
    F1 = "F1"
    F2 = "F2"


_FORMULA_ORDER = (FormulaId.F0, FormulaId.F1, FormulaId.F2)


class FormulaScoreStatus(str, Enum):
    SCORED = "SCORED"
    NO_USABLE_SERIES = "NO_USABLE_SERIES"


class MetricNameDisposition(str, Enum):
    CANONICAL = "CANONICAL"
    AUXILIARY = "AUXILIARY"
    UNKNOWN = "UNKNOWN"
    AMBIGUOUS = "AMBIGUOUS"


class _F0Config(V2Model):
    definition: Literal["abs(post_mean-pre_mean)/max(abs(pre_mean),epsilon)"]
    epsilon: StrictFloat = Field(gt=0.0)


class _F1Config(V2Model):
    definition: Literal[
        "abs(post_median-pre_median)/max(1.4826*pre_mad,scale_floor)"
    ]
    mad_multiplier: StrictFloat = Field(gt=0.0)
    scale_floor: StrictFloat = Field(gt=0.0)


class _F2Config(V2Model):
    clip: StrictFloat = Field(gt=0.0)
    definition: Literal[
        "max(min(F0,clip),min(F1,clip))+persistence_bonus"
    ]
    persistence_bonus: StrictFloat = Field(ge=0.0)
    persistence_gate: StrictFloat = Field(ge=0.0, le=1.0)
    persistence_threshold: Literal["max(3*1.4826*pre_mad,1e-9)"]


class _FormulaRegistry(V2Model):
    F0: _F0Config
    F1: _F1Config
    F2: _F2Config


class _NormalizationConfig(V2Model):
    auxiliary_suffixes: dict[str, Literal["error", "workload"]]
    canonical_suffixes: dict[str, CanonicalIndicator]
    casefold: Literal[False]
    root_service_allowlists: dict[DevSystem, tuple[str, ...]]
    service_prefix_allowlists: dict[DevSystem, tuple[str, ...]]
    trim: Literal[False]
    unknown_suffix_disposition: Literal["UNKNOWN"]
    unmapped_prefix_disposition: Literal["UNKNOWN"]

    @model_validator(mode="after")
    def require_exact_normalization_registry(self) -> _NormalizationConfig:
        if self.auxiliary_suffixes != _AUXILIARY_SUFFIXES:
            raise ValueError("auxiliary suffix registry differs from pre-registration")
        if self.canonical_suffixes != _CANONICAL_SUFFIXES:
            raise ValueError("canonical suffix registry differs from pre-registration")
        if self.service_prefix_allowlists != {
            "RE2-OB": _OB_SERVICE_PREFIXES,
            "RE2-SS": _SS_SERVICE_PREFIXES,
        }:
            raise ValueError("service prefix allowlist differs from pre-registration")
        if self.root_service_allowlists != _ROOT_SERVICE_ALLOWLISTS:
            raise ValueError("root service allowlist differs from pre-registration")
        return self


class _RankingConfig(V2Model):
    canonical_collapse_key: tuple[Literal["service", "canonical_indicator"], ...]
    metric_bytes_final_tie_break: Literal[True]
    primary_order: Literal["score_desc"]
    tie_breaker_domain: Literal["rcaeval-v2-indicator-tie-v1"]

    @model_validator(mode="after")
    def require_collapse_key(self) -> _RankingConfig:
        if self.canonical_collapse_key != ("service", "canonical_indicator"):
            raise ValueError("canonical collapse key differs from pre-registration")
        return self


class _SelectionGates(V2Model):
    ambiguous_count_max: Literal[0]
    eligible_unknown_count_max: Literal[0]
    memory_coverage_at_6_min: StrictFloat
    overall_coverage_at_6_min: StrictFloat
    per_fault_delta_from_f0_min: StrictFloat
    socket_coverage_at_6_min: StrictFloat

    @model_validator(mode="after")
    def require_fixed_gates(self) -> _SelectionGates:
        if (
            self.memory_coverage_at_6_min != 0.8
            or self.overall_coverage_at_6_min != 0.95
            or self.per_fault_delta_from_f0_min != -0.05
            or self.socket_coverage_at_6_min != 0.8
        ):
            raise ValueError("formula selection gates differ from pre-registration")
        return self


class _SelectionConfig(V2Model):
    formula_simplicity_order: tuple[Literal["F0", "F1", "F2"], ...]
    gates: _SelectionGates
    macro_tie_simpler_if_difference_lt: StrictFloat
    primary_metric: Literal["macro_truth_indicator_coverage_at_6"]

    @model_validator(mode="after")
    def require_fixed_selection(self) -> _SelectionConfig:
        if self.formula_simplicity_order != ("F0", "F1", "F2"):
            raise ValueError("formula simplicity order differs from pre-registration")
        if self.macro_tie_simpler_if_difference_lt != 0.01:
            raise ValueError("formula tie threshold differs from pre-registration")
        return self


class _WindowConfig(V2Model):
    post: Literal["[T0,T0+600]"]
    post_seconds: Literal[600]
    pre: Literal["[T0-600,T0)"]
    pre_seconds: Literal[600]


class _MetricValuePolicy(V2Model):
    initial_value: Literal["ZERO"]
    missing_timestamp: Literal["DROP_ROW"]
    missing_value: Literal["PREVIOUS_FINITE_OR_ZERO"]
    nonfinite_timestamp: Literal["FAIL_CLOSED"]
    nonfinite_value: Literal["PREVIOUS_FINITE_OR_ZERO"]
    row_order: Literal["PRESERVE"]


class IndicatorConfig(V2Model):
    classification: tuple[
        Literal["DEVELOPMENT_VISIBLE"],
        Literal["NOT_EXTERNAL_HOLDOUT"],
        Literal["NOT_PRIMARY_INFERENCE"],
    ]
    dataset_schema_sha256: dict[DevSystem, Sha256]
    dataset_lock_sha256: Sha256
    formulas: _FormulaRegistry
    metric_value_policy: _MetricValuePolicy
    normalization: _NormalizationConfig
    protocol_id: Literal["rcaeval-re2-v2-dev-v1"]
    protocol_sha256: Sha256
    ranking: _RankingConfig
    schema_version: Literal[
        "rcaeval-re2-v2.indicator-candidate-formulas.v2"
    ]
    selection: _SelectionConfig
    windows: _WindowConfig

    @model_validator(mode="after")
    def require_fixed_formula_parameters(self) -> IndicatorConfig:
        if self.dataset_schema_sha256 != _DATASET_SCHEMA_HASHES:
            raise ValueError("dataset schema hashes differ from pre-registration")
        if self.formulas.F0.epsilon != 1e-9:
            raise ValueError("F0 epsilon differs from pre-registration")
        if (
            self.formulas.F1.mad_multiplier != 1.4826
            or self.formulas.F1.scale_floor != 1e-9
        ):
            raise ValueError("F1 parameters differ from pre-registration")
        f2 = self.formulas.F2
        if (
            f2.clip != 20.0
            or f2.persistence_bonus != 1.0
            or f2.persistence_gate != 0.5
        ):
            raise ValueError("F2 parameters differ from pre-registration")
        return self


@dataclass(frozen=True, slots=True)
class LoadedIndicatorConfig:
    registry: IndicatorConfig
    sha256: str


class MetricSample(V2Model):
    timestamp: StrictFloat
    value: StrictFloat

    @model_validator(mode="after")
    def require_finite_sample(self) -> MetricSample:
        if not math.isfinite(self.timestamp) or not math.isfinite(self.value):
            raise ValueError("metric sample values must be finite")
        return self


class FormulaScore(V2Model):
    formula: FormulaId
    status: FormulaScoreStatus
    reason: str | None
    score: StrictFloat | None
    pre_count: StrictInt = Field(ge=0)
    post_count: StrictInt = Field(ge=0)
    pre_location: StrictFloat | None
    post_location: StrictFloat | None
    pre_scale: StrictFloat | None
    absolute_shift: StrictFloat | None
    relative_shift: StrictFloat | None
    robust_shift: StrictFloat | None
    persistence: StrictFloat | None = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def require_score_disposition(self) -> FormulaScore:
        numerical = (
            self.score,
            self.pre_location,
            self.post_location,
            self.pre_scale,
            self.absolute_shift,
            self.relative_shift,
            self.robust_shift,
            self.persistence,
        )
        if self.status is FormulaScoreStatus.SCORED:
            if self.reason is not None or any(value is None for value in numerical):
                raise ValueError("scored formula requires all finite statistics")
            if any(not math.isfinite(value) for value in numerical if value is not None):
                raise ValueError("formula statistics must be finite")
        elif self.reason is None or any(value is not None for value in numerical):
            raise ValueError("unusable formula series cannot claim statistics")
        return self


class MetricNormalization(V2Model):
    metric_name: str = Field(min_length=1, max_length=512)
    disposition: MetricNameDisposition
    service: MetricServiceName | None
    canonical_indicator: CanonicalIndicator | None
    auxiliary_kind: Literal["error", "workload"] | None

    @model_validator(mode="after")
    def require_normalization_disposition(self) -> MetricNormalization:
        if self.disposition is MetricNameDisposition.CANONICAL:
            if self.service is None or self.canonical_indicator is None:
                raise ValueError("canonical metric requires service and indicator")
            if self.auxiliary_kind is not None:
                raise ValueError("canonical metric cannot be auxiliary")
        elif self.disposition is MetricNameDisposition.AUXILIARY:
            if self.service is None or self.auxiliary_kind is None:
                raise ValueError("auxiliary metric requires service and kind")
            if self.canonical_indicator is not None:
                raise ValueError("auxiliary metric cannot be canonical")
        elif self.canonical_indicator is not None or self.auxiliary_kind is not None:
            raise ValueError("unknown or ambiguous metric cannot claim mapping")
        return self


class RawMetricCandidate(V2Model):
    system: DevSystem
    metric_name: str
    normalization: MetricNormalization
    formula: FormulaId
    formula_score: FormulaScore | None
    evidence_ref: str = Field(min_length=1, max_length=128)
    case_identity_sha256: Sha256
    eligible_unknown: bool
    config_sha256: Sha256


class MetricIndicatorCandidate(V2Model):
    service: MetricServiceName
    canonical_indicator: CanonicalIndicator
    metric_name: str
    formula: FormulaId
    score: StrictFloat
    score_method: str
    rank_within_service: StrictInt = Field(ge=1)
    rank_global: StrictInt = Field(ge=1)
    pre_count: StrictInt = Field(ge=1)
    post_count: StrictInt = Field(ge=1)
    pre_location: StrictFloat
    post_location: StrictFloat
    pre_scale: StrictFloat
    absolute_shift: StrictFloat
    relative_shift: StrictFloat
    robust_shift: StrictFloat
    persistence: StrictFloat = Field(ge=0.0, le=1.0)
    evidence_ref: str
    config_sha256: Sha256


class CoverageAtK(V2Model):
    numerator: StrictInt = Field(ge=0)
    denominator: StrictInt = Field(gt=0)
    value: StrictFloat = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def require_exact_rate(self) -> CoverageAtK:
        if self.numerator > self.denominator:
            raise ValueError("coverage numerator exceeds denominator")
        if self.value != self.numerator / self.denominator:
            raise ValueError("coverage value differs from raw counts")
        return self


FaultCoverage = Annotated[dict[str, CoverageAtK], Field(min_length=1)]


class FormulaEvaluation(V2Model):
    formula: FormulaId
    macro_truth_indicator_coverage_at_6: StrictFloat = Field(ge=0.0, le=1.0)
    overall_coverage_at_6: CoverageAtK
    memory_coverage_at_6: CoverageAtK
    socket_coverage_at_6: CoverageAtK
    per_fault_coverage_at_6: FaultCoverage
    eligible_unknown_count: StrictInt = Field(ge=0)
    ambiguous_count: StrictInt = Field(ge=0)
    auxiliary_metric_count: StrictInt = Field(ge=0)


class FormulaSelection(V2Model):
    selected_formula: FormulaId | None
    eligible_formulas: tuple[FormulaId, ...]
    rejections: dict[FormulaId, tuple[str, ...]]
    gate_passed: bool


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("indicator config contains a duplicate JSON key")
        result[key] = value
    return result


def load_indicator_config(
    path: Path, *, expected_sha256: str
) -> LoadedIndicatorConfig:
    path_text = str(path).casefold()
    if any(marker in path_text for marker in ("re2-tt", "tt-case-", ".ecomsre-private")):
        raise ValueError("indicator config path contains a forbidden TT/private marker")
    if (
        len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
        or expected_sha256 == "0" * 64
    ):
        raise ValueError("expected indicator config hash is invalid")
    if path.is_symlink() or not path.is_file():
        raise ValueError("indicator config must be a regular file")
    payload = path.read_bytes()
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError("indicator config hash mismatch")
    try:
        decoded = json.loads(
            payload,
            object_pairs_hook=_strict_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
        )
        registry = IndicatorConfig.model_validate(decoded, strict=False)
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("indicator config is invalid") from error
    return LoadedIndicatorConfig(registry=registry, sha256=actual_sha256)


def _require_loaded_config(config: LoadedIndicatorConfig) -> IndicatorConfig:
    if not isinstance(config, LoadedIndicatorConfig):
        raise TypeError("formula scoring requires a hash-verified indicator config")
    return config.registry


def _unusable(
    formula: FormulaId, reason: str, pre_count: int, post_count: int
) -> FormulaScore:
    return FormulaScore(
        formula=formula,
        status=FormulaScoreStatus.NO_USABLE_SERIES,
        reason=reason,
        score=None,
        pre_count=pre_count,
        post_count=post_count,
        pre_location=None,
        post_location=None,
        pre_scale=None,
        absolute_shift=None,
        relative_shift=None,
        robust_shift=None,
        persistence=None,
    )


def score_formula(
    samples: tuple[MetricSample, ...],
    injection_time: float,
    formula: FormulaId,
    config: LoadedIndicatorConfig,
) -> FormulaScore:
    registry = _require_loaded_config(config)
    if not isinstance(formula, FormulaId):
        raise TypeError("formula identifier must be typed")
    if type(injection_time) is not float or not math.isfinite(injection_time):
        raise ValueError("injection time must be a finite float")
    if any(not isinstance(sample, MetricSample) for sample in samples):
        raise TypeError("formula input must contain typed metric samples")
    pre_start = injection_time - float(registry.windows.pre_seconds)
    post_end = injection_time + float(registry.windows.post_seconds)
    pre = tuple(
        sample.value
        for sample in samples
        if pre_start <= sample.timestamp < injection_time
    )
    post = tuple(
        sample.value
        for sample in samples
        if injection_time <= sample.timestamp <= post_end
    )
    if not pre or not post:
        return _unusable(formula, "EMPTY_PRE_OR_POST_WINDOW", len(pre), len(post))

    pre_mean = fmean(pre)
    post_mean = fmean(post)
    pre_median = float(median(pre))
    post_median = float(median(post))
    pre_mad = float(median(tuple(abs(value - pre_median) for value in pre)))
    mean_shift = abs(post_mean - pre_mean)
    median_shift = abs(post_median - pre_median)
    relative_scale = max(abs(pre_mean), registry.formulas.F0.epsilon)
    robust_scale = max(
        registry.formulas.F1.mad_multiplier * pre_mad,
        registry.formulas.F1.scale_floor,
    )
    relative_shift = mean_shift / relative_scale
    robust_shift = median_shift / robust_scale
    persistence_threshold = max(
        3.0 * registry.formulas.F1.mad_multiplier * pre_mad,
        registry.formulas.F1.scale_floor,
    )
    median_delta = post_median - pre_median
    if median_delta == 0.0:
        persistence = 0.0
    else:
        direction = 1.0 if median_delta > 0.0 else -1.0
        persistence = sum(
            direction * (value - pre_median) >= persistence_threshold
            for value in post
        ) / len(post)
    primitives = (
        pre_mean,
        post_mean,
        pre_median,
        post_median,
        pre_mad,
        mean_shift,
        median_shift,
        relative_shift,
        robust_shift,
        persistence,
    )
    if any(not math.isfinite(value) for value in primitives):
        return _unusable(formula, "NON_FINITE_DERIVED_STATISTIC", len(pre), len(post))

    if formula is FormulaId.F0:
        score = relative_shift
        pre_location = pre_mean
        post_location = post_mean
        pre_scale = relative_scale
        absolute_shift = mean_shift
    elif formula is FormulaId.F1:
        score = robust_shift
        pre_location = pre_median
        post_location = post_median
        pre_scale = robust_scale
        absolute_shift = median_shift
    else:
        f2 = registry.formulas.F2
        score = max(min(relative_shift, f2.clip), min(robust_shift, f2.clip))
        if persistence >= f2.persistence_gate:
            score += f2.persistence_bonus
        pre_location = pre_median
        post_location = post_median
        pre_scale = robust_scale
        absolute_shift = median_shift
    if not math.isfinite(score):
        return _unusable(formula, "NON_FINITE_SCORE", len(pre), len(post))
    return FormulaScore(
        formula=formula,
        status=FormulaScoreStatus.SCORED,
        reason=None,
        score=float(score),
        pre_count=len(pre),
        post_count=len(post),
        pre_location=float(pre_location),
        post_location=float(post_location),
        pre_scale=float(pre_scale),
        absolute_shift=float(absolute_shift),
        relative_shift=float(relative_shift),
        robust_shift=float(robust_shift),
        persistence=float(persistence),
    )


def normalize_metric_name(
    system: str, metric_name: str, config: LoadedIndicatorConfig
) -> MetricNormalization:
    registry = _require_loaded_config(config)
    if system not in {"RE2-OB", "RE2-SS"}:
        raise ValueError("indicator normalization allows only OB/SS development systems")
    if not isinstance(metric_name, str) or not metric_name:
        raise ValueError("metric name must be a nonempty string")
    typed_system: DevSystem = system  # type: ignore[assignment]
    prefixes = registry.normalization.service_prefix_allowlists[typed_system]
    canonical_matches: list[tuple[str, CanonicalIndicator]] = []
    auxiliary_matches: list[tuple[str, Literal["error", "workload"]]] = []
    for prefix in prefixes:
        for suffix, indicator in registry.normalization.canonical_suffixes.items():
            if metric_name == prefix + suffix:
                canonical_matches.append((prefix, indicator))
        for suffix, kind in registry.normalization.auxiliary_suffixes.items():
            if metric_name == prefix + suffix:
                auxiliary_matches.append((prefix, kind))
    if len(canonical_matches) + len(auxiliary_matches) > 1:
        return MetricNormalization(
            metric_name=metric_name,
            disposition=MetricNameDisposition.AMBIGUOUS,
            service=None,
            canonical_indicator=None,
            auxiliary_kind=None,
        )
    if canonical_matches:
        service, indicator = canonical_matches[0]
        return MetricNormalization(
            metric_name=metric_name,
            disposition=MetricNameDisposition.CANONICAL,
            service=service,
            canonical_indicator=indicator,
            auxiliary_kind=None,
        )
    if auxiliary_matches:
        service, kind = auxiliary_matches[0]
        return MetricNormalization(
            metric_name=metric_name,
            disposition=MetricNameDisposition.AUXILIARY,
            service=service,
            canonical_indicator=None,
            auxiliary_kind=kind,
        )
    possible_services = tuple(
        prefix for prefix in prefixes if metric_name.startswith(prefix + "_")
    )
    unknown_service = possible_services[0] if len(possible_services) == 1 else None
    disposition = (
        MetricNameDisposition.AMBIGUOUS
        if len(possible_services) > 1
        else MetricNameDisposition.UNKNOWN
    )
    return MetricNormalization(
        metric_name=metric_name,
        disposition=disposition,
        service=unknown_service,
        canonical_indicator=None,
        auxiliary_kind=None,
    )


def score_metric_candidate(
    system: str,
    metric_name: str,
    samples: tuple[MetricSample, ...],
    injection_time: float,
    formula: FormulaId,
    evidence_ref: str,
    case_identity_sha256: str,
    config: LoadedIndicatorConfig,
) -> RawMetricCandidate:
    registry = _require_loaded_config(config)
    normalization = normalize_metric_name(system, metric_name, config)
    formula_score = (
        score_formula(samples, injection_time, formula, config)
        if normalization.disposition is MetricNameDisposition.CANONICAL
        else None
    )
    typed_system: DevSystem = system  # type: ignore[assignment]
    eligible_unknown = (
        normalization.disposition is MetricNameDisposition.UNKNOWN
        and normalization.service
        in registry.normalization.root_service_allowlists[typed_system]
    )
    return RawMetricCandidate(
        system=typed_system,
        metric_name=metric_name,
        normalization=normalization,
        formula=formula,
        formula_score=formula_score,
        evidence_ref=evidence_ref,
        case_identity_sha256=case_identity_sha256,
        eligible_unknown=eligible_unknown,
        config_sha256=config.sha256,
    )


def _candidate_sort_key(
    candidate: RawMetricCandidate, config: LoadedIndicatorConfig
) -> tuple[float, bytes, bytes]:
    score = candidate.formula_score
    if score is None or score.score is None:
        raise ValueError("unscored candidate cannot be ranked")
    domain = config.registry.ranking.tie_breaker_domain.encode("utf-8")
    metric_bytes = candidate.metric_name.encode("utf-8")
    normalization = candidate.normalization
    if normalization.service is None or normalization.canonical_indicator is None:
        raise ValueError("tie-ranked candidate lacks canonical identity")
    tie_digest = hashlib.sha256(
        b"\0".join(
            (
                domain,
                candidate.system.encode("utf-8"),
                candidate.case_identity_sha256.encode("ascii"),
                normalization.service.encode("utf-8"),
                normalization.canonical_indicator.encode("ascii"),
                metric_bytes,
            )
        )
    ).digest()
    return (-score.score, tie_digest, metric_bytes)


def collapse_and_rank_candidates(
    raw_candidates: tuple[RawMetricCandidate, ...],
    config: LoadedIndicatorConfig,
) -> tuple[MetricIndicatorCandidate, ...]:
    _require_loaded_config(config)
    canonical = tuple(
        item
        for item in raw_candidates
        if item.normalization.disposition is MetricNameDisposition.CANONICAL
        and item.formula_score is not None
        and item.formula_score.status is FormulaScoreStatus.SCORED
    )
    if any(item.config_sha256 != config.sha256 for item in raw_candidates):
        raise ValueError("raw candidate config hash mismatch")
    grouped: dict[tuple[str, str], list[RawMetricCandidate]] = {}
    for item in canonical:
        service = item.normalization.service
        indicator = item.normalization.canonical_indicator
        if service is None or indicator is None:
            raise ValueError("canonical raw candidate lacks normalized identity")
        grouped.setdefault((service, indicator), []).append(item)
    collapsed = tuple(
        sorted(group, key=lambda item: _candidate_sort_key(item, config))[0]
        for group in grouped.values()
    )
    ordered = tuple(sorted(collapsed, key=lambda item: _candidate_sort_key(item, config)))
    service_ranks: dict[str, int] = {}
    ranked: list[MetricIndicatorCandidate] = []
    for global_rank, item in enumerate(ordered, 1):
        normalization = item.normalization
        score = item.formula_score
        if (
            normalization.service is None
            or normalization.canonical_indicator is None
            or score is None
            or score.score is None
            or score.pre_location is None
            or score.post_location is None
            or score.pre_scale is None
            or score.absolute_shift is None
            or score.relative_shift is None
            or score.robust_shift is None
            or score.persistence is None
        ):
            raise ValueError("ranked candidate lacks formula statistics")
        within = service_ranks.get(normalization.service, 0) + 1
        service_ranks[normalization.service] = within
        ranked.append(
            MetricIndicatorCandidate(
                service=normalization.service,
                canonical_indicator=normalization.canonical_indicator,
                metric_name=item.metric_name,
                formula=item.formula,
                score=score.score,
                score_method=item.formula.value,
                rank_within_service=within,
                rank_global=global_rank,
                pre_count=score.pre_count,
                post_count=score.post_count,
                pre_location=score.pre_location,
                post_location=score.post_location,
                pre_scale=score.pre_scale,
                absolute_shift=score.absolute_shift,
                relative_shift=score.relative_shift,
                robust_shift=score.robust_shift,
                persistence=score.persistence,
                evidence_ref=item.evidence_ref,
                config_sha256=item.config_sha256,
            )
        )
    return tuple(ranked)


def resolve_indicator(
    judge_service: str,
    candidates: tuple[MetricIndicatorCandidate, ...],
) -> IndicatorResolutionV2:
    selected = tuple(
        item
        for item in candidates
        if item.service == judge_service and item.rank_within_service == 1
    )
    if not selected:
        return IndicatorResolutionV2(
            selected_service=judge_service,
            disposition="NO_INDICATOR_CANDIDATE",
            resolved_indicator=None,
            selected_metric=None,
            evidence_ref=None,
        )
    if len(selected) != 1:
        raise ValueError("judge service has multiple canonical rank-one candidates")
    candidate = selected[0]
    return IndicatorResolutionV2(
        selected_service=judge_service,
        disposition="RESOLVED",
        resolved_indicator=candidate.canonical_indicator,
        selected_metric=candidate.metric_name,
        evidence_ref=candidate.evidence_ref,
    )


def select_formula(
    evaluations: tuple[FormulaEvaluation, ...],
    config: LoadedIndicatorConfig,
) -> FormulaSelection:
    registry = _require_loaded_config(config)
    indexed = {item.formula: item for item in evaluations}
    if len(indexed) != len(evaluations) or set(indexed) != set(_FORMULA_ORDER):
        raise ValueError("formula selection requires exactly one F0/F1/F2 evaluation")
    baseline = indexed[FormulaId.F0]
    rejections: dict[FormulaId, tuple[str, ...]] = {}
    for formula in _FORMULA_ORDER:
        item = indexed[formula]
        reasons: list[str] = []
        gates = registry.selection.gates
        if item.overall_coverage_at_6.value < gates.overall_coverage_at_6_min:
            reasons.append("OVERALL_GATE")
        if item.memory_coverage_at_6.value < gates.memory_coverage_at_6_min:
            reasons.append("MEMORY_GATE")
        if item.socket_coverage_at_6.value < gates.socket_coverage_at_6_min:
            reasons.append("SOCKET_GATE")
        if set(item.per_fault_coverage_at_6) != set(
            baseline.per_fault_coverage_at_6
        ):
            reasons.append("PER_FAULT_SET_MISMATCH")
        elif any(
            item.per_fault_coverage_at_6[fault].value
            - baseline.per_fault_coverage_at_6[fault].value
            < gates.per_fault_delta_from_f0_min
            for fault in baseline.per_fault_coverage_at_6
        ):
            reasons.append("PER_FAULT_REGRESSION")
        if item.eligible_unknown_count > gates.eligible_unknown_count_max:
            reasons.append("ELIGIBLE_UNKNOWN_NONZERO")
        if item.ambiguous_count > gates.ambiguous_count_max:
            reasons.append("AMBIGUOUS_NONZERO")
        rejections[formula] = tuple(reasons)
    eligible = tuple(formula for formula in _FORMULA_ORDER if not rejections[formula])
    if not eligible:
        return FormulaSelection(
            selected_formula=None,
            eligible_formulas=(),
            rejections=rejections,
            gate_passed=False,
        )
    best_macro = max(
        indexed[formula].macro_truth_indicator_coverage_at_6 for formula in eligible
    )
    near_best = tuple(
        formula
        for formula in eligible
        if best_macro - indexed[formula].macro_truth_indicator_coverage_at_6
        < registry.selection.macro_tie_simpler_if_difference_lt
    )
    selected = min(near_best, key=_FORMULA_ORDER.index)
    return FormulaSelection(
        selected_formula=selected,
        eligible_formulas=eligible,
        rejections=rejections,
        gate_passed=True,
    )
