import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Unauthorized, api } from "./api";
import type { Viewer } from "./api";

export function ViewersView({ onAuth }: { onAuth: () => void }) {
  const [viewers, setViewers] = useState<Viewer[]>([]);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const fail = (e: unknown) => (e instanceof Unauthorized ? onAuth() : setError(e instanceof Error ? e.message : String(e)));
  const refresh = () => { api.viewers().then(setViewers).catch(fail); };
  useEffect(refresh, []); // eslint-disable-line react-hooks/exhaustive-deps

  const add = (e: FormEvent) => {
    e.preventDefault();
    setError("");
    const rows = text.split("\n").map((l) => l.split(",").map((x) => x.trim())).filter((r) => r[0]);
    if (rows.some((r) => !r[1])) return setError("Each line needs an id and a name, separated by a comma.");
    api.addViewers(rows.map(([id, name]) => ({ id, name }))).then(() => { setText(""); refresh(); }).catch(fail);
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-[26px] font-bold text-ink">Viewers</h1>
        <p className="mt-1 text-[14.5px] text-muted">Accounts that can be issued copies. In production these come from the platform's own user list.</p>
      </div>
      <form onSubmit={add} className="card p-5">
        <label className="text-[12.5px] text-muted">One per line: id, name
          <textarea className="field mt-1 h-24 py-2" value={text} onChange={(e) => setText(e.target.value)} placeholder={"s1001, Asha Rao\ns1002, Ravi Kumar"} />
        </label>
        <button className="btn-primary mt-3" type="submit" disabled={!text.trim()}><Plus size={16} /> Add viewers</button>
      </form>
      {error && <div role="alert" className="rounded-xl border border-bad-200 bg-bad-50 text-bad-700 p-4 text-[14px]">{error}</div>}
      <div className="card overflow-hidden">
        {viewers.length === 0 ? <p className="p-5 text-[14px] text-muted">No viewers yet.</p> : (
          <table className="w-full text-[14px]">
            <thead><tr className="text-left text-muted text-[12.5px]"><th className="px-5 py-2.5 font-medium">Name</th><th className="px-5 py-2.5 font-medium">Account id</th></tr></thead>
            <tbody>{viewers.slice(0, 500).map((s) => <tr key={s.id} className="border-t border-line"><td className="px-5 py-2.5 text-ink">{s.name}</td><td className="px-5 py-2.5 text-muted">{s.id}</td></tr>)}</tbody>
          </table>
        )}
      </div>
    </div>
  );
}
