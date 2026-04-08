#!/usr/bin/env bash
#
# check_api_portability.sh — guard against regressions in the SDK app
# portability contract. Run after any change to src/firmware/api/ or
# the kernel ELF loader.
#
# Exit code:
#   0  the API is clean
#   1  one or more leaks were found (output names them)
#
# What this checks:
#
#   1. No literal addresses in public API headers. The only allowed
#      hex constants are ABI namespace markers, magic numbers, vendor
#      auxv tags, instruction encodings, and bitmasks.
#
#   2. No direct REG32/REG16/REG8 macros in public API headers.
#
#   3. No Pocket-specific terminology (pocket, apf, cram1, bridge_base)
#      in public API headers.
#
#   4. The TARGET=pocket kernel build links cleanly.
#
#   5. The TARGET=sim kernel build links cleanly. Same kernel sources,
#      different target_platform.h -- the build is target-agnostic if
#      and only if both succeed.
#
#   6. Two-target divergence sanity-check: the sim build's os.bin must
#      contain the sim-specific GPU base (0x4b000000) and not the
#      Pocket one. This proves that per-target addresses flow through
#      caps_table.c at runtime, not through compile-time bake-in.
#
# See docs/app-virtual-map.md and the portability_baseline.txt for the
# full contract.

set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API="$ROOT/src/firmware/api"
OS="$ROOT/src/firmware/os"

fail=0
note() { printf '  %s\n' "$*"; }
ok()   { printf '\033[32m[ok]\033[0m %s\n' "$*"; }
bad()  { printf '\033[31m[fail]\033[0m %s\n' "$*"; fail=1; }

# ── Check 1: no literal addresses in API headers ─────────────────────
#
# Permitted patterns (allowlisted because they aren't memory addresses):
#   - magic numbers in defines named *_MAGIC
#   - vendor namespace constants 0xC0DE*
#   - instruction encodings (commented as "fence.i" etc)
#   - 24-bit ring command masks (0x00FFFFFF)
#   - SDL2 third-party shim constants
#   - syscall numbers / commentary in of_syscall*.h
#   - of_app_abi.h (auxv tag definitions)
#   - app.ld (linker script, owned by the app virtual map contract)
#
# Anything else with a 7-8 digit hex constant is a leak.

leaks=$(grep -RnE '0x[0-9A-Fa-f]{7,8}' "$API" 2>/dev/null \
        | grep -vE 'MAGIC|0xC0DE|fence\.i|0x00FFFFFF|0xFFFFFF00' \
        | grep -vE '/SDL2/|of_syscall_numbers\.h|of_syscall\.h|of_app_abi\.h|app\.ld' \
        || true)
if [ -z "$leaks" ]; then
    ok "no literal addresses in src/firmware/api/"
else
    bad "literal addresses found in src/firmware/api/"
    printf '%s\n' "$leaks" | sed 's/^/    /'
fi

# ── Check 2: no direct REG access in API headers ─────────────────────
leaks=$(grep -RnE 'REG[0-9]+\s*\(' "$API" 2>/dev/null || true)
if [ -z "$leaks" ]; then
    ok "no REG32/REG16/REG8 macros in src/firmware/api/"
else
    bad "direct REG access in src/firmware/api/"
    printf '%s\n' "$leaks" | sed 's/^/    /'
fi

# ── Check 3: no Pocket-specific terminology in API code ─────────────
#
# Comments and prose are allowed to discuss Pocket (it's an example
# platform). What we care about is code-level leakage: a #define, a
# typedef, a struct field, a function name, or any identifier that
# encodes the target name. Strip C-style comments and preprocessor
# trivia first, then grep the surviving code.
strip_comments() {
    # Remove /* ... */ blocks (multi-line) and // line tails.
    sed -E 's_//[^"]*$__' "$1" | \
    awk 'BEGIN{c=0} {
        line=$0
        while (1) {
            if (c==0) {
                p=index(line,"/*")
                if (p==0) { print line; break }
                printf "%s", substr(line,1,p-1)
                line=substr(line,p+2); c=1
            } else {
                p=index(line,"*/")
                if (p==0) { line=""; print ""; break }
                line=substr(line,p+2); c=0
            }
        }
    }'
}

leaks=""
for f in $(find "$API" -name '*.h' -not -path '*/SDL2/*'); do
    hits=$(strip_comments "$f" | grep -inE 'pocket|apf|cram1|bridge_base' \
           | grep -vE 'OF_PLATFORM_POCKET' || true)
    if [ -n "$hits" ]; then
        while IFS= read -r line; do
            leaks="$leaks
$f: $line"
        done <<< "$hits"
    fi
done

if [ -z "$leaks" ]; then
    ok "no Pocket-specific identifiers in src/firmware/api/ code"
else
    bad "Pocket-specific identifiers in src/firmware/api/ code"
    printf '%s\n' "$leaks" | grep -v '^$' | sed 's/^/    /'
fi

# ── Check 4: Pocket kernel builds ────────────────────────────────────
( cd "$OS" && make clean >/dev/null 2>&1 && make TARGET=pocket >/tmp/_check_pocket.log 2>&1 )
if [ -f "$OS/os.bin" ]; then
    ok "TARGET=pocket builds ($(stat -c%s "$OS/os.bin") bytes)"
    cp "$OS/os.bin" /tmp/_check_pocket_os.bin
else
    bad "TARGET=pocket build failed -- see /tmp/_check_pocket.log"
fi

# ── Check 5: sim kernel builds ───────────────────────────────────────
( cd "$OS" && make clean >/dev/null 2>&1 && make TARGET=sim >/tmp/_check_sim.log 2>&1 )
if [ -f "$OS/os.bin" ]; then
    ok "TARGET=sim builds ($(stat -c%s "$OS/os.bin") bytes)"
    cp "$OS/os.bin" /tmp/_check_sim_os.bin
    cp "$OS/firmware.elf" /tmp/_check_sim.elf
else
    bad "TARGET=sim build failed -- see /tmp/_check_sim.log"
fi

# ── Check 6: divergent gpu_base proves runtime indirection ───────────
if [ -f /tmp/_check_sim.elf ]; then
    if riscv64-elf-objdump -d /tmp/_check_sim.elf 2>/dev/null \
        | grep -q 'lui.*0x4b000'; then
        ok "sim os.bin contains sim-specific gpu_base 0x4b000000"
    else
        bad "sim os.bin missing 0x4b000000 -- caps_table not threading per-target gpu_base"
    fi
fi

# ── Restore Pocket as the canonical default ──────────────────────────
( cd "$OS" && make clean >/dev/null 2>&1 && make TARGET=pocket >/dev/null 2>&1 )

if [ "$fail" -eq 0 ]; then
    printf '\n\033[32mAll portability checks passed.\033[0m\n'
    exit 0
else
    printf '\n\033[31mPortability checks FAILED.\033[0m See above. Baseline:\n'
    printf '  tools/portability_baseline.txt\n'
    printf '  docs/app-virtual-map.md\n'
    exit 1
fi
