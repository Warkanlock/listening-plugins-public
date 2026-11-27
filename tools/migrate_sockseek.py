#!/usr/bin/env python3
"""Import an existing room's private accounts into the Sockseek extension."""
import argparse
import configparser
import fcntl
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "plugins"), str(ROOT / "sdk")]
from listening_plugin.contract import settings
from sockseek.configuration import default_credentials


def private_write(path, payload):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        name = Path(temporary.name)
        try:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            name.replace(path)
        finally:
            name.unlink(missing_ok=True)


def migrate(state, download_config, sharing_config):
    directory = state / "sockseek"
    credentials = directory / "credentials"
    credentials.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = credentials.stat()
    if (credentials.is_symlink() or directory.is_symlink() or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise PermissionError("The extension credentials directory must be private.")
    with (directory / ".operation").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        values = settings(credentials)
        target = credentials / "sockseek.conf"
        download = None
        sharing = None
        if download_config and not target.exists() and not {"username", "password"} & values.keys():
            download = download_config.read_bytes()
            account = default_credentials(download.decode("utf-8-sig"))
            if not all(account.get(key) for key in ("username", "password")):
                raise ValueError("The old download profile is missing an account.")
        if sharing_config and not {"share_username", "share_password"} & values.keys():
            config = configparser.ConfigParser(interpolation=None)
            with sharing_config.open(encoding="utf-8") as source:
                config.read_file(source)
            sharing = {"share_username": config.get("server", "login"),
                       "share_password": config.get("server", "passw")}
            if not all(value.strip() and value.isprintable() and len(value.encode()) <= 512
                       for value in sharing.values()):
                raise ValueError("The old sharing profile is missing a valid account.")
        if download is not None:
            private_write(target, download)
        if sharing is not None:
            private_write(credentials / "plugin-settings.json", json.dumps({**values, **sharing}).encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True, help="engine manager state directory")
    parser.add_argument("--download-config", type=Path, help="existing sockseek.conf")
    parser.add_argument("--sharing-config", type=Path, help="existing nicotine-share.conf")
    args = parser.parse_args()
    if not args.download_config and not args.sharing_config:
        parser.error("Choose at least one existing profile to import.")
    try:
        os.umask(0o077)
        migrate(args.state, args.download_config, args.sharing_config)
    except (configparser.Error, OSError, ValueError):
        parser.exit(1, "Could not import the profiles. Check the existing files and manager state permissions.\n")
    print("Account import complete. Existing extension account settings were preserved.")


if __name__ == "__main__":
    main()
