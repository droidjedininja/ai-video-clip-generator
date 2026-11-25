"""Shared upload boundary: original filenames never become storage paths."""
import os
import fcntl
import math
import time
import logging
import re
import shutil
from pathlib import Path
from uuid import uuid4
from fastapi.responses import FileResponse
from fastapi import HTTPException, UploadFile

DATA_DIR = Path(os.getenv('DATA_DIR', 'jobs'))
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
VIDEO_SUFFIXES = {'.mp4', '.mov', '.mkv', '.webm', '.m4v', '.avi'}
JOB_ID = re.compile(r'[0-9a-f]{32}')
CLIP_NAME = re.compile(r'clip_[1-3]\.mp4')


logger = logging.getLogger(__name__)


def retention_seconds():
    hours = float(os.getenv('JOB_RETENTION_HOURS', '24'))
    if not math.isfinite(hours) or not 0 < hours <= 8760:
        raise ValueError('JOB_RETENTION_HOURS must be positive, finite, and at most 8760')
    return hours * 3600


def cleanup_interval():
    seconds = int(os.getenv('JOB_CLEANUP_INTERVAL_SECONDS', '300'))
    if not 1 <= seconds <= 86400:
        raise ValueError('JOB_CLEANUP_INTERVAL_SECONDS must be between 1 and 86400')
    return seconds


def _lease(directory, shared=False, nonblocking=False):
    fd = os.open(directory / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        fcntl.flock(fd, mode | (fcntl.LOCK_NB if nonblocking else 0))
        return fd
    except BaseException:
        os.close(fd)
        raise


def finish_job(directory, lease):
    """Retention starts after upload/processing finishes, not when it starts."""
    try:
        os.utime(directory, None)
    finally:
        os.close(lease)


def cleanup_expired_jobs(now=None):
    now = time.time() if now is None else now
    cutoff = now - retention_seconds()
    removed = 0
    if not DATA_DIR.is_dir():
        return removed
    for directory in DATA_DIR.iterdir():
        if not JOB_ID.fullmatch(directory.name) or directory.is_symlink() or not directory.is_dir():
            continue
        lease = None
        try:
            age = directory.stat().st_mtime
            if age > cutoff:
                continue
            had_lock = (directory / '.lock').exists()
            lease = _lease(directory, nonblocking=True)
            # Existing locks don't change directory timestamps. Legacy jobs may
            # need a new lock; use their age from before creating that lock file.
            if had_lock and directory.stat().st_mtime > cutoff:
                continue
            shutil.rmtree(directory)
            removed += 1
        except BlockingIOError:
            pass  # Active processing or download in this or another process.
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning('Could not expire job %s', directory.name)
        finally:
            if lease is not None:
                os.close(lease)
    return removed


class LeasedFileResponse(FileResponse):
    def __init__(self, path, lease, **kwargs):
        super().__init__(path, **kwargs)
        self.lease = lease

    async def __call__(self, scope, receive, send):
        try:
            # Keep ownership of streaming until complete; don't offload to a
            # server path-send extension that can outlive this response call.
            scope = dict(scope)
            scope['extensions'] = {k: v for k, v in scope.get('extensions', {}).items()
                                   if k != 'http.response.pathsend'}
            await super().__call__(scope, receive, send)
        finally:
            os.close(self.lease)


def save_upload(file: UploadFile):
    name = file.filename or ''
    if not name or any(c in name for c in ('/', '\\', ':', '\x00')) or any(ord(c) < 32 for c in name):
        raise HTTPException(400, 'Invalid upload filename')
    suffix = Path(name).suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        raise HTTPException(415, 'Upload a supported video: MP4, MOV, MKV, WebM, M4V, or AVI')
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid4().hex
    job_dir = DATA_DIR / job_id
    job_dir.mkdir(mode=0o700)
    path = job_dir / ('source' + suffix)
    lease = None
    try:
        lease = _lease(job_dir)
        total = 0
        with path.open('xb') as out:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, 'Video exceeds the 100 MiB upload limit')
                out.write(chunk)
        if not total:
            raise HTTPException(400, 'Uploaded video is empty')
    except BaseException:
        try:
            shutil.rmtree(job_dir)
        finally:
            if lease is not None:
                os.close(lease)
        raise
    finally:
        file.file.close()
    return job_id, path, lease


def get_clip(job_id: str, filename: str):
    if not JOB_ID.fullmatch(job_id) or not CLIP_NAME.fullmatch(filename):
        raise HTTPException(404, 'Clip not found')
    root = DATA_DIR.resolve()
    path = root / job_id / filename
    lease = None
    try:
        if path.parent.is_symlink() or not path.parent.is_dir():
            raise HTTPException(404, 'Clip not found')
        if path.parent.stat().st_mtime <= time.time() - retention_seconds():
            raise HTTPException(404, 'Clip expired')
        lease = _lease(path.parent, shared=True)
        if path.parent.stat().st_mtime <= time.time() - retention_seconds():
            raise HTTPException(404, 'Clip expired')
        if path.is_symlink() or path.resolve().parent != root / job_id or not path.is_file():
            raise HTTPException(404, 'Clip not found')
        return path, lease
    except OSError as exc:
        if lease is not None:
            os.close(lease)
        raise HTTPException(404, 'Clip not found') from exc
    except BaseException:
        if lease is not None:
            os.close(lease)
        raise
