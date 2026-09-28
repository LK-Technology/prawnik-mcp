#!/usr/bin/env python3
"""Scan files for personal data before they are committed or published.

Detects: PESEL numbers (with checksum), Polish bank account numbers / IBANs (mod-97 check),
Polish phone numbers, e-mail addresses, plus any string listed in a local, never-committed
denylist file (`.pii-denylist`, one entry per line, e.g. names of parties in a private case).

Usage:
    python scripts/pii_scan.py                # scan files tracked by git (or the repo tree)
    python scripts/pii_scan.py path [path…]   # scan given files/directories

Exit code 1 if anything is found. Known-public values (e.g. court e-mails in public
judgments used as fixtures) can be allowed in `scripts/pii_allowlist.txt`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "data", "__pycache__", ".pytest_cache", ".ruff_cache", "dist", "build"}
TEXT_EXT = {".py", ".md", ".txt", ".json", ".toml", ".yml", ".yaml", ".html", ".xhtml", ".csv", ".cfg", ".ini"}
SAFE_EMAIL_DOMAINS = ("example.invalid", "example.com", "example.org", "anthropic.com")

PESEL_RE = re.compile(r"(?<!\d)(\d{11})(?!\d)")
IBAN_RE = re.compile(r"(?<![A-Z0-9])(?:PL)?\s?(\d{2}(?:\s?\d{4}){6})(?!\d)")
PHONE_RE = re.compile(r"(?<![\d+])(?:\+48[\s-]?)?(\d{3}[\s-]?\d{3}[\s-]?\d{3})(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def pesel_ok(s: str) -> bool:
    w = [1, 3, 7, 9, 1, 3, 7, 9, 1, 3]
    if int(s[2:4]) % 20 == 0 or int(s[2:4]) % 20 > 12 or not 1 <= int(s[4:6]) <= 31:
        return False
    return (10 - sum(int(s[i]) * w[i] for i in range(10)) % 10) % 10 == int(s[10])


def nrb_ok(digits: str) -> bool:
    d = re.sub(r"\D", "", digits)
    if len(d) != 26:
        return False
    rearranged = d[2:] + "2521" + d[:2]  # "PL" -> 25 21
    return int(rearranged) % 97 == 1


def load_list(p: Path) -> list[str]:
    if not p.exists():
        return []
    return [line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]


def candidate_files(args: list[str]) -> list[Path]:
    if args:
        out: list[Path] = []
        for a in args:
            p = Path(a)
            out += [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()]
        return out
    try:
        res = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True)
        return [ROOT / f for f in res.stdout.splitlines()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [f for f in ROOT.rglob("*") if f.is_file() and not (set(f.relative_to(ROOT).parts) & SKIP_DIRS)]


def extract_text(p: Path) -> str:
    if p.suffix.lower() in TEXT_EXT:
        return p.read_text(encoding="utf-8", errors="ignore")
    if p.suffix.lower() == ".pdf":
        try:
            import pypdf

            return "\n".join(pg.extract_text() or "" for pg in pypdf.PdfReader(str(p)).pages)
        except Exception:
            return ""
    if p.suffix.lower() == ".docx":
        try:
            import docx

            return "\n".join(par.text for par in docx.Document(str(p)).paragraphs)
        except Exception:
            return ""
    return ""


FORBIDDEN = ("data/", ".playwright-mcp/", ".pii-denylist", ".env")


def scan(files: list[Path]) -> list[str]:
    deny = load_list(ROOT / ".pii-denylist")
    allow = set(load_list(ROOT / "scripts" / "pii_allowlist.txt"))
    findings: list[str] = []
    for f in files:
        rel = f.relative_to(ROOT) if f.is_relative_to(ROOT) else f
        if str(rel).startswith(FORBIDDEN):
            findings.append(f"{rel}: forbidden path (private data or tool artifacts must never be committed)")
            continue
        if set(Path(rel).parts) & SKIP_DIRS or f.name in (".pii-denylist",):
            continue
        if any(str(rel).startswith(a[5:]) for a in allow if a.startswith("path:")):
            continue
        if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif"}:
            findings.append(f"{rel}: image file (cannot be scanned; review manually)")
            continue
        text = extract_text(f)
        if not text:
            continue
        for m in PESEL_RE.finditer(text):
            if pesel_ok(m.group(1)) and m.group(1) not in allow:
                findings.append(f"{rel}: PESEL-like number {m.group(1)[:4]}…")
        for m in IBAN_RE.finditer(text):
            if nrb_ok(m.group(1)) and re.sub(r"\D", "", m.group(1)) not in allow:
                findings.append(f"{rel}: bank account number …{re.sub(r'\D', '', m.group(1))[-4:]}")
        for m in EMAIL_RE.finditer(text):
            e = m.group(0)
            if not e.lower().endswith(SAFE_EMAIL_DOMAINS) and e not in allow:
                findings.append(f"{rel}: e-mail {e.split('@')[0][:2]}…@{e.split('@')[1]}")
        for m in PHONE_RE.finditer(text):
            if "+48" in m.group(0) and re.sub(r"\D", "", m.group(0)) not in allow:
                findings.append(f"{rel}: phone number …{re.sub(r'\D', '', m.group(0))[-3:]}")
        low = text.lower()
        for d in deny:
            if d.lower() in low:
                findings.append(f"{rel}: denylisted term #{deny.index(d) + 1}")
    return findings


def main() -> int:
    findings = scan(candidate_files(sys.argv[1:]))
    for line in findings:
        print(line)
    print(f"pii_scan: {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
