"""Tests for desktop_manager.scanner (tmp dirs only, never the real system paths)."""
import os
import stat

from desktop_manager import scanner as s


def _make_exe(path, magic=b"\x7fELF...."):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(magic + b"\x00" * 64)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_is_excluded_substring_and_glob():
    assert s._is_excluded("my-uninstaller", ["uninstall"])
    assert s._is_excluded("libfoo.so.1", ["*.so*"])
    assert s._is_excluded("install.sh", ["install.sh"])
    assert not s._is_excluded("vesktop", ["uninstall", "*.so*"])
    assert not s._is_excluded("eclipse-inst", [])


def test_has_skip_ext():
    from pathlib import Path
    assert s._has_skip_ext(Path("fw.abl.img"))
    assert s._has_skip_ext(Path("libvulkan.so.1"))
    assert not s._has_skip_ext(Path("vesktop"))
    assert not s._has_skip_ext(Path("zen-bin"))


def test_looks_executable_magic(tmp_path):
    assert s._looks_executable(_make_exe(tmp_path / "elf-bin"))
    assert s._looks_executable(_make_exe(tmp_path / "run.sh", b"#!/bin/sh\n"))
    data = tmp_path / "data.names"
    data.write_text("age,sex,cp\n")
    data.chmod(0o755)  # stray +x bit must NOT fool the check
    assert not s._looks_executable(data)
    assert not s._looks_executable(tmp_path / "missing")


def test_pretty_name():
    assert s._pretty_name("zen-bin") == "Zen Bin"
    assert s._pretty_name("signal-desktop") == "Signal Desktop"


def test_pretty_name_strips_versions_and_platform():
    assert s._pretty_name("Glint-1.9.5") == "Glint"
    assert s._pretty_name("helium-0.13.3.1-x86_64") == "Helium"
    assert s._pretty_name("ZCode-3.11.2-linux-x64") == "Zcode"
    assert s._pretty_name("notesnook_linux_x86_64") == "Notesnook"
    assert s._pretty_name("session-desktop-linux-x86_64-1.18.1") == "Session Desktop"
    assert s._pretty_name("PS4RPS-linux-1.0.0-beta.8-x86_64") == "Ps4Rps"


def test_pretty_name_keeps_legit_names():
    assert s._pretty_name("7z") == "7Z"
    assert s._pretty_name("monero-wallet-gui") == "Monero Wallet Gui"
    assert s._pretty_name("2to3-3.11") == "2To3"


def test_exec_key_stable_across_sources():
    # Same binary under different IDs/wrappers -> identical key.
    assert s.exec_key("/opt/Vesktop/vesktop") == s.exec_key(
        "mullvad-exclude /opt/Vesktop/vesktop")
    assert s.exec_key("/opt/Vesktop/vesktop %U") == s.exec_key(
        "/opt/Vesktop/vesktop")
    assert s.exec_key("") == ""
    # Multi-word commands stay distinct per app.
    assert s.exec_key("flatpak run org.a.App") != s.exec_key("flatpak run org.b.App")
    assert s.exec_key("flatpak run org.a.App") == s.exec_key("flatpak run org.a.App")


def test_scan_path_dirs_finds_and_skips(tmp_path):
    bindir = tmp_path / "bin"
    good = _make_exe(bindir / "myapp")
    _make_exe(bindir / "helper.so.1")  # skipped by extension
    (bindir / "notes.txt").write_text("hi")  # not executable
    apps = s.scan_path_dirs([str(bindir)], excludes=[])
    by_exec = {a.exec_path: a for a in apps}
    assert str(good.resolve()) in by_exec
    assert len(apps) == 1


def test_scan_path_dirs_missing_dir_ok():
    assert s.scan_path_dirs(["/nonexistent-dir-xyz"], []) == []


