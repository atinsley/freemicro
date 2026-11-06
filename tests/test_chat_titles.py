"""Numbering a chat's title with the Agent Key that opens it."""

from __future__ import annotations

import io
import json
from argparse import Namespace
from types import SimpleNamespace

import pytest

from freemicro import chat_titles, cli, daemon, desktop_sessions, focus, padconfig
from freemicro.agentkeys import AgentKeysError, parse_agent_keys
from freemicro.padconfig import load_default, parse

CLI_ID = "e7b56c88-f138-4830-9d38-da9f68531878"
LOCAL_ID = "local_709f264f-e1f0-4261-9bc4-9bb723070862"


# -- the setting -----------------------------------------------------------------


def test_off_by_default():
    assert parse_agent_keys({}).number_titles is False


def test_it_needs_chat_keys():
    with pytest.raises(AgentKeysError, match="number_titles"):
        parse_agent_keys({"number_titles": True})


def test_it_must_be_true_or_false():
    with pytest.raises(AgentKeysError, match="number_titles"):
        parse_agent_keys({"group": "chat", "number_titles": "yes"})


def test_the_setting_survives_a_round_trip():
    config = parse_agent_keys({"group": "chat", "number_titles": True})
    assert parse_agent_keys(config.to_dict()).number_titles is True


def test_the_shipped_keymap_leaves_it_off():
    assert load_default().agent_keys.number_titles is False


# -- the title -------------------------------------------------------------------


@pytest.mark.parametrize(
    "title, key, expected",
    [
        ("Code audit", 1, "1 · Code audit"),
        ("1 · Code audit", 1, "1 · Code audit"),
        ("1 · Code audit", 3, "3 · Code audit"),
        ("6 · Code audit", None, "Code audit"),
        ("Code audit", None, "Code audit"),
        ("1 · 2 · Code audit", 3, "3 · Code audit"),
        ("7 · not ours", 1, "1 · 7 · not ours"),
        ("10 things", 2, "2 · 10 things"),
        ("3 ·", 1, "1 · 3 ·"),
    ],
)
def test_the_number_is_added_replaced_or_removed(title, key, expected):
    assert chat_titles.with_number(title, key) == expected


# -- finding the title --------------------------------------------------------------


@pytest.fixture
def records(tmp_path, monkeypatch):
    folder = tmp_path / "records"
    (folder / "account" / "org").mkdir(parents=True)
    monkeypatch.setattr(desktop_sessions, "sessions_root", lambda: folder)

    def write(title):
        path = folder / "account" / "org" / f"{LOCAL_ID}.json"
        path.write_text(json.dumps({
            "sessionId": LOCAL_ID, "cliSessionId": CLI_ID,
            "isArchived": False, "title": title,
        }), encoding="utf-8")

    return write


def test_desktop_sessions_reads_a_chats_title(records):
    records("Ready")
    assert desktop_sessions.title_for(CLI_ID) == "Ready"


@pytest.mark.parametrize("title", [None, "", "   ", 7])
def test_a_missing_title_reads_as_empty(records, title):
    records(title)
    assert desktop_sessions.title_for(CLI_ID) == ""


def test_an_unknown_chat_has_no_title(records):
    assert desktop_sessions.title_for("unknown") == ""


# -- the hook's decision -------------------------------------------------------------


def _event(**extra):
    values = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": CLI_ID,
        "cwd": "/work/api",
        "prompt": "secret prompt",
    }
    values.update(extra)
    return values


def _title_of(output):
    assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    return output["hookSpecificOutput"]["sessionTitle"]


def _slots(index=None):
    slots = [SimpleNamespace(session=None, owned=True) for _ in range(6)]
    if index is not None:
        slots[index] = SimpleNamespace(
            session=SimpleNamespace(session_id=CLI_ID), owned=True
        )
    return slots


