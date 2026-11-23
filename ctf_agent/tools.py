"""Tools the agent can call, in Bedrock Converse toolSpec format.

The agent never sees the CTFd token or the flags: it only talks to CTFd through
get_challenge / submit_flag, and runs code through run_python.

run_python sandboxing:
  * docker  - `docker run --network none`, memory/CPU/pid limits, only the scratch dir
              mounted. Used automatically when docker is on PATH (it is on the EC2 box).
  * subprocess - fallback for laptops without docker. Scratch cwd, trimmed environment and
              a timeout, but NOT isolated from the filesystem. Fine for a classroom demo,
              not for hostile code.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .ctfd import CTFdClient, CTFdError

MAX_TOOL_OUTPUT = 6000
DOCKER_IMAGE = os.environ.get("PYTHON_SANDBOX_IMAGE", "python:3.12-slim")

TOOL_SPECS = [
    {"toolSpec": {
        "name": "get_challenge",
        "description": "Fetch the full details (description, files, hints, points) of a CTFd challenge.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {"challenge_id": {"type": "integer"}},
            "required": ["challenge_id"],
        }},
    }},
    {"toolSpec": {
        "name": "download_file",
        "description": "Download a challenge attachment (use a path from get_challenge 'files') "
                       "into the scratch directory that run_python uses. Returns the file name.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "e.g. /files/abc123/data.bin?token=..."}},
            "required": ["path"],
        }},
    }},
    {"toolSpec": {
        "name": "run_python",
        "description": "Run a Python 3 script (standard library only, no network) and return its "
                       "stdout/stderr. Files in the scratch directory persist between calls. "
                       "Print whatever you want to see.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        }},
    }},
    {"toolSpec": {
        "name": "submit_flag",
        "description": "Submit a candidate flag for a challenge. Attempts are limited and recorded.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {"challenge_id": {"type": "integer"}, "flag": {"type": "string"}},
            "required": ["challenge_id", "flag"],
        }},
    }},
]


def truncate(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit // 2] + f"\n...[{len(text) - limit} chars truncated]...\n" + text[-limit // 2:]


# ----------------------------------------------------------------------------- sandbox
def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class PythonSandbox:
    def __init__(self, workdir: Path, timeout: int = 30, mode: str = "auto"):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        try:  # the docker sandbox runs as an unprivileged user and must be able to write here
            os.chmod(self.workdir, 0o777)
        except OSError:
            pass
        self.timeout = timeout
        if mode == "auto":
            mode = "docker" if docker_available() else "subprocess"
        if mode not in ("docker", "subprocess"):
            raise ValueError(f"PYTHON_SANDBOX must be auto, docker or subprocess, not {mode!r}")
        self.mode = mode

    def run(self, code: str) -> str:
        script = self.workdir / "_snippet.py"
        script.write_text(code)
        if self.mode == "docker":
            cmd = ["docker", "run", "--rm", "--network", "none", "--memory", "256m",
                   "--cpus", "1", "--pids-limit", "128", "--read-only", "--tmpfs", "/tmp",
                   "-v", f"{self.workdir.resolve()}:/work", "-w", "/work",
                   "--user", "65534:65534", DOCKER_IMAGE, "python", "/work/_snippet.py"]
            env = None
        else:
            cmd = [sys.executable, str(script)]
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONIOENCODING": "utf-8"}
        try:
            p = subprocess.run(cmd, cwd=self.workdir, env=env, capture_output=True,
                               text=True, timeout=self.timeout + (30 if self.mode == "docker" else 0))
        except subprocess.TimeoutExpired:
            return f"[timed out after {self.timeout}s]"
        out = p.stdout
        if p.stderr:
            out += ("\n" if out else "") + "[stderr]\n" + p.stderr
        if p.returncode != 0:
            out += f"\n[exit code {p.returncode}]"
        return truncate(out.strip() or "[no output]")


# ----------------------------------------------------------------------------- toolbox
@dataclass
class Toolbox:
    """Executes tool calls for ONE challenge and records what happened."""
    client: CTFdClient
    sandbox: PythonSandbox
    challenge_id: int
    max_attempts: int
    attempts: int = 0
    solved: bool = False
    tool_calls: int = 0
    tried: set = field(default_factory=set)
    attempt_log: list = field(default_factory=list)

    def call(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """Returns (text, is_error)."""
        self.tool_calls += 1
        try:
            fn: Callable[..., str] = getattr(self, f"_t_{name}", None)  # type: ignore[assignment]
            if fn is None:
                return f"unknown tool {name!r}", True
            return fn(**args), False
        except TypeError as e:
            return f"bad arguments for {name}: {e}", True
        except CTFdError as e:
            return f"CTFd error: {e}", True
        except Exception as e:  # never let a tool crash the whole run
            return f"{type(e).__name__}: {e}", True

    def _t_get_challenge(self, challenge_id: int) -> str:
        if int(challenge_id) != self.challenge_id:
            return f"You are only working on challenge {self.challenge_id}."
        c = self.client.challenge(self.challenge_id)
        keep = {k: c.get(k) for k in ("id", "name", "category", "value", "description",
                                      "files", "connection_info", "hints") if c.get(k) not in (None, [], "")}
        return truncate(json.dumps(keep, indent=2))

    def _t_download_file(self, path: str) -> str:
        name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(path.split("?")[0]).name) or "download.bin"
        dest = self.sandbox.workdir / name
        self.client.download(path, dest)
        return f"saved to ./{name} ({dest.stat().st_size} bytes)"

    def _t_run_python(self, code: str) -> str:
        return self.sandbox.run(code)

    def _t_submit_flag(self, challenge_id: int, flag: str) -> str:
        if int(challenge_id) != self.challenge_id:
            return f"You are only working on challenge {self.challenge_id}."
        flag = flag.strip()
        if flag in self.tried:
            return "You already submitted that exact flag. It was not counted again. Try something different."
        if self.attempts >= self.max_attempts:
            return f"Attempt limit reached ({self.max_attempts}). No more submissions allowed."
        self.tried.add(flag)
        self.attempts += 1
        res = self.client.attempt(self.challenge_id, flag)
        self.attempt_log.append({"flag": flag, "status": res["status"]})
        if res["status"] in ("correct", "already_solved"):
            self.solved = True
        left = self.max_attempts - self.attempts
        return f"{res['status']}: {res['message']} (attempts left: {left})"
