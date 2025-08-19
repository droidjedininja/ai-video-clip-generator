import openai

# TODO: set OPENAI_API_KEY in environment and use openai from there
# For simplicity, you can also hardcode it here during local dev.
# import os; openai.api_key = os.getenv("OPENAI_API_KEY")

openai.api_key = "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"

def transcribe_audio(file_path: str):
    with open(file_path, "rb") as f:
        # Whisper transcription
        transcript = openai.Audio.transcriptions.create(
            model="whisper-1",
            file=f
        )
    # API may return dict-like; adapt if needed
    return transcript.get("text") if isinstance(transcript, dict) else transcript
