"""Noticing when a Claude app update changes what the desktop features rely on."""

from __future__ import annotations

import json
import plistlib
from argparse import Namespace

import pytest

from freemicro import cli, desktop_compat, desktop_sessions

CURRENT_RULE = (
    b'yieldsToProposedTitle(e,n){return!(n===void 0||n===e.title||e.title?.trim()'
    b'&&e.titleSource!=="auto"||e.titleFromPr||t.gA(e)||U.p(e.remoteControlSpawn))}'
)
CURRENT_BUNDLE = (
    b'url:"claude://code/new?source=desktop_action"'
    b'url:"claude://code/continue?session=last"' + CURRENT_RULE
)
RECORD = {
    "sessionId": "local_709f264f-e1f0-4261-9bc4-9bb723070862",
    "cliSessionId": "e7b56c88-f138-4830-9d38-da9f68531878",
    "title": "Ready",
    "titleSource": "auto",
    "isArchived": False,
}


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A Claude app and chat records that match what the features rely on."""
    bundle = tmp_path / "Claude.app"
    (bundle / "Contents" / "Resources").mkdir(parents=True)
    monkeypatch.setattr(desktop_compat, "app_path", lambda: bundle)
    root = tmp_path / "records"
    monkeypatch.setattr(desktop_sessions, "sessions_root", lambda: root)

    class App:
        @staticmethod
        def code(data):
            (bundle / "Contents" / "Resources" / "app.asar").write_bytes(data)

        @staticmethod
        def schemes(*names):
            plist = {"CFBundleURLTypes": [{"CFBundleURLSchemes": list(names)}]}
            (bundle / "Contents" / "Info.plist").write_bytes(plistlib.dumps(plist))

        @staticmethod
        def record(data=RECORD, name="local_a.json"):
            folder = root / "account" / "org"
            folder.mkdir(parents=True, exist_ok=True)
            text = data if isinstance(data, str) else json.dumps(data)
            (folder / name).write_text(text, encoding="utf-8")

    App.code(CURRENT_BUNDLE)
    App.schemes("claude", "msauth.example")
    App.record()
    return App


def _problems():
    return " | ".join(p.summary for p in desktop_compat.check())


def _without(key):
    return {k: v for k, v in RECORD.items() if k != key}


# -- when nothing is wrong -----------------------------------------------------------


def test_the_current_app_has_no_problems(app):
    assert desktop_compat.check() == []


def test_no_app_is_not_a_problem(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_compat, "app_path", lambda: tmp_path / "absent.app")
    assert desktop_compat.check() == []


def test_minified_names_changing_is_not_a_problem(app):
    """A new build renames ``t.gA`` and the arguments. That isn't a change of rule."""
    app.code(CURRENT_BUNDLE.replace(b"(e,n){", b"(a,b){").replace(b"t.gA", b"q.Zz"))
    assert desktop_compat.check() == []


def test_some_records_lacking_a_title_source_is_normal(app):
    app.record(_without("titleSource"), name="local_fork.json")
    assert desktop_compat.check() == []


