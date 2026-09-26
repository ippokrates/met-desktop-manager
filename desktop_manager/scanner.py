"""Scan system for executables.

Sources (per apps.yaml):
  1. desktop_files — Exec= paths harvested from installed .desktop
     launchers (curated by package maintainers, best metadata)
  2. path_dirs   — non-recursive, executable bit required
     (except .AppImage files, which are listed anyway and flagged)
  3. extra_dirs  — recursive, depth-limited (max_depth); same
     .AppImage exception as path_dirs
  4. flatpak     — `flatpak list --app`
  5. snap        — `snap list` -> /snap/bin/<name>

Dedupes by resolved exec path. Missing dirs / missing tools are skipped.
"""
from __future__ import annotations

import fnmatch
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent.parent / "apps.yaml"


def _user_config_file() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "desktop-manager" / "apps.yaml"


# Built-in defaults used when no config file exists (e.g. pipx install).
# Mirrors the shipped apps.yaml; `~` is expanded at scan time.
DEFAULT_CONFIG = {
    "path_dirs": ["~/.local/bin", "/usr/local/bin"],
    "extra_dirs": ["/opt", "~/Downloads", "~/Applications", "~/.local/bin"],
    "max_depth": 3,
    "desktop_files": {"enabled": True},
    "flatpak": {"enabled": True},
    "snap": {"enabled": True},
    "excludes": [
        "uninstall", "uninstaller", "install.sh",
        "chrome-sandbox", "crashpad", "fsnotifier", "abicheck",
        "*.so*", "*.jar", "*.img", "*.iso",
    ],
}

# Directories never descended into (noise / venv / caches / bundled runtimes)
PRUNE_DIRS = {
    "__pycache__", ".git", ".hg", ".svn", "node_modules",
    ".venv", "venv", ".cache", ".mozilla", ".config",
    "jbr", "jre", "jdk",
}

# File extensions that are never GUI apps even if +x bit is set
SKIP_EXTS = {
    ".so", ".o", ".a", ".jar", ".img", ".iso",
    ".qcow2", ".vmdk", ".vdi", ".cab", ".pak",
    ".mp3", ".pdf", ".png", ".svg", ".xpm",
}

# Cache of directory listings for icon lookup (avoids O(n^2) on big dirs)
_dir_listing_cache: dict[str, list[str]] = {}

ICON_EXTS = (".png", ".svg", ".xpm")


@dataclass
class DiscoveredApp:
    id: str
    name: str
    exec_path: str
    icon_hint: str = ""
    source: str = ""
    source_desktop: str = ""  # original launcher basename (desktop: apps only)
    needs_chmod: bool = False  # .AppImage found without the exec bit


def load_config(path: str | Path | None = None) -> dict:
    """Load config, first found wins (never crashes, never writes).

    Order: explicit path arg -> ~/.config/desktop-manager/apps.yaml ->
    apps.yaml next to the package (repo/dev mode) -> built-in defaults.
    An explicit-but-missing path returns {} (used by tests).
    """
    if path is not None:
        return _read_config_file(Path(path).expanduser())
    for candidate in (_user_config_file(), CONFIG_FILE):
        if candidate.is_file():
            cfg = _read_config_file(candidate)
            if cfg:
                return cfg
    return dict(DEFAULT_CONFIG)


def _read_config_file(p: Path) -> dict:
    if not p.is_file():
        return {}
    try:
        import yaml  # PyYAML (in requirements.txt)
    except ImportError:
        return {}
    try:
        return yaml.safe_load(p.read_text()) or {}
    except Exception:
        return {}


def _is_excluded(basename: str, excludes: list[str]) -> bool:
    lower = basename.lower()
    for pat in excludes or []:
        pat = str(pat).lower()
        if any(c in pat for c in "*?["):
            if fnmatch.fnmatch(lower, pat):
                return True
        elif pat in lower:
            return True
    return False


def _is_executable_file(p: Path) -> bool:
    try:
        return p.is_file() and os.access(p, os.X_OK)
    except OSError:
        return False


