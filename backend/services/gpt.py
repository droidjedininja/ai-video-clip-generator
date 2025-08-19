import openai

def detect_highlights(transcript: str):
    prompt = f"""
    You are an editor for viral short-form video.
    Given the transcript below, return a JSON array with the TOP 3 engaging segments.
    Each item must be of the form: {{"start": <int_seconds>, "end": <int_seconds>, "reason": "<why this is engaging>"}}
    Only output valid JSON (no extra commentary).
    Transcript:
    {transcript}
    """
    resp = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.choices[0].message["content"]
