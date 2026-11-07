"""Notice when a Claude app update changes what the desktop features rely on.

Agent keys and chat numbering lean on things the app doesn't document: its
``claude://`` links, its chat record files, and its rule for accepting a title from
a hook. An update could change any of them, and the features would then fail
quietly, because a key just falls back to its shortcut and a number just doesn't
appear. This looks for each of those things, so ``freemicro status`` and ``freemicro
doctor`` can say what changed.

It proves that the pieces are still there, not that they still behave the same, so
no warning is not a guarantee. It reads files only: no link is opened.
"""

from __future__ import annotations

import json
import mmap
import plistlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from freemicro import desktop_sessions

#: Enough to see the current format, without judging it by chats from older versions.
_SAMPLE = 25

_KEYS = "Agent keys may fall back to their keyboard shortcuts."
_TITLES = "Chat numbers may stop appearing, or the wrong chats may be skipped."
_BOTH = f"{_KEYS} {_TITLES}"

#: The method's arguments are renamed by each build, so they can't be matched exactly.
_TITLE_RULE_RE = re.compile(rb"yieldsToProposedTitle\(\w+,\w+\)\{return[^}]{0,500}\}")
_TITLE_RULE_PARTS = (b'titleSource!=="auto"', b"titleFromPr", b"remoteControlSpawn")
_LINKS = (b"claude://code/continue", b"claude://code/new")


@dataclass(frozen=True)
class Problem:
    summary: str
    effect: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def app_path() -> Path:
    return Path("/Applications/Claude.app")


def _scheme_problems(app: Path) -> list[Problem]:
    try:
        plist = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
        schemes = {
            scheme
            for entry in plist.get("CFBundleURLTypes", [])
            for scheme in entry.get("CFBundleURLSchemes", [])
        }
    except (OSError, ValueError, AttributeError, TypeError, plistlib.InvalidFileException):
        schemes = set()
    if "claude" in schemes:
        return []
    return [Problem("The app no longer registers claude:// links.", _KEYS)]


def _code_problems(app: Path) -> list[Problem]:
    path = app / "Contents" / "Resources" / "app.asar"
    try:
        with path.open("rb") as handle, mmap.mmap(
            handle.fileno(), 0, access=mmap.ACCESS_READ
        ) as code:
            return _problems_in_code(code)
    except (OSError, ValueError):  # mmap raises ValueError on an empty file.
        return [Problem(f"Couldn't read the app's code at {path}.", _BOTH)]


def _problems_in_code(code: Any) -> list[Problem]:
    problems = [
        Problem(f"The app's code no longer mentions {link.decode()}.", _KEYS)
        for link in _LINKS
        if code.find(link) < 0
    ]
    rule = _TITLE_RULE_RE.search(code)
    if rule is None:
        problems.append(Problem("Couldn't find the app's title rule.", _TITLES))
    elif not all(part in rule.group() for part in _TITLE_RULE_PARTS):
        problems.append(Problem("The app's title rule has changed.", _TITLES))
    return problems


def _newest_records(root: Path) -> list[dict[str, Any]]:
    """The newest records that can be read, as dictionaries."""
    paths = sorted(
        root.glob("*/*/local_*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    records = []
    for path in paths[:_SAMPLE]:
        try:
            data = json.loads(path.read_bytes())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            records.append(data)
    return records


def _has_ids(record: dict[str, Any]) -> bool:
    local_id = record.get("sessionId")
    return (
        isinstance(local_id, str)
        and desktop_sessions.LOCAL_ID_RE.match(local_id) is not None
        and isinstance(record.get("cliSessionId"), str)
    )


def _record_problems() -> list[Problem]:
    root = desktop_sessions.sessions_root()
    if not root.is_dir():
        return [Problem(
            f"The app's chat records aren't at {root}. "
            "You can ignore this if you don't use Claude Code in the app.",
            _BOTH,
        )]
    if not any(root.glob("*/*/local_*.json")):
        if not any(root.iterdir()):  # No chats yet.
            return []
        return [Problem(f"The app's chat records have moved within {root}.", _BOTH)]

    records = _newest_records(root)
    if not records:
        return [Problem("The app's chat records can no longer be read.", _BOTH)]
    problems = []
    if not any(_has_ids(record) for record in records):
        problems.append(Problem("The app's chat records have changed their ids.", _BOTH))
    if not any(isinstance(record.get("title"), str) for record in records):
        problems.append(Problem("The app's chat records no longer hold a title.", _TITLES))
    if not any("titleSource" in record for record in records):
        problems.append(
            Problem("The app's chat records no longer hold a title's source.", _TITLES)
        )
    return problems


def check() -> list[Problem]:
    """What no longer matches. Empty if all is well or there's no app. Never raises."""
    try:
        app = app_path()
        if not app.exists():
            return []
        return _scheme_problems(app) + _code_problems(app) + _record_problems()
    except Exception as exc:  # noqa: BLE001 - a health check must not be what breaks
        return [Problem(f"Couldn't finish checking the app: {exc}", _BOTH)]


__all__ = ["Problem", "app_path", "check"]