@pytest.fixture
def setup(monkeypatch, records):
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    monkeypatch.setattr(daemon, "lock_holder", lambda: {"pid": 1, "role": "daemon"})
    pad = parse({
        "version": 1, "bindings": {},
        "agent_keys": {"group": "chat", "number_titles": True},
    })
    monkeypatch.setattr(padconfig, "load", lambda *a, **k: pad)

    def key(index):
        monkeypatch.setattr(focus, "current_slots", lambda *a, **k: _slots(index))

    key(0)
    records("Code audit")
    return SimpleNamespace(key=key, records=records)


def test_a_chat_on_a_key_gets_its_number(setup):
    assert _title_of(chat_titles.title_output(_event())) == "1 · Code audit"


def test_the_number_is_the_keys(setup):
    setup.key(3)
    assert _title_of(chat_titles.title_output(_event())) == "4 · Code audit"


def test_a_chat_that_moved_key_gets_the_new_number(setup):
    setup.records("1 · Code audit")
    setup.key(4)
    assert _title_of(chat_titles.title_output(_event())) == "5 · Code audit"


def test_a_chat_already_correct_is_left_alone(setup):
    setup.records("1 · Code audit")
    assert chat_titles.title_output(_event()) is None


def test_a_chat_without_a_key_loses_its_number(setup):
    setup.records("2 · Code audit")
    setup.key(None)
    assert _title_of(chat_titles.title_output(_event())) == "Code audit"


def test_a_chat_without_a_key_and_a_plain_title_is_left_alone(setup):
    setup.key(None)
    assert chat_titles.title_output(_event()) is None


def test_a_new_chat_with_no_title_yet_is_left_alone(setup):
    setup.records(None)
    assert chat_titles.title_output(_event()) is None


def test_a_session_title_is_used_when_the_app_has_none(setup):
    setup.records(None)
    assert _title_of(
        chat_titles.title_output(_event(session_title="My name"))
    ) == "1 · My name"


def test_the_apps_title_is_preferred_so_a_rename_there_is_kept(setup):
    setup.records("Renamed in the app")
    assert _title_of(
        chat_titles.title_output(_event(session_title="1 · Old name"))
    ) == "1 · Renamed in the app"


def test_only_a_prompt_renames(setup):
    assert chat_titles.title_output(_event(hook_event_name="Stop")) is None


def test_nothing_happens_when_the_setting_is_off(setup, monkeypatch):
    pad = parse({"version": 1, "bindings": {}, "agent_keys": {"group": "chat"}})
    monkeypatch.setattr(padconfig, "load", lambda *a, **k: pad)
    assert chat_titles.title_output(_event()) is None


def test_nothing_happens_when_no_process_is_listening_to_the_pad(setup, monkeypatch):
    monkeypatch.setattr(daemon, "lock_holder", lambda: None)
    assert chat_titles.title_output(_event()) is None


def test_nothing_happens_in_an_sdk_session(setup, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-cli")
    assert chat_titles.title_output(_event()) is None


# -- running as the hook ----------------------------------------------------------------


def _run_hook(monkeypatch, capsys, event):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    code = cli.cmd_hook(Namespace())
    return code, capsys.readouterr().out


def test_the_hook_prints_only_the_title(setup, monkeypatch, capsys):
    code, out = _run_hook(monkeypatch, capsys, _event())
    assert code == 0
    assert _title_of(json.loads(out)) == "1 · Code audit"
    assert "secret prompt" not in out


def test_the_hook_prints_nothing_when_unchanged(setup, monkeypatch, capsys):
    setup.records("1 · Code audit")
    assert _run_hook(monkeypatch, capsys, _event()) == (0, "")


def test_a_failure_never_gets_in_the_way_of_a_prompt(setup, monkeypatch, capsys):
    def boom(event):
        raise RuntimeError("boom")

    monkeypatch.setattr(chat_titles, "title_output", boom)
    code, out = _run_hook(monkeypatch, capsys, _event())
    assert (code, out) == (0, "")


def test_a_broken_keymap_never_gets_in_the_way_of_a_prompt(setup, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise padconfig.PadConfigError("broken")

    monkeypatch.setattr(padconfig, "load", boom)
    code, out = _run_hook(monkeypatch, capsys, _event())
    assert (code, out) == (0, "")
