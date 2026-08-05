from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
from pydantic import ValidationError

from scripts.phase5b_hidden_pack.ground_truth_contract import HiddenGroundTruthV1
from ecomsre.phase5b.hidden_pack import (
    build_hidden_pack_manifest,
    write_canonical_json,
)
from scripts.phase5b_hidden_pack.seal_record import (
    HiddenPackSealRecord,
    load_hidden_pack_seal_record,
    verify_external_hidden_pack,
    verify_public_seal_records,
)
from scripts.phase5b_hidden_pack.seal_cli import main as seal_cli_main


TEMPLATES = tuple(f"hidden-{index:02d}" for index in range(1, 7))
SEEDS = tuple(f"seed-{index:02d}" for index in range(5))


def _confirmed_truth_payload() -> dict[str, object]:
    return {
        "schema_version": "phase5b.hidden-ground-truth.v1",
        "evaluation_version": "phase5b.v1",
        "template_id": "hidden-01",
        "seed_id": "seed-00",
        "decision": "RCA_CONFIRMED",
        "incident_confirmed": True,
        "root_service": "synthetic-service",
        "fault_mechanism": "request_processing_failure",
        "causal_chain": [
            "synthetic input becomes inconsistent",
            "synthetic SLI degrades",
        ],
        "affected_sli": "synthetic-sli",
        "required_support_sources": ["METRICS", "LOGS"],
        "required_contradiction_handling": [],
        "required_missing_evidence": [],
        "write_disposition": "NO_ACTION",
        "difficult_subsets": ["synthetic_contract_case"],
    }


def _seal_record_payload() -> dict[str, object]:
    return {
        "schema_version": "phase5b.hidden-pack-seal.v1",
        "evaluation_version": "phase5b.v1",
        "protocol_commit": "1" * 40,
        "freeze_manifest_sha256": "2" * 64,
        "pack_id": "synthetic-pack-for-contract-tests",
        "generator_version": "phase5b-synthetic-builder-v1",
        "builder_source_sha256": "3" * 64,
        "validator_source_sha256": "4" * 64,
        "private_validation_report_sha256": "5" * 64,
        "hidden_pack_manifest_sha256": "6" * 64,
        "agent_visible_pack_sha256": "7" * 64,
        "ground_truth_pack_sha256": "8" * 64,
        "template_count": 6,
        "seed_count_per_template": 5,
        "instance_count": 30,
        "sealed": True,
        "unblinded": False,
        "agent_runs": 0,
        "provider_calls": 0,
        "execution_entered": False,
    }


def _build_synthetic_pack(root: Path) -> tuple[Path, object]:
    for template_id in TEMPLATES:
        for seed_id in SEEDS:
            case_root = root / "agent-visible" / template_id / seed_id
            payloads = {
                "incident.json": {
                    "affected_sli": "synthetic protocol SLI",
                    "alert_source_service": None,
                    "ended_at": "2026-08-04T00:05:00Z",
                    "incident_id": f"synthetic-{seed_id}",
                    "schema_version": "phase1.incident.v1",
                    "severity": "SEV3",
                    "started_at": "2026-08-04T00:00:00Z",
                    "summary": "Synthetic non-evaluation protocol fixture.",
                },
                **{
                    f"{source}.json": {
                        "observations": [],
                        "schema_version": "phase1.replay-observations.v1",
                        "status": "AVAILABLE",
                    }
                    for source in ("metrics", "logs", "traces", "changes")
                },
            }
            for filename, payload in payloads.items():
                write_canonical_json(case_root / filename, payload)
            write_canonical_json(
                case_root / "manifest.json",
                {
                    "case_id": seed_id,
                    "files": {
                        filename: hashlib.sha256(
                            (case_root / filename).read_bytes()
                        ).hexdigest()
                        for filename in sorted(payloads)
                    },
                    "schema_version": "phase1.replay-manifest.v1",
                },
            )
            write_canonical_json(
                root / "ground-truth" / template_id / f"{seed_id}.json",
                {
                    "affected_sli": "synthetic-sli",
                    "causal_chain": [],
                    "decision": "ABSTAIN",
                    "difficult_subsets": ["synthetic_contract_case"],
                    "evaluation_version": "phase5b.v1",
                    "fault_mechanism": None,
                    "incident_confirmed": False,
                    "required_contradiction_handling": [
                        "Treat the synthetic signal as outside the current incident window."
                    ],
                    "required_missing_evidence": [],
                    "required_support_sources": [],
                    "root_service": None,
                    "schema_version": "phase5b.hidden-ground-truth.v1",
                    "seed_id": seed_id,
                    "template_id": template_id,
                    "write_disposition": "NO_ACTION",
                },
            )
    manifest = build_hidden_pack_manifest(
        root,
        pack_id="synthetic-pack-for-contract-tests",
        generator_version="phase5b-synthetic-builder-v1",
    )
    manifest_path = root / "manifest.json"
    write_canonical_json(manifest_path, manifest.model_dump(mode="json"))
    return manifest_path, manifest


