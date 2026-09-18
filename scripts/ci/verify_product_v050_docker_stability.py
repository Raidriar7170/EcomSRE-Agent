"""Verify the bounded live evidence and rejected knowledge; never run Docker/LLM."""

import hashlib
import json
import re
import subprocess
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


def verify_repair_claims(report, calls):
    from ecomsre.product.knowledge.drafts_v050 import KnowledgeDraft, compile_draft

    require(
        report["terminal"] == "ECOMSRE_PRODUCT_V050_NO_VALIDATED_LLM_KNOWLEDGE",
        "REPAIR_TERMINAL",
    )
    require(
        len(calls)
        == report["new_provider_requests"]
        == report["semantic_attempts"]
        == 3,
        "REPAIR_ATTEMPTS",
    )
    cost = sum(
        c["accounted_microusd"]
        if c["accounted_microusd"] is not None
        else c["reserved_microusd"]
        for c in calls
    )
    require(
        cost == report["committed_increment_microusd"] <= 1_000_000, "REPAIR_SUB_BUDGET"
    )
    require(
        report["provider_request_count"] == 51 + len(calls)
        and report["committed_upper_microusd"] == 695520 + cost,
        "REPAIR_TOTAL_BUDGET",
    )
    require(
        not report["level_a_validated"] and not report["level_b_validated"],
        "REPAIR_FALSE_LEVEL_VALIDATION",
    )
    require(
        report["telemetry_mode"] == "LIVE_PROVIDER_REPLAY_TELEMETRY",
        "REPAIR_TELEMETRY_MODE",
    )
    valid = 0
    for index, call in enumerate(calls):
        require(
            call["semantic_attempt_index"] == index
            and not call["format_only_repair_claimed"],
            "REPAIR_SEMANTIC_ACCOUNTING",
        )
        require(
            call["requested_model"] == call["actual_model"] == "gpt-5.4-mini-2026-03-17"
            and call["api_style"] == "responses"
            and call["evidence_mode"] == "LIVE_PROVIDER",
            "REPAIR_PROVIDER",
        )
        result = call["result"]
        require(
            not result["admission_passed"]
            and not result["canonical_reconstructed"]
            and not result["independent_validation_eligible"]
            and result["protected_unchanged"],
            "REPAIR_FALSE_ACCEPTANCE",
        )
        if call["draft_projection"] is None:
            require(
                not result["schema_valid"]
                and result["error_code"] == "PROVIDER_TRUNCATED",
                "REPAIR_TRUNCATION",
            )
            continue
        valid += 1
        require(
            semantic_sha256_v22(call["draft_projection"]) == call["projection_sha256"],
            "REPAIR_PROJECTION_HASH",
        )
        require(
            len(call["private_draft_sha256"]) == 64
            and call["replay_scope"]
            == "REDACTED_STRUCTURE_ADMISSION_REJECTION_ONLY_NOT_RAW_DRAFT_OR_CANONICAL_PROOF",
            "REPAIR_PROJECTION_SCOPE",
        )
        view = call["admission_replay_view"]
        for alias, row in view["evidence_catalog"].items():
            binding = {
                k: row[k]
                for k in (
                    "snapshot",
                    "incident_id",
                    "evidence_ref",
                    "window",
                    "services",
                )
            }
            require(
                alias == "e-" + semantic_sha256_v22(binding), "REPAIR_ALIAS_BINDING"
            )
            require(
                row["allowed_target_services"]
                == [
                    service
                    for service in row["services"]
                    if row["status"] == "SUCCESS_NONEMPTY"
                    and not row["truncated"]
                    and service in row["covered_services"]
                ],
                "REPAIR_ROLE_ELIGIBILITY",
            )
        try:
            compile_draft(KnowledgeDraft.model_validate(call["draft_projection"]), view)
        except ValueError as exc:
            require(str(exc) == result["error_code"], "REPAIR_REJECTION_DIFFERS")
        else:
            raise ValueError("REPAIR_REJECTION_NOT_REPRODUCED")
    require(valid == report["schema_valid_drafts"] == 2, "REPAIR_SCHEMA_COUNT")
    require(
        all(
            report[k] == 0
            for k in (
                "accepted_candidates",
                "canonical_reconstructions",
                "development_evaluations",
                "independent_validation_eligible",
                "new_product_recovery_writes",
                "docker_operations",
                "independent_new_episode_count",
            )
        ),
        "REPAIR_UNSUPPORTED_LEARNING",
    )
    require(
        report["holdout"] == "NOT_ATTEMPTED_NO_ADMITTED_CANDIDATE"
        and report["promotion"] == report["new_event_reuse"] == "NOT_ATTEMPTED",
        "REPAIR_C_STAGE",
    )
    require(
        report["seen_episode_count"] == report["live_episode_count"] == 5
        and report["original_discovery_episodes"] == 3
        and report["original_development_episodes"] == 2,
        "REPAIR_DENOMINATOR",
    )
    require(
        report["protected_before"] == report["protected_after"]
        and report["old_terminal_preserved"]
        and report["actual_invoice_usd"] is None,
        "REPAIR_HISTORY",
    )
    return {
        "verification": "PASS",
        "terminal": report["terminal"],
        "provider_requests": report["provider_request_count"],
        "development_evaluations": 0,
        "new_live_episodes": 0,
    }


