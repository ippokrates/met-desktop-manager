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


def main() -> None:
    from . import manager
    from .scanner import load_config, scan

    parser = build_parser()
    args = parser.parse_args()
    if args.list:
        apps = scan(load_config())
        if not apps:
            print("No apps discovered. Check apps.yaml paths.")
            return
        for app in apps:
            icon = f" [{app.icon_hint}]" if app.icon_hint else ""
            print(f"- {app.name} | {app.exec_path}{icon} ({app.source})")
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
        result = manager.sync(selected)
        print(f"Synced: {len(result['created'])} created/updated, "
              f"{len(result['removed'])} removed.")
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

    if sys.stdin.isatty():
        picked = _picker_tty(apps, state)
    else:
        picked = _picker_plain(apps, state)
    if picked is None:
        print("Cancelled, nothing changed.")
        return

    selected = {a.id: a for a in apps if a.id in picked}
    new = [i for i in picked if i not in state]
    gone = [i for i in state if i not in picked]
    print(f"Selected {len(selected)} app(s): {len(new)} new, {len(gone)} to remove.")
    if sys.stdin.isatty():
        try:
            import questionary
            if not questionary.confirm("Apply these changes?", default=True).ask():
                print("Cancelled, nothing changed.")
                return
        except ImportError:
            pass

    result = manager.sync(selected, target_dir, state_file)
    _print_summary(result, manager, target_dir)


def _picker_tty(apps, state) -> set[str] | None:
    """questionary picker with substring filter rounds; accumulates picks."""
    try:
        import questionary
        from questionary import Choice
    except ImportError:
        return _picker_plain(apps, state)

    picked: set[str] = {a.id for a in apps if a.id in state}
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
        print(f"{len(candidates)} match(es). Space toggles, Enter confirms.")
        choices = [
            Choice(title=f"{a.name}  ({a.exec_path})",
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


def _picker_plain(apps, state) -> set[str] | None:
    """Stdlib fallback for non-TTY (pipes/SSH): numbered ranges like 1,3,5-9."""
    for i, a in enumerate(apps, 1):
        mark = "x" if a.id in state else " "
        print(f"{i:4} [{mark}] {a.name} | {a.exec_path}")
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
                for n in range(int(lo), int(hi) + 1):
                    if 1 <= n <= len(apps):
                        picked.add(apps[n - 1].id)
            except ValueError:
                print(f"Ignoring invalid range: {part}")
        else:
            try:
                n = int(part)
                if 1 <= n <= len(apps):
                    picked.add(apps[n - 1].id)
                else:
                    print(f"Ignoring out-of-range: {part}")
            except ValueError:
                print(f"Ignoring invalid entry: {part}")
    return picked


def _print_summary(result: dict, manager, target_dir) -> None:
    created, removed = result["created"], result["removed"]
    if not created and not removed:
        print("Already in sync, nothing changed.")
        return
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
