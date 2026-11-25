"""Search and fetch recordings with the Sockseek command-line tool."""
import json
import subprocess
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from listening_plugin import CompletedTimeout, ConfigurationError, LoginUnavailable, Unavailable
from .configuration import has_login
from .session import Session
from .peers import ranked_candidates

BINARY = "/opt/sockseek/sockseek"
SOURCE = "soulseek"
SUFFIXES = {".mp3", ".flac", ".m4a"}


def query_words(value):
    text = unicodedata.normalize("NFC", value.lower())
    return " ".join("".join(char if char.isalnum() or unicodedata.category(char).startswith("M") else " "
                            for char in text).split())


def invoke(command, **options):
    try:
        return subprocess.run(command, **options)
    except subprocess.TimeoutExpired as error:
        # run() has killed and reaped the child before it raises.
        raise CompletedTimeout from error


def search_results(process):
    results = None
    failure_reason = None
    for line in process.stdout.splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if isinstance(entry, list):
            if results is not None:
                raise RuntimeError("Sockseek returned more than one search result.")
            results = entry
        elif isinstance(entry, dict) and entry.get("type") == "track_state":
            state = entry.get("data", {})
            if state.get("terminalOutcome") == "Failed":
                failure_reason = state.get("failureReason")
    # An empty array and exit 1 also accompany real failures. Only the
    # terminal track event can confirm that the search found no matches.
    if results is None or (process.returncode and not (
        process.returncode == 1 and results == []
        and failure_reason in ("NoSearchResults", "NoMatchingResults")
    )):
        raise RuntimeError("Sockseek search failed.")
    return results


class Provider:
    def __init__(self, context, binary=BINARY):
        self.credentials = context.credentials
        self.binary = binary

    def run(self, query, options, **arguments):
        return Session(self.binary, self.credentials).run(query, options, **arguments)

    def test(self):
        if not has_login(self.credentials):
            raise ConfigurationError("Set the account name and password.")
        result = invoke([self.binary, "--version"], capture_output=True, timeout=5)
        if result.returncode:
            raise ConfigurationError("Sockseek is unavailable.")
        Session(self.binary, self.credentials).check_login()

    def list(self, recording):
        formats = [suffix[1:] for suffix in recording.formats if suffix in SUFFIXES]
        if not formats:
            return ()
        preferred = recording.preferred_format
        if preferred not in formats:
            preferred = formats[0]
        title = query_words(recording.title or recording.search_title)
        hint = query_words(recording.search_title) or title
        artist = query_words(recording.artist)
        if not title:
            return ()
        query = " ".join(part for part in (artist, hint) if part)
        ranked = self._list_query(query, recording, formats, preferred)
        if query != title and not any(candidate.recommended for candidate in ranked):
            try:
                broader = self._list_query(title, recording, formats, preferred)
            except (CompletedTimeout, LoginUnavailable, RuntimeError):
                if not ranked:
                    raise
            else:
                known = {candidate.id for candidate in broader}
                ranked = [candidate for candidate in broader if candidate.recommended] + [
                    candidate for candidate in ranked if candidate.id not in known
                ] + [candidate for candidate in broader if not candidate.recommended]
        return ranked

    def _list_query(self, query, recording, formats, preferred):
        process = self.run(
            query, ["--progress-json", "--print", "json-all", "--format", ",".join(formats),
             "--pref-format", preferred, "--input-type", "string", "--song",
             "--search-timeout", "6500"], capture_output=True, text=True, timeout=14,
        )
        return ranked_candidates(search_results(process), formats, preferred, recording, SOURCE)

    def fetch(self, candidate, directory):
        link = candidate.id
        parts = urlsplit(link)
        if (candidate.source != SOURCE or parts.scheme != "slsk" or not parts.netloc
                or not parts.path or parts.query or parts.fragment or len(link) > 512):
            raise Unavailable("Unsupported Soulseek candidate.")
        directory = Path(directory)
        process = self.run(
            link, ["--output-dir", str(directory), "--max-retries", "2",
             "--max-stale-time", "30000"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90,
        )
        if process.returncode:
            raise Unavailable("This peer did not complete the transfer.")
        for file in directory.rglob("*"):
            if (file.is_file() and not file.is_symlink() and file.suffix.lower() in SUFFIXES
                    and directory.resolve() in file.resolve().parents):
                target = directory / ("audio" + file.suffix.lower())
                file.replace(target)
                return target.name
        raise Unavailable("The peer returned no audio candidate.")
