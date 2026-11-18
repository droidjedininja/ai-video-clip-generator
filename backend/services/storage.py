"""Shared upload boundary: original filenames never become storage paths."""
import os
import re
import shutil
from pathlib import Path
from uuid import uuid4
from fastapi import HTTPException, UploadFile

DATA_DIR = Path(os.getenv('DATA_DIR', 'jobs'))
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
VIDEO_SUFFIXES = {'.mp4', '.mov', '.mkv', '.webm', '.m4v', '.avi'}
JOB_ID = re.compile(r'[0-9a-f]{32}')
CLIP_NAME = re.compile(r'clip_[1-3]\.mp4')


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
    try:
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
        shutil.rmtree(job_dir)
        raise
    finally:
        file.file.close()
    return job_id, path


def get_clip(job_id: str, filename: str):
    if not JOB_ID.fullmatch(job_id) or not CLIP_NAME.fullmatch(filename):
        raise HTTPException(404, 'Clip not found')
    root = DATA_DIR.resolve()
    path = root / job_id / filename
    # Reject symlinks and any filesystem alias outside the expected job directory.
    if path.is_symlink() or path.parent.is_symlink() or path.resolve().parent != root / job_id or not path.is_file():
        raise HTTPException(404, 'Clip not found')
    return path
