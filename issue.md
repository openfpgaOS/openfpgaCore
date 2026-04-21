# Failing testdemo assertions after Zicbom / cached-FB landing

Post-boot app tests fail on BOTH UART-PHDP and SD boot paths, so these
are **OS-side bugs**, not boot-stream corruption. The PHDP trap that
started the session is already fixed (uncached alias in `boot.c`).

## Current failing assertions (testdemo)

1. `test_file.c:20`  `empty str`  `fopen("", "rb") == NULL`
2. `test_file.c:44`  `empty`      `fopen("", "rb") == NULL`
3. `test_lzw.c:16`   `compress ok`   `of_lzw_compress(pattern,256,...) > 0`
4. `test_lzw.c:28`   `zeros ok`      `of_lzw_compress(zeros,  256,...) > 0`
5. `test_lzw.c:47`   `rand  ok`      `of_lzw_compress(xorshift,256,...) > 0`
   -- **intermittent** (passes some runs, fails others)

---

## Bug 1 -- `fopen("")` succeeds

### Where
`src/firmware/os/kernel/syscall.c:584` `sys_openat`

```c
if (!path)
    return -EINVAL;            // NULL-guard only, no empty check
...
int slot = file_slot_lookup(path);   // line 641
```

### Why it matches
- `file_slot_lookup("")` -> `path_basename("")` returns `""`
- loops over `file_slots[]` doing `stricmp("", file_slots[i].filename)`
- `stricmp("", "")` returns 0 (both `while` guards fall through, final
  `char_lower('\0') - char_lower('\0') == 0`).
- So any slot registered with an **empty filename** matches `""`.
- `dir_probe_slots()` (syscall.c:314) has a fallback that synthesizes
  `"slot:0".."slot:9"` when APF won't return a filename, so in practice
  `file_slots[].filename` is never empty -- but a stricter contract is
  safer than relying on that invariant.

### Proposed fix
Add an explicit empty-path reject at the top of `sys_openat` (before
`alloc_fd()` so we don't leak an fd slot on the error path):

```c
if (!path || !path[0])
    return -ENOENT;
```

This matches Linux semantics (`open("", ...)` returns `-ENOENT`) and
removes the dependency on the FTAB never containing an empty string.

---

## Bug 2 -- LZW `compress ok` returns <= 0

### Symptoms
`of_lzw_compress()` returns -1 or 0 for 256-byte inputs. The xorshift
("rand") case is **intermittent**, which strongly implies an allocator
/ heap-state issue rather than a deterministic LZW bug.

### Where
`src/firmware/os/hal/lzw.c:42-57` -- calls kernel `dlmalloc` for three
work buffers:

```c
int bufsz   = LZWSIZE + (LZWSIZE >> 4);     // 17408
uint8_t *lzwbuf1 = malloc(bufsz);           // 17408
short   *lzwbuf2 = malloc(bufsz * 2);       // 34816
short   *lzwbuf3 = malloc(bufsz * 2);       // 34816   total ~84 KB
if (!lzwbuf1 || !lzwbuf2 || !lzwbuf3) {
    free(lzwbuf1); free(lzwbuf2); free(lzwbuf3);
    return -1;                               // <-- likely hit
}
```

`#define malloc dlmalloc` (lzw.c:16) routes through the **kernel**
dlmalloc in `kernel/of_malloc.c`, not through the app's musl.

### Suspected root cause: shared-brk / double-init heap corruption

`syscall_init(uintptr_t heap_start)` is called **twice**:

- `kernel/main.c:93`  at boot, `heap_start = __os_bss_end` (CRAM0)
- `kernel/main.c:130` at app load, `heap_start = app.bss_end` (SDRAM)

Each call does (syscall.c:1367-1379):
```c
brk_base    = heap_start;
current_brk = heap_start;
mmap_bottom = 0x13400000u;
```

But `dlmalloc`'s **internal malloc_state** (static BSS globals in
`kernel/dlmalloc.c`) is **not reset**. After the second `syscall_init`,
dlmalloc still thinks its heap segment starts at the old OS `bss_end`.
When it next calls `__kernel_sbrk`, it receives memory at the NEW
`current_brk` (the app's bss_end, tens of MB higher), and its internal
free-list / top-chunk pointers diverge from the actual memory range
returned by MORECORE.

Additionally, the **app's musl malloc is NOT overridden** in the app
binary -- the `malloc` override in `of_malloc.c` only affects the
kernel link. App and kernel each grow `current_brk` independently via
different paths (musl's `sbrk` syscall vs. `__kernel_sbrk`), so they
carve disjoint slabs out of the same brk region without knowing about
each other. That's fine as long as both allocators only extend -- but
dlmalloc's trim (`DEFAULT_TRIM_THRESHOLD = 256 KB`) **shrinks** brk via
`sbrk(-N)`, which would release pages musl may have already handed out
to the app.

### Why it's intermittent for "rand"
The three LZW allocations happen in sequence; whether all three
succeed depends on:
- how much brk the app's musl already consumed before the test
- whether dlmalloc's broken top-chunk happens to line up with a
  reachable address
- whether the previous test left dlmalloc's free list in a state where
  `bufsz*2` (34 KB) can be satisfied without a fresh MORECORE call

The first two tests (pattern, zeros) run back-to-back and reuse the
same buffers, so they pass/fail together. The third (rand) runs after
more app-side allocations, which is why it flips.

### Proposed investigation / fix (next session)
1. Instrument `of_lzw_compress`: print `current_brk`, `of_brk_limit()`,
   and which of the three `malloc` calls returned NULL.
2. Either:
   a. Reset dlmalloc state on every `syscall_init` (re-run
      `init_mparams` / zero the `gm` state), OR
   b. Stop using kernel `dlmalloc` from HAL code that runs inside
      syscalls -- preallocate the LZW buffers once at boot (they're
      ~84 KB, which fits in CRAM0 or a fixed SDRAM reservation), OR
   c. Route LZW through the app's heap via a small scratch arena
      passed in from userspace.
3. Disable the dlmalloc trim threshold (`DEFAULT_TRIM_THRESHOLD =
   MAX_SIZE_T`) at minimum, so the kernel never `sbrk(-N)`s pages
   musl may own.

Option (b) is the cleanest -- the buffers are fixed-size and the
"allocate every call" design is what creates the reentrancy into the
shared brk in the first place.

---

## Files touched this session (context for next)

- `src/firmware/os/targets/pocket/boot/boot.c:525` -- PHDP stream now
  writes via uncached alias `0x38xxxxxx`
- `src/firmware/os/targets/pocket/boot/boot.c` -- removed redundant
  `start_os: uart_mirror_on = 1` block (BRAM budget)
- `src/firmware/os/kernel/syscall.c` -- `of_term_uart_drain()` added
  to `timer_isr_callback` (unrelated to these failures)

## Files to read first next session

- `src/firmware/os/kernel/of_malloc.c`         -- kernel dlmalloc glue
- `src/firmware/os/kernel/dlmalloc.c`          -- allocator itself
- `src/firmware/os/kernel/syscall.c:1367-1380` -- `syscall_init`
- `src/firmware/os/kernel/main.c:85-135`       -- boot + app-load init
- `src/firmware/os/hal/lzw.c`                  -- allocation pattern
- `src/firmware/os/kernel/syscall.c:584-650`   -- `sys_openat`
