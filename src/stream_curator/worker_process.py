"""Minimal background process management for the persistent worker loop."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

_RUNTIME_DIR = "runtime"
_STATE_FILE = "worker-process.json"
_START_LOCK_FILE = "worker-process.lock"
_STDOUT_LOG_FILE = "worker.stdout.log"
_STDERR_LOG_FILE = "worker.stderr.log"


@dataclass(slots=True)
class WorkerProcessPaths:
    state_dir: Path
    state_path: Path
    start_lock_path: Path
    stdout_path: Path
    stderr_path: Path


@dataclass(slots=True)
class WorkerProcessStatus:
    running: bool
    pid: int | None
    started_at: str | None
    state_present: bool
    stale_state: bool
    state_path: str
    stdout_path: str
    stderr_path: str
    command: list[str]
    cwd: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class WorkerStartResult:
    started: bool
    already_running: bool
    status: WorkerProcessStatus

    def to_dict(self) -> dict[str, object]:
        return {
            "started": self.started,
            "already_running": self.already_running,
            "status": self.status.to_dict(),
        }


@dataclass(slots=True)
class WorkerStopResult:
    stopped: bool
    was_running: bool
    force_killed: bool
    cleaned_stale_state: bool
    status: WorkerProcessStatus

    def to_dict(self) -> dict[str, object]:
        return {
            "stopped": self.stopped,
            "was_running": self.was_running,
            "force_killed": self.force_killed,
            "cleaned_stale_state": self.cleaned_stale_state,
            "status": self.status.to_dict(),
        }


def get_worker_process_paths(project_root: Path) -> WorkerProcessPaths:
    state_dir = project_root / "data" / _RUNTIME_DIR
    return WorkerProcessPaths(
        state_dir=state_dir,
        state_path=state_dir / _STATE_FILE,
        start_lock_path=state_dir / _START_LOCK_FILE,
        stdout_path=state_dir / _STDOUT_LOG_FILE,
        stderr_path=state_dir / _STDERR_LOG_FILE,
    )


def get_worker_process_status(*, project_root: Path) -> WorkerProcessStatus:
    paths = get_worker_process_paths(project_root)
    payload = _read_state_payload(paths.state_path)
    state_present = paths.state_path.exists()
    pid = _coerce_int(payload.get("pid")) if payload else None
    started_at = _coerce_str(payload.get("started_at")) if payload else None
    running = pid is not None and _is_expected_process_running(pid, started_at)
    stale_state = state_present and not running
    return WorkerProcessStatus(
        running=running,
        pid=pid,
        started_at=started_at,
        state_present=state_present,
        stale_state=stale_state,
        state_path=str(paths.state_path),
        stdout_path=str(paths.stdout_path),
        stderr_path=str(paths.stderr_path),
        command=_normalize_command(payload.get("command")) if payload else [],
        cwd=_coerce_str(payload.get("cwd")) if payload else None,
    )


def start_worker_process(
    *,
    project_root: Path,
    python_executable: str | None = None,
    verbose: bool = False,
) -> WorkerStartResult:
    paths = get_worker_process_paths(project_root)
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    lock_handle = _acquire_start_lock(paths.start_lock_path)
    try:
        status = get_worker_process_status(project_root=project_root)
        if status.running:
            return WorkerStartResult(
                started=False,
                already_running=True,
                status=status,
            )

        _remove_state_file(paths.state_path)
        _reset_log_file(paths.stdout_path)
        _reset_log_file(paths.stderr_path)

        command = _build_worker_command(
            python_executable=python_executable or sys.executable,
            verbose=verbose,
        )
        with paths.stdout_path.open("a", encoding="utf-8") as stdout_handle, paths.stderr_path.open(
            "a", encoding="utf-8"
        ) as stderr_handle:
            process = subprocess.Popen(
                command,
                cwd=str(project_root),
                env=_worker_environment(project_root),
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                close_fds=True,
                **_popen_background_kwargs(),
            )

        started_at = _get_process_started_at(process.pid) or _now_iso()
        _write_state_payload(
            paths.state_path,
            {
                "pid": process.pid,
                "started_at": started_at,
                "command": command,
                "cwd": str(project_root),
                "stdout_path": str(paths.stdout_path),
                "stderr_path": str(paths.stderr_path),
            },
        )
        return WorkerStartResult(
            started=True,
            already_running=False,
            status=get_worker_process_status(project_root=project_root),
        )
    finally:
        _release_start_lock(paths.start_lock_path, lock_handle)


def stop_worker_process(
    *,
    project_root: Path,
    timeout_seconds: int = 10,
) -> WorkerStopResult:
    status = get_worker_process_status(project_root=project_root)
    paths = get_worker_process_paths(project_root)
    if not status.state_present:
        return WorkerStopResult(
            stopped=False,
            was_running=False,
            force_killed=False,
            cleaned_stale_state=False,
            status=status,
        )

    if not status.running or status.pid is None:
        _remove_state_file(paths.state_path)
        return WorkerStopResult(
            stopped=False,
            was_running=False,
            force_killed=False,
            cleaned_stale_state=True,
            status=get_worker_process_status(project_root=project_root),
        )

    force_killed = False
    _terminate_process(status.pid)
    exited = _wait_for_process_exit(status.pid, timeout_seconds=timeout_seconds)
    if not exited:
        force_killed = _force_kill_process(status.pid)
        exited = _wait_for_process_exit(status.pid, timeout_seconds=max(1, timeout_seconds // 2))

    if exited:
        _remove_state_file(paths.state_path)
    return WorkerStopResult(
        stopped=exited,
        was_running=True,
        force_killed=force_killed,
        cleaned_stale_state=False,
        status=get_worker_process_status(project_root=project_root),
    )


def _build_worker_command(
    *,
    python_executable: str,
    verbose: bool,
) -> list[str]:
    command = [python_executable, "-X", "utf8", "-m", "stream_curator.cli"]
    if verbose:
        command.append("-v")
    command.extend(["worker", "loop", "--max-cycles", "0"])
    return command


def _popen_background_kwargs() -> dict[str, object]:
    if os.name == "nt":
        creationflags = 0
        creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": creationflags}
    return {"start_new_session": True}


def _worker_environment(project_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    src_dir = project_root / "src"
    if src_dir.exists():
        existing_pythonpath = env.get("PYTHONPATH", "")
        if existing_pythonpath:
            env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing_pythonpath}"
        else:
            env["PYTHONPATH"] = str(src_dir)
    return env


def _terminate_process(pid: int) -> None:
    if os.name == "nt":
        _taskkill_process(pid, force=False)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return


def _force_kill_process(pid: int) -> bool:
    if os.name == "nt":
        return _taskkill_process(pid, force=True)
    sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
    try:
        os.kill(pid, sigkill)
    except OSError:
        return False
    return True


def _wait_for_process_exit(pid: int, *, timeout_seconds: int) -> bool:
    deadline = time.perf_counter() + max(1, timeout_seconds)
    while time.perf_counter() < deadline:
        if not _is_process_running(pid):
            return True
        time.sleep(0.2)
    return not _is_process_running(pid)


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _is_process_running_windows(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _is_expected_process_running(pid: int, started_at: str | None) -> bool:
    if not _is_process_running(pid):
        return False
    if not started_at:
        return True
    actual_started_at = _get_process_started_at(pid)
    if not actual_started_at:
        return True
    return actual_started_at == started_at


def _taskkill_process(pid: int, *, force: bool) -> bool:
    command = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        command.append("/F")
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return completed.returncode == 0


def _is_process_running_windows(pid: int) -> bool:
    process_query_limited_information = 0x1000
    synchronize = 0x00100000
    still_active = 259
    access = process_query_limited_information | synchronize

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    open_process.restype = ctypes.c_void_p

    get_exit_code_process = kernel32.GetExitCodeProcess
    get_exit_code_process.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    get_exit_code_process.restype = ctypes.c_int

    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    handle = open_process(access, 0, pid)
    if not handle:
        return False

    try:
        exit_code = ctypes.c_uint32()
        if not get_exit_code_process(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        close_handle(handle)


def _get_process_started_at(pid: int) -> str | None:
    if pid <= 0 or os.name != "nt":
        return None

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    open_process.restype = ctypes.c_void_p

    class _FileTime(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", ctypes.c_uint32),
            ("dwHighDateTime", ctypes.c_uint32),
        ]

    get_process_times = kernel32.GetProcessTimes
    get_process_times.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
    ]
    get_process_times.restype = ctypes.c_int

    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    handle = open_process(process_query_limited_information, 0, pid)
    if not handle:
        return None

    try:
        created = _FileTime()
        exited = _FileTime()
        kernel = _FileTime()
        user = _FileTime()
        if not get_process_times(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        if ticks <= 0:
            return None
        unix_seconds = (ticks - 116444736000000000) / 10000000
        return datetime.fromtimestamp(unix_seconds, tz=UTC).replace(microsecond=0).isoformat(timespec="seconds")
    finally:
        close_handle(handle)


def _read_state_payload(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(raw, dict):
        return raw
    return {}


def _write_state_payload(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _remove_state_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _reset_log_file(path: Path) -> None:
    path.write_text("", encoding="utf-8")


def _acquire_start_lock(path: Path, *, timeout_seconds: int = 10) -> int:
    deadline = time.perf_counter() + max(1, timeout_seconds)
    while time.perf_counter() < deadline:
        try:
            return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _is_stale_lock_file(path):
                _remove_state_file(path)
                continue
            time.sleep(0.1)
    raise RuntimeError("worker_start_lock_timeout")


def _release_start_lock(path: Path, handle: int) -> None:
    try:
        os.close(handle)
    finally:
        _remove_state_file(path)


def _is_stale_lock_file(path: Path, *, stale_after_seconds: int = 30) -> bool:
    try:
        age_seconds = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age_seconds >= stale_after_seconds

def _normalize_command(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _coerce_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()
