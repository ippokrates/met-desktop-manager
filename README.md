# MET Desktop Manager

Pick apps in a terminal checklist - get working launchers in your app menu.
Deselect an app and its launcher is removed.

Every managed launcher runs outside the Mullvad VPN tunnel
(`mullvad-exclude`) and is labelled accordingly:

```ini
# ~/.local/share/applications/google-chrome.desktop
[Desktop Entry]
Name=Google Chrome (Excluded)
Exec=mullvad-exclude /opt/google/chrome/google-chrome
Type=Application
Icon=google-chrome
X-Managed-By=met-desktop-manager
```

## Install

**Daily use** (global `desktop-manager` command):

```bash
git clone https://github.com/ippokrates/MET.git
cd MET
pipx install .          # install the command
pipx reinstall .        # pick up code changes later
```

**Development** (venv + tests):

```bash
python3 -m venv --prompt met .venv
source .venv/bin/activate
pip install -r requirements.txt   # or: pip install -e ".[dev]"
```

Then open `apps.yaml` and point `path_dirs` / `extra_dirs` at the folders
where your applications live (the defaults fit the author's machine).

Requirements: Python 3.12+, `questionary`, `rich`, `pyyaml`
(`pytest` for tests). Optional system tools, used when present:
`desktop-file-validate`, `update-desktop-database`, `flatpak`, `snap`.

## Use

| Command | What it does |
|---|---|
| `desktop-manager` | Interactive picker (filter, tick with `Space`, confirm) |
| `desktop-manager --list` | Print every discovered app |
| `desktop-manager --create "Google Chrome" --exec /opt/google/chrome/google-chrome` | One-off launcher, no picker |
| `desktop-manager --sync --all` | Launcher for everything found, no questions asked |

Without pipx, prefix with the venv python:
`.venv/bin/python -m desktop_manager --list`.

In non-terminal environments (pipes, SSH without TTY) the picker falls
back to a numbered prompt accepting ranges like `1,3,5-9`.

## How it works

```
apps.yaml → scanner → picker → generator → manager → ~/.local/share/applications/
                             │                      │
                             │                 state.json (memory)
```

- **Scanner** (`desktop_manager/scanner.py`) - finds executables in
  `path_dirs` (flat) and `extra_dirs` (recursive, depth-limited), plus
  `flatpak` and `snap`. Filters noise with `excludes`, an extension
  blocklist, and magic-byte checks, so only real binaries and scripts pass.
- **Generator** (`desktop_manager/generator.py`) - builds the `.desktop`
  text: `mullvad-exclude` prefix, `(Excluded)` suffix, safe filenames
  (`google-chrome.desktop`), quoting for paths with spaces.
- **Manager** (`desktop_manager/manager.py`) - writes/deletes the files,
  refreshes the desktop database, validates the result.
- **State** - maps `app_id → filename` so deselect finds the right file.
  Stored at `~/.local/share/desktop-manager/state.json`.
- **Config** - `~/.config/desktop-manager/apps.yaml` wins if present,
  then the repo's `apps.yaml`, then built-in defaults. The installed
  command works out of the box anywhere.

### Safety

- Only files carrying `X-Managed-By=met-desktop-manager` are ever deleted.
  Your existing launchers (e.g. `google-chrome.desktop` handmade earlier)
  always survive.
- A name collision never overwrites - the new file gets a `-2` suffix.

## Configure (`apps.yaml`)

```yaml
path_dirs:   # scanned flat, executables only
extra_dirs:  # scanned recursively, max_depth levels deep
max_depth: 3
flatpak: {enabled: true}
snap: {enabled: true}
excludes:    # substrings or globs, e.g. uninstall, "*.so*", crashpad
```

`/usr/bin` stays excluded on purpose (2,400+ CLI tools would flood the
picker). Re-add it if you ever want everything listed.

## Test

```bash
.venv/bin/python -m pytest tests/ -q   # 39 tests, tmp dirs only
```

The suite never touches the real applications dir or real state -
everything runs in pytest `tmp_path`. Generated files are additionally
checked with `desktop-file-validate` where available.

## Layout

```
MET/
├── desktop_manager/
│   ├── __main__.py    # CLI: flags + interactive picker + rich summary
│   ├── scanner.py     # app discovery
│   ├── generator.py   # .desktop text + filenames
│   └── manager.py     # create/delete/sync + validation
├── tests/             # pytest suite (generator/scanner/manager/cli)
├── apps.yaml          # scan sources, excludes, target dir
├── pyproject.toml     # packaging: provides the `desktop-manager` command
├── state.json         # dev fallback state map (gitignored)
├── requirements.txt
└── .venv/             # virtualenv (gitignored)
```
