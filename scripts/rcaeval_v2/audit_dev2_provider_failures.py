from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from ecomsre_rcaeval_v2.dev3_audit import (
    audit_dev2_failure_artifacts,
    write_audit_lock_create_once,
)
from ecomsre_rcaeval_v2.dev3_evaluation_root import (
    EVALUATION_LOCK_NAME,
    verify_evaluation_root,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Freeze the identity-free, read-only dev.2 Provider failure audit."
    )
    parser.add_argument("--dev2-smoke-schedule", type=Path, required=True)
    parser.add_argument("--dev2-smoke-journal-root", type=Path, required=True)
    parser.add_argument("--dev2-smoke-gate", type=Path, required=True)
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--private-schedule-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--smoke-journal-root", type=Path, required=True)
    parser.add_argument("--design-journal-root", type=Path, required=True)
    args = parser.parse_args()
    verify_evaluation_root(
        args.control_root,
        args.private_schedule_root,
        args.output_root,
        args.smoke_journal_root,
        args.design_journal_root,
        project_root=PROJECT_ROOT,
    )
    evaluation_lock = args.control_root / "locks" / EVALUATION_LOCK_NAME
    audit = audit_dev2_failure_artifacts(
        smoke_schedule_path=args.dev2_smoke_schedule,
        smoke_journal_root=args.dev2_smoke_journal_root,
        smoke_gate_path=args.dev2_smoke_gate,
        evaluation_root_lock_sha256=hashlib.sha256(
            evaluation_lock.read_bytes()
        ).hexdigest(),
    )
    write_audit_lock_create_once(
        args.control_root / "locks/dev2-provider-failure-audit.json", audit
    )
    print("DEV2_PROVIDER_FAILURE_AUDIT_LOCKED")
    print(f"failures={audit.audit.failure_count}")
    print(f"retry_eligible={audit.audit.retry_eligible_count}")
    print(f"retry_ineligible={audit.audit.retry_ineligible_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
