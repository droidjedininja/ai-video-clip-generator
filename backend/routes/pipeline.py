from fastapi import APIRouter, UploadFile, File
import shutil, os, json
from services.whisper import transcribe_audio
from services.gpt import detect_highlights
from services.clipper import cut_clip

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

UPLOAD_DIR = "uploads"
CLIPS_DIR = "clips"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CLIPS_DIR, exist_ok=True)

@router.post("/")
async def process_video(file: UploadFile = File(...)):
    # Save uploaded file
    file_path = f"{UPLOAD_DIR}/{file.filename}"
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Step 1: Transcribe
    transcript = transcribe_audio(file_path)

    # Step 2: Detect highlights (expects JSON string)
    highlights_raw = detect_highlights(transcript)

    try:
        highlights = json.loads(highlights_raw)
    except Exception as e:
        return {"error": f"Could not parse highlights: {e}", "raw": highlights_raw}

    # Step 3: Cut clips
    clip_paths = []
    for i, h in enumerate(highlights):
        start, end = int(h.get("start", 0)), int(h.get("end", 0))
        duration = max(0, end - start)
        output_file = f"{CLIPS_DIR}/clip_{i+1}.mp4"
        cut_clip(file_path, start, duration, output_file)
        clip_paths.append(output_file)

    return {
        "status": "success",
        "transcript": transcript,
        "highlights": highlights,
        "clips": clip_paths
    }
