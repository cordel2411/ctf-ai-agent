"""Claude on Amazon Bedrock, driven through the Converse API with tool use.

Credentials come from boto3's normal chain: the EC2 instance role on the lab server, or
`aws configure` / AWS_PROFILE / SSO on a laptop. No API keys live in this repo.

Two details that commonly break Bedrock agents are handled here:
  * the assistant message returned by Converse is appended to the history *verbatim*
    (reasoning and toolUse blocks included), which Claude requires for multi-turn tool use;
  * newer Claude models must be called through an inference profile ID
    (us.* / global.*), not the bare model ID, otherwise Bedrock answers
    "on-demand throughput isn't supported".
"""
from __future__ import annotations

import time
from typing import Any, Optional

from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

from .config import Settings
from .tools import TOOL_SPECS, Toolbox, truncate

SYSTEM_PROMPT = """You are a skilled player in an educational capture-the-flag (CTF) competition \
that runs on a private practice server. Your job is to solve the one challenge you are given.

How to work:
1. Call get_challenge first and read the description carefully.
2. Use run_python (Python 3 standard library only, no internet) for every decoding, decryption or \
calculation. Do not do long calculations by hand, and do not guess.
3. Flags look like flag{...}. Before you submit, check that your answer has that shape and that you \
got it from a computation you actually ran.
4. Submit with submit_flag. Submissions are limited and each one is recorded, so submit only \
candidates you have verified.
5. When submit_flag says "correct", stop and reply with one short sentence. If you cannot solve \
the challenge, say so briefly instead of inventing a flag.

Nobody can answer questions while you work, so do not ask for clarification."""


class BedrockError(RuntimeError):
    """A Bedrock failure with a human-readable fix attached."""


def make_bedrock_client(settings: Settings):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-runtime",
        region_name=settings.aws_region,
        config=Config(retries={"max_attempts": 8, "mode": "adaptive"},
                      read_timeout=300, connect_timeout=10),
    )


def explain(err: Exception, model_id: str, region: str) -> BedrockError:
    if isinstance(err, NoCredentialsError):
        return BedrockError(
            "No AWS credentials found. On EC2, attach the instance role created by "
            "infra/cloudformation.yaml. On a laptop run `aws configure` or `aws sso login`.")
    if isinstance(err, EndpointConnectionError):
        return BedrockError(f"Cannot reach Bedrock in {region}: {err}. Check the region and network.")
    if not isinstance(err, ClientError):
        return BedrockError(str(err))
    code = err.response.get("Error", {}).get("Code", "")
    msg = err.response.get("Error", {}).get("Message", "")
    if code == "AccessDeniedException":
        return BedrockError(
            f"Access denied for model {model_id} in {region}: {msg}\n"
            "Fix, in this order:\n"
            "  1. The caller needs bedrock:InvokeModel (infra/bedrock-policy.json has it).\n"
            "  2. First-time Anthropic use may require the use-case form and a Marketplace "
            "subscription: Bedrock console > Model catalog > open the Claude model > "
            "submit the form / try it in the Playground once, then wait a minute.\n"
            "  3. A pending AWS account verification also blocks Anthropic models; "
            "check the AWS Support / Billing notices.")
    if code == "ValidationException" and "on-demand throughput" in msg:
        return BedrockError(
            f"{model_id} must be called through an inference profile. Use the "
            "us.* or global.* version of the ID (run `ctf-agent models` to list them).")
    if code == "ResourceNotFoundException" or (code == "ValidationException" and "model identifier" in msg):
        return BedrockError(
            f"Model {model_id} was not found in {region}: {msg}\n"
            "Run `ctf-agent models` to list valid IDs, then set BEDROCK_MODEL_ID in .env.")
    if code in ("ThrottlingException", "ServiceQuotaExceededException"):
        return BedrockError(f"Bedrock rate/quota limit hit for {model_id}: {msg}. "
                            "Wait and retry, or request a quota increase.")
    return BedrockError(f"{code}: {msg}")


TRANSIENT = {"ModelTimeoutException", "ServiceUnavailableException",
             "InternalServerException", "ModelErrorException", "ThrottlingException"}


def _loggable(blocks: list[dict]) -> list[dict]:
    out = []
    for b in blocks:
        if "text" in b:
            out.append({"text": b["text"]})
        elif "toolUse" in b:
            out.append({"toolUse": {"name": b["toolUse"]["name"], "input": b["toolUse"].get("input")}})
        elif "toolResult" in b:
            tr = b["toolResult"]
            out.append({"toolResult": {"status": tr.get("status"),
                                       "text": truncate("".join(c.get("text", "") for c in tr["content"]), 2000)}})
        elif "reasoningContent" in b:
            rc = b["reasoningContent"].get("reasoningText", {}).get("text", "")
            out.append({"reasoning": truncate(rc, 1000) if rc else "[redacted]"})
    return out


