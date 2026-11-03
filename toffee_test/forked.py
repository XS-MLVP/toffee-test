"""Run each VCS test's setup, call, and teardown in a fresh child process."""

from __future__ import annotations

import ctypes
import os
import pickle
import signal
import sys
import tempfile
import time
from pathlib import Path

import pytest
from _pytest.reports import TestReport
from _pytest.runner import runtestprotocol


_CHILD_ENV = "TOFFEE_TEST_FORKED_CHILD"


def _is_vcs_item(item: pytest.Item) -> bool:
    return bool(getattr(item, "_toffee_vcs_dut", False))


def _failure_report(item: pytest.Item, when: str, message: str) -> TestReport:
    return TestReport(
        nodeid=item.nodeid,
        location=item.location,
        keywords=item.keywords,
        outcome="failed",
        longrepr=message,
        when=when,
        sections=(("stdout", message),),
    )


def _write_result(path: str, reports: list[TestReport], error: str | None = None) -> None:
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "wb") as stream:
        pickle.dump({"reports": reports, "error": error}, stream, protocol=5)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def _run_child(
    item: pytest.Item, result_path: str, nextitem: pytest.Item | None, parent_pid: int
) -> None:
    os.environ[_CHILD_ENV] = "1"
    reports: list[TestReport] = []
    error = None
    try:
        if sys.platform.startswith("linux"):
            # UCAgent can terminate pytest when its outer timeout expires.
            # Do not leave native DUTs alive holding stdout pipes or VDB locks.
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code))
            if os.getppid() != parent_pid:
                os._exit(1)
        reports.extend(runtestprotocol(item, log=False, nextitem=nextitem))
    except BaseException as exc:  # pragma: no cover - defensive process boundary
        error = f"forked child raised {type(exc).__name__}: {exc}"
    try:
        _write_result(result_path, reports, error)
    finally:
        os._exit(0 if error is None else 1)


def _load_result(path: str) -> dict:
    with open(path, "rb") as stream:
        return pickle.load(stream)


def run_simulator_protocol(
    item: pytest.Item,
    *,
    log: bool = True,
    nextitem: pytest.Item | None = None,
    timeout: float = 300.0,
) -> list[TestReport]:
    """Run the complete pytest lifecycle in a child process.

    Picker VCS state must be constructed and finalized in the same child;
    constructing it in the controller prevents the next parametrized case
    from initializing a fresh VCS runtime.
    """
    fd, result_path = tempfile.mkstemp(prefix="toffee-forked-", suffix=".pkl")
    os.close(fd)
    os.unlink(result_path)
    reports: list[TestReport] = []
    if log:
        item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    parent_pid = os.getpid()
    child = os.fork()
    if child == 0:
        _run_child(item, result_path, nextitem, parent_pid)

    deadline = time.monotonic() + timeout
    status = None
    while True:
        waited, status = os.waitpid(child, os.WNOHANG)
        if waited == child:
            break
        if time.monotonic() >= deadline:
            os.kill(child, signal.SIGTERM)
            grace = time.monotonic() + 0.5
            while True:
                waited, _ = os.waitpid(child, os.WNOHANG)
                if waited == child:
                    break
                if time.monotonic() >= grace:
                    os.kill(child, signal.SIGKILL)
                    os.waitpid(child, 0)
                    break
                time.sleep(0.01)
            reports.append(_failure_report(item, "call", f"forked VCS test timed out after {timeout:g}s"))
            status = None
            break
        time.sleep(0.01)
    else:  # pragma: no cover
        status = None

    if status is not None and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
        try:
            result = _load_result(result_path)
            reports.extend(result.get("reports", []))
            if result.get("error"):
                reports.append(_failure_report(item, "call", result["error"]))
        except Exception as exc:
            reports.append(_failure_report(item, "call", f"invalid forked result: {exc}"))
    elif not any(report.when == "call" for report in reports):
        detail = f"status={status}" if status is not None else "unknown child status"
        reports.append(_failure_report(item, "call", f"forked VCS child exited abnormally ({detail})"))

    for report in reports:
        if log:
            item.ihook.pytest_runtest_logreport(report=report)
    if log:
        item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    Path(result_path).unlink(missing_ok=True)
    if hasattr(item, "_request"):
        item._request = False
        item.funcargs = None
    return reports


# Backward-compatible name for callers of the first VCS-only implementation.
run_vcs_protocol = run_simulator_protocol
