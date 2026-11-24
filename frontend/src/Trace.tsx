import { FileVideo, LoaderCircle, UploadCloud } from "lucide-react";
import { useEffect, useState } from "react";
import type { DragEvent, FormEvent } from "react";
import { Unauthorized, api, waitForJob } from "./api";
import type { Report as R } from "./api";
import { Report } from "./Report";

const VERDICT: Record<string, string> = { identified: "Identified", narrowed: "Narrowed", inconclusive: "Not enough", not_ours: "Not ours", no_candidates: "No viewers" };

export function TraceView({ onAuth }: { onAuth: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [firstSeen, setFirstSeen] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const [report, setReport] = useState<R | null>(null);
  const [history, setHistory] = useState<{ id: string; verdict: string; created: string }[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  const [over, setOver] = useState(false);

  const fail = (e: unknown) => (e instanceof Unauthorized ? onAuth() : setError(e instanceof Error ? e.message : String(e)));
  const refresh = () => {
    api.traces().then(setHistory).catch(fail);
    api.students().then((s) => setNames(Object.fromEntries(s.map((x) => [x.id, x.name])))).catch(fail);
  };
  useEffect(refresh, []); // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (e: FormEvent) => {
    e.preventDefault();
    if (!file) return;
    setBusy(true); setError(""); setReport(null); setProgress(0);
    try {
      const { job } = await api.trace(file, firstSeen);
      const done = await waitForJob(job, (j) => setProgress(j.progress));
      if (done.status === "failed" || !done.result?.trace) throw new Error(done.error || "The trace failed.");
      setReport(await api.report(done.result.trace));
      refresh();
    } catch (err) { fail(err); } finally { setBusy(false); }
  };
  const drop = (e: DragEvent) => { e.preventDefault(); setOver(false); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); };

  return (
    <div className="space-y-7">
      <section>
        <h1 className="text-[30px] sm:text-[36px] font-bold text-ink tracking-tight leading-tight">Whose copy was this?</h1>
        <p className="mt-2 text-[15.5px] text-body max-w-[640px]">Drop in a leaked video, even a re-compressed Telegram forward, a screen recording or a phone filming a screen. LeakTrace finds the lecture, reads the hidden marks, and names the account only when the evidence is overwhelming.</p>
        <form onSubmit={run} className="mt-5 card p-5 grid gap-4 md:grid-cols-[1fr_auto] md:items-end">
          <label onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={drop}
            className={`flex items-center gap-3 rounded-xl border-2 border-dashed px-4 py-5 cursor-pointer transition ${over ? "border-brand-500 bg-brand-50" : "border-line hover:border-brand-300"}`}>
            {file ? <FileVideo size={22} className="text-brand-700 shrink-0" /> : <UploadCloud size={22} className="text-faint shrink-0" />}
            <span className="min-w-0">
              <span className="block text-[14.5px] font-semibold text-ink truncate">{file ? file.name : "Choose or drop the leaked file"}</span>
              <span className="block text-[12.5px] text-muted">{file ? `${(file.size / 1e6).toFixed(1)} MB` : "Video or audio, any format"}</span>
            </span>
            <input type="file" accept="video/*,audio/*,.3gp,.mkv" className="sr-only" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          <div className="flex flex-col sm:flex-row gap-3 md:flex-col">
            <label className="text-[12.5px] text-muted">First seen (optional)
              <input type="datetime-local" className="field mt-1" value={firstSeen} onChange={(e) => setFirstSeen(e.target.value)} />
            </label>
            <button type="submit" className="btn-primary h-10 px-5" disabled={!file || busy}>
              {busy ? <><LoaderCircle size={16} className="animate-spin" /> Tracing</> : "Trace it"}
            </button>
          </div>
        </form>
        {busy && (
          <div className="mt-4 card p-4" role="status">
            <div className="flex justify-between text-[13.5px]"><span className="text-body">{progress < 0.03 ? "Finding which lecture this is" : "Reading the hidden marks"}</span><span className="tabular-nums text-muted">{Math.round(progress * 100)}%</span></div>
            <div className="mt-2 h-2 rounded-full bg-canvas overflow-hidden"><div className="h-full bg-brand-500 transition-all" style={{ width: `${Math.max(3, progress * 100)}%` }} /></div>
          </div>
        )}
        {error && <div role="alert" className="mt-4 rounded-xl border border-bad-200 bg-bad-50 text-bad-700 p-4 text-[14px]">{error}</div>}
      </section>

      {report && <Report r={report} names={names} />}

      {history.length > 0 && (
        <section>
          <h2 className="font-semibold text-ink">Earlier traces</h2>
          <ul className="mt-2 card divide-y divide-line">
            {history.slice(0, 10).map((h) => (
              <li key={h.id}>
                <button type="button" className="w-full flex items-center justify-between gap-3 px-4 py-2.5 text-left hover:bg-canvas outline-none focus-visible:bg-brand-50"
                  onClick={() => api.report(h.id).then(setReport).catch(fail)}>
                  <span className="text-[14px] text-ink">{new Date(h.created).toLocaleString()}</span>
                  <span className={`text-[12.5px] font-semibold rounded-full px-2.5 py-0.5 ${h.verdict === "identified" ? "bg-brand-50 text-brand-700" : "bg-canvas text-muted"}`}>{VERDICT[h.verdict] ?? h.verdict}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
