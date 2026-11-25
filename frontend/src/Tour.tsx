import { X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

export interface TourStep { target: string; title: string; text: string }

const GAP = 12, EDGE = 16, PAD = 8;

/** A few boxes that point at parts of the page, one at a time, over a dimmed backdrop. */
export function Tour({ steps, onClose }: { steps: TourStep[]; onClose: () => void }) {
  const [i, setI] = useState(0);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const next = useRef<HTMLButtonElement>(null);
  const step = steps[i];
  const last = i === steps.length - 1;

  useEffect(() => {
    const el = document.querySelector<HTMLElement>(`[data-tour="${step.target}"]`);
    if (!el) return;
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ block: el.offsetHeight > innerHeight * 0.6 ? "start" : "center", behavior: reduce ? "auto" : "smooth" });
    let frame = 0;
    const measure = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(() => setRect(el.getBoundingClientRect())); };
    measure();
    window.addEventListener("scroll", measure, true);
    window.addEventListener("resize", measure);
    return () => { cancelAnimationFrame(frame); window.removeEventListener("scroll", measure, true); window.removeEventListener("resize", measure); };
  }, [step.target]);

  useLayoutEffect(() => { // below the target if it fits, else above, else pinned to the bottom of the screen
    if (!rect || !box.current) return;
    const h = box.current.offsetHeight, w = box.current.offsetWidth, vh = innerHeight, vw = innerWidth;
    const top = rect.bottom + PAD + GAP + h <= vh - EDGE ? rect.bottom + PAD + GAP
      : rect.top - PAD - GAP - h >= EDGE ? rect.top - PAD - GAP - h : vh - EDGE - h;
    const left = Math.min(Math.max(EDGE, rect.left), vw - EDGE - w);
    setPos({ top, left });
  }, [rect, i]);

  useEffect(() => { next.current?.focus({ preventScroll: true }); }, [i]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight" && !last) setI((x) => x + 1);
      if (e.key === "ArrowLeft" && i > 0) setI((x) => x - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [i, last, onClose]);

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-labelledby="tour-title">
      {rect ? (
        <div key="spot" aria-hidden className="fixed rounded-2xl ring-2 ring-white/80 transition-[top,left,width,height] duration-300 pointer-events-none"
          style={{ top: rect.top - PAD, left: rect.left - PAD, width: rect.width + 2 * PAD, height: rect.height + 2 * PAD,
            boxShadow: "0 0 0 9999px rgba(29, 20, 22, 0.55)" }} />
      ) : <div key="dim" aria-hidden className="fixed inset-0 bg-[rgba(29,20,22,0.55)]" />}
      <div ref={box} className="fixed w-[min(340px,calc(100vw-32px))] card shadow-xl p-4 transition-[top,left] duration-300"
        style={pos ? { top: pos.top, left: pos.left } : { top: -9999, left: EDGE }}>
        <div className="flex items-start justify-between gap-3">
          <h2 id="tour-title" className="font-bold text-ink text-[16px] leading-snug">{step.title}</h2>
          <button type="button" onClick={onClose} aria-label="Close the guide" className="-mr-1 -mt-1 p-1 rounded-md text-faint hover:text-ink outline-none focus-visible:ring-4 focus-visible:ring-brand-100"><X size={16} /></button>
        </div>
        <p className="mt-1.5 text-[14px] text-body leading-relaxed">{step.text}</p>
        <div className="mt-4 flex items-center gap-3">
          <div className="flex gap-1.5" aria-label={`Step ${i + 1} of ${steps.length}`}>
            {steps.map((s, k) => <span key={s.target} className={`size-1.5 rounded-full ${k === i ? "bg-brand-700" : "bg-line"}`} />)}
          </div>
          {!last && <button type="button" onClick={onClose} className="ml-auto text-[13.5px] font-semibold text-muted hover:text-ink outline-none focus-visible:underline">Skip</button>}
          <button ref={next} type="button" onClick={() => (last ? onClose() : setI(i + 1))} className={`btn-primary h-8 px-3.5 ${last ? "ml-auto" : ""}`}>
            {last ? "Got it" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}