def _looks_executable(p: Path) -> bool:
    """Magic-byte check: only real binaries/scripts pass.

    Downloads/ is full of data files with a stray +x bit (datasets,
    firmware, READMEs). os.access alone is not enough.
    """
    try:
        with open(p, "rb") as f:
            magic = f.read(4)
    except OSError:
        return False
    if magic.startswith(b"#!") or magic.startswith(b"\x7fELF") or magic.startswith(b"MZ"):
        return True
    # Mach-O (macOS) / fat binary, just in case
    return magic in (
        b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",
        b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe",
    )


def _has_skip_ext(p: Path) -> bool:
    """True if the file (or any of its suffixes, e.g. .so.1) is a non-app type."""
    suffixes = [s.lower() for s in p.suffixes] or [p.suffix.lower()]
    return any(s in SKIP_EXTS or s.startswith(".so") for s in suffixes)


def _is_appimage(p: Path | str) -> bool:
    """True for `.AppImage` files (case-insensitive).

    Only the final suffix counts, so `foo.AppImage.part` is not one.
    """
    try:
        return Path(p).suffix.lower() == ".appimage"
    except Exception:
        return False


def exec_key(exec_path: str) -> str:
    """Stable cross-source identity for an executable.

    Source-prefixed IDs (bin:/file:/desktop:) change when discovery does;
    the underlying binary doesn't. Strips our `mullvad-exclude` wrapper and
    field codes, resolves absolute paths (symlinks included) and bare names
    via PATH. Multi-word commands (e.g. `flatpak run <id>`) normalize with
    their first token resolved, staying distinct per app.
    """
    value = (exec_path or "").strip()
    if not value:
        return ""
    if value.startswith("mullvad-exclude "):
        value = value[len("mullvad-exclude "):].strip()
    try:
        tokens = shlex.split(value)
    except ValueError:
        return os.path.normpath(value)
    tokens = [t for t in tokens if not FIELD_CODE_RE.fullmatch(t)]
    if not tokens:
        return ""
    first = tokens[0]
    if os.path.isabs(first):
        try:
            first = str(Path(first).resolve())
        except OSError:
            first = os.path.normpath(first)
    elif "/" not in first:
        found = shutil.which(first)
        if found:
            try:
                first = str(Path(found).resolve())
            except OSError:
                first = os.path.normpath(found)
    return first if len(tokens) == 1 else first + " " + " ".join(tokens[1:])


def binary_exists(exec_string: str) -> bool:
    """True if the binary in an Exec= line still exists on disk.

    Strips `mullvad-exclude`, field codes (%U) and args, then checks:
    absolute path -> is it still a file? `flatpak run <id>` -> is that
    flatpak installed? bare name -> is it on PATH? Never raises.
    """
    value = (exec_string or "").strip()
    if not value:
        return False
    if value.startswith("mullvad-exclude "):
        value = value[len("mullvad-exclude "):].strip()
    try:
        tokens = shlex.split(value)
    except ValueError:
        return False
    tokens = [t for t in tokens if not FIELD_CODE_RE.fullmatch(t)]
    if not tokens:
        return False
    first = tokens[0]
    # flatpak run <app-id>: check that specific app is installed.
    # First token may be bare "flatpak" or resolved "/usr/bin/flatpak".
    first_base = Path(first).name if "/" in first else first
    if first_base == "flatpak" and len(tokens) >= 3 and tokens[1] == "run":
        if shutil.which("flatpak") is None:
            return False
        try:
            out = subprocess.run(
                ["flatpak", "info", tokens[2]],
                capture_output=True, timeout=15,
            )
            return out.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    if os.path.isabs(first):
        try:
            return Path(first).is_file()
        except OSError:
            return False
    if "/" in first:
        return False  # ambiguous relative path with dirs
    return shutil.which(first) is not None


# Tokens that are packaging noise, not part of an app's real name
# (architectures, OS names, bundle formats, release channels)
NOISE_TOKENS = {
    "x86", "x64", "x86_64", "amd64", "arm64", "aarch64",
    "i386", "i686", "x32", "win32", "win64",
    "linux", "macos", "darwin", "windows",
    "appimage", "portable", "beta", "alpha", "rc",
}
VERSION_RE = re.compile(r"^v?\d+(\.\d+)+$|^\d+$|^(beta|alpha|rc)\.?[\d.]*$", re.IGNORECASE)


