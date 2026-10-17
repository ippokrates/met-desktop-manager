"""Tests for desktop_manager.generator (pure functions, no I/O)."""
import pytest

from desktop_manager import generator as g


def test_sanitize_basic():
    assert g.sanitize_filename("Zen Browser") == "zen-browser.desktop"
    assert g.sanitize_filename("Vesktop") == "vesktop.desktop"
    assert g.sanitize_filename("Mullvad VPN") == "mullvad-vpn.desktop"


def test_sanitize_garbage_falls_back():
    assert g.sanitize_filename("  ***  ") == "app.desktop"
    assert g.sanitize_filename("") == "app.desktop"


def test_sanitize_caps_length():
    assert len(g.sanitize_filename("a" * 200)) <= len(".desktop") + 64


def test_quote_plain_path_untouched():
    assert g.quote_exec("/opt/Vesktop/vesktop") == "/opt/Vesktop/vesktop"


def test_quote_path_with_spaces():
    assert g.quote_exec("/home/user/My Apps/run me") == '"/home/user/My Apps/run me"'


def test_quote_multiword_commands_per_word():
    assert g.quote_exec("flatpak run org.signal.Signal") == "flatpak run org.signal.Signal"
    assert g.quote_exec("mullvad-exclude /home/user/Downloads/zen/zen-bin") == \
        "mullvad-exclude /home/user/Downloads/zen/zen-bin"


def test_quote_path_with_spaces_after_prefix(tmp_path):
    target = tmp_path / "Mullvad VPN" / "mullvad-gui"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\x7fELF" + b"\x00" * 60)
    out = g.quote_exec(f"mullvad-exclude {target}")
    assert out == f'mullvad-exclude "{target}"'


def test_quote_already_quoted_passthrough():
    assert g.quote_exec('"/already quoted"') == '"/already quoted"'


def test_build_content_excluded_by_default():
    c = g.build_desktop_content("Zen Bin", "/home/user/Downloads/zen/zen-bin")
    assert "Name=Zen Bin (Excluded)" in c
    assert "Exec=mullvad-exclude /home/user/Downloads/zen/zen-bin" in c
    assert "Type=Application" in c
    assert "X-Managed-By=met-desktop-manager" in c
    assert c.endswith("\n")
    assert g.is_managed_content(c)


def test_build_content_idempotent():
    once = g.build_desktop_content("Zen Bin", "/opt/Vesktop/vesktop")
    name_line = next(l for l in once.splitlines() if l.startswith("Name="))
    exec_line = next(l for l in once.splitlines() if l.startswith("Exec="))
    twice = g.build_desktop_content(
        name_line.split("=", 1)[1], exec_line.split("=", 1)[1])
    assert twice == once


def test_build_content_opt_out():
    c = g.build_desktop_content("Plain", "/bin/ls", exclude=False)
    assert "\nName=Plain\n" in c
    assert "\nExec=/bin/ls\n" in c


def test_build_content_requires_fields():
    with pytest.raises(ValueError):
        g.build_desktop_content("", "/bin/ls")
    with pytest.raises(ValueError):
        g.build_desktop_content("X", "")


def test_build_content_routed_appimage():
    """AppImage launchers call the router and record the real file."""
    c = g.build_desktop_content(
        "OpenChamber (Excluded)", "/home/u/Downloads/OpenChamber-1.2.2.AppImage",
        route_cmd=["/usr/local/bin/desktop-manager"],
        route_id="file:openchamber-1.2.2",
        met_binary="/home/u/Downloads/OpenChamber-1.2.2.AppImage")
    assert "Exec=/usr/local/bin/desktop-manager run file:openchamber-1.2.2" in c
    assert "mullvad-exclude" not in c  # the router applies it at launch time
    assert "X-Met-Binary=/home/u/Downloads/OpenChamber-1.2.2.AppImage" in c
    assert "X-Managed-By=met-desktop-manager" in c


def test_build_content_routed_quotes_router_path_with_spaces():
    c = g.build_desktop_content(
        "App", "/home/u/Downloads/app-1.0.AppImage",
        route_cmd=["/opt/My Tools/desktop-manager"],
        route_id="file:app-1.0",
        met_binary="/home/u/Downloads/app-1.0.AppImage")
    assert 'Exec="/opt/My Tools/desktop-manager" run file:app-1.0' in c


def test_build_content_routed_needs_both_parts():
    """Half-set routing falls back to today's direct Exec."""
    c = g.build_desktop_content(
        "App", "/home/u/Downloads/app-1.0.AppImage",
        route_cmd=["/usr/bin/met"], route_id="", met_binary="/x/App.AppImage")
    assert "Exec=mullvad-exclude /home/u/Downloads/app-1.0.AppImage" in c
    assert "X-Met-Binary=/x/App.AppImage" in c


def test_build_content_plain_stays_plain():
    c = g.build_desktop_content("Zen Bin", "/home/user/Downloads/zen/zen-bin")
    assert "X-Met-Binary" not in c
    assert "Exec=mullvad-exclude /home/user/Downloads/zen/zen-bin" in c


def test_routed_exec_helper():
    assert g.routed_exec(["/usr/bin/met"], "file:a-1.0") == "/usr/bin/met run file:a-1.0"
    assert g.routed_exec(["/opt/My Tools/met"], "file:a") == '"/opt/My Tools/met" run file:a'


def test_unique_filename_never_overwrites(tmp_path):
    (tmp_path / "zen-bin.desktop").write_text("existing")
    assert g.unique_filename(tmp_path, "Zen Bin") == "zen-bin-2.desktop"
    assert g.unique_filename(tmp_path, "Fresh") == "fresh.desktop"
