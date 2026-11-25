import { Download, LoaderCircle, ScanSearch } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api } from "./api";
import type { DemoInfo, Report as R, Session } from "./api";
import { Report } from "./Report";
import { LeakUpload } from "./Trace";
import { HlsVideo } from "./Watch";

const initials = (name: string) => name.split(" ").map((w) => w[0]).join("").slice(0, 2);

function Face({ name, tone = "plain" }: { name?: string; tone?: "plain" | "on" | "caught" | "unknown" }) {
  const look = { plain: "bg-canvas text-body", on: "bg-brand-700 text-white", caught: "bg-brand-700 text-white ring-4 ring-brand-100",
    unknown: "bg-canvas text-faint border border-dashed border-faint" }[tone];
  return <span aria-hidden className={`grid place-items-center size-9 shrink-0 rounded-full text-[13px] font-bold ${look}`}>{name ? initials(name) : "?"}</span>;
}

/** A numbered step; the guide points at its heading and `lead` (the first thing to use in it). */
function Step({ n, title, lead, children, tour }: { n: number; title: string; lead?: ReactNode; children?: ReactNode; tour: string }) {
  return (
    <section>
      <div data-tour={tour} className="scroll-mt-20">
        <h2 className="flex items-center gap-3 text-[19px] font-bold text-ink">
          <span className="grid place-items-center size-7 rounded-full bg-ink text-white text-[13px]">{n}</span>{title}
        </h2>
        {lead && <div className="mt-4">{lead}</div>}
      </div>
      {children && <div className="mt-4">{children}</div>}
    </section>
  );
}