def _seal_for_synthetic_pack(manifest_path: Path, manifest: object) -> HiddenPackSealRecord:
    payload = _seal_record_payload()
    payload.update(
        {
            "pack_id": manifest.pack_id,  # type: ignore[attr-defined]
            "generator_version": manifest.generator_version,  # type: ignore[attr-defined]
            "hidden_pack_manifest_sha256": hashlib.sha256(
                manifest_path.read_bytes()
            ).hexdigest(),
            "agent_visible_pack_sha256": manifest.agent_visible_pack_sha256,  # type: ignore[attr-defined]
            "ground_truth_pack_sha256": manifest.ground_truth_pack_sha256,  # type: ignore[attr-defined]
        }
    )
    return HiddenPackSealRecord.model_validate(payload)


def test_hidden_ground_truth_accepts_strict_confirmed_semantics() -> None:
    truth = HiddenGroundTruthV1.model_validate(_confirmed_truth_payload())

    assert truth.decision.value == "RCA_CONFIRMED"
    assert truth.root_service == "synthetic-service"
    assert tuple(source.value for source in truth.required_support_sources) == (
        "METRICS",
        "LOGS",
    )
    with pytest.raises(ValidationError, match="frozen"):
        truth.root_service = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("root_service", None, "root and mechanism"),
        ("fault_mechanism", None, "root and mechanism"),
        ("causal_chain", [], "causal chain"),
        ("affected_sli", None, "affected SLI"),
        ("required_support_sources", ["METRICS"], "two support sources"),
        ("required_missing_evidence", ["synthetic gap"], "evidence gaps"),
        ("incident_confirmed", False, "confirmed incident"),
    ),
)
def test_hidden_ground_truth_rejects_incomplete_confirmed_truth(
    field: str,
    value: object,
    message: str,
) -> None:
    payload = _confirmed_truth_payload()
    payload[field] = value

    with pytest.raises(ValidationError, match=message):
        HiddenGroundTruthV1.model_validate(payload)


def test_hidden_ground_truth_requires_concrete_need_more_gap() -> None:
    payload = _confirmed_truth_payload()
    payload.update(
        {
            "decision": "NEED_MORE_EVIDENCE",
            "root_service": None,
            "fault_mechanism": None,
            "causal_chain": [],
            "required_support_sources": ["METRICS"],
            "required_contradiction_handling": [
                "Resolve the synthetic disagreement with fresh read-only evidence."
            ],
            "required_missing_evidence": [
                "Fresh synthetic evidence that distinguishes the candidates."
            ],
        }
    )

    truth = HiddenGroundTruthV1.model_validate(payload)
    assert truth.decision.value == "NEED_MORE_EVIDENCE"

    payload["required_missing_evidence"] = []
    with pytest.raises(ValidationError, match="concrete missing evidence"):
        HiddenGroundTruthV1.model_validate(payload)

    payload["required_missing_evidence"] = ["Fresh synthetic evidence."]
    payload["root_service"] = "premature-root"
    with pytest.raises(ValidationError, match="cannot claim"):
        HiddenGroundTruthV1.model_validate(payload)


def test_hidden_ground_truth_requires_abstain_without_incident_claim() -> None:
    payload = _confirmed_truth_payload()
    payload.update(
        {
            "decision": "ABSTAIN",
            "incident_confirmed": False,
            "root_service": None,
            "fault_mechanism": None,
            "causal_chain": [],
            "required_support_sources": [],
            "required_contradiction_handling": [
                "Treat the synthetic signal as outside the current incident window."
            ],
        }
    )

    truth = HiddenGroundTruthV1.model_validate(payload)
    assert truth.decision.value == "ABSTAIN"

    payload["incident_confirmed"] = True
    with pytest.raises(ValidationError, match="no confirmed incident"):
        HiddenGroundTruthV1.model_validate(payload)


