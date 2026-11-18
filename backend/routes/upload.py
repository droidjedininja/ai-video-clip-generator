from fastapi import APIRouter, UploadFile, File
from services.storage import save_upload

router = APIRouter(prefix='/upload', tags=['upload'])


@router.post('/')
def upload_video(file: UploadFile = File(...)):
    job_id, _ = save_upload(file)
    return {'status': 'success', 'job_id': job_id}
