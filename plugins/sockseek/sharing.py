"""Serve the operator's read-only audio folder through Nicotine+."""
import configparser
import os
import stat
import tempfile
from pathlib import Path

from listening_plugin import ConfigurationError
from listening_plugin.contract import settings

from .configuration import default_credentials, effective_config

SHARE = Path("/share")
SCRATCH = Path("/scratch")
NICOTINE = "/usr/local/bin/nicotine"


def check_share_folder(folder):
    def scan_error(error):
        raise error

    if folder.is_symlink() or not folder.is_dir():
        raise ConfigurationError("Mount the folder to share.")
    for parent, directories, files in os.walk(folder, onerror=scan_error):
        root = Path(parent)
        if any((root / name).is_symlink() for name in directories) or any(
                not stat.S_ISREG((root / name).lstat().st_mode) for name in files):
            raise ConfigurationError("Sharing requires regular files and folders without symbolic links.")


def share_profile(credentials, folder):
    values = settings(credentials)
    username = values.get("share_username", "").strip()
    password = values.get("share_password", "")
    if not all(value and value == value.strip() and value.isprintable() for value in (username, password)):
        raise ConfigurationError("Set the sharing account name and password.")
    with effective_config(credentials) as path:
        try:
            download = default_credentials(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            download = {}
    if username.strip().casefold() == download.get("username", "").strip().casefold():
        raise ConfigurationError("Use a separate account for sharing and downloads.")
    profile = configparser.ConfigParser(interpolation=None)
    profile["server"] = {"login": username, "passw": password, "upnp": "False"}
    profile["transfers"] = {"shared": repr([("Listening Room", str(folder))])}
    profile["plugins"] = {"enable": "False"}
    profile["logging"] = {"privatechat": "False", "chatrooms": "False"}
    return profile


def serve_share(credentials):
    check_share_folder(SHARE)
    profile = share_profile(credentials, SHARE)
    work = Path(tempfile.mkdtemp(prefix="listening-share-"))
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=work, delete=False) as target:
        profile.write(target)
    os.execve(NICOTINE, [NICOTINE, "--headless", "--isolated", "--config", target.name,
                         "--user-data", str(SCRATCH)],
              {**os.environ, "HOME": str(work), "XDG_CONFIG_HOME": str(work), "XDG_DATA_HOME": str(SCRATCH)})
