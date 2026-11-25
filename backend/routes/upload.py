from fastapi import APIRouter, UploadFile, File
from services.storage import save_upload, finish_job

router = APIRouter(prefix='/upload', tags=['upload'])


@router.post('/')
def upload_video(file: UploadFile = File(...)):
    job_id, path, lease = save_upload(file)
    finish_job(path.parent, lease)
    return {'status': 'success', 'job_id': job_id}
