import copy
import json
from pathlib import Path

import pytest

from scripts.ci.verify_product_v050_provider_unblock import verify_claims

ROOT=Path(__file__).resolve().parents[2]/'docs/results/product-v050/provider-unblock'


def evidence():
    return [json.loads((ROOT/name).read_text()) for name in ['result.json','calls.json','smoke-traces.json']]


def test_retained_trace_establishes_four_followups():
    assert verify_claims(*evidence())['complete_read_followups']==4


@pytest.mark.parametrize('tamper', ['acceptance','zero','missing_observation','detached_call','missing_followup'])
def test_no_unearned_success_from_tampered_projection(tamper):
    report,calls,sessions=copy.deepcopy(evidence())
    if tamper=='acceptance':
        report['terminal']='ECOMSRE_PRODUCT_V050_ACCEPTANCE_PASS'
    elif tamper=='zero':
        report['accepted_model_selected_reads']=0
        sessions=[]
    elif tamper=='missing_observation':
        sessions[-1]['supplemental_observations']=[]
    elif tamper=='detached_call':
        sessions[-1]['session_id']='invented'
    else:
        sessions[-1]['call_inputs'][1]['observed_evidence_refs']=[]
    with pytest.raises((ValueError,RuntimeError)):
        verify_claims(report,calls,sessions)