export function Demo({ onConsole }: { onConsole: (tab: string) => void }) {
  const [info, setInfo] = useState<DemoInfo | null>(null);
  const [error, setError] = useState("");
  const [viewer, setViewer] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [result, setResult] = useState<{ report: R; what: string } | null>(null);
  const [revealed, setRevealed] = useState<Record<string, string>>({}); // sample key → viewer id it traced to
  const [opening, setOpening] = useState("");
  const resultRef = useRef<HTMLDivElement>(null);
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));

  useEffect(() => {
    let timer = 0;
    const load = () => api.demo().then((d) => {
      setInfo(d);
      setViewer((v) => v || d.viewers[0]?.id || "");
      if (d.preparing || d.title?.status !== "ready") timer = window.setTimeout(load, 10000);
    }).catch(fail);
    load();
    return () => clearTimeout(timer);
  }, []);

  const ready = info?.title?.status === "ready";
  useEffect(() => {
    if (!ready || !viewer || !info?.title) return;
    api.session(info.title.id, viewer).then(setSession).catch(fail);
  }, [ready, viewer, info?.title?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const names = useMemo(() => {
    const m: Record<string, string> = {};
    for (let i = 1; i <= 994; i++) m[`demo-a${String(i).padStart(4, "0")}`] = `Account ${i}`;
    for (const v of info?.viewers ?? []) m[v.id] = v.name;
    return m;
  }, [info?.viewers]);

  const show = (report: R, what: string) => {
    setResult({ report, what });
    requestAnimationFrame(() => resultRef.current?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" }));
  };
  const traceSample = (key: string, trace: string, label: string) => {
    setOpening(key);
    api.report(trace).then((r) => {
      if (r.suspects[0] && r.verdict !== "inconclusive") setRevealed((x) => ({ ...x, [key]: r.suspects[0].viewer }));
      show(r, label);
    }).catch(fail).finally(() => setOpening(""));
  };

  const caught = result && result.report.verdict === "identified" ? result.report.suspects[0]?.viewer : undefined;
  const current = info?.viewers.find((v) => v.id === viewer);
  const waitingFor = info ? 4 - info.samples.length : 0;

  return (
    <div className="space-y-12">
      <section data-tour="intro" className="max-w-[680px] scroll-mt-20">
        <h1 className="text-[32px] sm:text-[40px] font-bold text-ink tracking-[-0.02em] leading-[1.1]">Find out who leaked your video.</h1>
        <p className="mt-3 text-[16px] text-body leading-relaxed">
          Every viewer streams their own copy of each video, marked in ways nobody can see or hear. When a copy turns up on Telegram, re-recorded or cut down, LeakTrace tells you which account it came from.
        </p>
      </section>

      {error && <div role="alert" className="rounded-xl border border-bad-200 bg-bad-50 text-bad-700 p-4 text-[14px]">{error}</div>}

      <Step n={1} title="Watch a viewer's copy" tour="watch" lead={
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Viewer">
          {info?.viewers.map((v) => (
            <button key={v.id} type="button" role="radio" aria-checked={viewer === v.id} onClick={() => setViewer(v.id)}
              className={`flex items-center gap-2 rounded-full border pl-1 pr-3.5 py-1 text-[14px] font-semibold transition outline-none focus-visible:ring-4 focus-visible:ring-brand-100 ${viewer === v.id ? "border-brand-300 bg-brand-50 text-ink" : "border-line bg-white text-body hover:border-brand-300"}`}>
              <Face name={v.name} tone={caught === v.id ? "caught" : viewer === v.id ? "on" : "plain"} />
              {v.name}
              {caught === v.id && <span className="text-[12px] font-bold text-brand-700">leaked it</span>}
            </button>
          ))}
        </div>}>
        <div className="card overflow-hidden">
          {session ? <HlsVideo src={session.hls} className="w-full bg-black aspect-video" />
            : <div className="aspect-video bg-ink/90 grid place-items-center text-white/70 text-[14px]">
                {ready || !info ? <LoaderCircle className="animate-spin" /> : "The sample video is being prepared. This takes a few minutes the first time."}
              </div>}
          <div className="p-4 flex flex-wrap items-center justify-between gap-3">
            <p className="text-[14px] text-body">{current ? <><span className="font-semibold text-ink">{current.name}'s copy.</span> It looks and sounds like everyone else's.</> : " "}</p>
            {session && <a className="btn-ghost" href={session.download}><Download size={16} /> Download this copy</a>}
          </div>
        </div>
      </Step>

      <Step n={2} title="Pick a leak" tour="leaks" lead={
        <p className="-mt-1 text-[14.5px] text-muted max-w-[640px]">Each of these was made from one viewer's copy, two minutes of it, damaged the way real leaks are. Whose copy was it?</p>}>
        <div className="grid gap-4 sm:grid-cols-2">
          {info?.samples.map((s) => {
            const who = revealed[s.key] ? names[revealed[s.key]] : undefined;
            return (
              <article key={s.key} className="card overflow-hidden flex flex-col">
                <video src={s.video} controls playsInline preload="metadata" className="w-full bg-black aspect-video" />
                <div className="p-4 flex-1 flex flex-col">
                  <h3 className="font-bold text-ink text-[15.5px]">{s.label}</h3>
                  <p className="mt-1 text-[13.5px] text-muted flex-1">{s.blurb}</p>
                  <div className="mt-4 flex items-center justify-between gap-3">
                    <span className="flex items-center gap-2 text-[13.5px] text-body">
                      <Face name={who} tone={who ? "on" : "unknown"} />{who ? <span><span className="font-semibold text-ink">{who}</span>'s copy</span> : "Whose copy?"}
                    </span>
                    <button type="button" className="btn-primary" disabled={opening === s.key} onClick={() => traceSample(s.key, s.trace, s.label)}>
                      {opening === s.key ? <LoaderCircle size={16} className="animate-spin" /> : <ScanSearch size={16} />} Trace it
                    </button>
                  </div>
                </div>
              </article>
            );
          })}
          {info?.preparing && Array.from({ length: waitingFor }, (_, k) => (
            <div key={k} className="card grid place-items-center aspect-[4/3] p-6 text-center text-[14px] text-muted">
              <span><LoaderCircle size={20} className="animate-spin mx-auto mb-2 text-faint" />Making this leak and tracing it. A few minutes, the first time only.</span>
            </div>
          ))}
          {info && !info.preparing && info.samples.length === 0 && (
            <p className="text-[14px] text-muted">No sample leaks on this server yet. Download a copy above and try your own below.</p>
          )}
        </div>
      </Step>

      <Step n={3} title="See whose copy it was" tour="result" lead={
        <div ref={resultRef} className="scroll-mt-28">
          {result ? (
            <div className="space-y-3">
              <p className="text-[14px] text-muted">{result.what}, traced among {result.report.candidates.toLocaleString()} accounts that opened this video.</p>
              <Report r={result.report} names={names} compact />
            </div>
          ) : (
            <div className="card p-6 text-[14.5px] text-muted">Press <span className="font-semibold text-ink">Trace it</span> on a leak above. The answer shows up here.</div>
          )}
        </div>} />

      <section data-tour="own" className="space-y-4">
        <div>
          <h2 className="text-[19px] font-bold text-ink">Try it with your own leak</h2>
          <p className="mt-1 text-[14.5px] text-muted max-w-[640px]">Download a copy in step 1, then film it with your phone, crop it, or screen-record it, and drop the result here. Two minutes or more works best.</p>
        </div>
        <LeakUpload compact onAuth={() => undefined} onReport={(r) => show(r, "Your leak")} />
      </section>

      <section className="card p-6">
        <h2 className="text-[19px] font-bold text-ink">How it fits into a streaming service</h2>
        <ol className="mt-4 space-y-3 text-[14.5px] text-body list-decimal pl-5 marker:font-bold marker:text-ink">
          <li>Each video is packaged once into two marked versions, A and B, as ordinary HLS and DASH.</li>
          <li>Your CDN sends every viewer their own mix of A and B pieces, chosen at the edge from their signed link. Players, DRM and caching stay as they are.</li>
          <li>When a leak shows up, trace it here or through the API. An account is named only when the chance of a mistake is below one in a million.</li>
        </ol>
        <p className="mt-5 text-[14px] text-muted">
          Tested on 69 real leaks of a 4-minute video among 1,000 accounts: no innocent account named.{" "}
          <button type="button" className="font-semibold text-brand-700 hover:underline" onClick={() => onConsole("bench")}>See the results</button>
        </p>
      </section>
    </div>
  );
}
