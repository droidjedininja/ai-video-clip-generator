import json
import math
import subprocess
from pathlib import Path


def media_duration(input_file):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
         '-format_whitelist', 'mov,matroska,webm,avi', '-show_entries', 'format=duration', '-of', 'json', str(input_file)],
        check=True, capture_output=True, text=True, timeout=15,
    )
    try:
        duration = float(json.loads(result.stdout)['format']['duration'])
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError('Cannot determine video duration') from exc
    if not math.isfinite(duration) or not 0 < duration <= 3600:
        raise ValueError('Video duration must be between zero and 3600 seconds')
    return duration


def extract_audio(input_file):
    output = Path(input_file).parent / 'audio.mp3'
    subprocess.run(
        ['ffmpeg', '-nostdin', '-v', 'error', '-n', '-protocol_whitelist', 'file,pipe',
         '-format_whitelist', 'mov,matroska,webm,avi', '-i', str(input_file), '-vn',
         '-ac', '1', '-ar', '16000', '-b:a', '48k', str(output)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120,
    )
    if output.stat().st_size > 24 * 1024 * 1024:
        raise ValueError('Extracted audio exceeds transcription size limit')
    return output


def cut_clip(input_file, start, duration, output_file):
    cmd = [
        'ffmpeg', '-nostdin', '-v', 'error', '-n', '-ss', str(start),
        '-protocol_whitelist', 'file,pipe', '-format_whitelist', 'mov,matroska,webm,avi', '-i', str(input_file),
        '-t', str(duration),
        '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2',
        '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '23', '-c:a', 'aac', str(output_file),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=180)
    return output_file
