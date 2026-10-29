"""Interactive CLI entry point."""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime

from rich.console import Console

# Prompt prefix shown in front of every questionary question.
# One place owns the symbol, so changing it later means editing one line.
PROMPT_MARK = "❯"


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
    p.add_argument("--export", metavar="FILE",
                   help="Export managed entries to a JSON file")
    p.add_argument("--import", dest="import_file", metavar="FILE",
                   help="Import managed entries from a JSON file")
    p.add_argument("--check-updates", action="store_true",
                   help="Compare installed version with GitHub")
    return p


def _print_group_header(group_key: str, count: int, console) -> None:
    """One-line header: bold folder name + dimmed full path + count.

    Example: `  Vesktop  /opt/Vesktop  (1 app)`
    """
    from rich.markup import escape

    from .scanner import group_base_name, short_group_path

    base = group_base_name(group_key)
    short = short_group_path(group_key)
    noun = "app" if count == 1 else "apps"
    console.print(f"  [bold]{escape(base)}[/bold] "
                  f"[dim]{escape(short)} ({count} {noun})[/dim]")


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
    """Return (config, showing_system_apps) for a --show/--hide-system flag.

    The config is left as loaded; the override is applied by passing
    `show_system` to scan() rather than by rewriting system_apps.hide, so
    there is only one place that knows how the override works.

    Example:
      _resolve_show_system(cfg, True)  -> (cfg, True)   # show them
      _resolve_show_system(cfg, None)  -> (cfg, not hide)
    """
    showing = (not bool((cfg or {}).get("system_apps", {}).get("hide", True))
               if override is None else override)
    return dict(cfg or {}), showing


def _rescan(cfg: dict, show: bool) -> list:
    """Rescan with system apps shown/hidden."""
    from .scanner import scan as _scan
    return _scan(cfg, show_system=show)


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


def _toggle_rescan(cfg, cur_apps, cur_show):
    """Rescan after a system-apps toggle. Returns (apps, show) or None.

    None means the rescan failed, so the caller must keep the current list
    AND the current toggle. Flipping only on success is the whole point:
    the two can never disagree about what the list contains.

    Example:
      _toggle_rescan(cfg, apps, False) -> (apps_with_system, True)
      rescan raises                     -> None
    """
    new_apps = _try_rescan(cfg, not cur_show)
    if new_apps is None:
        return None
    return new_apps, not cur_show


def _pause(msg: str = "Press Enter to return to the menu") -> None:
    """Wait for Enter so output can be read before the menu redraws."""
    try:
        input(f"{msg} ...")
    except (EOFError, KeyboardInterrupt):
        pass


def _cmd_list(override: bool | None) -> int:
    """Print discovered apps (body of the --list branch)."""
    from .scanner import group_by_directory, load_config, scan

    cfg, _showing = _resolve_show_system(load_config(), override)
    apps = scan(cfg, show_system=override)
    if not apps:
        print("No apps discovered. Check apps.yaml paths.")
        return 0
    console = Console()
    for group_key, g_apps in group_by_directory(apps).items():
        _print_group_header(group_key, len(g_apps), console)
        for app in g_apps:
            icon = f" [{app.icon_hint}]" if app.icon_hint else ""
            print(f"    - {app.name}{_chmod_tag(app)} | "
                  f"{app.exec_path}{icon} ({app.source})")
    print(f"\n{len(apps)} app(s) discovered.")
    return 0


def _cmd_export(path: str) -> int:
    """Write a JSON backup (body of the --export branch)."""
    from pathlib import Path

    from . import manager

    data = manager.export_managed()
    dest = Path(path).expanduser()
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        dest.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
    except OSError as e:
        print(f"Cannot write {dest}: {e}")
        return 1
    print(f"Exported {len(data['entries'])} app(s) to {dest}")
    return 0


def _cmd_import(path: str) -> int:
    """Restore a JSON backup (body of the --import branch).

    Errors print and return 1 instead of parser.error(), which would raise
    SystemExit and close the menu.
    """
    from pathlib import Path

    from . import manager

    src = Path(path).expanduser()
    try:
        data = json.loads(src.read_text())
    except OSError as e:
        print(f"Cannot read {src}: {e}")
        return 1
    except ValueError as e:
        print(f"Invalid JSON in {src}: {e}")
        return 1
    try:
        result = manager.import_managed(data)
    except ValueError as e:
        print(str(e))
        return 1
    wrote, skipped = result["wrote"], result["skipped"]
    print(f"Imported {len(wrote)} app(s), skipped {len(skipped)}.")
    for f in wrote:
        ok, msg = manager.validate_desktop_file(
            manager.resolve_target_dir() / f)
        print(f"  {f}: {'valid' if ok else 'INVALID: ' + msg}")
    for s in skipped:
        print(f"  skipped {s.get('app_id') or '?'} "
              f"({s.get('filename') or '?'}): {s.get('reason')}")
    return 0


