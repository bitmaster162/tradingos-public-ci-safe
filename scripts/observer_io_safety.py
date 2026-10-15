"""Local observer diagnostics: no retry, no scheduler or trading actions.

Final receipt names are published only after a complete flushed staging file.
Staging fragments remain on failure. This is not a power-loss durability claim.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

READ_COMMANDS = frozenset({
    ("rev-parse", "HEAD"),
    ("branch", "--show-current"),
    ("status", "--porcelain=v1", "--untracked-files=all"),
})


class GitReadFailure(RuntimeError):
    def __init__(self, code: str, details: dict):
        self.code, self.details = code, details
        super().__init__(code)


def write_receipt_new(path: Path, value: dict) -> None:
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
    if path.exists() or path.is_symlink():
        raise FileExistsError(errno.EEXIST, "immutable receipt already exists", str(path))
    staging = path.with_name(path.name + ".partial-" + uuid.uuid4().hex)
    # No final empty RESULT.json exists if write/flush/fsync fails.
    with staging.open("xb") as stream:
        count = stream.write(raw)
        if count != len(raw):
            raise OSError(errno.EIO, "short receipt write")
        stream.flush()
        os.fsync(stream.fileno())
    if staging.read_bytes() != raw:
        raise OSError(errno.EIO, "receipt staging readback mismatch")
    # Same-directory hard-link creation is exclusive: no replacement of an old
    # final receipt, including if another writer wins after the initial check.
    os.link(staging, path)
    if path.read_bytes() != raw:
        raise OSError(errno.EIO, "receipt final readback mismatch")
    try:
        staging.unlink()
    except OSError:
        # Publication succeeded. Retaining our second name is not data loss.
        pass


def run_git_read(repo: Path, args: tuple[str, ...], timeout_seconds: float = 30) -> tuple[str, dict]:
    if args not in READ_COMMANDS:
        raise GitReadFailure("GIT_READ_COMMAND_NOT_ALLOWED", {"attempts": 0})
    if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 30:
        raise GitReadFailure("GIT_READ_INVALID_TIMEOUT", {"attempts": 0})
    start = time.monotonic()
    facts = {"operation": list(args), "timeout_seconds": timeout_seconds, "attempts": 1,
             "timed_out": False, "pid": None, "return_code": None,
             "direct_child_reaped": False, "descendants_attested": False}
    stdout = stderr = b""
    capture_complete = False
    failure = None
    try:
        with tempfile.TemporaryFile("w+b") as out, tempfile.TemporaryFile("w+b") as err:
            try:
                process = subprocess.Popen(
                    ["git", "--no-optional-locks", "-C", str(repo), *args],
                    stdin=subprocess.DEVNULL, stdout=out, stderr=err, shell=False)
                facts["pid"] = process.pid
                try:
                    facts["return_code"] = process.wait(timeout=timeout_seconds)
                    facts["direct_child_reaped"] = True
                except subprocess.TimeoutExpired:
                    facts["timed_out"] = True
                    failure = "GIT_READ_TIMEOUT"
                    try:
                        process.kill()
                        facts["return_code"] = process.wait(timeout=5)
                        facts["direct_child_reaped"] = True
                    except (OSError, subprocess.TimeoutExpired) as cleanup:
                        facts["cleanup_error_type"] = type(cleanup).__name__
                        failure = "GIT_READ_CLEANUP_UNCONFIRMED"
            except OSError as exc:
                failure = "GIT_READ_OS_ERROR"
                facts.update(errno=exc.errno, winerror=getattr(exc, "winerror", None))
            out.seek(0)
            err.seek(0)
            stdout, stderr = out.read(), err.read()
            capture_complete = True
    except OSError as exc:
        failure = failure or "GIT_DIAGNOSTIC_STORAGE_FAILED"
        facts.update(diagnostic_errno=exc.errno, diagnostic_winerror=getattr(exc, "winerror", None))
    facts.update(elapsed_seconds=round(time.monotonic() - start, 6),
                 output_capture_complete=capture_complete,
                 stdout_bytes=len(stdout) if capture_complete else None,
                 stderr_bytes=len(stderr) if capture_complete else None,
                 stdout_sha256=hashlib.sha256(stdout).hexdigest() if capture_complete else None,
                 stderr_sha256=hashlib.sha256(stderr).hexdigest() if capture_complete else None)
    if failure is None and facts["return_code"] != 0:
        failure = "GIT_READ_NONZERO"
    if failure is not None:
        facts["result"] = failure
        raise GitReadFailure(failure, facts)
    try:
        text = stdout.decode("utf-8").strip()
    except UnicodeError as exc:
        facts["result"] = "GIT_READ_OUTPUT_ENCODING"
        raise GitReadFailure("GIT_READ_OUTPUT_ENCODING", facts) from exc
    facts["result"] = "PASS"
    return text, facts
