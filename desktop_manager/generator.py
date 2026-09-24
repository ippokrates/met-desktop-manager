"""Build .desktop file content + filenames.

Follows the freedesktop Desktop Entry spec (minimal Application profile):
  https://specifications.freedesktop.org/desktop-entry-spec/
"""
from __future__ import annotations

import os
import re
from pathlib import Path

MANAGED_MARKER = "X-Managed-By=met-desktop-manager"
MANAGED_VALUE = "met-desktop-manager"

# All apps managed by this tool are launched outside the Mullvad VPN tunnel.
MULLVAD_PREFIX = "mullvad-exclude"
EXCLUDED_SUFFIX = " (Excluded)"

DEFAULT_ICON = "application-x-executable"
DEFAULT_CATEGORIES = "Utility;"


def sanitize_filename(name: str) -> str:
    """Turn a display name into a safe `<slug>.desktop` filename.

    Rules: lowercase, spaces/underscores -> '-', keep [a-z0-9-],
    collapse repeats, fall back to 'app', cap stem at 64 chars.
    """
    slug = (name or "").strip().lower()
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug:
        slug = "app"
    return f"{slug[:64]}.desktop"


def quote_exec(exec_path: str) -> str:
    """Quote an Exec value per the Desktop Entry spec.

    - `flatpak run <id> [args]` commands are passed through (each part
      quoted individually if needed).
    - Absolute paths with spaces/tabs/quotes are double-quoted with
      interior backslashes/quotes escaped.
    - Already-quoted values are left alone.
    """
    value = (exec_path or "").strip()
    if not value:
        return value
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value
    parts = value.split()
    if value.startswith("/"):
        # Single absolute path (possibly with spaces): quote as one unit.
        if len(parts) > 1:
            return _quote_word(value)
        return _quote_word(parts[0])
    # Multi-word command line (e.g. "mullvad-exclude ...", "flatpak run ..."):
    # quote each part, but first group parts that form an existing path
    # (otherwise a path with spaces would be split into separate args).
    out = [parts[0]]
    i = 1
    while i < len(parts):
        match_end = 0
        for j in range(len(parts), i, -1):
            candidate = os.path.expanduser(" ".join(parts[i:j]))
            if os.path.exists(candidate):
                match_end = j
                break
        if match_end:
            out.append(_quote_word(" ".join(parts[i:match_end])))
            i = match_end
        else:
            out.append(_quote_word(parts[i]))
            i += 1
    return " ".join(out)


def _quote_word(word: str) -> str:
    if not re.search(r'[ \t\n"\'\\><~|&;$*?#()`]', word):
        return word
    return '"' + word.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_desktop_content(
    name: str,
    exec_path: str,
    icon: str = "",
    comment: str = "",
    categories: str = DEFAULT_CATEGORIES,
    terminal: bool = False,
    exclude: bool = True,
) -> str:
    """Return full `[Desktop Entry]` text, including the managed marker.

    With exclude=True (default), the app is launched outside the VPN
    tunnel: `Exec` is prefixed with `mullvad-exclude` and `Name` gets an
    ` (Excluded)` suffix. Both are idempotent (no double prefix/suffix).
    """
    if not (name or "").strip():
        raise ValueError("name must not be empty")
    if not (exec_path or "").strip():
        raise ValueError("exec_path must not be empty")
    clean_name = name.strip()
    if exclude and not clean_name.lower().endswith(EXCLUDED_SUFFIX.lower()):
        clean_name += EXCLUDED_SUFFIX
    exec_value = quote_exec(exec_path)
    if exclude and not exec_value.startswith(MULLVAD_PREFIX + " "):
        exec_value = f"{MULLVAD_PREFIX} {exec_value}"
    lines = [
        "[Desktop Entry]",
        "Version=1.0",
        f"Name={clean_name}",
        f"Exec={exec_value}",
        "Type=Application",
        f"Terminal={'true' if terminal else 'false'}",
        f"Icon={(icon or '').strip() or DEFAULT_ICON}",
    ]
    if comment.strip():
        lines.append(f"Comment={comment.strip()}")
    if categories.strip():
        lines.append(f"Categories={categories.strip()}")
    lines.append(f"X-Managed-By={MANAGED_VALUE}")
    return "\n".join(lines) + "\n"


def unique_filename(target_dir: Path, base: str) -> str:
    """Return a non-colliding filename in target_dir (`<slug>[-2].desktop`).

    Never overwrites an existing file (managed or not).
    """
    candidate = sanitize_filename(base)
    stem = candidate[: -len(".desktop")]
    i = 2
    while (target_dir / candidate).exists():
        candidate = f"{stem}-{i}.desktop"
        i += 1
    return candidate


def is_managed_content(text: str) -> bool:
    """True if .desktop text carries our managed marker."""
    return f"X-Managed-By={MANAGED_VALUE}" in text
