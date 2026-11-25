import asyncio
from unittest.mock import Mock
import pytest
from request_limits import RequestLimitMiddleware
import request_limits


def request(chunks, headers=(), disconnect=False, app_error=False, middleware=None):
    called, messages, received = [], [], []
    async def app(scope, receive, send):
        called.append(True)
        body = b''
        while True:
            message = await receive()
            body += message.get('body', b'')
            if not message.get('more_body'):
                break
        received.append(body)
        if app_error:
            raise RuntimeError('test route failure')
        await send({'type':'http.response.start', 'status':200, 'headers':[]})
        await send({'type':'http.response.body', 'body':b'ok'})
    calls = []
    items = [{'type':'http.request', 'body':c, 'more_body':i<len(chunks)-1} for i,c in enumerate(chunks)]
    if disconnect:
        items=[{'type':'http.disconnect'}]
    async def receive():
        calls.append(True)
        return items.pop(0)
    async def send(message):
        messages.append(message)
    scope={'type':'http','method':'POST','path':'/pipeline/','headers':list(headers)}
    layer=middleware or RequestLimitMiddleware(app)
    asyncio.run(layer(scope, receive, send))
    return called, messages, received, calls


def status(messages):
    return next(m['status'] for m in messages if m['type']=='http.response.start')


def test_declared_oversize_is_rejected_without_receiving_or_parsing(monkeypatch):
    monkeypatch.setattr(request_limits, 'MAX_REQUEST_BYTES', 8)
    called, messages, _, reads=request([b'not read'], [(b'content-length',b'9')])
    assert status(messages)==413
    assert not called and not reads


@pytest.mark.parametrize('headers', [[], [(b'transfer-encoding', b'chunked')], [(b'content-length',b'1')]])
def test_actual_stream_limit_precedes_application_even_if_length_missing_or_false(monkeypatch, headers):
    monkeypatch.setattr(request_limits, 'MAX_REQUEST_BYTES', 8)
    called, messages, _, _=request([b'1234', b'5678', b'9'], headers)
    assert status(messages)==413 and not called


@pytest.mark.parametrize('headers', [[(b'content-length',b'-1')], [(b'content-length',b'abc')],
    [(b'content-length',b'2'),(b'content-length',b'2')],
    [(b'content-length',b'2'),(b'transfer-encoding',b'chunked')]])
def test_invalid_or_ambiguous_lengths_rejected_without_read(monkeypatch, headers):
    called, messages, _, reads=request([b'xx'],headers)
    assert status(messages)==400 and not called and not reads


def test_valid_request_at_boundary_replays_exact_bytes(monkeypatch):
    monkeypatch.setattr(request_limits, 'MAX_REQUEST_BYTES', 8)
    called, messages, bodies, _=request([b'1234',b'5678'],[(b'content-length',b'8')])
    assert status(messages)==200 and called and bodies==[b'12345678']


def test_shorter_body_than_declared_is_not_parsed(monkeypatch):
    called,messages,_,_=request([b'x'],[(b'content-length',b'2')])
    assert status(messages)==400 and not called


def test_staging_file_closed_on_rejection_disconnect_and_route_failure(monkeypatch):
    monkeypatch.setattr(request_limits, 'MAX_REQUEST_BYTES', 8)
    original=request_limits.tempfile.TemporaryFile
    files=[]
    def temporary():
        file=original()
        files.append(file)
        return file
    monkeypatch.setattr(request_limits.tempfile,'TemporaryFile',temporary)
    request([b'123456789'])
    request([],disconnect=True)
    with pytest.raises(RuntimeError):
        request([b'valid'],app_error=True)
    assert len(files)==3 and all(f.closed for f in files)


def test_concurrent_upload_limit_does_not_read_body():
    app=Mock()
    layer=RequestLimitMiddleware(app)
    assert layer.slots.acquire(False) and layer.slots.acquire(False)
    try:
        called,messages,_,reads=request([b'x'],middleware=layer)
        assert status(messages)==429 and not reads
        app.assert_not_called()
    finally:
        layer.slots.release()
        layer.slots.release()


def test_real_multipart_route_is_not_reached_for_large_declared_body(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from main import app
    from services import storage
    monkeypatch.setattr(storage,'DATA_DIR',tmp_path/'jobs')
    with TestClient(app) as client:
        response=client.post('/upload/',content=b'',headers={
            'content-length':str(request_limits.MAX_REQUEST_BYTES+1),
            'content-type':'multipart/form-data; boundary=missing',
            'origin':'http://localhost:3000'})
    assert response.status_code==413
    assert response.headers['access-control-allow-origin']=='http://localhost:3000'
    assert not storage.DATA_DIR.exists()
