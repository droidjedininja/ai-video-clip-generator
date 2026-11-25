import io
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from main import app
from routes import pipeline
from services import storage, whisper, highlights, clipper


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATA_DIR', tmp_path/'jobs')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def fake_processing(monkeypatch):
    monkeypatch.setattr(pipeline, 'media_duration', lambda _: 60)
    def extract(source):
        path = source.parent/'audio.mp3'
        path.write_bytes(b'audio')
        return path
    monkeypatch.setattr(pipeline, 'extract_audio', extract)
    monkeypatch.setattr(pipeline, 'transcribe_audio', lambda _: {
        'text': 'hello', 'segments': [{'start': 0, 'end': 10, 'text': 'hello'}]})
    monkeypatch.setattr(pipeline, 'detect_highlights', lambda *_: [{'start': .5, 'end': 5.5, 'reason': 'example'}])
    def cut(source, start, duration, output):
        Path(output).write_bytes(b'clip:' + Path(source).read_bytes())
    monkeypatch.setattr(pipeline, 'cut_clip', cut)


@pytest.mark.parametrize('route', ['/upload/', '/pipeline/'])
@pytest.mark.parametrize('name', ['../outside.mp4', '/tmp/outside.mp4', '..\\outside.mp4', 'C:outside.mp4'])
def test_filename_traversal_rejected(client, tmp_path, route, name):
    response = client.post(route, files={'file': (name, b'video', 'video/mp4')})
    assert response.status_code == 400
    assert not (tmp_path/'outside.mp4').exists()
    assert not storage.DATA_DIR.exists()


def test_storage_rejects_controls_and_empty_filename():
    for name in ['bad\x00.mp4', 'bad\n.mp4', '']:
        with pytest.raises(HTTPException) as exc:
            storage.save_upload(UploadFile(filename=name, file=io.BytesIO(b'x')))
        assert exc.value.status_code == 400


@pytest.mark.parametrize('route', ['/upload/', '/pipeline/'])
def test_upload_size_and_empty_cleanup(client, monkeypatch, route):
    monkeypatch.setattr(storage, 'MAX_UPLOAD_BYTES', 4)
    response = client.post(route, files={'file': ('clip.mp4', b'12345', 'video/mp4')})
    assert response.status_code == 413
    assert list(storage.DATA_DIR.iterdir()) == []
    response = client.post(route, files={'file': ('clip.mp4', b'', 'video/mp4')})
    assert response.status_code == 400
    assert list(storage.DATA_DIR.iterdir()) == []


def test_unknown_extension_rejected(client):
    assert client.post('/upload/', files={'file': ('program.py', b'x')}).status_code == 415


def test_duplicate_original_names_are_isolated(client):
    a = client.post('/upload/', files={'file': ('clip.mp4', b'one')}).json()
    b = client.post('/upload/', files={'file': ('clip.mp4', b'two')}).json()
    assert a['job_id'] != b['job_id']
    assert (storage.DATA_DIR/a['job_id']/'source.mp4').read_bytes() == b'one'
    assert (storage.DATA_DIR/b['job_id']/'source.mp4').read_bytes() == b'two'


def test_jobs_have_distinct_persistent_downloads(client, fake_processing):
    responses = [client.post('/pipeline/', files={'file': ('same.mp4', content)}) for content in (b'first', b'second')]
    assert all(r.status_code == 200 for r in responses)
    first, second = [r.json() for r in responses]
    assert first['job_id'] != second['job_id']
    assert first['clips'] != second['clips']
    assert client.get(first['clips'][0]).content == b'clip:first'
    assert client.get(second['clips'][0]).content == b'clip:second'
    assert first['highlights'][0]['start'] == .5
    for job in (first, second):
        assert sorted(p.name for p in (storage.DATA_DIR/job['job_id']).iterdir()) == ['.lock', 'clip_1.mp4']


