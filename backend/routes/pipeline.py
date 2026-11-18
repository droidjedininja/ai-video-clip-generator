import logging
import os
import shutil
import subprocess
from threading import BoundedSemaphore
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from openai import OpenAIError
from services.whisper import transcribe_audio
from services.gpt import detect_highlights, InvalidHighlights
from services.clipper import cut_clip, media_duration, extract_audio
from services.storage import save_upload, get_clip

router = APIRouter(prefix='/pipeline', tags=['pipeline'])
# Synchronous handlers run in FastAPI's thread pool; bound expensive work per process.
slots = BoundedSemaphore(2)
logger = logging.getLogger(__name__)


@router.post('/')
def process_video(file: UploadFile = File(...)):
    if not os.getenv('OPENAI_API_KEY'):
        raise HTTPException(503, 'Server is missing OPENAI_API_KEY')
    if not slots.acquire(blocking=False):
        raise HTTPException(429, 'Processing is busy. Try again shortly.')
    file_path = None
    succeeded = False
    try:
        job_id, file_path = save_upload(file)
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
                'highlights': highlights, 'clips': clips}
    except (OpenAIError, InvalidHighlights) as exc:
        # Avoid logging request data or provider exception messages that may contain secrets.
        logger.warning('AI processing failed: %s', type(exc).__name__)
        raise HTTPException(502, 'AI processing failed. Please try again.') from exc
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
            slots.release()


@router.get('/{job_id}/clips/{filename}')
def download_clip(job_id: str, filename: str):
    return FileResponse(get_clip(job_id, filename), media_type='video/mp4', filename=filename)
