"""Local CPU Whisper inference in a bounded subprocess; no hosted API client."""
import json
import os
import subprocess
import sys
from pathlib import Path

MODELS = {'tiny', 'base', 'small'}


class LocalTranscriptionError(RuntimeError):
    pass


def transcribe_audio(file_path: str):
    try:
        timeout = int(os.getenv('WHISPER_TIMEOUT_SECONDS', '600'))
        if not 1 <= timeout <= 3600:
            raise ValueError('Invalid transcription timeout')
    except ValueError as exc:
        raise LocalTranscriptionError('Invalid WHISPER_TIMEOUT_SECONDS') from exc
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'services.whisper', str(Path(file_path).resolve())],
            cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True,
            text=True, timeout=timeout,
        )
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or not isinstance(data.get('text'), str) or not isinstance(data.get('segments'), list):
            raise ValueError('Invalid local transcription output')
        return data
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        raise LocalTranscriptionError('Could not run local Whisper') from exc
    # TimeoutExpired is handled by the pipeline as HTTP 504 and kills the worker.


def run_local(file_path):
    from faster_whisper import WhisperModel
    model_name = os.getenv('WHISPER_MODEL', 'tiny')
    if model_name not in MODELS:
        raise ValueError('WHISPER_MODEL must be tiny, base, or small')
    cache = os.getenv('WHISPER_CACHE_DIR', str(Path.home()/'.cache'/'video-clip-whisper'))
    model = WhisperModel(model_name, device='cpu', compute_type='int8', cpu_threads=2,
                         num_workers=1, download_root=cache,
                         local_files_only=os.getenv('WHISPER_OFFLINE', '0') == '1')
    segments, _ = model.transcribe(file_path, beam_size=1, vad_filter=True,
                                   condition_on_previous_text=False)
    timed = [{'start': s.start, 'end': s.end, 'text': s.text.strip()}
             for s in segments if s.text.strip() and s.no_speech_prob < .6]
    return {'text': ' '.join(s['text'] for s in timed), 'segments': timed}


if __name__ == '__main__':
    try:
        print(json.dumps(run_local(sys.argv[1])))
    except Exception as exc:
        print(f'Local Whisper failed: {type(exc).__name__}', file=sys.stderr)
        sys.exit(1)
