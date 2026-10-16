"""Tests for the update check: version compare, manual and auto flows."""
from desktop_manager import updatecheck as uc
from desktop_manager.__main__ import main, build_parser


def test_compare_versions():
    assert uc._compare("0.2.0", "0.1.0") == 1
    assert uc._compare("0.1.0", "0.1.0") == 0
    assert uc._compare("0.1.0", "0.2.0") == -1
    # numeric ordering, not text ordering ("0.10" > "0.2")
    assert uc._compare("0.10.0", "0.2.0") == 1
    # unparseable version: never triggers anything
    assert uc._compare("1.0-beta", "0.9.0") == 0


def test_auto_hints_when_newer(monkeypatch, capsys):
    monkeypatch.setattr(uc, "installed", lambda: "0.1.0")
    monkeypatch.setattr(uc, "latest_on_github", lambda: "0.2.0")
    uc.auto_check()
    assert "update available: 0.2.0 (you have 0.1.0)" in capsys.readouterr().out


def test_auto_silent_when_equal(monkeypatch, capsys):
    monkeypatch.setattr(uc, "installed", lambda: "0.1.0")
    monkeypatch.setattr(uc, "latest_on_github", lambda: "0.1.0")
    uc.auto_check()
    assert capsys.readouterr().out == ""


def test_auto_silent_when_unreachable(monkeypatch, capsys):
    monkeypatch.setattr(uc, "installed", lambda: "0.1.0")
    monkeypatch.setattr(uc, "latest_on_github", lambda: "")
    uc.auto_check()
    assert capsys.readouterr().out == ""


def test_auto_silent_when_not_installed(monkeypatch, capsys):
    monkeypatch.setattr(uc, "installed", lambda: "")
    monkeypatch.setattr(uc, "latest_on_github", lambda: "0.2.0")
    uc.auto_check()
    assert capsys.readouterr().out == ""


def test_manual_reports_status_even_when_fresh(monkeypatch, capsys):
    monkeypatch.setattr(uc, "installed", lambda: "0.1.0")
    monkeypatch.setattr(uc, "latest_on_github", lambda: "0.1.0")
    uc.manual_check()
    out = capsys.readouterr().out
    assert "installed 0.1.0" in out
    assert "github   0.1.0" in out


def test_manual_tells_when_unreachable(monkeypatch, capsys):
    monkeypatch.setattr(uc, "latest_on_github", lambda: "")
    uc.manual_check()
    assert "Could not reach GitHub" in capsys.readouterr().out


def test_cli_flag_runs_manual_check(monkeypatch):
    calls = []
    monkeypatch.setattr(uc, "manual_check", lambda: calls.append(True))
    monkeypatch.setattr("sys.argv", ["met", "--check-updates"])
    main()
    assert calls == [True]


def test_parser_has_flag():
    assert build_parser().parse_args(["--check-updates"]).check_updates is True
