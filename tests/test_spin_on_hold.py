"""``lighting.spin_on_hold``: a rainbow underglow while push-to-talk is held."""

from __future__ import annotations

import json

import pytest

from freemicro import cli
from freemicro.device.lighting import ZONE_UNDERGLOW, parse_effect
from freemicro.padconfig import PadConfigError, load_default, parse

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
    assert tuple(action.light.zones) == (ZONE_UNDERGLOW,)


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


def test_spin_claims_the_underglow_so_the_renderer_paints_it():
    assert ZONE_UNDERGLOW in parse(_doc(spin=True)).activity_zones


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
