"""Background job runner: status lifecycle queued -> processing -> done |
error. Slow and exploding stub jobs prove an intermediate non-done status
is observable and errors are captured, not swallowed or crashing the worker."""

import threading
import time

from pipeline.jobs import JobRunner


def _poll_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_status_is_processing_while_a_slow_job_runs():
    runner = JobRunner()
    started = threading.Event()
    release = threading.Event()

    def slow_job():
        started.set()
        release.wait(timeout=5)

    runner.submit("job-1", slow_job)
    assert started.wait(timeout=5)
    assert runner.status("job-1")["status"] == "processing"
    release.set()
    assert _poll_until(lambda: runner.status("job-1")["status"] == "done")


def test_slow_job_reaches_done_eventually():
    runner = JobRunner()
    ran = threading.Event()

    def slow_job():
        time.sleep(0.1)
        ran.set()

    runner.submit("job-1", slow_job)
    assert _poll_until(lambda: runner.status("job-1")["status"] == "done")
    assert ran.is_set()


def test_exploding_job_reaches_error_with_captured_detail():
    runner = JobRunner()

    def exploding_job():
        raise RuntimeError("simulated pipeline failure")

    runner.submit("job-1", exploding_job)
    assert _poll_until(lambda: runner.status("job-1")["status"] in ("done", "error"))
    status = runner.status("job-1")
    assert status["status"] == "error"
    assert "simulated pipeline failure" in status["error"]


def test_worker_survives_a_job_exception_and_keeps_processing_later_jobs():
    """The single background thread must never die from one bad job —
    every job submitted afterward still has to run."""
    runner = JobRunner()

    def exploding_job():
        raise RuntimeError("boom")

    ran = threading.Event()

    def healthy_job():
        ran.set()

    runner.submit("job-1", exploding_job)
    runner.submit("job-2", healthy_job)

    assert _poll_until(lambda: runner.status("job-2")["status"] == "done")
    assert ran.is_set()


def test_unknown_job_id_returns_empty_status():
    runner = JobRunner()
    assert runner.status("does-not-exist") == {}


def test_set_progress_is_reflected_in_status_while_running():
    runner = JobRunner()
    started = threading.Event()
    release = threading.Event()

    def slow_job():
        started.set()
        runner.set_progress("job-1", 1, 3)
        release.wait(timeout=5)

    runner.submit("job-1", slow_job)
    assert started.wait(timeout=5)
    assert _poll_until(lambda: runner.status("job-1").get("progress") == {"current": 1, "total": 3})
    release.set()
    assert _poll_until(lambda: runner.status("job-1")["status"] == "done")


def test_set_progress_on_unknown_job_is_a_noop():
    runner = JobRunner()
    runner.set_progress("does-not-exist", 1, 3)  # must not raise
    assert runner.status("does-not-exist") == {}


def test_jobs_run_in_submission_order():
    runner = JobRunner()
    order = []
    order_lock = threading.Lock()

    def make_job(n):
        def job():
            with order_lock:
                order.append(n)

        return job

    for i in range(5):
        runner.submit(f"job-{i}", make_job(i))

    assert _poll_until(lambda: all(runner.status(f"job-{i}")["status"] == "done" for i in range(5)))
    assert order == [0, 1, 2, 3, 4]
