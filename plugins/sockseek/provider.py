"""Search and fetch recordings with the Sockseek command-line tool."""
import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from listening_plugin import Candidate, CompletedTimeout, ConfigurationError, Unavailable
from .configuration import has_login
from .session import Session
from .peers import ranked_links

BINARY = "/opt/sockseek/sockseek"
SOURCE = "soulseek"
SUFFIXES = {".mp3", ".flac", ".m4a"}


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

    def search(self, recording):
        formats = [suffix[1:] for suffix in recording.formats if suffix in SUFFIXES]
        if not formats:
            return ()
        preferred = recording.preferred_format
        if preferred not in formats:
            preferred = formats[0]
        parts = (recording.artist, recording.search_title)
        query = " - ".join(value for value in parts if value).strip()
        if not query or query.startswith("-"):
            return ()
        process = self.run(
            query, ["--progress-json", "--print", "json-all", "--format", ",".join(formats),
             "--pref-format", preferred, "--input-type", "string", "--song",
             "--search-timeout", "6000"], capture_output=True, text=True, timeout=22,
        )
        return (Candidate(SOURCE, link) for link in ranked_links(search_results(process), formats, preferred))

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