def test_having_no_chats_yet_is_not_a_problem(app, tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(desktop_sessions, "sessions_root", lambda: empty)
    assert desktop_compat.check() == []


# -- the app's code ------------------------------------------------------------------


@pytest.mark.parametrize(
    "link", [b"claude://code/continue", b"claude://code/new"], ids=["chat", "new"]
)
def test_a_link_going_away_is_noticed(app, link):
    app.code(CURRENT_BUNDLE.replace(link, b"claude://other"))
    assert link.decode() in _problems()


def test_the_link_scheme_going_away_is_noticed(app):
    app.schemes("something-else")
    assert "claude://" in _problems()


def test_a_missing_info_plist_is_noticed(app, tmp_path):
    (tmp_path / "Claude.app" / "Contents" / "Info.plist").unlink()
    assert "claude://" in _problems()


def test_the_title_rule_going_away_is_noticed(app):
    app.code(CURRENT_BUNDLE.replace(b"yieldsToProposedTitle", b"somethingElse"))
    assert "title rule" in _problems()


@pytest.mark.parametrize(
    "part",
    [b'e.titleSource!=="auto"', b"e.titleFromPr", b"U.p(e.remoteControlSpawn)"],
    ids=["source", "pr", "remote-control"],
)
def test_the_title_rule_changing_is_noticed(app, part):
    app.code(CURRENT_BUNDLE.replace(part, b"e.other"))
    assert "title rule" in _problems()


def test_code_it_cannot_find_is_noticed(app, tmp_path):
    (tmp_path / "Claude.app" / "Contents" / "Resources" / "app.asar").unlink()
    assert "code" in _problems()


def test_empty_code_is_noticed_rather_than_crashing(app):
    app.code(b"")
    assert "code" in _problems()


# -- the app's chat records ----------------------------------------------------------


def test_the_records_folder_going_away_is_noticed(app, tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_sessions, "sessions_root", lambda: tmp_path / "gone")
    assert "records" in _problems()


def test_the_records_moving_within_the_folder_is_noticed(app, tmp_path, monkeypatch):
    root = tmp_path / "moved"
    root.mkdir()
    (root / "chats.db").write_text("x", encoding="utf-8")
    monkeypatch.setattr(desktop_sessions, "sessions_root", lambda: root)
    assert "records" in _problems()


def test_records_that_no_longer_parse_are_noticed(app):
    app.record("not json")
    assert "records" in _problems()


@pytest.mark.parametrize("key", ["sessionId", "cliSessionId"])
def test_losing_a_chat_id_is_noticed(app, key):
    app.record(_without(key))
    assert "ids" in _problems()


def test_a_chat_id_in_a_new_form_is_noticed(app):
    app.record({**RECORD, "sessionId": "chat/123"})
    assert "ids" in _problems()


@pytest.mark.parametrize("key", ["title", "titleSource"])
def test_losing_a_title_field_is_noticed(app, key):
    app.record(_without(key))
    assert "title" in _problems()


def test_each_problem_says_what_it_affects(app):
    app.code(b"nothing useful")
    problems = desktop_compat.check()
    assert problems
    assert all(p.summary and p.effect for p in problems)


def test_nothing_it_reads_can_raise(app, monkeypatch):
    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(desktop_sessions, "sessions_root", boom)
    assert "boom" in _problems()


# -- where it shows up ---------------------------------------------------------------


def test_status_is_quiet_when_nothing_is_wrong(app, capsys):
    cli.cmd_status(Namespace(json=False))
    assert "Claude app" not in capsys.readouterr().out


def test_status_warns_and_says_what_it_affects(app, capsys):
    app.code(CURRENT_BUNDLE.replace(b"claude://code/continue", b"claude://other"))
    assert cli.cmd_status(Namespace(json=False)) == 0
    out = capsys.readouterr().out
    assert "! The Claude app has changed" in out
    assert "claude://code/continue" in out
    assert "Agent keys" in out


def test_status_json_carries_the_problems(app, capsys):
    app.code(CURRENT_BUNDLE.replace(b"claude://code/new", b"claude://other"))
    cli.cmd_status(Namespace(json=True))
    problems = json.loads(capsys.readouterr().out)["desktop_app"]
    assert "claude://code/new" in problems[0]["summary"]
    assert problems[0]["effect"]


def test_status_json_has_an_empty_list_when_nothing_is_wrong(app, capsys):
    cli.cmd_status(Namespace(json=True))
    assert json.loads(capsys.readouterr().out)["desktop_app"] == []


def test_doctor_warns_without_counting_a_failure(app, capsys):
    """The pad itself is fine, so a changed app shouldn't fail the whole check."""
    app.code(b"nothing useful")
    failures = []
    cli._doctor_desktop_app(lambda *a, **k: failures.append(a))
    out = capsys.readouterr().out
    assert "[warn]" in out
    assert failures == []


def test_doctor_says_when_the_app_is_fine(app, capsys):
    passes = []
    cli._doctor_desktop_app(lambda good, label: passes.append((good, label)))
    assert "[warn]" not in capsys.readouterr().out
    assert passes and passes[0][0] is True


def test_doctor_says_nothing_with_no_app(capsys):
    calls = []
    cli._doctor_desktop_app(lambda *a, **k: calls.append(a))
    assert calls == []
    assert capsys.readouterr().out == ""


def test_the_tests_never_read_the_real_app():
    assert not str(desktop_compat.app_path()).startswith("/Applications")
