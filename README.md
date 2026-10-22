# 🛠️ met - Desktop Launcher Manager

<div align="center">
  
  <p><strong>Pick apps in a terminal checklist, get working launchers in your app menu.</strong></p>
  
  <p>
    <img src="https://github.com/ippokrates/met-desktop-manager/actions/workflows/ci.yml/badge.svg" alt="tests"/>
    <img src="https://img.shields.io/github/license/ippokrates/met-desktop-manager" alt="license"/>
    <img src="https://img.shields.io/badge/platform-Linux-lightgrey" alt="platform"/>
    <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="python"/>
  </p>
</div>

Every managed launcher runs outside the Mullvad VPN tunnel (`mullvad-exclude`)
and is labelled accordingly:

```ini
# ~/.local/share/applications/google-chrome.desktop
[Desktop Entry]
Name=Google Chrome (Excluded)
Exec=mullvad-exclude /opt/google/chrome/google-chrome
Type=Application
Icon=google-chrome
X-Managed-By=met-desktop-manager
```

## ⚡ Quick Start

### Installation

#### One-liner (Recommended)
```bash
curl -fsSL https://raw.githubusercontent.com/ippokrates/met-desktop-manager/main/install.sh | bash
```

Downloads the install script straight from the repo and runs it. It needs
Python 3.12+ and pipx, installs `met` / `desktop-manager` via pipx, and
doubles as the updater: run it again any time to pick up new commits.

#### From the repo
```bash
git clone https://github.com/ippokrates/met-desktop-manager.git
cd met-desktop-manager
pipx install .          # install the command
pipx reinstall met-desktop-manager   # pick up code changes later
```

#### Development (venv + tests)
```bash
python3 -m venv --prompt met .venv
source .venv/bin/activate
pip install -r requirements.txt   # or: pip install -e ".[dev]"
```

