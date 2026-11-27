"""Validate the metadata shared by the worker and the release catalog."""
import json
import re
from pathlib import Path

ID = r"[a-z][a-z0-9-]{0,47}"
VERSION = r"[0-9]+(?:\.[0-9]+){1,2}"
IMAGE = r"[a-z0-9][a-z0-9.:/_-]+@sha256:[0-9a-f]{64}"


def matches(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def strings(values, pattern):
    return (isinstance(values, list) and bool(values) and all(matches(v, pattern) for v in values)
            and len(values) == len(set(values)))


def capabilities(manifest):
    return manifest.get("capabilities", list(manifest["sources"]))


def read_manifest(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    fields = {"manifest_version", "protocol_version", "id", "version", "architectures",
              "server_versions", "sources", "settings"}
    if not isinstance(data, dict) or not fields <= data.keys() <= fields | {"capabilities"}:
        raise ValueError(f"Invalid manifest fields in {path}")
    for field in ("manifest_version", "protocol_version"):
        if type(data[field]) is not int or data[field] != 1:
            raise ValueError(f"Unsupported {field} in {path}")
    sources = data["sources"]
    if not (matches(data["id"], ID) and matches(data["version"], VERSION)
            and strings(data["architectures"], r"[a-z0-9][a-z0-9_]{0,31}")
            and strings(data["server_versions"], VERSION)
            and isinstance(sources, dict) and set(sources) <= {"search", "fetch"}
            and strings(capabilities(data), r"search|fetch|share")
            and set(sources) == (set(capabilities(data)) - {"share"})
            and all(strings(names, r"[a-z][a-z0-9._-]{0,63}") for names in sources.values())):
        raise ValueError(f"Invalid plugin identity, compatibility or sources in {path}")
    settings = data["settings"]
    if not isinstance(settings, list) or len(settings) > 12:
        raise ValueError(f"Invalid settings in {path}")
    keys = set()
    for field in settings:
        if not (isinstance(field, dict) and set(field) == {"key", "type", "label", "required"}
                and matches(field["key"], r"[a-z][a-z0-9_]{0,39}") and field["key"] not in keys
                and field["type"] in ("text", "secret") and type(field["required"]) is bool
                and isinstance(field["label"], str) and 0 < len(field["label"].strip()) <= 80
                and field["label"].isprintable()):
            raise ValueError(f"Invalid setting declaration in {path}")
        keys.add(field["key"])
    return data
