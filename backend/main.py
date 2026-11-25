import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes import upload, pipeline
from request_limits import RequestLimitMiddleware
from services.jobs import JobWorker
from services.storage import cleanup_expired_jobs, cleanup_interval, retention_seconds

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    retention_seconds()  # Fail early for invalid retention configuration.
    interval = cleanup_interval()

    async def sweep():
        try:
            await asyncio.to_thread(cleanup_expired_jobs)
        except OSError:
            logger.warning('Storage cleanup failed; will retry at the next interval')

    worker = JobWorker(pipeline.process_job)
    await asyncio.to_thread(worker.start)
    app.state.worker = worker
    task = None
    try:
        await sweep()

        async def cleanup_loop():
            while True:
                await asyncio.sleep(interval)
                await sweep()

        task = asyncio.create_task(cleanup_loop())
        yield
    finally:
        try:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        finally:
            await asyncio.to_thread(worker.stop)
            app.state.worker = None


app = FastAPI(title='AI Video Clip Generator', lifespan=lifespan)
app.add_middleware(RequestLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:3000', 'http://127.0.0.1:3000'],
    allow_methods=['*'], allow_headers=['*'],
)
app.include_router(upload.router)
app.include_router(pipeline.router)


@app.get('/')
def root():
    return {'message': 'AI Video Clip Generator API running'}