def test_scan_extra_dirs_prunes_and_limits(tmp_path):
    root = tmp_path / "opt"
    top = _make_exe(root / "topapp")
    deep = _make_exe(root / "a" / "b" / "c" / "d" / "deepapp")  # depth 4 > max 3
    _make_exe(root / "jbr" / "bin" / "java")  # pruned runtime dir
    apps = s.scan_extra_dirs([str(root)], max_depth=3, excludes=[])
    execs = {a.exec_path for a in apps}
    assert str(top.resolve()) in execs
    assert str(deep.resolve()) not in execs
    assert not any("jbr" in e for e in execs)


def test_scan_full_with_tmp_config(tmp_path):
    bindir = tmp_path / "bin"
    extra = tmp_path / "extra"
    _make_exe(bindir / "cool-tool")
    _make_exe(extra / "fancy" / "fancyapp")
    cfg = {
        "path_dirs": [str(bindir)],
        "extra_dirs": [str(extra)],
        "max_depth": 3,
        "excludes": [],
        "flatpak": {"enabled": False},
        "snap": {"enabled": False},
    }
    apps = s.scan(cfg)
    names = {a.name for a in apps}
    assert {"Cool Tool", "Fancyapp"} <= names


def test_scan_empty_config_with_disabled_managers():
    cfg = {"path_dirs": [], "extra_dirs": [],
           "desktop_files": {"enabled": False},
           "flatpak": {"enabled": False}, "snap": {"enabled": False}}
    assert s.scan(cfg) == []


def test_load_config_missing_returns_empty():
    assert s.load_config("/nonexistent-xyz.yaml") == {}


def test_clean_exec_strips_codes_and_wrappers():
    assert s._clean_exec("/usr/share/codium/codium %F") == ("/usr/share/codium/codium", 0)
    assert s._clean_exec("/usr/share/codium/codium --new-window %F") == (
        "/usr/share/codium/codium", 1)
    assert s._clean_exec("env WEBKIT_DISABLE_X=1 myapp %u") == ("myapp", 0)
    assert s._clean_exec("FOO=1 myapp --flag") == ("myapp", 1)
    assert s._clean_exec('myapp "some arg" %U') == ("myapp", 1)
    assert s._clean_exec("%F") == ("", 0)
    assert s._clean_exec("") == ("", 0)


def _exe(path):
    import stat
    path.write_bytes(b"\x7fELF" + b"\x00" * 60)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_resolve_binary(tmp_path, monkeypatch):
    real = _exe(tmp_path / "realapp")
    assert s._resolve_binary(str(real)) == str(real.resolve())
    assert s._resolve_binary("/nonexistent-xyz-app") == ""
    assert s._resolve_binary("rel/dir/app") == ""  # ambiguous, skipped
    (tmp_path / "data.txt").write_text("not executable")
    assert s._resolve_binary(str(tmp_path / "data.txt")) == ""
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    assert s._resolve_binary("realapp") == str(real.resolve())
    assert s._resolve_binary("definitely-not-here-xyz") == ""


def test_parse_desktop_file(tmp_path):
    f = tmp_path / "a.desktop"
    f.write_text("[Desktop Entry]\nName=Demo\nName[de]=X\nExec=/bin/demo %F\n"
                 "Icon=demo\nType=Application\n")
    fields = s._parse_desktop_file(f)
    assert fields["Name"] == "Demo"  # locale variant ignored
    assert fields["Exec"] == "/bin/demo %F"
    assert fields["Icon"] == "demo"
    mine = tmp_path / "mine.desktop"
    mine.write_text("[Desktop Entry]\nX-Managed-By=met-desktop-manager\nExec=/x\n")
    assert s._parse_desktop_file(mine).get("managed") == "yes"


def _launcher(directory, filename, body):
    p = directory / filename
    p.write_text(body)
    return p


def test_scan_desktop_files_harvests_and_prefers_plain(tmp_path):
    appdir = tmp_path / "applications"
    appdir.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _exe(bindir / "coolapp")
    _launcher(appdir, "coolapp.desktop",
              "[Desktop Entry]\nName=Cool App\nExec=" + str(bindir / "coolapp") + " %F\n"
              "Icon=coolapp\nType=Application\n")
    _launcher(appdir, "coolapp-newwin.desktop",
              "[Desktop Entry]\nName=Cool New Window\nExec=" + str(bindir / "coolapp")
              + " --new-window %F\nIcon=coolapp\n")
    apps = s.scan_desktop_files([str(appdir)], excludes=[])
    assert len(apps) == 1  # same binary deduped
    assert apps[0].name == "Cool App"  # fewest-args launcher wins
    assert apps[0].icon_hint == "coolapp"
    assert apps[0].exec_path == str((bindir / "coolapp").resolve())


