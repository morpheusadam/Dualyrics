"""Back-off for a line translation that failed while the line is still on screen (Live mode, the screen source).

After a failure the line is asked again after 1 s, 2 s, then 4 s; after a rate limit (HTTP 429, or every
Gemini key resting) after 4 s, then 8 s. At most ``MAX_ATTEMPTS`` attempts in all, within ``WINDOW`` seconds of
the first failure; a success starts the count again. (The schedule follows the ideas of OverTranslate and
Mimi; written here.)
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_ATTEMPTS = 3        # attempts per line, the first one included
WINDOW = 45.0           # seconds after the first failure when a line is no longer retried


def is_rate_limit(err: BaseException | None) -> bool:
    if err is None:
        return False
    from dualyrics.translate import KeysResting

    return isinstance(err, KeysResting) or "rate limit" in str(err).lower() or "429" in str(err)


@dataclass
class Backoff:
    attempts: int = 0          # failed attempts so far
    first_at: float = 0.0      # when the first one failed
    due: float = 0.0           # when to try again (0 = never)

    def failed(self, now: float, limited: bool = False) -> None:
        if self.attempts == 0:
            self.first_at = now
        self.attempts += 1
        if self.attempts >= MAX_ATTEMPTS or now - self.first_at >= WINDOW:
            self.due = 0.0
            return
        if limited:
            delay = 4.0 * (2 ** (self.attempts - 1))
        else:
            delay = min(4.0, 2.0 ** (self.attempts - 1))
        self.due = now + delay if now + delay - self.first_at <= WINDOW else 0.0

    def ok(self) -> None:
        self.attempts, self.first_at, self.due = 0, 0.0, 0.0

    def ready(self, now: float) -> bool:
        return bool(self.due) and now >= self.due

    def taken(self) -> None:
        """The retry was sent: no second one until it fails again."""
        self.due = 0.0