def _pretty_name(stem: str) -> str:
    """Turn `Glint-1.9.5` into `Glint`: drop versions + platform tokens."""
    tokens = re.split(r"[-_.\s]+", stem)
    kept = [t for t in tokens
            if t and t.lower() not in NOISE_TOKENS and not VERSION_RE.match(t)]
    cleaned = " ".join(kept).strip() or stem
    cleaned = re.sub(r"[-_.]+", " ", cleaned).strip()
    return cleaned.title() if cleaned else stem


def _find_icon_hint(exec_file: Path) -> str:
    """Look for an icon next to the binary: <stem>.png/svg, icon.png, etc."""
    try:
        directory = exec_file.parent
        stem = exec_file.stem.lower()
        candidates: list[Path] = []
        for ext in ICON_EXTS:
            candidates.append(directory / f"{exec_file.stem}{ext}")
            candidates.append(directory / f"{stem}{ext}")
        for generic in ("icon", "app-icon", "logo"):
            for ext in ICON_EXTS:
                candidates.append(directory / f"{generic}{ext}")
        for c in candidates:
            if c.is_file():
                return str(c)
        # Last resort: any png/svg in same dir mentioning the stem.
        # Guarded: skip huge dirs (e.g. /usr/bin) via cached listing.
        try:
            key = str(directory)
            listing = _dir_listing_cache.get(key)
            if listing is None:
                listing = os.listdir(directory)
                _dir_listing_cache[key] = listing
            if len(listing) <= 150:
                for child_name in listing:
                    child_lower = child_name.lower()
                    if child_lower.endswith(ICON_EXTS) and stem in child_lower:
                        return str(directory / child_name)
        except OSError:
            pass
    except OSError:
        pass
    return ""


def _unique_id(base: str, seen: set[str]) -> str:
    candidate = base
    i = 2
    while candidate in seen:
        candidate = f"{base}-{i}"
        i += 1
    seen.add(candidate)
    return candidate


def scan_path_dirs(path_dirs: list[str], excludes: list[str]) -> list[DiscoveredApp]:
    apps: list[DiscoveredApp] = []
    seen_ids: set[str] = set()
    for raw in path_dirs or []:
        d = Path(os.path.expandvars(os.path.expanduser(str(raw))))
        if not d.is_dir():
            continue
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                if entry.is_symlink() and not entry.exists():
                    continue  # broken symlink
                if _is_excluded(entry.name, excludes):
                    continue
                if _has_skip_ext(entry):
                    continue
                is_appimage = _is_appimage(entry.name)
                executable = os.access(entry, os.X_OK)
                if not executable and not is_appimage:
                    continue
                resolved_path = entry.resolve()
                if not _looks_executable(resolved_path):
                    continue
                resolved = str(resolved_path)
                name = _pretty_name(entry.stem if not entry.suffix else entry.name)
                # strip extension for display only if it looks like .sh/.run/.bin/.AppImage
                app_id = _unique_id(f"bin:{entry.name.lower()}", seen_ids)
                apps.append(DiscoveredApp(
                    id=app_id,
                    name=name or entry.name,
                    exec_path=resolved,
                    icon_hint=_find_icon_hint(entry.resolve()),
                    source=f"PATH:{d}",
                    needs_chmod=is_appimage and not executable,
                ))
            except OSError:
                continue
    return apps