def verify_repair():
    directory = ROOT / "docs/results/product-v050/knowledge-contract-repair"
    report = json.loads((directory / "result.json").read_text())
    protocol = json.loads((directory / "protocol.json").read_text())
    require(
        report["run_source_commit"] == protocol["run_source_commit"],
        "REPAIR_RUN_IDENTITY",
    )
    for path, digest in protocol["sources"].items():
        relative = (
            path
            if path.startswith(("scripts/", "src/"))
            else "src/ecomsre/product/" + path
        )
        frozen = subprocess.check_output(
            ["git", "show", protocol["run_source_commit"] + ":" + relative], cwd=ROOT
        )
        require(
            hashlib.sha256(frozen).hexdigest() == digest,
            "REPAIR_FROZEN_SOURCE:" + relative,
        )
    offline = json.loads((directory / "offline-checks.json").read_text())
    require(
        offline["exit_code"] == 0
        and all(c["status"] == "PASSED" for c in offline["cases"]),
        "REPAIR_OFFLINE",
    )
    for path, digest in offline["source_sha256"].items():
        require(
            hashlib.sha256(subprocess.check_output(
                ["git", "show", "3ae84ab4bda5054ccfbfde74e9c655a416e312e4:" + path], cwd=ROOT
            )).hexdigest() == digest,
            "REPAIR_HISTORICAL_SOURCE:" + path,
        )
    return verify_repair_claims(
        report, json.loads((directory / "calls.json").read_text())
    )


