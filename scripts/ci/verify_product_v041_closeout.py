"""Verify the bounded live safety package and its public presentation bindings."""

from __future__ import annotations
from datetime import datetime
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET
from pydantic import BaseModel
from ecomsre.product.remediation.approval import OperatorApprovalV1
from ecomsre.product.remediation.authorization import AttemptAuthorizationV1
from ecomsre.product.remediation.attempt_contracts import (
    RemediationAttemptV1,
    RemediationDecisionEventV1,
    WriteIntentV1,
)
from ecomsre.product.remediation.contracts import RemediationCandidateV1
from ecomsre.product.remediation.execution_contracts import (
    ExecutorDispatchV1,
    StepReceiptV1,
    RecoveryWindowV1,
    RecoveryEvaluationV1,
    RecoveryPolicyV1,
)

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "docs/results/product-v041-live-safety"
MODELS: dict[str, type[BaseModel]] = {
    "candidate": RemediationCandidateV1,
    "approval": OperatorApprovalV1,
    "attempt": RemediationAttemptV1,
    "authorization": AttemptAuthorizationV1,
    "write_intent": WriteIntentV1,
    "executor_dispatch": ExecutorDispatchV1,
    "receipt": StepReceiptV1,
    "recovery_window": RecoveryWindowV1,
    "recovery_evaluation": RecoveryEvaluationV1,
}


def require(value: Any, reason: str) -> None:
    if not value:
        raise ValueError(reason)


