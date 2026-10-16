#!/bin/sh
# Installer and updater for MET Desktop Manager.
# Same script handles both: first install and re-run to update.
#
# One-liner (what the README shows):
#   curl -fsSL https://raw.githubusercontent.com/ippokrates/met-desktop-manager/main/install.sh | bash
set -eu

REPO="git+https://github.com/ippokrates/met-desktop-manager.git"

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n\n' "$*" >&2; exit 1; }

# 1. Python 3.12+ (project requirement)
if ! command -v python3 >/dev/null 2>&1; then
    die "python3 is required. Install it with your package manager first."
fi
PYVERS="$(python3 -c 'import sys; print(sys.version_info[0]*100+sys.version_info[1])')"
if [ "$PYVERS" -lt 312 ]; then
    die "met-desktop-manager needs Python 3.12+. Found an older python3 ($PYVERS)."
fi

# 2. pipx (installs the app in its own venv and puts the commands on PATH)
if ! command -v pipx >/dev/null 2>&1; then
    die "pipx is required. Install it, then re-run this script:
    sudo apt install pipx && pipx ensurepath   # Debian / Ubuntu
    sudo dnf install pipx                      # Fedora
    sudo pacman -S python-pipx                 # Arch
    See https://pipx.pypa.io/ for other systems."
fi

# 3. Install (first time) / reinstall over the old version (updates)
say "Installing met-desktop-manager via pipx..."
pipx install --force "$REPO" || die "pipx install failed. Check your network and the repo URL."

say ""
say "Done. Try:  met --list"
say "If 'met' is not found, your PATH is missing the pipx bin dir; run:  pipx ensurepath"
