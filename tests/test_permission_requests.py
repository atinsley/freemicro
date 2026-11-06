"""Answering a permission request from the pad through a PermissionRequest hook."""

from __future__ import annotations

import io
import json
import threading
from argparse import Namespace

import pytest

from freemicro import alerts, cli, daemon
from freemicro import permission_requests as pr
from freemicro.input.actions import Action, Backend, release
from freemicro.input.actions import perform as execute
from freemicro.padconfig import load_default, parse
from freemicro.state.engine import AgentState, default_store
from freemicro.state.hooks import classify


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RecordingBackend(Backend):
    description = "recording"

    def __init__(self):
        self.keys = []
        self.scripts = []

    def press_key(self, combo):
        self.keys.append(combo)

    def run_applescript(self, script):
        self.scripts.append(script)


@pytest.fixture(autouse=True)
def clean_latch(monkeypatch):
    pr.forget_recent_answer()
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    monkeypatch.setattr(alerts, "spawn", lambda argv: None)
    yield
    pr.forget_recent_answer()


def _event(session="s1", tool="Bash", suggestions=None, cwd="/work/api"):
    event = {
        "hook_event_name": "PermissionRequest",
        "session_id": session,
        "cwd": cwd,
        "tool_name": tool,
        "tool_input": {"command": "secret-value --token abc"},
        "prompt_id": "p1",
    }
    if suggestions is not None:
        event["permission_suggestions"] = suggestions
    return event


def _request(clock, **kwargs):
    return pr.create(_event(**kwargs), now=clock())


# -- the request on disk ---------------------------------------------------


def test_a_request_is_private_and_never_stores_the_tool_input():
    clock = Clock()
    request = _request(clock)
    path = pr.directory() / f"{request.id}.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert pr.directory().stat().st_mode & 0o777 == 0o700
    assert "secret-value" not in path.read_text(encoding="utf-8")


def test_only_the_newest_request_is_answered():
    clock = Clock()
    older = _request(clock, session="old")
    clock.advance(5)
    newer = _request(clock, session="new")
    clock.advance(1)
    outcome = pr.answer_newest("approve", now=clock(), monotonic=clock)
    assert outcome.status == pr.ANSWERED
    assert outcome.request.id == newer.id
    assert pr.read_answer(newer) == "approve"
    assert pr.read_answer(older) == ""


def test_project_restricts_which_request_is_answered():
    clock = Clock()
    api = _request(clock, session="api", cwd="/work/api")
    clock.advance(5)
    _request(clock, session="web", cwd="/work/web")
    clock.advance(1)
    outcome = pr.answer_newest(
        "reject", project="/work/api", now=clock(), monotonic=clock
    )
    assert outcome.request.id == api.id
    assert pr.read_answer(api) == "reject"


def test_nothing_waiting_answers_nothing():
    clock = Clock()
    assert pr.answer_newest("approve", now=clock(), monotonic=clock).status == pr.NONE


def test_a_prompt_that_just_appeared_cannot_be_answered_by_a_press_in_flight():
    clock = Clock()
    older = _request(clock, session="old")
    clock.advance(5)
    newer = _request(clock, session="new")
    clock.advance(pr.MIN_AGE_SECONDS / 2)
    outcome = pr.answer_newest("approve", now=clock(), monotonic=clock)
    assert outcome.status == pr.HELD
    assert pr.read_answer(newer) == ""
    assert pr.read_answer(older) == ""


def test_a_quick_second_press_does_not_answer_the_next_request():
    clock = Clock()
    older = _request(clock, session="old")
    clock.advance(5)
    _request(clock, session="new")
    clock.advance(1)
    assert pr.answer_newest("approve", now=clock(), monotonic=clock).status == pr.ANSWERED
    clock.advance(pr.COOLDOWN_SECONDS / 2)
    assert pr.answer_newest("approve", now=clock(), monotonic=clock).status == pr.HELD
    assert pr.read_answer(older) == ""


def test_a_request_whose_hook_died_is_ignored_and_removed():
    clock = Clock()
    request = pr.create(_event(), now=clock(), pid=999_999_999)
    clock.advance(1)
    assert pr.answer_newest("approve", now=clock(), monotonic=clock).status == pr.NONE
    assert not (pr.directory() / f"{request.id}.json").exists()


def test_a_request_past_the_wait_is_not_answered():
    clock = Clock()
    _request(clock)
    clock.advance(pr.WAIT_SECONDS + 1)
    assert pr.answer_newest("approve", now=clock(), monotonic=clock).status == pr.NONE


def test_retiring_a_session_only_removes_that_session():
    clock = Clock()
    mine = _request(clock, session="mine")
    other = _request(clock, session="other")
    pr.retire_session("mine")
    assert not (pr.directory() / f"{mine.id}.json").exists()
    assert (pr.directory() / f"{other.id}.json").exists()


def test_an_invalid_answer_is_refused():
    clock = Clock()
    request = _request(clock)
    clock.advance(1)
    with pytest.raises(ValueError):
        pr.answer_newest("yes-please", now=clock(), monotonic=clock)
    assert pr.read_answer(request) == ""