def verify_case(case: dict[str, Any]) -> None:
    ident = case["case_id"]
    counts = case["counts"]
    objects = case["objects"]
    require(ident in ("S0", "S1", "S2", "S3", "S4"), "CASE_ID")
    require(
        case["provider_calls"] == 0 and case["evidence_status"] == "COLLECTED",
        "EVIDENCE_STATUS",
    )
    require(
        case["baseline_file_restored"]
        and case["cleanup"]["clean"]
        and case["cleanup"]["non_owned_unchanged"],
        "CLEANUP",
    )
    require(
        case["cleanup"]["remaining"] == {"container": 0, "network": 0, "volume": 0},
        "REMAINING",
    )
    require(
        not case.get("collection_errors") and not case.get("failure_type"),
        "HIDDEN_FAILURE",
    )
    for kind, model in MODELS.items():
        require(counts[kind] == len(objects[kind]), "OBJECT_CARDINALITY:" + kind)
        for obj in objects[kind]:
            model.model_validate(obj)
    writes = 1 if ident in ("S3", "S4") else 0
    for field in (
        "authorization",
        "write_intent",
        "executor_dispatch",
        "receipt",
        "gateway_consumption",
        "external_product_writes",
    ):
        require(counts[field] == writes, "WRITE_COUNT:" + field)
    require(case["gateway_evidence"]["consumptions"] == writes, "GATEWAY_COUNT")
    applied = [
        r
        for r in case["external_write_audit"]
        if r["stage"] == "APPLIED" and r["controller"] is None
    ]
    require(
        len(applied) == writes and all(r["document"] == "baseline" for r in applied),
        "EXTERNAL_EFFECT",
    )
    if ident == "S0":
        require(
            case["terminal"] == "NO_CANDIDATE"
            and counts["candidate"] == counts["approval"] == counts["attempt"] == 0,
            "S0_AUTHORITY",
        )
        require(
            case["gateway_evidence"]["kind"] == "NOT_STARTED_S0"
            and not case["gateway_evidence"]["birth_running"],
            "S0_GATEWAY",
        )
    else:
        require(
            counts["candidate"] == counts["approval"] == counts["attempt"] == 1,
            "SEMANTIC_OBJECTS",
        )
        diagnosis = case["fault_diagnosis"]
        require(
            diagnosis["provider_calls"]
            == diagnosis["agent_writes"]
            == diagnosis["runbook_executions"]
            == 0,
            "DIAGNOSIS_WRITE_OR_PROVIDER",
        )
        require(
            objects["candidate"][0]["target_logical_service"] == "payment"
            and objects["candidate"][0]["runbook_id"] == "ROLLBACK_CONFIGURATION",
            "TARGET_OR_RUNBOOK",
        )
        require(
            diagnosis["terminal"] == "CORE_KNOWN"
            and diagnosis["mechanism"] == "CONFIGURATION_ERROR"
            and diagnosis["broad_domain"] == "CONFIGURATION",
            "FAULT_DIAGNOSIS",
        )
        require(
            case["fault"]["confirmed_requests"] == 30
            and case["fault"]["expected_payment_failures"] == 30,
            "FAULT_PROOF",
        )
        attempt = objects["attempt"][0]
        if ident in ("S1", "S2"):
            reason = (
                "APPROVAL_REVOKED"
                if ident == "S1"
                else "CONFIGURATION_DRIFT_NOT_VISIBLE"
            )
            require(
                attempt["safe_error_code"] == reason
                and attempt["final_disposition"] == "NO_WRITE",
                "DENIAL",
            )
            require(case["decision_trace"], "DENIAL_TRACE")
            previous = "0" * 64
            denied = False
            for ordinal, raw_event in enumerate(case["decision_trace"], 1):
                event = RemediationDecisionEventV1.model_validate(raw_event)
                require(
                    event.attempt_id == attempt["attempt_id"]
                    and event.previous_event_sha256 == previous
                    and event.ordinal == ordinal,
                    "TRACE_BINDING",
                )
                previous = event.event_sha256
                if event.outcome == "DENY" and event.reason_code == reason:
                    denied = True
            require(denied, "DENIAL_REASON_TRACE")
            from ecomsre.product.remediation.state import StateObservationV1

            evidence = case["decision_evidence"]
            refs = {
                ref
                for event in case["decision_trace"]
                for ref in event["evidence_refs"]
            }
            require(refs == set(evidence) and bool(refs), "DENIAL_STATE_EVIDENCE")
            for ref, payload in evidence.items():
                require(
                    hashlib.sha256(
                        json.dumps(
                            payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest()
                    == ref,
                    "DENIAL_CAS_HASH",
                )
                state = StateObservationV1.model_validate(payload)
                require(
                    state.environment_id == attempt["environment_id"]
                    and state.target_logical_service == "payment"
                    and state.environment_owned,
                    "DENIAL_STATE_BINDING",
                )
                if ident == "S2":
                    require(
                        not state.fault_still_present
                        and state.current_configuration_digest
                        == state.baseline_configuration_digest,
                        "DENIAL_STATE_NOT_BASELINE",
                    )
        if ident == "S1":
            require(case["approval_status"]["status"] == "REVOKED", "REVOCATION")
        if ident == "S2":
            require(case["drift_state"]["fault_still_present"] is False, "DRIFT_FAULT")
            require(
                case["drift_state"]["current_configuration_digest"]
                == case["drift_state"]["baseline_configuration_digest"],
                "DRIFT_BASELINE",
            )
        if writes:
            require(objects["receipt"][0]["outcome"] == "APPLIED", "RECEIPT")
            require(
                counts["recovery_window"] == 2 and counts["recovery_evaluation"] == 1,
                "RECOVERY_CARDINALITY",
            )
            require(
                case["executor_replay"]["actual_run_one_calls"] == 1,
                "REPLAY_NOT_CALLED",
            )
            require(
                case["executor_replay"]["terminal_after"] == case["terminal"],
                "REPLAY_CHANGED_TERMINAL",
            )
            require(
                case["executor_replay"]["duplicate_wakeup_result"]
                == "REMEDIATION_RECONCILIATION_REQUIRED",
                "REPLAY_REFUSAL",
            )
            expected = "RECOVERED" if ident == "S3" else "VERIFICATION_FAILED"
            require(
                case["terminal"] == expected
                and objects["recovery_evaluation"][0]["terminal"] == expected,
                "RECOVERY_TERMINAL",
            )
            refs = {
                r
                for kind in ("receipt", "recovery_window")
                for obj in objects[kind]
                for r in obj["supporting_evidence_refs"]
            }
            require(refs == set(case["recovery_evidence"]), "RECOVERY_REFS")
            # CAS uses canonical JSON bytes from the Product object store.

            for ref, obj in case["recovery_evidence"].items():
                require(
                    hashlib.sha256(
                        json.dumps(
                            obj,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest()
                    == ref,
                    "RECOVERY_CAS",
                )
            from ecomsre.product.remediation.verifier import evaluate

            policy = RecoveryPolicyV1.model_validate(case["recovery_policy"])
            recomputed = evaluate(
                attempt_id=attempt["attempt_id"],
                receipt=StepReceiptV1.model_validate(objects["receipt"][0]),
                windows=[
                    RecoveryWindowV1.model_validate(w)
                    for w in objects["recovery_window"]
                ],
                policy=policy,
                resolve=lambda ref: json.dumps(
                    case["recovery_evidence"][ref],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode(),
                now=datetime.fromisoformat(
                    objects["recovery_evaluation"][0]["created_at"].replace(
                        "Z", "+00:00"
                    )
                ),
            )
            require(
                recomputed.terminal == expected
                and list(recomputed.reason_codes)
                == objects["recovery_evaluation"][0]["reason_codes"],
                "RECOVERY_RECOMPUTATION",
            )
            if ident == "S4":
                require(
                    attempt["final_disposition"] == "ESCALATE_HUMAN", "S4_DISPOSITION"
                )
                require(
                    "BUSINESS_SLI_FAILED"
                    in objects["recovery_evaluation"][0]["reason_codes"],
                    "S4_REASON",
                )
            else:
                for suffix in ("/remediation-candidates", "/approvals", "/attempts"):
                    calls = [
                        c
                        for c in case["api_calls"]
                        if c["method"] == "POST" and c["route"].endswith(suffix)
                    ]
                    groups: dict[str, list[dict[str, Any]]] = {}
                    for call in calls:
                        groups.setdefault(call["key_sha256"], []).append(call)
                    duplicate = [v for v in groups.values() if len(v) >= 2]
                    require(duplicate, "API_REPLAY_MISSING:" + suffix)
                    require(
                        any(
                            len(
                                {
                                    (r["request_sha256"], r["response_sha256"])
                                    for r in group
                                }
                            )
                            == 1
                            for group in duplicate
                        ),
                        "API_REPLAY_CHANGED",
                    )


class Document(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        if data.get("id"):
            self.ids.append(str(data["id"]))
        if data.get("href"):
            self.links.append(str(data["href"]))


def verify_documents() -> None:
    readme = (ROOT / "README.md").read_text()
    require(
        "v0.4.1" in readme and "Diagnosis" in readme and "收尾完成" in readme,
        "README_VERSION",
    )
    require(
        "收尾完成" in (ROOT / "docs/product/STATUS.md").read_text(), "STATUS_INCOMPLETE"
    )
    doc = Document()
    doc.feed((ROOT / "docs/interview/ecomsre-agent-v041-handbook.html").read_text())
    require(len(doc.ids) == len(set(doc.ids)), "DUPLICATE_HTML_ID")
    for link in doc.links:
        if link.startswith("#"):
            require(link[1:] in doc.ids, "HTML_ANCHOR")
    require(
        (ROOT / "docs/interview/ecomsre-agent-v03-handbook.html").is_file(),
        "HISTORY_REMOVED",
    )
    ET.parse(ROOT / "docs/assets/ecomsre-v041-architecture.svg")
    require(
        "flowchart" in (ROOT / "docs/assets/ecomsre-v041-architecture.mmd").read_text(),
        "MERMAID_MISSING",
    )
    for path in [
        ROOT / "README.md",
        *(ROOT / "docs/product").glob("*.md"),
        ROOT / "docs/interview/PROJECT_PITCH.md",
    ]:
        for target in re.findall(r"\]\(([^\s)]+)\)", path.read_text()):
            if "://" in target or target.startswith("#"):
                continue
            require(
                (path.parent / target.split("#")[0]).exists(),
                "BROKEN_LINK:" + str(path.relative_to(ROOT)) + ":" + target,
            )
    claims = json.loads(
        (ROOT / "docs/analysis/product-v041-claim-map.json").read_text()
    )
    for claim in claims["claims"]:
        require(
            hashlib.sha256((ROOT / claim["source_file"]).read_bytes()).hexdigest()
            == claim["evidence_sha256"],
            "CLAIM_SOURCE_HASH",
        )


def main() -> None:
    cases = [json.loads(p.read_text()) for p in sorted(RESULT.glob("case-s*.json"))]
    require(
        {c["case_id"] for c in cases} == {"S0", "S1", "S2", "S3", "S4"}
        and len(cases) == 5,
        "MATRIX_INCOMPLETE",
    )
    for case in cases:
        verify_case(case)
    matrix = json.loads((RESULT / "live-safety-matrix.json").read_text())
    require(matrix["status"] == "PASS", "MATRIX_STATUS")
    for case in cases:
        row = next(r for r in matrix["cases"] if r["case_id"] == case["case_id"])
        require(
            row["counts"] == case["counts"] and row["terminal"] == case["terminal"],
            "MATRIX_CASE_DRIFT",
        )
    timing = json.loads((RESULT / "timing-summary.json").read_text())
    from scripts.product.live_safety_v041.summarize import duration

    for case in cases:
        item = timing["cases"][case["case_id"]]
        require(
            item["events"] == case["timeline"] and item["sample_count"] == 1,
            "TIMING_CASE_DRIFT",
        )
        for metric in item["metrics"].values():
            require(
                metric
                == duration(item["events"], metric["start_event"], metric["end_event"]),
                "TIMING_RECOMPUTATION",
            )
    manifest = json.loads((RESULT / "evidence-manifest.json").read_text())
    for name, expected in manifest["files"].items():
        require(
            hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected,
            "MANIFEST_HASH",
        )
    verify_documents()
    print(
        json.dumps(
            {
                "terminal": "ECOMSRE_PRODUCT_V041_CLOSEOUT_EVIDENCE_PASS",
                "cases": 5,
                "unauthorized_writes": 0,
                "duplicate_writes": 0,
                "provider_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
