import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contract.support import ROOT, module, worker
from listening_plugin import ConfigurationError


class Sharing(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.credentials = self.root / "credentials"
        self.credentials.mkdir()
        self.folder = self.root / "shared"
        self.folder.mkdir()

    def test_worker_launches_only_the_approved_folder_with_private_sharing_settings(self):
        (self.credentials / "sockseek.conf").write_text("username = download\npassword = download-password\n")
        (self.credentials / "plugin-settings.json").write_text(
            json.dumps({"share_username": "sharing", "share_password": "a % password = for sharing"}))
        sharing = module("sockseek", "sharing")

        def exec_service(binary, command, env):
            self.assertEqual(binary, sharing.NICOTINE)
            self.assertEqual(command[1:4], ["--headless", "--isolated", "--config"])
            config = Path(command[4])
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
            settings = config.read_text()
            self.assertIn("login = sharing", settings)
            self.assertIn("passw = a % password = for sharing", settings)
            self.assertIn(f"shared = [('Listening Room', '{self.folder}')]", settings)
            self.assertNotIn("download-password", settings)
            self.assertNotIn("sharing", " ".join(command))
            self.assertEqual(env["HOME"], str(config.parent))

        with patch.object(sharing, "SHARE", self.folder), patch.object(sharing.tempfile, "tempdir", str(self.root)), \
                patch.object(sharing.os, "execve", side_effect=exec_service) as launch:
            worker("sockseek", self.credentials).share()
            launch.assert_called_once()

    def test_share_rejects_the_download_account_and_links_to_private_files(self):
        (self.credentials / "sockseek.conf").write_text("user = account\npass = download-password\n")
        (self.credentials / "plugin-settings.json").write_text(
            json.dumps({"share_username": "Account", "share_password": "other"}))
        sharing = module("sockseek", "sharing")
        with patch.object(sharing, "SHARE", self.folder), patch.object(sharing.os, "execve") as launch:
            strategy = worker("sockseek", self.credentials)
            with self.assertRaisesRegex(ConfigurationError, "separate account"):
                strategy.share()
            (self.folder / "audio.flac").symlink_to(self.credentials / "sockseek.conf")
            with self.assertRaisesRegex(ConfigurationError, "symbolic links"):
                strategy.share()
            launch.assert_not_called()

    def test_account_import_preserves_settings_on_retry(self):
        download = self.root / "old-sockseek.conf"
        download.write_text("username = download\npassword = download-password\n# Existing profiles\n")
        sharing = self.root / "old-nicotine.conf"
        sharing.write_text("[server]\nlogin = sharing\npassw = sharing-password\n")
        state = self.root / "fleet-state"
        command = [sys.executable, str(ROOT / "tools/migrate_sockseek.py"), "--state", str(state),
                   "--download-config", str(download), "--sharing-config", str(sharing)]
        for _ in range(2):
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertNotIn("password", result.stdout + result.stderr)
            download.write_text("invalid after import")
            sharing.write_text("invalid after import")
        credentials = state / "sockseek" / "credentials"
        self.assertIn("# Existing profiles", (credentials / "sockseek.conf").read_text())
        settings = credentials / "plugin-settings.json"
        self.assertEqual(json.loads(settings.read_text()),
                         {"share_username": "sharing", "share_password": "sharing-password"})
        self.assertEqual(stat.S_IMODE(settings.stat().st_mode), 0o600)

    def test_indexed_files_cannot_follow_links_replaced_during_sharing(self):
        paths = module("sockseek", "share_files")
        album = self.folder / "Album"
        album.mkdir()
        audio = album / "song.flac"
        audio.write_bytes(b"fixture audio")
        secret = self.credentials / "song.flac"
        secret.write_bytes(b"private data")
        with patch.object(paths, "SHARE_ROOT", self.folder):
            with paths.open_share_file(audio) as source:
                self.assertEqual(source.read(), b"fixture audio")
            audio.unlink()
            audio.symlink_to(secret)
            with self.assertRaises(OSError):
                paths.open_share_file(audio)
            audio.unlink()
            os.link(secret, audio)
            with self.assertRaises(PermissionError):
                paths.share_file_size(audio)
            audio.unlink()
            album.rmdir()
            album.symlink_to(self.credentials, target_is_directory=True)
            with self.assertRaises(OSError):
                paths.open_share_file(audio)
            with self.assertRaises(OSError), paths.scan_share_folder(album):
                self.fail("A scan reached a symlink outside the share")
