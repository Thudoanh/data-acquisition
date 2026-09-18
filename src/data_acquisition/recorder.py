"""Live-specific capture policy used by the media worker."""

from datetime import datetime


def capture_is_complete(record_started_at: str, actual_start: str | None) -> bool:
    """Conservatively mark a live capture complete only if recording began at start."""
    if actual_start is None:
        return False
    return (datetime.fromisoformat(record_started_at) -
            datetime.fromisoformat(actual_start)).total_seconds() <= 5
