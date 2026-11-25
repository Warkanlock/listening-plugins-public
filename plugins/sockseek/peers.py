"""Rank Soulseek replies without treating a peer filename as verified metadata."""
from difflib import SequenceMatcher
from pathlib import PurePosixPath
import re
import unicodedata
from urllib.parse import quote

from listening_plugin import Candidate

VARIANTS = {"live", "acoustic", "demo", "karaoke", "remix", "instrumental", "cover"}
TRACK_PREFIX = re.compile(r"^\s*(?:\d{1,3}[-.]\d{1,3}|\d{1,3})(?:\s*[-._]+\s*|\s+)")


def words(text):
    text = unicodedata.normalize("NFKD", text.casefold())
    return " ".join(re.findall(r"[^\W_]+", "".join(char for char in text if not unicodedata.combining(char))))


def contains(text, phrase):
    return bool(phrase) and f" {phrase} " in f" {text} "


def artist_similarity(artist, context):
    if not artist or contains(context, artist):
        return 1.0
    parts = context.split()
    length = len(artist.split())
    return max((SequenceMatcher(None, artist, " ".join(parts[i:i + length])).ratio()
                for i in range(len(parts) - length + 1)), default=0.0)


def positive_int(value):
    return value if type(value) is int and value > 0 else None


def ranked_candidates(groups, formats, preferred, recording, source):
    title = words(recording.title or recording.search_title)
    hint = words(recording.search_title)
    # A short hint can omit an edition suffix. A longer hint can include a date
    # and venue that peers omit from filenames, so rank using the song title.
    if hint and title.startswith(hint + " "):
        title = hint
    artist = words(recording.artist)
    album = words(recording.album)
    requested_variants = set(words(f"{recording.title} {recording.version}").split()) & VARIANTS
    if not title:
        return []
    ranked = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        peer, item = group.get("User"), group.get("File")
        if not isinstance(peer, dict) or not isinstance(item, dict):
            continue
        username, filename = peer.get("Username"), item.get("Filename")
        if not isinstance(username, str) or not username or not isinstance(filename, str):
            continue
        path = PurePosixPath(filename.replace("\\", "/"))
        suffix = path.suffix.casefold().lstrip(".")
        if suffix not in formats:
            continue
        remote = quote(filename.replace("\\", "/"), safe="/").lstrip("/")
        key = f"slsk://{quote(username, safe='')}/{remote}"
        if len(key) > 512 or not remote:
            continue
        name = words(path.stem)
        unnumbered = words(TRACK_PREFIX.sub("", path.stem))
        title_score = max(1.0 if contains(text, title) else SequenceMatcher(None, title, text).ratio()
                          for text in {name, unnumbered})
        if title_score < .65:
            continue
        context = words("/".join(path.parts))
        artist_score = artist_similarity(artist, context)
        album_matches = contains(context, album)
        description = unnumbered if contains(unnumbered, title) else name
        if artist and not description.startswith(title):
            description = description.removeprefix(artist + " ")
        presented_variants = set(description.split()) & VARIANTS
        if album_matches and "live" in requested_variants and "live" in album.split():
            presented_variants.add("live")
        variant_matches = presented_variants == requested_variants
        duration = positive_int(item.get("Length"))
        bitrate = positive_int(item.get("Bitrate"))
        duration_matches = not duration or not recording.duration or abs(duration - recording.duration) <= 5.5
        quality_matches = suffix != "mp3" or not bitrate or bitrate >= 192
        reliable = title_score >= .88 and artist_score >= .86 and variant_matches and duration_matches and quality_matches
        candidate = Candidate(source, key, label=path.name[:180], format=suffix,
                              duration=duration, bitrate=bitrate, recommended=reliable)
        priority = (reliable, title_score, artist_score, duration_matches, variant_matches, album_matches,
                    quality_matches, suffix == preferred, bool(peer.get("HasFreeUploadSlot")), bitrate or 0)
        if key not in ranked or priority > ranked[key][0]:
            ranked[key] = priority, candidate
    return [item for _, item in sorted(ranked.values(), key=lambda entry: entry[0], reverse=True)]
