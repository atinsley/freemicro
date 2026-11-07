"""Read the pad's battery from macOS, and post a banner when it runs low.

macOS keeps its own reading of a Bluetooth device's battery, and ``pmset -g accps``
prints it. Asking macOS puts nothing on the pad's vendor channel, so this can't get
in the way of a lighting write, and it works without the menu bar running.

A pad on USB may not be listed. That reads as "no reading", which is never low.

Turned on with ``"lighting": {"battery": {"notify": true}}``. It shares its
``threshold`` with the lighting cue, but neither needs the other.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from freemicro.alerts import _display_notification, spawn

#: How often the daemon looks. A pad's battery takes days to run down.
POLL_SECONDS = 300.0

#: Warn again each time the level falls this many more points.
REPEAT_POINTS = 5
#: How far above the threshold it must rise to count as recharged, so that a reading
#: wobbling between 20% and 21% doesn't warn every time.
RECHARGED_POINTS = 5

_PAD_LINE_RE = re.compile(r"Codex Micro[^\t\n]*\t\s*(\d{1,3})%;[ \t]*([^;\n]*)")
_CHARGING_RE = re.compile(r"^(charging|charged|finishing charge)", re.IGNORECASE)


@dataclass(frozen=True)
class Reading:
    level: int
    charging: bool

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "charging": self.charging}


def parse_pmset(text: str) -> Reading | None:
    """The pad's reading in ``pmset -g accps`` output, or ``None``."""
    match = _PAD_LINE_RE.search(text)
    if match is None or int(match.group(1)) > 100:
        return None
    charging = _CHARGING_RE.match(match.group(2).strip()) is not None
    return Reading(int(match.group(1)), charging)


def _pmset_output() -> str:
    # Device names can hold bytes that aren't UTF-8, such as the apostrophe in
    # "Aaron's AirPods", so an exact decode would throw the whole answer away.
    return subprocess.run(
        ["/usr/bin/pmset", "-g", "accps"],
        capture_output=True,
        timeout=5,
        check=False,
    ).stdout.decode("utf-8", "replace")


def read_pad() -> Reading | None:
    """The pad's battery right now, or ``None`` if unknown. Never raises."""
    try:
        return parse_pmset(_pmset_output())
    except Exception:  # noqa: BLE001 - no reading is just "battery unknown"
        return None


class LowBatteryWarner:
    """Decides when a reading is worth a banner, and posts it."""

    def __init__(self, runner: Callable[[Any], None] = spawn) -> None:
        self._runner = runner
        #: The level last warned about, until the pad has been charged.
        self._warned_at: int | None = None

    def see(self, reading: Reading | None, threshold: int) -> None:
        if reading is None:
            return
        level = reading.level
        if reading.charging or level >= threshold + RECHARGED_POINTS:
            self._warned_at = None
            return
        if level > threshold:
            return
        if self._warned_at is None or level <= self._warned_at - REPEAT_POINTS:
            self._warned_at = level
            script = _display_notification(
                "Codex Micro battery is low", f"{level}% left. Plug it in soon."
            )
            self._runner(["osascript", "-e", script])


def _start_thread(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="freemicro-battery", daemon=True).start()


class BatteryWatch:
    """Looks at the battery on a slow clock, from the daemon's tick.

    The read happens on its own thread, because the tick also drives the lights and
    a stuck ``pmset`` mustn't be able to freeze them.
    """

    def __init__(
        self,
        read: Callable[[], Reading | None] = read_pad,
        warner: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
        start: Callable[[Callable[[], None]], None] = _start_thread,
    ) -> None:
        self._read = read
        self._warner = warner if warner is not None else LowBatteryWarner()
        self._clock = clock
        self._start = start
        self._checked_at: float | None = None
        self._busy = False

    def poll(self, config: Any) -> None:
        """Start a check if one is due. Returns at once, and never raises."""
        if not config.notify or self._busy:
            return
        now = self._clock()
        if self._checked_at is not None and now - self._checked_at < POLL_SECONDS:
            return
        self._checked_at = now
        self._busy = True
        threshold = config.threshold

        def job() -> None:
            try:
                self._warner.see(self._read(), threshold)
            except Exception as exc:  # noqa: BLE001 - the next check gets another try
                print(f"  [battery] check failed: {exc}", flush=True)
            finally:
                self._busy = False

        try:
            self._start(job)
        except Exception:  # noqa: BLE001 - never let this reach the render loop
            self._busy = False


__all__ = [
    "POLL_SECONDS",
    "BatteryWatch",
    "LowBatteryWarner",
    "Reading",
    "parse_pmset",
    "read_pad",
]
