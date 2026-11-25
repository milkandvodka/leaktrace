import { LoaderCircle, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Unauthorized, api, mmss, waitForJob } from "./api";
import type { Title } from "./api";

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
const STATUS: Record<string, string> = { ready: "bg-ok-50 text-ok-700", processing: "bg-warn-50 text-warn-700", failed: "bg-bad-50 text-bad-700" };

export function Library({ onAuth }: { onAuth: () => void }) {
  const [titles, setTitles] = useState<Title[]>([]);
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const fail = (e: unknown) => (e instanceof Unauthorized ? onAuth() : setError(e instanceof Error ? e.message : String(e)));
  const refresh = () => { api.titles().then(setTitles).catch(fail); };
  useEffect(refresh, []); // eslint-disable-line react-hooks/exhaustive-deps

  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (!file || !name.trim()) return;
    setBusy(true); setError(""); setProgress(0);
    try {
      const { job } = await api.addTitle(slug(name) || "title", name.trim(), file);
      refresh();
      const done = await waitForJob(job, (j) => setProgress(j.progress));
      if (done.status === "failed") throw new Error(done.error || "Packaging failed.");
      setName(""); setFile(null);
    } catch (err) { fail(err); } finally { setBusy(false); refresh(); }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-[26px] font-bold text-ink">Titles</h1>
        <p className="mt-1 text-[14.5px] text-muted max-w-[640px]">Each title is packaged once into two marked versions. Every viewer's stream and download is their own mix of the two, so no two copies are alike.</p>
      </div>
      <form onSubmit={add} className="card p-5 grid gap-3 md:grid-cols-[1fr_1fr_auto] md:items-end">
        <label className="text-[12.5px] text-muted">Name<input className="field mt-1" value={name} onChange={(e) => setName(e.target.value)} placeholder="Episode 3: The harbour" /></label>
        <label className="text-[12.5px] text-muted">Master video<input className="field mt-1 pt-[5px] text-muted file:mr-3 file:h-7 file:rounded-md file:border-0 file:bg-brand-50 file:px-3 file:text-[13px] file:font-semibold file:text-brand-700 hover:file:bg-brand-100" type="file" accept="video/*" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
        <button className="btn-primary h-10" type="submit" disabled={busy || !file || !name.trim()}>
          {busy ? <><LoaderCircle size={16} className="animate-spin" /> {Math.round(progress * 100)}%</> : <><Plus size={16} /> Add title</>}
        </button>
      </form>
      {error && <div role="alert" className="rounded-xl border border-bad-200 bg-bad-50 text-bad-700 p-4 text-[14px]">{error}</div>}
      <div className="card overflow-hidden">
        {titles.length === 0 ? <p className="p-5 text-[14px] text-muted">No titles yet. Add one above.</p> : (
          <table className="w-full text-[14px]">
            <thead><tr className="text-left text-muted text-[12.5px]"><th className="px-5 py-2.5 font-medium">Title</th><th className="px-3 py-2.5 font-medium">Length</th><th className="px-3 py-2.5 font-medium">Marked pieces</th><th className="px-5 py-2.5 font-medium">Status</th></tr></thead>
            <tbody>
              {titles.map((l) => (
                <tr key={l.id} className="border-t border-line">
                  <td className="px-5 py-3"><div className="text-ink font-medium">{l.name}</div><div className="text-[12px] text-faint">{l.id}</div></td>
                  <td className="px-3 py-3 tabular-nums">{l.meta ? mmss(l.meta.duration) : "-"}</td>
                  <td className="px-3 py-3 tabular-nums">{l.meta?.marked != null ? `${l.meta.marked} of ${l.meta.segments}` : "-"}</td>
                  <td className="px-5 py-3"><span className={`text-[12.5px] font-semibold rounded-full px-2.5 py-0.5 ${STATUS[l.status]}`}>{l.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
