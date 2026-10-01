"""Explicit one-shot minimal probes; same original ledger, no incident or job."""

import argparse
from datetime import UTC, datetime
import fcntl
import json
import math
import os
from pathlib import Path
import time

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre.product.investigation.contracts import PriceSchedule
from ecomsre.product.investigation.http_diagnostics import http_failure, ProductDiagnosticTransport
from ecomsre.product.investigation.repository import InvestigationRepository
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from scripts.product_v050.project_environment import load_project_environment

ROOT = Path(__file__).resolve().parents[2]
MODEL = 'gpt-5.4-mini-2026-03-17'
PREFIX = 'provider-unblock-20260917:'


def payload_for(stage, style):
    payload = dict(model=MODEL, store=False, service_tier='default')
    if style == 'responses':
        payload.update(input='Reply exactly OK.', max_output_tokens=512, reasoning={'effort': 'none'})
    else:
        payload.update(messages=[{'role': 'user', 'content': 'Reply exactly OK.'}], max_completion_tokens=512, reasoning_effort='none')
    if stage == 'function':
        function = dict(name='ack', description='Acknowledge the probe', strict=True,
                        parameters={'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok'], 'additionalProperties': False})
        if style == 'responses':
            payload.update(input='Call ack with ok=true.', tools=[dict(type='function', **function)], tool_choice={'type': 'function', 'name': 'ack'}, parallel_tool_calls=False)
        else:
            payload.update(messages=[{'role': 'user', 'content': 'Call ack with ok=true.'}], tools=[{'type': 'function', 'function': function}], tool_choice={'type': 'function', 'function': {'name': 'ack'}}, parallel_tool_calls=False)
    return payload


def valid_ack(arguments):
    parsed = json.loads(arguments)
    return isinstance(parsed, dict) and set(parsed) == {'ok'} and type(parsed['ok']) is bool and parsed['ok'] is True


