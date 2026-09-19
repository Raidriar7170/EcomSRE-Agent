"""Public evidence consistency checks; these are not model-learning evidence."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.ci.verify_product_v050_docker_stability import verify_feasibility_claims

DIRECTORY = Path(__file__).resolve().parents[2] / 'docs/results/product-v050/knowledge-feasibility'


def materials():
    return [json.loads((DIRECTORY / name).read_text()) for name in
            ['result.json', 'calls.json', 'protocol.json', 'feasibility.json']]


def test_real_projection_recomputes_original_development_denominator():
    assert verify_feasibility_claims(*materials())['development'] == '1/2'


@pytest.mark.parametrize('mutation', ['denominator', 'promote', 'model', 'cost', 'outcome', 'role', 'duplicate', 'dev_members', 'duplicate_outcome', 'evaluation_members'])
def test_reject_overclaims_and_history_or_selection_tampering(mutation):
    report, calls, protocol, matrix = deepcopy(materials())
    if mutation == 'denominator':
        report['development_denominator'] = 1
    elif mutation == 'promote':
        report['promoted'] = 1
    elif mutation == 'model':
        calls[0]['actual_model'] = 'other'
    elif mutation == 'cost':
        calls[0]['charged_microusd'] = 0
    elif mutation == 'outcome':
        calls[1]['result']['development']['outcomes'][0]['outcome']['status'] = 'TRUE'
    elif mutation == 'role':
        protocol['roster'][-1]['role'] = 'DISCOVERY'
    elif mutation == 'dev_members':
        protocol['development_ids'][1] = protocol['roster'][0]['incident_id']
    elif mutation == 'duplicate_outcome':
        calls[1]['result']['development']['outcomes'].append(deepcopy(calls[1]['result']['development']['outcomes'][0]))
    elif mutation == 'evaluation_members':
        calls[1]['result']['development']['incident_ids'].pop()
    else:
        calls[2]['model_selection']['predicates'] = ['core:RUNTIME_RUNNING']
    with pytest.raises((AssertionError, RuntimeError, ValueError)):
        verify_feasibility_claims(report, calls, protocol, matrix)
