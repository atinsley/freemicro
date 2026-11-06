"""Open a specific chat in the Claude desktop app, with no keystrokes.

A chat in the desktop app has no terminal tab to raise. The app does register
``claude://`` links, and its own Dock menu and Spotlight entries open a chat with
``claude://code/continue?session=<id>``. That works on a chat that's already open,
where ``claude --desktop --resume`` refuses.

The link needs the app's ``local_...`` id, while hooks report the CLI's session id.
The app records both in a ``local_*.json`` file for each chat, so this looks one up
from the other.

**None of this is documented.** The links and the files were found by reading the
app and tested against version 2.1.317 of its bundled Claude Code. An update could
change either one, so a failed lookup is an ordinary outcome: it returns ``""``,
and the Agent Key falls back to its keyboard shortcut.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Dict, Optional

NEW_CHAT_URL = "claude://code/new"

#: The form the app itself requires, and all that may ever reach a URL.
_LOCAL_ID_RE = re.compile(r"^local_[A-Za-z0-9-]{1,64}$")

#: CLI session id -> the file that named its app id. A chat's record doesn't move
#: and there are hundreds of them, so a key press shouldn't read them all each time.
_found: Dict[str, Path] = {}


def sessions_root() -> Path:
    return Path.home() / "Library" / "Application Support" / "Claude" / (
        "claude-code-sessions"
    )


def forget_lookups() -> None:
    _found.clear()


def chat_url(local_id: str) -> str:
    return f"claude://code/continue?session={local_id}"


def _record_in(path: Path, session_id: str) -> Optional[dict]:
    """The contents of ``path`` if it is a usable record of this session."""
    try:
        raw = path.read_bytes()
        if session_id.encode("utf-8") not in raw:  # Avoids parsing nearly every file.
            return None
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("cliSessionId") != session_id:
        return None
    if data.get("isArchived"):
        return None
    local_id = data.get("sessionId")
    if isinstance(local_id, str) and _LOCAL_ID_RE.match(local_id):
        return data
    return None


def _id_in(path: Path, session_id: str) -> Optional[str]:
    record = _record_in(path, session_id)
    return record["sessionId"] if record else None


def _scan(session_id: str) -> str:
    try:
        paths = list(sessions_root().glob("*/*/local_*.json"))
    except OSError:
        return ""
    for path in paths:
        local_id = _id_in(path, session_id)
        if local_id:
            _found[session_id] = path
            return local_id
    return ""


def local_id_for(session_id: str) -> str:
    """The app's id for a hook's session id, or ``""``. Never raises."""
    if not session_id:
        return ""
    known = _found.get(session_id)
    if known is not None:
        local_id = _id_in(known, session_id)
        if local_id:
            return local_id
        del _found[session_id]
    return _scan(session_id)


def title_for(session_id: str) -> str:
    """The title the app shows for this chat, or ``""``. Never raises."""
    if not local_id_for(session_id):  # Also remembers which file holds the record.
        return ""
    record = _record_in(_found[session_id], session_id)
    title = record.get("title") if record else None
    return title.strip() if isinstance(title, str) else ""


def started_by_remote_control(session_id: str) -> bool:
    """Was this chat started through Remote Control? Never raises."""
    if not local_id_for(session_id):
        return False
    record = _record_in(_found[session_id], session_id)
    return bool(record and record.get("remoteControlSpawn"))


def spawn_open(url: str) -> None:
    """Hand a link to macOS. Not waited on, because this runs on the key path."""
    subprocess.Popen(  # noqa: S603 - fixed argv, and the id is pattern-checked
        ["open", url],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


__all__ = [
    "NEW_CHAT_URL",
    "chat_url",
    "forget_lookups",
    "local_id_for",
    "sessions_root",
    "spawn_open",
    "started_by_remote_control",
    "title_for",
]
