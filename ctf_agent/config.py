"""Settings, read from environment variables and an optional .env file."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def home_dir() -> Path:
    """Directory that holds .env, results/ and workdir/."""
    return Path(os.environ.get("CTF_AGENT_HOME") or Path.cwd()).resolve()


def env_path() -> Path:
    return home_dir() / ".env"


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is optional at import time
        return
    p = env_path()
    if p.is_file():
        load_dotenv(p, override=False)


def write_env_value(key: str, value: str) -> None:
    """Set KEY=value in .env (create or update the line) and in os.environ."""
    p = env_path()
    lines = p.read_text().splitlines() if p.is_file() else []
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if pat.match(line):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    p.write_text("\n".join(lines) + "\n")
    try:
        p.chmod(0o600)
    except OSError:
        pass
    os.environ[key] = value


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _float(name: str) -> Optional[float]:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else None


@dataclass
class Settings:
    ctfd_url: str
    ctfd_token: str
    ctf_name: str
    admin_name: str
    admin_email: str
    admin_password: str
    agent_name: str
    agent_email: str
    agent_password: str
    aws_region: str
    model_id: str
    max_tokens: int
    temperature: Optional[float]
    max_turns: int
    max_attempts: int
    challenge_timeout: int
    python_timeout: int
    sandbox: str
    results_dir: Path
    workdir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        load_env()

        def g(key: str, default: str = "") -> str:
            return os.environ.get(key, default).strip()

        home = home_dir()
        return cls(
            ctfd_url=g("CTFD_URL", "http://localhost").rstrip("/"),
            ctfd_token=g("CTFD_TOKEN"),
            ctf_name=g("CTF_NAME", "FGCU CTF Lab"),
            admin_name=g("CTFD_ADMIN_NAME", "admin"),
            admin_email=g("CTFD_ADMIN_EMAIL", "admin@example.com"),
            admin_password=g("CTFD_ADMIN_PASSWORD"),
            agent_name=g("AGENT_USER_NAME", "ctf-agent"),
            agent_email=g("AGENT_USER_EMAIL", "agent@example.com"),
            agent_password=g("AGENT_USER_PASSWORD"),
            aws_region=g("AWS_REGION") or g("AWS_DEFAULT_REGION") or "us-east-2",
            model_id=g("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-5-5"),
            max_tokens=_int("MAX_TOKENS", 4096),
            temperature=_float("TEMPERATURE"),
            max_turns=_int("MAX_TURNS", 25),
            max_attempts=_int("MAX_ATTEMPTS", 5),
            challenge_timeout=_int("CHALLENGE_TIMEOUT_SECONDS", 600),
            python_timeout=_int("PYTHON_TIMEOUT_SECONDS", 30),
            sandbox=g("PYTHON_SANDBOX", "auto").lower(),
            results_dir=Path(g("RESULTS_DIR") or home / "results"),
            workdir=Path(g("WORKDIR") or home / "workdir"),
        )
