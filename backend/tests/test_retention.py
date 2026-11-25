import asyncio
import os
import time
from pathlib import Path
from threading import Event
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from services import storage
import main


@pytest.fixture
def jobs(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'DATA_DIR',tmp_path/'jobs')
    monkeypatch.setenv('JOB_RETENTION_HOURS','24')
    return storage.DATA_DIR


def job(jobs,name,old=True,lock=True):
    directory=jobs/name
    directory.mkdir(parents=True)
    (directory/'clip_1.mp4').write_bytes(b'video')
    if lock:
        (directory/'.lock').touch()
    if old:
        timestamp=time.time()-25*3600
        os.utime(directory,(timestamp,timestamp))
    return directory


def test_only_expired_managed_directories_removed(jobs,tmp_path):
    old=job(jobs,'a'*32)
    fresh=job(jobs,'b'*32,False)
    legacy=job(jobs,'c'*32,lock=False)
    other=job(jobs,'not-a-job')
    outside=tmp_path/'outside'
    outside.mkdir()
    (outside/'keep').write_text('safe')
    (jobs/('d'*32)).symlink_to(outside,target_is_directory=True)
    (old/'nested-link').symlink_to(outside,target_is_directory=True)
    timestamp=time.time()-25*3600
    os.utime(old,(timestamp,timestamp))
    assert storage.cleanup_expired_jobs()==2
    assert not old.exists() and not legacy.exists()
    assert fresh.exists() and other.exists() and (outside/'keep').read_text()=='safe'


def test_processing_lock_skips_active_job_and_completion_resets_retention(jobs):
    directory=job(jobs,'a'*32)
    lease=storage._lease(directory)
    assert storage.cleanup_expired_jobs()==0 and directory.exists()
    storage.finish_job(directory,lease)
    assert storage.cleanup_expired_jobs()==0 and directory.exists()


def test_download_lease_lasts_until_stream_finishes(jobs):
    directory=job(jobs,'a'*32,False)
    path,lease=storage.get_clip(directory.name,'clip_1.mp4')
    timestamp=time.time()-25*3600
    os.utime(directory,(timestamp,timestamp))
    assert storage.cleanup_expired_jobs()==0
    response=storage.LeasedFileResponse(path,lease)
    messages=[]
    async def send(message):
        messages.append(message)
        assert directory.exists()
        assert storage.cleanup_expired_jobs()==0
    async def receive():
        return {'type':'http.disconnect'}
    asyncio.run(response({'type':'http','method':'GET','headers':[], 'extensions':{}, 'asgi':{'spec_version':'2.4'}},receive,send))
    assert b''.join(m.get('body',b'') for m in messages)==b'video'
    assert storage.cleanup_expired_jobs()==1


def test_download_error_releases_lease(jobs):
    directory=job(jobs,'a'*32,False)
    path,lease=storage.get_clip(directory.name,'clip_1.mp4')
    response=storage.LeasedFileResponse(path,lease)
    async def send(_):
        raise RuntimeError('disconnected')
    async def receive():
        return {'type':'http.disconnect'}
    with pytest.raises(RuntimeError):
        asyncio.run(response({'type':'http','method':'GET','headers':[], 'asgi':{'spec_version':'2.4'}},receive,send))
    timestamp=time.time()-25*3600
    os.utime(directory,(timestamp,timestamp))
    assert storage.cleanup_expired_jobs()==1


def test_expired_clip_returns_404_before_periodic_sweep(jobs):
    directory=job(jobs,'a'*32)
    with pytest.raises(HTTPException) as exc:
        storage.get_clip(directory.name,'clip_1.mp4')
    assert exc.value.status_code==404


def test_startup_cleanup_and_periodic_cleanup(jobs,monkeypatch):
    old=job(jobs,'a'*32)
    completed=Event()
    original=storage.cleanup_expired_jobs
    def sweep():
        existed=(jobs/('b'*32)).exists()
        result=original()
        if existed and not (jobs/('b'*32)).exists():
            completed.set()
        return result
    monkeypatch.setattr(main,'cleanup_interval',lambda:.02)
    monkeypatch.setattr(main,'cleanup_expired_jobs',sweep)
    with TestClient(main.app):
        assert not old.exists()
        completed.clear()
        later=job(jobs,'b'*32)
        assert completed.wait(2)
        assert not later.exists()


@pytest.mark.parametrize('value',['0','-1','nan','inf','9000'])
def test_invalid_retention_rejected(monkeypatch,value):
    monkeypatch.setenv('JOB_RETENTION_HOURS',value)
    with pytest.raises(ValueError):
        storage.retention_seconds()


@pytest.mark.parametrize('value',['0','-1','nan','86401'])
def test_invalid_cleanup_interval_rejected(monkeypatch,value):
    monkeypatch.setenv('JOB_CLEANUP_INTERVAL_SECONDS',value)
    with pytest.raises(ValueError):
        storage.cleanup_interval()


def test_bad_retention_stops_startup_before_deleting_jobs(jobs,monkeypatch):
    directory=job(jobs,'a'*32)
    monkeypatch.setenv('JOB_RETENTION_HOURS','0')
    with pytest.raises(ValueError), TestClient(main.app):
        pass
    assert directory.exists()
