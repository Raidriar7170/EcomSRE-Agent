from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from ecomsre.product.connectors.opensearch_capture_analysis_v0222 import (
    analyze_capture_bundle_v0222,
)
from ecomsre.product.connectors.opensearch_capture_v0222 import (
    OpenSearchCaptureStoreV0222,
)
from ecomsre.product.connectors.opensearch_candidates_v0222 import (
    OpenSearchProfileRecommendationStatusV0222,
)
from tests.product_v0221.test_increment2_profile_resolution import (
    _field_caps,
    _mapping,
    _samples,
)
from tests.product_v0222.test_increment1_capture_first import _capture


def test_frozen_ambiguous_capture_builds_safe_candidate_set(tmp_path: Path) -> None:
    mapping = deepcopy(_mapping())
    properties = mapping["otel-v1-apm-span-0001"]["mappings"]["properties"]  # type: ignore[index]
    properties["event"] = {"properties": {"stringValue": {"type": "text"}}}  # type: ignore[index]
    field_caps = deepcopy(_field_caps())
    field_caps["fields"]["event.stringValue"] = {  # type: ignore[index]
        "text": {"type": "text", "searchable": True, "aggregatable": False}
    }
    samples = [deepcopy(sample) for sample in _samples()]
    for sample in samples:
        sample["event"] = deepcopy(sample["body"])
    search = {
        "timed_out": False,
        "_shards": {"failed": 0},
        "hits": {"hits": [{"_source": sample} for sample in samples]},
    }
    store = OpenSearchCaptureStoreV0222(
        private_root=tmp_path / "private",
        session_id="product-v0222-capture-1",
        maximum_response_bytes=2_000_000,
    )
    payloads = (
        ("resolved-index", "INDEX_RESOLUTION", {"indices": [{"name": "otel-logs-1"}]}),
        ("mapping", "MAPPING", mapping),
        ("field-caps", "FIELD_CAPS", field_caps),
        ("structural-sample", "STRUCTURAL_SAMPLE", search),
        (
            "service-aggregation",
            "SERVICE_AGGREGATION",
            {
                "aggregations": {
                    "services": {
                        "buckets": [{"key": "checkoutservice", "doc_count": 2}]
                    }
                }
            },
        ),
        ("timestamp-range", "TIMESTAMP_RANGE", search),
        ("profile-verification", "PROFILE_VERIFICATION", search),
    )
    for ordinal, (request_id, request_kind, payload) in enumerate(payloads, start=1):
        _capture(
            store,
            ordinal=ordinal,
            request_id=request_id,
            request_kind=request_kind,
            body=payload,
        )
    bundle = store.build_bundle()

    analysis = analyze_capture_bundle_v0222(
        private_root=tmp_path / "private",
        bundle=bundle,
        index_pattern="otel-v1-apm-span-*",
        checkout_aliases=("checkoutservice",),
    )

    assert analysis.public_summary.capture_bundle_sha256 == bundle.bundle_sha256
    assert analysis.candidate_set.capture_bundle_sha256 == bundle.bundle_sha256
    assert len(analysis.candidate_set.candidates) == 2
    assert analysis.candidate_set.recommendation_status is (
        OpenSearchProfileRecommendationStatusV0222.OPERATOR_SELECTION_REQUIRED
    )
    assert {
        candidate.profile_fields["message"]
        for candidate in analysis.candidate_set.candidates
    } == {"body.stringValue", "event.stringValue"}
    serialized = analysis.public_summary.model_dump_json()
    assert "checkout completed" not in serialized
    assert all(
        component.supporting_capture_refs for component in analysis.component_candidates
    )
