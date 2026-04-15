# openfpgaOS Syscall Reference

Applications communicate with the OS kernel via the RISC-V `ecall` instruction. The syscall number is placed in register `a7`, arguments in `a0`-`a5`, and the return value is in `a0`.

## Syscall Convention

```
a7 = syscall number
a0 = argument 0 / return value
a1 = argument 1
a2 = argument 2
a3 = argument 3
a4 = argument 4
a5 = argument 5
```

On return, `a0` contains the result. Negative values indicate errors (negated errno).

## Linux-Compatible Syscalls

These use standard RISC-V Linux syscall numbers for musl libc compatibility.

| Number | Name | Signature | Notes |
|--------|------|-----------|-------|
| 17 | `getcwd` | -- | Not implemented (returns -ENOSYS) |
| 56 | `openat` | `(dirfd, pathname, flags, mode)` | Opens files: `"filename"` (registered lookup), `"save_N"` / `"save:N"`, `"slot:N"` |
| 57 | `close` | `(fd)` | Closes file descriptor. Save files auto-flush actual bytes written. |
| 62 | `_llseek` | `(fd, off_hi, off_lo, &result, whence)` | riscv32 uses 5-arg `_llseek`, not 3-arg `lseek`. SEEK_SET=0, SEEK_CUR=1, SEEK_END=2. Returns 0 on success, writes 64-bit result to pointer. |
| 63 | `read` | `(fd, buf, count)` | Reads from data slot or save file. Uses 64 KB read-ahead buffer. |
| 64 | `write` | `(fd, buf, count)` | stdout/stderr to terminal, save files to CRAM1 |
| 65 | `readv` | `(fd, iov, iovcnt)` | Vectored read (required by musl fread) |
| 66 | `writev` | `(fd, iov, iovcnt)` | Vectored write |
| 79 | `fstatat` | -- | Not implemented (returns -ENOSYS) |
| 80 | `fstat` | `(fd, statbuf)` | Fills st_size from fd table |
| 93 | `exit` | `(status)` | Halts CPU (does not return to OS) |
| 94 | `exit_group` | `(status)` | Same as exit |
| 96 | `set_tid_address` | -- | Returns 1 (stub) |
| 113 | `clock_gettime` | `(clk_id, tp)` | Hardware cycle counter, 10 ns resolution |
| 114 | `clock_getres` | `(clk_id, tp)` | Returns 10 ns resolution |
| 130 | `tkill` | -- | No-op (returns 0) |
| 134 | `rt_sigaction` | -- | No-op (returns 0) |
| 135 | `rt_sigprocmask` | -- | No-op (returns 0) |
| 172 | `getpid` | -- | Returns 1 |
| 178 | `gettid` | -- | Returns 1 |
| 214 | `brk` | `(addr)` | Heap management; 0 returns current brk |
| 215 | `munmap` | `(addr, length)` | No-op (memory not reclaimed) |
| 222 | `mmap2` | `(addr, len, prot, flags, fd, pgoff)` | Anonymous bump allocation |
| 226 | `mprotect` | -- | No-op (no MMU) |
| 233 | `madvise` | -- | No-op |
| 259 | `riscv_flush_icache` | -- | Executes fence.i |
| 291 | `statx` | `(dirfd, path, flags, mask, statxbuf)` | Minimal statx for riscv32 fstat |
| 403 | `clock_gettime64` | `(clk_id, tp)` | riscv32 musl sends this instead of 113 |
| 406 | `clock_getres64` | `(clk_id, tp)` | riscv32 variant of clock_getres |
| 422 | `futex` | -- | Returns 0 (stub for musl thread init) |

## openfpgaOS HAL Syscalls (0x1000+)

Custom syscall range for direct hardware access. Defined in `of_syscall_numbers.h`.

### Video (0x1000-0x1009)

| Number | Name | Arguments | Returns |
|--------|------|-----------|---------|
| 0x1000 | `VIDEO_INIT` | -- | 0 |
| 0x1001 | `VIDEO_FLIP` | -- | 0 |
| 0x1002 | `VIDEO_WAIT_FLIP` | -- | 0 (blocks until vsync) |
| 0x1003 | `VIDEO_SET_PALETTE` | `a0`=index (0-255), `a1`=0x00RRGGBB | 0 |
| 0x1004 | `VIDEO_GET_SURFACE` | -- | Pointer to draw buffer |
| 0x1005 | `VIDEO_SET_DISPLAY_MODE` | `a0`=mode (0=terminal, 1=framebuffer) | 0 |
| 0x1006 | `VIDEO_CLEAR` | `a0`=color index | 0 |
| 0x1007 | `VIDEO_SET_PALETTE_BULK` | `a0`=palette ptr, `a1`=count | 0 |
| 0x1008 | `VIDEO_FLUSH_CACHE` | -- | 0 (flushes D-cache) |
| 0x1009 | `VIDEO_SET_COLOR_MODE` | `a0`=mode (0-5) | 0 |

