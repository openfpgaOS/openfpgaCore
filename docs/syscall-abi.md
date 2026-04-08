# openfpgaOS Syscall ABI

openfpgaOS uses the **RISC-V Supervisor Binary Interface (SBI)** calling
convention for its custom syscalls, with a Linux-compatible fallback for
musl/POSIX code. This document specifies the ABI and the rules for
extending it.

The reference for SBI is the RISC-V SBI specification:
<https://github.com/riscv-non-isa/riscv-sbi-doc>.

---

## 1. Two namespaces, one `ecall` instruction

Both namespaces enter the kernel through the standard RISC-V `ecall`
instruction. The kernel discriminates by inspecting `a7` (the EID):

| `a7` range                              | Namespace                  | Convention   |
|------------------------------------------|---------------------------|--------------|
| `0x00000000` .. `0x000001FF`             | Linux RISC-V syscalls     | Linux ABI    |
| `0x00000200` .. `0x0FFFFFFF`             | Reserved (unused)         | —            |
| `0x10000000` .. `0x2FFFFFFF`             | Standard SBI extensions   | SBI ABI      |
| `0xC0DE0000` .. `0xC0DE00FF`             | **openfpgaOS vendor**     | SBI ABI      |

The vendor base `0xC0DE0000` has the high bit set, putting it
unambiguously above any plausible Linux syscall expansion (Linux RISC-V
currently tops out around 450) and well clear of the standard SBI
working-group range.

---

## 2. SBI calling convention (vendor extensions)

### Registers

| Register | Role on entry              | Role on return     |
|----------|----------------------------|--------------------|
| `a7`     | EID (extension ID)         | unspecified        |
| `a6`     | FID (function ID)          | unspecified        |
| `a0`     | argument 0                 | `sbiret.error`     |
| `a1`     | argument 1                 | `sbiret.value`     |
| `a2..a5` | arguments 2 .. 5           | unspecified        |

The kernel returns a 2-word `struct sbiret { long error; long value; }`,
which the RISC-V psABI places in `a0` / `a1` automatically.

### Error codes

`error` is `0` on success or one of the negative values from
`api/of_error.h`. The first 13 codes match the SBI standard exactly:

```
OF_OK                    =   0   /* SBI_SUCCESS              */
OF_ERR_FAILED            =  -1   /* SBI_ERR_FAILED           */
OF_ERR_NOT_SUPPORTED     =  -2   /* SBI_ERR_NOT_SUPPORTED    */
OF_ERR_INVALID_PARAM     =  -3   /* SBI_ERR_INVALID_PARAM    */
OF_ERR_DENIED            =  -4   /* SBI_ERR_DENIED           */
OF_ERR_INVALID_ADDRESS   =  -5   /* SBI_ERR_INVALID_ADDRESS  */
OF_ERR_ALREADY_AVAILABLE =  -6
OF_ERR_ALREADY_STARTED   =  -7
OF_ERR_ALREADY_STOPPED   =  -8
OF_ERR_NO_SHMEM          =  -9
OF_ERR_INVALID_STATE     = -10
OF_ERR_BAD_RANGE         = -11
OF_ERR_TIMEOUT           = -12
OF_ERR_IO                = -13
```

openfpgaOS-specific errors live below `-100` to avoid colliding with
future standard SBI codes.

### Convention used by the dispatcher today

To keep the SDK call sites simple, the kernel currently uses this
shape for vendor calls:

- **Success that returns a value** → `error = 0`, `value = the result`
- **Failure**                       → `error = OF_ERR_*`, `value = 0`

SDK wrappers therefore read `.value` for the call's natural return and
check `.error` for failure detection (or ignore it if the call cannot
fail). See the inline wrappers in `api/of_*.h` for examples.

### Calling convention from C

The wrappers in `api/of_syscall.h` handle the register layout for you:

```c
#include "of_syscall.h"
#include "of_syscall_numbers.h"

/* No-arg call returning a value */
struct of_sbiret r = of_ecall0(OF_EID_BASE, OF_BASE_FID_GET_VERSION);
uint32_t version = (uint32_t)r.value;

/* Three-arg call */
struct of_sbiret r = of_ecall3(OF_EID_LZW, OF_LZW_FID_COMPRESS,
                               (long)src, (long)src_len, (long)dst);
int32_t out_len = (int32_t)r.value;
```

The wrappers come in `of_ecall0()` through `of_ecall6()` for 0..6
arguments. Six is the hard maximum (a0..a5) — anything that wants more
must pass a struct pointer.

---

## 3. Linux-compat calling convention