Then open `apps.yaml` and point `path_dirs` / `extra_dirs` at the folders
where your applications live (defaults fit the author's machine).

Commands: `met` or `desktop-manager` (two names for the same program).

### Basic Usage

```bash
# Interactive picker: filter, tick with Space, confirm
met

# List every discovered app
met --list
```

## 🔧 Commands

### Core Commands

#### `met` (interactive picker)
Checkbox picker. Filter rounds accumulate picks, first row toggles system apps.

```bash
met [--show-system | --hide-system]
```

- `Space` toggles a row, `!sys` in the filter box flips system apps
- non-terminal environments (pipes, SSH) fall back to a numbered prompt
  accepting ranges like `1,3,5-9` (`s` toggles system apps)

#### `met --list`
Print every discovered app, grouped under one header per folder.

```bash
met --list [--show-system | --hide-system]
```

#### `met --create NAME --exec PATH`
One-off launcher, no picker.

```bash
met --create "Google Chrome" --exec /opt/google/chrome/google-chrome [--icon ICON]
```

#### `met --sync --all`
Launcher for everything found, no questions asked.

```bash
met --sync --all
```

Vanished apps (no longer discovered) are reported and kept, never deleted
silently; the command exits non-zero instead.

#### `met run APP-ID [ARGS]`
Launch a managed app by app id. This is what AppImage launchers call from
their `Exec=` line (see [Self-updating AppImages](#self-updating-appimages));
you rarely need it by hand. Extra arguments are forwarded to the app.

```bash
met run file:openchamber-1.2.2

# show what would be launched, without launching it
met run --resolve file:openchamber-1.2.2
```

### Utility Commands

#### `met --export FILE`
Save managed entries to a JSON backup (home paths stored portable as `~/...`).

```bash
met --export backup.json
```

#### `met --import FILE`
Restore managed entries from that file. Handmade files are never overwritten
(ours gets a `-2` sibling).

```bash
met --import backup.json
```

#### `met --check-updates`
Compare the installed version with the version on GitHub main.

```bash
met --check-updates
```

Interactive sessions also auto-check quietly after finishing and print a
one-line hint when a newer version exists. `--sync --all` and `--list`
never fetch anything.

## ⚙️ Configuration (`apps.yaml`)

First found wins: `~/.config/desktop-manager/apps.yaml`, then the repo's
`apps.yaml`, then built-in defaults.

```yaml
path_dirs:   # scanned flat, executables only
extra_dirs:  # scanned recursively, max_depth levels deep
max_depth: 3
desktop_files: {enabled: true}  # harvest Exec= from installed launchers
system_apps:  # system launchers hide by default; allowlist shows, blacklist hides more
  hide: true
  allowlist: []
  blacklist: []
flatpak: {enabled: true}
snap: {enabled: true}
appimage: {prefer_newest: true}  # newest AppImage wins, even while the old file exists
excludes:    # substrings or globs, e.g. uninstall, "*.so*", crashpad
target_dir: ~/.local/share/applications
```

`/usr/bin` stays excluded on purpose (2,400+ CLI tools would flood the picker).

### Location overrides (environment)

```bash
# Where the config file is looked up
export XDG_CONFIG_HOME=/custom/config        # $XDG_CONFIG_HOME/desktop-manager/apps.yaml

# Where state.json lives
export XDG_DATA_HOME=/custom/data            # $XDG_DATA_HOME/desktop-manager/state.json
```

## 💾 State

`state.json` (`~/.local/share/desktop-manager/state.json`) maps
`app_id → filename` so a deselect finds the right file to delete.

### Safety

- Only files carrying `X-Managed-By=met-desktop-manager` are ever deleted.
- Selections are matched by executable, not just ID: if discovery changes
  (e.g. an app moves from filesystem scan to launcher harvest), your
  selection is adopted, not deleted.
- Missing apps show as Broken when the program is gone and Kept when it
  is still on disk; deleting them needs an explicit extra confirmation.

### Self-updating AppImages

An app that replaces its own file on update (`OpenChamber-1.2.2.AppImage` →
`OpenChamber-1.2.3.AppImage`) would leave a launcher pointing at a deleted
file. AppImage launchers therefore name an app id instead of the file:

```ini
Exec=/home/you/.local/bin/desktop-manager run file:openchamber-1.2.2
X-Met-Binary=/home/you/Downloads/OpenChamber-1.2.2.AppImage
```

`met run` resolves the app at launch time. It uses `X-Met-Binary`, and when
that file is gone it adopts the newest `.AppImage` whose name matches minus
the version, then re-points the launcher and the state. Newest is decided by
*modification time*, never by name: `app-1.9.0` beats `app-1.10.2` as text,
which is wrong, and `2.2a` vs `2.2.1` has no universal answer. The updater
wrote the new file last, so its mtime wins.

Because the state is re-pointed too, the picker shows the app **still
ticked** after an update instead of a broken old row plus a new unticked
one. Running `met` also does this work up front: it repairs a launcher whose
file was already replaced, and converts a still-working AppImage launcher to
this form so a future update cannot break it.

The newest matching file is adopted even while the old one is still on disk,
so a download you never cleaned up cannot leave the icon on an old version.
Set `appimage.prefer_newest: false` in `apps.yaml` to repair only when the
recorded file is gone.

Plain binaries, flatpak and snap launchers keep their direct `Exec=`. They
keep working even if this tool is uninstalled, and only AppImages depend on
`met run`.

## 📊 Examples

### Move to a new distro

```bash
met --export backup.json        # old machine
met --import backup.json        # new machine, then run `met` once
```

## 🐛 Troubleshooting

### Command not found (after code changes)
```bash
pipx reinstall met-desktop-manager
```

### "No apps discovered. Check apps.yaml paths."
Point `path_dirs` / `extra_dirs` at folders that exist on your machine.

### WARNING: state.json is unreadable
State resets to empty: managed files become untracked. Move the file aside
to start clean, or restore it from a backup.

### [needs chmod +x] tag on an AppImage
met adds the exec bit itself on the next sync. Reported apps appear
in the summary as `made executable`.

### AppImage icon starts an old version
A newer file sits next to the old one, and the launcher still names the old
path. Run `met`, or just click the icon: the router adopts the newest match by
modification time and re-points the launcher. To stay on an old build, put
`appimage: {prefer_newest: false}` in `apps.yaml`.

### AppImage icon does nothing after an update
The app replaced its own file (or was renamed). Run `met`: it re-links the
launcher to the newest matching AppImage. To see what a launcher points at:

```bash
met run --resolve file:openchamber-1.2.2
```

If it prints nothing, the file was renamed rather than version-bumped: untick
the old entry and tick the new one once.

## 🤝 Contributing

### Development Setup

```bash
git clone https://github.com/ippokrates/met-desktop-manager.git
cd met-desktop-manager
python3 -m venv --prompt met .venv
source .venv/bin/activate
pip install -e ".[dev]"

.venv/bin/python -m pytest tests/ -q   # whole suite, tmp dirs only
```

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details.

---

<div align="center">
  Made with ❤️ by <a href="https://github.com/ippokrates">@ippokratis</a>
</div>
