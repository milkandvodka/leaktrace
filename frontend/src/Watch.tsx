import Hls from "hls.js";
import { Download, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Unauthorized, api } from "./api";
import type { Lecture, Student } from "./api";

export function Watch({ onAuth }: { onAuth: () => void }) {
  const [lectures, setLectures] = useState<Lecture[]>([]);
  const [students, setStudents] = useState<Student[]>([]);
  const [lecture, setLecture] = useState("");
  const [student, setStudent] = useState("");
  const [links, setLinks] = useState<{ playlist: string; download: string } | null>(null);
  const [error, setError] = useState("");
  const video = useRef<HTMLVideoElement>(null);
  const fail = (e: unknown) => (e instanceof Unauthorized ? onAuth() : setError(e instanceof Error ? e.message : String(e)));

  useEffect(() => {
    api.lectures().then((l) => { const ready = l.filter((x) => x.status === "ready"); setLectures(ready); setLecture(ready[0]?.id ?? ""); }).catch(fail);
    api.students().then((s) => { setStudents(s); setStudent(s[0]?.id ?? ""); }).catch(fail);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = video.current;
    if (!el || !links) return;
    if (Hls.isSupported()) {
      const hls = new Hls();
      hls.loadSource(links.playlist);
      hls.attachMedia(el);
      return () => hls.destroy();
    }
    el.src = links.playlist; // Safari plays HLS natively
  }, [links]);

  const open = () => { setError(""); api.session(lecture, student).then(setLinks).catch(fail); };
  const name = students.find((s) => s.id === student)?.name;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-[26px] font-bold text-ink">Watch as a student</h1>
        <p className="mt-1 text-[14.5px] text-muted max-w-[640px]">What a student sees: the normal lecture. Underneath, this copy carries marks unique to them in its timing, its sound and its picture.</p>
      </div>
      <div className="card p-5 grid gap-3 md:grid-cols-[1fr_1fr_auto] md:items-end">
        <label className="text-[12.5px] text-muted">Lecture
          <select className="field mt-1" value={lecture} onChange={(e) => setLecture(e.target.value)}>
            {lectures.map((l) => <option key={l.id} value={l.id}>{l.title}</option>)}
          </select>
        </label>
        <label className="text-[12.5px] text-muted">Student
          <select className="field mt-1" value={student} onChange={(e) => setStudent(e.target.value)}>
            {students.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.id})</option>)}
          </select>
        </label>
        <button type="button" className="btn-primary h-10" disabled={!lecture || !student} onClick={open}><Play size={16} /> Open their copy</button>
      </div>
      {error && <div role="alert" className="rounded-xl border border-bad-200 bg-bad-50 text-bad-700 p-4 text-[14px]">{error}</div>}
      {links && (
        <div className="card overflow-hidden animate-rise">
          <video ref={video} controls playsInline className="w-full bg-black aspect-video" />
          <div className="p-4 flex flex-wrap items-center justify-between gap-3">
            <span className="text-[14px] text-body">This copy belongs to <span className="font-semibold text-ink">{name}</span>. Watching it is logged.</span>
            <a className="btn-ghost" href={links.download}><Download size={16} /> Download their copy</a>
          </div>
        </div>
      )}
    </div>
  );
}
