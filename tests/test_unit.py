import json
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from ctf_agent import report
from ctf_agent.bedrock_agent import BedrockAgent, BedrockError
from ctf_agent.challenges import build_challenges
from ctf_agent.config import Settings
from ctf_agent.tools import PythonSandbox, Toolbox

from .solvers import ScriptedModel, solver_code

SPECS = build_challenges()


def settings(tmp_path, **over):
    cfg = Settings.from_env()
    cfg.results_dir, cfg.workdir = tmp_path / "results", tmp_path / "work"
    cfg.sandbox, cfg.max_attempts, cfg.max_turns = "subprocess", 5, 12
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


class StubCTFd:
    """Minimal in-memory stand-in for CTFdClient (player side)."""

    def __init__(self):
        self.by_id = {i + 1: s for i, s in enumerate(SPECS)}
        self.solved = set()

    def challenge(self, cid):
        s = self.by_id[cid]
        return {"id": cid, "name": s.name, "category": s.category, "value": s.value,
                "description": s.description, "files": []}

    def attempt(self, cid, flag):
        if flag == self.by_id[cid].flag:
            self.solved.add(cid)
            return {"status": "correct", "message": "Correct"}
        return {"status": "incorrect", "message": "Incorrect"}


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.name)
def test_every_seeded_challenge_is_solvable(spec, tmp_path):
    sb = PythonSandbox(tmp_path, timeout=20, mode="subprocess")
    out = sb.run(solver_code(spec.name, spec.description))
    assert spec.flag in out.splitlines()


def test_flag_never_leaks_into_description():
    for s in SPECS:
        assert s.flag not in s.description


def test_toolbox_limits_and_duplicates(tmp_path):
    stub = StubCTFd()
    tb = Toolbox(stub, PythonSandbox(tmp_path, mode="subprocess"), 1, max_attempts=2)
    assert tb.call("submit_flag", {"challenge_id": 1, "flag": "flag{a}"})[0].startswith("incorrect")
    assert "already submitted" in tb.call("submit_flag", {"challenge_id": 1, "flag": "flag{a}"})[0]
    assert tb.attempts == 1
    tb.call("submit_flag", {"challenge_id": 1, "flag": "flag{b}"})
    assert "Attempt limit" in tb.call("submit_flag", {"challenge_id": 1, "flag": "flag{c}"})[0]
    assert "only working on challenge 1" in tb.call("get_challenge", {"challenge_id": 2})[0]
    text, err = tb.call("nope", {})
    assert err and "unknown tool" in text
    text, err = tb.call("run_python", {})
    assert err and "bad arguments" in text


def test_sandbox_timeout(tmp_path):
    sb = PythonSandbox(tmp_path, timeout=1, mode="subprocess")
    assert "timed out" in sb.run("import time; time.sleep(5)")


@pytest.mark.parametrize("idx", range(len(SPECS)))
def test_agent_loop_solves_with_fake_bedrock(idx, tmp_path):
    cfg, stub = settings(tmp_path), StubCTFd()
    cid = idx + 1
    tb = Toolbox(stub, PythonSandbox(tmp_path / str(cid), mode="subprocess"), cid, cfg.max_attempts)
    res = BedrockAgent(cfg, ScriptedModel()).solve(stub.challenge(cid) | {"id": cid}, tb)
    assert res["solved"] and res["outcome"] == "solved"
    assert res["attempts"] == 2 and res["tool_calls"] == 4
    assert res["input_tokens"] > 0 and cid in stub.solved


def test_agent_gives_up_cleanly(tmp_path):
    class Lazy:
        def converse(self, **kw):
            return {"output": {"message": {"role": "assistant", "content": [{"text": "no idea"}]}},
                    "stopReason": "end_turn", "usage": {}}
    cfg, stub = settings(tmp_path), StubCTFd()
    tb = Toolbox(stub, PythonSandbox(tmp_path, mode="subprocess"), 1, 5)
    res = BedrockAgent(cfg, Lazy()).solve(stub.challenge(1), tb)
    assert res["outcome"] == "gave_up" and not res["solved"]


def _client_error(code, msg="x"):
    return ClientError({"Error": {"Code": code, "Message": msg}}, "Converse")


def test_access_denied_has_actionable_message(tmp_path):
    class Denied:
        def converse(self, **kw):
            raise _client_error("AccessDeniedException", "not authorized")
    with pytest.raises(BedrockError, match="Model catalog"):
        BedrockAgent(settings(tmp_path), Denied()).ping()


def test_on_demand_throughput_hint(tmp_path):
    class Bad:
        def converse(self, **kw):
            raise _client_error("ValidationException", "Invocation of model ID x with on-demand throughput isn't supported")
    with pytest.raises(BedrockError, match="inference profile"):
        BedrockAgent(settings(tmp_path), Bad()).ping()


def test_transient_errors_are_retried(tmp_path):
    calls = {"n": 0}

    class Flaky:
        def converse(self, **kw):
            calls["n"] += 1
            if calls["n"] < 3:
                raise _client_error("ModelTimeoutException")
            return {"output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
                    "stopReason": "end_turn", "usage": {}}
    assert BedrockAgent(settings(tmp_path), Flaky(), sleep=lambda s: None).ping() == "ok"
    assert calls["n"] == 3


def test_report_md_and_pdf(tmp_path):
    rows = [{"challenge_id": 1, "challenge": "Caesar's Salad", "category": "Crypto", "value": 100,
             "solved": True, "outcome": "solved", "attempts": 2, "seconds": 12.4, "turns": 4,
             "tool_calls": 4, "input_tokens": 1000, "output_tokens": 200, "model_id": "m"},
            {"challenge_id": 2, "challenge": "Tiny RSA", "category": "Crypto", "value": 300,
             "solved": False, "outcome": "out_of_attempts", "attempts": 5, "seconds": 140, "turns": 9,
             "tool_calls": 9, "input_tokens": 5000, "output_tokens": 900, "model_id": "m"}]
    meta = {"run_id": "t", "model_id": "m", "region": "us-east-2", "started": "now"}
    run = tmp_path / "run"
    run.mkdir()
    (run / "results.json").write_text(json.dumps({"meta": meta, "results": rows}))
    base = tmp_path / "student_baseline.csv"
    base.write_text("challenge,student_median_seconds,student_mean_attempts,student_solve_rate\n"
                    "Caesar's Salad,300,1.5,0.9\nTiny RSA,1800,4,0.3\n")
    md, pdf = report.generate(run, base)
    text = md.read_text()
    assert "Solved 1 of 2" in text and "24.2x faster" in text
    assert pdf.read_bytes().startswith(b"%PDF")
