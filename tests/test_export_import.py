"""Tests for export/import. EVERYTHING runs in tmp_path.

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


def _managed(name="Vesktop (Excluded)",
             exec_line="mullvad-exclude /opt/Vesktop/vesktop"):
    return ("[Desktop Entry]\nVersion=1.0\n"
            f"Name={name}\nExec={exec_line}\nType=Application\n"
            "Terminal=false\nIcon=vesktop\n"
            "X-Managed-By=met-desktop-manager\n")


def test_portable_rewrite_handles_wrapper_and_quotes():
    home = "/home/old"
    text = (_managed(exec_line='mullvad-exclude "/home/old/My App/run" --x')
            + "Path=/home/old/work\n")
    portable = manager._portable_text(text, home)
    assert 'Exec=mullvad-exclude "~/My App/run" --x' in portable
    assert "Path=~/work" in portable
    back = manager._restore_text(portable, "/home/new")
    assert 'Exec=mullvad-exclude "/home/new/My App/run" --x' in back
    assert "Path=/home/new/work" in back


def test_portable_leaves_names_and_flatpak_alone():
    text = _managed(exec_line="flatpak run org.videolan.VLC")
    portable = manager._portable_text(text, "/home/old")
    assert "Exec=flatpak run org.videolan.VLC" in portable
    assert "Name=Vesktop (Excluded)" in portable
    assert manager._restore_text(portable, "/home/new") == text


def test_export_import_roundtrip_with_home_change(dirs, tmp_path):
    target, state = dirs
    old_home, new_home = str(tmp_path / "old"), str(tmp_path / "new")
    exe = f"{old_home}/Downloads/zen/zen"
    dest = manager.create_desktop(
        {"id": "file:zen", "name": "Zen", "exec_path": exe, "icon": ""},
        target, state)
    assert old_home in dest.read_text()

    data = manager.export_managed(target, state, home=old_home)
    assert len(data["entries"]) == 1
    assert "~/" in data["entries"][0]["content"]
    assert old_home not in data["entries"][0]["content"]

    target2 = tmp_path / "apps2"
    target2.mkdir()
    state2 = tmp_path / "state2.json"
    res = manager.import_managed(data, target2, state2, home=new_home)
    assert res["wrote"] == [dest.name]
    assert res["skipped"] == []
    text = (target2 / dest.name).read_text()
    assert new_home in text and "~/" not in text
    assert generator.is_managed_content(text)
    assert json.loads(state2.read_text()) == {"file:zen": dest.name}


def test_import_skips_unmanaged_content(dirs):
    target, state = dirs
    data = {"entries": [{"app_id": "x", "filename": "evil.desktop",
                         "content": "[Desktop Entry]\nName=X\nExec=/x\n"}]}
    res = manager.import_managed(data, target, state)
    assert res["wrote"] == []
    assert len(res["skipped"]) == 1
    assert not (target / "evil.desktop").exists()


def test_import_never_overwrites_handmade(dirs):
    target, state = dirs
    handmade = target / "shaded.desktop"
    handmade.write_text("[Desktop Entry]\nName=Mine\nExec=/mine\n")
    data = {"entries": [{"app_id": "a", "filename": "shaded.desktop",
                         "content": _managed()}]}
    res = manager.import_managed(data, target, state)
    assert res["wrote"] == ["shaded-2.desktop"]
    assert handmade.read_text().startswith("[Desktop Entry]\nName=Mine")


def test_import_rejects_unsafe_filenames(dirs):
    target, state = dirs
    data = {"entries": [
        {"app_id": "a", "filename": "../evil.desktop", "content": _managed()},
        {"app_id": "b", "filename": "sub/dir.desktop", "content": _managed()},
    ]}
    res = manager.import_managed(data, target, state)
    assert res["wrote"] == []
    assert len(res["skipped"]) == 2
    assert list(target.glob("*.desktop")) == []


def test_import_rejects_bad_bundle(dirs):
    target, state = dirs
    with pytest.raises(ValueError):
        manager.import_managed({"nope": 1}, target, state)
    with pytest.raises(ValueError):
        manager.import_managed({"entries": "notalist"}, target, state)


def test_export_includes_stray_with_synthetic_id(dirs):
    target, state = dirs
    stray = target / "stray.desktop"
    stray.write_text(_managed())
    data = manager.export_managed(target, state)
    assert [e["app_id"] for e in data["entries"]] == ["imported:stray"]


def test_fake_ls_bundle_imports(dirs):
    """The exact bundle from chat: /bin/ls exists everywhere."""
    target, state = dirs
    data = {"entries": [{
        "app_id": "test:fake-ls", "filename": "fake-ls-test.desktop",
        "content": ("[Desktop Entry]\nVersion=1.0\n"
                    "Name=Fake Ls Test (Excluded)\n"
                    "Exec=mullvad-exclude /bin/ls\nType=Application\n"
                    "Terminal=false\nIcon=application-x-executable\n"
                    "Categories=Utility;\n"
                    "X-Managed-By=met-desktop-manager\n")}]}
    res = manager.import_managed(data, target, state)
    assert res["wrote"] == ["fake-ls-test.desktop"]
    dest = target / "fake-ls-test.desktop"
    assert generator.is_managed_content(dest.read_text())
    ok, msg = manager.validate_desktop_file(dest)
    assert ok, msg
