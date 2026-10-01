import io
import json
import urllib.error

import pytest

from ecomsre.product.investigation import http_diagnostics as diag

KEY = 'sk-test-secret-not-a-real-credential'


class Body(io.BytesIO):
    reads = 0

    def read(self, size=-1):
        self.reads += 1
        assert size == diag.BODY_LIMIT + 1
        return super().read(size)


def failure(body, headers=None):
    stream = Body(body)
    error = urllib.error.HTTPError('https://api.openai.com/v1/responses', 404, 'ignored', headers or {}, stream)
    outer = ConnectionError('safe')
    outer.__cause__ = error
    return outer, stream


@pytest.mark.parametrize('body,kind', [
    (b'{"error":{"message":"model unavailable","code":"model_not_found","type":"invalid_request_error"}}', 'JSON_ERROR'),
    (b'<html>404 secret contents</html>', 'HTML'),
    (b'{"message":"do not expose this"}', 'OTHER'),
    (b'', 'EMPTY'),
    (b'404 page not found\n', 'TEXT'),
    (b'x' * (diag.BODY_LIMIT + 100), 'TEXT'),
])
def test_bounded_single_read_and_categories(body, kind):
    exc, stream = failure(body)
    result = diag.http_failure(exc, secret=KEY)
    assert result['http_status'] == 404
    assert result['response_body_kind'] == kind
    assert result['request_id'] is None
    assert result['body_truncated'] == (len(body) > diag.BODY_LIMIT)
    assert stream.reads == 1


def test_secret_redaction_and_allowlisted_headers():
    exc, stream = failure(json.dumps({'error': {'message': 'Bad API key: '+KEY}}).encode(),
                          {'Content-Type': 'application/json; charset=utf-8', 'x-request-id': 'req_example', 'Set-Cookie': KEY})
    result = diag.http_failure(exc, secret=KEY)
    assert KEY not in json.dumps(result)
    assert result['response_content_type'] == 'application/json'
    assert result['request_id'] == 'req_example'
    assert '[REDACTED]' in result['sanitized_error_message']
    assert stream.reads == 1


def test_sanitizer_failure_keeps_status(monkeypatch):
    exc, stream = failure(b'{"error":{"message":"some message"}}')
    def broken(*args):
        raise RuntimeError(KEY)
    monkeypatch.setattr(diag, 'sanitize_message', broken)
    result = diag.http_failure(exc, secret=KEY)
    assert result['http_status'] == 404
    assert result['sanitized_error_message'] is None
    assert result['message_projection_status'] == 'SANITIZATION_OR_READ_FAILED'
    assert stream.reads == 1


def test_failed_probe_keeps_reservation_and_cannot_repeat(tmp_path):
    from types import SimpleNamespace
    from test_investigation import repository
    from ecomsre.model.gateway import OpenAICompatibleConfig
    from ecomsre.product.investigation.contracts import PriceSchedule
    from scripts.product_v050.provider_unblock import run_probe, MODEL
    repo = repository(tmp_path)
    config = OpenAICompatibleConfig('https://api.openai.com/v1', KEY, MODEL)
    prices = PriceSchedule(provider_profile='test', model=MODEL, as_of='2026-09-17', input_usd_per_million=0.75, output_usd_per_million=4.5, source='fixture')
    exc, stream = failure(b'{"error":{"message":"model unavailable","code":"model_not_found"}}', {'Content-Type':'application/json','x-request-id':'req_fixture'})
    def post(**kwargs):
        assert kwargs['url']=='https://api.openai.com/v1/responses'
        assert kwargs['payload']['reasoning']=={'effort':'none'}
        assert 'tools' not in kwargs['payload']
        raise exc
    transport=SimpleNamespace(post_json=post)
    result=run_probe(repo,config,prices,stage='text',style='responses',transport=transport)
    assert result['http_status']==404
    assert result['provider_error_code']=='model_not_found'
    assert result['request_id']=='req_fixture'
    assert stream.reads==1
    assert repo.accounting()['unknown_usage_requests']==1
    assert repo.accounting()['committed_upper_microusd']==result['reserved_microusd']
    with pytest.raises(ValueError,match='ALREADY_CONSUMED'):
        run_probe(repo,config,prices,stage='text',style='responses',transport=transport)
    with pytest.raises(ValueError,match='TEXT_GATE'):
        run_probe(repo,config,prices,stage='function',style='responses',transport=transport)
    assert repo.accounting()['request_count']==1


