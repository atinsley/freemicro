"""A failed write to the pad is an ordinary outcome for `freemicro lights`."""

from __future__ import annotations

import json

import pytest

from freemicro import cli
from freemicro.device import DeviceError


class FailingPad:
    def send(self, message):
        raise DeviceError("IOHIDDeviceSetReport failed: 0xE00002BC")

    def close(self):
        pass


class WorkingPad:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)

    def close(self):
        pass


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "keymap.json"
    path.write_text(
        json.dumps({"version": 1, "bindings": {}, "lighting": {"enabled": True}}),
        encoding="utf-8",
    )
    return path


def _lights(config, *extra):
    return cli.main(["lights", "waiting", "--hold", "0", "--config", str(config), *extra])


@pytest.mark.parametrize("extra", [(), ("--restore",)])
def test_a_failed_write_is_one_line_and_exit_1(monkeypatch, config, capsys, extra):
    monkeypatch.setattr(cli, "_open_pad", lambda: FailingPad())
    assert _lights(config, *extra) == 1
    captured = capsys.readouterr()
    assert "Could not reach the pad" in captured.err
    assert "0xE00002BC" in captured.err
    assert "try again" in captured.err
    assert "Traceback" not in captured.err
    assert "waiting" not in captured.out


def test_a_working_pad_still_gets_the_light(monkeypatch, config, capsys):
    pad = WorkingPad()
    monkeypatch.setattr(cli, "_open_pad", lambda: pad)
    assert _lights(config) == 0
    assert pad.sent
    assert "waiting" in capsys.readouterr().out
