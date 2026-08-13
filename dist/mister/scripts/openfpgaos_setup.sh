#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# openfpgaOS one-button setup — run every installed game's setup.sh.
#
# Lives in /media/fat/Scripts/ (delivered by the core's Downloader DB) so it
# shows up in the OSD Scripts menu.  Each game's own setup.sh is idempotent
# (injects new wads, seeds saves once, republishes launchers), so running
# this after every update_all — or on every boot — is safe and cheap.
#
#   openfpgaos_setup.sh                run setup for every installed game
#   openfpgaos_setup.sh --install-boot-hook
#                                      also append this runner to
#                                      /media/fat/linux/user-startup.sh so it
#                                      runs automatically at every boot
#                                      (added once; your file is not replaced)
#------------------------------------------------------------------------------
set -u

ROOT=/media/fat/games/OpenfpgaOS
STARTUP=/media/fat/linux/user-startup.sh
HOOK='/media/fat/Scripts/openfpgaos_setup.sh # openfpgaos auto-setup'

if [ "${1:-}" = "--install-boot-hook" ]; then
    if grep -q "openfpgaos auto-setup" "$STARTUP" 2>/dev/null; then
        echo "Boot hook already installed in $STARTUP"
    else
        { [ -f "$STARTUP" ] || printf '#!/bin/sh\n'; cat "$STARTUP" 2>/dev/null; } > /tmp/us.new
        printf '%s\n' "$HOOK" >> /tmp/us.new
        mv /tmp/us.new "$STARTUP" && chmod +x "$STARTUP"
        echo "Boot hook installed: setup now runs automatically at every boot."
    fi
fi

ran=0
for s in "$ROOT"/*/setup.sh; do
    [ -x "$s" ] || continue
    g="$(basename "$(dirname "$s")")"
    echo "== $g"
    ( cd "$(dirname "$s")" && ./setup.sh ) || echo "  [!] $g setup reported a problem — see its setup.log"
    ran=$((ran+1))
done

if [ "$ran" = 0 ]; then
    echo "No openfpgaOS games installed yet (nothing in $ROOT/*/setup.sh)."
    echo "Install game packages first, then re-run this."
else
    echo "Done: $ran game(s) processed."
fi
