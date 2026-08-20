from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.dta_v2.v22.controller_modes import ProviderOutputModeV22
from ecomsre.dta_v2.v22.provider_protocol_v4 import (
    ProviderBoundaryProbeReportV4,
)
from scripts.ci import verify_dta_v22_pr_d_v4 as verifier


REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_semantic_object(
    path: Path, value: dict[str, object], digest_key: str
) -> None:
    payload = {key: item for key, item in value.items() if key != digest_key}
    path.write_text(
        json.dumps(
            {**payload, digest_key: semantic_sha256_v22(payload)},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_v4_pre_execution_admission_binds_manifest_history_and_progress() -> None:
    manifest = verifier.verify_pre_execution_admission_v4(REPO_ROOT)
    assert manifest["decision_id"] == "DEC-058"
    assert manifest["pre_execution_state"] == "V4_EXECUTION_READY"
    assert manifest["projection_max_bytes_observed"] <= 12_000
    assert manifest["projection_mean_bytes_observed"] <= 8_000
    assert manifest["projected_input_token_max"] <= 5_500
    assert manifest["projected_input_token_mean"] <= 4_000
    assert manifest["projected_input_tokens_per_minute"] <= 30_000
    assert verifier.MANIFEST_RELATIVE_V4 == Path(
        "config/dta-v22/provider-gate/pr-d-provider-boundary-v4-manifest.json"
    )


def test_v4_hosted_admission_does_not_require_private_v3_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_home() -> Path:
        raise AssertionError("hosted verifier resolved a private home path")

    monkeypatch.setattr(verifier.Path, "home", forbidden_home)
    verifier.verify_pre_execution_admission_v4(
        REPO_ROOT,
        require_private_history=False,
    )


def test_v4_local_admission_checks_private_file_mode_and_raw_bytes(
    tmp_path: Path,
) -> None:
    private = tmp_path / "provider-protocol-v3"
    private.mkdir()
    path = private / "fixture.json"
    content = b'{"historical":true}\n'
    path.write_bytes(content)
    path.chmod(0o600)
    bindings = {"fixture.json": hashlib.sha256(content).hexdigest()}
    verifier._verify_private_history_v4(
        private,
        bindings,
    )
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private v3 historical binding differs"):
        verifier._verify_private_history_v4(private, bindings)


def test_v4_private_execution_files_require_regular_mode_0600(
    tmp_path: Path,
) -> None:
    path = tmp_path / "probe.json"
    path.write_text("{}\n", encoding="utf-8")
    path.chmod(0o600)
    assert verifier._load_private_object_v4(path) == {}
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private v4 evidence authority differs"):
        verifier._load_private_object_v4(path)


def test_v4_private_history_binds_raw_semantic_and_embedded_evidence(
    tmp_path: Path,
) -> None:
    payload = {"schema_version": "fixture.v1", "terminal": "BLOCKED"}
    evidence_sha = semantic_sha256_v22(payload)
    value = {**payload, "evidence_sha256": evidence_sha}
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    path.chmod(0o600)
    verifier._verify_private_history_v4(
        tmp_path,
        {path.name: hashlib.sha256(path.read_bytes()).hexdigest()},
        {path.name: semantic_sha256_v22(value)},
        {path.name: evidence_sha},
    )
    with pytest.raises(ValueError, match="evidence binding differs"):
        verifier._verify_private_history_v4(
            tmp_path,
            {path.name: hashlib.sha256(path.read_bytes()).hexdigest()},
            {path.name: semantic_sha256_v22(value)},
            {path.name: "0" * 64},
        )


def test_v4_manifest_semantic_tamper_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = REPO_ROOT / verifier.MANIFEST_RELATIVE_V4
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["minimum_request_start_interval_seconds"] = 1.0
    tampered = tmp_path / "manifest.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(verifier, "MANIFEST_RELATIVE_V4", tampered)
    with pytest.raises(ValueError, match="manifest semantic hash differs"):
        verifier.load_and_verify_manifest_v4(REPO_ROOT)


def test_v4_public_results_are_absent_before_formal_campaign() -> None:
    assert verifier.verify_public_results_v4(REPO_ROOT) is None


def test_v4_public_leakage_scan_rejects_private_path_and_credential(
    tmp_path: Path,
) -> None:
    private_path = tmp_path / "private-path.json"
    private_path.write_text(
        json.dumps({"path": "/Users/example/private/evidence.json"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="public leakage scan"):
        verifier._verify_public_leakage_v4((private_path,))

    credential = tmp_path / "credential.json"
    credential.write_text(
        json.dumps({"authorization": "Bearer fixture-secret-value"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="public leakage scan"):
        verifier._verify_public_leakage_v4((credential,))


@pytest.mark.parametrize(
    "forbidden_key",
    ("raw_provider_text", "base_url", "full_raw_evidence"),
)
def test_v4_public_leakage_scan_rejects_forbidden_structured_fields(
    tmp_path: Path,
    forbidden_key: str,
) -> None:
    path = tmp_path / "forged.json"
    path.write_text(json.dumps({forbidden_key: "redacted-fixture"}), encoding="utf-8")
    with pytest.raises(ValueError, match="public leakage scan"):
        verifier._verify_public_leakage_v4((path,))


@pytest.mark.parametrize(
    "mutation",
    (
        {"raw_provider_text": "redacted-fixture"},
        {"base_url": "provider.invalid"},
        {"full_raw_evidence": ["redacted-fixture"]},
        {"executed_at": "not-a-timestamp"},
    ),
)
def test_v4_public_replicate_rejects_rehashed_open_envelope(
    tmp_path: Path,
    mutation: dict[str, object],
) -> None:
    path = tmp_path / "replicate.json"
    value: dict[str, object] = {
        "schema_version": "dta-v22-pr-d-provider-boundary-v4-replicate-result.v1",
        "executed_at": "2026-08-20T00:00:00+00:00",
        "report": {},
        "private_raw_sha256": "1" * 64,
        "private_semantic_sha256": "2" * 64,
        **mutation,
    }
    _write_semantic_object(path, value, "result_sha256")
    with pytest.raises(ValueError, match="public replicate envelope"):
        verifier._verify_public_replicate(path)


def test_v4_result_identity_rejects_rehashed_manifest_and_probe_forgery() -> None:
    manifest = {"manifest_sha256": "1" * 64}
    campaign = {
        "manifest_sha256": "2" * 64,
        "probe_binding": {"probe_report_sha256": "3" * 64},
        "selected_mode": "STRICT_STRUCTURED_OUTPUT",
    }
    with pytest.raises(ValueError, match="manifest binding"):
        verifier._verify_result_identity_v4(
            manifest=manifest,
            campaign=campaign,
            probe_report=None,
            reports={},
        )


def test_v4_result_identity_rejects_probe_summary_forgery() -> None:
    probe_report = ProviderBoundaryProbeReportV4.model_construct(
        schema_version="dta-v22.provider-boundary-probe-report.v4",
        model="gpt-5.4-mini-2026-03-17",
        selected_mode=ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT,
        provider_request_sha256="1" * 64,
        schema_sha256="2" * 64,
        prompt_sha256="3" * 64,
        supported=True,
        provider_calls=1,
        attempts=(),
        report_sha256="4" * 64,
    )
    campaign = {
        "schema_version": "dta-v22-pr-d-provider-boundary-v4-campaign-result.v1",
        "goal_version": verifier.GOAL_VERSION_V4,
        "amendment_version": verifier.AMENDMENT_VERSION_V4,
        "decision_id": "DEC-058",
        "manifest_sha256": "5" * 64,
        "selected_mode": "STRICT_STRUCTURED_OUTPUT",
        "probe_binding": {
            "manifest_sha256": "5" * 64,
            "private_raw_sha256": "6" * 64,
            "private_semantic_sha256": "7" * 64,
            "probe_evidence_sha256": "8" * 64,
            "supported": False,
            "provider_calls": 2,
            "selected_mode": "STRICT_STRUCTURED_OUTPUT",
            "attempted_modes": ["STRICT_STRUCTURED_OUTPUT"],
            "failure_class": None,
            "safe_failure_code": None,
            "probe_report_sha256": probe_report.report_sha256,
            "probe_report": probe_report.model_dump(mode="json"),
            "manifest_binding_raw_sha256": "9" * 64,
            "manifest_binding_semantic_sha256": "a" * 64,
        },
    }
    with pytest.raises(ValueError, match="probe report binding"):
        verifier._verify_result_identity_v4(
            manifest={"manifest_sha256": "5" * 64},
            campaign=campaign,
            probe_report=probe_report,
            reports={},
        )


def test_v4_result_identity_rejects_boolean_provider_call_count() -> None:
    probe_report = ProviderBoundaryProbeReportV4.model_construct(
        schema_version="dta-v22.provider-boundary-probe-report.v4",
        model="gpt-5.4-mini-2026-03-17",
        selected_mode=ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT,
        provider_request_sha256="1" * 64,
        schema_sha256="2" * 64,
        prompt_sha256="3" * 64,
        supported=True,
        provider_calls=1,
        attempts=(),
        report_sha256="4" * 64,
    )
    binding = {
        "manifest_sha256": "5" * 64,
        "private_raw_sha256": "6" * 64,
        "private_semantic_sha256": "7" * 64,
        "probe_evidence_sha256": "8" * 64,
        "supported": True,
        "provider_calls": True,
        "selected_mode": "STRICT_STRUCTURED_OUTPUT",
        "attempted_modes": [],
        "failure_class": None,
        "safe_failure_code": None,
        "probe_report_sha256": probe_report.report_sha256,
        "probe_report": probe_report.model_dump(mode="json"),
        "manifest_binding_raw_sha256": "9" * 64,
        "manifest_binding_semantic_sha256": "a" * 64,
    }
    campaign = {
        "schema_version": "dta-v22-pr-d-provider-boundary-v4-campaign-result.v1",
        "goal_version": verifier.GOAL_VERSION_V4,
        "amendment_version": verifier.AMENDMENT_VERSION_V4,
        "decision_id": "DEC-058",
        "manifest_sha256": "5" * 64,
        "selected_mode": "STRICT_STRUCTURED_OUTPUT",
        "probe_binding": binding,
    }
    with pytest.raises(ValueError, match="probe report binding"):
        verifier._verify_result_identity_v4(
            manifest={"manifest_sha256": "5" * 64},
            campaign=campaign,
            probe_report=probe_report,
            reports={},
        )


def test_v4_private_manifest_binding_recomputes_public_raw_and_semantic_hashes(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "private"
    private_root.mkdir(mode=0o700)
    payload = {
        "schema_version": "dta-v22-pr-d-provider-boundary-v4-manifest-binding.v1",
        "implementation_commit": "a" * 40,
        "implementation_tree": "b" * 40,
        "manifest_sha256": "c" * 64,
        "bound_at": "2026-08-20T00:00:00+00:00",
    }
    binding = {**payload, "binding_sha256": semantic_sha256_v22(payload)}
    binding_path = private_root / "manifest-binding.json"
    binding_path.write_text(json.dumps(binding) + "\n", encoding="utf-8")
    binding_path.chmod(0o600)
    campaign = {
        "implementation_commit": payload["implementation_commit"],
        "implementation_tree": payload["implementation_tree"],
        "probe_binding": {
            "manifest_binding_raw_sha256": "f" * 64,
            "manifest_binding_semantic_sha256": "e" * 64,
        },
    }
    with pytest.raises(ValueError, match="private v4 manifest binding"):
        verifier._verify_private_manifest_binding_v4(
            private_root=private_root,
            manifest_sha256=str(payload["manifest_sha256"]),
            public_campaign=campaign,
        )


def test_v4_post_state_requires_human_brief_and_runs_leakage_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = {
        "implementation_commit": "a" * 40,
        "merge_ready": False,
        "terminal": "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE",
    }
    monkeypatch.setattr(
        verifier,
        "verify_pre_execution_admission_v4",
        lambda _root: {"manifest_sha256": "1" * 64},
    )
    monkeypatch.setattr(
        verifier,
        "verify_public_results_v4",
        lambda _root, manifest: campaign,
    )
    monkeypatch.setattr(
        verifier,
        "_changed_paths",
        lambda _root, _base: set(verifier.COMMIT_B_PATHS_V4),
    )
    monkeypatch.setattr(verifier, "_verify_progress_v4", lambda *_args, **_kwargs: None)
    with pytest.raises(ValueError, match="required public closure artifacts"):
        verifier.verify_repository_v4(tmp_path)


def test_v4_post_artifacts_bind_ci_review_campaign_and_activity_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    implementation_commit = "a" * 40
    implementation_tree = "b" * 40
    manifest = {"manifest_sha256": "c" * 64}
    campaign = {
        "implementation_commit": implementation_commit,
        "implementation_tree": implementation_tree,
        "campaign_sha256": "d" * 64,
        "observed_provider_calls": 49,
        "terminal": "DTA_V22_PR_D_CONTROLLER_READY",
        "merge_ready": True,
    }
    brief = tmp_path / verifier.HUMAN_BRIEF_RELATIVE_V4
    disposition_path = tmp_path / verifier.DISPOSITION_RELATIVE_V4
    attestation_path = tmp_path / verifier.ADMIN_ATTESTATION_RELATIVE_V4
    for path in (brief, disposition_path, attestation_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text(
        "\n".join(
            (
                "DEC-058",
                verifier.AMENDMENT_VERSION_V4,
                implementation_commit,
                str(campaign["terminal"]),
            )
        ),
        encoding="utf-8",
    )
    disposition_payload = {
        "schema_version": "dta-v22-pr-d-provider-boundary-v4.current-disposition.v1",
        "goal_version": verifier.GOAL_VERSION_V4,
        "amendment_version": verifier.AMENDMENT_VERSION_V4,
        "decision_id": "DEC-058",
        "implementation_commit": implementation_commit,
        "implementation_tree": implementation_tree,
        "manifest_sha256": manifest["manifest_sha256"],
        "campaign_sha256": campaign["campaign_sha256"],
        "pre_execution_exact_head_ci_head": implementation_commit,
        "pre_execution_exact_head_ci_run_id": 123,
        "pre_execution_exact_head_ci_run_url": (
            "https://github.com/Raidriar7170/EcomSRE-Agent/actions/runs/123"
        ),
        "pre_execution_exact_head_ci_status": "PASS",
        "pre_execution_independent_review_head": implementation_commit,
        "pre_execution_independent_review_must_fix_count": 0,
        "pre_execution_claim_accuracy": "PASS",
        "terminal": campaign["terminal"],
        "merge_ready": campaign["merge_ready"],
    }
    _write_semantic_object(
        disposition_path,
        disposition_payload,
        "disposition_sha256",
    )
    changed_paths = sorted(
        (
            verifier.HUMAN_BRIEF_RELATIVE_V4.as_posix(),
            verifier.DISPOSITION_RELATIVE_V4.as_posix(),
            verifier.ADMIN_ATTESTATION_RELATIVE_V4.as_posix(),
        )
    )
    attestable_paths = [
        path
        for path in changed_paths
        if path != verifier.ADMIN_ATTESTATION_RELATIVE_V4.as_posix()
    ]
    attestation_payload = {
        "schema_version": (
            "dta-v22-pr-d-provider-boundary-v4-administrative-attestation.v1"
        ),
        "goal_version": verifier.GOAL_VERSION_V4,
        "amendment_version": verifier.AMENDMENT_VERSION_V4,
        "decision_id": "DEC-058",
        "repository": "Raidriar7170/EcomSRE-Agent",
        "pr": 60,
        "starting_head": verifier.STARTING_HEAD_V4,
        "starting_tree": verifier.STARTING_TREE_V4,
        "implementation_commit": implementation_commit,
        "implementation_tree": implementation_tree,
        "commit_b_parent": implementation_commit,
        "changed_paths": changed_paths,
        "artifact_raw_sha256_by_path": {
            path: hashlib.sha256((tmp_path / path).read_bytes()).hexdigest()
            for path in attestable_paths
        },
        "provider_called": True,
        "provider_call_count": 49,
        "docker_called": False,
        "held_out_executed": False,
        "scenario_executed": False,
        "fault_injected": False,
        "agent_evidence_dispatched": False,
        "agent_write_executed": False,
        "runbook_executed": False,
        "private_evidence_changed": True,
        "public_result_changed": True,
        "third_v3_replicate_executed": False,
        "execution_report_rebound": False,
        "campaign_sha256": campaign["campaign_sha256"],
        "terminal": campaign["terminal"],
    }
    _write_semantic_object(attestation_path, attestation_payload, "record_sha256")
    monkeypatch.setattr(verifier, "_verify_commit_b_topology_v4", lambda *_args: None)
    monkeypatch.setattr(
        verifier,
        "_changed_paths",
        lambda _root, _base: set(changed_paths),
    )
    verifier._verify_post_execution_artifacts_v4(
        tmp_path,
        manifest=manifest,
        campaign=campaign,
    )

    forged = json.loads(disposition_path.read_text(encoding="utf-8"))
    forged["pre_execution_claim_accuracy"] = "FAIL"
    forged_payload = {
        key: value for key, value in forged.items() if key != "disposition_sha256"
    }
    forged["disposition_sha256"] = semantic_sha256_v22(forged_payload)
    disposition_path.write_text(json.dumps(forged), encoding="utf-8")
    with pytest.raises(ValueError, match="review disposition binding"):
        verifier._verify_post_execution_artifacts_v4(
            tmp_path,
            manifest=manifest,
            campaign=campaign,
        )


def test_v4_two_commit_topology_requires_exact_single_parent_children(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    implementation_commit = "a" * 40
    result_commit = "b" * 40

    def exact_git(_root: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return result_commit
        if args == ("rev-list", "--parents", "-n", "1", implementation_commit):
            return f"{implementation_commit} {verifier.STARTING_HEAD_V4}"
        if args == ("rev-list", "--parents", "-n", "1", result_commit):
            return f"{result_commit} {implementation_commit}"
        raise AssertionError(args)

    monkeypatch.setattr(verifier, "_git", exact_git)
    verifier._verify_commit_b_topology_v4(tmp_path, implementation_commit)

    def extra_commit_git(_root: Path, *args: str) -> str:
        if args == ("rev-list", "--parents", "-n", "1", result_commit):
            return f"{result_commit} {'c' * 40}"
        return exact_git(_root, *args)

    monkeypatch.setattr(verifier, "_git", extra_commit_git)
    with pytest.raises(ValueError, match="Commit B"):
        verifier._verify_commit_b_topology_v4(tmp_path, implementation_commit)


def test_v4_verifier_post_route_receives_the_verified_manifest() -> None:
    source = inspect.getsource(verifier.verify_repository_v4)
    assert "verify_public_results_v4(root, manifest)" in source