def test_provider_uses_diagnostic_once_and_keeps_failure_budget(tmp_path):
    from types import SimpleNamespace
    from test_investigation import repository
    from ecomsre.model.gateway import OpenAICompatibleConfig
    from ecomsre.product.investigation.contracts import InvestigationDecision, PriceSchedule
    from ecomsre.product.investigation.provider import StructuredProvider
    from ecomsre.product.errors import ProductError
    repo=repository(tmp_path)
    exc,stream=failure(b'404 page not found',{'Content-Type':'text/plain'})
    def post(**kwargs):
        raise exc
    provider=StructuredProvider(OpenAICompatibleConfig('https://api.openai.com/v1',KEY,'fixture'),
        PriceSchedule(provider_profile='fixture',model='fixture',as_of='2026-09-17',input_usd_per_million=1,output_usd_per_million=1,source='fixture'),
        repo,transport=SimpleNamespace(post_json=post))
    with pytest.raises(ProductError) as error:
        provider.complete(key='test',task='test',view={},schema=InvestigationDecision)
    assert error.value.code=='PROVIDER_HTTP_404'
    assert stream.reads==1
    assert repo.accounting()['unknown_usage_requests']==1
    with repo.store.connect() as c:
        saved=json.loads(c.execute('SELECT payload_json FROM investigation_provider_calls_v050').fetchone()[0])
    assert saved['sanitized_error_message']=='404 page not found'


@pytest.mark.parametrize('arguments,valid', [('{"ok":true}',True),('{"ok":1}',False),('{"ok":false}',False),('{"ok":true,"extra":1}',False)])
def test_ack_requires_actual_boolean(arguments,valid):
    from scripts.product_v050.provider_unblock import valid_ack
    assert valid_ack(arguments) is valid


def test_http200_incomplete_not_misclassified_as_transport(tmp_path):
    from types import SimpleNamespace
    from test_investigation import repository
    from ecomsre.model.gateway import OpenAICompatibleConfig
    from ecomsre.product.investigation.contracts import PriceSchedule
    from scripts.product_v050.provider_unblock import run_probe, MODEL
    repo=repository(tmp_path)
    response={'model':MODEL,'status':'incomplete','usage':{'input_tokens':1,'output_tokens':2},'output':[]}
    result=run_probe(repo,OpenAICompatibleConfig('https://api.openai.com/v1',KEY,MODEL),
        PriceSchedule(provider_profile='fixture',model=MODEL,as_of='2026-09-17',input_usd_per_million=1,output_usd_per_million=1,source='fixture'),
        stage='text',style='responses',transport=SimpleNamespace(post_json=lambda **kwargs:response))
    assert result['status']=='FAIL' and result['http_status']==200
    assert result['accounted_microusd']==3


@pytest.mark.parametrize('count,reservation', [(6,100),(1,999999)])
def test_probe_sub_budget_blocks_before_transport(tmp_path,count,reservation):
    from test_investigation import repository
    from ecomsre.model.gateway import OpenAICompatibleConfig
    from ecomsre.product.investigation.contracts import PriceSchedule
    from scripts.product_v050.provider_unblock import run_probe, MODEL, PREFIX
    repo=repository(tmp_path)
    for i in range(count):
        repo.reserve(PREFIX+str(i),{},reservation)
    with pytest.raises(ValueError,match='SUB_BUDGET'):
        run_probe(repo,OpenAICompatibleConfig('https://api.openai.com/v1',KEY,MODEL),
                  PriceSchedule(provider_profile='fixture',model=MODEL,as_of='2026-09-17',input_usd_per_million=1,output_usd_per_million=1,source='fixture'),
                  stage='text',style='responses')
    assert repo.accounting()['request_count']==count


def test_schema_error_location_cannot_echo_secret(tmp_path):
    from types import SimpleNamespace
    from test_investigation import repository
    from ecomsre.model.gateway import OpenAICompatibleConfig
    from ecomsre.product.investigation.contracts import InvestigationDecision, PriceSchedule
    from ecomsre.product.investigation.provider import StructuredProvider
    from ecomsre.product.errors import ProductError
    repo=repository(tmp_path)
    response={'model':'fixture','choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'function':{'name':'submit_proposal','arguments':json.dumps({KEY:KEY})}}]}}]}
    provider=StructuredProvider(OpenAICompatibleConfig('https://api.openai.com/v1',KEY,'fixture'),
        PriceSchedule(provider_profile='fixture',model='fixture',as_of='2026-09-17',input_usd_per_million=1,output_usd_per_million=1,source='fixture'),
        repo,transport=SimpleNamespace(post_json=lambda **kwargs:response))
    with pytest.raises(ProductError):
        provider.complete(key='schema-failure',task='investigate',view={},schema=InvestigationDecision)
    with repo.store.connect() as c:
        saved=c.execute('SELECT payload_json FROM investigation_provider_calls_v050').fetchone()[0]
    assert KEY not in saved
    assert 'UNKNOWN_FIELD' in saved
    assert 'schema_validation_errors' in saved