# -- the decision the hook prints ------------------------------------------


def _decision(output):
    assert output["hookSpecificOutput"]["hookEventName"] == "PermissionRequest"
    return output["hookSpecificOutput"]["decision"]


def test_approve_allows_with_no_lasting_permission():
    decision = _decision(pr.decision_output("approve", _event()))
    assert decision == {"behavior": "allow"}


def test_reject_denies_with_a_message():
    decision = _decision(pr.decision_output("reject", _event()))
    assert decision["behavior"] == "deny"
    assert decision["message"]
    assert "interrupt" not in decision


def test_always_only_applies_suggested_rules_never_a_mode_change():
    rules = {
        "type": "addRules", "behavior": "allow", "destination": "session",
        "rules": [{"toolName": "Bash", "ruleContent": "npm test"}],
    }
    mode = {"type": "setMode", "mode": "bypassPermissions", "destination": "session"}
    decision = _decision(pr.decision_output("always", _event(suggestions=[rules, mode])))
    assert decision == {"behavior": "allow", "updatedPermissions": [rules]}


def test_always_with_nothing_suggested_is_a_single_approval():
    decision = _decision(pr.decision_output("always", _event(suggestions=[])))
    assert decision == {"behavior": "allow"}


# -- the wait ----------------------------------------------------------------


def test_the_wait_returns_the_answer():
    clock = Clock()
    request = _request(clock)
    pr.write_answer(request, "reject")
    assert pr.wait_for_answer(request, clock=clock, sleep=clock.advance) == "reject"


def test_the_wait_ends_without_a_decision_when_the_prompt_was_answered_on_screen():
    clock = Clock()
    request = _request(clock)
    pr.retire_session(request.session_id)
    assert pr.wait_for_answer(request, clock=clock, sleep=clock.advance) == ""


def test_the_wait_gives_up_without_a_decision():
    clock = Clock()
    request = _request(clock)
    assert pr.wait_for_answer(request, clock=clock, sleep=clock.advance) == ""
    assert clock() >= request.created_at + pr.WAIT_SECONDS


# -- running as the hook -------------------------------------------------------


def _pad_owner(monkeypatch, role="run"):
    monkeypatch.setattr(daemon, "lock_holder", lambda: {"pid": 1, "role": role})


def test_no_pad_process_means_no_wait(monkeypatch):
    monkeypatch.setattr(daemon, "lock_holder", lambda: None)
    assert pr.handle(_event()) is None
    assert pr.requests() == []


def test_the_web_ui_holding_the_pad_means_no_wait(monkeypatch):
    monkeypatch.setattr(
        daemon, "lock_holder", lambda: {"pid": 1, "owner": "freemicro web UI"}
    )
    assert pr.handle(_event()) is None


@pytest.mark.parametrize("role", ["run", "daemon"])
def test_a_pad_process_gets_a_chance_to_answer(monkeypatch, role):
    _pad_owner(monkeypatch, role)
    monkeypatch.setattr(pr, "wait_for_answer", lambda request, **_: "approve")
    assert _decision(pr.handle(_event())) == {"behavior": "allow"}
    assert pr.requests() == []


