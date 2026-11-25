import logging
import os
import shutil
import subprocess
from threading import BoundedSemaphore
from fastapi import APIRouter, UploadFile, File, HTTPException
from services.whisper import transcribe_audio, LocalTranscriptionError
from services.highlights import detect_highlights, InvalidHighlights
from services.clipper import cut_clip, media_duration, extract_audio
from services.storage import save_upload, get_clip, finish_job, LeasedFileResponse

router = APIRouter(prefix='/pipeline', tags=['pipeline'])
# Local inference is CPU/memory intensive; allow one pipeline per backend process.
slots = BoundedSemaphore(1)
logger = logging.getLogger(__name__)


@router.post('/')
def process_video(file: UploadFile = File(...)):
    if not slots.acquire(blocking=False):
        raise HTTPException(429, 'Processing is busy. Try again shortly.')
    file_path = None
    lease = None
    succeeded = False
    try:
        job_id, file_path, lease = save_upload(file)
        duration = media_duration(file_path)
        audio_path = extract_audio(file_path)
        transcript = transcribe_audio(str(audio_path))
        if not transcript['segments']:
            raise ValueError('No timed speech was detected in the video')
        highlights = detect_highlights(transcript, duration)
        clips = []
        for i, h in enumerate(highlights, 1):
            name = f'clip_{i}.mp4'
            cut_clip(str(file_path), h['start'], h['end'] - h['start'], str(file_path.parent / name))
            clips.append(f'/pipeline/{job_id}/clips/{name}')
        succeeded = True
        return {'status': 'success', 'job_id': job_id, 'transcript': transcript['text'],
                'highlights': highlights, 'clips': clips,
                'highlight_method': 'rule_based', 'transcription_backend': 'faster-whisper'}
    except LocalTranscriptionError as exc:
        logger.warning('Local transcription failed: %s', type(exc).__name__)
        raise HTTPException(503, 'Local transcription failed. Check the model cache, network for first download, and server configuration.') from exc
    except InvalidHighlights as exc:
        raise HTTPException(422, 'No usable speech highlights were found in this video.') from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, 'Video processing timed out') from exc
    except (subprocess.CalledProcessError, ValueError) as exc:
        raise HTTPException(422, 'Video could not be processed. Check its format, audio track, and duration.') from exc
    finally:
        try:
            if file_path is not None:
                if not succeeded:
                    shutil.rmtree(file_path.parent, ignore_errors=True)
                else:
                    file_path.unlink(missing_ok=True)
                    (file_path.parent / 'audio.mp3').unlink(missing_ok=True)
        finally:
            try:
                if lease is not None:
                    if succeeded:
                        finish_job(file_path.parent, lease)
                    else:
                        os.close(lease)
            finally:
                slots.release()


@router.get('/{job_id}/clips/{filename}')
def download_clip(job_id: str, filename: str):
    path, lease = get_clip(job_id, filename)
    return LeasedFileResponse(path, lease, media_type='video/mp4', filename=filename)
