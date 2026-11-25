import Hls from "hls.js";
import { Download, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Unauthorized, api } from "./api";
import type { Session, Title, Viewer } from "./api";

export function Watch({ onAuth }: { onAuth: () => void }) {
  const [titles, setTitles] = useState<Title[]>([]);
  const [viewers, setViewers] = useState<Viewer[]>([]);
  const [title, setTitle] = useState("");
  const [viewer, setViewer] = useState("");
  const [links, setLinks] = useState<Session | null>(null);
  const [format, setFormat] = useState<"hls" | "dash">("hls");
  const [error, setError] = useState("");
  const video = useRef<HTMLVideoElement>(null);
  const fail = (e: unknown) => (e instanceof Unauthorized ? onAuth() : setError(e instanceof Error ? e.message : String(e)));

  useEffect(() => {
    api.titles().then((l) => { const ready = l.filter((x) => x.status === "ready"); setTitles(ready); setTitle(ready[0]?.id ?? ""); }).catch(fail);
    api.viewers().then((s) => { setViewers(s); setViewer(s[0]?.id ?? ""); }).catch(fail);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = video.current;
    if (!el || !links) return;
    if (format === "dash") {
      let player: { reset: () => void } | null = null;
      let gone = false;
      import("dashjs").then(({ default: dashjs }) => { // loaded only when someone picks DASH
        if (gone) return;
        const p = dashjs.MediaPlayer().create();
        p.initialize(el, links.dash, false);
        player = p;
      }).catch(fail);
      return () => { gone = true; player?.reset(); };
    }
    if (Hls.isSupported()) {
      const hls = new Hls();
      hls.loadSource(links.hls);
      hls.attachMedia(el);
      return () => hls.destroy();
    }
    el.src = links.hls; // Safari plays HLS natively
  }, [links, format]); // eslint-disable-line react-hooks/exhaustive-deps

  const open = () => { setError(""); api.session(title, viewer).then(setLinks).catch(fail); };
  const name = viewers.find((s) => s.id === viewer)?.name;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-[26px] font-bold text-ink">Watch as a viewer</h1>
        <p className="mt-1 text-[14.5px] text-muted max-w-[640px]">What a viewer sees: the normal video. Underneath, this copy carries marks unique to them in its timing, its sound and its picture.</p>
      </div>
      <div className="card p-5 grid gap-3 md:grid-cols-[1fr_1fr_auto] md:items-end">
        <label className="text-[12.5px] text-muted">Title
          <select className="field mt-1" value={title} onChange={(e) => setTitle(e.target.value)}>
            {titles.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        </label>
        <label className="text-[12.5px] text-muted">Viewer
          <select className="field mt-1" value={viewer} onChange={(e) => setViewer(e.target.value)}>
            {viewers.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.id})</option>)}
          </select>
        </label>
        <button type="button" className="btn-primary h-10" disabled={!title || !viewer} onClick={open}><Play size={16} /> Open their copy</button>
      </div>
      {error && <div role="alert" className="rounded-xl border border-bad-200 bg-bad-50 text-bad-700 p-4 text-[14px]">{error}</div>}
      {links && (
        <div className="card overflow-hidden animate-rise">
          <video ref={video} controls playsInline className="w-full bg-black aspect-video" />
          <div className="p-4 flex flex-wrap items-center justify-between gap-3">
            <span className="text-[14px] text-body">This copy belongs to <span className="font-semibold text-ink">{name}</span>. Watching it is logged.</span>
            <a className="btn-ghost" href={links.download}><Download size={16} /> Download their copy</a>
          </div>
          <div className="px-4 pb-4 flex flex-wrap items-center gap-3 text-[12.5px] text-faint">
            <div role="radiogroup" aria-label="Streaming format" className="inline-flex rounded-lg border border-line p-0.5">
              {(["hls", "dash"] as const).map((f) => (
                <button key={f} type="button" role="radio" aria-checked={format === f} onClick={() => setFormat(f)}
                  className={`px-2.5 py-1 rounded-md text-[12.5px] font-semibold ${format === f ? "bg-brand-50 text-brand-700" : "text-muted"}`}>{f.toUpperCase()}</button>
              ))}
            </div>
            <span className="break-all">Same manifests for every viewer; only the token in the path differs: <a className="underline" href={links.hls}>HLS</a> · <a className="underline" href={links.dash}>DASH</a></span>
          </div>
        </div>
      )}
    </div>
  );
}