def verify_feasibility_claims(report, calls, protocol, matrix):
    require(report['terminal'] == 'ECOMSRE_PRODUCT_V050_NO_VALIDATED_LLM_KNOWLEDGE', 'FEASIBILITY_TERMINAL')
    require(len(calls) == report['new_requests'] == 3, 'FEASIBILITY_CALL_COUNT')
    require(protocol['max_semantic_attempts'] == 3 and protocol['request_cap'] == 6
            and protocol['committed_cap_microusd'] == 2000000, 'FEASIBILITY_CAPS')
    require(protocol['reasoning'] == 'medium' and protocol['max_output_tokens'] == 8192, 'FEASIBILITY_OUTPUT_BUDGET')
    require([r['role'] for r in protocol['roster']] == ['DISCOVERY'] * 3 + ['DEVELOPMENT'] * 2, 'FEASIBILITY_ROLES')
    require([(r['incident_id'], r['role']) for r in matrix['rows']] ==
            [(r['incident_id'], r['role']) for r in protocol['roster']], 'FEASIBILITY_ROSTER')
    require(protocol['development_ids'] == [r['incident_id'] for r in protocol['roster'] if r['role'] == 'DEVELOPMENT'], 'FEASIBILITY_DEVELOPMENT_ROLES')
    previous = protocol['budget_before']['committed_upper_microusd']
    for index, call in enumerate(calls):
        result = call['result']
        require(call['call_key'] == result['key'] == f'knowledge-draft-v050.2:proposal:{index}', 'FEASIBILITY_REQUEST_BINDING')
        require(call['actual_model'] == call['requested_model'] == protocol['model']
                and call['reasoning'] == 'medium' and call['max_output_tokens'] == 8192, 'FEASIBILITY_MODEL')
        require(call['state'] == 'COMPLETED' and call['http_status'] == 200
                and call['response_status'] == 'completed' and call['incomplete_details'] is None, 'FEASIBILITY_COMPLETION')
        usage = call['usage']
        cost = (usage['input_tokens'] * 750000 + usage['output_tokens'] * 4500000 + 999999) // 1000000
        require(cost == call['charged_microusd'] == call['accounted_microusd']
                and cost <= call['reserved_microusd'], 'FEASIBILITY_COST')
        previous += cost
        require(result['budget_after']['committed_upper_microusd'] == previous
                and result['budget_after']['provider_request_count'] == 55 + index, 'FEASIBILITY_LEDGER')
        require(result['old_history_unchanged'] and result['schema_valid']
                and not result['development_passed'] and not result['independent_validation_eligible']
                and result['new_live_episodes'] == result['new_product_recovery_writes'] == 0, 'FEASIBILITY_BOUNDARY')
        require(call['model_selection']['target'] == protocol['target']
                and call['model_selection']['member_incidents'] == ['I01', 'I02', 'I03', 'I04', 'I05']
                and call['model_selection']['expression'] is None, 'FEASIBILITY_SELECTION')
    require(sum(c['result']['schema_valid'] for c in calls) == report['schema_valid'] == 3, 'FEASIBILITY_SCHEMA_COUNT')
    require(sum(c['result']['admitted'] for c in calls) == report['admitted'] == 1
            and sum(c['result']['development_evaluated'] for c in calls) == report['development_evaluated'] == 1, 'FEASIBILITY_ADMISSION_COUNT')
    require(calls[0]['result']['error_code'] == 'TWO_SOURCES_REQUIRED'
            and calls[2]['result']['error_code'] == 'DUPLICATE_KNOWLEDGE_CANDIDATE', 'FEASIBILITY_REJECTIONS')
    require(calls[1]['model_selection']['predicates'] == calls[2]['model_selection']['predicates'], 'FEASIBILITY_DUPLICATE')
    predicates = calls[1]['model_selection']['predicates']
    outcomes = calls[1]['result']['development']['outcomes']
    require(len(outcomes) == len(matrix['rows'])
            and len({r['incident_id'] for r in outcomes}) == len(outcomes)
            and calls[1]['result']['development']['incident_ids'] == sorted(r['incident_id'] for r in matrix['rows']), 'FEASIBILITY_OUTCOME_DENOMINATOR')
    require({r['incident_id'] for r in outcomes} == {r['incident_id'] for r in matrix['rows']}, 'FEASIBILITY_EVALUATION_MEMBERS')
    truth = {}
    for row in matrix['rows']:
        target = next(t for t in row['targets'] if t['target'] == protocol['target'])
        states = {p['predicate']: p['status'] for p in target['predicates']}
        selected = [states[p] for p in predicates]
        status = 'FALSE' if 'FALSE' in selected else ('UNKNOWN' if 'UNKNOWN' in selected else 'TRUE')
        outcome = next(o for o in outcomes if o['incident_id'] == row['incident_id'])
        require(outcome['components']['predicates'] == [{'predicate': p, 'status': states[p]} for p in predicates]
                and outcome['outcome']['status'] == status, 'FEASIBILITY_EVALUATOR_REPLAY')
        truth[row['incident_id']] = status == 'TRUE'
    require(sum(truth.values()) == report['seen_true'] == 1 and report['seen_denominator'] == 5, 'FEASIBILITY_SEEN_RATE')
    require(sum(truth[i] for i in protocol['development_ids']) == report['development_true'] == 1
            and report['development_denominator'] == len(protocol['development_ids']) == 2, 'FEASIBILITY_DEVELOPMENT_RATE')
    require(previous == report['committed_upper_microusd'] == 918501
            and report['reported_cost_microusd'] + report['unknown_reservation_microusd'] == previous
            and previous - protocol['budget_before']['committed_upper_microusd'] == report['round_committed_microusd'] == 78920
            and report['cumulative_requests'] == 57, 'FEASIBILITY_TOTAL_BUDGET')
    require(all(report[k] == 0 for k in ['frozen', 'independent_validation', 'promoted', 'new_event_reuse', 'new_live_episodes', 'product_recovery_writes']), 'FEASIBILITY_NO_PROMOTION')
    require(report['old_history_unchanged'] and report['d_precheck']['mutations'] == 0
            and not report['d_precheck']['new_baseline_admitted'], 'FEASIBILITY_HISTORY')
    return {'verification': 'PASS', 'schema_valid': 3, 'admitted': 1, 'development': '1/2', 'independent_validation': 'NOT_ATTEMPTED'}


def verify_feasibility():
    directory = ROOT / 'docs/results/product-v050/knowledge-feasibility'
    report, calls, protocol, matrix = [json.loads((directory / n).read_text()) for n in
                                       ['result.json', 'calls.json', 'protocol.json', 'feasibility.json']]
    for name, digest in report['artifacts_sha256'].items():
        require(hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest, 'FEASIBILITY_ARTIFACT:' + name)
    for path, digest in protocol['sources'].items():
        relative = path if path.startswith(('src/', 'scripts/')) else 'src/ecomsre/product/' + path
        require(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest, 'FEASIBILITY_EXECUTED_SOURCE:' + relative)
    offline = json.loads((directory / 'offline-checks.json').read_text())
    require(offline['exit_code'] == 0 and all(c['status'] == 'PASSED' for c in offline['cases']), 'FEASIBILITY_OFFLINE')
    for path, digest in offline['source_sha256'].items():
        require(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest, 'FEASIBILITY_CURRENT_SOURCE:' + path)
    return verify_feasibility_claims(report, calls, protocol, matrix)


