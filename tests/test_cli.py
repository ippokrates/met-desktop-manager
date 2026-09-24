"""Tests for desktop_manager.__main__ (picker + interactive).

Interactive tests patch scan() with fake apps and redirect all file
writes to tmp_path. The real applications dir is never touched.
"""
import io
import sys

import pytest

from desktop_manager.__main__ import _picker_plain, interactive
from desktop_manager.scanner import DiscoveredApp


def _fake_apps():
    return [
        DiscoveredApp(id="a1", name="App One", exec_path="/bin/one"),
        DiscoveredApp(id="a2", name="App Two", exec_path="/bin/two"),
        DiscoveredApp(id="a3", name="App Three", exec_path="/bin/three"),
    ]


def _patch_scan(monkeypatch):
    monkeypatch.setattr("desktop_manager.scanner.scan", lambda cfg: _fake_apps())


def test_picker_plain_ranges(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("1,3\n"))
    picked = _picker_plain(_fake_apps(), {})
    assert picked == {"a1", "a3"}


def test_picker_plain_range_syntax_and_invalid(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("1-2,99,zzz\n"))
    picked = _picker_plain(_fake_apps(), {})
    assert picked == {"a1", "a2"}


def test_picker_plain_eof_cancels(monkeypatch):
    class ExplodingStdin(io.StringIO):
        def readline(self, *a, **k):
            raise EOFError
    monkeypatch.setattr(sys, "stdin", ExplodingStdin(""))
    assert _picker_plain(_fake_apps(), {}) is None


def test_interactive_creates_then_deselects(tmp_path, capsys, monkeypatch):
    _patch_scan(monkeypatch)
    target, state = tmp_path / "apps", tmp_path / "state.json"

    monkeypatch.setattr(sys, "stdin", io.StringIO("1,2\n"))
    interactive(target, state)
    assert (target / "app-one.desktop").exists()
    assert (target / "app-two.desktop").exists()
    assert not (target / "app-three.desktop").exists()

    monkeypatch.setattr(sys, "stdin", io.StringIO("2\n"))
    interactive(target, state)
    assert not (target / "app-one.desktop").exists()
    assert (target / "app-two.desktop").exists()


def test_interactive_empty_selection_removes_all(tmp_path, capsys, monkeypatch):
    _patch_scan(monkeypatch)
    target, state = tmp_path / "apps", tmp_path / "state.json"

    monkeypatch.setattr(sys, "stdin", io.StringIO("1\n"))
    interactive(target, state)
    assert (target / "app-one.desktop").exists()

    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    interactive(target, state)
    assert not (target / "app-one.desktop").exists()
