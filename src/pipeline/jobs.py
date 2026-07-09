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
    worker thread (the sole writer). Every access — including reads in
    status() — still takes the same lock: dict mutation isn't guaranteed
    atomic across all CPython builds, and this dict is tiny and accessed
    rarely enough that the lock costs nothing worth optimizing away."""

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
            self._statuses[job_id] = {"status": "queued", "error": None, "progress": None}
        self._queue.put((job_id, fn))

    def status(self, job_id):
        """Returns {"status": ..., "error": ..., "progress": ...} or {} if
        job_id was never submitted. `progress` is None until the running job
        reports one via set_progress, then {"current": ..., "total": ...}."""
        with self._lock:
            return dict(self._statuses.get(job_id, {}))

    def set_progress(self, job_id, current, total):
        """Called from inside a running job (fn, on the worker thread) to
        report how far along it is -- e.g. "step 3 of 10 generated". A no-op
        if job_id isn't currently tracked (defensive: a job must never crash
        the pipeline just because it tried to report progress after being
        removed)."""
        with self._lock:
            if job_id in self._statuses:
                self._statuses[job_id]["progress"] = {"current": current, "total": total}

    def seed_done(self, job_id):
        """Marks job_id as done without running anything -- used at server
        startup to restore status for sessions that finished in a previous
        run (their output files already exist on disk; nothing needs
        regenerating). Never call this for a job that hasn't actually
        completed -- there is no way to distinguish a seeded "done" from a
        genuinely finished one afterward."""
        with self._lock:
            self._statuses[job_id] = {"status": "done", "error": None}

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