def verify():
    verify_history()
    offline = json.loads((RESULT / "offline-checks.json").read_text())
    require(
        offline["exit_code"] == 0
        and all(c["status"] == "PASSED" for c in offline["cases"]),
        "OFFLINE_FAILURE",
    )
    for path, digest in offline["source_sha256"].items():
        # The earlier check remains bound to its immutable execution source.
        # Current successor source is independently checked below, not substituted
        # into the old experiment or used to rewrite its test results.
        content = subprocess.check_output(
            ["git", "show", "398b414b2561b27a94a9663bb8e0ba24627e984f:" + path],
            cwd=ROOT,
        )
        require(
            hashlib.sha256(content).hexdigest() == digest,
            "HISTORICAL_SOURCE_DRIFT:" + path,
        )
    historical = verify_claims(
        *(
            json.loads((RESULT / name).read_text())
            for name in ["result.json", "calls.json", "live-traces.json"]
        )
    )
    return {"historical": historical, "knowledge_contract_repair": verify_repair(), "knowledge_feasibility": verify_feasibility()}


def verify_private_repair():
    """Optional local CAS/ledger check. Read-only; never loads credentials."""
    import sqlite3
    from ecomsre.product.knowledge.drafts_v050 import (
        TASK,
        KnowledgeDraft,
        compile_draft,
    )

    data = ROOT / ".local/product-v050"
    directory = ROOT / "docs/results/product-v050/knowledge-contract-repair"
    calls = json.loads((directory / "calls.json").read_text())

    def read_object(digest):
        require(bool(re.fullmatch("[0-9a-f]{64}", digest)), "PRIVATE_CAS_ID")
        content = (
            data / "objects/sha256" / digest[:2] / (digest + ".json")
        ).read_bytes()
        require(hashlib.sha256(content).hexdigest() == digest, "PRIVATE_CAS_HASH")
        return json.loads(content)

    with sqlite3.connect(
        (data / "product.sqlite3").as_uri() + "?mode=ro", uri=True
    ) as c:
        c.row_factory = sqlite3.Row
        count = 0
        for call in calls:
            row = c.execute(
                "SELECT * FROM investigation_provider_calls_v050 WHERE call_key=?",
                (call["ledger_key"],),
            ).fetchone()
            require(
                row is not None and row["request_sha256"] == call["request_sha256"],
                "PRIVATE_REQUEST_BINDING",
            )
            payload = json.loads(row["payload_json"])
            view = read_object(call["view_object_sha256"])
            require(
                semantic_sha256_v22({"task": TASK, "view": view})
                == payload["task_view_sha256"]
                == call["task_view_sha256"],
                "PRIVATE_VIEW_BINDING",
            )
            if call["draft_projection"] is None:
                require(
                    payload["error_code"] == "PROVIDER_TRUNCATED", "PRIVATE_TRUNCATION"
                )
                continue
            raw = payload["proposal"]
            require(
                semantic_sha256_v22(raw) == call["private_draft_sha256"],
                "PRIVATE_RAW_DRAFT_HASH",
            )
            try:
                compile_draft(KnowledgeDraft.model_validate(raw), view)
            except ValueError as exc:
                require(
                    str(exc) == call["result"]["error_code"],
                    "PRIVATE_REJECTION_DIFFERS",
                )
            else:
                raise ValueError("PRIVATE_REJECTION_NOT_REPRODUCED")
            count += 1
        protocol = json.loads((directory / "protocol.json").read_text())
        queries = {
            "calls": "SELECT * FROM investigation_provider_calls_v050 WHERE call_key NOT LIKE 'knowledge-draft-v050.1:%' ORDER BY call_key",
            "sessions": "SELECT * FROM investigation_sessions_v050 ORDER BY session_id",
            "episodes": "SELECT * FROM knowledge_episode_incidents_v050 ORDER BY incident_id",
            "rejections": "SELECT * FROM knowledge_rejections_v050 WHERE source_request_key NOT LIKE 'knowledge-draft-v050.1:%' ORDER BY source_request_key",
        }
        for name, query in queries.items():
            require(
                semantic_sha256_v22([dict(r) for r in c.execute(query)])
                == protocol["protected"][name],
                "PRIVATE_HISTORY_DRIFT:" + name,
            )
    return {
        "verification": "PASS",
        "original_private_draft_rejections_recomputed": count,
        "protected_history_unchanged": True,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-repair", action="store_true")
    args = parser.parse_args()
    result = verify()
    if args.private_repair:
        result["private_repair"] = verify_private_repair()
    print(json.dumps(result, sort_keys=True))