Linux-built code (musl, ports) uses the historic Linux RISC-V ABI:

| Register | Role on entry        | Role on return |
|----------|----------------------|----------------|
| `a7`     | syscall number       | unspecified    |
| `a0..a5` | arguments 0..5       | `a0` = return  |

The return value goes in `a0` alone. The kernel routes these through
`linux_dispatch()` in `kernel/syscall.c` and writes the return into
`sbiret.error` so that the trap handler still presents it in `a0` to
musl. `a1` is clobbered, which is permitted by the RISC-V ABI
(caller-saved return registers).

Statically linked musl in apps emits these directly through its own
`__syscall` macro -- no SDK helper needed. The wrappers in
`of_syscall.h` (`__of_linux_syscall*`) exist only for the openfpgaOS
SDK's own use of `SYS_exit` from `of.h::of_exit()`.

---

## 4. Extending the ABI

### Adding a new function to an existing subsystem

1. Append the new FID to the matching `enum of_<subsys>_fid` in
   `api/of_syscall_numbers.h`. **Append only** — never insert in the
   middle of the list, never reuse a retired number, never reorder.
2. Add a `case OF_<SUBSYS>_FID_<NAME>:` to the corresponding switch in
   `kernel/syscall.c::of_vendor_dispatch()` (or in the per-subsystem
   helper for video / audio / input / net).
3. Add an inline wrapper in the SDK header that uses `of_ecallN()` so
   apps don't need to know the EID/FID values:

   ```c
   static inline int of_widget_frob(int x) {
       return (int)of_ecall1(OF_EID_WIDGET, OF_WIDGET_FID_FROB, x).value;
   }
   ```

### Adding a new subsystem

1. Append a new `OF_EID_<NAME>` at the end of the EID block in
   `api/of_syscall_numbers.h`. **Append only** — never insert in the
   middle, never reuse a retired EID.
2. Define a fresh `enum of_<name>_fid` starting at `0`.
3. Add a top-level `case OF_EID_<NAME>:` arm in
   `kernel/syscall.c::of_vendor_dispatch()`.
4. Create a new `api/of_<name>.h` SDK header with `static inline`
   wrappers built on `of_ecallN()`.

### Retiring a function

Mark its FID slot with a `_RETIRED_*` enumerator. Do not delete the
slot — old binaries still call it by number. The kernel can return
`OF_ERR_NOT_SUPPORTED` from the dispatcher case.

### Retiring an entire subsystem

Same rule: leave the EID in place, mark it `_RETIRED`, and have its
dispatcher case return `OF_ERR_NOT_SUPPORTED` for every FID. See
`OF_EID_TILE` and `OF_EID_SPRITE` for the canonical example.

---

## 5. Why SBI, why these specific choices

- **Already on RISC-V.** SBI is the supervisor↔firmware ABI defined by
  the RISC-V foundation. There is no impedance mismatch.
- **`a6` was free.** The Linux RISC-V ABI uses `a0..a5` plus `a7`. By
  putting the FID in `a6` we don't disturb any existing user-side
  register conventions and we get a full 32-bit FID space per EID.
- **No more cramped subsystem zones.** The pre-SBI numbering packed
  subsystem and function into a single `a7`, in 16-slot windows
  (`0x10D0`..`0x10DF` for mixer, etc.). The mixer ran out of slots.
  With per-EID FID enums there are 2³² function slots per subsystem.
- **Clean error/value separation.** SBI's `{error, value}` pair removes
  the "negative is error" hack from the contract — return values can
  legitimately be negative without confusion.
- **Vendor range is high-bit-set.** `0xC0DE0000` cannot collide with
  Linux syscall numbers (which are positive small integers) or with
  future standard SBI extensions assigned by the working group (which
  live in the low `0x10..0x2F` range).

---

## 6. File map

| File                                       | Role                                     |
|--------------------------------------------|------------------------------------------|
| `src/firmware/api/of_syscall.h`            | `of_ecallN()` wrappers, `sbiret` struct  |
| `src/firmware/api/of_syscall_numbers.h`    | EID constants, per-subsystem FID enums   |
| `src/firmware/api/of_error.h`              | SBI-aligned error codes                  |
| `src/firmware/api/of_*.h`                  | SDK inline wrappers per subsystem        |
| `src/firmware/os/targets/pocket/boot/start.S` | Trap handler (loads a6/a7, calls C)   |
| `src/firmware/os/kernel/syscall.c`         | `syscall_dispatch`, vendor + Linux paths |
| `src/firmware/os/kernel/syscall.h`         | Public dispatcher signature              |
