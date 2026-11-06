"""Agent Keys open a specific chat in the Claude desktop app through its links."""

from __future__ import annotations

import json

import pytest

from freemicro import desktop_sessions as ds
from freemicro import focus, hooks_install
from freemicro.input.actions import Action, Backend, perform
from freemicro.padconfig import load_default, parse
from freemicro.state.engine import AgentState, SessionState

#: Taken at import, because a shared fixture replaces it for every test.
REAL_SPAWN_OPEN = ds.spawn_open

CLI_ID = "e7b56c88-f138-4830-9d38-da9f68531878"
LOCAL_ID = "local_709f264f-e1f0-4261-9bc4-9bb723070862"


class RecordingBackend(Backend):
    description = "recording"

    def __init__(self):
        self.keys = []
        self.scripts = []

    def press_key(self, combo):
        self.keys.append(combo)

    def run_applescript(self, script):
        self.scripts.append(script)


@pytest.fixture
def root(tmp_path, monkeypatch):
    folder = tmp_path / "claude-code-sessions"
    (folder / "account" / "org").mkdir(parents=True)
    monkeypatch.setattr(ds, "sessions_root", lambda: folder)
    ds.forget_lookups()
    yield folder
    ds.forget_lookups()


@pytest.fixture
def opened(monkeypatch):
    urls = []
    monkeypatch.setattr(ds, "spawn_open", urls.append)
    return urls


def _record(root, local_id=LOCAL_ID, cli_id=CLI_ID, **extra):
    data = {"sessionId": local_id, "cliSessionId": cli_id, "isArchived": False}
    data.update(extra)
    path = root / "account" / "org" / f"{local_id}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _session(session_id=CLI_ID, **extra):
    values = {
        "session_id": session_id,
        "state": AgentState.IDLE,
        "updated_at": 1000.0,
        "cwd": "/work/api",
    }
    values.update(extra)
    return SessionState(**values)


# -- the lookup -----------------------------------------------------------------


def test_finds_the_apps_id_for_a_hook_session_id(root):
    _record(root)
    assert ds.local_id_for(CLI_ID) == LOCAL_ID


def test_an_unknown_session_has_no_id(root):
    _record(root)
    assert ds.local_id_for("11111111-2222-3333-4444-555555555555") == ""


def test_no_sessions_folder_has_no_id(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "sessions_root", lambda: tmp_path / "missing")
    ds.forget_lookups()
    assert ds.local_id_for(CLI_ID) == ""


def test_an_archived_chat_is_not_opened(root):
    _record(root, isArchived=True)
    assert ds.local_id_for(CLI_ID) == ""


def test_an_id_merely_mentioned_in_another_chat_does_not_match(root):
    _record(root, cli_id="other", title=f"Notes about {CLI_ID}")
    assert ds.local_id_for(CLI_ID) == ""


@pytest.mark.parametrize(
    "bad", ["local_../../etc", 'local_x"; rm -rf ~', "cse_123", "", "local_" + "a" * 65]
)
def test_an_unexpected_app_id_is_refused(root, bad):
    path = root / "account" / "org" / "local_bad.json"
    path.write_text(
        json.dumps({"sessionId": bad, "cliSessionId": CLI_ID}), encoding="utf-8"
    )
    assert ds.local_id_for(CLI_ID) == ""


def test_broken_files_are_skipped(root):
    (root / "account" / "org" / "local_broken.json").write_text(
        f"not json {CLI_ID}", encoding="utf-8"
    )
    _record(root)
    assert ds.local_id_for(CLI_ID) == LOCAL_ID


def test_a_found_id_is_remembered(root, monkeypatch):
    _record(root)
    assert ds.local_id_for(CLI_ID) == LOCAL_ID
    monkeypatch.setattr(ds, "_scan", lambda session_id: pytest.fail("scanned again"))
    assert ds.local_id_for(CLI_ID) == LOCAL_ID


def test_a_chat_archived_after_it_was_remembered_stops_matching(root):
    _record(root)
    assert ds.local_id_for(CLI_ID) == LOCAL_ID
    _record(root, isArchived=True)
    assert ds.local_id_for(CLI_ID) == ""


def test_a_chat_that_appears_later_is_found(root):
    assert ds.local_id_for(CLI_ID) == ""
    _record(root)
    assert ds.local_id_for(CLI_ID) == LOCAL_ID


def test_the_links_are_the_apps_own():
    assert ds.chat_url(LOCAL_ID) == f"claude://code/continue?session={LOCAL_ID}"
    assert ds.NEW_CHAT_URL == "claude://code/new"


# -- planning -------------------------------------------------------------------


def test_a_desktop_chat_plans_its_link(root):
    _record(root)
    plan = focus.plan_for_session(_session(), tty_lookup=lambda pid: "")
    assert plan.method == focus.METHOD_DESKTOP
    assert plan.url == ds.chat_url(LOCAL_ID)
    assert plan.actionable
    assert not plan.slot_empty
    assert "Claude app" in plan.describe()