Color modes: 0=8-bit indexed, 1=4-bit indexed, 2=2-bit indexed, 3=RGB565, 4=RGB555, 5=RGBA5551.

### Audio (0x1010-0x1014)

| Number | Name | Arguments | Returns |
|--------|------|-----------|---------|
| 0x1010 | `AUDIO_WRITE` | `a0`=samples ptr (int16_t stereo), `a1`=count | Samples written |
| 0x1011 | `AUDIO_GET_FREE` | -- | Free space in FIFO |
| 0x1014 | `AUDIO_INIT` | -- | 0 |

### Input (0x1020-0x1022)

| Number | Name | Arguments | Returns |
|--------|------|-----------|---------|
| 0x1020 | `INPUT_POLL` | -- | 0 |
| 0x1021 | `INPUT_GET_STATE` | `a0`=player (0-1), `a1`=state ptr | Button bitmask |
| 0x1022 | `INPUT_SET_DEADZONE` | `a0`=deadzone value | 0 |

### Save (0x1030-0x1034)

| Number | Name | Arguments | Returns |
|--------|------|-----------|---------|
| 0x1030 | `SAVE_READ` | `a0`=slot (0-9), `a1`=buf, `a2`=offset, `a3`=len | Bytes read |
| 0x1031 | `SAVE_WRITE` | `a0`=slot (0-9), `a1`=buf, `a2`=offset, `a3`=len | Bytes written |
| 0x1032 | `SAVE_FLUSH` | `a0`=slot | 0 (flushes full 256 KB) |
| 0x1033 | `SAVE_ERASE` | `a0`=slot | 0 |
| 0x1034 | `SAVE_FLUSH_SIZE` | `a0`=slot, `a1`=size | 0 (flushes specified bytes) |

### Analogizer (0x1040-0x1041)

| Number | Name | Arguments | Returns |
|--------|------|-----------|---------|
| 0x1040 | `ANALOGIZER_GET_STATE` | `a0`=state ptr | Enabled (0/1) |
| 0x1041 | `ANALOGIZER_IS_ENABLED` | -- | Enabled (0/1) |

### Terminal (0x1050-0x1053)

| Number | Name | Arguments | Returns |
|--------|------|-----------|---------|
| 0x1050 | `TERM_PUTCHAR` | `a0`=character (CP437) | 0 |
| 0x1051 | `TERM_CLEAR` | -- | 0 |
| 0x1052 | `TERM_PRINTF` | `a0`=format string ptr | 0 |
| 0x1053 | `TERM_SET_POS` | `a0`=col, `a1`=row | 0 |

### Link Cable (0x1060-0x1062)

| Number | Name | Arguments | Returns |
|--------|------|-----------|---------|
| 0x1060 | `LINK_SEND` | `a0`=data (32-bit) | 0 on success |
| 0x1061 | `LINK_RECV` | `a0`=data ptr | 0 on success |
| 0x1062 | `LINK_GET_STATUS` | -- | Status word |

### Timer (0x1070-0x1073)

| Number | Name | Arguments | Returns |
|--------|------|-----------|---------|
| 0x1070 | `TIMER_GET_US` | -- | Microseconds since boot |
| 0x1071 | `TIMER_GET_MS` | -- | Milliseconds since boot |
| 0x1072 | `TIMER_DELAY_US` | `a0`=microseconds | 0 |
| 0x1073 | `TIMER_DELAY_MS` | `a0`=milliseconds | 0 |

### File I/O (0x1080-0x1081)

| Number | Name | Arguments | Returns |
|--------|------|-----------|---------|
| 0x1080 | `FILE_READ` | `a0`=slot_id, `a1`=offset, `a2`=dest ptr, `a3`=length | Bytes read |
| 0x1081 | `FILE_SIZE` | `a0`=slot_id | File size in bytes |

### Tile Engine (0x1090-0x1094)

| Number | Name | Arguments | Returns |
|--------|------|-----------|---------|
| 0x1090 | `TILE_ENABLE` | `a0`=enable (0/1) | 0 |
| 0x1091 | `TILE_SCROLL` | `a0`=scroll_x, `a1`=scroll_y | 0 |
| 0x1092 | `TILE_SET` | `a0`=col, `a1`=row, `a2`=tile_index | 0 |
| 0x1093 | `TILE_LOAD_MAP` | `a0`=map data ptr, `a1`=count | 0 |
| 0x1094 | `TILE_LOAD_CHR` | `a0`=chr data ptr, `a1`=size | 0 |

