"""Runs the agent against every unsolved CTFd challenge and records the results."""
from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path
from typing import Callable, Optional

from .bedrock_agent import BedrockAgent
from .config import Settings
from .ctfd import CTFdClient
from .tools import PythonSandbox, Toolbox

CSV_FIELDS = ["challenge_id", "challenge", "category", "value", "solved", "outcome",
              "attempts", "seconds", "turns", "tool_calls", "input_tokens",
              "output_tokens", "model_id"]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "challenge"


def select(challenges: list[dict], only: Optional[list[str]]) -> list[dict]:
    if not only:
        return challenges
    picked = []
    for ch in challenges:
        for token in only:
            if token.isdigit() and int(token) == ch["id"] or token.lower() in ch["name"].lower():
                picked.append(ch)
                break
    return picked


def write_results(out: Path, meta: dict, rows: list[dict]) -> None:
    (out / "results.json").write_text(json.dumps({"meta": meta, "results": rows}, indent=2))
    with open(out / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def run_benchmark(cfg: Settings, ctfd: CTFdClient, agent: BedrockAgent,
                  only: Optional[list[str]] = None, limit: Optional[int] = None,
                  run_id: Optional[str] = None,
                  log: Callable[[str], None] = print) -> Path:
    run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
    out = cfg.results_dir / run_id
    (out / "transcripts").mkdir(parents=True, exist_ok=True)

    all_chals = sorted(ctfd.challenges(), key=lambda c: (c.get("value", 0), c["id"]))
    todo = [c for c in select(all_chals, only) if not c.get("solved_by_me")]
    skipped = [c["name"] for c in select(all_chals, only) if c.get("solved_by_me")]
    if limit:
        todo = todo[:limit]
    if skipped:
        log(f"Skipping already solved by the agent user: {', '.join(skipped)}  "
            "(run `ctf-agent reset-agent` to benchmark them again)")
    if not todo:
        log("Nothing to do: no unsolved challenges matched.")

    meta = {"run_id": run_id, "model_id": cfg.model_id, "region": cfg.aws_region,
            "ctfd_url": cfg.ctfd_url, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
            "max_attempts": cfg.max_attempts, "max_turns": cfg.max_turns,
            "challenge_timeout_seconds": cfg.challenge_timeout}
    rows: list[dict] = []

    for i, ch in enumerate(todo, 1):
        sandbox = PythonSandbox(cfg.workdir / run_id / str(ch["id"]), cfg.python_timeout, cfg.sandbox)
        if i == 1:
            meta["sandbox"] = sandbox.mode
            if sandbox.mode == "subprocess":
                log("WARNING: docker not available, running agent code in a plain subprocess "
                    "(no filesystem isolation).")
        log(f"[{i}/{len(todo)}] {ch['name']} ({ch.get('category')}, {ch.get('value')} pts) ...")
        toolbox = Toolbox(ctfd, sandbox, ch["id"], cfg.max_attempts)
        res = agent.solve(ch, toolbox)
        transcript = res.pop("transcript")
        (out / "transcripts" / f"{ch['id']:03d}-{slug(ch['name'])}.json").write_text(
            json.dumps({"challenge": ch["name"], "attempts": toolbox.attempt_log,
                        "messages": transcript}, indent=2))
        row = {"challenge_id": ch["id"], "challenge": ch["name"], "category": ch.get("category"),
               "value": ch.get("value"), "model_id": cfg.model_id, **res}
        rows.append(row)
        verdict = "SOLVED" if row["solved"] else f"not solved ({row['outcome']})"
        log(f"    {verdict} in {row['seconds']}s, {row['attempts']} attempt(s), "
            f"{row['input_tokens']}+{row['output_tokens']} tokens"
            + (f"\n    error: {row['error']}" if row["error"] else ""))
        write_results(out, meta, rows)   # incremental: a crash keeps what finished

    meta["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_results(out, meta, rows)
    return out
