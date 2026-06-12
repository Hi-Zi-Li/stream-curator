"""Subprocess wrapper for invoking source CLIs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class CommandResult:
    command: list[str]
    stdout: str
    stderr: str
    returncode: int

    def json(self) -> Any:
        return json.loads(self.stdout)


class SubprocessRunner:
    def __init__(self, *, timeout_seconds: int = 45) -> None:
        self._timeout_seconds = timeout_seconds

    def run(self, command: list[str]) -> CommandResult:
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("STREAM_CURATOR_PYTHON_EXECUTABLE", sys.executable)
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            kwargs["startupinfo"] = startupinfo
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
                stdin=subprocess.DEVNULL,
                timeout=self._timeout_seconds,
                **kwargs,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Command timed out after {self._timeout_seconds}s: {' '.join(command)}"
            ) from exc
        result = CommandResult(
            command=command,
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Command failed ({result.returncode}): {' '.join(command)}\n{result.stderr.strip()}"
            )
        return result
