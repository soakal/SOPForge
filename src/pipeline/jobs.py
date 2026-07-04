"""Background job runner (Phase 3): each session's generation + export
pipeline runs on a worker thread, not inline in the request handler.
Status moves queued -> processing -> done | error; the caller (server.py's
POST /sessions) returns as soon as a job is queued, never blocking on the
actual work."""

import queue
import threading


class JobRunner:
    """One worker thread pulls jobs off a FIFO queue and runs them in
    order. Each job's status lives in a shared dict, mutated only by the
    worker thread (the sole writer) — callers only ever read it, so no
    lock is needed around a read, only around the dict mutation itself."""

    def __init__(self):
        self._queue = queue.Queue()
        self._statuses = {}
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def submit(self, job_id, fn):
        """Registers job_id as "queued" and enqueues fn (a zero-arg
        callable) to run on the worker thread. Returns immediately."""
        with self._lock:
            self._statuses[job_id] = {"status": "queued", "error": None}
        self._queue.put((job_id, fn))

    def status(self, job_id):
        """Returns {"status": ..., "error": ...} or {} if job_id was never
        submitted."""
        with self._lock:
            return dict(self._statuses.get(job_id, {}))

    def _worker(self):
        while True:
            job_id, fn = self._queue.get()
            with self._lock:
                self._statuses[job_id]["status"] = "processing"
            try:
                fn()
                with self._lock:
                    self._statuses[job_id]["status"] = "done"
            except Exception as exc:  # noqa: BLE001 - a job failure must never kill the worker thread
                with self._lock:
                    self._statuses[job_id]["status"] = "error"
                    self._statuses[job_id]["error"] = str(exc)
            finally:
                self._queue.task_done()
