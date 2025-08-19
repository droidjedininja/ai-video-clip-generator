from fastapi import APIRouter, UploadFile, File
import shutil, os

router = APIRouter(prefix="/upload", tags=["upload"])

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.post("/")
async def upload_video(file: UploadFile = File(...)):
    file_path = f"{UPLOAD_DIR}/{file.filename}"
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"status": "success", "path": file_path}
