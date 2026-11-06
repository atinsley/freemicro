"""The shipped "Claude" profile maps the pad onto the Claude desktop app.

Sessions in the desktop app have no terminal tab, so the terminal-oriented base
bindings cannot reach them. These tests pin what each key does while the app is
frontmost, and that nothing leaks into other apps.
"""

from __future__ import annotations

from freemicro.padconfig import load_default

#: input id -> (action kind, the parameter that defines it, its value)
EXPECTED = {
    "AG00": ("focus_session", "new_chat", True),
    "AG01": ("focus_session", "new_chat", True),
    "AG02": ("focus_session", "new_chat", True),
    "AG03": ("focus_session", "new_chat", True),
    "AG04": ("focus_session", "new_chat", True),
    "AG05": ("focus_session", "new_chat", True),
    "ACT06": ("text", "text", "/fast"),
    "ACT07": ("answer_permission", "answer", "approve"),
    "ACT08": ("answer_permission", "answer", "reject"),
    "ACT09": ("text", "text", "/branch"),
    "ENC_CLK": ("key", "key", "return"),
}


def _overrides(app="Claude"):
    return load_default().resolve_profile(app)


def test_every_mapped_key_does_the_documented_thing_in_the_claude_app():
    overrides = _overrides()
    assert set(overrides) == set(EXPECTED)
    for input_id, (kind, param, value) in EXPECTED.items():
        action = overrides[input_id]
        assert action.kind == kind, input_id
        assert action.params[param] == value, input_id


def test_typed_commands_never_submit_themselves():
    # A slash command only runs at the start of a message, so auto-submitting
    # into a composer holding a draft would send the draft with "/fast" on the
    # end of it. The user presses CODEX to run the command.
    overrides = _overrides()
    for input_id in ("ACT06", "ACT09"):
        assert not overrides[input_id].params.get("submit"), input_id


def test_the_profile_stays_out_of_other_apps():
    pad = load_default()
    assert pad.resolve_profile("Terminal") == {}
    assert pad.resolve_profile("Google Chrome") == {}
    assert pad.resolve_profile(None) == {}
    assert pad.bindings["AG00"].kind == "focus_session"
    assert pad.bindings["ACT09"].params["key"] == "cmd+t"


def test_approve_is_never_a_blind_keystroke():
    pad = load_default()
    assert pad.resolve_profile("Claude")["ACT07"].kind == "answer_permission"
    assert pad.bindings["ACT07"].kind == "answer_permission"


def test_the_default_keymap_still_loads_without_warnings():
    assert load_default().warnings == ()