### Sprite Engine (0x10A0-0x10A5)

| Number | Name | Arguments | Returns |
|--------|------|-----------|---------|
| 0x10A0 | `SPRITE_ENABLE` | `a0`=enable (0/1) | 0 |
| 0x10A1 | `SPRITE_SET` | `a0`=id, `a1`=tile, `a2`=palette, `a3`=flip_h, `a4`=flip_v | 0 |
| 0x10A2 | `SPRITE_MOVE` | `a0`=id, `a1`=x, `a2`=y | 0 |
| 0x10A3 | `SPRITE_LOAD_CHR` | `a0`=chr data ptr, `a1`=size | 0 |
| 0x10A4 | `SPRITE_HIDE` | `a0`=id | 0 |
| 0x10A5 | `SPRITE_HIDE_ALL` | -- | 0 |

### Version & File Slots (0x10B0-0x10B6)

| Number | Name | Arguments | Returns |
|--------|------|-----------|---------|
| 0x10B0 | `GET_VERSION` | -- | API version (major.minor.patch packed 24-bit) |
| 0x10B1 | `FILE_SLOT_COUNT` | -- | Number of registered file slots |
| 0x10B2 | `FILE_SLOT_GET` | `a0`=index, `a1`=slot struct ptr | 0 on success |
| 0x10B3 | `FILE_SLOT_REGISTER` | `a0`=slot_id, `a1`=filename ptr | 0 |
| 0x10B4 | `SET_IDLE_HOOK` | `a0`=callback ptr (or NULL) | 0 |
| 0x10B5 | `AUDIO_ENQUEUE` | `a0`=samples ptr, `a1`=count | Samples enqueued |
| 0x10B6 | `AUDIO_RING_FREE` | -- | Free space in ring buffer |

### Memory Allocation (0x10C0-0x10C3)

| Number | Name | Arguments | Returns |
|--------|------|-----------|---------|
| 0x10C0 | `MALLOC` | `a0`=size | Pointer (or 0 on failure) |
| 0x10C1 | `FREE` | `a0`=pointer | 0 |
| 0x10C2 | `REALLOC` | `a0`=pointer, `a1`=size | Pointer (or 0 on failure) |
| 0x10C3 | `CALLOC` | `a0`=count, `a1`=size | Pointer (or 0 on failure) |

### Audio Mixer (0x10D0-0x10D6)

| Number | Name | Arguments | Returns |
|--------|------|-----------|---------|
| 0x10D0 | `MIXER_INIT` | `a0`=max_voices, `a1`=output_rate | 0 |
| 0x10D1 | `MIXER_PLAY` | `a0`=pcm ptr (u8), `a1`=sample_count, `a2`=sample_rate, `a3`=priority, `a4`=volume | Voice ID |
| 0x10D2 | `MIXER_STOP` | `a0`=voice | 0 |
| 0x10D3 | `MIXER_STOP_ALL` | -- | 0 |
| 0x10D4 | `MIXER_SET_VOLUME` | `a0`=voice, `a1`=volume (0-255) | 0 |
| 0x10D5 | `MIXER_PUMP` | -- | 0 |
| 0x10D6 | `MIXER_VOICE_ACTIVE` | `a0`=voice | 1 if playing, 0 if done |

### Audio Codec (0x10D8-0x10D9)

| Number | Name | Arguments | Returns |
|--------|------|-----------|---------|
| 0x10D8 | `CODEC_PARSE_VOC` | `a0`=data ptr, `a1`=size, `a2`=result ptr | 0 on success |
| 0x10D9 | `CODEC_PARSE_WAV` | `a0`=data ptr, `a1`=size, `a2`=result ptr | 0 on success |

### LZW Compression (0x10E0-0x10E1)

| Number | Name | Arguments | Returns |
|--------|------|-----------|---------|
| 0x10E0 | `LZW_COMPRESS` | `a0`=input ptr, `a1`=input len, `a2`=output ptr | Compressed size |
| 0x10E1 | `LZW_UNCOMPRESS` | `a0`=input ptr, `a1`=compressed len, `a2`=output ptr | Decompressed size |

## Inline Syscall Wrapper

From `of_syscall.h`:

```c
static inline long __of_syscall2(long n, long arg0, long arg1) {
    register long a7 __asm__("a7") = n;
    register long a0 __asm__("a0") = arg0;
    register long a1 __asm__("a1") = arg1;
    __asm__ volatile("ecall" : "+r"(a0) : "r"(a7), "r"(a1) : "memory");
    return a0;
}
```

Variants: `__of_syscall0` through `__of_syscall5` for 0-5 arguments.
