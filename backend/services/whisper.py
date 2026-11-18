from openai import OpenAI


def transcribe_audio(file_path: str):
    """Client reads OPENAI_API_KEY; verbose output supplies real segment times."""
    with open(file_path, 'rb') as audio:
        transcript = OpenAI().audio.transcriptions.create(
            model='whisper-1', file=audio, response_format='verbose_json',
            timestamp_granularities=['segment'], timeout=120,
        )
    return {
        'text': transcript.text,
        'segments': [
            {'start': s.start, 'end': s.end, 'text': s.text}
            for s in (transcript.segments or [])
        ],
    }