def test_hidden_ground_truth_rejects_executable_text_and_extra_fields() -> None:
    payload = _confirmed_truth_payload()
    payload["causal_chain"] = ["docker compose restart synthetic-service"]
    with pytest.raises(ValidationError, match="shell syntax"):
        HiddenGroundTruthV1.model_validate(payload)

    payload = _confirmed_truth_payload()
    payload["actual_answer"] = "synthetic-only"
    with pytest.raises(ValidationError, match="Extra inputs"):
        HiddenGroundTruthV1.model_validate(payload)


def test_hidden_pack_seal_record_has_exact_safe_aggregate_fields() -> None:
    payload = _seal_record_payload()
    record = HiddenPackSealRecord.model_validate(payload)

    assert set(HiddenPackSealRecord.model_fields) == set(payload)
    assert record.template_count == 6
    assert record.instance_count == 30
    assert record.sealed is True
    assert record.unblinded is False
    assert record.agent_runs == 0
    assert record.provider_calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("protocol_commit", "1" * 39),
        ("freeze_manifest_sha256", "not-a-hash"),
        ("builder_source_sha256", "3" * 63),
        ("validator_source_sha256", "z" * 64),
        ("private_validation_report_sha256", "5" * 65),
        ("hidden_pack_manifest_sha256", "6" * 63),
        ("agent_visible_pack_sha256", "7" * 63),
        ("ground_truth_pack_sha256", "8" * 63),
        ("template_count", 5),
        ("seed_count_per_template", 4),
        ("instance_count", 29),
        ("sealed", False),
        ("unblinded", True),
        ("agent_runs", 1),
        ("provider_calls", 1),
        ("execution_entered", True),
    ),
)
def test_hidden_pack_seal_record_rejects_malformed_or_unsafe_state(
    field: str,
    value: object,
) -> None:
    payload = _seal_record_payload()
    payload[field] = value

    with pytest.raises(ValidationError):
        HiddenPackSealRecord.model_validate(payload)


def test_hidden_pack_seal_record_rejects_actual_answer_field() -> None:
    payload = _seal_record_payload()
    payload["actual_answer"] = "synthetic-only"

    with pytest.raises(ValidationError, match="Extra inputs"):
        HiddenPackSealRecord.model_validate(payload)


def test_synthetic_external_pack_verification_binds_safe_aggregate_hashes(
    tmp_path: Path,
) -> None:
    pack_root = tmp_path / "external-pack"
    manifest_path, manifest = _build_synthetic_pack(pack_root)
    seal_record = _seal_for_synthetic_pack(manifest_path, manifest)

    result = verify_external_hidden_pack(
        pack_root,
        seal_record,
        worktree_roots=(),
    )

    report = result.model_dump_json()
    assert result.status == "PHASE5B_HIDDEN_PACK_VERIFIED"
    assert result.template_count == 6
    assert result.seed_count_per_template == 5
    assert result.instance_count == 30
    assert str(pack_root) not in report
    assert "root_service" not in report
    assert "fault_mechanism" not in report


