import json
from pathlib import Path

from stream_curator import worker_process
from stream_curator.worker_process import (
    get_worker_process_paths,
    get_worker_process_status,
    start_worker_process,
    stop_worker_process,
)


def test_worker_process_status_reports_missing_state(tmp_path: Path) -> None:
    status = get_worker_process_status(project_root=tmp_path)

    assert status.running is False
    assert status.pid is None
    assert status.state_present is False
    assert status.stale_state is False
    assert status.command == []


def test_worker_process_start_writes_state_and_reuses_running_pid(
    tmp_path: Path,
    monkeypatch,
) -> None:
    launched: dict[str, object] = {}
    running_pids = {43210}

    class _DummyPopen:
        def __init__(self, command: list[str], **kwargs: object) -> None:
            launched["command"] = command
            launched["kwargs"] = kwargs
            self.pid = 43210

    monkeypatch.setattr(worker_process.subprocess, "Popen", _DummyPopen)
    monkeypatch.setattr(worker_process, "_is_process_running", lambda pid: pid in running_pids)

    result = start_worker_process(
        project_root=tmp_path,
        python_executable="python.exe",
        verbose=True,
    )

    assert result.started is True
    assert result.already_running is False
    assert result.status.running is True
    assert result.status.pid == 43210
    assert launched["command"] == [
        "python.exe",
        "-X",
        "utf8",
        "-m",
        "stream_curator.cli",
        "-v",
        "worker",
        "loop",
        "--max-cycles",
        "0",
    ]

    paths = get_worker_process_paths(tmp_path)
    payload = json.loads(paths.state_path.read_text(encoding="utf-8"))
    assert payload["pid"] == 43210
    assert paths.stdout_path.read_text(encoding="utf-8") == ""
    assert paths.stderr_path.read_text(encoding="utf-8") == ""

    second = start_worker_process(
        project_root=tmp_path,
        python_executable="python.exe",
        verbose=False,
    )
    assert second.started is False
    assert second.already_running is True


def test_worker_process_stop_cleans_stale_state(tmp_path: Path) -> None:
    paths = get_worker_process_paths(tmp_path)
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.state_path.write_text(
        json.dumps({"pid": 99999}),
        encoding="utf-8",
    )

    result = stop_worker_process(project_root=tmp_path, timeout_seconds=1)

    assert result.stopped is False
    assert result.was_running is False
    assert result.cleaned_stale_state is True
    assert paths.state_path.exists() is False
    assert result.status.running is False
    assert result.status.state_present is False


def test_worker_process_stop_terminates_running_pid(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = get_worker_process_paths(tmp_path)
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.state_path.write_text(
        json.dumps({"pid": 24680}),
        encoding="utf-8",
    )

    kill_calls: list[tuple[int, bool]] = []
    checks = {"count": 0}

    def _fake_is_running(pid: int) -> bool:
        checks["count"] += 1
        return checks["count"] == 1

    monkeypatch.setattr(worker_process, "_is_process_running", _fake_is_running)
    monkeypatch.setattr(worker_process, "_taskkill_process", lambda pid, force: kill_calls.append((pid, force)) or True)
    monkeypatch.setattr(worker_process, "_wait_for_process_exit", lambda pid, timeout_seconds: True)

    result = stop_worker_process(project_root=tmp_path, timeout_seconds=2)

    assert result.stopped is True
    assert result.was_running is True
    assert result.force_killed is False
    assert kill_calls == [(24680, False)]
    assert paths.state_path.exists() is False
    assert result.status.running is False
