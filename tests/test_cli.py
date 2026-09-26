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
    out = capsys.readouterr().out
    # one-line grouped header: bold name + dimmed path + count
    assert "bin" in out and "(3 apps)" in out


def test_picker_plain_grouped_numbering_follows_groups(capsys, monkeypatch):
    """Two dirs -> headers per dir, flat numbers underneath still work."""
    apps = [
        DiscoveredApp(id="b1", name="B Tool", exec_path="/opt/bdir/btool"),
        DiscoveredApp(id="a1", name="A Tool", exec_path="/home/u/.local/bin/atool"),
    ]
    monkeypatch.setattr(sys, "stdin", io.StringIO("2\n"))
    picked = _picker_plain(apps, {})
    out = capsys.readouterr().out
    # groups sorted by folder base ("bdir" < "bin"), so 1=b1, 2=a1
    assert picked == {"a1"}
    assert "bdir" in out and "bin" in out


def test_split_vanished_broken_vs_kept():
    from desktop_manager.__main__ import _split_vanished
    vanished = [
        {"filename": "gone.desktop", "binary": "/x/gone", "binary_exists": False},
        {"filename": "stay.desktop", "binary": "/x/stay", "binary_exists": True},
        {"filename": "old.desktop", "binary": "/x/old"},  # no flag -> safe kept
    ]
    broken, kept = _split_vanished(vanished)
    assert [v["filename"] for v in broken] == ["gone.desktop"]
    assert {v["filename"] for v in kept} == {"stay.desktop", "old.desktop"}


def test_print_vanished_labels(capsys):
    from desktop_manager.__main__ import _print_vanished
    _print_vanished([
        {"filename": "gone.desktop", "binary": "/x/gone", "binary_exists": False},
        {"filename": "stay.desktop", "binary": "/x/stay", "binary_exists": True},
    ], "Kept:")
    out = capsys.readouterr().out
    assert "Broken" in out and "gone.desktop" in out
    assert "Kept:" in out and "stay.desktop" in out


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


def test_preticked_ids_match_by_binary(tmp_path):
    from desktop_manager.__main__ import _preticked_ids
    target = tmp_path / "apps"
    target.mkdir()
    (target / "old.desktop").write_text(
        "[Desktop Entry]\nName=Old\nExec=/bin/fakeapp\nType=Application\n"
        "X-Managed-By=met-desktop-manager\n")
    state = {"file:fake": "old.desktop"}
    apps = [DiscoveredApp(id="desktop:fake", name="Fake", exec_path="/bin/fakeapp")]
    assert _preticked_ids(apps, state, target) == {"desktop:fake"}


def test_interactive_adopts_id_changed_selection(tmp_path, capsys, monkeypatch):
    """Harvest-incident replay: old state ID, new discovery ID, same binary."""
    from desktop_manager.__main__ import interactive
    target = tmp_path / "apps"
    target.mkdir()
    (target / "old.desktop").write_text(
        "[Desktop Entry]\nName=Tool\nExec=mullvad-exclude /bin/tool\n"
        "Type=Application\nX-Managed-By=met-desktop-manager\n")
    state_file = tmp_path / "state.json"
    state_file.write_text('{"file:tool": "old.desktop"}')
    monkeypatch.setattr(
        "desktop_manager.scanner.scan",
        lambda cfg: [DiscoveredApp(id="desktop:tool", name="Tool",
                                   exec_path="/bin/tool")])
    monkeypatch.setattr(sys, "stdin", io.StringIO("1\n"))
    interactive(target, state_file)
    out = capsys.readouterr().out
    assert (target / "old.desktop").exists()  # same file, not deleted
    assert "adopted" in out
    import json
    assert json.loads(state_file.read_text()) == {"desktop:tool": "old.desktop"}
