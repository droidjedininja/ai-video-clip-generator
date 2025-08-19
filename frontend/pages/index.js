import { useState } from "react";

export default function Home() {
  const [file, setFile] = useState(null);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const runPipeline = async () => {
    if (!file) return alert("Choose a file first.");
    setLoading(true);
    const formData = new FormData();
    formData.append("file", file);

    const res = await fetch("http://localhost:8000/pipeline/", {
      method: "POST",
      body: formData,
    });
    const data = await res.json();
    setResult(data);
    setLoading(false);
  };

  return (
    <div className="flex flex-col items-center p-10 gap-4">
      <h1 className="text-3xl font-bold">AI Video Clip Generator</h1>
      <input type="file" onChange={(e) => setFile(e.target.files[0])} />
      <button onClick={runPipeline} className="px-4 py-2 bg-black text-white rounded">
        {loading ? "Processing..." : "Process Video"}
      </button>

      {result && (
        <div className="w-full max-w-2xl text-left mt-6">
          <h2 className="text-xl font-semibold mb-2">Result</h2>
          <pre className="text-sm bg-gray-100 p-3 rounded overflow-auto">
            {JSON.stringify(result, null, 2)}
          </pre>
          {Array.isArray(result?.clips) && (
            <ul className="list-disc ml-6 mt-3">
              {result.clips.map((c, idx) => (
                <li key={idx}>{c}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
