import { useState } from "react";

const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

export default function Home() {
  const [file, setFile] = useState(null);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const runPipeline = async () => {
    if (!file || loading) return;
    setLoading(true);
    setError("");
    setResult(null);
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await fetch(`${API_URL}/pipeline/`, { method: "POST", body: formData });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.detail || "Processing failed. Please try again.");
      if (!data || data.status !== "success") throw new Error("The server returned an unexpected response.");
      setResult(data);
    } catch (err) {
      setError(err.message || "Could not reach the video server.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main style={{ maxWidth: 800, margin: "3rem auto", padding: "0 1rem", fontFamily: "system-ui" }}>
      <h1>AI Video Clip Generator</h1>
      <p>Choose a video up to 100 MiB and one hour long. Processing can take several minutes.</p>
      <label htmlFor="video">Video file</label>{" "}
      <input id="video" type="file" accept=".mp4,.mov,.mkv,.webm,.m4v,.avi" disabled={loading}
        onChange={(event) => setFile(event.target.files[0] || null)} />
      <p><button onClick={runPipeline} disabled={!file || loading}>
        {loading ? "Processing…" : "Process Video"}
      </button></p>
      {error && <p role="alert">{error}</p>}
      {result && <section>
        <h2>Your clips</h2>
        <ul>{result.clips.map((path, idx) => <li key={path}>
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
