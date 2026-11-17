"""Lyrics timing helpers: the current line for a playback position and the look-ahead window."""

from __future__ import annotations

import bisect

LOOKAHEAD = 12      # lines translated ahead of the singer while the whole-song translation is still running


def current_index(times: list[float], pos: float) -> int:
    """Index of the line being sung at ``pos`` seconds (-1 before the first line)."""
    return bisect.bisect_right(times, pos) - 1


def lookahead_window(times: list[float], pos: float, n: int = LOOKAHEAD, done: set[int] | None = None) -> list[int]:
    """Indexes of the current line and the next ``n - 1`` lines that are not translated yet (in order)."""
    if not times or n <= 0:
        return []
    start = max(0, current_index(times, pos))
    have = done or set()
    return [i for i in range(start, min(len(times), start + n)) if i not in have]
