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
           "flatpak": {"enabled": False}, "snap": {"enabled": False}}
    assert s.scan(cfg) == []


def test_load_config_missing_returns_empty():
    assert s.load_config("/nonexistent-xyz.yaml") == {}
