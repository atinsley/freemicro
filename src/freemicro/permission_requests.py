"""Answer a Claude Code permission request from the pad, through a hook.

:mod:`freemicro.permission_prompt` answers by typing into a terminal tab, which
cannot reach a session with no tab, such as one in the Claude desktop app. This
answers through Claude Code's ``PermissionRequest`` hook instead, so it works
wherever hooks do and sends no keystrokes.

How it fits together, as tested against the desktop app:

* The prompt stays on screen and usable while the hook runs, so a hook can wait.
  The hook's answer closes the prompt, and answering on screen does not wait for
  the hook. So the hook and the person simply race, and either may answer.
* The hook writes a small request file and polls for an answer file. The pad
  process, which owns the keys, writes the answer. Files, because the two are
  separate processes that already share ``~/.freemicro``.
* An answer on screen does not tell the hook. The session's next event does:
  :func:`retire_session` removes the request, and the hook exits with no output,
  which is no decision. Answering a request after that is harmless, because the
  hook has already gone.

Every failure ends in the same place: no decision, and the prompt stays with the
person. Only a press of the pad key can produce an allow.

Tool input is never written to disk. A request records which tool in which
project, which is all a key press needs.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional

from freemicro.config import config_home

APPROVE = "approve"
REJECT = "reject"
ALWAYS = "always"
ANSWERS = (APPROVE, REJECT, ALWAYS)

#: How long the hook waits for the pad. Bounded because a session that cannot
#: show a prompt, such as a background subagent, is stalled for this long.
WAIT_SECONDS = 300.0

#: What the installer gives Claude Code as the hook's timeout. Longer than the
#: wait, so the hook always finishes by itself rather than being killed.
HOOK_TIMEOUT_SECONDS = 310

POLL_SECONDS = 0.1

#: A request younger than this cannot be answered. A press already on its way
#: down when a prompt appears must not approve something nobody has read.
MIN_AGE_SECONDS = 0.5

#: After answering, further presses are ignored for this long, so an impatient
#: second press cannot answer the next request in the queue.
COOLDOWN_SECONDS = 1.0

#: The roles of the processes that listen to the pad's keys. The web UI can hold
#: the pad too, but pressing a key there does not answer anything.
_LISTENING_ROLES = ("run", "daemon", "keys", "start")

ANSWERED = "answered"
HELD = "held"
NONE = "none"

DENY_MESSAGE = "Declined from the FreeMicro pad."

#: Only these suggested updates may be applied by "always". A suggestion can also
#: change the session's permission mode, which is far more than "don't ask me
#: about this again".
_ALWAYS_UPDATE_TYPES = ("addRules",)

_last_answer_at: Optional[float] = None


@dataclass(frozen=True)
class Request:
    """One permission request a hook is waiting on."""

    id: str
    session_id: str
    cwd: str
    tool_name: str
    created_at: float
    pid: int

    @property
    def project(self) -> str:
        return os.path.basename(self.cwd.rstrip("/")) or self.cwd or "this session"

    def describe(self) -> str:
        return f"{self.tool_name or 'a tool'} in {self.project}"


class Outcome(NamedTuple):
    """What pressing the key did. ``HELD`` means ignored on purpose."""

    status: str
    request: Optional[Request] = None


def directory() -> Path:
    return config_home() / "permissions"


def _request_path(request_id: str) -> Path:
    return directory() / f"{request_id}.json"


def _answer_path(request_id: str) -> Path:
    return directory() / f"{request_id}.answer"


def _write_private(path: Path, payload: Dict[str, Any]) -> None:
    """Write atomically, so a reader never sees half a file. ``mkstemp`` is 0600."""
    fd, temp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _session_of(event: Dict[str, Any]) -> str:
    from freemicro.state.hooks import session_id_of

    return session_id_of(event)


def create(
    event: Dict[str, Any], now: Optional[float] = None, pid: Optional[int] = None
) -> Request:
    """Record that a hook is waiting. Deliberately keeps no tool input."""
    request = Request(
        id=secrets.token_hex(8),
        session_id=_session_of(event),
        cwd=str(event.get("cwd", "") or ""),
        tool_name=str(event.get("tool_name", "") or ""),
        created_at=time.time() if now is None else float(now),
        pid=os.getpid() if pid is None else int(pid),
    )
    folder = directory()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    _write_private(
        _request_path(request.id),
        {
            "id": request.id,
            "session_id": request.session_id,
            "cwd": request.cwd,
            "tool_name": request.tool_name,
            "created_at": request.created_at,
            "pid": request.pid,
        },
    )
    return request


def remove(request: Request) -> None:
    _unlink(_request_path(request.id))
    _unlink(_answer_path(request.id))


def requests() -> List[Request]:
    """Every request on disk, unfiltered. Unreadable files are skipped."""
    found: List[Request] = []
    try:
        paths = list(directory().glob("*.json"))
    except OSError:
        return found
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            found.append(
                Request(
                    id=path.stem,
                    session_id=str(data["session_id"]),
                    cwd=str(data.get("cwd", "")),
                    tool_name=str(data.get("tool_name", "")),
                    created_at=float(data["created_at"]),
                    pid=int(data["pid"]),
                )
            )
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return found


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # It exists, it just isn't ours to signal.
    return True


def _normalise_project(path: str) -> str:
    return os.path.normpath(os.path.expanduser(path)) if path else ""


def pending(now: Optional[float] = None, project: str = "") -> List[Request]:
    """Requests that can still be answered, newest first. Tidies up dead ones."""
    when = time.time() if now is None else float(now)
    wanted = _normalise_project(project)
    live: List[Request] = []
    for request in requests():
        if not _alive(request.pid) or when - request.created_at > WAIT_SECONDS:
            remove(request)
            continue
        if _answer_path(request.id).exists():
            continue
        if wanted and _normalise_project(request.cwd) != wanted:
            continue
        live.append(request)
    live.sort(key=lambda r: (-r.created_at, r.id))
    return live


def write_answer(request: Request, answer: str) -> None:
    if answer not in ANSWERS:
        raise ValueError(f"answer must be one of {', '.join(ANSWERS)}, got {answer!r}")
    _write_private(_answer_path(request.id), {"answer": answer})


def read_answer(request: Request) -> str:
    """The answer given to ``request``, or ``""``. Anything unexpected is no answer."""
    try:
        data = json.loads(_answer_path(request.id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    answer = data.get("answer") if isinstance(data, dict) else None
    return answer if answer in ANSWERS else ""


def forget_recent_answer() -> None:
    """Clear the cooldown. For tests."""
    global _last_answer_at
    _last_answer_at = None


def answer_newest(
    answer: str,
    project: str = "",
    now: Optional[float] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> Outcome:
    """Answer the newest waiting request. Called from the pad's key press."""
    global _last_answer_at
    if answer not in ANSWERS:
        raise ValueError(f"answer must be one of {', '.join(ANSWERS)}, got {answer!r}")
    when = time.time() if now is None else float(now)
    ticks = monotonic()
    if _last_answer_at is not None and ticks - _last_answer_at < COOLDOWN_SECONDS:
        return Outcome(HELD)
    waiting = pending(now=when, project=project)
    if not waiting:
        return Outcome(NONE)
    request = waiting[0]
    if when - request.created_at < MIN_AGE_SECONDS:
        return Outcome(HELD)
    write_answer(request, answer)
    _last_answer_at = ticks
    return Outcome(ANSWERED, request)


