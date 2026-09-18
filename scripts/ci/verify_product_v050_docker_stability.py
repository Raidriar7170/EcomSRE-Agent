"""Verify the bounded live evidence and rejected knowledge; never run Docker/LLM."""

import hashlib
import json
import re
from pathlib import Path

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.investigation.runtime import (
    check_predictions,
    supported_hypotheses,
)
from scripts.ci.verify_product_v050 import require
from scripts.ci.verify_product_v050_live_resume import verify as verify_history

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "docs/results/product-v050/docker-stability"


def verify_claims(report, calls, sessions):
    require(
        report["terminal"] == "ECOMSRE_PRODUCT_V050_NO_VALIDATED_LLM_KNOWLEDGE",
        "UNSUPPORTED_TERMINAL",
    )
    stability = report["stability_result"]
    samples = report["stability_sample_projection"]
    require(
        stability["status"] == "PASS"
        and len(samples) == stability["samples"] == 21
        and samples[-1]["elapsed_seconds"] >= 600
        and len({s["data_sha256"] for s in samples}) == 1,
        "STABILITY_EVIDENCE",
    )
    require(
        report["new_baseline_admitted"] and not report["settings_modified_by_agent"],
        "ADMISSION_BOUNDARY",
    )
    require(len(report["attempts"]) == 3, "ATTEMPT_RETENTION")
    for attempt in report["attempts"]:
        cleanup = attempt["cleanup"]
        require(
            cleanup["clean"]
            and cleanup["non_owned_unchanged"]
            and cleanup["network_extra_unchanged"]
            and cleanup["remaining"] == {"container": 0, "network": 0, "volume": 0},
            "CLEANUP_NOT_PROVEN",
        )
        counts = {
            kind: sum(x["kind"] == kind and x["removed"] for x in cleanup["receipts"])
            for kind in cleanup["remaining"]
        }
        require(
            counts == {"container": 22, "network": 1, "volume": 5}, "CLEANUP_RECEIPTS"
        )
    require(
        [a["status"] for a in report["baseline_attempts"]]
        == ["FAILED", "FAILED", "PASS"]
        and report["baseline_successful_windows"] == 5,
        "BASELINE_FAILURE_RETENTION",
    )
    old = json.loads(
        (ROOT / "docs/results/product-v050/provider-unblock/calls.json").read_text()
    )
    all_calls = old + calls
    require(
        len(calls) == report["new_provider_requests"] == 27
        and len(all_calls) == report["provider_request_count"] == 51,
        "CALL_COUNT",
    )
    require(
        len({c["ledger_key"] for c in all_calls}) == len(all_calls), "DUPLICATE_CALL"
    )
    commitment = sum(
        c["accounted_microusd"]
        if c["accounted_microusd"] is not None
        else c["reserved_microusd"]
        for c in all_calls
    )
    require(
        commitment
        == report["accounting"]["committed_upper_microusd"]
        == report["committed_upper_microusd"]
        <= 20_000_000,
        "COST_BOUND",
    )
    require(
        all(c["requested_model"] == "gpt-5.4-mini-2026-03-17" for c in all_calls)
        and report["actual_invoice_usd"] is None,
        "MODEL_OR_INVOICE_CLAIM",
    )
    by_key = {c["ledger_key"]: c for c in calls}
    reads = roundtrips = provisional = 0
    require(
        len(sessions) == report["live_episodes"] == 5
        and len({s["episode_id"] for s in sessions}) == 5,
        "EPISODE_COUNT",
    )
    prior_end = 0
    for s in sessions:
        require(
            s["start"]["monotonic_ns"] > prior_end
            and s["healthy_restored"]
            and s["lag_after"] < 20,
            "INDEPENDENT_EPISODE_BOUNDARY",
        )
        prior_end = s["end"]["monotonic_ns"]
        require(
            s["product_recovery_writes"] == 0
            and s["fault_activation_count"] == s["baseline_restore_count"] == 1,
            "WRITE_BOUNDARY",
        )
        observations = {}
        for o in s["supplemental_observations"]:
            action = (
                "read-"
                + semantic_sha256_v22(
                    {
                        "request": o["request_sha256"],
                        "window": o["window"],
                        "capability": o["capability_sha256"],
                    }
                )[:24]
            )
            require(
                action == o["catalog_action_id"]
                and o["evidence_ref"] == "investigation:" + o["object_sha256"],
                "READ_BINDING",
            )
            observations[action] = o
        decisions = {d["provider_call_index"]: d for d in s["decisions"]}
        inputs = {i["call_index"]: i for i in s["call_inputs"]}
        accepted = 0
        for index, d in decisions.items():
            call = by_key.get(s["session_id"] + ":" + str(index))
            proposal = {
                k: v
                for k, v in d.items()
                if k not in {"validation", "provider_call_index"}
            }
            require(
                call is not None
                and call["state"] == "COMPLETED"
                and call["http_status"] == 200
                and call["proposal_sha256"] == semantic_sha256_v22(proposal),
                "DECISION_CALL_BINDING",
            )
            if d["kind"] == "READ" and d["validation"] == "ACCEPTED":
                require(d["action_id"] in observations, "MISSING_READ")
                accepted += 1
                if index + 1 in decisions:
                    require(
                        observations[d["action_id"]]["evidence_ref"]
                        in inputs[index + 1]["observed_evidence_refs"],
                        "MISSING_FOLLOWUP_EVIDENCE",
                    )
                    roundtrips += 1
        require(accepted == s["read_count"] == len(observations), "READ_COUNT")
        reads += accepted
        accepted_claims = [
            d
            for d in s["decisions"]
            if d["validation"] == "ACCEPTED" and d["hypotheses"]
        ]
        model_hypotheses = accepted_claims[-1]["hypotheses"] if accepted_claims else []
        require(len(model_hypotheses) == len(s["hypotheses"]), "MODEL_HYPOTHESIS_COUNT")
        ids = [h["hypothesis_id"] for h in s["hypotheses"]]
        require(len(set(ids)) == len(ids), "HYPOTHESIS_ID_REUSE")
        for proposed, retained in zip(model_hypotheses, s["hypotheses"]):
            # Runtime assigns random IDs to null-ID model proposals; it does not
            # derive IDs from mechanism text. Compare every model-owned field.
            require(
                bool(re.fullmatch(r"hyp-[0-9a-f]{24}", retained["hypothesis_id"])),
                "HYPOTHESIS_ID_FORMAT",
            )
            require(
                proposed["hypothesis_id"] in (None, retained["hypothesis_id"]),
                "HYPOTHESIS_ID_BINDING",
            )
            require(
                {k: v for k, v in proposed.items() if k != "hypothesis_id"}
                == {k: v for k, v in retained.items() if k != "hypothesis_id"},
                "MODEL_HYPOTHESIS_BINDING",
            )
        checked = check_predictions(
            s["hypotheses"], s["hypothesis_support_observations"]
        )
        require(
            checked == s["checked_predictions"]
            and supported_hypotheses(s["hypotheses"], checked)
            == s["supported_hypothesis_ids"],
            "HYPOTHESIS_BINDING",
        )
        if s["status"] == "PROVISIONAL_SUPPORTED":
            require(bool(s["supported_hypothesis_ids"]), "FALSE_PROVISIONAL_SUPPORT")
            provisional += 1
    require(
        reads == report["accepted_model_selected_reads"] == 17
        and roundtrips == report["complete_read_followups"] == 13
        and provisional == 1,
        "INVESTIGATION_CLAIMS",
    )
    proposals = [c for c in calls if c["ledger_key"].startswith("knowledge:")]
    require(len(proposals) == report["knowledge_proposal_calls"] == 3, "PROPOSAL_CAP")
    valid = [c for c in proposals if c["proposal"] is not None]
    require(
        len(valid) == report["schema_valid_model_proposals"] == 2, "SCHEMA_VALID_COUNT"
    )
    for call in valid:
        proposal = call["proposal"]
        require(
            semantic_sha256_v22(proposal) == call["proposal_sha256"], "PROPOSAL_BINDING"
        )
        eligible = {
            o["evidence_ref"]
            for s in sessions
            if s["incident_id"] in proposal["member_incidents"]
            for o in s["candidate_evidence_catalog"]
            if proposal["target"] in o["covered_services"] and not o["truncated"]
        }
        require(
            bool(
                set(proposal["supporting_refs"] + proposal["counter_evidence_refs"])
                - eligible
            ),
            "REJECTION_NOT_REPRODUCED",
        )
    require(
        report["accepted_candidates"] == 0
        and report["holdout"] == "NOT_ATTEMPTED_NO_ACCEPTED_CANDIDATE"
        and report["promotion"] == report["new_event_reuse"] == "NOT_ATTEMPTED"
        and report["reused_event_llm_calls"] is None
        and report["new_product_recovery_writes"] == 0,
        "UNSUPPORTED_LEARNING_CLAIM",
    )
    return {
        "verification": "PASS",
        "terminal": report["terminal"],
        "episodes": len(sessions),
        "reads": reads,
        "complete_read_followups": roundtrips,
        "provider_requests": len(all_calls),
        "committed_upper_microusd": commitment,
        "accepted_candidates": 0,
        "new_campaign_clean": True,
    }


def verify():
    verify_history()
    offline = json.loads((RESULT / "offline-checks.json").read_text())
    require(
        offline["exit_code"] == 0
        and all(c["status"] == "PASSED" for c in offline["cases"]),
        "OFFLINE_FAILURE",
    )
    for path, digest in offline["source_sha256"].items():
        require(
            hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest,
            "CURRENT_SOURCE_DRIFT:" + path,
        )
    return verify_claims(
        *(
            json.loads((RESULT / name).read_text())
            for name in ["result.json", "calls.json", "live-traces.json"]
        )
    )


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
