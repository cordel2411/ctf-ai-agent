"""End-to-end against a REAL CTFd (Bedrock is scripted). Run with:
    CTFD_TEST_URL=http://localhost pytest -m integration
CTFd must be freshly installed (setup wizard not yet completed) or already bootstrapped
with the credentials in $CTF_AGENT_HOME/.env."""
import os

import pytest

from ctf_agent import cli
from ctf_agent.bedrock_agent import BedrockAgent
from ctf_agent.config import Settings
from ctf_agent.ctfd import CTFdClient
from ctf_agent.harness import run_benchmark

from .solvers import ScriptedModel

URL = os.environ.get("CTFD_TEST_URL")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not URL, reason="CTFD_TEST_URL not set")]


@pytest.fixture(scope="module")
def cfg(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    os.environ["CTF_AGENT_HOME"] = str(home)
    for k in ("CTFD_TOKEN", "CTFD_ADMIN_TOKEN", "CTFD_ADMIN_PASSWORD", "AGENT_USER_PASSWORD"):
        os.environ.pop(k, None)
    (home / ".env").write_text(f"CTFD_URL={URL}\n")
    if os.environ.get("CTFD_TEST_ADMIN_PASSWORD"):
        (home / ".env").write_text(f"CTFD_URL={URL}\nCTFD_ADMIN_PASSWORD={os.environ['CTFD_TEST_ADMIN_PASSWORD']}\n")
    assert cli.main(["bootstrap"]) == 0
    assert cli.main(["bootstrap"]) == 0  # idempotent
    c = Settings.from_env()
    c.sandbox, c.max_attempts = "subprocess", 5
    return c


def test_check_ctfd_parts(cfg):
    me = CTFdClient(cfg.ctfd_url, cfg.ctfd_token).whoami()
    assert me["name"] == cfg.agent_name


def test_full_benchmark_then_reset(cfg, capsys):
    ctfd = CTFdClient(cfg.ctfd_url, cfg.ctfd_token)
    out = run_benchmark(cfg, ctfd, BedrockAgent(cfg, ScriptedModel()), run_id="itest", log=print)
    import json
    data = json.loads((out / "results.json").read_text())
    rows = data["results"]
    assert len(rows) == 6 and all(r["solved"] for r in rows), rows
    assert all(r["attempts"] == 2 for r in rows)
    assert len(list((out / "transcripts").glob("*.json"))) == 6
    assert all(c["solved_by_me"] for c in ctfd.challenges())  # CTFd agrees

    # a second run has nothing left to do
    out2 = run_benchmark(cfg, ctfd, BedrockAgent(cfg, ScriptedModel()), run_id="itest2", log=print)
    assert json.loads((out2 / "results.json").read_text())["results"] == []

    # reset-agent makes everything runnable again
    assert cli.main(["reset-agent"]) == 0
    assert not any(c["solved_by_me"] for c in ctfd.challenges())

    assert cli.main(["report", "--run", "itest"]) == 0
    assert (out / "report.pdf").read_bytes().startswith(b"%PDF")