def retire_session(session_id: str) -> None:
    """The session has moved on, so whatever it was asking has been answered."""
    for request in requests():
        if request.session_id == session_id:
            remove(request)


def wait_for_answer(
    request: Request,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Wait for the pad. ``""`` means no answer came: stand aside."""
    deadline = request.created_at + WAIT_SECONDS
    path = _request_path(request.id)
    while clock() < deadline:
        answer = read_answer(request)
        if answer:
            return answer
        if not path.exists():
            return ""
        sleep(POLL_SECONDS)
    return read_answer(request)


def decision_output(answer: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """The JSON that tells Claude Code what was decided."""
    if answer == REJECT:
        decision: Dict[str, Any] = {"behavior": "deny", "message": DENY_MESSAGE}
    else:
        decision = {"behavior": "allow"}
    if answer == ALWAYS:
        suggestions = event.get("permission_suggestions")
        updates = [
            update
            for update in (suggestions if isinstance(suggestions, list) else [])
            if isinstance(update, dict) and update.get("type") in _ALWAYS_UPDATE_TYPES
        ]
        if updates:
            decision["updatedPermissions"] = updates
    return {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": decision,
        }
    }


def pad_is_listening() -> bool:
    from freemicro import daemon

    holder = daemon.lock_holder()
    return bool(holder) and holder.get("role") in _LISTENING_ROLES


def is_non_interactive() -> bool:
    """SDK sessions have no prompt, and may have no person, so never stall them."""
    return os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk")


def handle(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Run the hook: wait for the pad, then return the decision or ``None``."""
    if is_non_interactive() or not pad_is_listening():
        return None
    request = create(event)
    try:
        answer = wait_for_answer(request)
    finally:
        remove(request)
    return decision_output(answer, event) if answer else None


__all__ = [
    "ALWAYS",
    "ANSWERED",
    "ANSWERS",
    "APPROVE",
    "COOLDOWN_SECONDS",
    "DENY_MESSAGE",
    "HELD",
    "HOOK_TIMEOUT_SECONDS",
    "MIN_AGE_SECONDS",
    "NONE",
    "REJECT",
    "WAIT_SECONDS",
    "Outcome",
    "Request",
    "answer_newest",
    "create",
    "decision_output",
    "directory",
    "forget_recent_answer",
    "handle",
    "is_non_interactive",
    "pad_is_listening",
    "pending",
    "read_answer",
    "remove",
    "requests",
    "retire_session",
    "wait_for_answer",
    "write_answer",
]