def _cmd_options() -> int:
    """Read-only settings screen. Never writes the config."""
    import questionary
    from rich.table import Table

    from . import manager
    from .scanner import config_source, load_config

    cfg = load_config()
    source = config_source()

    def enabled(section, default=True):
        """Read `enabled` from {enabled: bool} or a bare bool."""
        if isinstance(section, dict):
            return bool(section.get("enabled", default))
        return bool(section) if section is not None else default

    sys_cfg = cfg.get("system_apps")
    sys_cfg = sys_cfg if isinstance(sys_cfg, dict) else {}
    appimage_cfg = cfg.get("appimage")
    appimage_cfg = appimage_cfg if isinstance(appimage_cfg, dict) else {}

    table = Table(title="Configuration", show_header=False)
    table.add_column("setting", style="bold")
    table.add_column("value")
    table.add_row("file", str(source) if source else "built-in defaults")
    table.add_row("target dir", str(manager.resolve_target_dir()))
    table.add_row("path dirs",
                  ", ".join(cfg.get("path_dirs", []) or []) or "-")
    table.add_row("extra dirs",
                  ", ".join(cfg.get("extra_dirs", []) or []) or "-")
    table.add_row("system apps",
                  "hidden" if sys_cfg.get("hide", True) else "shown")
    table.add_row("flatpak", "on" if enabled(cfg.get("flatpak")) else "off")
    table.add_row("snap", "on" if enabled(cfg.get("snap")) else "off")
    table.add_row("desktop files",
                  "on" if enabled(cfg.get("desktop_files")) else "off")
    table.add_row("appimages",
                  "adopt newest" if appimage_cfg.get("prefer_newest", True)
                  else "repair only")
    table.add_row("excludes",
                  f"{len(cfg.get('excludes', []) or [])} patterns")
    Console().print(table)

    choice = questionary.select(
        "Options",
        choices=["Open in editor", "Back"],
        qmark=PROMPT_MARK,
    ).ask()
    if choice != "Open in editor":
        return 0
    if source is None:
        print("No config file found, built-in defaults are in use.")
        return 0
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"
    try:
        subprocess.run([*shlex.split(editor), str(source)])
    except (FileNotFoundError, OSError) as e:
        print(f"Could not start editor: {e}")
    return 0


def _backup_name() -> str:
    """Default export name with a unique timestamp."""
    return datetime.now().strftime("met-backup-%Y-%m-%d-%H%M%S.json")


def _cmd_backup_menu() -> None:
    """Export / Import submenu; returns to the main menu afterwards."""
    import questionary

    choice = questionary.select(
        "Backup / restore",
        choices=["Export backup", "Import backup", "Back"],
        qmark=PROMPT_MARK,
    ).ask()
    if choice == "Export backup":
        path = questionary.path("Save backup to:",
                                default=_backup_name()).ask()
        if path:
            _cmd_export(path)
            _pause()
    elif choice == "Import backup":
        path = questionary.path("Read backup from:").ask()
        if path:
            _cmd_import(path)
            _pause()


def _menu(override: bool | None) -> int:
    """Startup screen. Picking apps or quitting ends the session."""
    import questionary

    from . import manager
    from .scanner import load_config

    while True:
        cfg = load_config()
        target_dir = manager.resolve_target_dir()
        count = len(manager.managed_files(target_dir))
        sys_cfg = cfg.get("system_apps")
        hide = sys_cfg.get("hide", True) if isinstance(sys_cfg, dict) else True
        header = (f"met   {count} app(s) managed  ·  "
                  f"system apps {'hidden' if hide else 'shown'}")

        choice = questionary.select(
            header,
            choices=["Pick apps", "List discovered", "Options",
                     "Backup / restore", "Quit"],
            qmark=PROMPT_MARK,
        ).ask()

        if choice in (None, "Quit"):
            return 0
        if choice == "Pick apps":
            interactive(show_system=override)
            return 0
        if choice == "List discovered":
            _cmd_list(override)
            _pause()
        elif choice == "Options":
            _cmd_options()
        elif choice == "Backup / restore":
            _cmd_backup_menu()