def run_probe(repo, config, prices, *, stage, style, transport=None):
    """Caller holds exclusive local probe lock; repository enforces global caps."""
    payload = payload_for(stage, style)
    path = '/v1/responses' if style == 'responses' else '/v1/chat/completions'
    if config.base_url != 'https://api.openai.com/v1' or config.model != MODEL or prices.model != MODEL:
        raise ValueError('UNBLOCK_CONFIGURATION_MISMATCH')
    key = PREFIX + stage + ':' + style
    with repo.store.connect() as c:
        rows = c.execute('SELECT call_key,reserved_microusd,payload_json,state FROM investigation_provider_calls_v050 WHERE call_key LIKE ?', (PREFIX+'%',)).fetchall()
    if any(r['call_key'] == key for r in rows):
        raise ValueError('UNBLOCK_ATTEMPT_ALREADY_CONSUMED')
    if stage == 'function' and not any(r['state']=='COMPLETED' and json.loads(r['payload_json']).get('stage')=='text' and json.loads(r['payload_json']).get('api_style')==style for r in rows):
        raise ValueError('UNBLOCK_TEXT_GATE_NOT_PASSED')
    input_cap = len(json.dumps(payload).encode()) + 4096
    reserve = math.ceil(input_cap * prices.input_usd_per_million + 512 * prices.output_usd_per_million)
    if len(rows) >= 6 or sum(r['reserved_microusd'] for r in rows) + reserve > 1_000_000:
        raise ValueError('UNBLOCK_SUB_BUDGET_EXHAUSTED')
    repo.reserve(key, {'payload':payload,'base_url':config.base_url,'pricing':prices.model_dump(mode='json')}, reserve)
    started = time.monotonic()
    report = dict(attempt_id=key, ledger_key=key, started_at=datetime.now(UTC).isoformat(), method='POST',
                  target_host='api.openai.com', target_path=path, requested_model=MODEL, api_style=style,
                  stage=stage, payload_shape={k:{'type':type(v).__name__, 'serialized_length':len(json.dumps(v))} for k,v in payload.items()},
                  proxy_environment_present=any(os.environ.get(k) for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')),
                  reserved_microusd=reserve, accounted_microusd=None, usage_status='UNKNOWN', status='FAIL',
                  http_status=None, response_content_type=None, request_id=None)
    transport = transport or ProductDiagnosticTransport()
    charge = None
    state = 'FAILED'
    try:
        response = transport.post_json(url='https://api.openai.com'+path,
            headers={'Authorization':'Bearer '+config.api_key,'Content-Type':'application/json'},payload=payload,timeout_seconds=90)
        report['http_status']=200
        if response.get('model') != MODEL:
            state='UNSAFE_COST_BOUND'
            raise ValueError('MODEL_IDENTITY_MISMATCH')
        usage=response.get('usage') or {}
        inp=usage.get('input_tokens' if style=='responses' else 'prompt_tokens')
        out=usage.get('output_tokens' if style=='responses' else 'completion_tokens')
        if type(inp) is int and type(out) is int and inp>=0 and out>=0:
            charge=math.ceil(inp*prices.input_usd_per_million+out*prices.output_usd_per_million)
            report.update(usage={'input_tokens':inp,'output_tokens':out}, usage_status='REPORTED', cost_basis='REPORTED_TOKENS_AT_UPPER_RATES_NOT_INVOICE')
            if inp>input_cap or out>512 or charge>reserve:
                state='UNSAFE_COST_BOUND'
                raise ValueError('USAGE_BOUND_EXCEEDED')
        if style=='responses':
            if response.get('status')!='completed' or response.get('error') is not None:
                raise ValueError('RESPONSE_NOT_COMPLETED')
            output=response.get('output',[])
            if any(p.get('type')=='refusal' for item in output for p in item.get('content',[])):
                raise ValueError('RESPONSE_REFUSED')
            if stage=='text':
                visible=''.join(p.get('text','') for item in output for p in item.get('content',[]) if p.get('type')=='output_text')
                if visible.strip()!='OK':
                    raise ValueError('VISIBLE_TEXT_NOT_OK')
            else:
                calls=[item for item in output if item.get('type')=='function_call']
                if len(calls)!=1 or calls[0].get('status')!='completed' or calls[0].get('name')!='ack' or not valid_ack(calls[0]['arguments']):
                    raise ValueError('FUNCTION_CONTRACT_INVALID')
        else:
            choices=response.get('choices',[])
            if len(choices)!=1 or choices[0].get('finish_reason') not in {'stop','tool_calls'} or choices[0].get('message',{}).get('refusal'):
                raise ValueError('CHAT_NOT_COMPLETED')
            message=choices[0]['message']
            if stage=='text' and message.get('content','').strip()!='OK':
                raise ValueError('VISIBLE_TEXT_NOT_OK')
            if stage=='function':
                calls=message.get('tool_calls',[])
                if len(calls)!=1 or calls[0].get('function',{}).get('name')!='ack' or not valid_ack(calls[0]['function']['arguments']):
                    raise ValueError('FUNCTION_CONTRACT_INVALID')
        report['status']='PASS'
        state='COMPLETED'
    except Exception as exc:
        diagnostic = http_failure(exc, secret=config.api_key)
        if diagnostic['http_status'] is None and report['http_status'] is not None:
            diagnostic.pop('http_status')
        report.update(diagnostic)
        # Exception messages can contain requests/credentials; retain types only.
        report['failure_category']=type(exc).__name__
    finally:
        report.update(getattr(transport, 'last_response', {}))
        report.update(latency_ms=(time.monotonic()-started)*1000, completed_at=datetime.now(UTC).isoformat(), accounted_microusd=charge)
        repo.settle(key,report,charge,state)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--stage',choices=['text','function'],default='text')
    parser.add_argument('--api-style',choices=['responses','chat_completions'],default='responses')
    args=parser.parse_args()
    os.umask(0o077)
    root=ROOT/'.local/product-v050'
    if not (root/'product.sqlite3').is_file():
        raise ValueError('ORIGINAL_LEDGER_REQUIRED')
    loaded=load_project_environment(Path.home()/'.config/ecomsre/provider.env')
    price_path=Path(os.environ.get('ECOMSRE_PRODUCT_PROVIDER_PRICE_FILE') or ROOT/'config/product-v050/openai-gpt54-mini-prices-20260917.json')
    prices=PriceSchedule.model_validate_json(price_path.read_text())
    config=OpenAICompatibleConfig.from_environment()
    if config is None:
        raise ValueError('PROJECT_CONFIGURATION_REQUIRED')
    store=SqliteStoreV1(root/'product.sqlite3')
    repo=InvestigationRepository(store,ContentAddressedObjectStoreV1(root/'objects',metadata_store=store))
    if not args.execute:
        print(json.dumps({'configuration_present':loaded,'ledger':str(root),'accounting':repo.accounting(),'dispatch':False}))
        return
    output=root/'provider-unblock'
    output.mkdir(exist_ok=True)
    with (output/'probe.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        report=run_probe(repo,config,prices,stage=args.stage,style=args.api_style)
        with (output/(args.stage+'-'+args.api_style+'.json')).open('x') as target:
            json.dump(report,target,indent=2)
    print(json.dumps(report),flush=True)
    print(json.dumps({'accounting':repo.accounting()}),flush=True)


if __name__=='__main__':
    main()
