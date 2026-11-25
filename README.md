# Local Video Clip Generator (MVP)

Turn speech in a video into up to three vertical clips. Transcription runs on your
CPU with [faster-whisper](https://github.com/SYSTRAN/faster-whisper); highlight
selection uses deterministic rules, and FFmpeg creates the clips.

**No OpenAI API key, API tokens, credits, or paid inference service is required.**
Your audio stays on the machine running the backend. The first run downloads a
public Whisper model from Hugging Face; subsequent runs reuse its local cache.

## Quickstart with Docker

```bash
git clone https://github.com/droidjedininja/ai-video-clip-generator.git
cd ai-video-clip-generator
docker compose up --build
```

Open http://localhost:3000. The backend is at http://localhost:8000. No `.env` is
required. Docker installs FFmpeg/ffprobe and CPU inference dependencies. A named
Docker volume preserves downloaded models across container restarts/rebuilds.

The default `tiny` model prioritizes modest CPU and memory use. Its first download
is roughly 75 MiB, plus tokenizer files; dependency/image downloads are larger.
Reserve at least 2 GiB free RAM for the default setup plus disk for models/videos.
Processing speed depends on hardware. Larger models consume more RAM and take
longer, so start with `tiny` and a short video with clear speech.

## Optional settings

Copy `.env.example` to `.env` only if you want to override defaults:

| Variable | Default | Purpose |
| --- | --- | --- |
| `WHISPER_MODEL` | `tiny` | `tiny`, `base`, or `small`; larger models may improve accuracy |
| `WHISPER_TIMEOUT_SECONDS` | `600` | Worker wall-clock timeout, including model loading/download; 1–3600 |
| `WHISPER_OFFLINE` | `0` | Set to `1` after the selected model is cached to prohibit model downloads |
| `WHISPER_CACHE_DIR` | `/models` in Docker | Native model directory; default `~/.cache/video-clip-whisper`. Docker uses its `/models` volume |

For example, `WHISPER_MODEL=base docker compose up --build` selects the base model.
Restart the backend after changing settings. Download each selected model once
before enabling offline mode. Runtime transcription uses CPU int8 and two CPU
threads, with one pipeline at a time per backend process. No GPU is required.

## How highlights are selected

Whisper supplies text and segment timestamps. The selector builds speech windows
up to 45 seconds long, ranks them by speech density and simple question/explanation
cues, and returns at most three nonoverlapping windows in chronological order.
Final timestamps are constrained to the video duration. It does not call an LLM
or infer visual excitement, emotion, virality, or the best camera framing. Results
need human review; tiny-model transcription errors and non-English cue words can
reduce selection quality. Silent videos return an error instead of invented clips.

## Limits and storage

- Containers: MP4, MOV, MKV, WebM, M4V and AVI, with an audio track.
- Maximum saved video size: 100 MiB; duration must be positive and at most one hour.
- Audio is extracted to mono MP3 locally. There is no hosted transcription size cap.
- UUID-based job directories isolate uploads and output clips; original filenames
  never become disk paths. Unsafe names and unsupported extensions are rejected.
- Busy requests receive HTTP 429. Processing runs outside the event loop, but this
  MVP has no durable queue, progress polling, or restart recovery.
- ffprobe, extraction and each clip render have 15, 120 and 180 second timeouts.
  Local transcription runs in a subprocess with the configurable timeout above.
  A one-hour upload may exceed that budget on slower CPUs.
- Source video and extracted audio are removed after success. Failed pipeline jobs
  are removed; completed clips and standalone uploads persist in `backend/jobs/`
  until you remove their job directories. Models remain in their separate cache.

## API and UI

- `POST /pipeline/`: multipart `file`; returns `status`, `job_id`, transcript,
  highlights, relative clip URLs, `highlight_method: "rule_based"` and
  `transcription_backend: "faster-whisper"`.
- `GET /pipeline/{job_id}/clips/clip_1.mp4`: download a generated clip. Only expected
  job IDs and clip filenames are accepted; source files are not served.
- `POST /upload/`: store an isolated upload and return `job_id` without processing.
  `/pipeline/` takes its own file upload.

Errors return non-2xx statuses and a `detail` message: 400 invalid upload, 413
saved-file size limit, 415 unsupported extension, 422 invalid video/no usable speech,
429 busy, 503 local model/configuration failure, and 504 processing timeout.
The UI restores controls after failures and provides players and download links.
`NEXT_PUBLIC_API_URL` overrides its default `http://localhost:8000` origin; set it
before building or starting Next.js.

## Native installation and tests

Python 3.11, Node 24, FFmpeg/ffprobe and an OpenMP runtime are used by the project.
On Debian/Ubuntu, install `ffmpeg libgomp1`, then:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r backend/requirements.txt
cd backend
python -m uvicorn main:app --host 127.0.0.1 --port 8000
# In a second terminal, from frontend/:
npm ci
npm run dev
```

From the repository root, in the Python environment:

```bash
python -m pip install pytest httpx
python -m pytest backend/tests -q
cd frontend
npm ci
npm run build
```

Automated tests mock the model and media subprocesses. They test upload boundaries,
job isolation, downloads, cleanup, deterministic timing rules, CPU/offline settings,
timeouts and responsiveness without network calls or model downloads. CI also
builds the frontend and backend Docker image. Real local inference is tested
separately with a short synthetic speech video.

## Migration from the OpenAI version

The OpenAI SDK and hosted transcription/highlight calls have been removed. Existing
`OPENAI_API_KEY` entries in local files are unused; you may remove them yourself.
An older exposed key was revoked; revocation does not erase historical Git, Docker,
or Pages copies. Do not reuse old credentials. The app cannot run on GitHub Pages:
it requires a backend with CPU, model storage, and FFmpeg.

## Deployment boundary

This remains a local, single-user MVP. Docker binds both ports to loopback and CORS
permits the local frontend origins. There is no user authentication or per-user
clip authorization; UUID URLs are not an authorization system. Before public
hosting, add authentication, private user storage, ingress body limits, quotas,
retention and durable workers. The saved-file limit applies after multipart parsing.
Local inference avoids API charges but still consumes electricity, CPU and storage.

Copyright © 2026 Freddie. All rights reserved.
