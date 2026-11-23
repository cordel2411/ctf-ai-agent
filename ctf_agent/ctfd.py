"""CTFd REST client plus the helpers that bootstrap a fresh CTFd instance.

Verified against CTFd 3.8.x:
  * /setup and /login are form posts protected by a hidden "nonce" field.
  * Session-authenticated API calls need a "CSRF-Token" header; calls that use
    "Authorization: Token <token>" do not.
  * Admin tokens can use the whole admin API, so after logging in once as admin we
    mint a token and use token auth for everything else.
"""
from __future__ import annotations

import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import requests

NONCE_RE = re.compile(r'<input[^>]*name="nonce"[^>]*value="([^"]+)"')
CSRF_RE = re.compile(r"""['"]csrfNonce['"]\s*:\s*"([^"]+)\"""")


class CTFdError(RuntimeError):
    pass


# --------------------------------------------------------------------------- client
class CTFdClient:
    """JSON API client using token auth (or an already-authenticated session)."""

    def __init__(self, base_url: str, token: str = "", timeout: int = 30,
                 session: Optional[requests.Session] = None,
                 csrf_token: str = ""):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.s = session or requests.Session()
        self.s.headers["Content-Type"] = "application/json"
        if token:
            self.s.headers["Authorization"] = f"Token {token}"
        if csrf_token:
            self.s.headers["CSRF-Token"] = csrf_token

    # -- low level
    def request(self, method: str, path: str, **kw: Any) -> requests.Response:
        url = path if path.startswith("http") else self.base + path
        r = None
        for n in range(6):
            r = self.s.request(method, url, timeout=self.timeout, **kw)
            if r.status_code != 429:
                return r
            wait = r.headers.get("Retry-After")
            time.sleep(float(wait) if wait and wait.isdigit() else min(5 * (n + 1), 30))
        return r  # still rate limited

    def api(self, method: str, path: str, **kw: Any) -> dict:
        r = self.request(method, "/api/v1" + path, **kw)
        try:
            body = r.json()
        except ValueError:
            raise CTFdError(f"{method} {path}: HTTP {r.status_code}, not JSON: {r.text[:200]!r}")
        if r.status_code >= 400 or body.get("success") is False:
            detail = body.get("errors") or body.get("message") or body
            raise CTFdError(f"{method} {path}: HTTP {r.status_code}: {detail}")
        return body

    # -- player API
    def whoami(self) -> dict:
        return self.api("GET", "/users/me")["data"]

    def challenges(self) -> list[dict]:
        return self.api("GET", "/challenges")["data"]

    def challenge(self, challenge_id: int) -> dict:
        return self.api("GET", f"/challenges/{int(challenge_id)}")["data"]

    def attempt(self, challenge_id: int, submission: str) -> dict:
        """Returns {"status": correct|incorrect|already_solved|ratelimited|paused, "message": str}."""
        r = self.request("POST", "/api/v1/challenges/attempt",
                         json={"challenge_id": int(challenge_id), "submission": submission})
        if r.status_code == 429:
            return {"status": "ratelimited", "message": "Too many submissions, slow down."}
        try:
            body = r.json()
        except ValueError:
            raise CTFdError(f"attempt: HTTP {r.status_code}, not JSON: {r.text[:200]!r}")
        if r.status_code >= 400 and "data" not in body:
            raise CTFdError(f"attempt: HTTP {r.status_code}: {body}")
        data = body.get("data") or {}
        return {"status": data.get("status", "unknown"), "message": data.get("message", "")}

    def download(self, path_or_url: str, dest: Path) -> Path:
        url = path_or_url if path_or_url.startswith("http") else self.base + path_or_url
        # never send our token to some other host
        if urlparse(url).netloc != urlparse(self.base).netloc:
            raise CTFdError("refusing to download from a different host than CTFd")
        r = self.request("GET", url)
        if r.status_code != 200:
            raise CTFdError(f"download failed: HTTP {r.status_code}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return dest

    # -- admin API
    def find_challenge(self, name: str) -> Optional[dict]:
        rows = self.api("GET", "/challenges", params={"view": "admin", "name": name})["data"]
        for row in rows:
            if row["name"] == name:
                return row
        return None

    def create_challenge(self, name: str, category: str, description: str,
                         value: int, flag: str) -> dict:
        chal = self.api("POST", "/challenges", json={
            "name": name, "category": category, "description": description,
            "value": value, "state": "visible", "type": "standard",
        })["data"]
        self.api("POST", "/flags", json={
            "challenge_id": chal["id"], "content": flag, "type": "static", "data": "",
        })
        return chal

    def delete_challenge(self, challenge_id: int) -> None:
        self.api("DELETE", f"/challenges/{int(challenge_id)}")

    def find_user(self, name: str) -> Optional[dict]:
        rows = self.api("GET", "/users", params={"field": "name", "q": name})["data"]
        for row in rows:
            if row["name"] == name:
                return row
        return None

    def create_user(self, name: str, email: str, password: str) -> dict:
        return self.api("POST", "/users", params={"notify": "false"}, json={
            "name": name, "email": email, "password": password,
            "type": "user", "verified": True, "hidden": False, "banned": False,
        })["data"]

    def submissions_for_user(self, user_id: int) -> list[dict]:
        out, page = [], 1
        while True:
            body = self.api("GET", "/submissions", params={"user_id": user_id, "page": page})
            out.extend(body["data"])
            meta = (body.get("meta") or {}).get("pagination") or {}
            if not meta.get("next"):
                return out
            page = meta["next"]

    def delete_submission(self, submission_id: int) -> None:
        self.api("DELETE", f"/submissions/{int(submission_id)}")


# ------------------------------------------------------------------- bootstrapping
def wait_for_ctfd(base: str, timeout: int = 300) -> None:
    deadline, last = time.time() + timeout, "no response"
    while time.time() < deadline:
        try:
            r = requests.get(base + "/", timeout=5, allow_redirects=False)
            if r.status_code in (200, 301, 302):
                return
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = type(e).__name__
        time.sleep(3)
    raise CTFdError(f"CTFd not reachable at {base} after {timeout}s ({last}). "
                    "Is the container running? Try: docker compose ps / docker compose logs ctfd")


def is_setup(base: str) -> bool:
    """CTFd redirects /setup to / once the setup wizard has been completed."""
    r = requests.get(base + "/setup", timeout=15, allow_redirects=False)
    return r.status_code in (301, 302)


def _nonce(html: str) -> str:
    m = NONCE_RE.search(html)
    if not m:
        raise CTFdError("could not find the CSRF nonce on the page (unexpected CTFd version?)")
    return m.group(1)


def run_setup(base: str, ctf_name: str, admin_name: str, admin_email: str,
              admin_password: str) -> None:
    s = requests.Session()
    r = s.get(base + "/setup", timeout=15)
    nonce = _nonce(r.text)
    form = {
        "ctf_name": ctf_name,
        "ctf_description": "Capture-the-flag lab used to benchmark an AI agent against students.",
        "user_mode": "users",
        "challenge_visibility": "private",
        "account_visibility": "public",
        "score_visibility": "public",
        "registration_visibility": "public",
        "ctf_theme": "core",
        "name": admin_name,
        "email": admin_email,
        "password": admin_password,
        "nonce": nonce,
    }
    r = s.post(base + "/setup", data=form, allow_redirects=False, timeout=120)
    if r.status_code not in (301, 302):
        errs = re.findall(r'<div class="alert[^>]*>(.*?)</div>', r.text, flags=re.S)
        msg = " ".join(re.sub(r"<[^>]+>|\s+", " ", e).strip() for e in errs) or f"HTTP {r.status_code}"
        raise CTFdError(f"CTFd setup failed: {msg}")


def login_session(base: str, name: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.get(base + "/login", timeout=15)
    nonce = _nonce(r.text)
    r = s.post(base + "/login", data={"name": name, "password": password,
                                      "_submit": "Submit", "nonce": nonce},
               allow_redirects=False, timeout=30)
    if r.status_code not in (301, 302):
        raise CTFdError(f"login as {name!r} failed (wrong password?)")
    return s


def create_token(base: str, session: requests.Session, description: str, days: int = 365) -> str:
    page = session.get(base + "/settings", timeout=15)
    m = CSRF_RE.search(page.text)
    if not m:
        raise CTFdError("could not read the session CSRF nonce from /settings")
    exp = (date.today() + timedelta(days=days)).isoformat()
    r = session.post(base + "/api/v1/tokens",
                     json={"expiration": exp, "description": description},
                     headers={"CSRF-Token": m.group(1), "Content-Type": "application/json"},
                     timeout=30)
    try:
        body = r.json()
    except ValueError:
        raise CTFdError(f"token creation: HTTP {r.status_code}: {r.text[:200]!r}")
    if not body.get("success"):
        raise CTFdError(f"token creation failed: {body}")
    return body["data"]["value"]
