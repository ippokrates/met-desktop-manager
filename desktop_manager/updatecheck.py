"""Update check: compare the installed version with GitHub main.

Stdlib only (urllib, tomllib, importlib.metadata, subprocess). Checks never
raise, so a mere update hint can never break a real run of the tool.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
import urllib.request
from importlib.metadata import PackageNotFoundError, version as pkg_version

PACKAGE = "met-desktop-manager"

RAW_URL = ("https://raw.githubusercontent.com/ippokrates/"
           "met-desktop-manager/main/pyproject.toml")


def latest_on_github() -> str:
    """Version string from the repo's pyproject.toml. '' on any failure."""
    try:
        with urllib.request.urlopen(RAW_URL, timeout=2) as r:
            text = r.read().decode()
        return str(tomllib.loads(text)["project"]["version"])
    except Exception:
        return ""


def installed() -> str:
    """Version of the installed package, '' when not installed (dev runs)."""
    try:
        return pkg_version(PACKAGE)
    except PackageNotFoundError:
        return ""


def _compare(a: str, b: str) -> int:
    """1 when a is newer than b, 0 when equal (or unparseable), -1 older.

    Numeric tuple comparison, so `0.10.0` counts as newer than `0.2.0`
    (plain text ordering would flip that one).
    """
    try:
        ka = tuple(int(x) for x in a.strip().split("."))
        kb = tuple(int(x) for x in b.strip().split("."))
    except ValueError:
        return 0
    return (ka > kb) - (ka < kb)


def _pipx_spec() -> str | None:
    """How pipx installed this app (`package_or_url`), None when it did not.

    Example return: 'git+https://github.com/ippokrates/met-desktop-manager.git'
    """
    try:
        out = subprocess.run(["pipx", "list", "--json"],
                             capture_output=True, text=True, timeout=10)
        main = (json.loads(out.stdout or "{}")
                .get("venvs", {})
                .get(PACKAGE, {})
                .get("metadata", {})
                .get("main_package", {}))
        return main.get("package_or_url")
    except Exception:
        return None


def _is_local(spec: str) -> bool:
    """True for a checkout path (`.`, `..`, `/x`, `file:`), which we must not touch.

    Reinstalling one of those from GitHub would silently replace a dev install.
    """
    return spec in (".", "..") or spec.startswith(("/", "./", "../", "~", "file:"))


def run_update() -> int:
    """met update: reinstall the pipx package from the source it came from."""
    if not shutil.which("pipx"):
        print("pipx not found. Install it, then run install.sh.")
        return 1
    spec = _pipx_spec()
    if not spec:
        print("Not installed via pipx. Run install.sh to install or update.")
        return 1
    if _is_local(spec):
        print(f"Installed from a local checkout ({spec}).")
        print("Update with: git pull && pipx reinstall met-desktop-manager")
        return 1
    mine, theirs = installed(), latest_on_github()
    if not theirs:
        print("Could not reach GitHub; try again later.")
        return 1
    if mine and _compare(theirs, mine) <= 0:
        print(f"Already up to date ({mine}).")
        return 0
    print(f"Updating {mine or 'installed copy'} -> {theirs} ...", flush=True)
    # Nothing may import from the package after this point: pipx replaces the
    # venv this process runs from. Printing is safe, imports are not.
    try:
        result = subprocess.run(["pipx", "reinstall", PACKAGE])
    except OSError as e:
        print(f"Could not run pipx: {e}")
        return 1
    if result.returncode != 0:
        print(f"Update failed (pipx exit {result.returncode}).")
        return result.returncode
    print(f"Updated to {theirs}. The next run of met uses it.")
    return 0


def manual_check() -> None:
    """met --check-updates: always fetches, always reports."""
    mine, theirs = installed(), latest_on_github()
    if not theirs:
        print("Could not reach GitHub; try again later.")
        return
    print(f"installed {mine or '(not installed via pipx)'}")
    print(f"github   {theirs}")
    if _compare(theirs, mine) == 1:
        print("-> update with: met update")


def auto_check() -> None:
    """Runs after interactive sessions: fetches GitHub, hints when newer.

    No cache, no interval: the tool runs rarely, so one small fetch per
    interactive session costs less than state to maintain. Scripted
    commands (--list, --sync --all) never call this, so their output
    stays clean. Silent on any failure.
    """
    try:
        theirs = latest_on_github()
        mine = installed()
        if theirs and mine and _compare(theirs, mine) == 1:
            print(f"note: update available: {theirs} (you have {mine}) "
                  "-> update with: met update")
    except Exception:
        pass
