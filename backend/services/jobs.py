"""Disk-backed local queue, one worker per storage directory, atomic status files."""
import json
import math
import logging
import os
import shutil
import subprocess
import tempfile
import time
from collections import deque
from pathlib import Path
from threading import Condition, Event, Thread
from fastapi import HTTPException
from services import storage
from services.whisper import LocalTranscriptionError
from services.highlights import InvalidHighlights

logger = logging.getLogger(__name__)
TERMINAL = {'completed', 'failed'}
STATES = {'queued', 'preparing', 'transcribing', 'selecting', 'rendering'} | TERMINAL


class JobInterrupted(RuntimeError):
    pass


def _valid(data, job_id):
    return (isinstance(data, dict) and data.get('job_id') == job_id
            and isinstance(data.get('status'), str) and data['status'] in STATES
            and type(data.get('progress')) is int and 0 <= data['progress'] <= 100
            and type(data.get('created_at')) in (int, float)
            and 0 <= data['created_at'] < 1e12 and math.isfinite(data['created_at']))


def _write(directory, data):
    path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                         prefix='.status-', delete=False) as out:
            path = Path(out.name)
            json.dump(data, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(path, directory / 'job.json')
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


def _remove_media(directory, successful=False):
    names = ['audio.mp3'] + ['source'+suffix for suffix in storage.VIDEO_SUFFIXES]
    if not successful:
        names += [f'clip_{i}.mp4' for i in range(1, 4)]
    for name in names:
        (directory/name).unlink(missing_ok=True)


def _failure(directory, data, code, message):
    _remove_media(directory)
    data.update(status='failed', detail=message, error={'code':code, 'message':message},
                updated_at=time.time(), finished_at=time.time(), result=None)
    _write(directory, data)


class JobWorker:
    def __init__(self, processor):
        self.processor = processor
        self.root = storage.DATA_DIR
        self.queue = deque()
        self.condition = Condition()
        self.stopping = Event()
        self.thread = None
        self.owner = None
        self.max_queued = int(os.getenv('MAX_QUEUED_JOBS', '5'))
        if not 1 <= self.max_queued <= 20:
            raise ValueError('MAX_QUEUED_JOBS must be between 1 and 20')

    def start(self):
        self.root.mkdir(parents=True, exist_ok=True)
        import fcntl
        self.owner = os.open(self.root/'.worker.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._recover()
            self.thread = Thread(target=self._run, name='video-job-worker', daemon=False)
            self.thread.start()
        except BaseException as exc:
            os.close(self.owner)
            self.owner = None
            if isinstance(exc, BlockingIOError):
                raise RuntimeError('Another worker owns DATA_DIR. Run one backend process per storage directory.') from exc
            raise

    def _recover(self):
        queued = []
        for directory in self.root.iterdir():
            if not storage.JOB_ID.fullmatch(directory.name) or directory.is_symlink() or not directory.is_dir():
                continue
            if not (directory/'job.json').exists():
                continue  # Standalone uploads and legacy clips retain their existing policy.
            lease = storage._lease(directory)
            try:
                try:
                    data = storage.read_job_metadata(directory)
                    if not _valid(data, directory.name):
                        raise ValueError('Invalid status')
                except (OSError, ValueError):
                    data = {'job_id':directory.name, 'progress':0, 'created_at':time.time()}
                    _failure(directory, data, 'invalid_status', 'Stored job status was damaged. Upload the video again.')
                else:
                    if data['status'] == 'queued':
                        queued.append((data.get('created_at', 0), directory.name))
                    elif data['status'] not in TERMINAL:
                        _failure(directory, data, 'interrupted',
                                 'The server restarted during processing. Upload the video again.')
            finally:
                os.close(lease)
        self.queue.extend(job_id for _, job_id in sorted(queued))

    def submit(self, file):
        with self.condition:
            if self.stopping.is_set():
                raise HTTPException(503, 'The worker is shutting down. Try again shortly.')
            if len(self.queue) >= self.max_queued:
                raise HTTPException(429, 'The local job queue is full. Try again shortly.')
            job_id, source, lease = storage.save_upload(file)
            try:
                now = time.time()
                data = {'job_id':job_id, 'status':'queued', 'progress':0,
                        'detail':'Waiting for the local worker', 'created_at':now,
                        'updated_at':now, 'finished_at':None, 'result':None, 'error':None}
                _write(source.parent, data)
                self.queue.append(job_id)
                self.condition.notify()
            except BaseException:
                shutil.rmtree(source.parent, ignore_errors=True)
                raise
            finally:
                os.close(lease)
        return {'job_id':job_id, 'status':'queued', 'status_url':f'/pipeline/jobs/{job_id}'}

    def snapshot(self, job_id):
        if not storage.JOB_ID.fullmatch(job_id):
            raise HTTPException(404, 'Job not found')
        directory = self.root/job_id
        try:
            if directory.is_symlink():
                raise HTTPException(404, 'Job not found')
            data = storage.read_job_metadata(directory)
            if not _valid(data, job_id):
                raise HTTPException(404, 'Job not found')
            if data['status'] in TERMINAL and directory.stat().st_mtime <= time.time()-storage.retention_seconds():
                raise HTTPException(404, 'Job expired')
            if self.stopping.is_set() and data['status'] not in TERMINAL:
                raise HTTPException(503, 'The processing worker is unavailable. Restart the server to resume queued jobs.')
            # Atomic replacement lets progress reads proceed while processing holds a job lease.
            result = {k:data.get(k) for k in ('job_id','status','progress','detail','created_at',
                      'updated_at','finished_at','result','error')}
            result['status_url'] = f'/pipeline/jobs/{job_id}'
            result['expires_at'] = (directory.stat().st_mtime+storage.retention_seconds()
                                    if data['status'] in TERMINAL else None)
            return result
        except FileNotFoundError as exc:
            raise HTTPException(404, 'Job not found') from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(503, 'Stored job status could not be read') from exc

    def _run(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.queue or self.stopping.is_set())
                    if self.stopping.is_set():
                        return
                    job_id = self.queue.popleft()
                try:
                    self._process(job_id)
                except Exception as exc:
                    logger.error('Job worker storage failure: %s', type(exc).__name__)
                    self.stopping.set()
        finally:
            os.close(self.owner)
            self.owner = None

    def _process(self, job_id):
        directory = self.root/job_id
        lease = storage._lease(directory)
        try:
            try:
                data = storage.read_job_metadata(directory)
                if not _valid(data, job_id):
                    raise ValueError('Invalid status')
            except (OSError, ValueError):
                data = {'job_id':job_id, 'progress':0, 'created_at':time.time()}
                _failure(directory, data, 'invalid_status', 'Stored job status was damaged. Upload the video again.')
                return
            def update(status, progress, detail):
                if self.stopping.is_set():
                    raise JobInterrupted()
                data.update(status=status, progress=progress, detail=detail, updated_at=time.time())
                _write(directory, data)
            try:
                result = self.processor(job_id, directory, update)
                if self.stopping.is_set():
                    raise JobInterrupted()
                _remove_media(directory, successful=True)
                data.update(status='completed', progress=100, detail='Clips are ready',
                            result=result, finished_at=time.time(), updated_at=time.time())
                _write(directory, data)
            except JobInterrupted:
                _failure(directory, data, 'interrupted', 'The server stopped during processing. Upload the video again.')
            except LocalTranscriptionError:
                _failure(directory, data, 'transcription_failed',
                         'Local transcription failed. Check the model cache and configuration.')
            except InvalidHighlights:
                _failure(directory, data, 'no_highlights', 'No usable speech highlights were found.')
            except subprocess.TimeoutExpired:
                _failure(directory, data, 'processing_timeout', 'Video processing timed out.')
            except (subprocess.CalledProcessError, ValueError):
                _failure(directory, data, 'invalid_video', 'Check the video format, audio track, and duration.')
            except Exception as exc:
                logger.warning('Job failed: %s', type(exc).__name__)
                _failure(directory, data, 'processing_failed', 'Processing failed. Check server storage and try again.')
        finally:
            storage.finish_job(directory, lease)

    def stop(self):
        self.stopping.set()
        with self.condition:
            self.condition.notify_all()
        if self.thread is not None:
            # Wait for the bounded current stage, then fail the interrupted job.
            # Keep exclusive worker ownership until all processing actually stops.
            self.thread.join()
