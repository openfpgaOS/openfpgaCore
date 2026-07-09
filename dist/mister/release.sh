#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# openfpgaOS — publish the MiSTer CORE release to GitHub.
#
# The "core" is the game-agnostic bitstream + OS kernel (openfpgaOS.rbf +
# boot.rom); games ship separately from their own repos.  `make release
# TARGET=mister` runs `make package` first (which builds
# releases/mister/openfpgaos-core-v<ver>.zip + the Downloader DB), then this
# script drafts the GitHub release.
#
#   tag    openfpgaos-mister-v<version>     (version from dist/core/core.json)
#   title  "openfpgaOS core for MiSTer v<version>"
#   assets releases/mister/openfpgaos-core-v<version>.zip
#          (+ openfpgaos.json.zip + openfpgaos.downloader.ini if present)
#
# Env:  PREV=<tag>   changelog baseline (default: previous openfpgaos-mister-v* tag)
#       PUBLISH=1    publish a live release instead of a draft (default: draft)
#

set -euo pipefail

GREEN='\033[92m'; CYAN='\033[96m'; YELLOW='\033[93m'; RED='\033[91m'; RESET='\033[0m'
err() { echo -e "${RED}Error:${RESET} $*" >&2; exit 1; }

# dist/mister/ -> repo root
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

command -v gh  >/dev/null 2>&1 || err "the GitHub CLI ('gh') is not installed."
command -v git >/dev/null 2>&1 || err "git is not installed."
gh auth status >/dev/null 2>&1 || err "not logged in to GitHub — run 'gh auth login'."

VER="${CORE_VER:-}"
[ -n "$VER" ] || VER="$(python3 -c "import json;print(json.load(open('dist/core/core.json'))['core']['metadata']['version'])" 2>/dev/null || true)"
[ -n "$VER" ] || err "could not read core version from dist/core/core.json."

ZIP="releases/mister/openfpgaos-core-v$VER.zip"
[ -f "$ZIP" ] || err "asset $ZIP not found — run 'make package TARGET=mister' first."

TAG="openfpgaos-mister-v$VER"
TITLE="openfpgaOS core for MiSTer v$VER"
PREV="${PREV:-}"
PUBLISH="${PUBLISH:-0}"

# Assets: the core zip, plus the Downloader DB + ini snippet if packaged.
ASSETS=("$ZIP")
[ -f "releases/mister/openfpgaos.json.zip" ]       && ASSETS+=("releases/mister/openfpgaos.json.zip")
[ -f "releases/mister/openfpgaos.downloader.ini" ] && ASSETS+=("releases/mister/openfpgaos.downloader.ini")

# Refuse to clobber an already-released version.
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null 2>&1; then
    err "tag $TAG already exists — bump 'version' in dist/core/core.json before releasing."
fi
if gh release view "$TAG" >/dev/null 2>&1; then
    err "release $TAG already exists on GitHub — bump 'version' in dist/core/core.json."
fi

# Release notes: commits since the previous core tag, or a first-release line.
[ -n "$PREV" ] || PREV="$(git tag --list 'openfpgaos-mister-v*' --sort=-version:refname | head -1)"

NOTES_FILE="$(mktemp)"
trap 'rm -f "$NOTES_FILE"' EXIT

if [ -z "$PREV" ]; then
    echo "First MiSTer release of the openfpgaOS core." > "$NOTES_FILE"
    RANGE="first release"
elif ! git rev-parse -q --verify "refs/tags/$PREV" >/dev/null 2>&1; then
    err "baseline tag '$PREV' does not exist (check PREV=)."
else
    git log --no-merges --pretty=format:'- %s' "$PREV..HEAD" > "$NOTES_FILE"
    [ -s "$NOTES_FILE" ] || echo "No changes since $PREV." > "$NOTES_FILE"
    RANGE="changes since $PREV"
fi

if [ -n "$(git status --porcelain)" ]; then
    echo -e "${YELLOW}Warning:${RESET} working tree has uncommitted changes; releasing from $(git rev-parse --short HEAD)."
fi

MODE_FLAG="--draft"; MODE_DESC="DRAFT (review and publish from the GitHub UI)"
if [ "$PUBLISH" = "1" ]; then MODE_FLAG="--latest"; MODE_DESC="LIVE"; fi

echo -e "${CYAN}-- Release ---------------------------------------${RESET}"
echo -e "  tag    : $TAG"
echo -e "  title  : $TITLE"
echo -e "  assets : ${ASSETS[*]}"
echo -e "  notes  : $RANGE"
echo -e "  mode   : $MODE_DESC"
echo -e "${CYAN}--------------------------------------------------${RESET}"
sed 's/^/    /' "$NOTES_FILE"
echo -e "${CYAN}--------------------------------------------------${RESET}"

gh release create "$TAG" "${ASSETS[@]}" \
    --target "$(git rev-parse HEAD)" \
    --title "$TITLE" \
    --notes-file "$NOTES_FILE" \
    $MODE_FLAG

echo -e "${GREEN}Release $TAG created ($MODE_DESC).${RESET}"
