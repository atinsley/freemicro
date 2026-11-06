"""SDK sessions have no chat to open, so they must not take an Agent Key."""

from __future__ import annotations

import io
import json
from argparse import Namespace

import pytest

from freemicro import cli
from freemicro.state.engine import default_store


def _hook(monkeypatch, entrypoint, event_name="UserPromptSubmit", session="s1"):
    if entrypoint is None:
        monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", entrypoint)
    event = {"hook_event_name": event_name, "session_id": session, "cwd": "/work/api"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    assert cli.cmd_hook(Namespace()) == 0


def _tracked():
    return {s.session_id for s in default_store().sessions()}


@pytest.mark.parametrize("entrypoint", ["sdk-cli", "sdk-py", "sdk-ts"])
def test_an_sdk_session_is_not_tracked(monkeypatch, entrypoint):
    _hook(monkeypatch, entrypoint)
    assert "s1" not in _tracked()


@pytest.mark.parametrize("entrypoint", ["claude-desktop", "cli", None])
def test_an_interactive_session_is_tracked(monkeypatch, entrypoint):
    _hook(monkeypatch, entrypoint)
    assert "s1" in _tracked()


def test_a_session_already_tracked_is_removed_when_it_reports_as_sdk(monkeypatch):
    _hook(monkeypatch, "claude-desktop")
    assert "s1" in _tracked()
    _hook(monkeypatch, "sdk-cli", event_name="PreToolUse")
    assert "s1" not in _tracked()


def test_other_sessions_are_untouched(monkeypatch):
    _hook(monkeypatch, "claude-desktop", session="chat")
    _hook(monkeypatch, "sdk-cli", session="background")
    assert _tracked() == {"chat"}
