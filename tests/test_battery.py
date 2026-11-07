"""Reading the pad's battery from macOS, and warning when it runs low."""

from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

import pytest

from freemicro import battery, cli, padconfig
from freemicro.padconfig import PadConfigError

PMSET = (
    "Now drawing from 'AC Power'\n"
    " -InternalBattery-0 (id=38076515)\t100%; charged; 0:00 remaining present: true\n"
    " -Aaron�s AirPods Pro #3 (id=63509693)\t12%; discharging present: true\n"
    " -Codex Micro #1 (id=50664605)\t{line}\n"
)


def _pmset(line="65%; "):
    return PMSET.format(line=line)


# -- reading it -------------------------------------------------------------------


def test_the_pads_level_is_read_not_another_devices():
    assert battery.parse_pmset(_pmset()) == battery.Reading(65, False)


@pytest.mark.parametrize(
    "state, charging",
    [
        ("", False),
        ("discharging; 3:10 remaining present: true", False),
        ("charging; 0:40 remaining present: true", True),
        ("charged; 0:00 remaining present: true", True),
        ("finishing charge; 0:05 remaining present: true", True),
    ],
)
def test_charging_is_understood(state, charging):
    assert battery.parse_pmset(_pmset(f"40%; {state}")).charging is charging


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Now drawing from 'AC Power'\n",
        _pmset("not a number"),
        _pmset("140%; "),
    ],
)
def test_no_usable_reading_is_none(text):
    assert battery.parse_pmset(text) is None


def test_a_failing_pmset_is_no_reading(monkeypatch):
    def boom():
        raise OSError("no pmset")

    monkeypatch.setattr(battery, "_pmset_output", boom)
    assert battery.read_pad() is None


# -- when to warn -----------------------------------------------------------------


@pytest.fixture
def warner():
    sent = []
    return SimpleNamespace(
        sent=sent, low=battery.LowBatteryWarner(runner=sent.append)
    )


def _see(warner, *levels, threshold=20, charging=False):
    for level in levels:
        warner.low.see(battery.Reading(level, charging), threshold)
    return len(warner.sent)


def test_above_the_threshold_says_nothing(warner):
    assert _see(warner, 65, 21) == 0


def test_at_the_threshold_warns(warner):
    assert _see(warner, 21, 20) == 1


def test_it_warns_once_rather_than_at_every_check(warner):
    assert _see(warner, 20, 20, 19, 18, 16) == 1


def test_it_warns_again_each_further_five_points(warner):
    assert _see(warner, 20, 15) == 2
    assert _see(warner, 12, 10, 7, 5) == 4


def test_starting_already_low_warns(warner):
    assert _see(warner, 8) == 1


def test_charging_never_warns(warner):
    assert _see(warner, 8, 6, charging=True) == 0


def test_a_small_wobble_above_the_threshold_doesnt_warn_again(warner):
    assert _see(warner, 20, 21, 22, 20) == 1


def test_a_real_recharge_arms_it_again(warner):
    assert _see(warner, 20, 26, 20) == 2


def test_being_put_on_charge_arms_it_again(warner):
    assert _see(warner, 15) == 1
    assert _see(warner, 15, charging=True) == 1
    assert _see(warner, 15) == 2


def test_no_reading_changes_nothing(warner):
    _see(warner, 20)
    warner.low.see(None, 20)
    assert _see(warner, 20) == 1


def test_the_banner_names_the_pad_and_the_level(warner):
    _see(warner, 15)
    argv = warner.sent[0]
    assert argv[0] == "osascript"
    assert "15%" in argv[2]
    assert "Codex Micro" in argv[2]


# -- how often it looks ------------------------------------------------------------


@pytest.fixture
def watch():
    now = [1000.0]
    reads = []
    seen = []

    def read():
        reads.append(now[0])
        return battery.Reading(50, False)

    low = SimpleNamespace(see=lambda reading, threshold: seen.append((reading, threshold)))
    return SimpleNamespace(
        now=now, reads=reads, seen=seen,
        watch=battery.BatteryWatch(
            read=read, warner=low, clock=lambda: now[0], start=lambda job: job()
        ),
    )


