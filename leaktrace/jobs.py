"""Background jobs (packaging and tracing take minutes): a small thread pool, with state in SQLite so the UI can
poll progress and a restart marks anything unfinished as failed instead of leaving it 'running' forever."""
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .store import Store


class Jobs:
    def __init__(self, store: Store, workers: int = 2):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        store.fail_unfinished_jobs()

    def submit(self, kind: str, fn: Callable[[Callable[[float], None]], dict], detail: dict | None = None) -> str:
        """Run fn(progress) in the background; its returned dict becomes the job's result."""
        jid = uuid.uuid4().hex[:12]
        self.store.create_job(jid, kind, detail)

        def run():
            self.store.update_job(jid, status="running")
            last = [0.0]

            def progress(p: float):
                if p - last[0] >= 0.01 or p >= 1:  # don't write the database on every frame
                    last[0] = p
                    self.store.update_job(jid, progress=round(float(p), 3))

            try:
                result = fn(progress)
                self.store.update_job(jid, status="done", progress=1.0, result=result or {})
            except Exception as e:  # the job failed; the UI shows why
                self.store.update_job(jid, status="failed", error=f"{type(e).__name__}: {e}"[:2000])
                traceback.print_exc()

        self.pool.submit(run)
        return jid

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
