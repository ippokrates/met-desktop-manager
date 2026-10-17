"""Resolve and launch managed apps (the `desktop-manager run <app-id>` entry point).

Why this exists: a self-updating AppImage replaces its file
(`OpenChamber-1.2.2.AppImage` -> `OpenChamber-1.2.3.AppImage`), so a
`.desktop` file whose `Exec=` names the old path rots. AppImage launchers
therefore point here instead:

    Exec=/home/u/.local/bin/desktop-manager run file:openchamber-1.2.2
    X-Met-Binary=/home/u/Downloads/OpenChamber-1.2.2.AppImage

At launch the router looks the app up in `state.json`, finds the newest
matching `.AppImage` when the recorded file is gone, heals the launcher and
the state (so the picker keeps the app ticked) and finally replaces itself
with the app, outside the VPN tunnel.

Newest wins by *modification time*, never by name: the updater wrote the new
file last, while name ordering has no universal answer (`1.9.0` sorts after
`1.10.2` as text; `2.2a` vs `2.2.1` is ambiguous).

Only AppImages are routed. Plain binaries, flatpak and snap launchers keep
their direct `Exec=`, so they still work if this tool is gone.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import generator
from .manager import STATE_FILE, _ensure_executable, _is_managed_file
from .manager import load_state, managed_exec_key, resolve_target_dir, save_state
from .scanner import is_appimage_path

# First version-looking token and everything after it: "OpenChamber-1.2.2" ->
# "OpenChamber". No digit in the name -> unchanged.
_VERSION_TAIL = re.compile(r"[._\- ]?\d.*$")
# Shortest prefix we accept for a search pattern, so `7zip-1.0` does not
# yield "*.AppImage" and match every AppImage in the folder.
_MIN_PREFIX = 3
# Replaces the id of a routed Exec= line ("...run <id>").
_EXEC_ID = re.compile(r"(\brun )\S+")


def strip_version(name: str) -> str:
    """Drop the version part of an app name.

    Example:
      "OpenChamber-1.2.2" -> "OpenChamber"
      "vesktop"           -> "vesktop"
      "7zip-1.0"          -> "7zip"      (digit-leading names cut later)
    """
    text = (name or "").strip()
    if not text:
        return text
    prefix = _VERSION_TAIL.sub("", text)
    if len(prefix) >= _MIN_PREFIX:
        return prefix
    # Names starting with a digit ("7zip-1.0"): cut at the separator instead.
    match = re.search(r"[._\- ]\d", text)
    if match and match.start() >= _MIN_PREFIX:
        return text[: match.start()]
    return text


def _natural_key(name: str) -> tuple[str, ...]:
    """Sort key comparing digit runs as numbers ("x9" < "x10").

    Digit runs are zero-padded into strings so every element stays a string
    and tuples of different shapes still compare without raising.
    """
    return tuple(f"{int(t):012d}" if t.isdigit() else t
                 for t in re.split(r"(\d+)", name))


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def find_newest_appimage(old_path: Path | str) -> str:
    """Newest `.AppImage` next to `old_path` sharing its version-stripped name.

    Returns "" when nothing matches. Non-AppImage leftovers (a `.part`
    download) are ignored, and `old_path` itself is never returned.
    """
    old = Path(old_path)
    prefix = strip_version(old.stem)
    try:
        candidates = [e for e in old.parent.iterdir()
                      if e != old and e.is_file()
                      and e.name.startswith(prefix) and is_appimage_path(e)]
    except OSError:
        return ""
    if not candidates:
        return ""
    newest = max(candidates, key=lambda p: (_mtime(p), _natural_key(p.name)))
    return str(newest)


def met_cmd() -> list[str]:
    """Command words that invoke this tool (head of every routed Exec line).

    Prefers the command running right now, so a launcher created from a dev
    checkout calls the checkout and one created by a pipx install calls the
    pipx shim (`pipx reinstall` then keeps working from the same path).

    Example:
      `met` on PATH -> ["/home/u/.local/bin/met"]
    """
    argv0 = (sys.argv[0] if sys.argv else "") or ""
    if os.path.basename(argv0) in ("desktop-manager", "met"):
        base = os.path.basename(argv0)
        found = argv0 if Path(argv0).is_file() else shutil.which(base)
        if found:
            return [os.path.abspath(found)]
    for name in ("desktop-manager", "met"):
        found = shutil.which(name)
        if found:
            return [found]
    return [sys.executable, "-m", "desktop_manager"]


def _id_stem(app_id: str) -> str:
    """`file:openchamber-1.2.2` -> `openchamber-1.2.2`."""
    return (app_id or "").split(":", 1)[-1]


def _launcher_for(app_id: str, state: dict, target_dir) -> tuple[str, Path | None]:
    """(effective app id, launcher path) for app_id.

    Falls back to a version-stripped id match, so a launcher whose Exec
    still names an older version (half-healed file) keeps resolving.
    """
    filename = state.get(app_id)
    if filename:
        return app_id, Path(target_dir) / filename
    want = strip_version(_id_stem(app_id)).lower()
    if want:
        for key, name in state.items():
            if strip_version(_id_stem(key)).lower() == want:
                return key, Path(target_dir) / name
    return app_id, None


def _recorded_binary(launcher: Path) -> str:
    """The app file a launcher points at: X-Met-Binary, else its Exec= target."""
    try:
        text = launcher.read_text(errors="ignore")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith("X-Met-Binary="):
            return line.split("=", 1)[1].strip()
    return managed_exec_key(launcher)


def _rewrite_launcher(launcher: Path, old_id: str, new_id: str,
                      new_binary: str) -> bool:
    """Point a managed launcher at new_id / new_binary. Returns True on change.

    A launcher written before routing existed (direct `Exec=` path) is
    converted to the routed form, which is also how an update that broke it
    gets repaired. Unreadable/write-protected files change nothing.
    """
    try:
        original = launcher.read_text(errors="ignore")
    except OSError:
        return False
    lines = original.splitlines()
    routed = any(line.startswith("X-Met-Binary=") for line in lines)
    out = []
    for line in lines:
        if line.startswith("Exec="):
            if routed:
                out.append(_EXEC_ID.sub(rf"\g<1>{new_id}", line, count=1))
            else:
                out.append("Exec=" + generator.routed_exec(met_cmd(), new_id))
        elif line.startswith("X-Met-Binary="):
            out.append(f"X-Met-Binary={new_binary}")
        else:
            out.append(line)
    if not routed:
        out.append(f"X-Met-Binary={new_binary}")
    new_text = "\n".join(out) + "\n"
    if new_text == original:
        return False
    try:
        launcher.write_text(new_text)
    except OSError:
        return False
    return True


def _heal_state(eff_id: str, new_id: str, state: dict, state_file) -> None:
    """Rename the state key to new_id so the picker stays ticked."""
    if new_id == eff_id or eff_id not in state or new_id in state:
        return
    state[new_id] = state.pop(eff_id)
    save_state(state, state_file)


def resolve(app_id: str, state: dict | None = None, state_file=None,
            target_dir=None, convert: bool = False) -> tuple[str, str, str]:
    """Resolve an app id to a launchable binary, healing a replaced AppImage.

    Returns (binary, effective app id, status) with status one of:
      "ok"      binary exists, nothing changed
      "routed"  a working AppImage launcher was rewritten to the routed form
      "healed"  a newer AppImage was adopted; launcher + state updated
      "missing" the app is gone (no matching AppImage found)
      "unknown" no managed launcher is tracked under this id

    `convert=True` (used by the picker/`--sync` heal, never on the launch
    path) also routes AppImage launchers that still work, so an update can
    no longer break them in the first place.
    """
    state_file = Path(state_file) if state_file else STATE_FILE
    target_dir = Path(target_dir) if target_dir else resolve_target_dir()
    if state is None:
        state = load_state(state_file)
    eff_id, launcher = _launcher_for(app_id, state, target_dir)
    if launcher is None or not launcher.is_file():
        return "", eff_id, "unknown"
    if not _is_managed_file(launcher):
        # Foreign file (hand-made, or ours was replaced): never touch it.
        return "", eff_id, "unknown"
    binary = _recorded_binary(launcher)
    if binary and Path(binary).is_file():
        if convert and is_appimage_path(binary):
            if _rewrite_launcher(launcher, eff_id, eff_id, binary):
                return binary, eff_id, "routed"
        return binary, eff_id, "ok"
    if not binary or not is_appimage_path(binary):
        return "", eff_id, "missing"
    new_binary = find_newest_appimage(binary)
    if not new_binary:
        return "", eff_id, "missing"
    new_id = f"file:{Path(new_binary).stem.lower()}"
    _rewrite_launcher(launcher, eff_id, new_id, new_binary)
    _heal_state(eff_id, new_id, state, state_file)
    return new_binary, new_id, "healed"


def heal_all(target_dir=None, state_file=None) -> list[str]:
    """Re-link replaced AppImage launchers and route the working ones.

    Run by the picker and by `--sync`, so a launcher broken by an update is
    repaired even when the menu icon was never clicked since, and a working
    AppImage launcher is made update-proof before it can break. Returns one
    message per change.
    """
    state_file = Path(state_file) if state_file else STATE_FILE
    target_dir = Path(target_dir) if target_dir else resolve_target_dir()
    state = load_state(state_file)
    messages = []
    for app_id in list(state):
        binary, eff_id, status = resolve(app_id, state, state_file, target_dir,
                                         convert=True)
        if status == "healed":
            messages.append(f"re-linked {app_id} -> {Path(binary).name}")
        elif status == "routed":
            messages.append(f"routed {app_id} (survives its own updates)")
    return messages


def _notify(message: str) -> None:
    """Desktop notification for launches with no terminal attached."""
    if not shutil.which("notify-send"):
        return
    try:
        subprocess.run(["notify-send", "-a", "desktop-manager", message],
                       check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def run(argv: list[str]) -> int:
    """`desktop-manager run [--resolve] <app-id> [args...]`: launch a managed app.

    Everything after the id is forwarded to the app, so `%U`-style field
    codes from the .desktop file keep working.
    """
    args = list(argv)
    path_only = bool(args and args[0] in ("--resolve", "-r"))
    if path_only:
        args = args[1:]
    if not args:
        print("usage: desktop-manager run <app-id> [args...]", file=sys.stderr)
        return 2
    app_id, *extra = args
    binary, eff_id, status = resolve(app_id)
    if path_only:
        if binary:
            print(binary)
            return 0
        print(f"unresolved ({status}): {app_id}", file=sys.stderr)
        return 1
    if status == "unknown":
        print(f"unknown app: {app_id}", file=sys.stderr)
        return 1
    if not binary:
        message = (f"{eff_id}: no matching .AppImage found "
                   f"(renamed or removed since the launcher was created?)")
        print(message, file=sys.stderr)
        _notify(message)
        return 1
    _ensure_executable(binary)
    cmd = [generator.MULLVAD_PREFIX, binary, *extra]
    try:
        os.execvp(cmd[0], cmd)
    except OSError as e:
        message = f"{binary}: cannot launch ({e})"
        print(message, file=sys.stderr)
        _notify(message)
        return 1
    return 0
