"""Run the explicitly authorized fresh no-fault qualification once; never formal."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import uuid

from scripts.product.qualification_v040_v3.driver import V3Driver
from scripts.product.qualification_v040_v3.freeze import (
    verify_freeze,
    consume_once,
    GOAL_PATH,
    GOAL_SHA,
)
from scripts.product.qualification_v040.guard import (
    QualificationBlocked,
    ZERO_COUNTS,
    require,
    sha,
)
from scripts.product.qualification_v040.runtime import QualificationRuntime
from scripts.product.v040_runtime import seal_private

AUTHORIZATION_SHA256 = (
    "bb5373899fc8523007b6e9cec0b865403450e4bf4f15b81426ae20f6e1f2deeb"
)
BASE = "7d1c975e3450901a6b09fcbca8c0cd82f24cfc14"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-no-fault", action="store_true", required=True)
    parser.add_argument("--goal", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    authorization = args.goal.read_bytes()
    require(
        hashlib.sha256(authorization).hexdigest() == AUTHORIZATION_SHA256,
        "AUTHORIZATION_MISMATCH",
    )
    repository = Path(__file__).resolve().parents[3]
    require(
        args.goal.resolve() == (repository / GOAL_PATH).resolve()
        and AUTHORIZATION_SHA256 == GOAL_SHA,
        "GOAL_PATH_DRIFT",
    )
    policy = verify_freeze(repository)
    qualification = uuid.uuid4().hex
    consumption = consume_once(repository, AUTHORIZATION_SHA256, qualification)
    private = (
        repository
        / ".local/product-v040-qualifications"
        / ("qualification-" + qualification)
    )
    runtime = QualificationRuntime(repository, private, qualification)
    driver = V3Driver(runtime, policy)
    driver.result["no_fault_qualification_consumption"] = consumption
    driver.result["goal_id"] = "ecomsre-product-v040-runtime-qualification-successor-v3"
    driver.result["predecessor_pr98"] = {
        "head": BASE,
        "status": "BLOCKED_PRE_EXECUTION",
        "blocker": "WRITER_CONTAINER_DRIFT",
        "preserved": True,
    }
    from ecomsre.product.remediation.window_requests import create_private_file

    create_private_file(private / "host/authorization.txt", authorization)
    print(
        json.dumps(
            {
                "qualification_id": qualification,
                "private_root": str(private),
                "formal_authority": False,
            }
        ),
        flush=True,
    )
    with runtime.operation_lock():
        try:
            require(
                not runtime.command(("git", "status", "--porcelain")).strip(),
                "SOURCE_TREE_DIRTY",
            )
            require(
                runtime.command(("git", "merge-base", BASE, "HEAD")).strip() == BASE,
                "SOURCE_BASE_DRIFT",
            )
            historical = (
                repository.parent
                / "product-v040-live-payment/.local/product-v040/preparation-004-failed"
            )
            driver.setup(historical)
            driver.run()
        except Exception as error:
            code = (
                error.code
                if isinstance(error, QualificationBlocked)
                else "QUALIFICATION_PROTOCOL_ERROR"
            )
            driver.result["status"] = "BLOCKED_PRE_EXECUTION"
            driver.result["blocker"] = {
                "code": code,
                "error_type": type(error).__name__,
            }
            if driver.journal is not None:
                try:
                    next_stage = driver.journal.plan["stages"][
                        driver.journal.next_stage
                    ]["name"]
                    driver.journal.block(code, next_stage, type(error).__name__)
                except QualificationBlocked:
                    pass
            else:
                seal_private(
                    private / "host/preflight-first-divergence.json",
                    {
                        "code": code,
                        "stage": "PREFLIGHT",
                        "error_type": type(error).__name__,
                        "utc": datetime.now(UTC).isoformat(),
                        "formal_authority": False,
                        "prohibited_action_budget": ZERO_COUNTS,
                    },
                )
            # Private exception context is retained for diagnosis; never exported.
            import traceback

            create_private_file(
                private / "errors/terminal.txt", traceback.format_exc().encode()
            )
        try:
            driver.result["cleanup"] = driver.cleanup()
        except Exception as error:
            driver.latch_cleanup_failure(error)
            driver.result["cleanup"] = {
                "status": "MANUAL_INTERVENTION_REQUIRED",
                "error_type": type(error).__name__,
                "code": getattr(error, "code", "CLEANUP_PROTOCOL_ERROR"),
            }
            driver.result["status"] = "BLOCKED_PRE_EXECUTION"
            import traceback

            create_private_file(
                private / "errors/cleanup.txt", traceback.format_exc().encode()
            )
        if driver.result["counter_evidence"]["status"] == "NOT_YET_OBSERVED":
            try:
                driver.zero_database_counts()
            except Exception as error:
                driver.latch_cleanup_failure(error)
                driver.result["status"] = "BLOCKED_PRE_EXECUTION"
        driver.result["source"] = json.loads(
            (private / "host/qualification-identity.json").read_bytes()
        )
        driver.result["ended_at"] = datetime.now(UTC).isoformat()
        driver.result["first_divergence"] = (
            "LATCHED"
            if (private / "host/preflight-first-divergence.json").exists()
            or (private / "host/stage-journal/first-divergence.json").exists()
            else "NONE"
        )
        driver.result["stage_journal_sha256"] = sha(
            {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((private / "host/stage-journal").glob("*.json"))
            }
        )
        driver.result["completed_stage_count"] = (
            driver.journal.next_stage if driver.journal else 0
        )
        driver.result["expected_stage_count"] = (
            len(driver.journal.plan["stages"]) if driver.journal else None
        )
        driver.result["future_formal_campaign_recommendation"] = (
            "WITHHOLD_PENDING_INDEPENDENT_REVIEW"
        )
        seal_private(private / "qualification-result.json", driver.result)
        index = {
            p.relative_to(private).as_posix(): hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
            for p in sorted(private.rglob("*"))
            if p.is_file() and not p.is_symlink()
        }
        seal_private(
            private / "evidence-index.json",
            {"files": index, "files_sha256": sha(index), "formal_authority": False},
        )
        print(
            json.dumps(
                {
                    "status": driver.result["status"],
                    "cleanup": driver.result["cleanup"],
                    "first_divergence": driver.result["first_divergence"],
                    "counters": driver.result["counters"],
                    "counter_evidence": driver.result["counter_evidence"],
                }
            ),
            flush=True,
        )
    raise SystemExit(0 if driver.result["status"] == "PASS_NO_FAULT_ONLY" else 2)


if __name__ == "__main__":
    main()
