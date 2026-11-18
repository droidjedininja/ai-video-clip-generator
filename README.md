# AI Video Clip Generator (MVP)

Upload a video, transcribe its audio with Whisper, select timestamped highlights
with GPT, and download vertical MP4 clips produced by FFmpeg.

## Local quickstart

```bash
git clone https://github.com/droidjedininja/ai-video-clip-generator.git
cd ai-video-clip-generator
cp .env.example .env
# Edit .env locally to set OPENAI_API_KEY. Never commit it.
docker compose up --build
```

Open http://localhost:3000. The backend is at http://localhost:8000.
Docker installs FFmpeg/ffprobe. Dependencies and the frontend lockfile are checked
in; Python 3.11 and Node 24 are used by CI.

**Credential rotation:** an earlier revision contained a hardcoded OpenAI key.
Revoke that key in the OpenAI dashboard and supply a new one through `.env`.
Removing it from the current source does not revoke it or remove it from Git
history, past Docker images, or previous Pages artifacts. Do not reuse the old key.
The obsolete Pages workflow has been removed: this application requires a backend
and cannot be deployed by publishing the repository as static files.

## Processing and limits

- Supported containers: MP4, MOV, MKV, WebM, M4V and AVI, with an audio track.
- Maximum saved video size: 100 MiB; duration must be positive and at most one hour.
- Audio is extracted to mono MP3 before transcription; a 24 MiB audio limit is
  checked before sending it to OpenAI. Timed Whisper segments drive highlight selection.
- One to three highlight ranges must have finite numeric times within the video's
  duration. Malformed or out-of-range model output is rejected before cutting.
- Each upload uses a generated job directory. Original filenames are validated
  but never used as disk paths. Jobs never overwrite other jobs' clips.
- Synchronous processing runs in FastAPI's thread pool with at most two pipelines
  per backend process. Busy requests receive HTTP 429. Processing may take minutes;
  this MVP does not have a durable background queue or restart recovery.
- ffprobe, audio extraction and each clip render have 15, 120 and 180 second
  subprocess timeouts. Media input protocols and demuxers are restricted.
- Source video and extracted audio are removed after successful processing. Failed
  pipeline jobs are cleaned up; completed clips and standalone uploads persist in
  `backend/jobs/` until you delete their job directories.

## API

- `POST /pipeline/`: multipart `file`; returns `status`, `job_id`, transcript text,
  validated highlights and relative clip download URLs.
- `GET /pipeline/{job_id}/clips/clip_1.mp4`: download a generated clip. Only the
  expected job ID and clip filenames are accepted; source files aren't served.
- `POST /upload/`: save an isolated upload and return `job_id`. This endpoint does
  not start processing; `/pipeline/` takes its own file upload.

Errors use non-2xx HTTP statuses and a `detail` message: 400 invalid upload, 413
saved-file size limit, 415 unsupported extension, 422 invalid/unprocessable video,
429 busy, 502 AI failure, 503 missing server key, and 504 media timeout.
The UI reports failures, re-enables its controls, and provides video players and
links for successful clips. `NEXT_PUBLIC_API_URL` overrides its default
`http://localhost:8000` API origin; set it before building or starting Next.js.

## Deployment boundary

This is a local, single-user MVP. Docker binds both ports to loopback, CORS permits
only the local frontend origins, and the pipeline has no user authentication.
Download URLs are unguessable job IDs, not an authenticated authorization system.
Do not expose these services publicly without authentication, per-user job access,
request-body limits at the ingress, quotas, storage cleanup and a durable worker
queue. The saved-file limit is applied after multipart parsing; enforce an ingress
body limit too for untrusted clients. OpenAI requests incur charges.

## Tests and builds

```bash
python3 -m pip install -r backend/requirements.txt pytest httpx
python3 -m pytest backend/tests -q
cd frontend
npm ci
npm run build
```

Backend tests use synthetic uploads and mocked OpenAI/FFmpeg calls. They cover
traversal, size rejection, isolation, downloads, cleanup, timing validation, SDK
response handling, concurrency and server responsiveness. They do not call paid
APIs or render video. CI also builds the frontend and backend Docker image.
A real OpenAI/video end-to-end run requires a rotated key and separate verification.

Copyright © 2026 Freddie. All rights reserved.
