#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# openfpgaOS MiSTer setup — assemble the vhd images the Downloader can't ship.
#
# The Downloader database delivers the core, os.bin, and freeware instance
# shells.  The COMMERCIAL content — the IWADs (doom2/plutonia/tnt/doom/
# ultimatedoom.wad) that live in the family image, and the PCM music derived
# from them — cannot be distributed.  Drop your own IWADs into
#   /media/fat/games/openfpgaOS/wads/
# then run this from the MiSTer Scripts menu.  It builds:
#   vhd/doom.vhd        family library (S0) — your IWADs + engine + os.ini
#   vhd/doommusic.vhd   PCM music (S2)      — DOOM*MUS.wad, if you provide them
# and injects any missing IWAD into the freeware instance shells that need it.
#
# Uses only loop-mount + cp + dd (all present on MiSTer's kernel) — no host
# tools.  Idempotent: re-running only rebuilds what's missing or stale.
#
# NOTE: draft.  Preallocation of /config and /saves in freshly-dd'd images is
# left to the release-time shells (built by the SDK's mkimage, which f_expand's
# them contiguously for the power-cut-safe write contract).  On-device we only
# COPY wads into pre-formatted shells; we do not create nvslot files here.
#------------------------------------------------------------------------------
set -u

ROOT=/media/fat/games/openfpgaOS
WADS="$ROOT/wads"
VHD="$ROOT/vhd"
MNT=/tmp/ofs_setup_mnt
LOG="$ROOT/setup.log"

ok()   { echo "  [+] $1" | tee -a "$LOG"; }
warn() { echo "  [!] $1" | tee -a "$LOG"; }
die()  { echo "  [x] $1" | tee -a "$LOG"; exit 1; }

: > "$LOG"
echo "openfpgaOS setup — $(date)" | tee -a "$LOG"
[ -d "$WADS" ] || die "drop your IWADs in $WADS first"
mkdir -p "$VHD" "$MNT"

# Case-insensitive locate of a wad the user supplied.
find_wad() { find "$WADS" -maxdepth 1 -iname "$1" 2>/dev/null | head -1; }

# Loop-mount helper (rw) with cleanup.
mnt_rw()  { umount "$MNT" 2>/dev/null; mount -o loop "$1" "$MNT" 2>/dev/null; }
mnt_ro()  { umount "$MNT" 2>/dev/null; mount -o loop,ro "$1" "$MNT" 2>/dev/null; }
unmnt()   { sync; umount "$MNT" 2>/dev/null; }

# Copy an IWAD into a mounted image's /assets if the user has it.
inject_iwad() {
    local wad="$1"                       # e.g. DOOM2.WAD
    local src; src=$(find_wad "$wad")
    if [ -z "$src" ]; then warn "missing $wad — skipped"; return 1; fi
    cp "$src" "$MNT/assets/$wad" && ok "injected $wad ($(stat -c%s "$src") B)"
}

# ── 1. Family image (S0): IWADs + engine ────────────────────────────────────
# Ships as a pre-formatted shell `vhd/doom.shell.vhd` (engine + os.ini +
# preallocated config/saves, no IWADs).  We copy it to doom.vhd and inject the
# user's IWADs.  (If no shell is present the release is incomplete.)
build_family() {
    local shell="$VHD/doom.shell.vhd"
    [ -f "$shell" ] || { warn "no family shell ($shell) — skipping family build"; return; }
    if [ -f "$VHD/doom.vhd" ] && [ "$VHD/doom.vhd" -nt "$shell" ]; then
        ok "family doom.vhd up to date"; return
    fi
    cp "$shell" "$VHD/doom.vhd" || die "cp family shell failed"
    mnt_rw "$VHD/doom.vhd" || die "mount family failed"
    local got=0
    for w in DOOM.WAD DOOM2.WAD PLUTONIA.WAD TNT.WAD doomu.wad; do
        inject_iwad "$w" && got=$((got+1))
    done
    unmnt
    [ "$got" -gt 0 ] && ok "family built with $got IWAD(s)" || warn "family built but NO IWADs found"
}

# ── 2. PCM music (S2): DOOM*MUS.wad ─────────────────────────────────────────
build_music() {
    local shell="$VHD/doommusic.shell.vhd"
    local has=0
    for m in DOOMMUS.WAD DOOM2MUS.WAD; do [ -n "$(find_wad "$m")" ] && has=1; done
    [ "$has" = 1 ] || { warn "no *MUS.wad provided — CD music skipped (MIDI still works)"; return; }
    [ -f "$shell" ] || { warn "no music shell — skipping"; return; }
    cp "$shell" "$VHD/doommusic.vhd" || die "cp music shell failed"
    mnt_rw "$VHD/doommusic.vhd" || die "mount music failed"
    for m in DOOMMUS.WAD DOOM2MUS.WAD; do inject_iwad "$m"; done
    unmnt
    ok "doommusic.vhd built"
}

# ── 3. Instances needing a commercial IWAD ──────────────────────────────────
# Freeware instance shells that -merge a mod onto a commercial IWAD carry the
# mod (freeware) but need the IWAD.  The instance's os.ini -iwad points at the
# family (S0) copy, so nothing to inject here IF the instance is always played
# with doom.vhd mounted on S0.  Standalone freeware (freedoom, rekkr) needs
# nothing.  Left as a no-op stub for the PR to refine per the final os.ini
# volume-search policy.
finish_instances() { ok "instances: no per-instance IWAD injection needed (IWADs resolve from S0)"; }

build_family
build_music
finish_instances
umount "$MNT" 2>/dev/null; rmdir "$MNT" 2>/dev/null
echo "openfpgaOS setup done — see $LOG" | tee -a "$LOG"
