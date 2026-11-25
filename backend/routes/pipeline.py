from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from services.whisper import transcribe_audio
from services.highlights import detect_highlights
from services.clipper import cut_clip, media_duration, extract_audio
from services.storage import get_clip, LeasedFileResponse, VIDEO_SUFFIXES

router = APIRouter(prefix='/pipeline', tags=['pipeline'])


def worker_for(request):
    worker = getattr(request.app.state, 'worker', None)
    if worker is None:
        raise HTTPException(503, 'The processing worker is not ready')
    return worker


@router.post('/', status_code=202)
def submit_video(request: Request, file: UploadFile = File(...)):
    return worker_for(request).submit(file)


@router.get('/jobs/{job_id}')
def job_status(job_id: str, request: Request):
    return worker_for(request).snapshot(job_id)


def process_job(job_id, directory, update):
    update('preparing', 5, 'Inspecting video and extracting audio')
    sources = [p for p in directory.glob('source.*') if p.suffix in VIDEO_SUFFIXES
               and not p.is_symlink() and p.is_file()]
    if len(sources) != 1:
        raise ValueError('Missing source video')
    source = sources[0]
    duration = media_duration(source)
    audio = extract_audio(source)
    update('transcribing', 15, 'Transcribing speech locally; the first run may download the model')
    transcript = transcribe_audio(str(audio))
    if not transcript['segments']:
        raise ValueError('No timed speech was detected')
    update('selecting', 65, 'Selecting speech highlights')
    highlights = detect_highlights(transcript, duration)
    clips = []
    for i, highlight in enumerate(highlights, 1):
        update('rendering', 75+int(20*(i-1)/len(highlights)), f'Rendering clip {i} of {len(highlights)}')
        name = f'clip_{i}.mp4'
        cut_clip(str(source), highlight['start'], highlight['end']-highlight['start'], str(directory/name))
        clips.append(f'/pipeline/{job_id}/clips/{name}')
    return {'transcript':transcript['text'], 'highlights':highlights, 'clips':clips,
            'highlight_method':'rule_based', 'transcription_backend':'faster-whisper'}


@router.get('/{job_id}/clips/{filename}')
def download_clip(job_id: str, filename: str):
    path, lease = get_clip(job_id, filename)
    return LeasedFileResponse(path, lease, media_type='video/mp4', filename=filename)