def test_scan_desktop_files_skips(tmp_path):
    appdir = tmp_path / "applications"
    appdir.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _exe(bindir / "okapp")
    _launcher(appdir, "ok.desktop",
              "[Desktop Entry]\nName=Ok\nExec=" + str(bindir / "okapp") + "\n")
    _launcher(appdir, "hidden.desktop",
              "[Desktop Entry]\nName=H\nExec=" + str(bindir / "okapp") + "\nNoDisplay=true\n")
    _launcher(appdir, "link.desktop",
              "[Desktop Entry]\nName=L\nExec=" + str(bindir / "okapp") + "\nType=Link\n")
    _launcher(appdir, "gone.desktop",
              "[Desktop Entry]\nName=G\nExec=/nonexistent-xyz-app\n")
    _launcher(appdir, "mine.desktop",
              "[Desktop Entry]\nName=M\nExec=" + str(bindir / "okapp") + "\n"
              "X-Managed-By=met-desktop-manager\n")
    apps = s.scan_desktop_files([str(appdir)], excludes=[])
    assert [a.name for a in apps] == ["Ok"]
    # excludes apply to harvested binaries too
    assert s.scan_desktop_files([str(appdir)], excludes=["okapp"]) == []


def test_group_by_directory_buckets():
    from desktop_manager.scanner import DiscoveredApp
    apps = [
        DiscoveredApp(id="a", name="Vesktop", exec_path="/opt/Vesktop/vesktop",
                      source="SCAN:/opt"),
        DiscoveredApp(id="b", name="Tool", exec_path="/home/u/.local/bin/tool",
                      source="PATH:/home/u/.local/bin"),
        DiscoveredApp(id="c", name="Spot", exec_path="flatpak run com.spot.App",
                      source="flatpak"),
    ]
    groups = s.group_by_directory(apps)
    assert set(groups) == {"/opt/Vesktop", "/home/u/.local/bin", "flatpak"}
    assert [a.id for a in groups["/opt/Vesktop"]] == ["a"]
    # groups sorted by folder base name
    assert list(groups) == sorted(groups,
                                  key=lambda k: s.group_base_name(k).lower())


def test_group_helpers_labels_and_home(tmp_path, monkeypatch):
    from desktop_manager.scanner import DiscoveredApp
    flat = DiscoveredApp(id="f", name="Spot", exec_path="flatpak run x", source="flatpak")
    assert s.group_key_for_app(flat) == "flatpak"
    snap = DiscoveredApp(id="p", name="S", exec_path="/snap/bin/s", source="snap")
    assert s.group_key_for_app(snap) == "snap"
    assert s.group_base_name("/opt/Vesktop") == "Vesktop"
    assert s.group_base_name("flatpak") == "flatpak"
    monkeypatch.setenv("HOME", str(tmp_path))
    assert s.short_group_path(str(tmp_path / ".local" / "bin")) == "~/.local/bin"


def test_binary_exists_abs_and_missing(tmp_path):
    real = _make_exe(tmp_path / "realbin")
    assert s.binary_exists(str(real)) is True
    assert s.binary_exists("mullvad-exclude " + str(real)) is True
    assert s.binary_exists(str(real) + " --flag %U") is True
    assert s.binary_exists(str(tmp_path / "nope-missing-xyz")) is False
    assert s.binary_exists("") is False
    assert s.binary_exists("mullvad-exclude %U") is False


def test_binary_exists_bare_name_via_path(tmp_path, monkeypatch):
    real = _make_exe(tmp_path / "pathbin")
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    assert s.binary_exists("pathbin") is True
    assert s.binary_exists("definitely-not-here-xyz") is False
