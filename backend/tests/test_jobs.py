import os
import time
from threading import Event, Thread
from pathlib import Path
import pytest
from fastapi import HTTPException
from services import storage
from services.jobs import JobWorker, _write


@pytest.fixture
def root(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'DATA_DIR',tmp_path/'jobs')
    storage.DATA_DIR.mkdir()
    return storage.DATA_DIR


def saved(root,job_id,status='queued'):
    directory=root/job_id
    directory.mkdir()
    (directory/'source.mp4').write_bytes(b'video')
    now=time.time()
    _write(directory,{'job_id':job_id,'status':status,'progress':0,'created_at':now,
                      'updated_at':now,'finished_at':None,'result':None,'error':None})
    return directory


def processor(job_id,directory,update):
    update('preparing',5,'Preparing')
    update('transcribing',15,'Transcribing')
    update('selecting',65,'Selecting')
    update('rendering',75,'Rendering clip 1 of 1')
    (directory/'clip_1.mp4').write_bytes(b'clip')
    return {'transcript':'hello','highlights':[], 'clips':[f'/pipeline/{job_id}/clips/clip_1.mp4']}


def wait(worker,job_id,expected):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        data=worker.snapshot(job_id)
        if data['status']==expected:
            return data
        time.sleep(.01)
    raise AssertionError(f'Job did not become {expected}')


def test_queued_jobs_survive_restart_and_results_persist(root):
    job_id='a'*32
    directory=saved(root,job_id)
    worker=JobWorker(processor)
    worker.start()
    try:
        result=wait(worker,job_id,'completed')
        assert result['progress']==100 and result['result']['transcript']=='hello'
        assert not (directory/'source.mp4').exists()
    finally:
        worker.stop()
    other=JobWorker(lambda *_:pytest.fail('Completed work must not rerun'))
    other.start()
    try:
        assert other.snapshot(job_id)['result']==result['result']
    finally:
        other.stop()


def test_restart_marks_active_job_failed_and_cleans_partial_media(root):
    job_id='b'*32
    directory=saved(root,job_id,'rendering')
    (directory/'clip_1.mp4').write_bytes(b'partial')
    worker=JobWorker(processor)
    worker.start()
    try:
        result=worker.snapshot(job_id)
        assert result['status']=='failed' and result['error']['code']=='interrupted'
        assert sorted(p.name for p in directory.iterdir())==['.lock','job.json']
    finally:
        worker.stop()


def test_single_worker_ownership_is_exclusive(root):
    first=JobWorker(processor)
    first.start()
    try:
        second=JobWorker(processor)
        with pytest.raises(RuntimeError,match='Another worker'):
            second.start()
    finally:
        first.stop()
    third=JobWorker(processor)
    third.start()
    third.stop()


def test_queued_jobs_protected_from_retention_while_waiting(root):
    first_id,second_id='a'*32,'b'*32
    saved(root,first_id)
    second=saved(root,second_id)
    started,release=Event(),Event()
    def blocked(job_id,directory,update):
        update('transcribing',15,'Transcribing')
        started.set()
        assert release.wait(5)
        return processor(job_id,directory,update)
    worker=JobWorker(blocked)
    worker.start()
    try:
        assert started.wait(5)
        timestamp=time.time()-25*3600
        os.utime(second,(timestamp,timestamp))
        assert storage.cleanup_expired_jobs()==0
        assert worker.snapshot(second_id)['status']=='queued'
    finally:
        release.set()
        worker.stop()


def test_graceful_stop_keeps_waiting_jobs_and_records_interruption(root):
    first_id,second_id='a'*32,'b'*32
    saved(root,first_id)
    saved(root,second_id)
    started,release=Event(),Event()
    def blocked(job_id,directory,update):
        update('transcribing',15,'Transcribing')
        started.set()
        assert release.wait(5)
        return processor(job_id,directory,update)
    worker=JobWorker(blocked)
    worker.start()
    assert started.wait(5)
    worker.stopping.set()
    release.set()
    worker.stop()
    assert storage.read_job_metadata(root/first_id)['error']['code']=='interrupted'
    assert storage.read_job_metadata(root/second_id)['status']=='queued'
    replacement=JobWorker(processor)
    replacement.start()
    try:
        assert wait(replacement,second_id,'completed')['progress']==100
    finally:
        replacement.stop()


def test_corrupted_status_is_explicitly_failed(root):
    directory=root/('c'*32)
    directory.mkdir()
    (directory/'job.json').write_text('{broken')
    worker=JobWorker(processor)
    worker.start()
    try:
        result=worker.snapshot(directory.name)
        assert result['status']=='failed' and result['error']['code']=='invalid_status'
    finally:
        worker.stop()


def test_status_rejects_invalid_missing_symlink_and_expired_ids(root,tmp_path):
    worker=JobWorker(processor)
    worker.start()
    try:
        for job_id in ['../outside','not-an-id','d'*32]:
            with pytest.raises(HTTPException) as exc:
                worker.snapshot(job_id)
            assert exc.value.status_code==404
        outside=tmp_path/'outside'
        outside.mkdir()
        (root/('e'*32)).symlink_to(outside,target_is_directory=True)
        with pytest.raises(HTTPException):
            worker.snapshot('e'*32)
        directory=saved(root,'f'*32,'failed')
        timestamp=time.time()-25*3600
        os.utime(directory,(timestamp,timestamp))
        with pytest.raises(HTTPException) as exc:
            worker.snapshot('f'*32)
        assert exc.value.status_code==404
    finally:
        worker.stop()


@pytest.mark.parametrize('value',['0','-1','21','nan'])
def test_invalid_queue_cap_rejected(root,monkeypatch,value):
    monkeypatch.setenv('MAX_QUEUED_JOBS',value)
    with pytest.raises(ValueError):
        JobWorker(processor)


def test_invalid_timestamp_metadata_is_failed_instead_of_blocking_restart(root):
    directory=saved(root,'a'*32)
    data=storage.read_job_metadata(directory)
    data['created_at']=10**1000
    _write(directory,data)
    worker=JobWorker(processor)
    worker.start()
    try:
        assert worker.snapshot(directory.name)['error']['code']=='invalid_status'
    finally:
        worker.stop()


@pytest.mark.parametrize('bad_state',[[],{},None])
def test_non_string_state_is_recovered_as_failure(root,bad_state):
    directory=saved(root,'a'*32)
    data=storage.read_job_metadata(directory)
    data['status']=bad_state
    _write(directory,data)
    worker=JobWorker(processor)
    worker.start()
    try:
        assert worker.snapshot(directory.name)['error']['code']=='invalid_status'
    finally:
        worker.stop()
