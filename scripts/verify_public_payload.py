"""Fail-closed static check for the minimal public CI payload."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = frozenset({
    ".github/workflows/public-ci.yml",
    ".gitattributes",
    ".gitignore",
    "README.md",
    "scripts/observer_io_safety.py",
    "scripts/verify_public_payload.py",
    "tests/test_public_io_safety.py",
})
HELPER_SHA256 = "8684e892fe3d59d9cefdfb29fa8bfba8e2151580484d65b760180eab6bb60f8e"
SKIP_PARTS = frozenset({".git", "__pycache__", ".pytest_cache"})
SENSITIVE_PATTERNS = [
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"gh[pousr]_[a-zA-Z0-9]{20,}"),
    re.compile(rb"github_pat_[a-zA-Z0-9_]{20,}"),
    re.compile(rb"xox[baprs]-[a-zA-Z0-9-]{20,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
]


def main() -> None:
    files = {
        p.relative_to(ROOT).as_posix(): p
        for p in ROOT.rglob("*")
        if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_PARTS)
    }
    if set(files) != ALLOWED:
        raise SystemExit(f"PUBLIC_FILE_ALLOWLIST_DRIFT: {sorted(set(files) ^ ALLOWED)}")
    digest = hashlib.sha256(files["scripts/observer_io_safety.py"].read_bytes()).hexdigest()
    if digest != HELPER_SHA256:
        raise SystemExit("PUBLIC_HELPER_SHA256_DRIFT")
    for path, file in sorted(files.items()):
        raw = file.read_bytes()
        for pattern in SENSITIVE_PATTERNS:
            if pattern.search(raw):
                raise SystemExit(f"PUBLIC_SECRET_PATTERN_HOLD:{path}")
    print(f"PUBLIC_SUBSET_VERIFIED files={len(files)} helper_sha256={digest}")


if __name__ == "__main__":
    main()