def scan_extra_dirs(extra_dirs: list[str], max_depth: int, excludes: list[str]) -> list[DiscoveredApp]:
    apps: list[DiscoveredApp] = []
    seen_ids: set[str] = set()
    for raw in extra_dirs or []:
        root = Path(os.path.expandvars(os.path.expanduser(str(raw))))
        if not root.is_dir():
            continue
        # BFS with depth limit: (dir, depth) where root itself is depth 0
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            try:
                with os.scandir(current) as it:
                    entries = list(it)
            except OSError:
                continue
            for entry in entries:
                try:
                    name = entry.name
                    if entry.is_dir(follow_symlinks=False):
                        if name in PRUNE_DIRS or name.startswith("."):
                            continue
                        if _is_excluded(name, excludes):
                            continue
                        if depth < max_depth:
                            stack.append((Path(entry.path), depth + 1))
                    elif entry.is_file(follow_symlinks=True):
                        if _is_excluded(name, excludes):
                            continue
                        fp = Path(entry.path)
                        if _has_skip_ext(fp):
                            continue
                        is_appimage = _is_appimage(fp.name)
                        executable = os.access(fp, os.X_OK)
                        if not executable and not is_appimage:
                            continue
                        resolved_fp = fp.resolve()
                        if not _looks_executable(resolved_fp):
                            continue
                        resolved = str(resolved_fp)
                        app_id = _unique_id(f"file:{fp.stem.lower()}", seen_ids)
                        apps.append(DiscoveredApp(
                            id=app_id,
                            name=_pretty_name(fp.stem),
                            exec_path=resolved,
                            icon_hint=_find_icon_hint(fp.resolve()),
                            source=f"SCAN:{root}",
                            needs_chmod=is_appimage and not executable,
                        ))
                except OSError:
                    continue
    return apps


# Launcher dirs harvested for Exec= paths (package-maintainer curated).
# Later entries are fallbacks; all are skipped silently when missing.
DESKTOP_FILE_DIRS = [
    "/usr/share/applications",
    "~/.local/share/applications",
    "~/.local/share/flatpak/exports/share/applications",
    "/var/lib/flatpak/exports/share/applications",
]

# freedesktop field codes (%f %F %u %U %d %D %n %N %i %c %k %v %m)
FIELD_CODE_RE = re.compile(r"%[fFuUdDnNickvm]")
ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _clean_exec(value: str) -> tuple[str, int]:
    """Split an Exec= line into (binary, extra_arg_count).

    Strips field codes, `env` prefixes and VAR= assignments.
    Returns ("", 0) when no usable binary remains.
    """
    try:
        tokens = shlex.split(value.strip())
    except ValueError:
        return "", 0
    tokens = [t for t in tokens if not FIELD_CODE_RE.fullmatch(t)]
    while tokens and tokens[0] == "env":
        tokens.pop(0)
    while tokens and ENV_ASSIGN_RE.match(tokens[0]) and "/" not in tokens[0]:
        tokens.pop(0)
    if not tokens:
        return "", 0
    return tokens[0], len(tokens) - 1


def _resolve_binary(binary: str) -> str:
    """Resolve an Exec binary to an absolute, verified executable path."""
    candidate: Path | None = None
    if os.path.isabs(binary):
        candidate = Path(binary)
    elif "/" in binary:
        return ""  # relative path with dirs: ambiguous, skip
    else:
        found = shutil.which(binary)
        if found:
            candidate = Path(found)
    if candidate is None:
        return ""
    try:
        resolved = candidate.resolve()
    except OSError:
        return ""
    if not resolved.is_file() or _has_skip_ext(resolved):
        return ""
    if not _looks_executable(resolved):
        return ""
    return str(resolved)


def _parse_desktop_file(path: Path) -> dict:
    """First occurrence of each interesting key (locale variants ignored)."""
    fields: dict[str, str] = {}
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return fields
    if "X-Managed-By=met-desktop-manager" in text:
        fields["managed"] = "yes"  # ours: never harvest our own output
        return fields
    for line in text.splitlines():
        if line.startswith("Name=") and "Name" not in fields:
            fields["Name"] = line[5:].strip()
        elif line.startswith("Exec=") and "Exec" not in fields:
            fields["Exec"] = line[5:].strip()
        elif line.startswith("Icon=") and "Icon" not in fields:
            fields["Icon"] = line[5:].strip()
        elif line.startswith("Type=") and "Type" not in fields:
            fields["Type"] = line[5:].strip()
        elif line.startswith("NoDisplay=") and "NoDisplay" not in fields:
            fields["NoDisplay"] = line[10:].strip().lower()
        elif line.startswith("Hidden=") and "Hidden" not in fields:
            fields["Hidden"] = line[7:].strip().lower()
    return fields


