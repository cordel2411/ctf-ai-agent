"""ctf-agent command line interface."""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path
from typing import Optional

from . import __version__, report
from .bedrock_agent import BedrockAgent, BedrockError, make_bedrock_client
from .challenges import build_challenges
from .config import Settings, write_env_value
from .ctfd import (CTFdClient, CTFdError, create_token, is_setup, login_session,
                   run_setup, wait_for_ctfd)
from .harness import run_benchmark


def say(msg: str = "") -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


# ------------------------------------------------------------------------- helpers
def _token_ok(base: str, token: str, name: Optional[str] = None, admin: bool = False) -> bool:
    if not token:
        return False
    client = CTFdClient(base, token)
    try:
        me = client.whoami()
        if admin:
            client.api("GET", "/configs")  # admin-only endpoint: 403 for normal users
    except CTFdError:
        return False
    return not name or me.get("name") == name


def admin_client(cfg: Settings) -> CTFdClient:
    tok = os.environ.get("CTFD_ADMIN_TOKEN", "")
    if not _token_ok(cfg.ctfd_url, tok, admin=True):
        die("no valid CTFD_ADMIN_TOKEN. Run `ctf-agent bootstrap` first.")
    return CTFdClient(cfg.ctfd_url, tok)


def agent_client(cfg: Settings) -> CTFdClient:
    if not _token_ok(cfg.ctfd_url, cfg.ctfd_token):
        die("CTFD_TOKEN is missing or invalid. Run `ctf-agent bootstrap`.")
    return CTFdClient(cfg.ctfd_url, cfg.ctfd_token)


def seed_challenges(admin: CTFdClient, reset: bool = False) -> None:
    for spec in build_challenges():
        existing = admin.find_challenge(spec.name)
        if existing and reset:
            admin.delete_challenge(existing["id"])
            existing = None
        if existing:
            say(f"  = {spec.name} (already exists)")
            continue
        admin.create_challenge(spec.name, spec.category, spec.description, spec.value, spec.flag)
        say(f"  + {spec.name} ({spec.value} pts)")


# ------------------------------------------------------------------------ commands
def cmd_bootstrap(cfg: Settings, args) -> int:
    base = cfg.ctfd_url
    say(f"Waiting for CTFd at {base} ...")
    wait_for_ctfd(base)

    if not cfg.admin_password:
        cfg.admin_password = secrets.token_urlsafe(18)
        write_env_value("CTFD_ADMIN_PASSWORD", cfg.admin_password)
    if not cfg.agent_password:
        cfg.agent_password = secrets.token_urlsafe(18)
        write_env_value("AGENT_USER_PASSWORD", cfg.agent_password)

    if is_setup(base):
        say("CTFd setup wizard already completed.")
    else:
        say("Running CTFd setup wizard ...")
        run_setup(base, cfg.ctf_name, cfg.admin_name, cfg.admin_email, cfg.admin_password)

    admin_tok = os.environ.get("CTFD_ADMIN_TOKEN", "")
    if not _token_ok(base, admin_tok, admin=True):
        say("Creating admin API token ...")
        try:
            sess = login_session(base, cfg.admin_name, cfg.admin_password)
        except CTFdError as e:
            die(f"{e}\nThe admin account already exists with a different password. Put the right "
                "CTFD_ADMIN_PASSWORD in .env, or set CTFD_ADMIN_TOKEN directly.")
        admin_tok = create_token(base, sess, "ctf-agent admin")
        write_env_value("CTFD_ADMIN_TOKEN", admin_tok)
    admin = CTFdClient(base, admin_tok)

    if not admin.find_user(cfg.agent_name):
        say(f"Creating player account {cfg.agent_name!r} ...")
        admin.create_user(cfg.agent_name, cfg.agent_email, cfg.agent_password)
    if not _token_ok(base, cfg.ctfd_token, name=cfg.agent_name):
        say("Creating the agent's API token ...")
        try:
            sess = login_session(base, cfg.agent_name, cfg.agent_password)
        except CTFdError as e:
            die(f"{e}\nThe agent account exists with a different password. Fix "
                "AGENT_USER_PASSWORD in .env or delete that user in the CTFd admin panel.")
        write_env_value("CTFD_TOKEN", create_token(base, sess, "ctf-agent"))

    if not args.no_seed:
        say("Seeding challenges ...")
        seed_challenges(admin)
    say("\nBootstrap complete. Next: `ctf-agent check`, then `ctf-agent run`.")
    return 0


def cmd_seed(cfg: Settings, args) -> int:
    say("Seeding challenges ...")
    seed_challenges(admin_client(cfg), reset=args.reset)
    return 0


def cmd_reset_agent(cfg: Settings, args) -> int:
    admin = admin_client(cfg)
    user = admin.find_user(cfg.agent_name)
    if not user:
        die(f"user {cfg.agent_name!r} not found")
    subs = admin.submissions_for_user(user["id"])
    for s in subs:
        admin.delete_submission(s["id"])
    say(f"Deleted {len(subs)} submissions for {cfg.agent_name}. Challenges are unsolved again.")
    return 0


