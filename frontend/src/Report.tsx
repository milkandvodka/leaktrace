import { Check, CircleHelp, Copy, SearchX, ShieldAlert, UserX } from "lucide-react";
import { useState } from "react";
import { mmss } from "./api";
import type { Report as R } from "./api";

const GROUPS: { title: string; carriers: [string, string][] }[] = [
  { title: "Timing", carriers: [["timing", "Tiny time shifts"]] },
  { title: "Sound", carriers: [["patchwork", "Spectrum pattern"], ["loudness", "Loudness swell"], ["tilt", "Tone balance"]] },
  { title: "Picture", carriers: [["brightness", "Brightness"], ["tint", "Tint"], ["saturation", "Saturation"], ["hue", "Hue"], ["contrast", "Contrast"]] },
];

function odds(p: number): string {
  if (p <= 0) return "less than 1 in a trillion";
  const n = 1 / p;
  if (n >= 1e12) return "less than 1 in a trillion";
  if (n >= 1e9) return `about 1 in ${Math.round(n / 1e9)} billion`;
  if (n >= 1e6) return `about 1 in ${Math.round(n / 1e6)} million`;
  if (n >= 1e3) return `about 1 in ${Math.round(n / 1e3)} thousand`;
  return `about 1 in ${Math.max(1, Math.round(n))}`;
}

function Strength({ z }: { z: number }) {
  const pct = Math.min(100, (z / 10) * 100);
  const word = z >= 4 ? "strong" : z >= 1.5 ? "present" : z > 0.3 ? "faint" : "not found";
  return (
    <div className="flex items-center gap-2 min-w-0">
      <div className="h-2 flex-1 rounded-full bg-canvas overflow-hidden"><div className="h-full bg-brand-500 rounded-full" style={{ width: `${pct}%` }} /></div>
      <span className="w-[68px] text-[12px] text-muted text-right">{word}</span>
    </div>
  );
}

export function Report({ r, names }: { r: R; names: Record<string, string> }) {
  const [copied, setCopied] = useState(false);
  const top = r.suspects[0];
  const who = (id: string) => (names[id] ? `${names[id]} (${id})` : id);
  const banner = {
    identified: { Icon: ShieldAlert, tone: "border-brand-300 bg-brand-50", title: `Leaked from ${top ? who(top.viewer) : "?"}`,
      text: `Chance this is the wrong account: ${top ? odds(top.p_family) : "-"}.` },
    narrowed: { Icon: CircleHelp, tone: "border-warn-200 bg-warn-50", title: `Most likely ${top ? who(top.viewer) : "?"}`,
      text: "The evidence points here but isn't strong enough to name one account. A longer piece of the video would settle it." },
    inconclusive: { Icon: CircleHelp, tone: "border-line bg-white", title: "Not enough to tell",
      text: "This clip is too short or too damaged to point at one account. Nobody is accused." },
    not_ours: { Icon: SearchX, tone: "border-line bg-white", title: "This isn't one of your titles",
      text: "No title in the library matches its sound or picture." },
    no_candidates: { Icon: UserX, tone: "border-line bg-white", title: "Nobody has opened this title",
      text: "There's no viewer to compare against yet." },
  }[r.verdict];

  return (
    <div className="space-y-5 animate-rise">
      <div className={`rounded-2xl border p-5 flex gap-4 ${banner.tone}`}>
        <banner.Icon size={28} className="shrink-0 text-brand-700 mt-0.5" />
        <div>
          <h2 className="text-[22px] font-bold text-ink leading-tight">{banner.title}</h2>
          <p className="mt-1 text-[14.5px] text-body">{banner.text}</p>
          {r.coalition.length > 1 && (
            <p className="mt-2 text-[14px] text-ink font-semibold">Several accounts were mixed together: {r.coalition.map(who).join(", ")}.</p>
          )}
        </div>
      </div>

      {r.title && r.location && (
        <div className="grid gap-5 lg:grid-cols-[1fr_1fr]">
          <div className="card p-5">
            <h3 className="font-semibold text-ink">What was leaked</h3>
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-[14px]">
              <dt className="text-muted">Title</dt><dd className="text-ink font-medium">{r.title.name}</dd>
              <dt className="text-muted">Part</dt><dd>{mmss(r.location.offset)} to {mmss(r.location.offset + r.location.speed * r.location.duration)}</dd>
              {Math.abs(r.location.speed - 1) > 0.02 && (<><dt className="text-muted">Speed</dt><dd>{r.location.speed.toFixed(2)}x</dd></>)}
              <dt className="text-muted">Found by</dt><dd>{r.location.via === "audio" ? "its sound" : "its picture (no sound)"}</dd>
              <dt className="text-muted">Marks read</dt><dd>{r.segments_used} pieces of about 2 seconds</dd>
              <dt className="text-muted">Compared with</dt><dd>{r.candidates.toLocaleString()} accounts that opened it</dd>
            </dl>
          </div>
          <div className="card p-5">
            <h3 className="font-semibold text-ink">Which hidden marks survived</h3>
            <div className="mt-3 space-y-3">
              {GROUPS.map((g) => (
                <div key={g.title}>
                  <div className="text-[12px] font-semibold text-muted">{g.title}</div>
                  {g.carriers.map(([key, label]) => (
                    <div key={key} className="grid grid-cols-[120px_1fr] items-center gap-3 mt-1">
                      <span className="text-[13.5px] text-body">{label}</span>
                      <Strength z={r.carriers[key]?.mean_z ?? 0} />
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {r.suspects.length > 0 && r.verdict !== "not_ours" && (
        <div className="card overflow-hidden">
          <h3 className="font-semibold text-ink px-5 pt-4">Closest accounts</h3>
          <table className="w-full mt-2 text-[14px]">
            <thead><tr className="text-left text-muted text-[12.5px]"><th className="px-5 py-2 font-medium">Account</th><th className="px-3 py-2 font-medium">Evidence score</th><th className="px-5 py-2 font-medium">Chance it's wrong</th></tr></thead>
            <tbody>
              {r.suspects.slice(0, 5).map((s, i) => (
                <tr key={s.viewer} className="border-t border-line">
                  <td className="px-5 py-2.5 text-ink font-medium">{i === 0 && s.verdict === "identified" && <Check size={14} className="inline mr-1.5 text-brand-700" />}{who(s.viewer)}</td>
                  <td className="px-3 py-2.5 tabular-nums">{s.z.toFixed(1)}</td>
                  <td className="px-5 py-2.5 text-muted">{s.p_family >= 0.5 ? "likely wrong" : odds(s.p_family)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {r.notice && (
        <div className="card p-5">
          <div className="flex items-center justify-between gap-3">
            <h3 className="font-semibold text-ink">Takedown notice draft</h3>
            <button type="button" className="btn-ghost h-8 text-[13px]"
              onClick={() => { navigator.clipboard.writeText(r.notice).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }); }}>
              {copied ? <><Check size={14} /> Copied</> : <><Copy size={14} /> Copy</>}
            </button>
          </div>
          <pre className="mt-3 whitespace-pre-wrap text-[13.5px] text-body font-sans bg-canvas rounded-lg p-3">{r.notice}</pre>
        </div>
      )}
    </div>
  );
}
