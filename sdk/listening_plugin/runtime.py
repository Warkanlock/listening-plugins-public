"""Translate the room's v1 wire protocol at the worker entry point."""
import json
import re
import sys
from pathlib import Path
from typing import Iterable

from .contract import (LIMIT, Candidate, CompletedTimeout, ConfigurationError,
                       LoginUnavailable, PluginContext, Recording, Unavailable)
from .manifest import capabilities, read_manifest


def check_methods(provider, manifest):
    for name in ("test", *capabilities(manifest)):
        if name == "search" and callable(getattr(provider, "list", None)):
            continue
        if not callable(getattr(provider, name, None)):
            raise ValueError(f"Provider must implement {name}()")


def candidates(results: Iterable[Candidate], sources):
    seen = set()
    ordered = []
    # The manager re-encodes the reply with ASCII escapes and a trailing newline.
    size = len(json.dumps({"ok": True, "candidates": [], "version": 1})) + 1
    for candidate in results:
        if not (isinstance(candidate, Candidate) and candidate.source in sources
                and isinstance(candidate.id, str) and 0 < len(candidate.id) <= 512):
            raise ValueError("Invalid candidate")
        key = (candidate.source, candidate.id)
        if key not in seen:
            entry = {"source": candidate.source, "id": candidate.id}
            if isinstance(candidate.label, str) and candidate.label:
                entry["label"] = candidate.label[:180]
            if isinstance(candidate.format, str) and candidate.format:
                entry["format"] = candidate.format[:12]
            for field, limit in (("duration", 86400), ("bitrate", 100000)):
                value = getattr(candidate, field)
                if type(value) is int and 0 < value <= limit:
                    entry[field] = value
            for field in ("recommended", "identity_match"):
                if type(value := getattr(candidate, field)) is bool:
                    entry[field] = value
            size += len(json.dumps(entry)) + (2 if ordered else 0)
            if size > LIMIT:
                break
            seen.add(key)
            ordered.append(entry)
            if len(ordered) == 20:
                break
    return ordered


def dispatch(provider, sources, request):
    if not isinstance(request, dict) or type(request.get("version")) is not int or request["version"] != 1:
        raise ValueError("Unsupported engine protocol")
    action = request["action"]
    if action == "test":
        provider.test()
        return {"ok": True}
    if action not in sources:
        raise ValueError("Unsupported capability")
    if action == "search":
        rec = request["recording"]
        formats = rec["formats"]
        if not isinstance(formats, list) or not all(isinstance(value, str) for value in formats):
            raise ValueError("Invalid formats")
        query = rec.get("search_query", "")
        if not isinstance(query, str) or len(query) > 1000:
            raise ValueError("Invalid search query")
        recording = Recording(artist=rec["artist"], title=rec["title"], formats=tuple(formats),
                              preferred_format=rec.get("preferred_format", ""), search_title=rec.get("search_title", rec["title"]),
                              id=rec.get("id", ""), album=rec.get("album", ""), duration=rec.get("duration", 0),
                              version=rec.get("version", ""), search_query=query)
        listing = getattr(provider, "list", None)
        results = listing(recording) if callable(listing) else provider.search(recording)
        return {"ok": True, "candidates": candidates(results, sources[action])}
    candidate = Candidate(**request["candidate"])
    if candidate.source not in sources[action] or not isinstance(candidate.id, str) or not 0 < len(candidate.id) <= 512:
        raise Unavailable("Unsupported candidate.")
    name = provider.fetch(candidate, Path(request["staging"]))
    if not isinstance(name, str) or name in (".", "..") or not re.fullmatch(r"[^/\\\x00-\x1f]{1,255}", name):
        raise ValueError("Invalid staged filename")
    return {"ok": True, "file": name}


def serve(provider_factory, sources):
    try:
        line = sys.stdin.buffer.readline(LIMIT + 1)
        if len(line) > LIMIT:
            raise ValueError("Request is too large")
        result = dispatch(provider_factory(), sources, json.loads(line))
    except LoginUnavailable as error:
        result = {"ok": False, "retryable": True, "code": "login_unavailable"}
        if error.retry_after_seconds is not None:
            result["retry_after_seconds"] = error.retry_after_seconds
    except CompletedTimeout:
        result = {"ok": False, "retryable": True, "code": "timeout"}
    except Unavailable as error:
        result = {"ok": False, "retryable": False, "error": str(error)}
    except ConfigurationError as error:
        result = {"ok": False, "error": str(error)}
    except Exception:
        result = {"ok": False, "retryable": True, "error": "The plugin could not complete the operation."}
    print(json.dumps({**result, "version": 1}, ensure_ascii=True, allow_nan=False))


def main():
    from plugin.provider import Provider
    manifest = read_manifest(Path("/opt/listening/plugin/manifest.json"))
    check_methods(Provider, manifest)
    args = sys.argv[1:]
    if args == ["--validate"]:
        return
    context = PluginContext(Path("/credentials"))
    if args == ["--share"] and "share" in capabilities(manifest):
        try:
            Provider(context).share()
        except ConfigurationError as error:
            raise SystemExit(str(error)) from None
        except Exception:
            raise SystemExit("The plugin could not start sharing.") from None
    elif not args:
        serve(lambda: Provider(context), manifest["sources"])
    else:
        raise SystemExit("Unsupported plugin operation.")
