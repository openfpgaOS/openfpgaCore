#!/bin/bash
#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------

# Build musl libc for RISC-V (rv32imafc / ilp32f)
# Downloads musl 1.2.5, cross-compiles, installs to this directory.

set -e

MUSL_VERSION=1.2.5
MUSL_URL="https://musl.libc.org/releases/musl-${MUSL_VERSION}.tar.gz"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"
INSTALL_DIR="${SCRIPT_DIR}"

# Auto-detect RISC-V toolchain prefix
if command -v riscv64-unknown-elf-gcc >/dev/null 2>&1; then
    CROSS=riscv64-unknown-elf-
elif command -v riscv64-elf-gcc >/dev/null 2>&1; then
    CROSS=riscv64-elf-
else
    echo "Error: No RISC-V toolchain found" >&2
    exit 1
fi

echo "Using toolchain: ${CROSS}gcc"
echo "Building musl ${MUSL_VERSION} for rv32imafc..."

# Download
mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"

if [ ! -f "musl-${MUSL_VERSION}.tar.gz" ]; then
    echo "Downloading musl ${MUSL_VERSION}..."
    curl -LO "${MUSL_URL}"
fi

# Extract
if [ ! -d "musl-${MUSL_VERSION}" ]; then
    tar xzf "musl-${MUSL_VERSION}.tar.gz"
fi

cd "musl-${MUSL_VERSION}"

# Patch setjmp/longjmp for single-precision float ABI (ilp32f).
# musl upstream only handles soft-float vs double-float; our rv32imafc
# target has 32-bit float registers (fsw/flw) not 64-bit (fsd/fld).
for f in src/setjmp/riscv32/setjmp.S src/setjmp/riscv32/longjmp.S; do
    if grep -q 'ifndef __riscv_float_abi_soft' "$f"; then
        echo "Patching $f for single-float ABI..."
        SAVE_OP="fsw"; LOAD_OP="flw"
        if echo "$f" | grep -q setjmp; then SAVE_OP="fsw"; else SAVE_OP="flw"; fi
        sed -i \
            -e 's/#ifndef __riscv_float_abi_soft/#ifdef __riscv_float_abi_single/' \
            -e "s/fsd \(fs[0-9]*\), *\([0-9]*\)/fsw \1, \2/g" \
            -e "s/fld \(fs[0-9]*\), *\([0-9]*\)/flw \1, \2/g" \
            "$f"
        # Fix offsets: single-float uses 4-byte stride, not 8-byte
        python3 -c "
import re, sys
with open('$f') as fh: text = fh.read()
def fix(m):
    op, reg, off = m.group(1), m.group(2), int(m.group(3))
    idx = (off - 56) // 8
    new_off = 56 + idx * 4
    return f'\t{op} {reg}, {new_off}(a0)'
text = re.sub(r'\t(f[sl]w) (fs\d+),\s*(\d+)\(a0\)', fix, text)
with open('$f', 'w') as fh: fh.write(text)
"
    fi
done

# Configure for rv32imafc bare-metal
CFLAGS="-march=rv32imafc -mabi=ilp32f -O2 -ffunction-sections -fdata-sections" \
./configure \
    --target=riscv32 \
    --prefix="${INSTALL_DIR}" \
    --syslibdir="${INSTALL_DIR}/lib" \
    --disable-shared \
    CROSS_COMPILE="${CROSS}" \
    CC="${CROSS}gcc"

# Build everything (libraries + crt object files) and install.
# `make all` is the default target and includes lib/crt1.o, lib/crti.o,
# lib/crtn.o, lib/Scrt1.o -- these are required for static-linking SDK
# apps that use the standard musl _start → __libc_start_main entry path.
make -j$(nproc) all
make install

# Sanity check: the install must include the crt object files. Without
# crt1.o, SDK apps can't be linked (no _start symbol).
for crt in crt1.o crti.o crtn.o; do
    if [ ! -f "${INSTALL_DIR}/lib/${crt}" ]; then
        echo "ERROR: ${INSTALL_DIR}/lib/${crt} missing after install" >&2
        echo "       SDK apps will fail to link without this file." >&2
        exit 1
    fi
done

echo ""
echo "musl ${MUSL_VERSION} installed to ${INSTALL_DIR}"
echo "  Headers:  ${INSTALL_DIR}/include/"
echo "  Library:  ${INSTALL_DIR}/lib/libc.a"
echo "  CRT:      ${INSTALL_DIR}/lib/crt1.o, crti.o, crtn.o"
