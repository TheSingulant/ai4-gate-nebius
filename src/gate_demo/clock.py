"""Time helpers. All timestamps are whole seconds, RFC 3339 UTC with a ``Z``."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

Clock = Callable[[], int]


def system_clock() -> int:
    return int(time.time())


def rfc3339(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_rfc3339(text: str) -> int:
    moment = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


class ManualClock:
    """A clock tests and the scripted demo can move forward by hand."""

    def __init__(self, start: int = 1_790_000_000) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += seconds
