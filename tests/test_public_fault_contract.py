"""Public synthetic fault matrix for isolated I/O helper. No network access."""
from __future__ import annotations

import errno
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import observer_io_safety as io


class PublicFaultContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="public-fault-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.final = self.root / "RESULT.json"

    def test_concurrent_final_wins_without_overwrite(self):
        original_link = io.os.link

        def competing_writer(source, dest):
            Path(dest).write_bytes(b"another complete receipt")
            return original_link(source, dest)

        with patch.object(io.os, "link", side_effect=competing_writer):
            with self.assertRaises(FileExistsError):
                io.write_receipt_new(self.final, {"status": "proposed"})
        self.assertEqual(self.final.read_bytes(), b"another complete receipt")
        self.assertEqual(len(list(self.root.glob("*.partial-*"))), 1)

    def test_hardlink_not_supported_fails_closed(self):
        unsupported = getattr(errno, "EOPNOTSUPP", errno.EINVAL)
        with patch.object(io.os, "link", side_effect=OSError(unsupported, "synthetic")):
            with self.assertRaises(OSError):
                io.write_receipt_new(self.final, {"status": "candidate"})
        self.assertFalse(self.final.exists())
        self.assertEqual(json.loads(next(self.root.glob("*.partial-*")).read_text()),
                         {"status": "candidate"})

    def test_nonzero_child_records_hashes_but_not_raw_stderr(self):
        actual = subprocess.Popen

        def synthetic_child(command, **kwargs):
            code = ("import sys; "
                    "sys.stdout.buffer.write(b'public-output'); "
                    "sys.stderr.buffer.write(b'synthetic-error'); sys.exit(7)")
            return actual([sys.executable, "-B", "-c", code], **kwargs)

        with patch.object(io.subprocess, "Popen", side_effect=synthetic_child) as spy:
            with self.assertRaises(io.GitReadFailure) as raised:
                io.run_git_read(self.root, ("rev-parse", "HEAD"))
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(raised.exception.code, "GIT_READ_NONZERO")
        facts = raised.exception.details
        self.assertEqual(facts["return_code"], 7)
        self.assertTrue(facts["direct_child_reaped"])
        self.assertEqual(facts["stdout_sha256"], hashlib.sha256(b"public-output").hexdigest())
        self.assertEqual(facts["stderr_sha256"], hashlib.sha256(b"synthetic-error").hexdigest())
        self.assertNotIn("synthetic-error", json.dumps(facts))

    def test_diagnostic_storage_failure_never_launches_child(self):
        with patch.object(io.tempfile, "TemporaryFile",
                          side_effect=OSError(errno.ENOSPC, "synthetic")), \
             patch.object(io.subprocess, "Popen") as spawn:
            with self.assertRaises(io.GitReadFailure) as raised:
                io.run_git_read(self.root, ("rev-parse", "HEAD"))
        spawn.assert_not_called()
        self.assertEqual(raised.exception.code, "GIT_DIAGNOSTIC_STORAGE_FAILED")
        self.assertIsNone(raised.exception.details["stdout_sha256"])
        self.assertIsNone(raised.exception.details["pid"])
        self.assertFalse(raised.exception.details["output_capture_complete"])

    def test_unconfirmed_cleanup_does_not_claim_success(self):
        child = Mock(pid=9999)
        child.wait.side_effect = subprocess.TimeoutExpired(["synthetic"], 30)
        with patch.object(io.subprocess, "Popen", return_value=child) as spawn:
            with self.assertRaises(io.GitReadFailure) as raised:
                io.run_git_read(self.root, ("rev-parse", "HEAD"), timeout_seconds=0.1)
        spawn.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(raised.exception.code, "GIT_READ_CLEANUP_UNCONFIRMED")
        self.assertTrue(raised.exception.details["timed_out"])
        self.assertFalse(raised.exception.details["direct_child_reaped"])
        self.assertFalse(raised.exception.details["descendants_attested"])

    def test_invalid_utf8_cannot_pass_git_identity(self):
        actual = subprocess.Popen

        def invalid_child(command, **kwargs):
            return actual([sys.executable, "-B", "-c",
                           "import os; os.write(1, bytes([255]))"], **kwargs)

        with patch.object(io.subprocess, "Popen", side_effect=invalid_child):
            with self.assertRaises(io.GitReadFailure) as raised:
                io.run_git_read(self.root, ("rev-parse", "HEAD"))
        self.assertEqual(raised.exception.code, "GIT_READ_OUTPUT_ENCODING")
        self.assertEqual(raised.exception.details["stdout_bytes"], 1)
        self.assertTrue(raised.exception.details["output_capture_complete"])
        self.assertTrue(raised.exception.details["direct_child_reaped"])


if __name__ == "__main__":
    unittest.main()
