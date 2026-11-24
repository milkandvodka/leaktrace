import { Film, KeyRound, PlayCircle, ScanSearch, ShieldCheck, Users } from "lucide-react";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { token } from "./api";
import { BenchView } from "./Bench";
import { Library } from "./Library";
import { StudentsView } from "./Students";
import { TraceView } from "./Trace";
import { Watch } from "./Watch";

const TABS = [
  { key: "trace", label: "Trace a leak", Icon: ScanSearch },
  { key: "lectures", label: "Lectures", Icon: Film },
  { key: "watch", label: "Watch as a student", Icon: PlayCircle },
  { key: "students", label: "Students", Icon: Users },
  { key: "bench", label: "Attack bench", Icon: ShieldCheck },
] as const;
type Tab = (typeof TABS)[number]["key"];

const fromHash = (): Tab => (TABS.find((t) => `#${t.key}` === location.hash)?.key ?? "trace");

function TokenGate({ onDone }: { onDone: () => void }) {
  const [value, setValue] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (value.trim()) { token.set(value.trim()); onDone(); }
  };
  return (
    <div className="min-h-full grid place-items-center p-6">
      <form onSubmit={submit} className="card w-full max-w-[400px] p-6 animate-rise">
        <div className="flex items-center gap-2 text-ink font-bold text-[17px]"><KeyRound size={18} className="text-brand-700" /> Admin token</div>
        <p className="mt-1.5 text-[13.5px] text-muted">The value of LEAKTRACE_ADMIN_TOKEN on the server. It stays in this browser.</p>
        <input className="field mt-4" type="password" autoFocus value={value} onChange={(e) => setValue(e.target.value)} aria-label="Admin token" />
        <button className="btn-primary mt-3 w-full" type="submit">Continue</button>
      </form>
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState<Tab>(fromHash);
  const [authed, setAuthed] = useState(() => Boolean(token.get()));
  useEffect(() => {
    const onHash = () => setTab(fromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  if (!authed) return <TokenGate onDone={() => setAuthed(true)} />;
  const signOut = () => { token.set(""); setAuthed(false); };
  const go = (t: Tab) => { history.replaceState(null, "", `#${t}`); setTab(t); };

  return (
    <div className="min-h-full flex flex-col">
      <header className="bg-white border-b border-line">
        <div className="max-w-[1100px] mx-auto px-4 sm:px-6 h-14 flex items-center gap-3">
          <img src="/favicon.svg" alt="" className="size-7" />
          <span className="font-bold text-ink text-[16px] tracking-[-0.01em]">LeakTrace</span>
          <span className="hidden md:inline text-[13px] text-muted border-l border-line pl-3">Every copy is unique. Every leak has an owner.</span>
        </div>
        <nav aria-label="Sections" className="max-w-[1100px] mx-auto px-4 sm:px-6 flex gap-1 overflow-x-auto">
          {TABS.map(({ key, label, Icon }) => (
            <button key={key} type="button" onClick={() => go(key)} aria-current={tab === key ? "page" : undefined}
              className={`flex items-center gap-2 whitespace-nowrap px-3 h-10 text-[14px] font-semibold border-b-2 transition outline-none focus-visible:bg-brand-50 ${tab === key ? "border-brand-700 text-ink" : "border-transparent text-muted hover:text-ink"}`}>
              <Icon size={16} /> {label}
            </button>
          ))}
        </nav>
      </header>
      <main className="flex-1 max-w-[1100px] w-full mx-auto px-4 sm:px-6 py-7">
        {tab === "trace" && <TraceView onAuth={signOut} />}
        {tab === "lectures" && <Library onAuth={signOut} />}
        {tab === "watch" && <Watch onAuth={signOut} />}
        {tab === "students" && <StudentsView onAuth={signOut} />}
        {tab === "bench" && <BenchView onAuth={signOut} />}
      </main>
    </div>
  );
}
