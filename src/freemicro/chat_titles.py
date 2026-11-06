"""Put the Agent Key's number on a chat's title, as ``1 · Code audit``.

Claude Code lets a ``UserPromptSubmit`` hook set a session's title. Tested in the
desktop app: it renames the chat straight away, and it can rename it again later.

That only happens when a prompt is sent, so a title can fall behind the keys: a
chat that gains or loses a key while idle keeps its old title until its next
prompt. The pad itself is always right.

A new chat is skipped until the app has given it a title, because setting one that
early might stop the app generating its own. The app's record is the first choice
for the title, since it reflects a rename made in the app.

A chat started through Remote Control is skipped too. Its transcript takes the new
title, but the app keeps showing the old one, so the two would only disagree.

Turned on with ``"agent_keys": {"group": "chat", "number_titles": true}``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from freemicro import desktop_sessions, focus, padconfig, permission_requests
from freemicro.state.hooks import session_id_of

SEPARATOR = " · "

#: One or more numbers we could have added. Only the six keys, so that a title
#: like ``7 · notes`` or ``10 things`` that the person wrote is left alone.
_NUMBER_RE = re.compile(r"^(?:[1-6] · )+")


def with_number(title: str, key: Optional[int]) -> str:
    """``title`` carrying ``key``'s number, or none if ``key`` is ``None``."""
    bare = _NUMBER_RE.sub("", title)
    return bare if key is None else f"{key}{SEPARATOR}{bare}"


def _key_of(session_id: str, config: Any) -> Optional[int]:
    """The 1-based Agent Key this chat is on, or ``None``."""
    for index, slot in enumerate(focus.current_slots(config)):
        session = slot.session
        if slot.owned and session is not None and session.session_id == session_id:
            return index + 1
    return None


def title_output(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The hook output that retitles this chat, or ``None`` to leave it alone."""
    if event.get("hook_event_name") != "UserPromptSubmit":
        return None
    config = padconfig.load().agent_keys
    if not config.number_titles or not permission_requests.pad_is_listening():
        return None
    if permission_requests.is_non_interactive():
        return None
    session_id = session_id_of(event)
    if desktop_sessions.started_by_remote_control(session_id):
        return None
    title = desktop_sessions.title_for(session_id) or str(
        event.get("session_title") or ""
    ).strip()
    if not title:
        return None
    wanted = with_number(title, _key_of(session_id, config))
    if not wanted or wanted == title:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "sessionTitle": wanted,
        }
    }


__all__ = ["SEPARATOR", "title_output", "with_number"]
