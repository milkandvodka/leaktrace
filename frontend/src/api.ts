// Typed calls to the LeakTrace API. The admin token lives in localStorage (per browser).

export class Unauthorized extends Error {}

const TOKEN_KEY = "lt.token";
export const token = {
  get: () => { try { return localStorage.getItem(TOKEN_KEY) ?? ""; } catch { return ""; } },
  set: (t: string) => { try { localStorage.setItem(TOKEN_KEY, t); } catch { /* private mode: lasts this page */ } },
};

/** Whether this browser has seen the demo guide. */
const SEEN = "lt.tour-seen";
export const tourSeen = {
  get: () => { try { return localStorage.getItem(SEEN) === "1"; } catch { return false; } },
  set: () => { try { localStorage.setItem(SEEN, "1"); } catch { /* blocked storage: it shows again next visit */ } },
};

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(path, { ...init, headers: { ...(init.headers ?? {}), Authorization: `Bearer ${token.get()}` } });
  if (r.status === 401) throw new Unauthorized();
  if (!r.ok) {
    let msg = `The server said ${r.status}.`;
    try { const j = await r.json(); if (typeof j.detail === "string") msg = j.detail; } catch { /* not JSON */ }
    throw new Error(msg);
  }
  return r.json();
}

export interface Title {
  id: string; name: string; status: "processing" | "ready" | "failed"; created: string;
  meta?: { duration: number; segments: number; marked: number; has_audio: boolean; size?: string; fps?: string; ladder?: string[] } | null;
}
export interface Viewer { id: string; name: string; created: string }
export interface Job {
  id: string; kind: string; status: "queued" | "running" | "done" | "failed"; progress: number;
  result?: { trace?: string; verdict?: string; title?: string } | null; error?: string | null;
}
export interface Suspect { viewer: string; z: number; p: number; p_family: number; verdict: string }
export interface Report {
  id: string; leak: string; created: string;
  verdict: "identified" | "narrowed" | "inconclusive" | "not_ours" | "no_candidates";
  title: { id: string; name: string } | null;
  location: { offset: number; speed: number; duration: number; via: "audio" | "video" } | null;
  segments_used: number;
  carriers: Record<string, { weight: number; mean_z: number }>;
  segments: { seg: number; y: number }[];
  suspects: Suspect[]; coalition: string[]; candidates: number; notice: string;
}
export interface BenchRow { attack: string; clip: string; n: number; top_correct: number; identified: number;
  narrowed_or_better: number; false_accusations: number; mean_z: number }
export interface Bench { title: string; created: string; candidates: number; psnr_db: number; table: BenchRow[] }

export interface DemoSample { key: string; label: string; blurb: string; trace: string; video: string }
export interface DemoInfo {
  title: { id: string; name: string; status: Title["status"] } | null;
  viewers: { id: string; name: string }[]; samples: DemoSample[]; preparing: boolean; audience: number;
}

export type Session = { token: string; hls: string; dash: string; download: string; expires: number };

export const api = {
  health: () => fetch("/api/health").then((r) => r.json() as Promise<{ ok: boolean; demo: boolean }>),
  demo: () => call<DemoInfo>("/api/demo"),
  titles: () => call<Title[]>("/api/titles"),
  viewers: () => call<Viewer[]>("/api/viewers"),
  addViewers: (viewers: { id: string; name: string }[]) =>
    call<{ added: number }>("/api/viewers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ viewers }) }),
  addTitle: (id: string, name: string, file: File) => {
    const fd = new FormData();
    fd.append("id", id); fd.append("name", name); fd.append("file", file);
    return call<{ job: string }>("/api/titles", { method: "POST", body: fd });
  },
  session: (title: string, viewer: string) =>
    call<Session>("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title, viewer }) }),
  trace: (file: File, firstSeen: string) => {
    const fd = new FormData();
    fd.append("file", file);
    if (firstSeen) fd.append("first_seen", new Date(firstSeen).toISOString());
    return call<{ job: string }>("/api/traces", { method: "POST", body: fd });
  },
  traces: () => call<{ id: string; title: string | null; verdict: string; created: string }[]>("/api/traces"),
  report: (id: string) => call<{ report: Report }>(`/api/traces/${id}`).then((t) => t.report),
  job: (id: string) => call<Job>(`/api/jobs/${id}`),
  bench: () => call<Bench>("/api/bench/latest"),
};

export async function waitForJob(id: string, onProgress: (j: Job) => void): Promise<Job> {
  for (;;) {
    const j = await api.job(id);
    onProgress(j);
    if (j.status === "done" || j.status === "failed") return j;
    await new Promise((r) => setTimeout(r, 1000));
  }
}

export const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
