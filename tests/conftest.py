"""Shared test fixtures.

The single most important thing here: **no test ever touches real hardware.**
A developer running ``pytest`` with a Codex Micro plugged in must not have their
LEDs repainted or their terminal typed into, so the device layer is disabled for
the whole session and every action test uses a recording backend.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from freemicro.device import ENV_NO_DEVICE


@pytest.fixture(autouse=True)
def no_hardware(monkeypatch):
    """Make the pad look absent to every test."""
    monkeypatch.setenv(ENV_NO_DEVICE, "1")


@pytest.fixture(autouse=True)
def no_launchd(monkeypatch):
    """No test may register a real job with launchd. Tests can still override this."""
    monkeypatch.setattr(
        "freemicro.daemon._launchctl",
        lambda *args, **kwargs: (1, "launchctl is disabled in tests"),
    )


@pytest.fixture(autouse=True)
def source_outside_protected_folders(monkeypatch):
    """A clone under ``~/Documents`` must not change what the daemon tests see."""
    monkeypatch.setattr("freemicro.daemon._source_dir", lambda: Path("/opt/freemicro"))


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    """Keep tests off the developer's real ``~/.freemicro`` config."""
    monkeypatch.setenv("FREEMICRO_HOME", str(tmp_path / "freemicro"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("FREEMICRO_KEYMAP", raising=False)
