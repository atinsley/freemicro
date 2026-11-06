"""Installing, updating and daemonising must not damage what is already there."""

from __future__ import annotations

import json
import os
from argparse import Namespace
from pathlib import Path

import pytest

from freemicro import cli, daemon, onboarding
from freemicro import hooks_install as hi

# -- Claude Code's settings file ---------------------------------------------


@pytest.mark.parametrize(
    "content",
    ['{"permissions": {"allow": ["x"]},}', "not json", "[1, 2]", '"text"'],
)
def test_install_refuses_a_settings_file_it_cannot_read(tmp_path, content):
    settings = tmp_path / "settings.json"
    settings.write_text(content, encoding="utf-8")
    with pytest.raises(hi.SettingsError, match="settings.json"):
        hi.install_hooks(settings)
    assert settings.read_text(encoding="utf-8") == content


def test_a_dry_run_also_refuses(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("not json", encoding="utf-8")
    with pytest.raises(hi.SettingsError):
        hi.install_hooks(settings, dry_run=True)


def test_uninstall_refuses_too(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("not json", encoding="utf-8")
    with pytest.raises(hi.SettingsError):
        hi.uninstall_hooks(settings)
    assert settings.read_text(encoding="utf-8") == "not json"


@pytest.mark.parametrize("content", [None, "", "  \n"])
def test_a_missing_or_blank_file_is_a_fresh_start(tmp_path, content):
    settings = tmp_path / "settings.json"
    if content is not None:
        settings.write_text(content, encoding="utf-8")
    hi.install_hooks(settings)
    assert set(json.loads(settings.read_text(encoding="utf-8"))["hooks"]) == set(
        hi.HOOK_EVENTS
    )


def test_status_still_tolerates_a_broken_file(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("not json", encoding="utf-8")
    assert hi.status(settings)["installed"] is False


def test_a_failed_write_leaves_the_original_alone(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    original = json.dumps({"model": "opus"})
    settings.write_text(original, encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        hi.install_hooks(settings)
    assert settings.read_text(encoding="utf-8") == original
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


def test_the_file_keeps_its_permissions(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    settings.chmod(0o600)
    hi.install_hooks(settings)
    assert settings.stat().st_mode & 0o777 == 0o600


def test_a_new_file_is_private(tmp_path):
    settings = tmp_path / "settings.json"
    hi.install_hooks(settings)
    assert settings.stat().st_mode & 0o777 == 0o600


def test_a_symlinked_file_stays_a_symlink(tmp_path):
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "settings.json"
    link.symlink_to(real)
    hi.install_hooks(link)
    assert link.is_symlink()
    assert "hooks" in json.loads(real.read_text(encoding="utf-8"))


def test_non_ascii_text_survives_a_rewrite(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"note": "café"}, ensure_ascii=False), encoding="utf-8")
    hi.install_hooks(settings)
    assert "café" in settings.read_text(encoding="utf-8")


def _install_args(settings, **overrides):
    values = {
        "uninstall": False, "settings": str(settings), "dry_run": False,
        "no_verify": True,
    }
    values.update(overrides)
    return Namespace(**values)


@pytest.mark.parametrize("uninstall", [False, True])
def test_the_command_explains_and_fails(tmp_path, capsys, uninstall):
    settings = tmp_path / "settings.json"
    settings.write_text("not json", encoding="utf-8")
    assert cli.cmd_install(_install_args(settings, uninstall=uninstall)) == 2
    error = capsys.readouterr().err
    assert str(settings) in error
    assert "not changed" in error


def test_setup_reports_it_rather_than_crashing(monkeypatch, tmp_path):
    monkeypatch.setattr(hi, "default_settings_path", lambda: tmp_path / "settings.json")
    (tmp_path / "settings.json").write_text("not json", encoding="utf-8")
    monkeypatch.setattr(onboarding, "ask", lambda *a, **k: True)
    answers = onboarding.Answers(Namespace(yes=True))
    assert onboarding.step_hooks(answers) is False


# -- freemicro update ---------------------------------------------------------


def _fake_distribution(monkeypatch, direct_url):
    class Dist:
        def read_text(self, name):
            return direct_url if name == "direct_url.json" else None

    monkeypatch.setattr(
        "importlib.metadata.distribution", lambda name: Dist()
    )


def test_a_pypi_install_is_not_from_source(monkeypatch):
    _fake_distribution(monkeypatch, None)
    assert cli._installed_from_source() == ""


def test_an_editable_install_is_from_source(monkeypatch):
    _fake_distribution(
        monkeypatch,
        '{"url": "file:///Users/me/freemicro", "dir_info": {"editable": true}}',
    )
    assert cli._installed_from_source() == "/Users/me/freemicro"


def test_a_git_install_is_from_source(monkeypatch):
    _fake_distribution(
        monkeypatch,
        '{"url": "https://github.com/me/freemicro.git", '
        '"vcs_info": {"vcs": "git", "commit_id": "abc"}}',
    )
    assert cli._installed_from_source() == "https://github.com/me/freemicro.git"


def test_unreadable_metadata_is_not_from_source(monkeypatch):
    _fake_distribution(monkeypatch, "not json")
    assert cli._installed_from_source() == ""


@pytest.mark.parametrize("check", [True, False])
def test_update_never_replaces_a_source_install(monkeypatch, capsys, check):
    def forbidden(*args, **kwargs):
        raise AssertionError("must not run anything or contact PyPI")

    monkeypatch.setattr(cli, "_installed_from_source", lambda: "/Users/me/freemicro")
    monkeypatch.setattr(cli, "_latest_pypi_version", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    assert cli.cmd_update(Namespace(check=check)) == 0
    assert "/Users/me/freemicro" in capsys.readouterr().out


# -- the daemon ------------------------------------------------------------------


def _layout(monkeypatch, tmp_path, binary, source):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(daemon, "daemon_argv", lambda: [str(tmp_path / binary)])
    monkeypatch.setattr(daemon, "_source_dir", lambda: tmp_path / source)


def test_code_in_a_protected_folder_is_caught_with_the_launcher_outside_it(
    monkeypatch, tmp_path
):
    _layout(
        monkeypatch, tmp_path,
        ".local/bin/freemicro", "Documents/python/freemicro/src/freemicro",
    )
    assert daemon.protected_location() == "Documents"


def test_a_normal_install_passes(monkeypatch, tmp_path):
    _layout(
        monkeypatch, tmp_path,
        ".local/bin/freemicro", ".local/pipx/venvs/freemicro/lib/freemicro",
    )
    assert daemon.protected_location() is None


def test_a_launcher_in_a_protected_folder_is_still_caught(monkeypatch, tmp_path):
    _layout(monkeypatch, tmp_path, "Desktop/venv/bin/freemicro", "code/freemicro")
    assert daemon.protected_location() == "Desktop"


def test_install_refuses_and_says_how_to_fix_it(monkeypatch, tmp_path):
    _layout(
        monkeypatch, tmp_path,
        ".local/bin/freemicro", "Documents/python/freemicro/src/freemicro",
    )
    monkeypatch.setattr(daemon, "agents_dir", lambda: tmp_path / "agents")

    def forbidden(*args, **kwargs):
        raise AssertionError("must refuse before touching launchd")

    monkeypatch.setattr(daemon, "_launchctl", forbidden)
    result = daemon.install()
    assert result["ok"] is False
    assert result["warning"] == "Documents"
    assert "~/Documents" in result["error"]
    assert not (tmp_path / "agents").exists()
