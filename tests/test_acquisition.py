"""Exercise the same v1 requests that the room sends to installed workers."""
import json
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from unittest.mock import MagicMock, patch

from contract.support import module, run_worker, sources, worker
from listening_plugin.runtime import dispatch


def request(strategy, action, **fields):
    wire = json.dumps({"version": 1, "action": action, **fields}).encode()
    with patch.object(sys, "stdin", SimpleNamespace(buffer=BytesIO(wire))), redirect_stdout(StringIO()) as reply:
        run_worker(strategy)
    return json.loads(reply.getvalue())


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.catalog = worker("example-catalog", self.root)
        self.peer = worker("sockseek", self.root)
        self.record = {"title": "Song", "artist": "Artist", "formats": [".mp3", ".flac"], "preferred_format": "mp3"}

    def test_both_providers_rank_twenty_distinct_candidates(self):
        names = ["00"] * 25 + [f"{n:02d}" for n in range(1, 25)]
        entries = [{"id": n, "formats": [".mp3", ".flac"], "artist": "Artist", "title": "Song"} for n in names]
        peers = [{"User": {"Username": "peer"}, "File": {"Filename": n + ".mp3", "Bitrate": 320-int(n)}} for n in names]
        peers.append({"User": {"Username": "peer"}, "File": {"Filename": "lossless.flac", "Bitrate": 950}})
        with patch.object(self.catalog.source, "recordings", return_value=entries), patch.object(
                self.peer, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(peers))):
            for adapter, prefix in [(self.catalog, ""), (self.peer, "slsk://peer/")]:
                with self.subTest(adapter=type(adapter).__name__):
                    found = request(adapter, "search", recording=self.record)
                    self.assertTrue(found["ok"])
                    self.assertEqual([c["id"] for c in found["candidates"]], [f"{prefix}{i:02d}.mp3" for i in range(20)])

    def test_catalog_timeout_is_sanitized_and_partial_file_is_removed(self):
        (self.root / "catalog-url").write_text("http://example.test")
        candidate = {"source": "example-catalog", "id": "pick.mp3"}
        for failure in (TimeoutError("private"), socket.timeout("private"), URLError(socket.timeout("private"))):
            with self.subTest(failure=type(failure).__name__), patch.object(module("example-catalog", "source"), "build_opener") as opener:
                opener.return_value.open.side_effect = failure
                self.assertEqual(request(self.catalog, "fetch", candidate=candidate, staging=str(self.root)),
                                 {"ok": False, "retryable": True, "code": "timeout", "version": 1})
        response = MagicMock()
        response.read.side_effect = [b"partial file", TimeoutError("private")]
        with patch.object(module("example-catalog", "source"), "build_opener") as opener:
            opener.return_value.open.return_value = response
            reply = request(self.catalog, "fetch", candidate=candidate, staging=str(self.root))
        self.assertEqual(reply, {"ok": False, "retryable": True, "code": "timeout", "version": 1})
        self.assertFalse((self.root / "pick.mp3").exists())
        response.__exit__.assert_called_once()

    def test_catalog_preserves_existing_file_and_cleans_up_after_close_failure(self):
        path = self.root / "pick.mp3"
        path.write_bytes(b"existing")
        with patch.object(self.catalog.source, "open", return_value=BytesIO(b"new")):
            with self.assertRaises(FileExistsError):
                self.catalog.source.download(path.name, path)
        self.assertEqual(path.read_bytes(), b"existing")
        path.unlink()

        class FullDisk(BytesIO):
            def close(self):
                super().close()
                raise OSError("out of space")

        def open_output(name, mode):
            with open(name, mode):
                pass
            return FullDisk()

        with patch.object(self.catalog.source, "open", return_value=BytesIO(b"new")), patch.object(Path, "open", open_output):
            with self.assertRaisesRegex(OSError, "out of space"):
                self.catalog.source.download(path.name, path)
        self.assertFalse(path.exists())

    def test_catalog_missing_copy_and_transient_failure_remain_distinct(self):
        (self.root / "catalog-url").write_text("http://example.test")
        for error, retryable in [(HTTPError("", 404, "missing", {}, None), False),
                                 (URLError(ConnectionRefusedError("private")), True)]:
            with self.subTest(error=error), patch.object(module("example-catalog", "source"), "build_opener") as opener:
                opener.return_value.open.side_effect = error
                reply = request(self.catalog, "fetch", candidate={"source":"example-catalog", "id":"pick.mp3"},
                                staging=str(self.root))
                self.assertFalse(reply["ok"])
                self.assertIs(reply["retryable"], retryable)
                self.assertNotIn("code", reply)
                self.assertNotIn("private", str(reply))

    def test_executable_reads_effective_profile_and_cleanup_survives_failure(self):
        (self.root / "sockseek.conf").write_text("login = fixture;old\nlog-file = {configdir}/sockseek.log\n")
        (self.root / "plugin-settings.json").write_text(json.dumps({"password": "new"}))
        binary = self.root / "fixture"
        binary.write_text(f"#!{sys.executable}\n" + """
from pathlib import Path
import sys
root = Path(__file__).parent
config = Path(sys.argv[sys.argv.index('--config')+1])
text = config.read_text()
assert 'password = "new"' in text
assert 'login = fixture;old' in text
assert str(root / 'sockseek.log') in text
assert config.stat().st_mode & 0o777 == 0o600
(root / 'observed').write_text(str(config))
print('[]')
sys.exit(1 if (root/'fail').exists() else 0)
""")
        binary.chmod(0o700)
        strategy = worker("sockseek", self.root, str(binary))
        session_module = module("sockseek", "session")
        session = session_module.Session(str(binary), self.root, self.root / "session")
        for success in (True, False):
            if not success:
                (self.root / "fail").touch()
            with self.subTest(success=success), patch.object(
                    module("sockseek", "provider"), "Session", return_value=session), patch.object(
                    session_module, "has_container_init", return_value=False):
                self.assertEqual(request(strategy, "search", recording=self.record)["ok"], success)
                observed = self.root / "observed"
                self.assertFalse(Path(observed.read_text()).exists())
                observed.unlink()
        self.assertIn("fixture;old", (self.root / "sockseek.conf").read_text())
