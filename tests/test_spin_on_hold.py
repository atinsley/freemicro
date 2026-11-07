"""``lighting.spin_on_hold``: the whole pad is a rainbow while push-to-talk is held."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace

import pytest

from freemicro import cli
from freemicro.agentkeys import AgentSlot, Project
from freemicro.device import codex_micro
from freemicro.device.lighting import (
    METHOD_RGBCFG,
    METHOD_THREAD_STATUS,
    ZONE_AGENT_KEYS,
    ZONE_BACKLIGHT,
    ZONE_UNDERGLOW,
    parse_color,
    parse_effect,
    thread_entry,
)
from freemicro.lighting_owner import ActivityOverlay, LightingWorker, OnDemandPainter
from freemicro.padconfig import PadConfigError, load_default, parse, spin_light
from freemicro.renderers.micro_leds import MicroLedsRenderer
from freemicro.state.engine import AgentState

SPIN_ZONES = {ZONE_UNDERGLOW, ZONE_BACKLIGHT, ZONE_AGENT_KEYS}
RAINBOW = parse_effect("rainbow")
OWN_LIGHT = {"color": "#2E8B57", "effect": "snake", "speed": 0.4}


def _doc(spin=None):
    lighting = {"enabled": True}
    if spin is not None:
        lighting["spin_on_hold"] = spin
    return {
        "version": 1,
        "bindings": {
            "ACT10": {"action": "hold", "key": "ctrl+cmd+o", "light": dict(OWN_LIGHT)},
            "ACT09": {"action": "hold", "key": "ctrl+cmd+u"},
            "ACT12": {"action": "key", "key": "return"},
            "ACT08": {"action": "key", "key": "escape", "light": dict(OWN_LIGHT)},
        },
        "chords": {},
        "profiles": {"Claude": {"ACT06": {"action": "hold", "key": "ctrl+cmd+p"}}},
        "layers": {"fn": {"ACT07": {"action": "hold", "key": "ctrl+cmd+l"}}},
        "lighting": lighting,
    }


def _assert_spins(action):
    assert action.light is not None
    assert action.light.effect == parse_effect("rainbow")
    assert action.light.speed > 0
    assert set(action.light.zones) == SPIN_ZONES


def test_off_by_default_and_the_binding_keeps_its_own_light():
    pad = parse(_doc())
    assert pad.lighting.spin_on_hold is False
    assert pad.bindings["ACT10"].light.effect == parse_effect("snake")
    assert pad.bindings["ACT09"].light is None


def test_on_makes_every_hold_key_spin():
    pad = parse(_doc(spin=True))
    assert pad.lighting.spin_on_hold is True
    _assert_spins(pad.bindings["ACT10"])
    _assert_spins(pad.bindings["ACT09"])
    _assert_spins(pad.resolve_profile("Claude")["ACT06"])
    _assert_spins(pad.layers["fn"]["ACT07"])


def test_on_leaves_keys_that_are_not_holds_alone():
    pad = parse(_doc(spin=True))
    assert pad.bindings["ACT12"].light is None
    assert pad.bindings["ACT08"].light.effect == parse_effect("snake")


def test_a_chorded_hold_spins_too():
    document = _doc(spin=True)
    document["bindings"]["ACT06+ACT07"] = {"action": "hold", "key": "ctrl+cmd+j"}
    pad = parse(document)
    _assert_spins(next(iter(pad.chords.values())))


def test_spin_claims_every_zone_so_the_renderer_paints_them():
    assert SPIN_ZONES <= set(parse(_doc(spin=True)).activity_zones)


# -- what the pad is sent ----------------------------------------------------------


def _renderer():
    document = _doc(spin=True)
    document["lighting"].update(zones=["agent_keys"], on_exit="leave", auto_dim_seconds=0)
    return MicroLedsRenderer(device=None, config=parse(document), store=None)


def _slots():
    return [
        AgentSlot(index=0, path="/a", project=Project("/a", AgentState.WORKING, 0.0)),
        AgentSlot(index=1, path="/b", project=Project("/b", AgentState.ERROR, 0.0)),
        *(AgentSlot(index=index) for index in range(2, 6)),
    ]


def _frame(renderer, overlay=None):
    messages = renderer.messages_for(AgentState.WORKING, slots=_slots(), overlay=overlay)
    by_method = {message["m"]: message["p"] for message in messages}
    return by_method[METHOD_RGBCFG], by_method[METHOD_THREAD_STATUS]


def test_holding_carries_every_zone_in_the_agent_key_message():
    zones, agent_keys = _frame(_renderer(), overlay=spin_light())
    assert [entry["e"] for entry in agent_keys] == [RAINBOW] * 6
    assert agent_keys[0]["sk"] == 1
    assert agent_keys[0]["sa"] == 1
    assert all("sk" not in entry and "sa" not in entry for entry in agent_keys[1:])
    # What the other message would say is still the state underneath.
    assert zones["keys"]["e"] != RAINBOW
    assert zones["ambient"]["e"] != RAINBOW


def test_a_light_from_a_config_file_is_not_sent_together():
    light = replace(spin_light(), together=False)
    zones, agent_keys = _frame(_renderer(), overlay=light)
    assert zones["keys"]["e"] == RAINBOW
    assert zones["ambient"]["e"] == RAINBOW
    assert agent_keys[0]["sk"] == 0


class _Pad:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message["m"])


def _live(monkeypatch):
    pad = _Pad()
    document = _doc(spin=True)
    document["lighting"].update(zones=["agent_keys"], on_exit="leave", auto_dim_seconds=0)
    renderer = MicroLedsRenderer(device=pad, config=parse(document), store=None)
    monkeypatch.setattr(renderer, "slots", lambda sessions=None: _slots())
    monkeypatch.setattr("freemicro.renderers.micro_leds._register_driving", lambda r: None)
    return pad, renderer


def test_pressing_sends_only_the_one_message(monkeypatch):
    pad, renderer = _live(monkeypatch)
    renderer.render(AgentState.WORKING, [])
    pad.sent.clear()
    renderer.set_overlay(spin_light())
    renderer.render(AgentState.WORKING, [])
    assert pad.sent == [METHOD_THREAD_STATUS]


def test_letting_go_clears_the_flags_then_repaints_the_rest(monkeypatch):
    pad, renderer = _live(monkeypatch)
    renderer.render(AgentState.WORKING, [])
    renderer.set_overlay(spin_light())
    renderer.render(AgentState.WORKING, [])
    pad.sent.clear()
    renderer.set_overlay(None)
    renderer.render(AgentState.WORKING, [])
    assert pad.sent == [METHOD_THREAD_STATUS, METHOD_RGBCFG]
    renderer.set_overlay(None)
    pad.sent.clear()
    renderer.render(AgentState.WORKING, [])
    assert pad.sent == []


def test_every_frame_in_spin_mode_leaves_the_flags_off():
    renderer = _renderer()
    _, agent_keys = _frame(renderer)
    assert agent_keys[0]["sk"] == 0
    assert agent_keys[0]["sa"] == 0
    blank = next(m for m in renderer._blank_messages() if m["m"] == METHOD_THREAD_STATUS)
    assert blank["p"][0]["sk"] == 0
    assert blank["p"][0]["sa"] == 0


def test_flags_stay_out_of_the_message_without_spin_mode():
    document = _doc()
    document["lighting"].update(zones=["agent_keys"], on_exit="leave", auto_dim_seconds=0)
    renderer = MicroLedsRenderer(device=None, config=parse(document), store=None)
    assert "sk" not in _frame(renderer)[1][0]


# -- painting on demand ------------------------------------------------------------


def _painter(read_state=lambda: (AgentState.IDLE, [])):
    overlay = ActivityOverlay()
    asked = []
    painter = OnDemandPainter(
        overlay, read_state, lambda state, sessions, light: asked.append(light)
    )
    return overlay, asked, painter


def test_a_press_and_a_release_ask_for_a_paint_at_once():
    overlay, asked, painter = _painter()
    light = spin_light()
    overlay.note("ACT10", light)
    painter.paint_if_changed()
    assert asked == [light]
    overlay.note("ACT10", None)
    painter.paint_if_changed()
    assert asked == [light, None]


def test_other_key_events_ask_for_nothing():
    overlay, asked, painter = _painter()
    painter.paint_if_changed()
    overlay.note("ACT10", spin_light())
    painter.paint_if_changed()
    painter.paint_if_changed()
    assert len(asked) == 1


def test_a_light_the_tick_already_asked_for_isnt_asked_for_again():
    overlay, asked, painter = _painter()
    light = spin_light()
    overlay.note("ACT10", light)
    painter.shown_by_tick(light)
    painter.paint_if_changed()
    assert asked == []


def test_a_failed_state_read_never_reaches_the_keys():
    def boom():
        raise OSError("no state")

    overlay, asked, painter = _painter(read_state=boom)
    overlay.note("ACT10", spin_light())
    painter.paint_if_changed()
    assert asked == []


# -- writing on its own thread -----------------------------------------------------


class _SlowRenderer:
    def __init__(self):
        self.painted = []
        self.overlay = None
        self.started = threading.Event()
        self.carry_on = threading.Event()
        self.carry_on.set()

    def set_overlay(self, light):
        self.overlay = light

    def render(self, state, sessions):
        self.started.set()
        assert self.carry_on.wait(5)
        self.painted.append(self.overlay)


def test_asking_for_a_frame_never_waits_for_a_slow_write():
    renderer = _SlowRenderer()
    renderer.carry_on.clear()
    worker = LightingWorker(lambda: [renderer])
    worker.paint(AgentState.IDLE, [], "first")
    assert renderer.started.wait(5)
    worker.paint(AgentState.IDLE, [], "second")  # Would hang here if it waited.
    assert renderer.painted == []
    renderer.carry_on.set()
    worker.stop()
    assert renderer.painted == ["first", "second"]


def test_frames_asked_for_during_a_slow_write_collapse_to_the_newest():
    renderer = _SlowRenderer()
    renderer.carry_on.clear()
    worker = LightingWorker(lambda: [renderer])
    worker.paint(AgentState.IDLE, [], "press")
    assert renderer.started.wait(5)
    worker.paint(AgentState.IDLE, [], "release")
    worker.paint(AgentState.IDLE, [], "press again")
    worker.paint(AgentState.IDLE, [], "release again")
    renderer.carry_on.set()
    worker.stop()
    assert renderer.painted == ["press", "release again"]


def test_stopping_finishes_the_last_frame_and_can_be_repeated():
    renderer = _SlowRenderer()
    worker = LightingWorker(lambda: [renderer])
    worker.paint(AgentState.IDLE, [], "last")
    worker.stop()
    worker.stop()
    assert renderer.painted == ["last"]


def test_a_failed_paint_leaves_the_worker_running():
    renderer = _SlowRenderer()
    calls = []

    def renderers():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("boom")
        return [renderer]

    worker = LightingWorker(renderers)
    worker.paint(AgentState.IDLE, [], "lost")
    while not calls:
        time.sleep(0.001)
    worker.paint(AgentState.IDLE, [], "kept")
    worker.stop()
    assert renderer.painted == ["kept"]


# -- closing while a write is under way --------------------------------------------


class _FakeIOKit:
    def __init__(self):
        self.closed = threading.Event()
        self.entered = threading.Event()
        self.carry_on = threading.Event()

    def IOHIDDeviceSetReport(self, *args):
        assert not self.closed.is_set(), "wrote to a closed handle"
        self.entered.set()
        assert self.carry_on.wait(5)
        assert not self.closed.is_set(), "handle was freed during the write"
        return 0

    def IOHIDDeviceClose(self, *args):
        self.closed.set()


@pytest.fixture
def fake_iokit(monkeypatch):
    iokit = _FakeIOKit()
    monkeypatch.setattr(codex_micro, "_iokit", iokit)
    monkeypatch.setattr(codex_micro, "_cf", None)
    return iokit


def test_close_waits_for_the_write_under_way(fake_iokit):
    device = codex_micro.Device(object())
    errors = []

    def write():
        try:
            device.send_report(b"x" * 63)
        except BaseException as exc:  # noqa: BLE001 - reported by the assert below
            errors.append(exc)

    writer = threading.Thread(target=write)
    writer.start()
    assert fake_iokit.entered.wait(5)
    closer = threading.Thread(target=device.close)
    closer.start()
    closer.join(0.1)
    assert closer.is_alive()
    assert not fake_iokit.closed.is_set()

    fake_iokit.carry_on.set()
    writer.join(5)
    closer.join(5)
    assert errors == []
    assert fake_iokit.closed.is_set()


def test_a_message_stops_at_its_next_report_once_closing(fake_iokit):
    fake_iokit.carry_on.set()
    device = codex_micro.Device(object())
    device.close()
    with pytest.raises(codex_micro.DeviceError):
        device.send({"m": "x"})


def test_a_write_that_never_returns_cant_hang_close(fake_iokit, monkeypatch):
    monkeypatch.setattr(codex_micro, "CLOSE_WAIT_SECONDS", 0.01)
    device = codex_micro.Device(object())
    writer = threading.Thread(target=device.send_report, args=(b"x" * 63,), daemon=True)
    writer.start()
    assert fake_iokit.entered.wait(5)
    device.close()
    # The handle stays alive for the stuck write, rather than being freed under it.
    assert not fake_iokit.closed.is_set()
    fake_iokit.carry_on.set()
    writer.join(5)


# -- message size ------------------------------------------------------------------


def _reports(message, transport=codex_micro.TRANSPORT_BLE):
    payload = json.dumps(message, separators=(",", ":"))
    return len(codex_micro.frame_message(payload, transport))


def test_the_rainbow_fits_in_four_bluetooth_reports():
    _, agent_keys = _frame(_renderer(), overlay=spin_light())
    assert _reports({"m": METHOD_THREAD_STATUS, "p": agent_keys}) == 4


def test_whole_numbers_are_sent_in_their_shortest_form():
    assert json.dumps(thread_entry(0, 0, "rainbow", 1.0, 1.0), separators=(",", ":")) == (
        '{"id":0,"c":0,"b":1,"e":3,"s":1}'
    )
    assert json.dumps(thread_entry(0, 0, "solid", 0.5, 0.4), separators=(",", ":")) == (
        '{"id":0,"c":0,"b":0.5,"e":1,"s":0.4}'
    )


def test_an_unchanged_frame_sends_nothing(monkeypatch):
    pad, renderer = _live(monkeypatch)
    renderer.render(AgentState.WORKING, [])
    pad.sent.clear()
    renderer.render(AgentState.WORKING, [])
    assert pad.sent == []


def test_a_failed_write_sends_the_whole_frame_again(monkeypatch):
    pad, renderer = _live(monkeypatch)
    renderer.render(AgentState.WORKING, [])

    def boom(message):
        raise OSError("gone")

    original, pad.send = pad.send, boom
    renderer.set_overlay(spin_light())
    renderer.render(AgentState.WORKING, [])
    pad.send = original
    renderer._retry_at = 0.0
    pad.sent.clear()
    renderer.render(AgentState.WORKING, [])
    assert pad.sent == [METHOD_RGBCFG, METHOD_THREAD_STATUS]


def test_letting_go_puts_every_zone_back():
    renderer = _renderer()
    _frame(renderer, overlay=spin_light())
    zones, agent_keys = _frame(renderer)

    # The backlight and the base have no state of their own here: dark, as the
    # factory leaves them.
    assert zones["keys"]["e"] == parse_effect("off") or zones["keys"]["b"] == 0
    assert zones["ambient"]["e"] == parse_effect("off") or zones["ambient"]["b"] == 0

    # Each key goes back to its own project, not to one look across all six.
    working = renderer.light_for(AgentState.WORKING)
    error = renderer.light_for(AgentState.ERROR)
    assert agent_keys[0]["c"] == parse_color(working.color)
    assert agent_keys[0]["e"] == working.effect
    assert agent_keys[1]["c"] == parse_color(error.color)
    assert agent_keys[1]["e"] == error.effect
    assert all(entry["e"] == parse_effect("off") for entry in agent_keys[2:])


def test_letting_go_shows_the_state_as_it_is_then():
    renderer = _renderer()
    _frame(renderer, overlay=spin_light())
    done = [AgentSlot(index=0, path="/a", project=Project("/a", AgentState.DONE, 0.0))]
    messages = renderer.messages_for(AgentState.DONE, slots=done)
    entry = next(m for m in messages if m["m"] == METHOD_THREAD_STATUS)["p"][0]
    assert entry["c"] == parse_color(renderer.light_for(AgentState.DONE).color)


def test_spin_on_hold_must_be_true_or_false():
    with pytest.raises(PadConfigError, match="spin_on_hold"):
        parse(_doc(spin="yes"))


def test_shipped_default_leaves_it_off():
    assert load_default().lighting.spin_on_hold is False


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "keymap.json"
    path.write_text(json.dumps(_doc()), encoding="utf-8")
    return path


def _spin_cli(config_path, value):
    return cli.main(["lights", "--spin", value, "--config", str(config_path)])


def _saved(config_path):
    return json.loads(config_path.read_text(encoding="utf-8"))


def test_cli_turns_it_on_and_off(config_path):
    assert _spin_cli(config_path, "on") == 0
    assert _saved(config_path)["lighting"]["spin_on_hold"] is True
    assert _spin_cli(config_path, "off") == 0
    assert _saved(config_path)["lighting"]["spin_on_hold"] is False


def test_cli_toggle_flips_the_saved_value(config_path):
    assert _spin_cli(config_path, "toggle") == 0
    assert _saved(config_path)["lighting"]["spin_on_hold"] is True
    assert _spin_cli(config_path, "toggle") == 0
    assert _saved(config_path)["lighting"]["spin_on_hold"] is False


def test_cli_preserves_the_rest_of_the_config(config_path):
    before = _saved(config_path)
    assert _spin_cli(config_path, "on") == 0
    after = _saved(config_path)
    assert after["bindings"] == before["bindings"]
    assert after["profiles"] == before["profiles"]
    assert after["lighting"]["enabled"] is True


def test_cli_warns_when_the_leds_are_off(config_path, capsys):
    document = _doc()
    document["lighting"]["enabled"] = False
    config_path.write_text(json.dumps(document), encoding="utf-8")
    assert _spin_cli(config_path, "on") == 0
    assert "freemicro lights --enable" in capsys.readouterr().out


def test_cli_rejects_an_unknown_value(config_path):
    with pytest.raises(SystemExit):
        _spin_cli(config_path, "sideways")