def main() -> int | None:
    if shutil.which("mullvad-exclude") is None:
        print("warning: mullvad-exclude not found; managed apps will not launch",
              file=sys.stderr)

    # `run` is dispatched before argparse: it is a hot path (every AppImage
    # launch) and must forward arbitrary app args (%U, files) untouched.
    if sys.argv[1:2] == ["run"]:
        from . import router
        sys.exit(router.run(sys.argv[2:]))

    if sys.argv[1:2] == ["update"]:
        from . import updatecheck
        return updatecheck.run_update()

    from . import manager
    from . import router
    from .scanner import load_config, scan

    parser = build_parser()
    args = parser.parse_args()
    override = True if args.show_system else (False if args.hide_system else None)
    if args.check_updates:
        from . import updatecheck
        updatecheck.manual_check()
        return
    if args.list:
        return _cmd_list(override)
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
        cfg, _showing = _resolve_show_system(load_config(), override)
        # Repair replaced AppImages first, so --all does not create a second
        # launcher for an app the state still remembers under its old id.
        for message in router.heal_all(manager.resolve_target_dir(),
                                       manager.STATE_FILE):
            print(message)
        apps = scan(cfg, show_system=override)
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
    if args.export:
        return _cmd_export(args.export)
    if args.import_file:
        return _cmd_import(args.import_file)
    if sys.stdin.isatty():
        return _menu(override)
    interactive(show_system=override)


def interactive(target_dir=None, state_file=None, show_system=None) -> None:
    """No-args mode: checkbox picker -> confirm -> sync -> summary.

    target_dir/state_file default to the real locations; tests pass tmp paths.
    show_system True/False forces system launchers shown/hidden (CLI flags);
    None follows apps.yaml.
    """
    from pathlib import Path

    from . import manager
    from . import router
    from . import updatecheck
    from .scanner import load_config, scan

    target_dir = manager.resolve_target_dir(target_dir)
    state_file = Path(state_file) if state_file else manager.STATE_FILE

    cfg, showing = _resolve_show_system(load_config(), show_system)
    apps = scan(cfg, show_system=show_system)
    if not apps:
        print("No apps discovered. Check apps.yaml paths.")
        return

    # Repair AppImage launchers whose file an update replaced, so the picker
    # shows them still ticked instead of broken-old + unticked-new.
    for message in router.heal_all(target_dir, state_file):
        print(message)

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
             if vanished else ""))
    if vanished:
        _print_vanished(vanished,
                        "No longer found, will be deleted:")
    prune = False
    if sys.stdin.isatty():
        import questionary
        if not questionary.confirm("Apply these changes?", default=True,
                                       qmark=PROMPT_MARK).ask():
            print("Cancelled, nothing changed.")
            return
        if vanished and questionary.confirm(
                "Also delete the broken files above?", default=True,
                qmark=PROMPT_MARK).ask():
            prune = True

    result = manager.sync(selected, target_dir, state_file,
                          discovered={a.id: a for a in apps},
                          prune_vanished=prune)
    _print_summary(result, manager, target_dir)
    updatecheck.auto_check()


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
    import questionary
    from questionary import Choice, Separator

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
            "Filter apps (substring of name/path, Enter = show all):",
            qmark=PROMPT_MARK).ask()
        if filt is None:  # Ctrl-C
            return None
        filt = filt.strip()
        if filt.lower() in ("!sys", "!system") and cfg is not None:
            new = _toggle_rescan(cfg, cur_apps, cur_show)
            if new is None:
                continue  # failed: keep old list and old toggle
            cur_apps, cur_show = new
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
            instruction="(Space on first row toggles system apps)",
            qmark=PROMPT_MARK).ask()
        if answer is None:  # Ctrl-C
            return None
        if TOGGLE_SYSTEM_VALUE in answer:
            picked = ((picked - {a.id for a in candidates})
                      | (set(answer) - {TOGGLE_SYSTEM_VALUE}))
            new = _toggle_rescan(cfg, cur_apps, cur_show)
            if new is not None:
                cur_apps, cur_show = new
                base_for = _rebuild_base()
                print(f"System apps {'shown' if cur_show else 'hidden'} "
                      f"({len(cur_apps)} total).")
            continue
        picked = (picked - {a.id for a in candidates}) | set(answer)
        more = questionary.confirm(
            f"{len(picked)} selected. Filter again to add more?",
            default=False, qmark=PROMPT_MARK).ask()
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
        console = Console()
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
            new = _toggle_rescan(cfg, cur_apps, cur_show)
            if new is not None:
                cur_apps, cur_show = new
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


if __name__ == "__main__":
    sys.exit(main())
