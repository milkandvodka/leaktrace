import { ArrowLeft, CircleHelp, Film, KeyRound, LayoutGrid, PlayCircle, ScanSearch, ShieldCheck, Users } from "lucide-react";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api, token, tourSeen } from "./api";
import { BenchView } from "./Bench";
import { Demo } from "./Demo";
import { Library } from "./Library";
import { ViewersView } from "./Viewers";
import { TraceView } from "./Trace";
import { Tour } from "./Tour";
import type { TourStep } from "./Tour";
import { Watch } from "./Watch";

const TABS = [
  { key: "trace", label: "Trace a leak", short: "Trace", Icon: ScanSearch },
  { key: "titles", label: "Titles", short: "Titles", Icon: Film },
  { key: "watch", label: "Watch as a viewer", short: "Watch", Icon: PlayCircle },
  { key: "viewers", label: "Viewers", short: "Viewers", Icon: Users },
  { key: "bench", label: "Attack bench", short: "Bench", Icon: ShieldCheck },
] as const;
type Tab = (typeof TABS)[number]["key"];

const tabFromHash = (): Tab | null => TABS.find((t) => `#${t.key}` === location.hash)?.key ?? null;

const TOUR: TourStep[] = [
  { target: "intro", title: "A 30-second tour", text: "Here's how to see LeakTrace catch a leak, in three steps. Skip it any time." },
  { target: "watch", title: "Watch a viewer's copy", text: "Pick anyone. Their copy plays like normal. The marks hide in its timing, its sound and its picture." },
  { target: "leaks", title: "Real leaks", text: "Each of these was made from one viewer's copy: forwarded on Telegram, filmed with a phone, cropped, screen-recorded." },
  { target: "result", title: "Trace it", text: "Press Trace it on a leak. LeakTrace picks the account out of 1,000, and names it only when the chance of a mistake is below one in a million." },
  { target: "guide", title: "That's it", text: "Try your own leak at the bottom of the page. Open this guide again here. The full console is next to it." },
];

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
  const [tab, setTab] = useState<Tab | null>(tabFromHash);
  const [authed, setAuthed] = useState(() => Boolean(token.get()));
  const [demo, setDemo] = useState<boolean | null>(null); // a public demo needs no token
  const [touring, setTouring] = useState(false);
  useEffect(() => {
    api.health().then((h) => { setDemo(Boolean(h.demo)); if (h.demo && !tabFromHash() && !tourSeen.get()) setTouring(true); }).catch(() => setDemo(false));
  }, []);
  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  if (demo === null) return null;
  if (!authed && !demo) return <TokenGate onDone={() => setAuthed(true)} />;
  const signOut = () => { token.set(""); setAuthed(false); };
  const go = (t: Tab | null) => { history.replaceState(null, "", t ? `#${t}` : location.pathname); setTab(t); scrollTo(0, 0); };
  const view: Tab | "demo" = tab ?? (demo ? "demo" : "trace");
  const endTour = () => { setTouring(false); tourSeen.set(); };

  return (
    <div className="min-h-full flex flex-col">
      <header className="bg-white border-b border-line sticky top-0 z-40">
        <div className="max-w-[1100px] mx-auto px-4 sm:px-6 h-14 flex items-center gap-3">
          <button type="button" onClick={() => go(null)} className="flex items-center gap-3 rounded-lg outline-none focus-visible:ring-4 focus-visible:ring-brand-100">
            <img src="/favicon.svg" alt="" className="size-7" />
            <span className="font-bold text-ink text-[16px] tracking-[-0.01em]">LeakTrace</span>
          </button>
          <span className="hidden md:inline text-[13px] text-muted border-l border-line pl-3">Every copy is unique. Every leak has an owner.</span>
          {demo && (
            <div className="ml-auto flex items-center gap-2" data-tour="guide">
              {view === "demo" && (
                <button type="button" className="btn-ghost h-8 px-2.5" onClick={() => setTouring(true)} aria-label="Show the guide">
                  <CircleHelp size={16} /><span className="hidden sm:inline">Guide</span>
                </button>
              )}
              {view === "demo"
                ? <button type="button" className="btn-ghost h-8 px-2.5" onClick={() => go("trace")}><LayoutGrid size={16} /> Console</button>
                : <button type="button" className="btn-ghost h-8 px-2.5" onClick={() => go(null)}><ArrowLeft size={16} /> Demo</button>}
            </div>
          )}
        </div>
        {view !== "demo" && (
          <nav aria-label="Sections" className="max-w-[1100px] mx-auto px-1.5 sm:px-6 flex sm:gap-1 overflow-x-auto">
            {TABS.map(({ key, label, short, Icon }) => (
              <button key={key} type="button" onClick={() => go(key)} aria-current={view === key ? "page" : undefined}
                className={`flex items-center gap-2 whitespace-nowrap px-2.5 sm:px-3 h-10 text-[14px] font-semibold border-b-2 transition outline-none focus-visible:bg-brand-50 ${view === key ? "border-brand-700 text-ink" : "border-transparent text-muted hover:text-ink"}`}>
                <Icon size={16} className="hidden sm:block" /><span className="sm:hidden">{short}</span><span className="hidden sm:inline">{label}</span>
              </button>
            ))}
          </nav>
        )}
      </header>
      <main className="flex-1 max-w-[1100px] w-full mx-auto px-4 sm:px-6 py-7 sm:py-10">
        {view === "demo" && <Demo onConsole={(t) => go(t as Tab)} />}
        {view === "trace" && <TraceView onAuth={signOut} />}
        {view === "titles" && <Library onAuth={signOut} demo={Boolean(demo)} />}
        {view === "watch" && <Watch onAuth={signOut} />}
        {view === "viewers" && <ViewersView onAuth={signOut} />}
        {view === "bench" && <BenchView onAuth={signOut} />}
      </main>
      {touring && view === "demo" && <Tour steps={TOUR} onClose={endTour} />}
    </div>
  );
}
