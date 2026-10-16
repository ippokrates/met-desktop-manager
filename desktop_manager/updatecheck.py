"""Update check: compare the installed version with GitHub main.

Stdlib only (urllib, tomllib, importlib.metadata). Never raises, so a mere
update hint can never break a real run of the tool.
"""
from __future__ import annotations

import tomllib
import urllib.request
from importlib.metadata import PackageNotFoundError, version as pkg_version

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
        return pkg_version("met-desktop-manager")
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


def manual_check() -> None:
    """met --check-updates: always fetches, always reports."""
    mine, theirs = installed(), latest_on_github()
    if not theirs:
        print("Could not reach GitHub; try again later.")
        return
    print(f"installed {mine or '(not installed via pipx)'}")
    print(f"github   {theirs}")
    if _compare(theirs, mine) == 1:
        print("-> run install.sh (or: pipx reinstall met-desktop-manager) to update")


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
                  "-> run install.sh to update")
    except Exception:
        pass
