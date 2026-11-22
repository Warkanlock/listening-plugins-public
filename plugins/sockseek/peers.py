"""Rank the provider's search results and encode opaque peer links."""
from pathlib import Path
from urllib.parse import quote


def ranked_links(groups, formats, preferred):
    results = []
    for group in groups:
        peer, item = group.get("User") or {}, group.get("File") or {}
        username, filename = peer.get("Username"), item.get("Filename")
        if not isinstance(username, str) or not username or not isinstance(filename, str):
            continue
        suffix = Path(filename).suffix.casefold().lstrip(".")
        if suffix not in formats:
            continue
        remote = quote(filename.replace("\\", "/"), safe="/").lstrip("/")
        link = f"slsk://{quote(username, safe='')}/{remote}"
        if len(link) > 512 or not remote:
            continue
        priority = (suffix == preferred, bool(peer.get("HasFreeUploadSlot")),
                    int(item.get("Bitrate") or 0), float(peer.get("UploadSpeed") or 0))
        results.append((priority, link))
    return (link for _, link in sorted(results, reverse=True))
