"""Six self-contained crypto challenges. Ciphertexts are computed here, so they always
match the flag stored in CTFd. Everything the solver needs is in the description, so the
agent needs no file downloads or network access beyond CTFd itself."""
from __future__ import annotations

import base64
from dataclasses import dataclass


@dataclass(frozen=True)
class ChallengeSpec:
    name: str
    category: str
    value: int
    description: str
    flag: str


def _is_letter(ch: str) -> bool:
    return "a" <= ch.lower() <= "z" and ch.isascii()


def caesar(text: str, shift: int) -> str:
    out = []
    for ch in text:
        if _is_letter(ch):
            base = ord("A") if ch.isupper() else ord("a")
            out.append(chr((ord(ch) - base + shift) % 26 + base))
        else:
            out.append(ch)
    return "".join(out)


def vigenere(text: str, key: str) -> str:
    out, i = [], 0
    for ch in text:
        if _is_letter(ch):
            base = ord("A") if ch.isupper() else ord("a")
            k = ord(key[i % len(key)].lower()) - ord("a")
            out.append(chr((ord(ch) - base + k) % 26 + base))
            i += 1
        else:
            out.append(ch)
    return "".join(out)


def rail_fence(text: str, rails: int) -> str:
    rows = [[] for _ in range(rails)]
    row, step = 0, 1
    for ch in text:
        rows[row].append(ch)
        if row == 0:
            step = 1
        elif row == rails - 1:
            step = -1
        row += step
    return "".join("".join(r) for r in rows)


def build_challenges() -> list[ChallengeSpec]:
    specs: list[ChallengeSpec] = []

    flag = "flag{et_tu_brute}"
    specs.append(ChallengeSpec(
        "Caesar's Salad", "Crypto", 100,
        "A senator slipped us this note before the senate meeting:\n\n"
        f"    {caesar(flag, 7)}\n\n"
        "The shift is unknown. The flag format is flag{...}.",
        flag))

    flag = "flag{layers_like_an_onion}"
    layered = base64.b64encode(flag[::-1].encode().hex().encode()).decode()
    specs.append(ChallengeSpec(
        "Onion Layers", "Crypto", 100,
        "Somebody wrapped the flag in several layers of encoding. None of them is encryption.\n\n"
        f"    {layered}\n\n"
        "The flag format is flag{...}.",
        flag))

    flag = "flag{xor_is_reversible}"
    key = 0x5A
    xored = bytes(b ^ key for b in flag.encode()).hex()
    specs.append(ChallengeSpec(
        "One Byte Key", "Crypto", 200,
        "The flag was XORed with a single secret byte and printed as hex:\n\n"
        f"    {xored}\n\n"
        "The flag format is flag{...}.",
        flag))

    flag = "flag{vigenere_is_not_unbreakable}"
    specs.append(ChallengeSpec(
        "Vigenere Vault", "Crypto", 200,
        "This message was encrypted with a Vigenere cipher. Only letters were encrypted; "
        "digits and punctuation were left alone and did not advance the key. "
        "The key is the four-letter name of the platform that hosts this challenge "
        "(letters only, case-insensitive).\n\n"
        f"    {vigenere(flag, 'CTFD')}\n\n"
        "The flag format is flag{...}.",
        flag))

    flag = "flag{zig_zag_rails}"
    specs.append(ChallengeSpec(
        "Rail Fence", "Crypto", 200,
        "This is a rail fence (zig-zag) transposition cipher with 3 rails, read row by row:\n\n"
        f"    {rail_fence(flag, 3)}\n\n"
        "The flag format is flag{...}.",
        flag))

    flag = "flag{small_primes_fall}"
    p, q, e = 2003, 2011, 65537
    n = p * q
    cts = [pow(ord(ch), e, n) for ch in flag]
    specs.append(ChallengeSpec(
        "Tiny RSA", "Crypto", 300,
        "Textbook RSA with a very small modulus. Each character of the flag was encrypted "
        "separately as its ASCII code.\n\n"
        f"    n = {n}\n    e = {e}\n    c = {cts}\n\n"
        "The flag format is flag{...}.",
        flag))

    return specs


SEED_NAMES = [c.name for c in build_challenges()]
