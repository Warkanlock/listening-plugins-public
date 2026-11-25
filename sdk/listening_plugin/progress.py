"""Optional progress reporting for a single staged fetch."""

from pathlib import Path


def report_download_progress(staging: Path, received: int, total: int) -> None:
    """Publish a whole percentage from the source's actual byte counts."""
    if type(received) is not int or type(total) is not int or not 0 <= received <= total or total == 0:
        return
    try:
        pending = staging / ".download-progress.next"
        pending.write_text(str(100 * received // total), encoding="ascii")
        pending.replace(staging / ".download-progress")
    except OSError:
        # Observability must never determine whether a fetch succeeds.
        pass
