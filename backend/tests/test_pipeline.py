import io
import subprocess
import time
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


def wait_job(client, job_id):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        response=client.get(f'/pipeline/jobs/{job_id}')
        assert response.status_code==200,response.text
        data=response.json()
        if data['status'] in {'completed','failed'}:
            return data
        time.sleep(.01)
    raise AssertionError('Job did not finish')


def managed_directories():
    return [p for p in storage.DATA_DIR.iterdir() if p.is_dir()]


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
    assert not managed_directories()


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
    assert not managed_directories()
    response = client.post(route, files={'file': ('clip.mp4', b'', 'video/mp4')})
    assert response.status_code == 400
    assert not managed_directories()


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
    assert all(r.status_code == 202 for r in responses)
    first_job, second_job = [wait_job(client,r.json()['job_id']) for r in responses]
    assert first_job['status'] == second_job['status'] == 'completed'
    first, second = first_job['result'], second_job['result']
    assert first_job['job_id'] != second_job['job_id']
    assert first['clips'] != second['clips']
    assert client.get(first['clips'][0]).content == b'clip:first'
    assert client.get(second['clips'][0]).content == b'clip:second'
    assert first['highlights'][0]['start'] == .5
    for job in (first_job, second_job):
        assert sorted(p.name for p in (storage.DATA_DIR/job['job_id']).iterdir()) == ['.lock', 'clip_1.mp4', 'job.json']


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
    assert response.status_code == 202
    data = wait_job(client,response.json()['job_id'])
    assert data['status'] == 'completed'
    assert data['result']['highlight_method'] == 'rule_based'
    assert data['result']['transcription_backend'] == 'faster-whisper'


def test_queue_capacity_returns_busy(client, fake_processing, monkeypatch):
    started, release = Event(), Event()
    original = pipeline.transcribe_audio
    def wait(path):
        started.set()
        assert release.wait(5)
        return original(path)
    monkeypatch.setattr(pipeline,'transcribe_audio',wait)
    client.app.state.worker.max_queued=1
    first=client.post('/pipeline/',files={'file':('first.mp4',b'x')})
    assert first.status_code==202 and started.wait(5)
    try:
        second=client.post('/pipeline/',files={'file':('second.mp4',b'x')})
        assert second.status_code==202
        assert client.post('/pipeline/',files={'file':('third.mp4',b'x')}).status_code==429
    finally:
        release.set()
    assert wait_job(client,first.json()['job_id'])['status']=='completed'
    assert wait_job(client,second.json()['job_id'])['status']=='completed'


@pytest.mark.parametrize('failure,code', [
    (highlights.InvalidHighlights('invalid transcript'), 'no_highlights'),
    (whisper.LocalTranscriptionError('model unavailable'), 'transcription_failed'),
    (subprocess.TimeoutExpired('ffmpeg',180), 'processing_timeout'),
    (subprocess.CalledProcessError(1,'ffmpeg'), 'invalid_video'),
])
def test_failure_keeps_status_but_removes_media(client,fake_processing,monkeypatch,failure,code):
    def fail(*_):
        raise failure
    monkeypatch.setattr(pipeline,'cut_clip',fail)
    response=client.post('/pipeline/',files={'file':('clip.mp4',b'x')})
    assert response.status_code==202
    data=wait_job(client,response.json()['job_id'])
    assert data['status']=='failed' and data['error']['code']==code
    assert sorted(p.name for p in (storage.DATA_DIR/data['job_id']).iterdir())==['.lock','job.json']


def test_other_requests_remain_responsive(client,fake_processing,monkeypatch):
    started,release=Event(),Event()
    def wait(_):
        started.set()
        assert release.wait(5)
        return {'text':'hello','segments':[{'start':0,'end':5,'text':'hello'}]}
    monkeypatch.setattr(pipeline,'transcribe_audio',wait)
    response=client.post('/pipeline/',files={'file':('clip.mp4',b'x')})
    assert response.status_code==202
    try:
        assert started.wait(5)
        assert client.get('/').status_code==200
        data=client.get(response.json()['status_url']).json()
        assert data['status']=='transcribing' and data['progress']==15
    finally:
        release.set()
    assert wait_job(client,response.json()['job_id'])['status']=='completed'


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
