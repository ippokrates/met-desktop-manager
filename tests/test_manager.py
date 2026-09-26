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
    app = _app()
    manager.create_desktop(app, target, state)
    handmade = target / "handmade.desktop"
    handmade.write_text("[Desktop Entry]\nName=X\nExec=/x\nType=Application\n")

    # Binary still discovered, just not selected: genuine deselect -> removed.
    res = manager.sync({}, target, state, discovered={"file:vesktop": app})
    assert res["removed"] == ["vesktop.desktop"]
    assert res["vanished"] == []
    assert not (target / "vesktop.desktop").exists()
    assert handmade.exists()  # unmanaged files always survive
    assert handmade.read_text().startswith("[Desktop Entry]\nName=X")


def test_sync_vanished_is_kept_by_default(dirs):
    target, state = dirs
    app = _app()
    manager.create_desktop(app, target, state)
    # Binary gone from discovery and not selected: kept + reported.
    res = manager.sync({}, target, state, discovered={})
    assert res["removed"] == []
    assert (target / "vesktop.desktop").exists()
    assert len(res["vanished"]) == 1
    v = res["vanished"][0]
    assert v["app_id"] == "file:vesktop"
    assert v["filename"] == "vesktop.desktop"
    assert v["binary"] == manager.managed_exec_key(target / "vesktop.desktop")
    assert isinstance(v["binary_exists"], bool)
    assert json.loads(state.read_text()) == {"file:vesktop": "vesktop.desktop"}


def test_sync_vanished_reports_broken_when_binary_gone(dirs, tmp_path):
    import stat
    real = tmp_path / "gonebin"
    real.write_bytes(b"\x7fELF" + b"\x00" * 60)
    real.chmod(real.stat().st_mode | stat.S_IXUSR)
    target, state = dirs
    app = _app("file:gone", "Gone", str(real), "")
    manager.create_desktop(app, target, state)
    real.unlink()  # program deleted after launcher was created
    res = manager.sync({}, target, state, discovered={})
    assert (target / "gone.desktop").exists()  # still kept by default
    assert res["vanished"][0]["binary_exists"] is False


def test_sync_vanished_reports_kept_when_binary_still_there(dirs, tmp_path):
    import stat
    real = tmp_path / "staybin"
    real.write_bytes(b"\x7fELF" + b"\x00" * 60)
    real.chmod(real.stat().st_mode | stat.S_IXUSR)
    target, state = dirs
    app = _app("file:stay", "Stay", str(real), "")
    manager.create_desktop(app, target, state)
    # undiscovered (e.g. dir removed from apps.yaml) but file still on disk
    res = manager.sync({}, target, state, discovered={})
    assert (target / "stay.desktop").exists()
    assert res["vanished"][0]["binary_exists"] is True


def test_sync_vanished_pruned_on_request(dirs):
    target, state = dirs
    manager.create_desktop(_app(), target, state)
    res = manager.sync({}, target, state, discovered={}, prune_vanished=True)
    assert res["removed"] == ["vesktop.desktop"]
    assert not (target / "vesktop.desktop").exists()


def test_sync_adopts_id_changed_entry(dirs):
    """The harvest incident: same binary, new discovery ID -> adopt, no delete."""
    target, state = dirs
    manager.create_desktop(_app("file:vesktop", "Vesktop",
                                "/opt/Vesktop/vesktop", "vesktop"), target, state)
    new_app = {"id": "desktop:vesktop", "name": "VSCodium-Style Vesktop",
               "exec_path": "/opt/Vesktop/vesktop", "icon": "vesktop"}
    res = manager.sync({"desktop:vesktop": new_app}, target, state,
                       discovered={"desktop:vesktop": new_app})
    assert res["adopted"] == {"file:vesktop": "desktop:vesktop"}
    assert res["removed"] == []
    assert (target / "vesktop.desktop").exists()
    assert "VSCodium-Style Vesktop (Excluded)" in (target / "vesktop.desktop").read_text()
    assert json.loads(state.read_text()) == {"desktop:vesktop": "vesktop.desktop"}


def test_find_orphaned_marks_vanished(dirs):
    target, state = dirs
    app = _app()
    manager.create_desktop(app, target, state)
    orphans = manager.find_orphaned(json.loads(state.read_text()), {},
                                    target, discovered={})
    assert orphans["file:vesktop"]["vanished"] is True
    orphans2 = manager.find_orphaned(json.loads(state.read_text()), {},
                                     target,
                                     discovered={"file:vesktop": app})
    assert orphans2["file:vesktop"]["vanished"] is False


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
    assert res2["created"] == [] and res2["removed"] == []
    assert res2["vanished"] == [] and res2["adopted"] == {}


def test_name_collision_gets_suffix(dirs):
    target, state = dirs
    (target / "handmade.desktop").write_text("[Desktop Entry]\nName=X\n")
    dest = manager.create_desktop(_app("c", "Handmade", "/bin/ls", ""), target, state)
    assert dest.name == "handmade-2.desktop"


def test_orphan_managed_file_reaped_only_when_pruning(dirs):
    target, state = dirs
    manager.create_desktop(_app(), target, state)
    orphan = target / "orphan.desktop"
    orphan.write_text("[Desktop Entry]\nX-Managed-By=met-desktop-manager\nExec=/x\n")
    # Default: unknown stray files are kept + reported, not deleted.
    res = manager.sync({"file:vesktop": _app()}, target, state, discovered={})
    assert orphan.exists()
    assert [v["filename"] for v in res["vanished"]] == ["orphan.desktop"]
    # Explicit prune: reaped as before.
    res2 = manager.sync({"file:vesktop": _app()}, target, state,
                        discovered={}, prune_vanished=True)
    assert not orphan.exists() and "orphan.desktop" in res2["removed"], res2


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
