"""Reference solutions (as Python source) and a fake Bedrock model, used only by the tests."""
from __future__ import annotations

import json
import re

CAESAR = '''
ct = {ct!r}
for k in range(26):
    pt = "".join(
        chr((ord(c) - (65 if c.isupper() else 97) - k) % 26 + (65 if c.isupper() else 97)) if c.isalpha() else c
        for c in ct)
    if pt.startswith("flag{{"):
        print(pt)
'''

ONION = '''
import base64
print(bytes.fromhex(base64.b64decode({ct!r}).decode()).decode()[::-1])
'''

XOR = '''
data = bytes.fromhex({ct!r})
for k in range(256):
    pt = bytes(b ^ k for b in data)
    if pt.startswith(b"flag{{"):
        print(pt.decode())
'''

VIGENERE = '''
ct, key, out, i = {ct!r}, "ctfd", [], 0
for c in ct:
    if c.isalpha():
        base = 65 if c.isupper() else 97
        out.append(chr((ord(c) - base - (ord(key[i % 4]) - 97)) % 26 + base)); i += 1
    else:
        out.append(c)
print("".join(out))
'''

RAIL = '''
ct, rails = {ct!r}, 3
n = len(ct)
pattern, row, step = [], 0, 1
for _ in range(n):
    pattern.append(row)
    if row == 0: step = 1
    elif row == rails - 1: step = -1
    row += step
order = sorted(range(n), key=lambda i: (pattern[i], i))
res = [""] * n
for pos, idx in enumerate(order):
    res[idx] = ct[pos]
print("".join(res))
'''

RSA = '''
n, e, c = {n}, {e}, {c}
p = next(i for i in range(2, n) if n % i == 0)
q = n // p
d = pow(e, -1, (p - 1) * (q - 1))
print("".join(chr(pow(x, d, n)) for x in c))
'''


def solver_code(name: str, description: str) -> str:
    first_line = re.search(r"\n    (\S.*)\n", description).group(1)
    if name == "Caesar's Salad":
        return CAESAR.format(ct=first_line)
    if name == "Onion Layers":
        return ONION.format(ct=first_line)
    if name == "One Byte Key":
        return XOR.format(ct=first_line)
    if name == "Vigenere Vault":
        return VIGENERE.format(ct=first_line)
    if name == "Rail Fence":
        return RAIL.format(ct=first_line)
    if name == "Tiny RSA":
        n = int(re.search(r"n = (\d+)", description).group(1))
        e = int(re.search(r"e = (\d+)", description).group(1))
        c = json.loads(re.search(r"c = (\[.*?\])", description).group(1))
        return RSA.format(n=n, e=e, c=c)
    raise KeyError(name)


class ScriptedModel:
    """Stands in for boto3's bedrock-runtime client. Behaves like a competent solver:
    get_challenge -> run_python (reference solution) -> one wrong guess -> correct flag.
    It also validates the request shape the way Bedrock would."""

    def __init__(self):
        self.calls = 0

    @staticmethod
    def _validate(kwargs):
        msgs = kwargs["messages"]
        assert msgs[0]["role"] == "user"
        for a, b in zip(msgs, msgs[1:]):
            assert a["role"] != b["role"], "roles must alternate"
        pending = set()
        for m in msgs:
            for blk in m["content"]:
                if "text" in blk:
                    assert blk["text"].strip(), "empty text block"
                if "toolUse" in blk:
                    pending.add(blk["toolUse"]["toolUseId"])
                if "toolResult" in blk:
                    pending.discard(blk["toolResult"]["toolUseId"])
                    assert blk["toolResult"]["content"][0]["text"]
        assert kwargs["modelId"] and kwargs["toolConfig"]["tools"]

    def converse(self, **kwargs):
        self.calls += 1
        self._validate(kwargs)
        msgs = kwargs["messages"]
        k = sum(1 for m in msgs if m["role"] == "assistant")
        first = msgs[0]["content"][0]["text"]
        cid = int(re.search(r"challenge (\d+)", first).group(1))
        name = re.search(r'"([^"]+)"', first).group(1)

        def tool(name_, inp):
            return {"output": {"message": {"role": "assistant", "content": [
                {"text": "working on it"},
                {"toolUse": {"toolUseId": f"tu-{k}", "name": name_, "input": inp}}]}},
                "stopReason": "tool_use", "usage": {"inputTokens": 100, "outputTokens": 20}}

        last = lambda: msgs[-1]["content"][0]["toolResult"]["content"][0]["text"]  # noqa: E731
        if k == 0:
            return tool("get_challenge", {"challenge_id": cid})
        if k == 1:
            return tool("run_python", {"code": solver_code(name, json.loads(last())["description"])})
        if k == 2:
            self.answer = last().strip().splitlines()[-1]
            return tool("submit_flag", {"challenge_id": cid, "flag": "flag{first_guess}"})
        if k == 3:
            return tool("submit_flag", {"challenge_id": cid, "flag": self.answer})
        return {"output": {"message": {"role": "assistant", "content": [{"text": "done"}]}},
                "stopReason": "end_turn", "usage": {"inputTokens": 1, "outputTokens": 1}}
