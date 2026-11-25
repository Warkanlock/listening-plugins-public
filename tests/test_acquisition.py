"""Exercise the same v1 requests that the room sends to installed workers."""
import json
import socket
import subprocess
import sys
import tempfile
import threading
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
        peers = [{"User": {"Username": "peer"}, "File": {"Filename": "Artist/Song - " + n + ".mp3", "Bitrate": 320-int(n)}} for n in names]
        peers.append({"User": {"Username": "peer"}, "File": {"Filename": "Artist/Song.flac", "Bitrate": 950}})
        with patch.object(self.catalog.source, "recordings", return_value=entries), patch.object(
                self.peer, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(peers))):
            for adapter, prefix in [(self.catalog, ""), (self.peer, "slsk://peer/Artist/Song%20-%20")]:
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

    def test_sockseek_broadens_to_title_and_ranks_an_artist_found_in_the_folder(self):
        def result(filename, bitrate, duration):
            return {"User": {"Username": "fixture-peer"}, "File": {
                "Filename": filename, "Bitrate": bitrate, "Length": duration}}

        peers = [
            result(r"Music\Ligabue\01-02. Ho perso le parole (Live).flac", 950, 290),
            result(r"Music\Ligabue\02 - Ho perso le parole.mp3", 128, 274),
            result(r"Music\Ligabue\06-ho_perso_le_parole.mp3", 320, 274),
            result(r"Music\Ligabue\06. A different song.mp3", 320, 274),
        ]
        recording = {**self.record, "title": "Ho perso le parole", "artist": "Ligabue", "duration": 274}
        with patch.object(self.peer, "run", side_effect=[
            subprocess.CompletedProcess([], 0, "[]"),
            subprocess.CompletedProcess([], 0, json.dumps(peers)),
        ]) as run:
            response = request(self.peer, "search", recording=recording)
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         ["ligabue ho perso le parole", "ho perso le parole"])
        matches = response["candidates"]
        self.assertEqual(len(matches), 3)
        self.assertEqual(matches[0]["label"], "06-ho_perso_le_parole.mp3")
        self.assertEqual((matches[0]["format"], matches[0]["duration"], matches[0]["bitrate"]), ("mp3", 274, 320))
        self.assertEqual([match["recommended"] for match in matches], [True, False, False])
        with patch.object(self.peer, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(peers))) as run:
            live = request(self.peer, "search", recording={**recording, "version": "Live", "duration": 290})
        run.assert_called_once()
        self.assertTrue(live["candidates"][0]["recommended"])
        self.assertIn("(Live)", live["candidates"][0]["label"])

    def test_sockseek_publishes_byte_progress_while_the_transfer_is_open(self):
        progress = module("sockseek", "progress")
        observed = threading.Event()
        publish = progress.report_download_progress

        def report(staging, received, total):
            publish(staging, received, total)
            observed.set()

        with patch.object(progress, "report_download_progress", side_effect=report):
            with progress.progress_output(self.root) as output:
                output.write(b'{"type":"download_progress","data":{"bytesTransferred":83,"totalBytes":200}}\n')
                output.flush()
                self.assertTrue(observed.wait(2))
                self.assertEqual((self.root / ".download-progress").read_text(), "41")

    def test_sockseek_allows_a_slow_search_to_succeed_on_the_first_attempt(self):
        code = module("sockseek", "session")
        peers = [{"User": {"Username": "fixture-peer"},
                  "File": {"Filename": "Artist/Song.mp3", "Bitrate": 320}}]

        def finish(command, *, timeout, **options):
            if timeout < 16:
                raise subprocess.TimeoutExpired(command, timeout)
            return subprocess.CompletedProcess(command, 0, json.dumps(peers))

        with patch.object(code, "has_container_init", return_value=True), patch.object(
                code.Session, "ensure", return_value={}), patch.object(subprocess, "run", side_effect=finish) as client:
            response = request(self.peer, "search", recording=self.record)

        client.assert_called_once()
        self.assertTrue(response["ok"])
        self.assertEqual([match["label"] for match in response["candidates"]], ["Song.mp3"])

    def test_sockseek_title_fallback_shares_the_search_budget(self):
        code = module("sockseek", "session")
        now = 100
        limits = []

        def finish(command, *, timeout, **options):
            nonlocal now
            limits.append(timeout)
            elapsed = 13.9 if len(limits) == 1 else 13.6
            if timeout < elapsed:
                raise subprocess.TimeoutExpired(command, timeout)
            now += elapsed
            if len(limits) == 1:
                return subprocess.CompletedProcess(command, 0, "[]")
            return subprocess.CompletedProcess(command, 0, json.dumps([
                {"User": {"Username": "fixture-peer"}, "File": {"Filename": "Artist/Song.mp3"}}
            ]))

        def connect():
            nonlocal now
            now += 0.1
            return {}

        with patch.object(code.time, "monotonic", side_effect=lambda: now), patch.object(
                code, "has_container_init", return_value=True), patch.object(
                code.Session, "ensure", side_effect=connect), patch.object(subprocess, "run", side_effect=finish):
            response = request(self.peer, "search", recording=self.record)

        self.assertTrue(response["ok"])
        self.assertEqual([match["label"] for match in response["candidates"]], ["Song.mp3"])
        self.assertGreater(limits[0], 16)
        self.assertAlmostEqual(now - 100, 27.7)
        self.assertLessEqual(14.1 + limits[1], 29.01)

    def test_sockseek_returns_initial_candidates_if_too_little_time_remains_to_broaden(self):
        code = module("sockseek", "provider")
        peers = [{"User": {"Username": "fixture-peer"},
                  "File": {"Filename": "Song.mp3", "Bitrate": 320}}]
        with patch.object(code.time, "monotonic", side_effect=[100, 100, 123]), patch.object(
                self.peer, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(peers))) as client:
            response = request(self.peer, "search", recording=self.record)

        client.assert_called_once()
        self.assertTrue(response["ok"])
        self.assertEqual([(match["label"], match["recommended"]) for match in response["candidates"]],
                         [("Song.mp3", False)])

    def test_live_request_drops_venue_from_fallback_without_recommending_studio_copy(self):
        qualifier = "live, 2014-10-24: Fixture Hall"
        recording = {**self.record, "search_title": f"Song {qualifier}", "version": qualifier, "duration": 274}
        peers = [
            {"User": {"Username": "fixture-peer"}, "File": {"Filename": "Artist/Song.mp3", "Length": 274}},
            {"User": {"Username": "fixture-peer"}, "File": {"Filename": "Artist/Song (Live).flac", "Length": 274}},
        ]
        with patch.object(self.peer, "run", side_effect=[
            subprocess.CompletedProcess([], 0, "[]"),
            subprocess.CompletedProcess([], 0, json.dumps(peers)),
        ]) as run:
            response = request(self.peer, "search", recording=recording)
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         ["artist song live 2014 10 24 fixture hall", "song"])
        self.assertEqual([(match["label"], match["recommended"]) for match in response["candidates"]],
                         [("Song (Live).flac", True), ("Song.mp3", False)])

    def test_featured_credit_can_be_split_between_directory_and_filename(self):
        names = ["Track (feat. Guest Artist).mp3", "Track.mp3", "Track (feat. Other Artist).mp3",
                 "Track (feat. Guest Artist and Other Artist).mp3"]
        peers = [{"User": {"Username": "example"}, "File": {
            "Filename": "Main Artist/Album/" + name, "Length": 224, "Bitrate": 320}} for name in names]
        for index in (1, 2):
            peers[index]["File"]["Filename"] = "Main Artist/Guest Artist Collection/" + names[index]
        recording = {**self.record, "title": "Track", "artist": "Main Artist feat. Guest Artist", "duration": 224}
        with patch.object(self.peer, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(peers))) as run:
            result = request(self.peer, "search", recording=recording)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], "main artist track")
        self.assertEqual(result["candidates"][0]["label"], names[0])
        self.assertEqual([row["recommended"] for row in result["candidates"]], [True, False, False, False])

    def test_ft_in_the_song_title_is_not_a_guest_credit(self):
        peers = [{"User": {"Username": "example"}, "File": {
            "Filename": "Main Artist/Album/50 Ft Queenie.mp3", "Length": 224, "Bitrate": 320}}]
        recording = {**self.record, "title": "50 Ft Queenie", "artist": "Main Artist", "duration": 224}
        with patch.object(self.peer, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(peers))):
            result = request(self.peer, "search", recording=recording)
        self.assertTrue(result["candidates"][0]["recommended"])

    def test_sockseek_preserves_a_number_at_the_start_of_a_song_title(self):
        peers = [{"User": {"Username": "fixture-peer"}, "File": {"Filename": "Artist/2 U.flac"}}]
        with patch.object(self.peer, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(peers))) as run:
            result = request(self.peer, "search", recording={**self.record, "title": "2 U"})
        run.assert_called_once()
        self.assertEqual(result["candidates"][0]["label"], "2 U.flac")
        self.assertTrue(result["candidates"][0]["recommended"])

    def test_sockseek_distinguishes_the_artist_live_from_a_live_version(self):
        peers = [{"User": {"Username": "fixture-peer"}, "File": {"Filename": "Live - Lightning Crashes.mp3"}}]
        recording = {**self.record, "artist": "Live", "title": "Lightning Crashes"}
        with patch.object(self.peer, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(peers))) as run:
            result = request(self.peer, "search", recording=recording)
        run.assert_called_once()
        self.assertTrue(result["candidates"][0]["recommended"])

    def test_sockseek_preserves_written_letters_in_the_network_query(self):
        with patch.object(self.peer, "run", return_value=subprocess.CompletedProcess([], 0, "[]")) as run:
            request(self.peer, "search", recording={**self.record, "artist": "Niño", "title": "ガラス"})
        self.assertEqual([call.args[0] for call in run.call_args_list], ["niño ガラス", "ガラス"])

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
import json
import sys
root = Path(__file__).parent
assert '--progress-json' in sys.argv
config = Path(sys.argv[sys.argv.index('--config')+1])
text = config.read_text()
assert 'password = "new"' in text
assert 'login = fixture;old' in text
assert str(root / 'sockseek.log') in text
assert config.stat().st_mode & 0o777 == 0o600
(root / 'observed').write_text(str(config))
mode = (root / 'mode').read_text()
if mode in ('NoSearchResults', 'NoMatchingResults', 'Other'):
    print(json.dumps({'type': 'track_state', 'data': {'terminalOutcome': 'Failed', 'failureReason': mode}}))
print('[]')
sys.exit(0 if mode == 'normal' else 1)
""")
        binary.chmod(0o700)
        strategy = worker("sockseek", self.root, str(binary))
        session_module = module("sockseek", "session")
        session = session_module.Session(str(binary), self.root, self.root / "session")
        for mode in ("normal", "NoSearchResults", "NoMatchingResults", "Other", "missing_outcome"):
            (self.root / "mode").write_text(mode)
            with self.subTest(mode=mode), patch.object(
                    module("sockseek", "provider"), "Session", return_value=session), patch.object(
                    session_module, "has_container_init", return_value=False):
                result = request(strategy, "search", recording=self.record)
                if mode in ("Other", "missing_outcome"):
                    self.assertFalse(result["ok"])
                    self.assertTrue(result["retryable"])
                else:
                    self.assertEqual(result, {"ok": True, "candidates": [], "version": 1})
                observed = self.root / "observed"
                self.assertFalse(Path(observed.read_text()).exists())
                observed.unlink()
        self.assertIn("fixture;old", (self.root / "sockseek.conf").read_text())
