"""Python authoring contract for version 1 search and retrieval workers."""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Protocol

LIMIT = 64 * 1024


@dataclass(frozen=True)
class PluginContext:
    credentials: Path

    def settings(self) -> dict:
        return settings(self.credentials)


@dataclass(frozen=True)
class Recording:
    artist: str
    title: str
    formats: tuple[str, ...]
    preferred_format: str
    search_title: str
    id: str = ""
    album: str = ""
    duration: float = 0
    version: str = ""


@dataclass(frozen=True)
class Candidate:
    source: str
    id: str
    label: str = ""
    format: str = ""
    duration: Optional[int] = None
    bitrate: Optional[int] = None
    recommended: Optional[bool] = None


class ReadyPlugin(Protocol):
    def test(self) -> None: ...


class SearchPlugin(ReadyPlugin, Protocol):
    def search(self, recording: Recording) -> Iterable[Candidate]: ...


class ListingPlugin(ReadyPlugin, Protocol):
    def list(self, recording: Recording) -> Iterable[Candidate]: ...


class FetchPlugin(ReadyPlugin, Protocol):
    def fetch(self, candidate: Candidate, staging: Path) -> str: ...


class SharePlugin(ReadyPlugin, Protocol):
    def share(self) -> None: ...


class LoginUnavailable(Exception):
    """Login could not complete. An optional delay applies to the entire source."""

    def __init__(self, *args, retry_after_seconds=None):
        super().__init__(*args)
        if retry_after_seconds is not None and (
                type(retry_after_seconds) is not int or not 1 <= retry_after_seconds <= 86400):
            raise ValueError("Retry delay must be between 1 and 86400 seconds")
        self.retry_after_seconds = retry_after_seconds


class CompletedTimeout(Exception):
    """The operation has ended and its resources have been released."""


class Unavailable(Exception):
    """A permanent candidate failure with a public, credential-free message."""


class ConfigurationError(Exception):
    """An actionable, credential-free readiness message."""


def settings(directory):
    try:
        with (directory / "plugin-settings.json").open(encoding="utf-8") as source:
            data = json.loads(source.read(LIMIT + 1))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict) or not all(isinstance(value, str) for value in data.values()):
        raise ValueError("Invalid plugin settings.")
    return data