def scan_desktop_files(desktop_dirs: list[str] | None = None,
                       excludes: list[str] | None = None) -> list[DiscoveredApp]:
    """Harvest binaries referenced by installed .desktop launchers.

    One entry per binary (fewest-args launcher wins, e.g. plain Codium over
    "Codium --new-window"). Skips hidden entries, our own managed files,
    and unresolvable/deleted binaries.
    """
    excludes = excludes or []
    if desktop_dirs is None:
        desktop_dirs = DESKTOP_FILE_DIRS
    best: dict[str, tuple[int, str, DiscoveredApp]] = {}  # exec -> (nargs, fname, app)
    for raw in desktop_dirs:
        d = Path(os.path.expandvars(os.path.expanduser(str(raw))))
        if not d.is_dir():
            continue
        try:
            files = sorted(d.glob("*.desktop"))
        except OSError:
            continue
        for f in files:
            try:
                fields = _parse_desktop_file(f)
                if fields.get("managed"):
                    continue
                if fields.get("Type", "Application") != "Application":
                    continue
                if fields.get("NoDisplay") == "true" or fields.get("Hidden") == "true":
                    continue
                exec_line = fields.get("Exec", "")
                # Skip foreign mullvad-wrapped launchers (e.g. handmade
                # split-tunnel entries): not plain binaries we can adopt.
                if not exec_line or exec_line.startswith("mullvad-exclude "):
                    continue
                binary, nargs = _clean_exec(exec_line)
                if not binary:
                    continue
                resolved = _resolve_binary(binary)
                if not resolved:
                    continue
                if _is_excluded(Path(resolved).name, excludes):
                    continue
                name = fields.get("Name", "") or _pretty_name(Path(resolved).stem)
                icon = fields.get("Icon", "")
                app = DiscoveredApp(
                    id=f"desktop:{Path(resolved).stem.lower()}",
                    name=name,
                    exec_path=resolved,
                    icon_hint=icon or _find_icon_hint(Path(resolved)),
                    source=f"desktop:{d}",
                    source_desktop=f.name,
                )
                prev = best.get(resolved)
                if prev is None or (nargs, f.name) < (prev[0], prev[1]):
                    best[resolved] = (nargs, f.name, app)
            except OSError:
                continue
    seen_ids: set[str] = set()
    apps = [entry[2] for entry in sorted(best.values(), key=lambda e: e[2].name.lower())]
    for app in apps:  # ensure unique ids (same stem, different dirs)
        app.id = _unique_id(app.id, seen_ids)
    return apps