def test_a_terminal_session_still_uses_its_tab(root):
    _record(root)
    session = _session(tty="/dev/ttys004", term_program="Apple_Terminal")
    plan = focus.plan_for_session(session, tty_lookup=lambda pid: "")
    assert plan.method == focus.METHOD_TAB


def test_an_unknown_session_still_does_nothing(root):
    plan = focus.plan_for_session(_session(), tty_lookup=lambda pid: "")
    assert plan.method == focus.METHOD_NONE
    assert plan.session is not None


def test_a_prompt_answer_by_keystroke_never_targets_a_desktop_chat(root):
    from freemicro import permission_prompt

    _record(root)
    session = _session(
        state=AgentState.WAITING, last_event="Notification",
        pid=1, process_alive=True, updated_at=1000.0,
    )
    plan = permission_prompt.plan("approve", sessions=[session], now=1001.0)
    assert not plan.actionable


def test_perform_opens_the_link(root, opened):
    _record(root)
    backend = RecordingBackend()
    plan = focus.plan_for_session(_session(), tty_lookup=lambda pid: "")
    assert focus.perform(plan, backend) is True
    assert opened == [ds.chat_url(LOCAL_ID)]
    assert backend.scripts == []
    assert backend.keys == []


# -- the key --------------------------------------------------------------------


def _key(**params):
    values = {"slot": 0, "new_terminal": False}
    values.update(params)
    return Action("focus_session", values)


def _plan_is(monkeypatch, plan):
    monkeypatch.setattr(focus, "plan_for_slot", lambda *a, **k: plan)


def test_an_empty_key_starts_a_new_chat_when_asked(monkeypatch, opened):
    _plan_is(monkeypatch, focus.FocusPlan(reason="no live session for this key"))
    backend = RecordingBackend()
    perform(_key(new_chat=True, otherwise="cmd+shift+]"), backend)
    assert opened == [ds.NEW_CHAT_URL]
    assert backend.keys == []


def test_an_empty_key_does_not_start_a_chat_unless_asked(monkeypatch, opened):
    _plan_is(monkeypatch, focus.FocusPlan(reason="no live session for this key"))
    perform(_key(), RecordingBackend())
    assert opened == []


def test_new_chat_wins_over_a_new_terminal(monkeypatch, opened):
    _plan_is(monkeypatch, focus.FocusPlan(reason="no live session for this key"))
    backend = RecordingBackend()
    perform(_key(new_chat=True, new_terminal=True, terminal="Terminal"), backend)
    assert opened == [ds.NEW_CHAT_URL]
    assert backend.scripts == []


def test_a_chat_that_cant_be_reached_falls_back_to_the_shortcut(monkeypatch, opened):
    _plan_is(monkeypatch, focus.FocusPlan(session=_session(), reason="unknown"))
    backend = RecordingBackend()
    perform(_key(new_chat=True, otherwise="cmd+shift+]"), backend)
    assert backend.keys == ["cmd+shift+]"]
    assert opened == []


def test_a_reachable_chat_doesnt_also_press_the_shortcut(root, monkeypatch, opened):
    _record(root)
    _plan_is(monkeypatch, focus.plan_for_session(_session(), tty_lookup=lambda p: ""))
    backend = RecordingBackend()
    perform(_key(new_chat=True, otherwise="cmd+shift+]"), backend)
    assert opened == [ds.chat_url(LOCAL_ID)]
    assert backend.keys == []


def test_list_describes_both_fallbacks(monkeypatch):
    _plan_is(monkeypatch, focus.FocusPlan(reason="no live session for this key"))
    described = _key(new_chat=True, otherwise="cmd+shift+]").describe()
    assert "new chat" in described
    assert "cmd+shift+]" in described


@pytest.mark.parametrize(
    "params, message",
    [({"new_chat": "yes"}, "new_chat"), ({"otherwise": "not@a key!"}, "key")],
)
def test_bad_parameters_are_caught_when_the_config_loads(params, message):
    binding = {"action": "focus_session"}
    binding.update(params)
    with pytest.raises(Exception, match=message):
        parse({"version": 1, "bindings": {"AG00": binding}})


def test_opening_a_link_never_uses_a_shell(monkeypatch):
    calls = []

    class FakePopen:
        def __init__(self, argv, **kwargs):
            calls.append((argv, kwargs))

    monkeypatch.setattr("subprocess.Popen", FakePopen)
    REAL_SPAWN_OPEN(ds.chat_url(LOCAL_ID))
    argv, kwargs = calls[0]
    assert argv == ["open", ds.chat_url(LOCAL_ID)]
    assert not kwargs.get("shell")


# -- what ships -----------------------------------------------------------------


def test_the_claude_profile_gives_each_agent_key_its_own_chat():
    overrides = load_default().resolve_profile("Claude")
    for index in range(6):
        action = overrides[f"AG0{index}"]
        assert action.kind == "focus_session"
        assert action.params["slot"] == index
        assert action.params["new_chat"] is True
        assert action.params["otherwise"] == "cmd+shift+]"


def test_stop_failure_is_registered_so_a_failed_turn_goes_red():
    assert "StopFailure" in hooks_install.HOOK_EVENTS
