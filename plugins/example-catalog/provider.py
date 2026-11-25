"""Match catalog recordings in the room's preferred format order."""
import re
from pathlib import Path
from urllib.error import HTTPError

from listening_plugin import Candidate, Unavailable
from .source import CatalogSource

SOURCE = "example-catalog"
FORMATS = {".mp3", ".flac", ".m4a"}
IDENTITY = re.compile(r"[A-Za-z0-9_-]{1,80}")


class Provider:
    def __init__(self, context):
        self.source = CatalogSource(context.credentials)

    def test(self):
        self.source.recordings()

    def list(self, recording):
        desired = recording.formats
        title, artist = (value.strip().casefold() for value in (recording.title, recording.artist))
        matches = []
        for entry in self.source.recordings():
            if not isinstance(entry, dict):
                continue
            identity, formats = entry.get("id"), entry.get("formats")
            if not (isinstance(identity, str) and IDENTITY.fullmatch(identity) and isinstance(formats, list)):
                continue
            if str(entry.get("title", "")).strip().casefold() == title and str(entry.get("artist", "")).strip().casefold() == artist:
                matches.append((identity, formats))
        ids = (identity + suffix for suffix in desired if suffix in FORMATS
               for identity, formats in matches if suffix in formats)
        return (Candidate(SOURCE, value, label=value, format=Path(value).suffix[1:], recommended=True) for value in ids)

    def fetch(self, candidate, staging):
        name = candidate.id
        file = Path(name)
        if candidate.source != SOURCE or not IDENTITY.fullmatch(file.stem) or file.suffix not in FORMATS or file.name != name:
            raise Unavailable("Unsupported catalog candidate.")
        destination = Path(staging) / name
        try:
            self.source.download(name, destination)
        except HTTPError as error:
            if error.code in {404, 410}:
                raise Unavailable("The recording is unavailable.")
            raise
        return name
