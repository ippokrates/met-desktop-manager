"""Interactive CLI entry point."""
import argparse
import os
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=os.path.basename(sys.argv[0]) or "desktop-manager",
        description="Manage .desktop files in ~/.local/share/applications",
    )
    p.add_argument("--list", action="store_true", help="List discovered apps")
    p.add_argument("--create", metavar="NAME", help="Create .desktop for app NAME")
    p.add_argument("--exec", dest="exec_path", help="Executable path for --create")
    p.add_argument("--icon", default="", help="Icon name/path for --create")
    p.add_argument("--sync", action="store_true", help="Sync selection non-interactively")
    p.add_argument("--all", action="store_true", help="With --sync: select all discovered apps")
    p.add_argument("--show-system", action="store_true",
                   help="Include hidden system launchers for this run")
    p.add_argument("--hide-system", action="store_true",
                   help="Hide system launchers (default, overrides --show-system off)")
    return p


def _group_console():
    """Rich console for headers, or None when rich is missing."""
    try:
        from rich.console import Console
        return Console()
    except ImportError:
        return None


def _print_group_header(group_key: str, count: int, console=None) -> None:
    """One-line header: bold folder name + dimmed full path + count.

    Example: `  Vesktop  /opt/Vesktop  (1 app)`
    Plain print fallback when rich is unavailable (pipes/SSH).
    """
    from .scanner import group_base_name, short_group_path

    base = group_base_name(group_key)
    short = short_group_path(group_key)
    noun = "app" if count == 1 else "apps"
    if console is None:
        print(f"  {base}  {short}  ({count} {noun})")
        return
    try:
        from rich.markup import escape
        console.print(f"  [bold]{escape(base)}[/bold] "
                      f"[dim]{escape(short)} ({count} {noun})[/dim]")
    except Exception:
        print(f"  {base}  {short}  ({count} {noun})")


