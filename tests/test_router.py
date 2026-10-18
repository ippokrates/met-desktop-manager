"""Tests for desktop_manager.router (AppImage self-healing launcher).

Everything runs in tmp_path; the real applications dir is never touched.
"""
import os
from pathlib import Path

import pytest

from desktop_manager import generator, manager, router


def _appimage(path: Path, mtime: float | None = None) -> Path:
    """Create a fake AppImage file (executable, ELF-ish bytes)."""
    path.write_bytes(b"\x7fELF" + b"\x00" * 60)
    path.chmod(0o755)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _launcher(target: Path, app_id: str, binary: str, name="App") -> Path:
    """Write a routed managed launcher exactly like manager would."""
    target.mkdir(parents=True, exist_ok=True)
    dest = target / "app.desktop"
    dest.write_text(generator.build_desktop_content(
        name, binary, route_cmd=["/usr/local/bin/desktop-manager"],
        route_id=app_id, met_binary=binary))
    return dest


@pytest.fixture
def env(tmp_path):
    target = tmp_path / "apps"
    state_file = tmp_path / "state.json"
    download = tmp_path / "Downloads"
    download.mkdir()
    return target, state_file, download


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch):
    """Never read the developer's real apps.yaml during tests."""
    monkeypatch.setattr(router.scanner, "load_config", lambda *a, **k: {})


