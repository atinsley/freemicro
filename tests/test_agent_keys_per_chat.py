"""``agent_keys.group: "chat"``: one Agent Key per chat, like Codex's six threads."""

from __future__ import annotations

import pytest

from freemicro import focus
from freemicro.agentkeys import (
    AgentKeysConfig,
    AgentKeysError,
    SlotResolver,
    parse_agent_keys,
    resolve_slots,
)
from freemicro.padconfig import load_default
from freemicro.state.engine import AgentState, SessionState

NOW = 10_000.0


def _chat(session_id, age=0.0, state=AgentState.IDLE, cwd="/work/api"):
    return SessionState(
        session_id=session_id, state=state, updated_at=NOW - age, cwd=cwd
    )


def _config(**values):
    return parse_agent_keys({"group": "chat", **values})


def _ids(slots):
    return [slot.session.session_id if slot.session else None for slot in slots]


def _chats(count):
    return [_chat(f"c{i}", age=i * 10) for i in range(count)]


# -- the setting ---------------------------------------------------------------


def test_projects_are_still_the_default():
    assert parse_agent_keys({}).group == "project"
    assert AgentKeysConfig().group == "project"


def test_an_unknown_group_is_refused():
    with pytest.raises(AgentKeysError, match="group"):
        parse_agent_keys({"group": "folders"})


@pytest.mark.parametrize("policy", ["pinned", "manual"])
def test_chat_keys_cant_combine_with_pinned_projects(policy):
    with pytest.raises(AgentKeysError, match="group"):
        parse_agent_keys({"group": "chat", "policy": policy})


def test_the_setting_survives_a_round_trip():
    assert parse_agent_keys(_config().to_dict()).group == "chat"


# -- assignment -----------------------------------------------------------------


def test_chats_in_one_project_each_get_a_key():
    sessions = [_chat("a"), _chat("b", age=5), _chat("c", age=9)]
    slots = resolve_slots(_config(), sessions, now=NOW)
    assert _ids(slots) == ["a", "b", "c", None, None, None]


def test_project_mode_still_shares_one_key():
    sessions = [_chat("a"), _chat("b", age=5)]
    slots = resolve_slots(parse_agent_keys({}), sessions, now=NOW)
    assert _ids(slots) == ["a", None, None, None, None, None]


def test_only_the_six_most_recent_chats_get_keys():
    slots = resolve_slots(_config(), _chats(8), now=NOW)
    assert _ids(slots) == ["c0", "c1", "c2", "c3", "c4", "c5"]


def test_a_chat_keeps_its_key_as_the_order_changes():
    resolver = SlotResolver(config=_config())
    resolver.resolve([_chat("a"), _chat("b", age=5)], now=NOW)
    slots = resolver.resolve([_chat("b"), _chat("a", age=5)], now=NOW)
    assert _ids(slots)[:2] == ["a", "b"]


def test_a_newly_active_chat_takes_the_key_of_the_one_it_pushes_out():
    resolver = SlotResolver(config=_config())
    resolver.resolve(_chats(6), now=NOW)
    sessions = [_chat("new"), *[_chat(f"c{i}", age=(i + 1) * 10) for i in range(6)]]
    slots = resolver.resolve(sessions, now=NOW)
    assert _ids(slots) == ["c0", "c1", "c2", "c3", "c4", "new"]


def test_a_chat_waiting_on_you_always_gets_a_key():
    waiting = _chat("asking", age=500, state=AgentState.WAITING)
    slots = resolve_slots(_config(), [*_chats(6), waiting], now=NOW)
    assert "asking" in _ids(slots)
    assert "c5" not in _ids(slots)


def test_a_chat_with_an_error_always_gets_a_key():
    failed = _chat("failed", age=500, state=AgentState.ERROR)
    slots = resolve_slots(_config(), [*_chats(6), failed], now=NOW)
    assert "failed" in _ids(slots)


def test_a_chat_that_closes_frees_its_key_for_the_next():
    resolver = SlotResolver(config=_config())
    resolver.resolve([_chat("a"), _chat("b", age=5)], now=NOW)
    slots = resolver.resolve([_chat("b", age=5), _chat("c", age=9)], now=NOW)
    assert _ids(slots)[:2] == ["c", "b"]


def test_each_keys_state_is_its_own_chats():
    sessions = [_chat("a", state=AgentState.WORKING), _chat("b", age=5)]
    slots = resolve_slots(_config(), sessions, now=NOW)
    assert slots[0].state == AgentState.WORKING
    assert slots[1].state == AgentState.IDLE


def test_owned_keys_still_limit_the_chats():
    slots = resolve_slots(_config(keys=[3, 4, 5]), _chats(8), now=NOW)
    assert _ids(slots) == [None, None, None, "c0", "c1", "c2"]


def test_chats_in_different_projects_are_told_apart():
    sessions = [
        _chat("aaaaaaaa-1", cwd="/work/api"),
        _chat("bbbbbbbb-2", age=1, cwd="/work/api"),
        _chat("cccccccc-3", age=2, cwd="/work/web"),
    ]
    labels = [s.label for s in resolve_slots(_config(), sessions, now=NOW)[:3]]
    assert len(set(labels)) == 3
    assert all(("api" in label or "web" in label) for label in labels)


def test_a_chat_keeps_its_real_project_path():
    slots = resolve_slots(_config(), [_chat("a")], now=NOW)
    assert slots[0].project.path == "/work/api"


# -- a press --------------------------------------------------------------------


def test_each_key_plans_its_own_chat():
    sessions = [_chat("a"), _chat("b", age=5)]
    slots = resolve_slots(_config(), sessions, now=NOW)
    assert focus.plan_for_slot(0, slots=slots).session.session_id == "a"
    assert focus.plan_for_slot(1, slots=slots).session.session_id == "b"


def test_a_key_tied_to_a_project_still_finds_a_chat_there():
    slots = resolve_slots(_config(), [_chat("a")], now=NOW)
    plan = focus.plan_for_slot(0, project="/work/api", slots=slots)
    assert plan.session.session_id == "a"


def test_the_shipped_keymap_gives_each_chat_a_key():
    assert load_default().agent_keys.group == "chat"
