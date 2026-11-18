from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes import upload, pipeline

app = FastAPI(title="AI Video Clip Generator")

# CORS for local Next.js dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router)
app.include_router(pipeline.router)

@app.get("/")
def root():
    return {"message": "AI Video Clip Generator API running"}