class BedrockAgent:
    def __init__(self, settings: Settings, client: Any = None, sleep=time.sleep):
        self.cfg = settings
        self.client = client or make_bedrock_client(settings)
        self._sleep = sleep

    # ------------------------------------------------------------------ one API call
    def converse(self, messages: list[dict], tools: Optional[list] = None) -> dict:
        kwargs: dict[str, Any] = {
            "modelId": self.cfg.model_id,
            "system": [{"text": SYSTEM_PROMPT}],
            "messages": messages,
            "inferenceConfig": {"maxTokens": self.cfg.max_tokens},
        }
        if self.cfg.temperature is not None:
            kwargs["inferenceConfig"]["temperature"] = self.cfg.temperature
        if tools is not False:
            kwargs["toolConfig"] = {"tools": tools or TOOL_SPECS}
        last: Optional[Exception] = None
        for n in range(4):
            try:
                return self.client.converse(**kwargs)
            except Exception as e:  # noqa: BLE001
                code = e.response.get("Error", {}).get("Code", "") if isinstance(e, ClientError) else ""
                if code in TRANSIENT and n < 3:
                    last = e
                    self._sleep(2 ** (n + 1))
                    continue
                raise explain(e, self.cfg.model_id, self.cfg.aws_region) from e
        raise explain(last, self.cfg.model_id, self.cfg.aws_region)  # pragma: no cover

    def ping(self) -> str:
        """Cheap connectivity test used by `ctf-agent check`."""
        resp = self.converse([{"role": "user", "content": [{"text": "Reply with the single word: ok"}]}],
                             tools=False)
        parts = resp["output"]["message"]["content"]
        return "".join(b.get("text", "") for b in parts).strip()

    # --------------------------------------------------------------- one challenge
    def solve(self, challenge: dict, toolbox: Toolbox) -> dict:
        cfg = self.cfg
        cid = challenge["id"]
        messages: list[dict] = [{"role": "user", "content": [{"text": (
            f"Solve CTFd challenge {cid}: \"{challenge['name']}\" "
            f"(category: {challenge.get('category', '?')}, {challenge.get('value', '?')} points). "
            f"You may submit at most {cfg.max_attempts} flags. Start with get_challenge.")}]}]
        transcript: list[dict] = [{"role": "user", "content": _loggable(messages[0]["content"])}]
        usage = {"input": 0, "output": 0}
        outcome, stop_reason, turns, nudged = "max_turns", "", 0, False
        t0 = time.monotonic()
        error = ""

        try:
            for turns in range(1, cfg.max_turns + 1):
                if time.monotonic() - t0 > cfg.challenge_timeout:
                    outcome = "timeout"
                    break
                resp = self.converse(messages)
                u = resp.get("usage", {})
                usage["input"] += u.get("inputTokens", 0)
                usage["output"] += u.get("outputTokens", 0)
                msg = resp["output"]["message"]
                messages.append(msg)  # verbatim: keeps reasoning + toolUse blocks intact
                transcript.append({"turn": turns, "role": "assistant", "content": _loggable(msg["content"])})
                stop_reason = resp.get("stopReason", "")

                if stop_reason == "tool_use":
                    results = []
                    for block in msg["content"]:
                        if "toolUse" not in block:
                            continue
                        tu = block["toolUse"]
                        text, is_err = toolbox.call(tu["name"], tu.get("input") or {})
                        results.append({"toolResult": {
                            "toolUseId": tu["toolUseId"],
                            "content": [{"text": text or "[empty]"}],
                            "status": "error" if is_err else "success"}})
                    messages.append({"role": "user", "content": results})
                    transcript.append({"turn": turns, "role": "user", "content": _loggable(results)})
                    if toolbox.solved:
                        outcome = "solved"
                        break
                    if toolbox.attempts >= cfg.max_attempts:
                        outcome = "out_of_attempts"
                        break
                    continue

                if stop_reason == "max_tokens":
                    messages.append({"role": "user", "content": [{"text": "Your reply was cut off. Continue."}]})
                    continue

                if stop_reason == "end_turn" and not nudged and toolbox.attempts < cfg.max_attempts:
                    nudged = True
                    messages.append({"role": "user", "content": [{"text": (
                        "The challenge is not solved yet. Nobody can answer questions. "
                        "Keep working with the tools, or say you give up.")}]})
                    continue

                outcome = "gave_up"
                break
        except Exception as e:  # noqa: BLE001 - recorded, run continues with next challenge
            outcome, error = "error", str(e)

        return {
            "outcome": outcome,
            "solved": toolbox.solved,
            "attempts": toolbox.attempts,
            "seconds": round(time.monotonic() - t0, 1),
            "turns": turns,
            "tool_calls": toolbox.tool_calls,
            "input_tokens": usage["input"],
            "output_tokens": usage["output"],
            "stop_reason": stop_reason,
            "error": error,
            "transcript": transcript,
        }
