"""Resolve Sockseek's legacy profiles and private managed overrides."""
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

from listening_plugin.contract import settings

CREDENTIAL_KEYS = {"user": "username", "username": "username",
                   "pass": "password", "password": "password"}
PATH_OPTIONS = {
    "i", "input", "o", "p", "output-dir", "path", "parent", "pp", "playlist-path",
    "ip", "index-path", "iaa", "incomplete-album-action", "smd", "skip-music-dir",
    "lf", "log-file", "mock-files-dir",
}
CONFIGDIR_PREFIX = re.compile(r'^(\s*"?(?:(?i:move:)\s*)?)\{configdir\}(?=[/\\]|[\s"]*$)')


def default_credentials(content):
    profile = "default"
    credentials = {}
    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            profile = line[1:-1]
        if profile != "default" or not line or line.startswith("#"):
            continue
        option, separator, value = line.partition("=")
        if not separator:
            continue
        option, value = option.strip(), value.strip()
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        if option in CREDENTIAL_KEYS:
            credentials[CREDENTIAL_KEYS[option]] = value
        elif option in {"l", "login"}:
            credentials["username"], _, credentials["password"] = value.partition(";")
    return credentials


@contextmanager
def effective_config(directory):
    legacy = directory / "sockseek.conf"
    values = settings(directory)
    updates = {key: values[key] for key in ("username", "password") if key in values}
    if not updates:
        yield legacy
        return
    try:
        prior = legacy.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        prior = ""
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as config:
        # Sockseek normally expands {configdir} beside the active config.
        # Keep those paths anchored to the existing read-only mount.
        for line in prior.splitlines(keepends=True):
            key, separator, value = line.partition("=")
            option = key.strip()
            if option in PATH_OPTIONS:
                value = CONFIGDIR_PREFIX.sub(lambda match: match[1] + str(legacy.parent), value)
            elif option in {"nf", "name-format", "oc", "on-complete"}:
                value = value.replace("{configdir}", str(legacy.parent))
            config.write(key + separator + value)
        config.write("\n[default]\n")
        for key, value in updates.items():
            config.write(f'{key} = "{value}"\n')
        config.flush()
        yield Path(config.name)


def has_login(directory):
    with effective_config(directory) as path:
        try:
            credentials = default_credentials(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return False
    return all(credentials.get(key, "").strip() for key in ("username", "password"))
