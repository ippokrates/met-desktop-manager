"""Create/delete/sync .desktop files in the target applications dir.

Safety model:
  - Every file we create carries `X-Managed-By=met-desktop-manager`.
  - `state.json` maps app_id -> filename so deselect finds the file.
  - We NEVER touch files without the marker (pre-existing launchers like
    vesktop.desktop are safe), even if the name collides — we pick `-2`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Mapping

from . import generator
from .scanner import binary_exists, exec_key

TARGET_DIR = Path.home() / ".local" / "share" / "applications"
LEGACY_STATE_FILE = Path(__file__).resolve().parent.parent / "state.json"


def _default_state_file() -> Path:
    """Per-user state location, working for repo dev and pipx installs.

    Prefers the XDG data dir (~/.local/share/desktop-manager/state.json).
    Falls back to the repo-adjacent state.json only if it already holds
    real entries (so pre-existing installs don't orphan their files).
    """
    xdg_base = os.environ.get("XDG_DATA_HOME", "")
    base = Path(xdg_base) if xdg_base else Path.home() / ".local" / "share"
    xdg = base / "desktop-manager" / "state.json"
    if xdg.exists():
        return xdg
    try:
        if (LEGACY_STATE_FILE.exists()
                and json.loads(LEGACY_STATE_FILE.read_text()) not in ({}, [])):
            return LEGACY_STATE_FILE
    except (OSError, ValueError):
        pass
    return xdg


STATE_FILE = _default_state_file()


def load_state(state_file: Path | None = None) -> dict:
    state_file = state_file or STATE_FILE
    try:
        return json.loads(Path(state_file).read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict, state_file: Path | None = None) -> None:
    state_file = state_file or STATE_FILE
    p = Path(state_file)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    p.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def _is_managed_file(path: Path) -> bool:
    try:
        return generator.is_managed_content(path.read_text(errors="ignore"))
    except OSError:
        return False


def managed_exec_key(path: Path) -> str:
    """exec_key of a managed file's Exec= line ('' when unreadable)."""
    try:
        for line in Path(path).read_text(errors="ignore").splitlines():
            if line.startswith("Exec="):
                return exec_key(line[5:])
    except OSError:
        pass
    return ""


def _iter_apps(apps) -> list:
    if isinstance(apps, Mapping):
        return list(apps.values())
    return list(apps)


def find_orphaned(state: dict, selected: Mapping, target_dir,
                  discovered=None) -> dict:
    """State entries matching neither selection IDs nor selection binaries.

    Returns {app_id: {"filename", "binary", "vanished", "binary_exists"}}
    where vanished=True means the managed binary is absent from `discovered`
    (or discovery is unknown, i.e. discovered=None) — those must never be
    silently deleted. `binary_exists` is a real filesystem check of the
    managed Exec= target, so callers can tell a truly deleted program
    (safe to suggest removal) from one that is merely unscanned.
    """
    wanted = set(selected.keys())
    sel_keys = {exec_key(_app_fields(a)[1]) for a in _iter_apps(selected)}
    disc_keys = None
    if discovered is not None:
        disc_keys = {exec_key(_app_fields(a)[1]) for a in _iter_apps(discovered)}
    target_dir = Path(target_dir)
    orphaned = {}
    for app_id, filename in state.items():
        if app_id in wanted:
            continue
        candidate = target_dir / filename
        key = ""
        if candidate.is_file() and _is_managed_file(candidate):
            key = managed_exec_key(candidate)
        if key and key in sel_keys:
            continue  # same binary under a new ID: adoption, not removal
        vanished = disc_keys is None or key not in disc_keys
        orphaned[app_id] = {
            "filename": filename,
            "binary": key,
            "vanished": vanished,
            "binary_exists": binary_exists(key) if key else False,
        }
    return orphaned


def _app_fields(app) -> tuple[str, str, str]:
    """Accept DiscoveredApp or plain mapping -> (name, exec_path, icon)."""
    if isinstance(app, Mapping):
        return (
            str(app.get("name", "")),
            str(app.get("exec_path", "")),
            str(app.get("icon_hint", app.get("icon", ""))),
        )
    return (
        str(getattr(app, "name", "")),
        str(getattr(app, "exec_path", "")),
        str(getattr(app, "icon_hint", "") or getattr(app, "icon", "")),
    )


def create_desktop(app, target_dir: Path | None = None,
                   state_file: Path | None = None) -> Path:
    """Create (or refresh) the .desktop file for one app. Returns its path."""
    name, exec_path, icon = _app_fields(app)
    content = generator.build_desktop_content(name, exec_path, icon=icon)

    target_dir = Path(target_dir).expanduser() if target_dir else TARGET_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    state = load_state(state_file)
    app_id = app.get("id", name) if isinstance(app, Mapping) else (
        getattr(app, "id", name))

    # Reuse our previous file for this app if it still exists.
    existing = state.get(app_id)
    if existing and (target_dir / existing).is_file():
        dest = target_dir / existing
    else:
        dest = target_dir / generator.unique_filename(target_dir, name)

    dest.write_text(content)
    try:
        dest.chmod(0o644)
    except OSError:
        pass

    state[app_id] = dest.name
    save_state(state, state_file)
    _refresh_desktop_db(target_dir)
    return dest


def remove_desktop(app_id: str, target_dir: Path | None = None,
                   state_file: Path | None = None) -> bool:
    """Delete our managed file for app_id. Returns True if something was removed."""
    target_dir = Path(target_dir).expanduser() if target_dir else TARGET_DIR
    state = load_state(state_file)
    removed = False
    filename = state.pop(app_id, None)
    if filename and (target_dir / filename).is_file():
        if _is_managed_file(target_dir / filename):
            (target_dir / filename).unlink()
            removed = True
    save_state(state, state_file)
    if removed:
        _refresh_desktop_db(target_dir)
    return removed


def sync(selected: Mapping, target_dir: Path | None = None,
         state_file: Path | None = None, discovered=None,
         prune_vanished: bool = False) -> dict:
    """Reconcile target_dir with the selected apps.

    `selected`: mapping of app_id -> DiscoveredApp (or plain dict).
    `discovered`: full scan results (mapping or list); used to tell a
      genuine deselect (binary still around) from a vanished app.
    `prune_vanished`: when False (default), previously-managed apps whose
      binary vanished are KEPT and reported, never silently deleted.

    Returns {'created': [...], 'removed': [...], 'adopted': {old: new},
    'vanished': [{'app_id', 'filename', 'binary', 'binary_exists'}]}.
    `binary_exists` tells a truly deleted program (safe to suggest removal)
    from one that is merely unscanned.
    """
    target_dir = Path(target_dir).expanduser() if target_dir else TARGET_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(state_file)

    created, removed, vanished = [], [], []
    adopted = {}
    wanted = set(selected.keys())
    sel_by_key: dict[str, str] = {}
    for app_id, app in selected.items():
        key = exec_key(_app_fields(app)[1])
        if key and key not in sel_by_key:
            sel_by_key[key] = app_id

    # Phase 0: adopt entries whose binary is selected under a new ID
    # (e.g. file:vesktop -> desktop:vesktop after a discovery change).
    for app_id in [k for k in state if k not in wanted]:
        candidate = target_dir / state[app_id]
        if not (candidate.is_file() and _is_managed_file(candidate)):
            continue
        new_id = sel_by_key.get(managed_exec_key(candidate))
        if new_id and new_id not in state:
            state.pop(app_id)
            state[new_id] = candidate.name
            adopted[app_id] = new_id

    for app_id, app in selected.items():
        name, exec_path, icon = _app_fields(app)
        content = generator.build_desktop_content(name, exec_path, icon=icon)
        filename = state.get(app_id)
        dest = (target_dir / filename) if filename else None
        if dest and dest.is_file() and _is_managed_file(dest):
            if dest.read_text(errors="ignore") != content:
                dest.write_text(content)
                created.append(dest.name)
        else:
            dest = target_dir / generator.unique_filename(target_dir, name)
            dest.write_text(content)
            try:
                dest.chmod(0o644)
            except OSError:
                pass
            state[app_id] = dest.name
            created.append(dest.name)

    # Phase 2: removal candidates (post-adoption leftovers).
    for app_id, info in find_orphaned(state, selected, target_dir, discovered).items():
        filename = state.pop(app_id)
        candidate = target_dir / filename
        if not (candidate.is_file() and _is_managed_file(candidate)):
            continue  # nothing on disk to protect; just drop the stale key
        if info["vanished"] and not prune_vanished:
            state[app_id] = filename  # restore: kept, reported below
            vanished.append({"app_id": app_id, "filename": filename,
                             "binary": info["binary"],
                             "binary_exists": info.get("binary_exists", False)})
            continue
        candidate.unlink()
        removed.append(filename)

    # Orphan sweep: managed files on disk unknown to state.
    known_files = set(state.values())
    for child in target_dir.glob("*.desktop"):
        if child.name in known_files or not _is_managed_file(child):
            continue
        new_id = sel_by_key.get(managed_exec_key(child))
        if new_id and new_id not in state:
            state[new_id] = child.name  # adopt stray file, don't delete
            continue
        if prune_vanished:
            child.unlink()
            removed.append(child.name)
        else:
            key = managed_exec_key(child)
            vanished.append({"app_id": None, "filename": child.name,
                             "binary": key,
                             "binary_exists": binary_exists(key) if key else False})

    save_state(state, state_file)
    if created or removed:
        _refresh_desktop_db(target_dir)
    return {"created": created, "removed": removed,
            "adopted": adopted, "vanished": vanished}


def managed_files(target_dir: Path | None = None) -> list[Path]:
    """List all files in target_dir carrying our managed marker."""
    target_dir = Path(target_dir).expanduser() if target_dir else TARGET_DIR
    if not target_dir.is_dir():
        return []
    return [p for p in target_dir.glob("*.desktop") if _is_managed_file(p)]


def _refresh_desktop_db(target_dir: Path) -> None:
    updater = shutil.which("update-desktop-database")
    if updater is None:
        return
    try:
        subprocess.run([updater, str(target_dir)],
                       capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def validate_desktop_file(path: Path) -> tuple[bool, str]:
    """Run desktop-file-validate if available. Returns (ok, message)."""
    tool = shutil.which("desktop-file-validate")
    if tool is None:
        return True, "desktop-file-validate not installed, skipped"
    try:
        out = subprocess.run([tool, str(path)],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    ok = out.returncode == 0
    return ok, (out.stdout + out.stderr).strip() or "valid"