def _set_prefer_newest(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(router.scanner, "load_config",
                        lambda *a, **k: {"appimage": {"prefer_newest": value}})


def _old_and_new(env):
    """Recorded 1.1.1 (older mtime) plus 1.1.3 next to it, both on disk."""
    target, state_file, dl = env
    old = _appimage(dl / "app-1.1.1.AppImage", mtime=1_000_000)
    new = _appimage(dl / "app-1.1.3.AppImage", mtime=2_000_000)
    launcher = _launcher(target, "file:app-1.1.1", str(old))
    manager.save_state({"file:app-1.1.1": "app.desktop"}, state_file)
    return target, state_file, old, new, launcher


# --- strip_version --------------------------------------------------------

def test_strip_version_formats():
    assert router.strip_version("OpenChamber-1.2.2") == "OpenChamber"
    assert router.strip_version("OpenChamber 2.0.0") == "OpenChamber"
    assert router.strip_version("app_2.2a") == "app"
    assert router.strip_version("app-2.2.1") == "app"
    assert router.strip_version("vesktop") == "vesktop"


def test_strip_version_digit_leading_and_pure_versions():
    assert router.strip_version("7zip-1.0") == "7zip"
    assert router.strip_version("1.2.3") == "1.2.3"  # nothing sensible to strip
    assert router.strip_version("") == ""


# --- find_newest_appimage -------------------------------------------------

def test_newest_by_mtime_not_by_name(env):
    """Text order says 1.9.0 > 1.10.2; mtime says otherwise."""
    _, _, dl = env
    older = _appimage(dl / "app-1.9.0.AppImage", mtime=1_000_000)
    newer = _appimage(dl / "app-1.10.2.AppImage", mtime=2_000_000)
    assert router.find_newest_appimage(older) == str(newer)


def test_newest_tie_break_is_natural_number_order(env):
    """Same mtime -> 1.10.2 beats 1.9.0 (digit runs compare as numbers)."""
    _, _, dl = env
    low = _appimage(dl / "app-1.9.0.AppImage", mtime=5_000_000)
    high = _appimage(dl / "app-1.10.2.AppImage", mtime=5_000_000)
    assert router.find_newest_appimage(low) == str(high)


def test_newest_ignores_other_names_and_partials(env):
    _, _, dl = env
    old = _appimage(dl / "app-1.2.2.AppImage", mtime=1_000_000)
    _appimage(dl / "Other-9.9.9.AppImage", mtime=9_000_000)      # other app
    (dl / "app-1.2.3.AppImage.part").write_text("download")       # partial
    # only candidates besides the recorded file are the non-matching ones
    assert router.find_newest_appimage(old) == ""
    old.unlink()
    assert router.find_newest_appimage(old) == ""


def test_newest_never_returns_the_recorded_file(env):
    _, _, dl = env
    old = _appimage(dl / "app-1.2.2.AppImage", mtime=9_000_000)
    _appimage(dl / "app-1.2.3.AppImage", mtime=1_000_000)
    assert router.find_newest_appimage(old) == str(dl / "app-1.2.3.AppImage")


def test_newest_missing_dir_is_empty(tmp_path):
    assert router.find_newest_appimage(tmp_path / "nope" / "app-1.0.AppImage") == ""


# --- resolve --------------------------------------------------------------

def test_resolve_ok_leaves_everything_alone(env):
    target, state_file, dl = env
    binary = _appimage(dl / "app-1.2.2.AppImage")
    launcher = _launcher(target, "file:app-1.2.2", str(binary))
    before = launcher.read_text()
    state = {"file:app-1.2.2": "app.desktop"}

    got, eff_id, status = router.resolve("file:app-1.2.2", state, state_file, target)
    assert (got, eff_id, status) == (str(binary), "file:app-1.2.2", "ok")
    assert launcher.read_text() == before
    assert not state_file.exists()          # nothing written


def test_resolve_heals_launcher_and_state(env):
    target, state_file, dl = env
    gone = dl / "app-1.2.2.AppImage"
    launcher = _launcher(target, "file:app-1.2.2", str(gone))
    fresh = _appimage(dl / "app-1.2.3.AppImage")
    state = {"file:app-1.2.2": "app.desktop"}
    manager.save_state(state, state_file)

    got, eff_id, status = router.resolve(
        "file:app-1.2.2", state, state_file, target)
    assert (got, eff_id, status) == (str(fresh), "file:app-1.2.3", "healed")

    text = launcher.read_text()
    assert "run file:app-1.2.3" in text
    assert f"X-Met-Binary={fresh}" in text
    assert "openchamber" not in text  # sanity: no stray text
    assert manager.load_state(state_file) == {"file:app-1.2.3": "app.desktop"}


def test_resolve_converts_legacy_direct_exec_launcher(env):
    """A launcher from before routing existed gets repaired into routed form."""
    target, state_file, dl = env
    target.mkdir()
    gone = dl / "app-1.2.2.AppImage"
    (target / "app.desktop").write_text(
        "[Desktop Entry]\nName=App (Excluded)\n"
        f"Exec=mullvad-exclude {gone}\nType=Application\n"
        "X-Managed-By=met-desktop-manager\n")
    fresh = _appimage(dl / "app-1.2.3.AppImage")
    manager.save_state({"file:app-1.2.2": "app.desktop"}, state_file)

    got, eff_id, status = router.resolve("file:app-1.2.2", None, state_file, target)
    assert (got, eff_id, status) == (str(fresh), "file:app-1.2.3", "healed")
    text = (target / "app.desktop").read_text()
    assert "Exec=" in text and f"run file:app-1.2.3" in text
    assert "mullvad-exclude" not in text      # the router applies it now
    assert f"X-Met-Binary={fresh}" in text
    assert manager.load_state(state_file) == {"file:app-1.2.3": "app.desktop"}


def test_resolve_missing_when_nothing_matches(env):
    target, state_file, dl = env
    gone = dl / "app-1.2.2.AppImage"
    _launcher(target, "file:app-1.2.2", str(gone))
    state = {"file:app-1.2.2": "app.desktop"}
    got, eff_id, status = router.resolve("file:app-1.2.2", state, state_file, target)
    assert (got, status) == ("", "missing")
    assert state == {"file:app-1.2.2": "app.desktop"}   # unchanged


def test_resolve_unknown_id(env):
    target, state_file, _dl = env
    got, _eff, status = router.resolve("file:nope", {}, state_file, target)
    assert (got, status) == ("", "unknown")


def test_resolve_never_touches_a_foreign_file(env):
    """Safety: a hand-made launcher tracked by state is left alone."""
    target, state_file, dl = env
    target.mkdir()
    _appimage(dl / "app-1.2.3.AppImage")          # a heal would have used this
    (target / "app.desktop").write_text(
        "[Desktop Entry]\nName=Not ours\n"
        f"Exec=mullvad-exclude {dl}/app-1.2.2.AppImage\n"
        "Type=Application\n")
    state = {"file:app-1.2.2": "app.desktop"}

    got, eff_id, status = router.resolve("file:app-1.2.2", state, state_file, target)
    assert (got, status) == ("", "unknown")
    assert "Not ours" in (target / "app.desktop").read_text()
    assert state == {"file:app-1.2.2": "app.desktop"}


def test_resolve_follows_a_stale_exec_id(env):
    """Half-healed launcher: Exec id is old, state key is already new."""
    target, state_file, dl = env
    binary = _appimage(dl / "app-1.2.3.AppImage")
    _launcher(target, "file:app-1.2.2", str(binary))     # stale id in Exec
    state = {"file:app-1.2.3": "app.desktop"}
    got, eff_id, status = router.resolve("file:app-1.2.2", state, state_file, target)
    assert (got, eff_id, status) == (str(binary), "file:app-1.2.3", "ok")


def test_resolve_non_appimage_missing_is_not_healed(env):
    target, state_file, _dl = env
    target.mkdir()
    (target / "plain.desktop").write_text(
        "[Desktop Entry]\nName=Plain\nExec=mullvad-exclude /gone/tool\n"
        "Type=Application\nX-Managed-By=met-desktop-manager\n")
    state = {"bin:tool": "plain.desktop"}
    got, _eff, status = router.resolve("bin:tool", state, state_file, target)
    assert (got, status) == ("", "missing")


# --- heal_all -------------------------------------------------------------

def test_heal_all_reports_and_repairs(env):
    target, state_file, dl = env
    _launcher(target, "file:app-1.2.2", str(dl / "app-1.2.2.AppImage"))
    fresh = _appimage(dl / "app-1.2.3.AppImage")
    manager.save_state({"file:app-1.2.2": "app.desktop"}, state_file)

    messages = router.heal_all(target, state_file)
    assert messages == [f"re-linked file:app-1.2.2 -> {fresh.name}"]
    assert manager.load_state(state_file) == {"file:app-1.2.3": "app.desktop"}
    assert router.heal_all(target, state_file) == []     # idempotent


def test_heal_all_routes_a_working_appimage(env):
    """A still-working direct launcher is made update-proof too."""
    target, state_file, dl = env
    target.mkdir()
    binary = _appimage(dl / "krita-5.3.4.1-x86_64.AppImage")
    (target / "krita.desktop").write_text(
        "[Desktop Entry]\nName=Krita (Excluded)\n"
        f"Exec=mullvad-exclude {binary}\nType=Application\n"
        "X-Managed-By=met-desktop-manager\n")
    manager.save_state({"file:krita-5.3.4.1-x86_64": "krita.desktop"}, state_file)

    messages = router.heal_all(target, state_file)
    assert messages == ["routed file:krita-5.3.4.1-x86_64 (survives its own updates)"]
    text = (target / "krita.desktop").read_text()
    assert "run file:krita-5.3.4.1-x86_64" in text
    assert f"X-Met-Binary={binary}" in text
    state = manager.load_state(state_file)
    assert state == {"file:krita-5.3.4.1-x86_64": "krita.desktop"}  # key kept
    assert router.heal_all(target, state_file) == []                # idempotent


def test_heal_all_leaves_plain_binaries_alone(env):
    target, state_file, _dl = env
    target.mkdir()
    (target / "tool.desktop").write_text(
        "[Desktop Entry]\nName=Tool\nExec=mullvad-exclude /bin/sh\n"
        "Type=Application\nX-Managed-By=met-desktop-manager\n")
    manager.save_state({"bin:tool": "tool.desktop"}, state_file)
    assert router.heal_all(target, state_file) == []
    assert "Exec=mullvad-exclude /bin/sh" in (target / "tool.desktop").read_text()


def test_launch_path_does_not_rewrite_working_launchers(env):
    """`met run` stays read-only when nothing is broken."""
    target, state_file, dl = env
    target.mkdir()
    binary = _appimage(dl / "krita-5.3.4.1-x86_64.AppImage")
    (target / "krita.desktop").write_text(
        "[Desktop Entry]\nName=Krita\n"
        f"Exec=mullvad-exclude {binary}\nType=Application\n"
        "X-Managed-By=met-desktop-manager\n")
    before = (target / "krita.desktop").read_text()
    state = {"file:krita-5.3.4.1-x86_64": "krita.desktop"}
    got, _eff, status = router.resolve(
        "file:krita-5.3.4.1-x86_64", state, state_file, target)
    assert (got, status) == (str(binary), "ok")
    assert (target / "krita.desktop").read_text() == before


# --- prefer newest (old file still on disk) -------------------------------

def test_resolve_adopts_newer_while_old_exists(env):
    """New download next to the old one must win, not wait for a deletion."""
    target, state_file, old, new, launcher = _old_and_new(env)

    got, eff_id, status = router.resolve(
        "file:app-1.1.1", None, state_file, target)
    assert (got, eff_id, status) == (str(new), "file:app-1.1.3", "updated")

    text = launcher.read_text()
    assert "run file:app-1.1.3" in text
    assert f"X-Met-Binary={new}" in text
    assert manager.load_state(state_file) == {"file:app-1.1.3": "app.desktop"}
    assert old.is_file()                 # never deleted, only unpointed


def test_resolve_keeps_old_when_recorded_is_newest(env):
    """The recorded file being newest means no write at all."""
    target, state_file, dl = env
    newer = _appimage(dl / "app-1.1.3.AppImage", mtime=2_000_000)
    _appimage(dl / "app-1.1.1.AppImage", mtime=1_000_000)
    launcher = _launcher(target, "file:app-1.1.3", str(newer))
    manager.save_state({"file:app-1.1.3": "app.desktop"}, state_file)
    before = launcher.read_text()

    got, _eff, status = router.resolve(
        "file:app-1.1.3", None, state_file, target)
    assert (got, status) == (str(newer), "ok")
    assert launcher.read_text() == before
    assert manager.load_state(state_file) == {"file:app-1.1.3": "app.desktop"}


def test_resolve_equal_mtime_uses_natural_order(env):
    """Same mtime: 1.1.3 beats 1.1.1, and 1.1.1 never ousts 1.1.3."""
    target, state_file, dl = env
    record = _appimage(dl / "app-1.1.3.AppImage", mtime=5_000_000)
    _appimage(dl / "app-1.1.1.AppImage", mtime=5_000_000)
    _launcher(target, "file:app-1.1.3", str(record))
    manager.save_state({"file:app-1.1.3": "app.desktop"}, state_file)

    got, _eff, status = router.resolve(
        "file:app-1.1.3", None, state_file, target)
    assert (got, status) == (str(record), "ok")


def test_prefer_newest_false_keeps_old(env, monkeypatch):
    """Repair-only mode: a newer file on disk changes nothing."""
    _set_prefer_newest(monkeypatch, False)
    target, state_file, old, _new, launcher = _old_and_new(env)
    before = launcher.read_text()

    got, _eff, status = router.resolve(
        "file:app-1.1.1", None, state_file, target)
    assert (got, status) == (str(old), "ok")
    assert launcher.read_text() == before
    assert manager.load_state(state_file) == {"file:app-1.1.1": "app.desktop"}


def test_prefer_newest_false_still_routes(env, monkeypatch):
    """Repair-only mode still converts a legacy direct Exec= launcher."""
    _set_prefer_newest(monkeypatch, False)
    target, state_file, old, _new, _launcher_path = _old_and_new(env)
    (target / "app.desktop").write_text(
        "[Desktop Entry]\nName=App (Excluded)\n"
        f"Exec=mullvad-exclude {old}\nType=Application\n"
        "X-Managed-By=met-desktop-manager\n")

    got, _eff, status = router.resolve(
        "file:app-1.1.1", None, state_file, target, convert=True)
    assert (got, status) == (str(old), "routed")
    text = (target / "app.desktop").read_text()
    assert "run file:app-1.1.1" in text
    assert f"X-Met-Binary={old}" in text


def test_prefer_newest_defaults_to_true(monkeypatch):
    monkeypatch.setattr(router.scanner, "load_config", lambda *a, **k: {})
    assert router._prefer_newest() is True
    _set_prefer_newest(monkeypatch, False)
    assert router._prefer_newest() is False


def test_heal_all_reports_a_switch(env):
    """`met`/`--sync` adopt the newer file and say so."""
    target, state_file, old, new, _launcher_path = _old_and_new(env)

    messages = router.heal_all(target, state_file)
    assert messages == [f"switched file:app-1.1.1 -> {new.name}"]
    assert manager.load_state(state_file) == {"file:app-1.1.3": "app.desktop"}
    assert router.heal_all(target, state_file) == []       # idempotent
    assert old.is_file()


def test_newest_match_is_case_insensitive(env):
    """`krita-1.1` -> `Krita-1.2` must still heal."""
    _, _, dl = env
    old = _appimage(dl / "krita-1.1.AppImage", mtime=1_000_000)
    fresh = _appimage(dl / "Krita-1.2.AppImage", mtime=2_000_000)
    assert router.find_newest_appimage(old) == str(fresh)


def test_resolve_heals_across_a_case_change(env):
    target, state_file, dl = env
    old = _appimage(dl / "krita-1.1.AppImage", mtime=1_000_000)
    fresh = _appimage(dl / "Krita-1.2.AppImage", mtime=2_000_000)
    launcher = _launcher(target, "file:krita-1.1", str(old))
    manager.save_state({"file:krita-1.1": "app.desktop"}, state_file)

    got, eff_id, status = router.resolve(
        "file:krita-1.1", None, state_file, target)
    assert (got, eff_id, status) == (str(fresh), "file:krita-1.2", "updated")
    assert f"X-Met-Binary={fresh}" in launcher.read_text()
    assert manager.load_state(state_file) == {"file:krita-1.2": "app.desktop"}


# --- run ------------------------------------------------------------------

def test_run_builds_command_with_prefix_and_extra_args(env, monkeypatch, capsys):
    target, state_file, dl = env
    binary = _appimage(dl / "app-1.2.2.AppImage")
    _launcher(target, "file:app-1.2.2", str(binary))
    manager.save_state({"file:app-1.2.2": "app.desktop"}, state_file)
    monkeypatch.setattr(router, "STATE_FILE", state_file)
    monkeypatch.setattr(router, "resolve_target_dir", lambda *a: target)

    seen = {}
    monkeypatch.setattr(router.os, "execvp",
                        lambda f, c: seen.update(file=f, cmd=c))
    assert router.run(["file:app-1.2.2", "--flag", "%U"]) == 0
    assert seen["file"] == "mullvad-exclude"
    assert seen["cmd"] == ["mullvad-exclude", str(binary), "--flag", "%U"]


def test_run_resolve_prints_path(env, monkeypatch, capsys):
    target, state_file, dl = env
    binary = _appimage(dl / "app-1.2.2.AppImage")
    _launcher(target, "file:app-1.2.2", str(binary))
    manager.save_state({"file:app-1.2.2": "app.desktop"}, state_file)
    monkeypatch.setattr(router, "STATE_FILE", state_file)
    monkeypatch.setattr(router, "resolve_target_dir", lambda *a: target)

    assert router.run(["--resolve", "file:app-1.2.2"]) == 0
    assert capsys.readouterr().out.strip() == str(binary)


def test_run_unknown_app_fails(env, monkeypatch, capsys):
    target, state_file, _dl = env
    manager.save_state({}, state_file)
    monkeypatch.setattr(router, "STATE_FILE", state_file)
    monkeypatch.setattr(router, "resolve_target_dir", lambda *a: target)
    assert router.run(["file:nope"]) == 1
    assert "unknown app" in capsys.readouterr().err


def test_run_missing_appimage_notifies_and_fails(env, monkeypatch, capsys):
    target, state_file, dl = env
    _launcher(target, "file:app-1.2.2", str(dl / "app-1.2.2.AppImage"))
    manager.save_state({"file:app-1.2.2": "app.desktop"}, state_file)
    monkeypatch.setattr(router, "STATE_FILE", state_file)
    monkeypatch.setattr(router, "resolve_target_dir", lambda *a: target)

    notified = []
    monkeypatch.setattr(router, "_notify", notified.append)
    assert router.run(["file:app-1.2.2"]) == 1
    assert "no matching .AppImage" in capsys.readouterr().err
    assert notified and "no matching .AppImage" in notified[0]


def test_run_without_id_prints_usage(monkeypatch, capsys):
    assert router.run([]) == 2
    assert "usage" in capsys.readouterr().err


def test_met_cmd_is_never_empty():
    assert router.met_cmd()


def test_met_cmd_prefers_the_running_command(tmp_path, monkeypatch):
    fake = tmp_path / "met"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(router.sys, "argv", [str(fake)])
    assert router.met_cmd() == [str(fake)]


def test_met_cmd_ignores_python_m_invocation(monkeypatch):
    """`python -m desktop_manager` must not bake in __main__.py as the command."""
    monkeypatch.setattr(router.sys, "argv",
                        ["/repo/desktop_manager/__main__.py"])
    cmd = router.met_cmd()
    assert not cmd[0].endswith(".py")
