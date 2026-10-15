"""Synthetic standalone safety tests. Never use external APIs."""
from __future__ import annotations

import errno
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import observer_io_safety as io


class PublicIOSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="public-io-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.final = self.root / "RESULT.json"

    def test_complete_receipt_and_no_staging(self):
        io.write_receipt_new(self.final, {"status": "PASS", "can_trade": False})
        self.assertIs(json.loads(self.final.read_text())["can_trade"], False)
        self.assertEqual(list(self.root.glob("*.partial-*")), [])

    def test_existing_final_is_immutable(self):
        self.final.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            io.write_receipt_new(self.final, {"status": "replacement"})
        self.assertEqual(self.final.read_bytes(), b"original")

    def test_nonfinite_value_rejected_without_files(self):
        with self.assertRaises(ValueError):
            io.write_receipt_new(self.final, {"x": float("nan")})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_failed_fsync_cannot_publish_final(self):
        with patch.object(io.os, "fsync", side_effect=OSError(errno.ENOSPC, "synthetic")):
            with self.assertRaises(OSError):
                io.write_receipt_new(self.final, {"x": 1})
        self.assertFalse(self.final.exists())
        self.assertEqual(len(list(self.root.glob("*.partial-*"))), 1)

    def test_failed_hardlink_retains_staging(self):
        with patch.object(io.os, "link", side_effect=OSError(errno.ENOSPC, "synthetic")):
            with self.assertRaises(OSError):
                io.write_receipt_new(self.final, {"x": 1})
        self.assertFalse(self.final.exists())
        partial = next(self.root.glob("*.partial-*"))
        self.assertEqual(json.loads(partial.read_text()), {"x": 1})

    def test_non_read_git_commands_rejected(self):
        for operation in (("push", "origin"), ("fetch", "origin"), ("reset", "--hard")):
            with self.subTest(operation=operation), patch.object(io.subprocess, "Popen") as spawn:
                with self.assertRaises(io.GitReadFailure) as exc:
                    io.run_git_read(self.root, operation)
                self.assertEqual(exc.exception.code, "GIT_READ_COMMAND_NOT_ALLOWED")
                spawn.assert_not_called()

    def test_excessive_timeout_rejected(self):
        for delay in (31, 0, -1, True):
            with self.subTest(delay=delay), patch.object(io.subprocess, "Popen") as spawn:
                with self.assertRaises(io.GitReadFailure):
                    io.run_git_read(self.root, ("rev-parse", "HEAD"), delay)
                spawn.assert_not_called()

    def test_real_local_git_identity(self):
        git = shutil.which("git")
        if not git:
            self.skipTest("Git not installed")
        subprocess.run([git, "init", "-q", str(self.root)], check=True, timeout=15,
                       stdout=subprocess.DEVNULL)
        subprocess.run([git, "-C", str(self.root), "-c", "user.name=synthetic",
                        "-c", "user.email=synthetic@invalid.example", "commit",
                        "--allow-empty", "-q", "-m", "synthetic fixture"], check=True, timeout=15)
        head, facts = io.run_git_read(self.root, ("rev-parse", "HEAD"))
        self.assertRegex(head, r"^[0-9a-f]{40}$")
        self.assertEqual(facts["attempts"], 1)
        self.assertTrue(facts["direct_child_reaped"])
        self.assertTrue(facts["output_capture_complete"])

    def test_non_repository_git_failure(self):
        if not shutil.which("git"):
            self.skipTest("Git not installed")
        with self.assertRaises(io.GitReadFailure) as exc:
            io.run_git_read(self.root, ("rev-parse", "HEAD"))
        self.assertEqual(exc.exception.code, "GIT_READ_NONZERO")
        self.assertNotEqual(exc.exception.details["return_code"], 0)

    def test_real_timeout_reaps_direct_child(self):
        actual = subprocess.Popen
        def synthetic_popen(command, **kwargs):
            return actual([sys.executable, "-B", "-c", "import time; time.sleep(5)"], **kwargs)
        with patch.object(io.subprocess, "Popen", side_effect=synthetic_popen) as spawn:
            with self.assertRaises(io.GitReadFailure) as exc:
                io.run_git_read(self.root, ("rev-parse", "HEAD"), timeout_seconds=0.15)
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(exc.exception.code, "GIT_READ_TIMEOUT")
        self.assertTrue(exc.exception.details["timed_out"])
        self.assertTrue(exc.exception.details["direct_child_reaped"])
        self.assertFalse(exc.exception.details["descendants_attested"])

    def test_stdout_and_stderr_separated(self):
        actual = subprocess.Popen
        def synthetic_popen(command, **kwargs):
            return actual([sys.executable, "-B", "-c",
                           "import sys; print('synthetic_head'); print('diagnostic',file=sys.stderr)"],
                          **kwargs)
        with patch.object(io.subprocess, "Popen", side_effect=synthetic_popen):
            head, facts = io.run_git_read(self.root, ("rev-parse", "HEAD"))
        self.assertEqual(head, "synthetic_head")
        self.assertGreater(facts["stderr_bytes"], 0)
        self.assertNotIn("diagnostic", json.dumps(facts))


if __name__ == "__main__":
    unittest.main()
