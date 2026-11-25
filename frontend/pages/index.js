import { useEffect, useRef, useState } from "react";

const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
const STORAGE_KEY = `video-clip-job:${API_URL}`;
const JOB_ID = /^[0-9a-f]{32}$/;
const TERMINAL = new Set(["completed", "failed", "expired"]);
const LABELS = { queued: "Queued", preparing: "Preparing video", transcribing: "Transcribing speech",
  selecting: "Selecting highlights", rendering: "Rendering clips", completed: "Completed", failed: "Failed", expired: "Unavailable" };

export default function Home() {
  const [file, setFile] = useState(null);
  const [jobId, setJobId] = useState("");
  const [job, setJob] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [connection, setConnection] = useState("");
  const [restoreWarning, setRestoreWarning] = useState("");
  const uploadController = useRef(null);
  const busy = uploading || Boolean(jobId && !TERMINAL.has(job?.status));
  const result = job?.status === "completed" ? job.result : null;

  useEffect(() => {
    const linked = new URLSearchParams(window.location.search).get("job");
    if (linked && JOB_ID.test(linked)) {
      setJobId(linked);
      return;
    }
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored && JOB_ID.test(stored)) setJobId(stored);
    } catch {
      setRestoreWarning("Automatic restore is unavailable. Save the job link after uploading.");
    }
  }, []);

  useEffect(() => () => uploadController.current?.abort(), []);

  useEffect(() => {
    if (!jobId) return;
    try { localStorage.setItem(STORAGE_KEY, jobId); }
    catch { setRestoreWarning("Automatic restore is unavailable. Save the job link below."); }
    const url = new URL(window.location.href);
    url.searchParams.set("job", jobId);
    window.history.replaceState(null, "", url);
    let closed = false;
    let timer;
    let controller;
    let failures = 0;
    const poll = async () => {
      controller = new AbortController();
      try {
        const response = await fetch(`${API_URL}/pipeline/jobs/${jobId}`, { signal: controller.signal, cache: "no-store" });
        const data = await response.json().catch(() => null);
        if (closed) return;
        if (response.status === 404) {
          setJob({ status: "expired", progress: 0, detail: "This job is no longer available." });
          setError("This job expired or is unavailable. Upload a video to start a new job.");
          setConnection("");
          try { localStorage.removeItem(STORAGE_KEY); } catch {}
          return;
        }
        if (!response.ok || !data || !LABELS[data.status]) throw new Error("Job status is temporarily unavailable.");
        failures = 0;
        setJob(data);
        setConnection("");
        setError(data.status === "failed" ? data.error?.message || "Processing failed. Upload the video again." : "");
        if (!TERMINAL.has(data.status)) timer = setTimeout(poll, 1000);
      } catch (err) {
        if (closed || err.name === "AbortError") return;
        setConnection("Cannot reach job status. Retrying automatically; your job stays on the server.");
        timer = setTimeout(poll, Math.min(10000, 1000 * 2 ** Math.min(++failures, 3)));
      }
    };
    poll();
    return () => { closed = true; clearTimeout(timer); controller?.abort(); };
  }, [jobId]);

  const runPipeline = async () => {
    if (!file || busy) return;
    setUploading(true);
    setError("");
    setConnection("");
    setJob(null);
    setJobId("");
    const data = new FormData();
    data.append("file", file);
    uploadController.current = new AbortController();
    try {
      const response = await fetch(`${API_URL}/pipeline/`, { method: "POST", body: data, signal: uploadController.current.signal });
      const submitted = await response.json().catch(() => null);
      if (!response.ok) throw new Error(submitted?.detail || "Upload failed. Please try again.");
      if (!submitted || !JOB_ID.test(submitted.job_id)) throw new Error("The server did not return a valid job ID.");
      setJob({ status: "queued", progress: 0, detail: "Waiting for the local worker" });
      setJobId(submitted.job_id);
    } catch (err) {
      if (err.name !== "AbortError") setError(err.message || "Could not reach the video server.");
    } finally {
      setUploading(false);
    }
  };

  return (
    <main style={{ maxWidth: 800, margin: "3rem auto", padding: "0 1rem", fontFamily: "system-ui", lineHeight: 1.5 }}>
      <h1>AI Video Clip Generator</h1>
      <p>Local Whisper transcription and rule-based highlights. No API key or credits required.</p>
      <p>Choose a video up to 100 MiB and one hour long. Keep this page open until the upload finishes.</p>
      <label htmlFor="video">Video file</label>{" "}
      <input id="video" type="file" accept=".mp4,.mov,.mkv,.webm,.m4v,.avi" disabled={busy}
        onChange={(event) => setFile(event.target.files[0] || null)} />
      <p><button onClick={runPipeline} disabled={!file || busy}>
        {uploading ? "Uploading…" : busy ? "Processing…" : "Process Video"}
      </button></p>
      {uploading && <p role="status">Uploading your video. Processing starts after the server confirms the job.</p>}
      {jobId && <section aria-label="Job progress" style={{ padding: "1rem", background: "#f1f5f9", borderRadius: 8 }}>
        <h2 style={{ marginTop: 0 }}>{LABELS[job?.status] || "Restoring job"}</h2>
        <progress value={job?.progress || 0} max="100" style={{ width: "100%" }} aria-label="Stage-based processing progress" />
        <p aria-live="polite">{job?.detail || "Reading saved job status…"}</p>
        <p><small>Progress reflects processing stages, not an estimated finish time.</small></p>
        <p>Job: <code style={{ overflowWrap: "anywhere" }}>{jobId}</code> · <a href={`?job=${jobId}`}>Save this job link</a></p>
        {busy && !uploading && <p>You can close this page and return to this link. Keep the local server running.</p>}
      </section>}
      {connection && <p role="status">{connection}</p>}
      {error && <p role="alert">{error}</p>}
      {restoreWarning && <p>{restoreWarning}</p>}
      {result && <section>
        <h2>Your clips</h2>
        <p>{job.expires_at ? `Available until ${new Date(job.expires_at * 1000).toLocaleString()}. Save clips you want to keep.` : "Stored clips expire automatically."}</p>
        <ul>{(result.clips || []).map((path, idx) => <li key={path}>
          <video controls preload="metadata" src={`${API_URL}${path}`} style={{ maxWidth: "100%", maxHeight: 480 }} />
          <p><a href={`${API_URL}${path}`}>Download clip {idx + 1}</a></p>
        </li>)}</ul>
        <details><summary>Transcript and highlights</summary>
          <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify({ transcript: result.transcript, highlights: result.highlights }, null, 2)}</pre>
        </details>
      </section>}
    </main>
  );
}
