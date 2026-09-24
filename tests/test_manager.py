"""Tests for desktop_manager.manager. EVERYTHING runs in tmp_path.

Nothing here may touch ~/.local/share/applications or the real state.json.
"""
import json
from pathlib import Path

import pytest

from desktop_manager import generator, manager


@pytest.fixture
def dirs(tmp_path):
    target = tmp_path / "apps"
    target.mkdir()
    state = tmp_path / "state.json"
    return target, state


def _app(app_id="file:vesktop", name="Vesktop",
         exec_path="/opt/Vesktop/vesktop", icon="vesktop"):
    return {"id": app_id, "name": name, "exec_path": exec_path, "icon": icon}


def test_create_writes_file_and_state(dirs):
    target, state = dirs
    dest = manager.create_desktop(_app(), target, state)
    assert dest == target / "vesktop.desktop"
    text = dest.read_text()
    assert "Name=Vesktop (Excluded)" in text
    assert "Exec=mullvad-exclude /opt/Vesktop/vesktop" in text
    assert generator.is_managed_content(text)
    assert json.loads(state.read_text()) == {"file:vesktop": "vesktop.desktop"}


def test_sync_deselect_removes_only_managed(dirs):
    target, state = dirs
    manager.create_desktop(_app(), target, state)
    handmade = target / "handmade.desktop"
    handmade.write_text("[Desktop Entry]\nName=X\nExec=/x\nType=Application\n")

    res = manager.sync({}, target, state)
    assert res["removed"] == ["vesktop.desktop"]
    assert not (target / "vesktop.desktop").exists()
    assert handmade.exists()  # unmanaged files always survive
    assert handmade.read_text().startswith("[Desktop Entry]\nName=X")


def test_sync_select_creates_and_updates(dirs):
    target, state = dirs
    res = manager.sync({"file:zen": _app("file:zen", "Zen Bin",
                                         "/home/user/Downloads/zen/zen-bin")},
                       target, state)
    assert res["created"] == ["zen-bin.desktop"]
    # second identical sync: no changes (idempotent)
    res2 = manager.sync({"file:zen": _app("file:zen", "Zen Bin",
                                          "/home/user/Downloads/zen/zen-bin")},
                        target, state)
    assert res2 == {"created": [], "removed": []}


def test_name_collision_gets_suffix(dirs):
    target, state = dirs
    (target / "handmade.desktop").write_text("[Desktop Entry]\nName=X\n")
    dest = manager.create_desktop(_app("c", "Handmade", "/bin/ls", ""), target, state)
    assert dest.name == "handmade-2.desktop"


def test_orphan_managed_file_reaped(dirs):
    target, state = dirs
    manager.create_desktop(_app(), target, state)
    orphan = target / "orphan.desktop"
    orphan.write_text("[Desktop Entry]\nX-Managed-By=met-desktop-manager\nExec=/x\n")
    res = manager.sync({"file:vesktop": _app()}, target, state)
    assert not orphan.exists()
    assert "orphan.desktop" in res["removed"]


def test_remove_desktop(dirs):
    target, state = dirs
    manager.create_desktop(_app(), target, state)
    assert manager.remove_desktop("file:vesktop", target, state) is True
    assert not (target / "vesktop.desktop").exists()
    assert manager.remove_desktop("file:vesktop", target, state) is False


def test_remove_desktop_refuses_unmanaged(dirs):
    target, state = dirs
    handmade = target / "handmade.desktop"
    handmade.write_text("[Desktop Entry]\nName=X\n")
    state.write_text(json.dumps({"evil": "handmade.desktop"}))
    assert manager.remove_desktop("evil", target, state) is False
    assert handmade.exists()


def test_managed_files_lists_only_ours(dirs):
    target, state = dirs
    manager.create_desktop(_app(), target, state)
    (target / "other.desktop").write_text("[Desktop Entry]\nName=O\n")
    assert [p.name for p in manager.managed_files(target)] == ["vesktop.desktop"]


def test_validate_generated_file(dirs):
    target, state = dirs
    dest = manager.create_desktop(_app(), target, state)
    ok, msg = manager.validate_desktop_file(dest)
    assert ok, msg