def cmd_check(cfg: Settings, args) -> int:
    bad = 0

    def step(label: str, fn):
        nonlocal bad
        try:
            detail = fn()
            say(f"  OK    {label}" + (f": {detail}" if detail else ""))
        except Exception as e:  # noqa: BLE001
            bad += 1
            say(f"  FAIL  {label}\n        {str(e)}")

    say(f"ctf-agent {__version__}  |  CTFd {cfg.ctfd_url}  |  Bedrock {cfg.aws_region}  |  {cfg.model_id}")
    step("CTFd reachable", lambda: wait_for_ctfd(cfg.ctfd_url, timeout=15) or "")
    step("CTFd agent token", lambda: (_ for _ in ()).throw(CTFdError("CTFD_TOKEN invalid or missing; run `ctf-agent bootstrap`"))
         if not _token_ok(cfg.ctfd_url, cfg.ctfd_token) else f"logged in as {CTFdClient(cfg.ctfd_url, cfg.ctfd_token).whoami()['name']}")
    step("Challenges visible", lambda: f"{len(CTFdClient(cfg.ctfd_url, cfg.ctfd_token).challenges())} challenges")

    def aws_identity():
        import boto3
        ident = boto3.client("sts", region_name=cfg.aws_region).get_caller_identity()
        return ident["Arn"]
    step("AWS credentials", aws_identity)
    step("Bedrock model call", lambda: f"model replied {BedrockAgent(cfg).ping()!r}")

    from .tools import docker_available
    say(f"  INFO  python sandbox: {'docker (isolated)' if docker_available() else 'subprocess (no isolation)'}")
    say("\nAll good." if not bad else f"\n{bad} check(s) failed.")
    return 1 if bad else 0


def cmd_models(cfg: Settings, args) -> int:
    import boto3
    from botocore.exceptions import ClientError
    br = boto3.client("bedrock", region_name=cfg.aws_region)
    say(f"Anthropic models in {cfg.aws_region}:")
    try:
        for m in br.list_foundation_models(byProvider="Anthropic")["modelSummaries"]:
            say(f"  {m['modelId']}")
        say("\nInference profiles (use one of these IDs when the plain model ID is rejected):")
        token = None
        while True:
            kw = {"typeEquals": "SYSTEM_DEFINED", "maxResults": 100}
            if token:
                kw["nextToken"] = token
            resp = br.list_inference_profiles(**kw)
            for p in resp["inferenceProfileSummaries"]:
                if "anthropic" in p["inferenceProfileId"]:
                    say(f"  {p['inferenceProfileId']}")
            token = resp.get("nextToken")
            if not token:
                break
    except ClientError as e:
        die(f"{e.response['Error']['Code']}: {e.response['Error']['Message']}")
    return 0


def cmd_run(cfg: Settings, args) -> int:
    if args.model:
        cfg.model_id = args.model
    if args.max_attempts:
        cfg.max_attempts = args.max_attempts
    ctfd = agent_client(cfg)
    try:
        agent = BedrockAgent(cfg, make_bedrock_client(cfg))
        say(f"Model {cfg.model_id} in {cfg.aws_region}")
        agent.ping()
    except BedrockError as e:
        die(str(e))
    out = run_benchmark(cfg, ctfd, agent, only=args.challenge, limit=args.limit, run_id=args.run_id, log=say)
    return _finish_run(out, args.baseline)


def _default_baseline(arg: Optional[str]) -> Optional[Path]:
    if arg:
        return Path(arg)
    for name in ("student_baseline.csv", "student_baseline.example.csv"):
        p = Path(__file__).resolve().parent.parent / "data" / name
        if p.is_file():
            return p
    return None


def _finish_run(out: Path, baseline_arg: Optional[str]) -> int:
    if not (out / "results.json").is_file():
        return 0
    try:
        md, pdf = report.generate(out, _default_baseline(baseline_arg))
        say(f"\nResults: {out}/results.csv\nReport:  {pdf}\n         {md}")
    except Exception as e:  # noqa: BLE001
        say(f"\nResults saved in {out} (report generation failed: {e})")
    return 0


def cmd_report(cfg: Settings, args) -> int:
    runs = sorted(p for p in cfg.results_dir.glob("*") if (p / "results.json").is_file())
    if args.run:
        run = cfg.results_dir / args.run
    elif runs:
        run = runs[-1]
    else:
        die(f"no results found in {cfg.results_dir}")
    return _finish_run(run, args.baseline)


# --------------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ctf-agent", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bootstrap", help="set up CTFd, create admin + agent accounts and tokens, seed challenges")
    b.add_argument("--no-seed", action="store_true")
    s = sub.add_parser("seed", help="(re)create the built-in challenges")
    s.add_argument("--reset", action="store_true", help="delete and recreate them")
    sub.add_parser("reset-agent", help="forget the agent's solves so challenges can be re-run")
    sub.add_parser("check", help="verify CTFd, AWS credentials and Bedrock access")
    sub.add_parser("models", help="list Claude model IDs available in your region")

    r = sub.add_parser("run", help="run the agent against unsolved challenges")
    r.add_argument("-c", "--challenge", action="append", help="challenge id or part of its name (repeatable)")
    r.add_argument("-n", "--limit", type=int, help="stop after N challenges")
    r.add_argument("--model", help="override BEDROCK_MODEL_ID")
    r.add_argument("--max-attempts", type=int, help="override MAX_ATTEMPTS")
    r.add_argument("--run-id")
    r.add_argument("--baseline", help="student baseline CSV (default: data/student_baseline.csv)")

    rp = sub.add_parser("report", help="rebuild report.md/report.pdf for a finished run")
    rp.add_argument("--run", help="run id (default: latest)")
    rp.add_argument("--baseline")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Settings.from_env()
    handler = {"bootstrap": cmd_bootstrap, "seed": cmd_seed, "reset-agent": cmd_reset_agent,
               "check": cmd_check, "models": cmd_models, "run": cmd_run, "report": cmd_report}[args.cmd]
    try:
        return handler(cfg, args)
    except (CTFdError, BedrockError) as e:
        die(str(e))
    except KeyboardInterrupt:
        die("interrupted", 130)


if __name__ == "__main__":
    sys.exit(main())