def scan_flatpak(enabled: bool = True) -> list[DiscoveredApp]:
    if not enabled or shutil.which("flatpak") is None:
        return []
    try:
        out = subprocess.run(
            ["flatpak", "list", "--app", "--columns=application,name"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    apps: list[DiscoveredApp] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        app_id_flat = parts[0].strip()
        name = parts[1].strip() if len(parts) > 1 else app_id_flat
        if not app_id_flat:
            continue
        apps.append(DiscoveredApp(
            id=f"flatpak:{app_id_flat.lower()}",
            name=name,
            exec_path=f"flatpak run {app_id_flat}",
            icon_hint="",
            source="flatpak",
        ))
    return apps


def scan_snap(enabled: bool = True) -> list[DiscoveredApp]:
    if not enabled or shutil.which("snap") is None:
        return []
    try:
        out = subprocess.run(["snap", "list"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    apps: list[DiscoveredApp] = []
    lines = out.stdout.splitlines()[1:]  # skip header
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        snap_name = parts[0]
        bin_path = Path(f"/snap/bin/{snap_name}")
        exec_path = str(bin_path) if bin_path.exists() else snap_name
        apps.append(DiscoveredApp(
            id=f"snap:{snap_name.lower()}",
            name=_pretty_name(snap_name),
            exec_path=exec_path,
            icon_hint="",
            source="snap",
        ))
    return apps


def scan(config: dict | None = None) -> list[DiscoveredApp]:
    """Discover apps per config (loads apps.yaml when None). Sorted by name."""
    cfg = config if config is not None else load_config()
    excludes: list[str] = list(cfg.get("excludes", []) or [])
    max_depth = int(cfg.get("max_depth", 3) or 3)

    found: list[DiscoveredApp] = []
    desk_cfg = cfg.get("desktop_files", {})
    desk_enabled = desk_cfg.get("enabled", True) if isinstance(desk_cfg, dict) else bool(desk_cfg)
    if desk_enabled:
        found.extend(scan_desktop_files(excludes=excludes))
    found.extend(scan_path_dirs(cfg.get("path_dirs", []), excludes))
    found.extend(scan_extra_dirs(cfg.get("extra_dirs", []), max_depth, excludes))

    flat_cfg = cfg.get("flatpak", {})
    flat_enabled = flat_cfg.get("enabled", True) if isinstance(flat_cfg, dict) else bool(flat_cfg)
    snap_cfg = cfg.get("snap", {})
    snap_enabled = snap_cfg.get("enabled", True) if isinstance(snap_cfg, dict) else bool(snap_cfg)
    found.extend(scan_flatpak(flat_enabled))
    found.extend(scan_snap(snap_enabled))

    # Dedupe by resolved exec path (keep first), then sort
    seen_exec: set[str] = set()
    unique: list[DiscoveredApp] = []
    for app in found:
        key = os.path.normpath(app.exec_path)
        if key in seen_exec:
            continue
        seen_exec.add(key)
        unique.append(app)
    unique.sort(key=lambda a: a.name.lower())
    return unique


def group_key_for_app(app: DiscoveredApp) -> str:
    """Folder bucket for visual grouping (display only, IDs unchanged).

    Real binaries group by their parent directory; package-manager
    shims without a real path (flatpak/snap) group by source label.
    """
    source = (app.source or "").strip()
    if source in ("flatpak", "snap"):
        return source
    exec_path = (app.exec_path or "").strip()
    if not exec_path:
        return source or "other"
    try:
        tokens = shlex.split(exec_path)
    except ValueError:
        tokens = [exec_path]
    first = tokens[0] if tokens else ""
    if not first:
        return source or "other"
    if first == "flatpak":
        return "flatpak"
    if not os.path.isabs(first):
        return source or first or "other"
    return str(Path(first).parent)


def short_group_path(key: str) -> str:
    """`~`-shortened group label for display (`/home/you/.local/bin` -> `~/.local/bin`)."""
    if not key or not os.path.isabs(key):
        return key
    try:
        home = str(Path.home())
        if key == home or key.startswith(home + os.sep):
            return "~" + key[len(home):]
    except Exception:
        pass
    return key


def group_base_name(key: str) -> str:
    """Bold part of the header: last folder (`/opt/Vesktop` -> `Vesktop`)."""
    if not key:
        return "other"
    if os.path.isabs(key):
        return Path(key).name or key
    return key


def group_by_directory(apps: list[DiscoveredApp]) -> dict[str, list[DiscoveredApp]]:
    """Group apps by directory for display. Returns groups sorted by
    folder name; apps inside keep their input order (scan() already
    returns them name-sorted). View-only: IDs/order of the input
    list are not mutated."""
    groups: dict[str, list[DiscoveredApp]] = {}
    for app in apps or []:
        groups.setdefault(group_key_for_app(app), []).append(app)
    ordered = sorted(groups.items(),
                     key=lambda kv: (group_base_name(kv[0]).lower(), kv[0].lower()))
    return dict(ordered)


def load_existing_execs(dirs: list[str | Path] | None = None) -> set[str]:
    """Parse Exec= lines from existing .desktop files (for future filtering)."""
    if dirs is None:
        dirs = [
            Path.home() / ".local" / "share" / "applications",
            Path("/usr/share/applications"),
        ]
    execs: set[str] = set()
    for d in dirs:
        p = Path(d).expanduser()
        if not p.is_dir():
            continue
        try:
            files = list(p.glob("*.desktop"))
        except OSError:
            continue
        for f in files:
            try:
                for line in f.read_text(errors="ignore").splitlines():
                    if line.startswith("Exec="):
                        execs.add(line[5:].strip().strip('"').split()[0])
                        break
            except OSError:
                continue
    return execs