def _split_vanished(vanished: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split kept entries into broken vs still-on-disk.

    broken = binary truly missing (safe to suggest delete).
    kept = binary still exists (likely a config/scan gap).
    Entries without the new `binary_exists` flag default to kept (safe).
    """
    broken = [v for v in vanished if not v.get("binary_exists", True)]
    kept = [v for v in vanished if v.get("binary_exists", True)]
    return broken, kept


def _print_vanished(vanished: list[dict], kept_label: str) -> None:
    broken, kept = _split_vanished(vanished)
    if broken:
        print("Broken — program missing, safe to delete:")
        for v in broken:
            print(f"  - {v['filename']} ({v['binary'] or 'unknown binary'})")
    if kept:
        print(kept_label)
        for v in kept:
            print(f"  - {v['filename']} ({v['binary'] or 'unknown binary'})")


def _needs_chmod(app) -> bool:
    """True when an app is an .AppImage found without the exec bit."""
    if isinstance(app, dict):
        return bool(app.get("needs_chmod", False))
    return bool(getattr(app, "needs_chmod", False))


def _chmod_tag(app) -> str:
    return " [needs chmod +x]" if _needs_chmod(app) else ""


def _print_made_executable(result: dict) -> None:
    for fixed in result.get("made_executable", []) or []:
        print(f"made executable: {fixed}")


def _resolve_show_system(cfg: dict, override: bool | None) -> tuple[dict, bool]:
    """Apply --show/--hide-system override to a config copy.

    Returns (effective_cfg, showing). Single-arg scan() calls keep working
    with existing test mocks.
    """
    cfg = dict(cfg or {})
    sys_cfg = dict(cfg.get("system_apps", {}) or {})
    if override is True:
        sys_cfg["hide"] = False
    elif override is False:
        sys_cfg["hide"] = True
    cfg["system_apps"] = sys_cfg
    showing = not bool(sys_cfg.get("hide", True))
    return cfg, showing


def _rescan(cfg: dict, show: bool) -> list:
    """Rescan with system apps shown/hidden (single-arg scan for mock compat)."""
    from .scanner import scan as _scan
    new_cfg = dict(cfg or {})
    sys_cfg = dict(new_cfg.get("system_apps", {}) or {})
    sys_cfg["hide"] = not show
    new_cfg["system_apps"] = sys_cfg
    return _scan(new_cfg)


def _try_rescan(cfg: dict, show: bool) -> list | None:
    """Rescan for a system-apps toggle. Returns the new app list, or None.

    None means the rescan failed. Callers must then keep the current list
    AND the current toggle, so the two never disagree, and must not print a
    "shown (N total)" line for a list that never changed.
    """
    if cfg is None:
        return None
    try:
        return _rescan(cfg, show)
    except Exception as e:
        print(f"Rescan failed, list unchanged: {e}")
        return None


def main() -> None:
    from . import manager
    from .scanner import group_by_directory, load_config, scan

    parser = build_parser()
    args = parser.parse_args()
    override = True if args.show_system else (False if args.hide_system else None)
    if args.list:
        cfg, showing = _resolve_show_system(load_config(), override)
        apps = scan(cfg)
        if not apps:
            print("No apps discovered. Check apps.yaml paths.")
            return
        console = _group_console()
        for group_key, g_apps in group_by_directory(apps).items():
            _print_group_header(group_key, len(g_apps), console)
            for app in g_apps:
                icon = f" [{app.icon_hint}]" if app.icon_hint else ""
                print(f"    - {app.name}{_chmod_tag(app)} | "
                      f"{app.exec_path}{icon} ({app.source})")
        print(f"\n{len(apps)} app(s) discovered.")
        return
    if args.create:
        if not args.exec_path:
            parser.error("--create NAME requires --exec PATH")
        fixed = manager._ensure_executable(args.exec_path)
        dest = manager.create_desktop(
            {"id": args.create.lower(), "name": args.create,
             "exec_path": args.exec_path, "icon": args.icon})
        ok, msg = manager.validate_desktop_file(dest)
        print(f"Created {dest} (valid={ok}: {msg})")
        if fixed:
            print(f"made executable: {fixed}")
        return
    if args.sync:
        cfg, _showing = _resolve_show_system(load_config(), override)
        apps = scan(cfg)
        if args.all:
            selected = {a.id: a for a in apps}
        else:
            parser.error("--sync requires --all (interactive picker is the no-args mode)")
        result = manager.sync(selected, discovered={a.id: a for a in apps})
        print(f"Synced: {len(result['created'])} created/updated, "
              f"{len(result['removed'])} removed.")
        _print_made_executable(result)
        if result["vanished"]:
            _print_vanished(result["vanished"],
                            "Kept, no longer discovered (not deleted):")
            sys.exit(1)
        return
    interactive(show_system=override)


def interactive(target_dir=None, state_file=None, show_system=None) -> None:
    """No-args mode: checkbox picker -> confirm -> sync -> summary.

    target_dir/state_file default to the real locations; tests pass tmp paths.
    show_system True/False forces system launchers shown/hidden (CLI flags);
    None follows apps.yaml.
    """
    from pathlib import Path

    from . import manager
    from .scanner import load_config, scan

    target_dir = manager.resolve_target_dir(target_dir)
    state_file = Path(state_file) if state_file else manager.STATE_FILE

    cfg, showing = _resolve_show_system(load_config(), show_system)
    apps = scan(cfg)
    if not apps:
        print("No apps discovered. Check apps.yaml paths.")
        return
    state = manager.load_state(state_file)

    # Pre-tick by ID *or* by binary: an app selected under an old ID
    # (e.g. file:vesktop, now discovered as desktop:vesktop) stays ticked.
    preticked = _preticked_ids(apps, state, target_dir)

    if sys.stdin.isatty():
        holder: dict = {}
        picked = _picker_tty(apps, state, preticked, cfg, showing, holder)
        apps = holder.get("apps", apps)
    else:
        holder = {}
        picked = _picker_plain(apps, state, preticked, cfg, showing, holder)
        apps = holder.get("apps", apps)
    if picked is None:
        print("Cancelled, nothing changed.")
        return

    selected = {a.id: a for a in apps if a.id in picked}
    new = [i for i in picked if i not in preticked]
    orphans = manager.find_orphaned(state, selected, target_dir,
                                    {a.id: a for a in apps})
    doomed = [o for o in orphans.values() if not o["vanished"]]
    vanished = [o for o in orphans.values() if o["vanished"]]
    print(f"Selected {len(selected)} app(s): {len(new)} new, "
          f"{len(doomed)} to remove."
          + (f" {len(vanished)} previously-managed no longer discovered"
             f" (kept, see below)." if vanished else ""))
    if vanished:
        _print_vanished(vanished,
                        "No longer discovered, will be KEPT (not deleted):")
    prune = False
    if sys.stdin.isatty():
        try:
            import questionary
            if not questionary.confirm("Apply these changes?", default=True).ask():
                print("Cancelled, nothing changed.")
                return
            if vanished and questionary.confirm(
                    "Also DELETE the broken/kept files above?", default=False).ask():
                prune = True
        except ImportError:
            pass

    result = manager.sync(selected, target_dir, state_file,
                          discovered={a.id: a for a in apps},
                          prune_vanished=prune)
    _print_summary(result, manager, target_dir)


def _preticked_ids(apps, state, target_dir) -> set[str]:
    """IDs ticked at picker start: state IDs plus binary matches.

    Covers selections made under a previous discovery ID scheme.
    """
    from . import manager
    from .scanner import exec_key

    ticked = {a.id for a in apps if a.id in state}
    managed_keys = set()
    for filename in state.values():
        key = manager.managed_exec_key(target_dir / filename)
        if key:
            managed_keys.add(key)
    for path in manager.managed_files(target_dir):
        key = manager.managed_exec_key(path)
        if key:
            managed_keys.add(key)
    for a in apps:
        if exec_key(a.exec_path) in managed_keys:
            ticked.add(a.id)
    return ticked


TOGGLE_SYSTEM_VALUE = "__toggle_system__"


def _picker_tty(apps, state, preticked: set[str] | None = None,
                cfg: dict | None = None, show_system: bool = False,
                _final: dict | None = None) -> set[str] | None:
    """questionary picker with substring filter rounds; accumulates picks.

    First row is a system-apps switch (Separator above/below it, `s`
    shortcut). Picking it flips the list and redraws; picks are kept.
    Typing `!sys` in the filter box does the same. `_final["apps"]`
    receives the list backing the returned picks (after toggles).
    """
    try:
        import questionary
        from questionary import Choice, Separator
    except ImportError:
        return _picker_plain(apps, state, preticked, cfg, show_system, _final)

    picked: set[str] = set(preticked) if preticked is not None else {
        a.id for a in apps if a.id in state}
    from .scanner import group_base_name, group_key_for_app
    cur_apps = list(apps)
    cur_show = show_system

    def _rebuild_base():
        return {a.id: group_base_name(group_key_for_app(a)) for a in cur_apps}

    base_for = _rebuild_base()
    while True:
        filt = questionary.text(
            "Filter apps (substring of name/path, Enter = show all):").ask()
        if filt is None:  # Ctrl-C
            return None
        filt = filt.strip()
        if filt.lower() in ("!sys", "!system") and cfg is not None:
            new_apps = _try_rescan(cfg, not cur_show)
            if new_apps is None:
                continue  # failed: keep old list and old toggle
            cur_show = not cur_show
            cur_apps = new_apps
            base_for = _rebuild_base()
            print(f"System apps {'shown' if cur_show else 'hidden'} "
                  f"({len(cur_apps)} total).")
            continue
        low = filt.lower()
        candidates = [a for a in cur_apps
                      if not low or low in f"{a.name} {a.exec_path}".lower()]
        if not candidates:
            print("No matches, try another filter.")
            continue
        # Grouped order: folder name, then app name (visual grouping only).
        candidates.sort(key=lambda a: (base_for.get(a.id, "").lower(),
                                       a.name.lower()))
        print(f"{len(candidates)} match(es). Space toggles, Enter confirms.")
        toggle_label = (f"SHOW SYSTEM APPS: {'ON' if cur_show else 'OFF'}"
                        f"  ({len(cur_apps)} shown)")
        choices = [
            Choice(title=f">>> {toggle_label} <<< (Space to flip, Enter applies)",
                   value=TOGGLE_SYSTEM_VALUE,
                   description="toggle hidden settings and tools"),
            Separator(),
        ]
        choices += [
            Choice(title=f"[{base_for.get(a.id, '?')}] {a.name}{_chmod_tag(a)}  "
                         f"({a.exec_path})",
                   value=a.id, checked=(a.id in picked))
            for a in candidates
        ]
        answer = questionary.checkbox(
            "Select apps:", choices=choices,
            instruction="(Space on first row toggles system apps)").ask()
        if answer is None:  # Ctrl-C
            return None
        if TOGGLE_SYSTEM_VALUE in answer:
            picked = ((picked - {a.id for a in candidates})
                      | (set(answer) - {TOGGLE_SYSTEM_VALUE}))
            new_apps = _try_rescan(cfg, not cur_show)
            if new_apps is not None:
                cur_show = not cur_show
                cur_apps = new_apps
                base_for = _rebuild_base()
                print(f"System apps {'shown' if cur_show else 'hidden'} "
                      f"({len(cur_apps)} total).")
            continue
        picked = (picked - {a.id for a in candidates}) | set(answer)
        more = questionary.confirm(
            f"{len(picked)} selected. Filter again to add more?",
            default=False).ask()
        if not more:
            if _final is not None:
                _final["apps"] = cur_apps
            return picked


def _picker_plain(apps, state, preticked: set[str] | None = None,
                  cfg: dict | None = None, show_system: bool = False,
                  _final: dict | None = None) -> set[str] | None:
    """Stdlib fallback for non-TTY (pipes/SSH): numbered ranges like 1,3,5-9.

    Visual grouping only: headers per directory, flat numbering underneath
    so `1,3,5-9` keeps working. Number -> app mapping follows the grouped
    order (folder name, then app name). Type `s` or `!sys` to flip system
    apps when cfg is given (loops and redraws).
    """
    from .scanner import group_by_directory

    ticked = set(preticked) if preticked is not None else {
        a.id for a in apps if a.id in state}
    cur_apps = list(apps)
    cur_show = show_system
    while True:
        groups = group_by_directory(cur_apps)
        ordered = [a for g_apps in groups.values() for a in g_apps]
        console = _group_console()
        n = 0
        for group_key, g_apps in groups.items():
            _print_group_header(group_key, len(g_apps), console)
            for a in g_apps:
                n += 1
                mark = "x" if a.id in ticked else " "
                print(f"    {n:4} [{mark}] {a.name}{_chmod_tag(a)} | {a.exec_path}")
        if cfg is not None:
            print(f"System apps: {'shown' if cur_show else 'hidden'} "
                  f"({len(cur_apps)} shown). Type s or !sys to toggle.")
        try:
            raw = input("Enter numbers/ranges (e.g. 1,3,5-9), empty = none: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if cfg is not None and raw.strip().lower() in ("s", "!sys", "!system"):
            new_apps = _try_rescan(cfg, not cur_show)
            if new_apps is not None:
                cur_show = not cur_show
                cur_apps = new_apps
            continue
        picked: set[str] = set()
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                try:
                    lo, hi = part.split("-", 1)
                    for num in range(int(lo), int(hi) + 1):
                        if 1 <= num <= len(ordered):
                            picked.add(ordered[num - 1].id)
                except ValueError:
                    print(f"Ignoring invalid range: {part}")
            else:
                try:
                    num = int(part)
                    if 1 <= num <= len(ordered):
                        picked.add(ordered[num - 1].id)
                    else:
                        print(f"Ignoring out-of-range: {part}")
                except ValueError:
                    print(f"Ignoring invalid entry: {part}")
        if _final is not None:
            _final["apps"] = cur_apps
        return picked


def _print_summary(result: dict, manager, target_dir) -> None:
    created, removed = result["created"], result["removed"]
    adopted = result.get("adopted", {})
    vanished = result.get("vanished", [])
    _print_made_executable(result)
    if not created and not removed and not adopted and not vanished:
        print("Already in sync, nothing changed.")
        return
    for old_id, new_id in adopted.items():
        print(f"adopted: {old_id} -> {new_id} (same app, new discovery ID)")
    broken, kept = _split_vanished(vanished)
    for v in broken:
        print(f"broken (program missing, safe to delete): {v['filename']}"
              f" ({v['binary'] or 'unknown binary'})")
    for v in kept:
        print(f"kept (no longer discovered, not deleted): {v['filename']}"
              f" ({v['binary'] or 'unknown binary'})")
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        table = Table(title="Desktop files synced")
        table.add_column("Action")
        table.add_column("File")
        table.add_column("Valid")
        for f in created:
            ok, msg = manager.validate_desktop_file(
                target_dir / f)
            table.add_row("created/updated", f,
                          "yes" if ok else f"NO: {msg}")
        for f in removed:
            table.add_row("removed", f, "-")
        console.print(table)
    except ImportError:
        for f in created:
            print(f"created/updated: {f}")
        for f in removed:
            print(f"removed: {f}")


if __name__ == "__main__":
    main()
