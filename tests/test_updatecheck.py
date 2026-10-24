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


class _Result:
    """Stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def test_pipx_spec_reads_package_or_url(monkeypatch):
    out = ('{"venvs": {"met-desktop-manager": {"metadata": {"main_package": '
           '{"package_or_url": "git+https://example.com/x.git"}}}}}')
    monkeypatch.setattr(uc.subprocess, "run",
                        lambda *a, **k: _Result(stdout=out))
    assert uc._pipx_spec() == "git+https://example.com/x.git"


def test_pipx_spec_none_on_bad_output(monkeypatch):
    monkeypatch.setattr(uc.subprocess, "run",
                        lambda *a, **k: _Result(stdout="not json"))
    assert uc._pipx_spec() is None


def test_is_local():
    assert uc._is_local(".")
    assert uc._is_local("/home/me/repo")
    assert uc._is_local("file:///tmp/x")
    assert not uc._is_local("git+https://example.com/x.git")


def test_update_requires_pipx(monkeypatch, capsys):
    monkeypatch.setattr(uc.shutil, "which", lambda _: None)
    assert uc.run_update() == 1
    assert "pipx not found" in capsys.readouterr().out


def test_update_refuses_non_pipx_install(monkeypatch, capsys):
    monkeypatch.setattr(uc.shutil, "which", lambda _: "/usr/bin/pipx")
    monkeypatch.setattr(uc, "_pipx_spec", lambda: None)
    assert uc.run_update() == 1
    assert "Not installed via pipx" in capsys.readouterr().out


def test_update_refuses_local_checkout(monkeypatch, capsys):
    monkeypatch.setattr(uc.shutil, "which", lambda _: "/usr/bin/pipx")
    monkeypatch.setattr(uc, "_pipx_spec", lambda: "/home/me/met")
    assert uc.run_update() == 1
    out = capsys.readouterr().out
    assert "local checkout" in out
    assert "git pull" in out


def test_update_reports_unreachable_github(monkeypatch, capsys):
    monkeypatch.setattr(uc.shutil, "which", lambda _: "/usr/bin/pipx")
    monkeypatch.setattr(uc, "_pipx_spec", lambda: "git+https://example.com/x.git")
    monkeypatch.setattr(uc, "latest_on_github", lambda: "")
    assert uc.run_update() == 1
    assert "Could not reach GitHub" in capsys.readouterr().out


def test_update_skips_when_fresh(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(uc.shutil, "which", lambda _: "/usr/bin/pipx")
    monkeypatch.setattr(uc, "_pipx_spec", lambda: "git+https://example.com/x.git")
    monkeypatch.setattr(uc, "installed", lambda: "0.4.1")
    monkeypatch.setattr(uc, "latest_on_github", lambda: "0.4.1")
    monkeypatch.setattr(uc.subprocess, "run", lambda *a, **k: calls.append(a))
    assert uc.run_update() == 0
    assert calls == []
    assert "Already up to date" in capsys.readouterr().out


def test_update_reinstalls_when_newer(monkeypatch, capsys):
    calls = []

    def fake(argv, **kwargs):
        calls.append(argv)
        return _Result(0)

    monkeypatch.setattr(uc.shutil, "which", lambda _: "/usr/bin/pipx")
    monkeypatch.setattr(uc, "_pipx_spec", lambda: "git+https://example.com/x.git")
    monkeypatch.setattr(uc, "installed", lambda: "0.4.1")
    monkeypatch.setattr(uc, "latest_on_github", lambda: "0.5.0")
    monkeypatch.setattr(uc.subprocess, "run", fake)
    assert uc.run_update() == 0
    assert calls == [["pipx", "reinstall", "met-desktop-manager"]]
    assert "Updated to 0.5.0" in capsys.readouterr().out


def test_update_reports_pipx_failure(monkeypatch, capsys):
    monkeypatch.setattr(uc.shutil, "which", lambda _: "/usr/bin/pipx")
    monkeypatch.setattr(uc, "_pipx_spec", lambda: "git+https://example.com/x.git")
    monkeypatch.setattr(uc, "installed", lambda: "0.4.1")
    monkeypatch.setattr(uc, "latest_on_github", lambda: "0.5.0")
    monkeypatch.setattr(uc.subprocess, "run",
                        lambda *a, **k: _Result(returncode=3))
    assert uc.run_update() == 3
    assert "Update failed" in capsys.readouterr().out


def test_cli_update_subcommand(monkeypatch):
    calls = []
    monkeypatch.setattr(uc, "run_update", lambda: calls.append(True) or 0)
    monkeypatch.setattr("sys.argv", ["met", "update"])
    assert main() == 0
    assert calls == [True]
