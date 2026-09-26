"""Interactive CLI entry point."""
import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="desktop-manager",
        description="Manage .desktop files in ~/.local/share/applications",
    )
    p.add_argument("--list", action="store_true", help="List discovered apps")
    p.add_argument("--create", metavar="NAME", help="Create .desktop for app NAME")
    p.add_argument("--exec", dest="exec_path", help="Executable path for --create")
    p.add_argument("--icon", default="", help="Icon name/path for --create")
    p.add_argument("--sync", action="store_true", help="Sync selection non-interactively")
    p.add_argument("--all", action="store_true", help="With --sync: select all discovered apps")
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


def main() -> None:
    from . import manager
    from .scanner import group_by_directory, load_config, scan

    parser = build_parser()
    args = parser.parse_args()
    if args.list:
        apps = scan(load_config())
        if not apps:
            print("No apps discovered. Check apps.yaml paths.")
            return
        console = _group_console()
        for group_key, g_apps in group_by_directory(apps).items():
            _print_group_header(group_key, len(g_apps), console)
            for app in g_apps:
                icon = f" [{app.icon_hint}]" if app.icon_hint else ""
                print(f"    - {app.name} | {app.exec_path}{icon} ({app.source})")
        print(f"\n{len(apps)} app(s) discovered.")
        return
    if args.create:
        if not args.exec_path:
            parser.error("--create NAME requires --exec PATH")
        dest = manager.create_desktop(
            {"id": args.create.lower(), "name": args.create,
             "exec_path": args.exec_path, "icon": args.icon})
        ok, msg = manager.validate_desktop_file(dest)
        print(f"Created {dest} (valid={ok}: {msg})")
        return
    if args.sync:
        apps = scan(load_config())
        if args.all:
            selected = {a.id: a for a in apps}
        else:
            parser.error("--sync requires --all (interactive picker is the no-args mode)")
        result = manager.sync(selected, discovered={a.id: a for a in apps})
        print(f"Synced: {len(result['created'])} created/updated, "
              f"{len(result['removed'])} removed.")
        if result["vanished"]:
            print("Kept, no longer discovered (not deleted):")
            for v in result["vanished"]:
                print(f"  - {v['filename']} ({v['binary'] or 'unknown binary'})")
            sys.exit(1)
        return
    interactive()


def interactive(target_dir=None, state_file=None) -> None:
    """No-args mode: checkbox picker -> confirm -> sync -> summary.

    target_dir/state_file default to the real locations; tests pass tmp paths.
    """
    from pathlib import Path

    from . import manager
    from .scanner import load_config, scan

    target_dir = Path(target_dir).expanduser() if target_dir else manager.TARGET_DIR
    state_file = Path(state_file) if state_file else manager.STATE_FILE

    apps = scan(load_config())
    if not apps:
        print("No apps discovered. Check apps.yaml paths.")
        return
    state = manager.load_state(state_file)

    # Pre-tick by ID *or* by binary: an app selected under an old ID
    # (e.g. file:vesktop, now discovered as desktop:vesktop) stays ticked.
    preticked = _preticked_ids(apps, state, target_dir)

    if sys.stdin.isatty():
        picked = _picker_tty(apps, state, preticked)
    else:
        picked = _picker_plain(apps, state, preticked)
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
        print("No longer discovered, will be KEPT (not deleted):")
        for v in vanished:
            print(f"  - {v['filename']} ({v['binary'] or 'unknown binary'})")
    prune = False
    if sys.stdin.isatty():
        try:
            import questionary
            if not questionary.confirm("Apply these changes?", default=True).ask():
                print("Cancelled, nothing changed.")
                return
            if vanished and questionary.confirm(
                    "Also DELETE the kept files above?", default=False).ask():
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


def _picker_tty(apps, state, preticked: set[str] | None = None) -> set[str] | None:
    """questionary picker with substring filter rounds; accumulates picks."""
    try:
        import questionary
        from questionary import Choice
    except ImportError:
        return _picker_plain(apps, state, preticked)

    picked: set[str] = set(preticked) if preticked is not None else {
        a.id for a in apps if a.id in state}
    from .scanner import group_base_name, group_key_for_app
    base_for = {a.id: group_base_name(group_key_for_app(a)) for a in apps}
    while True:
        filt = questionary.text(
            "Filter apps (substring of name/path, Enter = show all):").ask()
        if filt is None:  # Ctrl-C
            return None
        filt = filt.strip().lower()
        candidates = [a for a in apps
                      if not filt or filt in f"{a.name} {a.exec_path}".lower()]
        if not candidates:
            print("No matches, try another filter.")
            continue
        # Grouped order: folder name, then app name (visual grouping only).
        candidates.sort(key=lambda a: (base_for.get(a.id, "").lower(),
                                       a.name.lower()))
        print(f"{len(candidates)} match(es). Space toggles, Enter confirms.")
        choices = [
            Choice(title=f"[{base_for.get(a.id, '?')}] {a.name}  ({a.exec_path})",
                   value=a.id, checked=(a.id in picked))
            for a in candidates
        ]
        answer = questionary.checkbox(
            "Select apps:", choices=choices).ask()
        if answer is None:  # Ctrl-C
            return None
        picked = (picked - {a.id for a in candidates}) | set(answer)
        more = questionary.confirm(
            f"{len(picked)} selected. Filter again to add more?",
            default=False).ask()
        if not more:
            return picked


def _picker_plain(apps, state, preticked: set[str] | None = None) -> set[str] | None:
    """Stdlib fallback for non-TTY (pipes/SSH): numbered ranges like 1,3,5-9.

    Visual grouping only: headers per directory, flat numbering underneath
    so `1,3,5-9` keeps working. Number -> app mapping follows the grouped
    order (folder name, then app name).
    """
    from .scanner import group_by_directory

    ticked = set(preticked) if preticked is not None else {
        a.id for a in apps if a.id in state}
    groups = group_by_directory(apps)
    ordered = [a for g_apps in groups.values() for a in g_apps]
    console = _group_console()
    n = 0
    for group_key, g_apps in groups.items():
        _print_group_header(group_key, len(g_apps), console)
        for a in g_apps:
            n += 1
            mark = "x" if a.id in ticked else " "
            print(f"    {n:4} [{mark}] {a.name} | {a.exec_path}")
    try:
        raw = input("Enter numbers/ranges (e.g. 1,3,5-9), empty = none: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
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
    return picked


def _print_summary(result: dict, manager, target_dir) -> None:
    created, removed = result["created"], result["removed"]
    adopted = result.get("adopted", {})
    vanished = result.get("vanished", [])
    if not created and not removed and not adopted and not vanished:
        print("Already in sync, nothing changed.")
        return
    for old_id, new_id in adopted.items():
        print(f"adopted: {old_id} -> {new_id} (same app, new discovery ID)")
    for v in vanished:
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