def test_external_pack_verification_rejects_manifest_hash_mismatch(
    tmp_path: Path,
) -> None:
    pack_root = tmp_path / "external-pack"
    manifest_path, manifest = _build_synthetic_pack(pack_root)
    payload = _seal_for_synthetic_pack(manifest_path, manifest).model_dump(mode="json")
    payload["hidden_pack_manifest_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="manifest SHA"):
        verify_external_hidden_pack(
            pack_root,
            HiddenPackSealRecord.model_validate(payload),
            worktree_roots=(),
        )


def test_external_pack_verification_rejects_pack_inside_worktree(
    tmp_path: Path,
) -> None:
    worktree_root = tmp_path / "worktree"
    pack_root = worktree_root / "pack"
    manifest_path, manifest = _build_synthetic_pack(pack_root)
    seal_record = _seal_for_synthetic_pack(manifest_path, manifest)

    with pytest.raises(ValueError, match="outside every Git worktree"):
        verify_external_hidden_pack(
            pack_root,
            seal_record,
            worktree_roots=(worktree_root,),
        )


def test_seal_record_loader_rejects_duplicate_or_unknown_answer_fields(
    tmp_path: Path,
) -> None:
    record_path = tmp_path / "seal.json"
    payload = _seal_record_payload()
    record_path.write_text(
        json.dumps(payload)[:-1] + ',"pack_id":"duplicate"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_hidden_pack_seal_record(record_path)

    payload["actual_answer"] = "synthetic-only"
    record_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="Extra inputs"):
        load_hidden_pack_seal_record(record_path)


def _build_synthetic_public_seal_root(root: Path) -> HiddenPackSealRecord:
    freeze_path = root / "config/phase5b/freeze-manifest.v1.json"
    freeze_path.parent.mkdir(parents=True)
    freeze_path.write_text("{}\n", encoding="utf-8")
    payload = _seal_record_payload()
    payload["freeze_manifest_sha256"] = hashlib.sha256(
        freeze_path.read_bytes()
    ).hexdigest()
    record = HiddenPackSealRecord.model_validate(payload)
    for path in (
        root / "config/phase5b-seal/hidden-pack-seal.v1.json",
        root
        / "docs/review-evidence/phase5b-hidden-pack/current-disposition.json",
    ):
        write_canonical_json(path, record.model_dump(mode="json"))
    write_canonical_json(
        root / "docs/review-evidence/phase5b-protocol/current-disposition.json",
        {
            "evaluation_version": "phase5b.v1",
            "protocol_commit": record.protocol_commit,
            "status": "PHASE5B_PROTOCOL_FREEZE_READY",
        },
    )
    return record


def test_public_seal_verification_binds_both_records_and_frozen_protocol(
    tmp_path: Path,
) -> None:
    record = _build_synthetic_public_seal_root(tmp_path)

    verified = verify_public_seal_records(tmp_path)
    assert verified == record

    disposition_path = (
        tmp_path
        / "docs/review-evidence/phase5b-hidden-pack/current-disposition.json"
    )
    changed = record.model_dump(mode="json")
    changed["pack_id"] = "different-pack"
    write_canonical_json(disposition_path, changed)
    with pytest.raises(ValueError, match="public seal records differ"):
        verify_public_seal_records(tmp_path)


def test_seal_cli_requires_external_pack_path(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        seal_cli_main(["verify-pack"])

    assert error.value.code == 2
    assert "--pack-root" in capsys.readouterr().err


def test_make_hidden_pack_verify_fails_closed_without_external_root() -> None:
    project_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["make", "phase5b-hidden-pack-verify"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "PHASE5B_HIDDEN_PACK_ROOT is required" in (
        result.stdout + result.stderr
    )


def test_hidden_pack_validation_cli_imports_no_execution_modules() -> None:
    project_root = Path(__file__).resolve().parents[2]
    source_path = project_root / "scripts/phase5b_hidden_pack/seal_cli.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)

    forbidden = (
        "ecomsre.model",
        "ecomsre.phase1.agent",
        "ecomsre.phase2.provider",
        "ecomsre.phase4.provider",
        "ecomsre.phase5a.provider",
        "ecomsre.phase5b.worker",
    )
    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imported
        for prefix in forbidden
    )


def test_hidden_pack_control_plane_stays_outside_frozen_discovery_roots() -> None:
    from ecomsre.phase5b.freeze import required_frozen_paths

    project_root = Path(__file__).resolve().parents[2]
    out_of_band_paths = {
        "config/phase5b-seal/hidden-pack-seal.v1.json",
        "scripts/phase5b_hidden_pack/ground_truth_contract.py",
        "scripts/phase5b_hidden_pack/seal_cli.py",
        "scripts/phase5b_hidden_pack/seal_record.py",
    }
    old_frozen_root_paths = {
        "config/phase5b/hidden-pack-seal.v1.json",
        "src/ecomsre/phase5b/ground_truth_contract.py",
        "src/ecomsre/phase5b/seal_cli.py",
        "src/ecomsre/phase5b/seal_record.py",
    }

    assert all((project_root / path).is_file() for path in out_of_band_paths)
    assert all(not (project_root / path).exists() for path in old_frozen_root_paths)
    assert out_of_band_paths.isdisjoint(required_frozen_paths(project_root))


def test_makefile_exposes_only_offline_hidden_pack_targets() -> None:
    project_root = Path(__file__).resolve().parents[2]
    makefile = (project_root / "Makefile").read_text(encoding="utf-8")

    assert "phase5b-hidden-pack-contract-test" in makefile
    assert "phase5b-hidden-pack-verify" in makefile
    assert "phase5b-hidden-pack-seal-verify" in makefile
    assert "verify-pack --pack-root" in makefile