def _config(**fields):
    return padconfig.BatteryConfig(**fields)


def test_nothing_is_read_unless_asked_for(watch):
    watch.watch.poll(_config())
    assert watch.reads == []


def test_it_looks_straight_away_then_on_a_slow_clock(watch):
    config = _config(notify=True)
    watch.watch.poll(config)
    watch.now[0] += battery.POLL_SECONDS - 1
    watch.watch.poll(config)
    assert len(watch.reads) == 1
    watch.now[0] += 1
    watch.watch.poll(config)
    assert len(watch.reads) == 2


def test_the_configured_threshold_is_used(watch):
    watch.watch.poll(_config(notify=True, threshold=30))
    assert watch.seen[0][1] == 30


def test_a_slow_read_is_never_started_twice():
    jobs = []
    now = [0.0]
    watch = battery.BatteryWatch(
        read=lambda: None,
        warner=SimpleNamespace(see=lambda *a: None),
        clock=lambda: now[0],
        start=jobs.append,
    )
    config = _config(notify=True)
    watch.poll(config)
    now[0] += battery.POLL_SECONDS * 2
    watch.poll(config)
    assert len(jobs) == 1
    jobs[0]()
    watch.poll(config)
    assert len(jobs) == 2


def test_a_failing_check_can_be_tried_again():
    jobs = []
    now = [0.0]

    def boom():
        raise RuntimeError("boom")

    watch = battery.BatteryWatch(
        read=boom,
        warner=SimpleNamespace(see=lambda *a: None),
        clock=lambda: now[0],
        start=lambda job: (jobs.append(job), job()),
    )
    config = _config(notify=True)
    watch.poll(config)
    now[0] += battery.POLL_SECONDS
    watch.poll(config)
    assert len(jobs) == 2


def test_the_real_start_runs_the_job():
    import threading

    done = threading.Event()
    battery._start_thread(done.set)
    assert done.wait(2)


# -- the setting ------------------------------------------------------------------


def _parsed(**fields):
    return padconfig.parse({
        "version": 1, "bindings": {}, "lighting": {"battery": fields},
    }).lighting.battery


def test_notifying_is_off_by_default():
    assert _parsed().notify is False
    assert padconfig.load_default().lighting.battery.notify is False


def test_notifying_can_be_turned_on_without_the_light():
    config = _parsed(notify=True, threshold=20)
    assert config.notify is True
    assert config.threshold == 20
    assert config.enabled is False


def test_the_setting_must_be_true_or_false():
    with pytest.raises(PadConfigError, match="notify"):
        _parsed(notify="yes")


# -- status -----------------------------------------------------------------------


def test_status_shows_the_level(monkeypatch, capsys):
    monkeypatch.setattr(battery, "_pmset_output", lambda: _pmset("65%; "))
    cli.cmd_status(Namespace(json=False))
    assert "Pad battery: 65%" in capsys.readouterr().out


def test_status_says_when_it_is_charging(monkeypatch, capsys):
    monkeypatch.setattr(battery, "_pmset_output", lambda: _pmset("65%; charging"))
    cli.cmd_status(Namespace(json=False))
    assert "Pad battery: 65% (charging)" in capsys.readouterr().out


def test_status_leaves_it_out_with_no_reading(capsys):
    cli.cmd_status(Namespace(json=False))
    assert "Pad battery" not in capsys.readouterr().out


def test_status_json_carries_it(monkeypatch, capsys):
    monkeypatch.setattr(battery, "_pmset_output", lambda: _pmset("65%; "))
    cli.cmd_status(Namespace(json=True))
    reading = json.loads(capsys.readouterr().out)["pad_battery"]
    assert reading == {"level": 65, "charging": False}


def test_status_json_has_null_with_no_reading(capsys):
    cli.cmd_status(Namespace(json=True))
    assert json.loads(capsys.readouterr().out)["pad_battery"] is None
