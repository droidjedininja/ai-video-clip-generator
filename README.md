# AI Video Clip Generator (MVP)

End-to-end pipeline: **Upload → Transcribe (Whisper) → Highlights (GPT) → Cut Clips (FFmpeg)**

## Quickstart

```bash
# 1) Clone and enter
git clone <your-repo-url>
cd ai-video-clip-generator

# 2) Set your OpenAI API key (recommended via env)
export OPENAI_API_KEY=sk-...

# 3) Run with Docker
docker-compose up --build
```

- Frontend: http://localhost:3000
- Backend:  http://localhost:8000

### API
`POST /pipeline/` with form-data `file=@yourvideo.mp4`  
Returns transcript, highlights JSON, and list of clip paths.

### Notes
- Requires FFmpeg (installed in backend Dockerfile).
- `services/whisper.py` currently sets `openai.api_key` directly — switch to env usage for production.
- This is an MVP scaffold. Improve by adding subtitle burn-in, better highlight parsing, storage (S3/Supabase), and auth/payments.

Copyright © 2026 Freddie. All rights reserved.