def test_download_rejects_invalid_ids_names_and_symlinks(client, tmp_path):
    assert client.get('/pipeline/not-a-job/clips/clip_1.mp4').status_code == 404
    job = 'a'*32
    directory = storage.DATA_DIR/job
    directory.mkdir(parents=True)
    outside = tmp_path/'outside.mp4'
    outside.write_bytes(b'private')
    (directory/'clip_1.mp4').symlink_to(outside)
    assert client.get(f'/pipeline/{job}/clips/clip_1.mp4').status_code == 404
    assert client.get(f'/pipeline/{job}/clips/source.mp4').status_code == 404
    assert client.get(f'/pipeline/{job}/clips/clip_2.mp4').status_code == 404


def test_pipeline_needs_no_api_key(client, fake_processing):
    response = client.post('/pipeline/', files={'file': ('clip.mp4', b'x')})
    assert response.status_code == 200
    assert response.json()['highlight_method'] == 'rule_based'
    assert response.json()['transcription_backend'] == 'faster-whisper'


def test_capacity_returns_busy(client):
    assert pipeline.slots.acquire(False)
    assert not pipeline.slots.acquire(False)
    try:
        assert client.post('/pipeline/', files={'file': ('clip.mp4', b'x')}).status_code == 429
    finally:
        pipeline.slots.release()


@pytest.mark.parametrize('failure,status', [
    (highlights.InvalidHighlights('invalid transcript'), 422),
    (whisper.LocalTranscriptionError('model unavailable'), 503),
    (subprocess.TimeoutExpired('ffmpeg', 180), 504),
    (subprocess.CalledProcessError(1, 'ffmpeg'), 422),
])
def test_failure_cleans_job_and_releases_slot(client, fake_processing, monkeypatch, failure, status):
    def fail(*_):
        raise failure
    monkeypatch.setattr(pipeline, 'cut_clip', fail)
    response = client.post('/pipeline/', files={'file': ('clip.mp4', b'x')})
    assert response.status_code == status
    assert list(storage.DATA_DIR.iterdir()) == []
    assert pipeline.slots.acquire(False)
    assert not pipeline.slots.acquire(False)
    pipeline.slots.release()


def test_other_requests_remain_responsive(client, fake_processing, monkeypatch):
    started, release = Event(), Event()
    def wait(_):
        started.set()
        assert release.wait(5)
        return {'text': 'hello', 'segments': [{'start': 0, 'end': 5, 'text': 'hello'}]}
    monkeypatch.setattr(pipeline, 'transcribe_audio', wait)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, '/pipeline/', files={'file': ('clip.mp4', b'x')})
        try:
            assert started.wait(5)
            assert client.get('/').status_code == 200
        finally:
            release.set()
        assert pending.result().status_code == 200


@pytest.mark.parametrize('value', [None, [], {'highlights': []}, {'highlights': [{}]},
    {'highlights': [{'start': 0, 'end': 1, 'reason': 'x'}]*4},
    {'highlights': [{'start': -1, 'end': 2, 'reason': 'x'}]},
    {'highlights': [{'start': 2, 'end': 1, 'reason': 'x'}]},
    {'highlights': [{'start': 1, 'end': 100, 'reason': 'x'}]},
    {'highlights': [{'start': True, 'end': 2, 'reason': 'x'}]},
    {'highlights': [{'start': 10**1000, 'end': 2, 'reason': 'x'}]},
    {'highlights': [{'start': float('nan'), 'end': 2, 'reason': 'x'}]},
    {'highlights': [{'start': 1, 'end': float('inf'), 'reason': 'x'}]},
])
def test_invalid_model_schema_and_times(value):
    with pytest.raises(highlights.InvalidHighlights):
        highlights.validate_highlights(value, 60)


def test_subprocesses_are_bounded_and_inputs_restricted(monkeypatch, tmp_path):
    runner = Mock(return_value=SimpleNamespace(stdout='{"format":{"duration":"60"}}'))
    monkeypatch.setattr(clipper.subprocess, 'run', runner)
    assert clipper.media_duration(tmp_path/'source.mp4') == 60
    clipper.cut_clip('source.mp4', 1, 2, 'clip_1.mp4')
    for call in runner.call_args_list:
        assert call.kwargs['timeout'] > 0
        assert '-protocol_whitelist' in call.args[0]
        assert '-format_whitelist' in call.args[0]
    assert '-n' in runner.call_args.args[0]
