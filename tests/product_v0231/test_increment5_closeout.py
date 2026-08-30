from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from scripts.ci.verify_product_v0231_result import (
    _PUBLIC_OUTPUTS_V0231,
    _verify_product_v0231_result_payloads,
    verify_product_v0231_result,
)


ROOT = Path(__file__).resolve().parents[2]
_SUPPORTING_INPUTS = (
    "config/product-v0231/continuity/campaign.json",
    "config/product-v0231/continuity/nofault-profile-binding.json",
    "config/product-v023/environment.otel-demo.json",
    "config/product-v023/nofault/profile.json",
    "docs/analysis/product-v0231-flagd-bind-descriptor.json",
    "docs/analysis/product-v0231-runtime-authority-descriptor.json",
)


def _copy_inputs(target: Path) -> None:
    for relative in (*_SUPPORTING_INPUTS, *_PUBLIC_OUTPUTS_V0231):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)


def _load(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _reseal(payload: dict[str, object], field: str) -> None:
    payload.pop(field, None)
    payload[field] = semantic_sha256_v22(payload)


def test_frozen_v0231_result_is_self_consistent_and_non_authorizing() -> None:
    result = verify_product_v0231_result(ROOT)

    assert result == {
        "terminal": "ECOMSRE_PRODUCT_V0231_NOFAULT_ACCEPTANCE_COMPLETE",
        "measured_terminal": "ECOMSRE_PRODUCT_V0231_NOFAULT_NOT_SUPPORTED",
        "execution_head": "e2c2f640d34a9bd928e32d8394894fd54d93722a",
        "frozen_evidence_commit": "505f16eb344e8dd6253c16437ff7e0ba8e5debab",
        "public_output_count": 9,
        "live_session_count": 1,
        "incident_count": 1,
        "diagnosis_count": 1,
        "fault_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "action_authority": "NONE",
    }


def test_frozen_v0231_bytes_reject_a_success_relabel(tmp_path: Path) -> None:
    _copy_inputs(tmp_path)
    path = tmp_path / "docs/results/product-v0231-nofault-acceptance.json"
    payload = _load(path)
    payload["terminal"] = "ECOMSRE_PRODUCT_V0231_NOFAULT_FULLY_SUPPORTED"
    _write(path, payload)

    with pytest.raises(ValueError, match="frozen output bytes differ"):
        verify_product_v0231_result(tmp_path)


@pytest.mark.parametrize(
    "case",
    (
        "public_counter",
        "public_cleanup",
        "public_authority_cross_binding",
        "public_cleanup_proof_cross_binding",
        "session_count",
        "session_start_cross_binding",
        "restart_completion_cross_binding",
        "resealed_reasons",
        "resealed_authorizing_handoff",
        "resealed_handoff_evidence_cross_binding",
        "source_profile_bytes",
        "resealed_campaign_baseline",
    ),
)
def test_v0231_payload_contracts_reject_resealed_tampering(
    tmp_path: Path,
    case: str,
) -> None:
    _copy_inputs(tmp_path)
    public_path = tmp_path / "docs/results/product-v0231-nofault-acceptance.json"
    session_path = tmp_path / "docs/analysis/product-v0231-continuation-session-1.json"
    restart_path = tmp_path / "docs/analysis/product-v0231-baseline-restart.json"
    handoff_path = tmp_path / "docs/analysis/product-v0231-knowledge-loop-handoff.json"
    campaign_path = tmp_path / "config/product-v0231/continuity/campaign.json"
    source_profile_path = tmp_path / "config/product-v023/nofault/profile.json"
    public = _load(public_path)
    session = _load(session_path)
    restart = _load(restart_path)
    handoff = _load(handoff_path)
    campaign = _load(campaign_path)

    if case == "public_counter":
        public["incident_count"] = 0
        _write(public_path, public)
    elif case == "public_cleanup":
        public["product_cleanup"] = "BLOCKED"
        _write(public_path, public)
    elif case == "public_authority_cross_binding":
        public["runtime_authority_proof_sha256"] = "0" * 64
        _write(public_path, public)
    elif case == "public_cleanup_proof_cross_binding":
        public["cleanup_proof_sha256"] = "0" * 64
        _write(public_path, public)
    elif case == "session_count":
        ledger = session["ledger"]
        assert isinstance(ledger, dict)
        ledger["live_session_count"] = 2
        _reseal(ledger, "ledger_sha256")
        _reseal(session, "report_sha256")
        _write(session_path, session)
    elif case == "session_start_cross_binding":
        start = session["start"]
        assert isinstance(start, dict)
        start["start_sha256"] = "0" * 64
        _reseal(session, "report_sha256")
        _write(session_path, session)
    elif case == "restart_completion_cross_binding":
        restart["session_completion_sha256"] = "0" * 64
        _reseal(restart, "report_sha256")
        _write(restart_path, restart)
    elif case == "resealed_reasons":
        result = public["result"]
        assert isinstance(result, dict)
        wrapped = result["wrapped_v023_result"]
        assert isinstance(wrapped, dict)
        wrapped["reasons"] = ["FRESH_HEALTHY_RUNTIME_MISSING"]
        _reseal(wrapped, "result_sha256")
        _reseal(result, "result_sha256")
        _write(public_path, public)
    elif case == "resealed_authorizing_handoff":
        handoff["authorized"] = True
        handoff["fault_calibration_authorized"] = True
        _reseal(handoff, "handoff_sha256")
        _write(handoff_path, handoff)
    elif case == "resealed_handoff_evidence_cross_binding":
        handoff["evidence_bundle_sha256"] = "0" * 64
        _reseal(handoff, "handoff_sha256")
        _write(handoff_path, handoff)
    elif case == "source_profile_bytes":
        source_profile_path.write_bytes(source_profile_path.read_bytes() + b" ")
    elif case == "resealed_campaign_baseline":
        campaign["active_baseline_sha256"] = "0" * 64
        _reseal(campaign, "campaign_sha256")
        _write(campaign_path, campaign)
    else:
        raise AssertionError(f"unhandled case: {case}")

    with pytest.raises(ValueError):
        _verify_product_v0231_result_payloads(tmp_path)
