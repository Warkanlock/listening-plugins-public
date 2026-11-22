from contextlib import redirect_stdout
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
from types import SimpleNamespace
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from contract.support import module, run_worker, sources, worker
from listening_plugin.runtime import dispatch


class Plugins(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.credentials = self.root / "credentials"
        self.credentials.mkdir()
        self.staging = self.root / "staging"
        self.staging.mkdir()

    def test_catalog_strategy_reads_private_settings_and_retrieves_its_own_candidate(self):
        (self.credentials / "plugin-settings.json").write_text('{"catalog_url":"https://catalog.test"}')
        strategy = worker("example-catalog", self.credentials)
        recording = {"title": "First Light", "artist": "Sample", "formats": [".mp3"]}
        releases = [{"id": "first", **recording}]
        with patch.object(strategy.source, "open", side_effect=[
                BytesIO(json.dumps(releases).encode()), BytesIO(b"fixture audio")]):
            self.assertEqual(strategy.source.origin(), "https://catalog.test")
            found = dispatch(strategy, sources("example-catalog"), {"version": 1, "action": "search", "recording": recording})
            copied = dispatch(strategy, sources("example-catalog"), {"version": 1, "action": "fetch",
                "candidate": found["candidates"][0], "staging": str(self.staging)})
        self.assertEqual(copied, {"ok": True, "file": "first.mp3"})
        self.assertEqual((self.staging / "first.mp3").read_bytes(), b"fixture audio")

    def test_sockseek_strategy_supports_legacy_and_managed_settings(self):
        legacy = ('# Existing profile settings\nlogin = fixture;example\n[late]\nuser = other\n'
                  'pass = {configdir}/literal\nlog-file = "{configdir}/sockseek.log"\n'
                  'on-complete = when=success -- printf "%s" "{configdir}/notes.txt"\n'
                  'skip-music-dir = {configdir}\\existing\n'
                  'incomplete-album-action = MOVE:{configdir}/partial\n')
        (self.credentials / "sockseek.conf").write_text(legacy)
        binary = shutil.which("true")
        if not binary:
            self.skipTest("true executable required")
        strategy = worker("sockseek", self.credentials, binary)
        self.assertTrue(dispatch(strategy, sources("sockseek"), {"version": 1, "action": "test"})["ok"])
        password = '  an "example" = value  '
        (self.credentials / "plugin-settings.json").write_text(json.dumps({"password": password}))
        with module("sockseek", "configuration").effective_config(self.credentials) as name:
            path = Path(name)
            expected = legacy.replace('"{configdir}/sockseek.log"', f'"{self.credentials}/sockseek.log"')
            expected = expected.replace('"{configdir}/notes.txt"', f'"{self.credentials}/notes.txt"')
            expected = expected.replace('{configdir}\\existing', f'{self.credentials}\\existing')
            expected = expected.replace('MOVE:{configdir}/partial', f'MOVE:{self.credentials}/partial')
            self.assertTrue(path.read_text().startswith(expected))
            self.assertEqual(module("sockseek", "configuration").default_credentials(path.read_text()), {"username": "fixture", "password": password})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertFalse(os.path.exists(name))
        self.assertTrue(dispatch(strategy, sources("sockseek"), {"version": 1, "action": "test"})["ok"])
        from listening_plugin import Unavailable
        with self.assertRaises(Unavailable):
            dispatch(strategy, sources("sockseek"), {"version": 1, "action": "fetch", "staging": str(self.staging),
                                        "candidate": {"source": "example-catalog", "id": "first.mp3"}})


    def test_sockseek_reports_peer_timeout_without_exposing_the_command(self):
        strategy = worker("sockseek", self.credentials, "/fixture")
        request = {
            "version": 1, "action": "fetch", "staging": str(self.staging),
            "candidate": {"source": "soulseek", "id": "slsk://peer/song.mp3"},
        }
        stream = SimpleNamespace(buffer=BytesIO(json.dumps(request).encode()))
        session = module("sockseek", "session")
        with patch.object(session, "has_container_init", return_value=True), patch.object(session.Session, "ensure", return_value={}), patch.object(session, "api") as api:
            api.side_effect = [{"soulseekClient": {"isReady": True}}, [], {"activeJobCount": 0}]
            with patch.object(sys, "stdin", stream), redirect_stdout(StringIO()) as response, patch.object(
                subprocess, "run", side_effect=subprocess.TimeoutExpired(["private-command"], 90),
            ):
                run_worker(strategy)
        self.assertEqual(json.loads(response.getvalue()), {
            "version": 1, "ok": False, "retryable": True, "code": "timeout",
        })

    def test_unavailable_login_is_backed_off_and_does_not_try_more_peers(self):
        from listening_plugin import LoginUnavailable
        import hashlib
        session_code = module("sockseek", "session")
        path = self.credentials / "sockseek.conf"
        path.write_bytes(b"login = fixture;old\n")
        session = session_code.Session("/fixture", self.credentials, self.staging)
        original = {"config": hashlib.sha256(path.read_bytes()).hexdigest()}
        with patch.object(session_code, "api", return_value={"soulseekClient": {"isReady": False}}):
            with self.assertRaises(LoginUnavailable) as failure:
                session.abort(original)
            self.assertEqual(failure.exception.retry_after_seconds, 1800)
        with patch.object(subprocess, "Popen") as spawn:
            with self.assertRaises(LoginUnavailable) as waiting:
                session.ensure()
            self.assertGreater(waiting.exception.retry_after_seconds, 0)
            self.assertLessEqual(waiting.exception.retry_after_seconds, 1800)
            spawn.assert_not_called()
        with self.assertRaises(LoginUnavailable):
            session.check_login()
        path.write_bytes(b"login = fixture;updated\n")
        session.check_login()
        self.assertNotIn("fixture", (self.staging / "session.json").read_text())

    def test_unavailable_login_advertises_bounded_delay_without_private_text(self):
        from listening_plugin import LoginUnavailable
        strategy = worker("sockseek", self.credentials, "/fixture")
        stream = SimpleNamespace(buffer=BytesIO(b'{"version":1,"action":"test"}'))
        with patch.object(strategy, "test", side_effect=LoginUnavailable(retry_after_seconds=75)), \
                patch.object(sys, "stdin", stream), redirect_stdout(StringIO()) as response:
            run_worker(strategy)
        self.assertEqual(json.loads(response.getvalue()), {
            "version": 1, "ok": False, "retryable": True, "code": "login_unavailable", "retry_after_seconds": 75})
        for invalid in (0, -5, 86401, True, "60"):
            with self.assertRaises(ValueError):
                LoginUnavailable(retry_after_seconds=invalid)

    def test_fetch_timeout_cancels_remote_transfer_before_returning(self):
        session_code = module("sockseek", "session")
        session = session_code.Session("/fixture", self.credentials, self.staging)
        workflow = "718858af-3577-451e-9fbc-c0320b9e136f"
        with patch.object(session_code, "api") as api:
            api.side_effect = [{"soulseekClient": {"isReady": True}}, [{"workflowId": workflow, "activeJobCount": 1}],
                               {}, {"activeJobCount": 0}]
            session.abort({})
            api.assert_any_call(f"workflows/{workflow}/cancel", "POST")

    def test_legacy_worker_reaps_foreground_client_and_backs_off_failed_login(self):
        from listening_plugin import LoginUnavailable
        code = module("sockseek", "session")
        session = code.Session("/fixture", self.credentials, self.staging)
        (self.credentials / "sockseek.conf").write_text("login = fixture;secret\n")
        with patch.object(code, "has_container_init", return_value=False), patch.object(session, "ensure") as daemon:
            error = subprocess.TimeoutExpired("private", 22, stderr=b"Initial Soulseek login failed: private account")
            with patch.object(subprocess, "run", side_effect=error) as client:
                for _ in range(2):
                    with self.assertRaises(LoginUnavailable):
                        session.run("query", [], timeout=22, capture_output=True)
                self.assertEqual(client.call_count, 1)
            daemon.assert_not_called()
        self.assertNotIn("secret", (self.staging / "session.json").read_text())