def test_a_non_interactive_session_never_waits(monkeypatch):
    _pad_owner(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-cli")
    assert pr.handle(_event()) is None
    assert pr.requests() == []


def test_the_hook_cleans_up_after_no_answer(monkeypatch):
    _pad_owner(monkeypatch)
    monkeypatch.setattr(pr, "wait_for_answer", lambda request, **_: "")
    assert pr.handle(_event()) is None
    assert pr.requests() == []


def _run_hook(monkeypatch, capsys, event):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    code = cli.cmd_hook(Namespace())
    return code, capsys.readouterr().out


def test_cmd_hook_prints_only_the_decision_and_shows_waiting(monkeypatch, capsys):
    _pad_owner(monkeypatch)
    monkeypatch.setattr(pr, "wait_for_answer", lambda request, **_: "reject")
    code, out = _run_hook(monkeypatch, capsys, _event())
    assert code == 0
    assert _decision(json.loads(out))["behavior"] == "deny"
    session = next(s for s in default_store().sessions() if s.session_id == "s1")
    assert session.state == AgentState.WAITING


def test_cmd_hook_prints_nothing_when_there_is_no_decision(monkeypatch, capsys):
    monkeypatch.setattr(daemon, "lock_holder", lambda: None)
    code, out = _run_hook(monkeypatch, capsys, _event())
    assert (code, out) == (0, "")


def test_cmd_hook_failing_in_the_wait_leaves_the_prompt_to_the_user(monkeypatch, capsys):
    def boom(event):
        raise RuntimeError("boom")

    monkeypatch.setattr(pr, "handle", boom)
    code, out = _run_hook(monkeypatch, capsys, _event())
    assert (code, out) == (0, "")


@pytest.mark.parametrize(
    "name", ["PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SessionEnd"]
)
def test_the_next_event_in_a_session_retires_its_request(monkeypatch, capsys, name):
    clock = Clock()
    request = _request(clock)
    _run_hook(monkeypatch, capsys, {"hook_event_name": name, "session_id": "s1"})
    assert not (pr.directory() / f"{request.id}.json").exists()


def test_a_notification_while_the_prompt_is_up_does_not_retire_it(monkeypatch, capsys):
    clock = Clock()
    request = _request(clock)
    _run_hook(monkeypatch, capsys, {
        "hook_event_name": "Notification", "session_id": "s1",
        "notification_type": "permission_prompt",
    })
    assert (pr.directory() / f"{request.id}.json").exists()


def test_another_sessions_event_does_not_retire_it(monkeypatch, capsys):
    clock = Clock()
    request = _request(clock)
    _run_hook(monkeypatch, capsys, {"hook_event_name": "Stop", "session_id": "other"})
    assert (pr.directory() / f"{request.id}.json").exists()


def test_permission_request_classifies_as_waiting():
    assert classify(_event()) == AgentState.WAITING


# -- the pad key ---------------------------------------------------------------


def _waiting_request():
    request = pr.create(_event(), now=_now() - 1)
    return request


def _now():
    import time

    return time.time()


def _answer_action(**params):
    return Action("answer_permission", params)


def test_the_key_answers_the_request_without_any_keystroke():
    request = _waiting_request()
    backend = RecordingBackend()
    execute(_answer_action(answer="approve"), backend)
    assert pr.read_answer(request) == "approve"
    assert backend.keys == []
    assert backend.scripts == []


def test_a_hold_answers_always_and_a_tap_answers_approve(monkeypatch):
    from freemicro.input import actions

    times = iter([100.0, 100.8])
    monkeypatch.setattr(actions, "_clock", lambda: next(times))
    request = _waiting_request()
    act = _answer_action(answer="approve", long_press="always")
    backend = RecordingBackend()
    execute(act, backend)
    assert pr.read_answer(request) == ""
    release(act, backend)
    assert pr.read_answer(request) == "always"


def test_the_key_says_what_it_answered(monkeypatch):
    spawned = []
    monkeypatch.setattr(alerts, "spawn", spawned.append)
    _waiting_request()
    execute(_answer_action(answer="reject"), RecordingBackend())
    assert len(spawned) == 1
    assert "Declined Bash" in spawned[0][-1]
    assert "api" in spawned[0][-1]
    assert "secret-value" not in spawned[0][-1]


def test_with_nothing_waiting_it_does_nothing():
    backend = RecordingBackend()
    execute(_answer_action(answer="approve"), backend)
    assert backend.keys == []
    assert backend.scripts == []


def test_with_nothing_waiting_otherwise_sends_its_key():
    backend = RecordingBackend()
    execute(_answer_action(answer="reject", otherwise="escape"), backend)
    assert backend.keys == ["escape"]


def test_otherwise_is_not_sent_when_a_request_was_answered():
    request = _waiting_request()
    backend = RecordingBackend()
    execute(_answer_action(answer="reject", otherwise="escape"), backend)
    assert pr.read_answer(request) == "reject"
    assert backend.keys == []


def test_otherwise_is_not_sent_during_the_cooldown():
    _waiting_request()
    backend = RecordingBackend()
    act = _answer_action(answer="reject", otherwise="escape")
    execute(act, backend)
    execute(act, backend)
    assert backend.keys == []


def test_a_misspelled_otherwise_is_caught_when_the_config_loads():
    with pytest.raises(Exception, match="(?i)otherwise|key"):
        parse({"version": 1, "bindings": {"ACT08": {
            "action": "answer_permission", "answer": "reject", "otherwise": "esc@pe!",
        }}})


def test_list_says_what_the_key_would_answer_now():
    _waiting_request()
    described = _answer_action(answer="approve").describe()
    assert "Bash" in described
    assert "api" in described


def test_hook_and_key_work_together_end_to_end(monkeypatch):
    _pad_owner(monkeypatch)
    results = []
    worker = threading.Thread(target=lambda: results.append(pr.handle(_event())))
    worker.start()
    deadline = _now() + 5
    while not pr.requests() and _now() < deadline:
        threading.Event().wait(0.01)
    threading.Event().wait(pr.MIN_AGE_SECONDS + 0.05)
    execute(_answer_action(answer="approve"), RecordingBackend())
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert _decision(results[0]) == {"behavior": "allow"}
    assert pr.requests() == []


# -- what ships ---------------------------------------------------------------


def test_the_claude_profile_uses_the_real_answers():
    overrides = load_default().resolve_profile("Claude")
    assert overrides["ACT07"].kind == "answer_permission"
    assert overrides["ACT07"].params["answer"] == "approve"
    assert overrides["ACT07"].params["long_press"] == "always"
    assert overrides["ACT07"].params["otherwise"] == "cmd+return"
    assert overrides["ACT08"].kind == "answer_permission"
    assert overrides["ACT08"].params["answer"] == "reject"
    assert overrides["ACT08"].params["otherwise"] == "escape"
