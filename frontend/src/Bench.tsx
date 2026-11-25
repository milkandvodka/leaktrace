import { useEffect, useState } from "react";
import { Unauthorized, api } from "./api";
import type { Bench } from "./api";

const LABEL: Record<string, string> = {
  telegram: "Telegram re-compression", telegram5: "Forwarded through 5 groups", p240: "Shrunk to 240p", crop70: "Cropped to the middle 70%",
  letterbox_portrait: "Re-posted as a vertical video", speed125: "Sped up to 1.25x", screenrec: "Screen recording", camcord: "Phone filming the screen",
  nokia: "Old Nokia filming the screen", muted: "Sound removed", audio_only: "Audio only", collude_interleave: "Two viewers splice their copies",
  "collude_interleave+telegram": "Spliced, then Telegram", collude_average: "Two viewers average their copies",
};
const clip = (c: string) => (c === "full" ? "whole title" : `${Math.round(Number(c) / 60)} min clip`);

export function BenchView({ onAuth }: { onAuth: () => void }) {
  const [bench, setBench] = useState<Bench | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.bench().then(setBench).catch((e) => (e instanceof Unauthorized ? onAuth() : setError(e instanceof Error ? e.message : String(e))));
  }, [onAuth]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-[26px] font-bold text-ink">Attack bench</h1>
        <p className="mt-1 text-[14.5px] text-muted max-w-[680px]">Real viewers' copies put through what leaks go through, then traced among {bench ? bench.candidates.toLocaleString() : "many"} accounts. "Named" means identified with less than a one-in-a-million chance of being wrong.</p>
      </div>
      {error && <div className="rounded-xl border border-line bg-white p-4 text-[14px] text-muted">{error}</div>}
      {bench && (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[640px] text-[14px]">
            <thead><tr className="text-left text-muted text-[12.5px]"><th className="px-5 py-2.5 font-medium">What happened to the copy</th><th className="px-3 py-2.5 font-medium">Length</th><th className="px-3 py-2.5 font-medium">Right account on top</th><th className="px-3 py-2.5 font-medium">Named</th><th className="px-5 py-2.5 font-medium">Innocent named</th></tr></thead>
            <tbody>
              {bench.table.map((t) => (
                <tr key={`${t.attack}-${t.clip}`} className="border-t border-line">
                  <td className="px-5 py-2.5 text-ink">{LABEL[t.attack] ?? t.attack}</td>
                  <td className="px-3 py-2.5 text-muted">{clip(t.clip)}</td>
                  <td className="px-3 py-2.5 tabular-nums">{t.top_correct}/{t.n}</td>
                  <td className="px-3 py-2.5 tabular-nums">{t.identified}/{t.n}</td>
                  <td className={`px-5 py-2.5 tabular-nums ${t.false_accusations ? "text-bad-700 font-semibold" : "text-ok-700"}`}>{t.false_accusations}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="px-5 py-3 border-t border-line text-[12.5px] text-muted">Picture quality of a marked copy against the original: {bench.psnr_db} dB PSNR (above 40 dB looks identical).</p>
        </div>
      )}
    </div>
  );
}
