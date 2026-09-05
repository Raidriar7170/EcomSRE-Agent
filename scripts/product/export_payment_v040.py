"""Publish the fixed safe projection after the one campaign and owned cleanup."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

from pydantic import BaseModel

from ecomsre.product.remediation.approval import OperatorApprovalV1
from ecomsre.product.remediation.authorization import AttemptAuthorizationV1
from ecomsre.product.remediation.contracts import RemediationCandidateV1
from ecomsre.product.remediation.verifier import evaluate
from ecomsre.product.remediation.live_evidence import (
    BLOCKED,
    NEGATIVE,
    PASS,
    complete_negative,
    LiveCleanupV040,
    LiveDiagnosisV040,
    LiveManifestV040,
    LiveResultV040,
)
from scripts.live_sandbox.product_v040 import ProductV040Lifecycle
from scripts.product.v040_control import observer_for
from scripts.product.v040_gates import counts
from scripts.product.v040_observer import resource_fingerprints
from scripts.ci.verify_product_v040_live import verify_code_binding
from scripts.product.v040_runtime import (
    ProductRuntimeV040,
    digest,
    read_json,
    seal_private,
)

LIMITATIONS = (
    "OWNED_LOCAL_PAYMENT_ONLY",
    "HUMAN_AUTHORIZED_ONLY",
    "NO_PROVIDER_OR_MODEL_ACTION_SELECTION",
    "NO_KAFKA_AUTOMATIC_REMEDIATION",
    "NO_PRODUCTION_OR_GENERAL_AUTONOMY_CLAIM",
    "NO_EXACTLY_ONCE_DISTRIBUTED_CLAIM",
)


def public_write(path: Path, value: object) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(
            "public measured result already exists; preserve its bytes"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def code_bindings(runtime: ProductRuntimeV040) -> dict[str, dict[str, str]]:
    inputs = read_json(runtime.private / "host/build-inputs.json")
    entries = runtime.command(
        (
            "git",
            "ls-files",
            "--stage",
            "-z",
            "--",
            "scripts/product/v040_*.py",
            "scripts/product/run_payment_v040.py",
            "scripts/product/export_payment_v040.py",
            "scripts/live_sandbox/product_v040.py",
            "scripts/ci/verify_product_v040_live.py",
            "config/product-v040/live-profile.v1.json",
            "config/product-v040/live-runtime.v1.yml",
            "config/product-v040/remediation-network.v1.yml",
            "docker-compose.product.yml",
        )
    ).split("\0")
    for entry in filter(None, entries):
        metadata, name = entry.split("\t", 1)
        mode, _, stage = metadata.split()
        if stage != "0":
            raise ValueError("unmerged public source binding")
        inputs[name] = {
            "git_mode": mode,
            "sha256": hashlib.sha256(
                (runtime.repository / name).read_bytes()
            ).hexdigest(),
        }
    return inputs


def ensure_quiescent(runtime: ProductRuntimeV040, product_owned: dict[str, list[str]]) -> None:
    writers = {"api", "worker", "remediation-executor", "remediation-control-gateway"}
    for resource in product_owned["container"]:
        item = json.loads(runtime.docker("inspect", resource))[0]
        if item["Config"]["Labels"].get("com.docker.compose.service") in writers and (
            item["State"].get("Running") or item["State"].get("Restarting")
            or item["State"].get("Paused")
        ):
            path = runtime.private / "host/export-blocked-active-writer.json"
            if not path.exists():
                seal_private(path, {"status": "NONFINAL_BLOCKER_OBSERVATION",
                                    "writer_state": "ACTIVE", "counter_state": "UNKNOWN",
                                    "manifest_sha256": read_json(runtime.private / "host/frozen-manifest.json")["manifest_sha256"]})
            raise ValueError("owned Product writer active; stop owned writers before final evidence export")


def cleanup_projection(
    previous: LiveCleanupV040,
    product: dict[str, list[str]],
    sandbox: dict[str, int],
    current_non_owned_changed: bool,
) -> LiveCleanupV040:
    # Never erase a previously observed change or turn historical unknown into
    # a claim that non-owned resources remained invariant throughout the run.
    changed = (True if previous.non_owned_resources_changed is True or current_non_owned_changed
               else None if previous.non_owned_resources_changed is None else False)
    actual_counts = {"owned_" + plural: len(product[kind]) + sandbox[kind]
                    for kind, plural in (("container", "containers"), ("network", "networks"), ("volume", "volumes"))}
    return LiveCleanupV040.model_validate({
        **previous.model_dump(), **actual_counts, "non_owned_resources_changed": changed,
        "verdict": "BLOCKED" if changed is not False or any(actual_counts.values()) else previous.verdict,
    })


def export(
    runtime: ProductRuntimeV040, lifecycle: ProductV040Lifecycle
) -> LiveResultV040:
    manifest = LiveManifestV040.model_validate_json(
        (runtime.private / "host/frozen-manifest.json").read_bytes()
    )
    product_owned = runtime.owned()
    sandbox_owned = lifecycle.environment.verify_owned_resources(require_complete=False)
    ensure_quiescent(runtime, product_owned)
    if (
        runtime.command(("git", "rev-parse", "HEAD")).strip() != manifest.code_head
        or runtime.command(
            ("git", "status", "--porcelain", "--untracked-files=normal")
        ).strip()
    ):
        raise ValueError("measured implementation changed before public projection")
    measured = read_json(runtime.private / "host/measured-execution.json")
    observer = observer_for(runtime, lifecycle)
    repo = observer.recovery.attempts
    candidate = approval = authorization = None
    with repo.store.connect() as connection:
        values: dict[str, BaseModel | None] = {}
        for name, model in (
            ("remediation_candidates", RemediationCandidateV1),
            ("remediation_approvals", OperatorApprovalV1),
            ("remediation_authorizations", AttemptAuthorizationV1),
        ):
            rows = connection.execute(f"SELECT payload_json FROM {name}").fetchall()
            if len(rows) > 1:
                raise ValueError("formal authority cardinality breached")
            values[name] = model.model_validate_json(rows[0][0]) if rows else None
        attempts = connection.execute(
            "SELECT attempt_id FROM remediation_attempts"
        ).fetchall()
    candidate = values["remediation_candidates"]
    approval = values["remediation_approvals"]
    authorization = values["remediation_authorizations"]
    if len(attempts) > 1:
        raise ValueError("formal attempt cardinality breached")
    receipt = observer.recovery.receipt(attempts[0][0]) if attempts else None
    windows = observer.recovery.windows(attempts[0][0]) if attempts else ()
    evaluation = observer.recovery.evaluation(attempts[0][0]) if attempts else None
    if evaluation is not None:
        rebuilt = evaluate(
            attempt_id=attempts[0][0],
            receipt=receipt,
            windows=windows,
            policy=manifest.policy,
            resolve=repo.objects.read_bytes,
            now=evaluation.created_at,
        )
        excluded = {"evaluation_id", "evaluation_sha256"}
        if rebuilt.model_dump(exclude=excluded) != evaluation.model_dump(
            exclude=excluded
        ):
            raise ValueError("stored verifier result does not match typed CAS evidence")
    observed_counts = counts(observer)
    cleanup_path = runtime.private / "host/cleanup.json"
    cleanup = LiveCleanupV040.model_validate_json(cleanup_path.read_bytes()) if cleanup_path.exists() else LiveCleanupV040(
        verdict="BLOCKED", baseline_restored=None, owned_containers=0,
        owned_networks=0, owned_volumes=0, non_owned_resources_changed=None,
    )
    non_owned_changed = resource_fingerprints(runtime, exclude_projects={
        "ecomsre-product-v040", "ecomsre-live-sandbox-v1"
    }) != read_json(runtime.private / "host/non-owned-before.json")
    cleanup = cleanup_projection(cleanup, product_owned, sandbox_owned, non_owned_changed)
    diagnosed = None
    raw = measured.get("diagnosis_record")
    if raw is not None:
        diagnosis = raw["diagnosis"]
        baseline = read_json(runtime.private / "host/healthy-baseline.json")
        payment = next(
            item["service_id"]
            for item in baseline["verification"]["service_identity_map"]["services"]
            if item["logical_service"] == "payment"
        )
        evidence = {item["evidence_ref"]: item for item in raw["evidence"]["objects"]}
        refs = diagnosis["supporting_evidence_refs"]
        diagnosed = LiveDiagnosisV040.model_validate(
            {
                "diagnosis_id": diagnosis["diagnosis_id"],
                "result_sha256": diagnosis["result_sha256"],
                "terminal": diagnosis["terminal"],
                "lane": diagnosis["core_or_extension_or_open_world"],
                "payment_unique_root": diagnosis["root_service_ids"] == [payment],
                "configuration_error": diagnosis["broad_domain"] == "CONFIGURATION"
                and diagnosis["mechanism"] == "CONFIGURATION_ERROR",
                "supporting_refs_resolve": set(refs).issubset(evidence),
                "supporting_source_types": sorted(
                    {evidence[ref]["source"] for ref in refs if ref in evidence}
                ),
                "evidence_aliases": [f"E{index + 1}" for index in range(len(refs))],
            }
        )
    terminal = BLOCKED
    stage = measured["blocked_stage"]
    if (
        evaluation is not None
        and evaluation.outcome == "PASS"
        and cleanup.verdict == "CLEAN"
        and "failure" not in measured
    ):
        terminal, stage = PASS, "NONE"
    elif cleanup.verdict == "CLEAN" and complete_negative(receipt, evaluation, windows):
        terminal = NEGATIVE
    elif stage == "NONE":
        stage = "CLEANUP" if cleanup.verdict != "CLEAN" else "PERSISTENCE"
    # Make all private reads before the final evidence digest. The index excludes
    # its own bytes and the public-source build context, which has its own binding.
    private_files = {}
    for path in sorted(runtime.private.rglob("*")):
        if (
            not path.is_file()
            or path.is_symlink()
            or path.is_relative_to(runtime.private / "host/build-context")
            or path.name == "private-evidence-index.json"
        ):
            continue
        private_files[str(path.relative_to(runtime.private))] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    private_sha = digest(private_files)
    result = LiveResultV040.build(
        terminal=terminal,
        manifest_sha256=manifest.manifest_sha256,
        code_head=manifest.code_head,
        environment_id=manifest.environment_id,
        baseline_id=manifest.baseline_id,
        baseline_sha256=manifest.baseline_sha256,
        diagnosis=diagnosed,
        candidate=candidate,
        approval=approval,
        authorization=authorization,
        current_state_admitted=authorization is not None,
        receipt=receipt,
        recovery_windows=windows,
        evaluation=evaluation,
        counts=observed_counts,
        cleanup=cleanup,
        blocked_stage=stage,
        safe_error_code="NONE"
        if terminal == PASS
        else "BOUNDED_RECOVERY_NOT_SUPPORTED"
        if terminal == NEGATIVE
        else "BOUNDED_CAMPAIGN_BLOCKED",
        preserved_evidence_sha256=private_sha,
        required_successor_change="NONE"
        if terminal == PASS
        else "NEW_VERSIONED_GOAL_AND_FRESH_AUTHORITY",
        limitations=LIMITATIONS,
        created_at=datetime.now(UTC),
    )
    source_bindings = code_bindings(runtime)
    verify_code_binding(runtime.repository, manifest, source_bindings)
    paths = {
        "config/product-v040/live-campaign-manifest.v1.json": manifest.model_dump(
            mode="json"
        ),
        "docs/results/product-v040-payment-live-acceptance.json": result.model_dump(
            mode="json"
        ),
    }
    seal_private(
        runtime.private / "host/private-evidence-index.json",
        {"files": private_files, "evidence_sha256": private_sha},
    )
    for name, value in paths.items():
        public_write(runtime.repository / name, value)
    public_write(
        runtime.repository / "docs/analysis/product-v040-live-evidence-manifest.json",
        {
            "schema_version": "ecomsre.product.v040.public-evidence-manifest.v1",
            "goal_sha256": manifest.goal_sha256,
            "code_head": manifest.code_head,
            "private_evidence_sha256": private_sha,
            "private_file_count": len(private_files),
            "source_bindings": source_bindings,
            "artifacts": {
                name: hashlib.sha256(
                    (runtime.repository / name).read_bytes()
                ).hexdigest()
                for name in paths
            },
        },
    )
    return result


def main() -> None:
    runtime = ProductRuntimeV040(Path(__file__).resolve().parents[2])
    with runtime.operation_lock():
        runtime.load()
        lifecycle = ProductV040Lifecycle(
            repository_root=runtime.repository,
            private_root=runtime.private / "sandbox",
            image_identities=runtime.private / "host/image-proofs-original.json",
        )
        lifecycle.admit()
        print(export(runtime, lifecycle).terminal)


if __name__ == "__main__":
    main()
