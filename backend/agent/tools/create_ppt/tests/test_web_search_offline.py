"""Search/reading contracts with local bytes and MockTransport, never real networking."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import sys

import httpx
from pydantic import SecretStr
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from core.config import settings
from agent.tools.create_ppt.scripts import public_materials as public, web_search as search


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Real network is forbidden in these offline checks')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('8.8.8.8', 443))])
    monkeypatch.setattr(settings, 'volc_search_api_key', SecretStr('offline-only-key'))
    monkeypatch.setattr(settings, 'volc_search_free_only_verified', True)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(settings, 'volc_search_free_window_start', (now - timedelta(hours=1)).isoformat())
    monkeypatch.setattr(settings, 'volc_search_free_window_end', (now + timedelta(hours=1)).isoformat())
    monkeypatch.setattr(settings, 'volc_search_free_budget', 10)
    monkeypatch.setattr(settings, 'volc_search_usage_path', str(tmp_path / 'usage.json'))


@pytest.fixture
def transport(monkeypatch):
    client = httpx.Client
    requests = []
    def install(response):
        def send(request):
            requests.append(request)
            if callable(response):
                return response(request)
            code, body = response
            return httpx.Response(code, json=body)
        monkeypatch.setattr(search.httpx, 'Client', lambda **kwargs: client(
            **kwargs, transport=httpx.MockTransport(send)))
        return requests
    return install


def result(rows):
    return {'ResponseMetadata': {'RequestId': 'offline-request'},
            'Result': {'ResultCount': len(rows), 'WebResults': rows}}


def test_unverified_free_boundary_does_not_send_request(monkeypatch, transport):
    seen = transport((200, result([])))
    monkeypatch.setattr(settings, 'volc_search_free_only_verified', False)
    assert search.search_web('学习案例')['status'] == 'unavailable'
    assert seen == []
    assert search.availability()['available'] is False


def test_official_request_and_candidate_fields(transport):
    seen = transport((200, result([{'Title': '真实来源标题', 'Url': 'https://example.org/paper',
        'Summary': '候选摘要', 'Snippet': '片段', 'PublishTime': '2025-03-02', 'SiteName': '来源站点'}])))
    output = search.search_web('学习案例', 3)
    request = seen[0]
    assert request.method == 'POST'
    assert str(request.url) == 'https://open.feedcoopapi.com/search_api/web_search'
    assert request.headers['Authorization'] == 'Bearer offline-only-key'
    assert json.loads(request.content) == {'Query': '学习案例', 'SearchType': 'web', 'Count': 3,
                                           'Filter': {'NeedUrl': True}}
    assert output['status'] == 'success'
    candidate = output['data']['candidates'][0]
    assert candidate['summary'] == '候选摘要' and candidate['content_scope'] == 'search_summary'
    assert candidate['published_at'] == '2025-03-02'
    assert 'offline-only-key' not in json.dumps(output)
    assert 'offline-only-key' not in repr(settings.volc_search_api_key)


def test_empty_search_is_distinct_from_failure(transport):
    transport((200, result([])))
    output = search.search_web('未知案例')
    assert output['status'] == 'no_results' and output['data']['candidates'] == []


@pytest.mark.parametrize('http_status,blocked', [(401, True), (402, True), (403, True), (429, False), (500, False)])
def test_http_failures_stop_only_when_authorization_or_billing_unavailable(transport, http_status, blocked):
    seen = transport((http_status, {}))
    first = search.search_web('案例')
    assert first['status'] in {'error', 'unavailable'}
    second = search.search_web('另一个案例')
    assert len(seen) == (1 if blocked else 2)
    if blocked:
        assert second['status'] == 'unavailable'
    if http_status == 429:
        assert '额度不足' not in first['error']['detail']
        assert 'blocked_reason' not in next(iter(search._usage()['windows'].values()))


@pytest.mark.parametrize('http_status,code', [(200, '10406'), (400, '10406'), (200, '10412')])
def test_quota_error_blocks_following_calls(transport, http_status, code):
    seen = transport((http_status, {'ResponseMetadata': {'Error': {'Code': code}}, 'Result': None}))
    assert search.search_web('案例')['status'] == 'error'
    assert search.search_web('另一个案例')['status'] == 'unavailable'
    assert len(seen) == 1


def test_invalid_response_is_not_reported_as_no_results(transport):
    transport((200, {'ResponseMetadata': {}, 'Result': {}}))
    output = search.search_web('案例')
    assert output['status'] == 'error'
    assert output['error']['code'] == 'search_invalid_response'


def test_timeout_keeps_reservation_and_does_not_retry(monkeypatch, transport):
    monkeypatch.setattr(settings, 'volc_search_free_budget', 1)
    def timeout(request):
        raise httpx.ReadTimeout('offline timeout', request=request)
    seen = transport(timeout)
    assert search.search_web('案例')['error']['code'] == 'search_timeout'
    assert search.search_web('继续')['status'] == 'unavailable'
    assert len(seen) == 1
    assert next(iter(search._usage()['windows'].values()))['reserved'] == 1


@pytest.mark.parametrize('published', ['2025-01-02', None])
def test_actual_publication_date_and_access_time_are_separate(monkeypatch, published):
    meta = '<meta property="article:published_time" content="%s">' % published if published else ''
    html = ('<html><head><title>实际标题</title>' + meta + '</head><body><article>真实正文。</article></body></html>').encode()
    monkeypatch.setattr(public, '_fetch', lambda url: (200, {'Content-Type': 'text/html'}, html))
    material, info = public.read_web_document('https://example.org/source', '核实案例')
    assert material.title == '实际标题' and material.date == published
    assert info['published_at'] == published and info['accessed_at']
    assert info['final_url'] == 'https://example.org/source' and info['source_host'] == 'example.org'
    assert info['full_paper_verified'] is False
    assert info['accessed_at'] != info['published_at']


def test_redirect_to_private_address_is_rejected(monkeypatch):
    seen = []
    def fetch(url):
        seen.append(url)
        return 302, {'Location': 'http://127.0.0.1/private'}, b''
    def dns(host, *args, **kwargs):
        return [(2, 1, 6, '', ('127.0.0.1' if host == '127.0.0.1' else '8.8.8.8', 80))]
    monkeypatch.setattr(public, '_fetch', fetch)
    monkeypatch.setattr(socket, 'getaddrinfo', dns)
    with pytest.raises(ValueError, match='本机、内网'):
        public.read_web_document('https://example.org/source', '核实案例')
    assert seen == ['https://example.org/source']


def test_public_pdf_uses_local_bytes_and_actual_metadata(monkeypatch):
    import fitz
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), 'Actual public PDF body for an offline check.')
        document.set_metadata({'title': 'Actual PDF title'})
        content = document.tobytes()
    monkeypatch.setattr(public, '_fetch', lambda url: (200, {'Content-Type': 'application/pdf'}, content))
    material, info = public.read_web_document('https://example.org/paper.pdf', '核实论文内容')
    assert material.title == 'Actual PDF title' and 'Actual public PDF body' in material.text
    assert info['content_scope'] == 'public_pdf_text'
    assert material.date is None and info['published_at'] is None


@pytest.mark.parametrize('http_status', [200, 503])
def test_only_successful_read_registers_current_task_material(monkeypatch, http_status):
    html = b'<html><title>Paper entry</title><body><article>Visible body only.</article></body></html>'
    monkeypatch.setattr(public, '_fetch', lambda url: (http_status, {'Content-Type': 'text/html'}, html))
    registered = []
    def register(material, metadata):
        registered.append((material, metadata))
        return {'material_id': material.material_id, 'evidence_id': material.material_id}
    tool = next(item for item in search.web_tools({'task_id': 'offline-task'}, register) if item.name == 'read_webpage')
    output = tool.handler(url='https://example.org/source', purpose='核实案例')
    assert len(registered) == (1 if http_status == 200 else 0)
    assert output['status'] == ('success' if http_status == 200 else 'error')
    if http_status == 503:
        assert output['data']['reading_status'] == 'failed'
    else:
        assert output['data']['material_id'] == registered[0][0].material_id
