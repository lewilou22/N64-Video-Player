#!/usr/bin/env bash
# Bootstrap libdragon preview under WSL2 / Debian-based Linux.
# Run from anywhere; uses ~/libdragon-preview for the clone.
#
# Usage:
#   ./scripts/wsl_bootstrap_libdragon.sh
#   N64_INST=/opt/libdragon ./scripts/wsl_bootstrap_libdragon.sh   # explicit prefix
#
# Docs: docs/WSL2_LIBDRAGON_SETUP.md

set -euo pipefail

TOOLCHAIN_DEB_URL="https://github.com/DragonMinded/libdragon/releases/download/toolchain-continuous-prerelease/gcc-toolchain-mips64-x86_64.deb"
TOOLCHAIN_DEB_NAME="gcc-toolchain-mips64-x86_64.deb"
N64_INST="${N64_INST:-/opt/libdragon}"
LDG_CLONE="${LIBDRAGON_CLONE:-$HOME/libdragon-preview}"

if [[ -f /proc/version ]] && grep -qiE 'microsoft|wsl' /proc/version; then
  echo "[info] WSL detected."
else
  echo "[warn] This script targets WSL2 / Linux; /proc/version does not look like WSL."
fi

echo "[1/5] apt packages (build-essential, git, curl, python3, ffmpeg, tk)…"
sudo apt-get update
sudo apt-get install -y build-essential git curl python3 python3-tk ffmpeg

TMP_DEB="$(mktemp -t libdragon-toolchain-XXXXXX.deb)"
cleanup() { rm -f "$TMP_DEB"; }
trap cleanup EXIT

echo "[2/5] downloading toolchain .deb…"
curl -fsSL -o "$TMP_DEB" "$TOOLCHAIN_DEB_URL"

echo "[3/5] installing toolchain (sudo dpkg)…"
sudo dpkg -i "$TMP_DEB" || sudo apt-get install -f -y

echo "[4/5] ensure N64_INST in ~/.bashrc …"
MARK="# libdragon N64_INST (added by n64-video-rom wsl_bootstrap_libdragon.sh)"
if ! grep -qF "export N64_INST=" "$HOME/.bashrc" 2>/dev/null; then
  {
    echo ""
    echo "$MARK"
    echo "export N64_INST=\"$N64_INST\""
  } >>"$HOME/.bashrc"
  echo "[info] Appended export N64_INST=$N64_INST to ~/.bashrc"
else
  echo "[info] ~/.bashrc already mentions N64_INST — not modifying. Current value should be $N64_INST for this script."
fi

export N64_INST

if [[ ! -x "$N64_INST/bin/mips64-elf-gcc" ]]; then
  echo "[error] Expected MIPS gcc at $N64_INST/bin/mips64-elf-gcc — check toolchain install path."
  echo "        Set N64_INST to your real prefix and re-run, or fix ~/.bashrc."
  exit 1
fi

echo "[5/5] clone libdragon preview + ./build.sh …"
if [[ ! -d "$LDG_CLONE/.git" ]]; then
  git clone -b preview --depth 1 https://github.com/DragonMinded/libdragon.git "$LDG_CLONE"
else
  echo "[info] $LDG_CLONE already exists — not modifying git state (update with git pull if needed)."
fi

(
  cd "$LDG_CLONE"
  ./build.sh
)

echo ""
echo "Done. Open a NEW WSL terminal (or: source ~/.bashrc) so N64_INST is set."
echo "Verify:  echo \"\$N64_INST\"  &&  ls \"\$N64_INST/bin/audioconv64\" \"\$N64_INST/bin/videoconv64\""
echo "This repo:  cd /path/to/n64-video-rom && . ./env.sh && make -f Makefile.sd"
echo "See: docs/WSL2_LIBDRAGON_SETUP.md"
