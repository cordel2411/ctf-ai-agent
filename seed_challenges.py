#!/usr/bin/env python3
"""Create 4 practice challenges in CTFd via the admin API (idempotent)."""
import base64, os, sys
import requests
from dotenv import load_dotenv

load_dotenv()
BASE = os.getenv("CTFD_URL", "http://localhost:8000").rstrip("/")
TOKEN = os.getenv("CTFD_ADMIN_TOKEN")
if not TOKEN:
    sys.exit("Set CTFD_ADMIN_TOKEN in .env (admin Settings > Access Tokens)")
H = {"Authorization": f"Token {TOKEN}", "Content-Type": "application/json"}


def caesar(text, k):
    out = []
    for c in text:
        if c.islower():
            out.append(chr((ord(c) - 97 + k) % 26 + 97))
        else:
            out.append(c)
    return "".join(out)


def make():
    a, b, c, d = "warmup_base64_works", "caesar_shift_seven", "hex_is_just_bytes", "layers_of_encoding"
    return [
        dict(name="Warmup: Base64", category="Crypto", value=100, secret=a,
             description="A friend sent this message. Decode it and wrap it as the flag.\n\n"
                         f"{base64.b64encode(a.encode()).decode()}"),
        dict(name="Hex Is Just Bytes", category="Crypto", value=100, secret=c,
             description="Convert these hex bytes to text; the result goes inside FGCU{...}.\n\n"
                         f"{c.encode().hex()}"),
        dict(name="Caesar's Visit", category="Crypto", value=200, secret=b,
             description="Julius left a note. Each letter was moved forward by a fixed amount "
                         "(underscores untouched). Recover it.\n\n" + caesar(b, 7)),
        dict(name="Layers", category="Crypto", value=300, secret=d,
             description="The secret was Base64-encoded, then that result was written as hex. "
                         "Peel both layers.\n\n" + base64.b64encode(d.encode()).hex()),
    ]


def main():
    existing = {c["name"] for c in requests.get(f"{BASE}/api/v1/challenges", headers=H, timeout=20).json()["data"]}
    for ch in make():
        if ch["name"] in existing:
            print("exists:", ch["name"]); continue
        r = requests.post(f"{BASE}/api/v1/challenges", headers=H, timeout=20, json=dict(
            name=ch["name"], category=ch["category"], description=ch["description"],
            value=ch["value"], state="visible", type="standard"))
        r.raise_for_status()
        cid = r.json()["data"]["id"]
        requests.post(f"{BASE}/api/v1/flags", headers=H, timeout=20, json=dict(
            challenge_id=cid, content="FGCU{" + ch["secret"] + "}", type="static", data="")).raise_for_status()
        print("created:", ch["name"], "id", cid)


if __name__ == "__main__":
    main()
