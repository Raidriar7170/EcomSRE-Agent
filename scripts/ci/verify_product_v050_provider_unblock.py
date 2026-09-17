"""Current source checks plus retained, bounded Provider-unblock observations."""

import hashlib
import json
from pathlib import Path

from scripts.product_v050.run_offline_checks import bound_sources
from scripts.ci.verify_product_v050 import require
from scripts.ci.verify_product_v050_continuation import verify as verify_history

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / 'docs/results/product-v050/provider-unblock'


def verify():
    verify_history()
    offline = json.loads((RESULT / 'offline-checks.json').read_text())
    require(offline['exit_code'] == 0 and all(c['status'] == 'PASSED' for c in offline['cases']), 'OFFLINE_FAILURE')
    require(set(offline['source_sha256']) == {str(p.relative_to(ROOT)) for p in bound_sources(ROOT)}, 'SOURCE_SCOPE')
    for path, digest in offline['source_sha256'].items():
        require(hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == digest, 'SOURCE_DRIFT:'+path)
    report = json.loads((RESULT / 'result.json').read_text())
    calls = json.loads((RESULT / 'calls.json').read_text())
    sessions = json.loads((RESULT / 'smoke-traces.json').read_text())
    return verify_claims(report, calls, sessions)


def verify_claims(report, calls, sessions):
    probes = [c for c in calls if c['kind'] == 'MINIMAL_PROBE']
    require(len(probes) <= 6 and sum(c['reserved_microusd'] for c in probes) <= 1_000_000, 'SUB_BUDGET')
    require(len(calls) == report['accounting']['request_count'] <= 200, 'TOTAL_REQUESTS')
    commitment = sum(c['accounted_microusd'] if c['accounted_microusd'] is not None else c['reserved_microusd'] for c in calls)
    require(commitment == report['accounting']['committed_upper_microusd'] <= 20_000_000, 'TOTAL_COST')
    require(report['actual_invoice_usd'] is None, 'INVOICE_UNKNOWN')
    require(all(c['requested_model'] == 'gpt-5.4-mini-2026-03-17' for c in calls), 'MODEL_DRIFT')
    for stage in ('text', 'function'):
        selected = [c for c in probes if c['stage'] == stage]
        require(len(selected) == 1 and selected[0]['status'] == 'PASS' and selected[0]['http_status'] == 200, 'MINIMAL_GATE:'+stage)
    reads = sum(d['kind'] == 'READ' and d['validation'] == 'ACCEPTED' for s in sessions for d in s['decisions'])
    require(reads == report['accepted_model_selected_reads'] == sum(s['read_count'] for s in sessions), 'READ_COUNT')
    from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
    require(report['terminal'] == 'ECOMSRE_PRODUCT_V050_ENGINEERING_COMPLETE_WITH_LIMITATIONS', 'UNSUPPORTED_TERMINAL')
    by_key = {c['ledger_key']: c for c in calls}
    require(len(by_key) == len(calls), 'DUPLICATE_LEDGER_KEY')
    roundtrips = 0
    for session in sessions:
        require(session['read_count'] == len(session['supplemental_observations']), 'SUPPLEMENTAL_COUNT')
        observations = {}
        for o in session['supplemental_observations']:
            action = 'read-' + semantic_sha256_v22({'request': o['request_sha256'], 'window': o['window'], 'capability': o['capability_sha256']})[:24]
            require(action == o['catalog_action_id'] and o['evidence_ref'] == 'investigation:' + o['object_sha256'], 'OBSERVATION_BINDING')
            observations[action] = o
        decisions = {d['provider_call_index']: d for d in session['decisions']}
        inputs = {i['call_index']: i for i in session['call_inputs']}
        for index, decision in decisions.items():
            call = by_key.get(session['session_id'] + ':' + str(index))
            proposal = {k:v for k,v in decision.items() if k not in {'validation', 'provider_call_index'}}
            require(call is not None and call['state'] == 'COMPLETED' and call['http_status'] == 200 and call['proposal_sha256'] == semantic_sha256_v22(proposal), 'DECISION_CALL_BINDING')
            if decision['kind'] == 'READ' and decision['validation'] == 'ACCEPTED':
                observation = observations.get(decision['action_id'])
                require(observation is not None, 'MISSING_READ_OBSERVATION')
                # Last capped read has no following model response and does not
                # establish a complete roundtrip by itself.
                if index + 1 in decisions:
                    require(observation['evidence_ref'] in inputs[index+1]['observed_evidence_refs'], 'FOLLOWUP_DID_NOT_OBSERVE_READ')
                    roundtrips += 1
    require(roundtrips > 0 and report['product_tool_roundtrip'] == 'PASS_REPLAY_ATTEMPT_6', 'NO_VERIFIED_ROUNDTRIP')
    require(report['live_episode_count'] == report['new_product_external_writes'] == 0, 'NO_LIVE_OR_WRITE_CLAIM')
    require(report['promotion'] == report['independent_holdout'] == report['learned_reuse'] == 'NOT_ATTEMPTED', 'NO_LEARNING_SUCCESS_CLAIM')
    require(report['historical_404_root_cause'] == 'UNDETERMINED_OLD_RESPONSE_METADATA_NOT_RETAINED', 'NO_RETROACTIVE_ROOT_CAUSE')
    return {'verification':'PASS','scope':'MINIMAL_PROVIDER_AND_REPLAY_PROTOCOL_ONLY','tool_roundtrip':'PASS_REPLAY_ATTEMPT_6','complete_read_followups':roundtrips,'terminal':report['terminal']}


if __name__ == '__main__':
    print(json.dumps(verify(),sort_keys=True))
