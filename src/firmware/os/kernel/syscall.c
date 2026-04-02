/*
 * openfpgaOS Syscall Dispatch
 * Implements Linux-compatible syscalls for musl + openfpgaOS HAL extensions
 */

#include "syscall.h"
#include "../hal/hal.h"
#include "../os_string.h"
#include "../../api/of_version.h"

/* dlmalloc functions (provided by of_malloc.c, USE_DL_PREFIX) */
extern void *dlmalloc(size_t);
extern void  dlfree(void *);
extern void *dlrealloc(void *, size_t);
extern void *dlcalloc(size_t, size_t);

/* ======================================================================
 * File descriptor table
 * ====================================================================== */

#define MAX_FDS         32
#define FD_STDIN        0
#define FD_STDOUT       1
#define FD_STDERR       2
#define FD_FIRST_FREE   3

/* Read-ahead buffer size — must match ra_buf_storage[].
 * 4KB amortizes DMA overhead for small sequential reads
 * (e.g. BUILD engine LZW 2-byte length prefixes)
 * while keeping the static buffer in BSS small. */
#define FD_READAHEAD_SIZE   (32 * 1024)

typedef struct {
    int      in_use;
    uint32_t slot_id;       /* APF data slot ID */
    uint32_t offset;        /* Current file offset */
    uint32_t size;          /* File size (0 if unknown) */
    int      flags;         /* O_RDONLY, O_WRONLY, etc. */
    int      is_save;       /* Is this a save file? */
    int      save_slot;     /* Save slot index (0-9) */
    uint32_t write_max;     /* High-water mark: max (offset) after any write */
} fd_entry_t;

/* Shared read-ahead buffer — reduces DMA count by caching the last
 * 4KB read.  Static BSS allocation, separate from DMA_BUFFER. */
#define RA_BUF_SIZE  FD_READAHEAD_SIZE
static uint8_t   ra_buf_storage[RA_BUF_SIZE];
static uint32_t  ra_file_off;   /* File offset of ra_buf_storage[0] */
static uint32_t  ra_valid;      /* Bytes of valid data in buffer */
static int       ra_owner_fd = -1;

static fd_entry_t fd_table[MAX_FDS];

/* ======================================================================
 * File slot registry -- maps filenames to APF data slot IDs
 *
 * Chip32 writes a file table ("FTAB") to CRAM1 at 0x39280000 (uncached)
 * during boot. The table has: magic(4) + count(4) + entries[count],
 * where each entry is: slot_id(4) + name_len(4) + name(24).
 *
 * The OS reads this table at init. Apps should use fopen("filename")
 * directly — the FTAB provides the filename→slot mapping automatically.
 * of_file_slot_register() still works for backward compatibility.
 * ====================================================================== */

#define MAX_FILE_SLOTS      16
#define FILE_SLOT_NAME_MAX  24

typedef struct {
    uint32_t slot_id;
    char     filename[FILE_SLOT_NAME_MAX];
} file_slot_entry_t;

static file_slot_entry_t file_slots[MAX_FILE_SLOTS];
static int file_slot_count;

/* Probe data slots 1-6 to see which have files loaded.
 * Registers them so fopen("slot:N") works. Filename-based lookup
 * (fopen("game.dat")) requires apps to call of_file_slot_register(). */
void file_slot_load_table(void) {
    /* Nothing to probe at init — the bridge getfile command requires
     * dedicated BRAM mapping not yet implemented. Apps can register
     * filenames with of_file_slot_register() or use fopen("slot:N"). */
}

void file_slot_register(uint32_t slot_id, const char *filename) {
    if (file_slot_count >= MAX_FILE_SLOTS)
        return;

    file_slot_entry_t *e = &file_slots[file_slot_count++];
    e->slot_id = slot_id;

    int i;
    for (i = 0; i < FILE_SLOT_NAME_MAX - 1 && filename[i]; i++)
        e->filename[i] = filename[i];
    e->filename[i] = '\0';
}

/* Case-insensitive character comparison */
static int char_lower(int c) {
    return (c >= 'A' && c <= 'Z') ? c + 32 : c;
}

/* Case-insensitive string comparison */
static int stricmp(const char *a, const char *b) {
    while (*a && *b) {
        int d = char_lower((unsigned char)*a) - char_lower((unsigned char)*b);
        if (d) return d;
        a++; b++;
    }
    return char_lower((unsigned char)*a) - char_lower((unsigned char)*b);
}

/* Extract basename from a path (after last '/' or '\') */
static const char *path_basename(const char *path) {
    const char *base = path;
    for (const char *p = path; *p; p++) {
        if (*p == '/' || *p == '\\')
            base = p + 1;
    }
    return base;
}

/* Search file slot registry by basename (case-insensitive).
 * Returns slot_id or -1 if not found. */
static int file_slot_lookup(const char *path) {
    const char *name = path_basename(path);
    for (int i = 0; i < file_slot_count; i++) {
        if (stricmp(name, file_slots[i].filename) == 0)
            return (int)file_slots[i].slot_id;
    }
    return -1;
}

/* ======================================================================
 * Heap (brk) management
 * ====================================================================== */

uintptr_t current_brk;
static uintptr_t brk_base;

/* ======================================================================
 * Error codes
 * ====================================================================== */

#define ENOSYS          38
#define EBADF           9
#define EINVAL          22
#define ENOMEM          12
#define ENOENT          2
#define EACCES          13
#define EMFILE          24
#define ENAMETOOLONG    36

/* ======================================================================
 * Linux syscall implementations
 * ====================================================================== */

static long sys_brk(long addr) {
    if (addr == 0)
        return (long)current_brk;

    uintptr_t new_brk = (uintptr_t)addr;

    if (new_brk < brk_base)
        return (long)current_brk;
    if (new_brk >= SAVE_REGION_ADDR)
        return (long)current_brk;

    current_brk = new_brk;
    return (long)current_brk;
}

static long sys_write(long fd, long buf, long count) {
    if (count <= 0)
        return 0;

    if (fd == FD_STDOUT || fd == FD_STDERR) {
        const char *s = (const char *)buf;
        for (long i = 0; i < count; i++)
            of_term_putchar(s[i]);
        return count;
    }

    if (fd < 0 || fd >= MAX_FDS || !fd_table[fd].in_use)
        return -EBADF;

    fd_entry_t *f = &fd_table[fd];

    if (f->is_save) {
        int rc = of_save_write(f->save_slot, (const void *)buf,
                           f->offset, (uint32_t)count);
        if (rc > 0) {
            f->offset += rc;
            if (f->offset > f->write_max)
                f->write_max = f->offset;
        }
        return rc;
    }

    return -EACCES;
}

static long sys_read(long fd, long buf, long count) {
    if (count <= 0)
        return 0;

    if (fd < 0 || fd >= MAX_FDS || !fd_table[fd].in_use)
        return -EBADF;

    fd_entry_t *f = &fd_table[fd];

    if (f->is_save) {
        int rc = of_save_read(f->save_slot, (void *)buf,
                          f->offset, (uint32_t)count);
        if (rc > 0)
            f->offset += rc;
        return rc;
    }

    /* Lazy file size resolution — only probe on first read, not at fopen */
    if (f->size == 0 && f->slot_id > 0 && f->offset == 0) {
        long sz = of_file_size(f->slot_id);
        if (sz > 0)
            f->size = (uint32_t)sz;
    }

    uint32_t to_read = (uint32_t)count;
    if (f->size > 0 && f->offset + to_read > f->size) {
        if (f->offset >= f->size)
            return 0;
        to_read = f->size - f->offset;
    }

    /* Read-ahead buffering: serve small reads from a cached buffer
     * to avoid per-call bridge DMA overhead (critical for BUILD engine
     * LZW format which reads 2-byte length prefixes repeatedly). */

    /* Switch read-ahead buffer ownership if needed */
    if (ra_owner_fd != fd) {
        ra_valid = 0;
        ra_owner_fd = fd;
    }

    uint8_t *dst = (uint8_t *)buf;
    uint32_t done = 0;

    while (done < to_read) {
        /* Serve from read-ahead buffer if offset is in range */
        if (ra_valid > 0 &&
            f->offset >= ra_file_off &&
            f->offset < ra_file_off + ra_valid) {
            uint32_t buf_off = f->offset - ra_file_off;
            uint32_t avail = ra_valid - buf_off;
            uint32_t n = to_read - done;
            if (n > avail) n = avail;
            memcpy(dst + done, ra_buf_storage + buf_off, n);
            f->offset += n;
            done += n;
            continue;
        }

        /* Refill read-ahead buffer via DMA */
        uint32_t fill = RA_BUF_SIZE;
        if (f->size > 0 && f->offset + fill > f->size)
            fill = f->size - f->offset;
        if (fill == 0) break;

        int rc = of_file_read(f->slot_id, f->offset,
                               (void *)DMA_BUFFER, fill);
        if (rc < 0) {
            if (f->size == 0) {
                f->size = f->offset;
                break;
            }
            if (done > 0) break;
            return (long)rc;
        }

        /* Copy from uncached alias to read-ahead buffer */
        volatile uint8_t *src = (volatile uint8_t *)DMA_BUFFER_UNCACHED;
        for (uint32_t i = 0; i < fill; i++)
            ra_buf_storage[i] = src[i];
        ra_file_off = f->offset;
        ra_valid = fill;
        /* Loop back to serve from buffer */
    }

    return (long)done;
}

static int alloc_fd(void) {
    for (int i = FD_FIRST_FREE; i < MAX_FDS; i++) {
        if (!fd_table[i].in_use) {
            fd_table[i].in_use = 1;
            return i;
        }
    }
    return -EMFILE;
}

/*
 * Parse a decimal integer from string, advancing *pp past the digits.
 * Returns the parsed value.
 */
static int parse_int(const char **pp) {
    int val = 0;
    while (**pp >= '0' && **pp <= '9') {
        val = val * 10 + (**pp - '0');
        (*pp)++;
    }
    return val;
}

/*
 * Compare n bytes (local, avoids pulling in memcmp for short checks).
 */
static int prefix_match(const char *s, const char *prefix, int n) {
    for (int i = 0; i < n; i++)
        if (s[i] != prefix[i]) return 0;
    return 1;
}

/*
 * sys_openat -- Open a file by path.
 *
 * Path conventions:
 *   "slot:<N>"  Open APF data slot N (read-only).
 *   "save:<N>"  Open save slot N (read/write, 0-9).
 *
 * This lets musl's fopen("slot:3", "rb") work transparently, which
 * means any game can load assets via standard C file I/O.
 */
static long sys_openat(long dirfd, long pathname, long flags, long mode) {
    (void)dirfd;
    (void)mode;
    const char *path = (const char *)pathname;
    if (!path)
        return -EINVAL;

    int fd = alloc_fd();
    if (fd < 0)
        return fd;

    fd_entry_t *f = &fd_table[fd];
    f->offset = 0;
    f->flags = (int)flags;
    f->is_save = 0;
    f->save_slot = -1;
    f->slot_id = 0;
    f->size = 0;
    f->write_max = 0;

    /* "slot:<N>" -- APF data slot (read-only) */
    if (prefix_match(path, "slot:", 5)) {
        const char *p = path + 5;
        int slot = parse_int(&p);
        f->slot_id = (uint32_t)slot;
        f->size = 0;  /* Resolved lazily on first read */
        return fd;
    }

    /* "save:<N>" or "save_<N>" -- save slot (read/write, 256 KB) */
    if (prefix_match(path, "save:", 5) || prefix_match(path, "save_", 5)) {
        const char *p = path + 5;
        int slot = parse_int(&p);
        if (slot < 0 || slot >= SAVE_MAX_SLOTS) {
            fd_table[fd].in_use = 0;
            return -EINVAL;
        }
        f->is_save = 1;
        f->save_slot = slot;
        f->size = SAVE_SLOT_SIZE;
        return fd;
    }

    /* Search file slot registry by basename (case-insensitive) */
    int slot = file_slot_lookup(path);
    if (slot >= 0) {
        f->slot_id = (uint32_t)slot;
        f->size = 0;  /* Resolved lazily on first read */
        return fd;
    }

    /* Unknown path -- no filesystem */
    fd_table[fd].in_use = 0;
    return -ENOENT;
}

static long sys_close(long fd) {
    if (fd < FD_FIRST_FREE || fd >= MAX_FDS)
        return -EBADF;
    if (!fd_table[fd].in_use)
        return -EBADF;

    if (fd_table[fd].is_save) {
        uint32_t flush_sz = fd_table[fd].write_max;
        if (flush_sz > 0)
            of_save_flush_size(fd_table[fd].save_slot, flush_sz);
    }

    /* Invalidate read-ahead if this fd owned it */
    if (ra_owner_fd == fd) {
        ra_owner_fd = -1;
        ra_valid = 0;
    }

    fd_table[fd].in_use = 0;
    return 0;
}

/* _llseek: (fd, offset_hi, offset_lo, &result, whence) — riscv32 uses this */
static long sys_llseek(long fd, long off_hi, long off_lo,
                       long result_ptr, long whence) {
    if (fd < 0 || fd >= MAX_FDS || !fd_table[fd].in_use)
        return -EBADF;

    fd_entry_t *f = &fd_table[fd];
    /* We only support 32-bit offsets — ignore off_hi */
    long offset = off_lo;
    long new_offset;

    switch (whence) {
    case 0: new_offset = offset; break;
    case 1: new_offset = (long)f->offset + offset; break;
    case 2: new_offset = (long)f->size + offset; break;
    default: return -EINVAL;
    }

    if (new_offset < 0)
        return -EINVAL;

    f->offset = (uint32_t)new_offset;

    /* Invalidate read-ahead buffer only if the new offset falls outside
     * the currently buffered range.  Forward seeks within the buffer
     * (common pattern: skip a few bytes in a file) stay fast. */
    if (ra_owner_fd == fd && ra_valid > 0) {
        if (new_offset < (long)ra_file_off ||
            new_offset >= (long)(ra_file_off + ra_valid))
            ra_valid = 0;
    }

    /* _llseek writes result to user pointer as 64-bit */
    if (result_ptr) {
        int64_t *res = (int64_t *)result_ptr;
        *res = (int64_t)new_offset;
    }
    return 0;  /* _llseek returns 0 on success, -1 on error */
}

static long sys_clock_gettime(long clk_id, long tp) {
    (void)clk_id;
    struct { uint32_t tv_sec; uint32_t tv_nsec; } *ts = (void *)tp;
    uint32_t ns;
    ts->tv_sec = of_timer_get_seconds(&ns);
    ts->tv_nsec = ns;
    return 0;
}

static long sys_clock_getres(long clk_id, long tp) {
    (void)clk_id;
    struct { uint32_t tv_sec; uint32_t tv_nsec; } *ts = (void *)tp;
    if (ts) { ts->tv_sec = 0; ts->tv_nsec = 10; }
    return 0;
}

static long sys_mmap2(long addr, long length, long prot,
                      long flags, long fd, long pgoffset) {
    (void)addr; (void)prot; (void)flags; (void)fd; (void)pgoffset;
    uintptr_t aligned = (current_brk + 4095) & ~4095;
    uintptr_t new_brk = aligned + (uintptr_t)length;
    if (new_brk >= SAVE_REGION_ADDR)
        return -ENOMEM;
    current_brk = new_brk;
    memset((void *)aligned, 0, (size_t)length);
    return (long)aligned;
}

static long sys_munmap(long addr, long length) {
    (void)addr; (void)length;
    return 0;
}

static long sys_writev(long fd, long iov_ptr, long iovcnt) {
    struct iovec { void *iov_base; uint32_t iov_len; } *iov = (void *)iov_ptr;
    long total = 0;
    for (long i = 0; i < iovcnt; i++) {
        long rc = sys_write(fd, (long)iov[i].iov_base, (long)iov[i].iov_len);
        if (rc < 0) return total > 0 ? total : rc;
        total += rc;
    }
    return total;
}

static long sys_readv(long fd, long iov_ptr, long iovcnt) {
    struct iovec { void *iov_base; uint32_t iov_len; } *iov = (void *)iov_ptr;
    long total = 0;
    for (long i = 0; i < iovcnt; i++) {
        if (iov[i].iov_len == 0) continue;
        long rc = sys_read(fd, (long)iov[i].iov_base, (long)iov[i].iov_len);
        if (rc < 0) return total > 0 ? total : rc;
        if (rc == 0) break;  /* EOF */
        total += rc;
        if ((uint32_t)rc < iov[i].iov_len) break;  /* short read */
    }
    return total;
}

/* ======================================================================
 * openfpgaOS HAL syscall implementations
 * ====================================================================== */

static long of_video_syscall(long n, long a0, long a1, long a2) {
    (void)a2;
    switch (n) {
    case OF_SYS_VIDEO_INIT:
        of_video_init();
        return 0;
    case OF_SYS_VIDEO_FLIP:
        of_video_flip();
        return 0;
    case OF_SYS_VIDEO_WAIT_FLIP:
        of_video_wait_flip();
        return 0;
    case OF_SYS_VIDEO_SET_PALETTE:
        of_video_set_palette((uint8_t)a0, (uint8_t)(a1 >> 16),
                       (uint8_t)(a1 >> 8), (uint8_t)a1);
        return 0;
    case OF_SYS_VIDEO_GET_SURFACE:
        return (long)of_video_get_surface();
    case OF_SYS_VIDEO_SET_DISPLAY_MODE:
        of_video_set_display_mode((int)a0);
        return 0;
    case OF_SYS_VIDEO_CLEAR:
        of_video_clear((uint8_t)a0);
        return 0;
    case OF_SYS_VIDEO_SET_PALETTE_BULK:
        of_video_set_palette_bulk((const uint32_t *)a0, (int)a1);
        return 0;
    case OF_SYS_VIDEO_FLUSH_CACHE:
        of_video_flush_cache();
        return 0;
    case OF_SYS_VIDEO_SET_COLOR_MODE:
        SYS_COLOR_MODE = (uint32_t)a0;
        return 0;
    default:
        return -ENOSYS;
    }
}

static long of_audio_syscall(long n, long a0, long a1) {
    switch (n) {
    case OF_SYS_AUDIO_WRITE:
        return of_audio_write((const int16_t *)a0, (int)a1);
    case OF_SYS_AUDIO_GET_FREE:
        return of_audio_get_free();
    case OF_SYS_OPL_WRITE:
        of_opl_write((uint16_t)a0, (uint8_t)a1);
        return 0;
    case OF_SYS_OPL_RESET:
        of_opl_reset();
        return 0;
    case OF_SYS_AUDIO_INIT:
        of_audio_init();
        return 0;
    default:
        return -ENOSYS;
    }
}

static long of_input_syscall(long n, long a0, long a1) {
    switch (n) {
    case OF_SYS_INPUT_POLL:
        of_input_poll();
        return 0;
    case OF_SYS_INPUT_GET_STATE: {
        const of_input_state_t *state = of_input_get_state((int)a0);
        if (a1)
            memcpy((void *)a1, state, sizeof(of_input_state_t));
        return (long)state->buttons;
    }
    case OF_SYS_INPUT_SET_DEADZONE:
        of_input_set_deadzone((int16_t)a0);
        return 0;
    default:
        return -ENOSYS;
    }
}

static long of_save_syscall(long n, long a0, long a1, long a2, long a3) {
    switch (n) {
    case OF_SYS_SAVE_READ:
        return of_save_read((int)a0, (void *)a1, (uint32_t)a2, (uint32_t)a3);
    case OF_SYS_SAVE_WRITE:
        return of_save_write((int)a0, (const void *)a1, (uint32_t)a2, (uint32_t)a3);
    case OF_SYS_SAVE_FLUSH:
        of_save_flush((int)a0);
        return 0;
    case OF_SYS_SAVE_FLUSH_SIZE:
        return of_save_flush_size((int)a0, (uint32_t)a1);
    case OF_SYS_SAVE_ERASE:
        of_save_erase((int)a0);
        return 0;
    default:
        return -ENOSYS;
    }
}

/* ======================================================================
 * Main syscall dispatch (called from trap handler via ecall)
 * ====================================================================== */

long syscall_dispatch(long n, long a0, long a1, long a2,
                      long a3, long a4, long a5) {
    /* Check for shutdown handshake on every syscall entry */
    of_check_shutdown();

    /* Auto-pump mixer on syscall entry — keeps audio alive during
     * blocking operations. Skip video/audio syscalls to avoid
     * delaying frame flips and causing FB flicker. */
    if (n < 0x1000 || n >= 0x1020)
        of_mixer_pump_auto();

    switch (n) {
    case SYS_brk:           return sys_brk(a0);
    case SYS_write:         return sys_write(a0, a1, a2);
    case SYS_read:          return sys_read(a0, a1, a2);
    case SYS_readv:         return sys_readv(a0, a1, a2);
    case SYS_writev:        return sys_writev(a0, a1, a2);
    case SYS_openat:        return sys_openat(a0, a1, a2, a3);
    case SYS_close:         return sys_close(a0);
    case SYS_lseek:         return sys_llseek(a0, a1, a2, a3, a4);
    case SYS_clock_gettime:     /* legacy 113 — kept for direct syscall callers */
    case SYS_clock_gettime64:   /* 403 — riscv32 musl sends this */
        return sys_clock_gettime(a0, a1);
    case SYS_clock_getres:      /* legacy 114 */
    case SYS_clock_getres64:    /* 406 — riscv32 musl sends this */
        return sys_clock_getres(a0, a1);
    case SYS_mmap2:         return sys_mmap2(a0, a1, a2, a3, a4, a5);
    case SYS_munmap:        return sys_munmap(a0, a1);
    case SYS_mprotect:      return 0;
    case SYS_madvise:       return 0;
    case SYS_futex:         return 0;   /* single-threaded — no real locking needed */
    case SYS_tkill:         return 0;   /* single-threaded — abort() sends SIGABRT here */

    case SYS_exit:
    case SYS_exit_group:
        while (1) {}
        return 0;

    case SYS_getpid:        return 1;
    case SYS_gettid:        return 1;
    case SYS_set_tid_address: return 1;

    case SYS_rt_sigaction:   return 0;
    case SYS_rt_sigprocmask: return 0;

    case SYS_statx: {
        /* Minimal statx: musl on riscv32 uses this instead of fstat.
         * a0=dirfd, a1=path, a2=flags, a3=mask, a4=statxbuf
         * For fstat: dirfd=fd, path="", flags=AT_EMPTY_PATH */
        long statx_fd = a0;
        if (statx_fd >= 0 && statx_fd < MAX_FDS && fd_table[statx_fd].in_use) {
            /* statx struct: offset 0x00=mask, 0x04=blksize, ...
             * offset 0x28=stx_size (uint64_t) */
            uint32_t *sx = (uint32_t *)a4;
            memset(sx, 0, 256);
            sx[0] = 0x7FF;  /* mask: all valid */
            sx[10] = fd_table[statx_fd].size;  /* stx_size low 32 bits at offset 0x28 */
            return 0;
        }
        return -EBADF;
    }

    case SYS_fstat: {
        /* Legacy fstat (syscall 80) — kept for direct callers */
        if (a0 >= 0 && a0 < MAX_FDS && fd_table[a0].in_use) {
            struct { uint64_t __pad[6]; uint32_t st_size; } *st = (void *)a1;
            memset(st, 0, 128);  /* zero full struct (musl expects 128 bytes) */
            st->st_size = fd_table[a0].size;
            return 0;
        }
        return -EBADF;
    }
    case SYS_fstatat:       return -ENOSYS;
    case SYS_fcntl:         return -ENOSYS;
    case SYS_ioctl:         return 0;   /* stub — makes musl stdout line-buffered */
    case SYS_faccessat:     return -ENOSYS;

    case SYS_riscv_flush_icache:
        of_cache_invalidate_icache();
        return 0;

    default:
        break;
    }

    /* openfpgaOS HAL syscalls (0x1000+) */
    if (n >= 0x1000 && n < 0x1010)
        return of_video_syscall(n, a0, a1, a2);
    if (n >= 0x1010 && n < 0x1020)
        return of_audio_syscall(n, a0, a1);
    if (n >= 0x1020 && n < 0x1030)
        return of_input_syscall(n, a0, a1);
    if (n >= 0x1030 && n < 0x1040)
        return of_save_syscall(n, a0, a1, a2, a3);

    if (n == OF_SYS_ANALOGIZER_GET_STATE) {
        if (a0)
            memcpy((void *)a0, of_analogizer_get_state(),
                   sizeof(of_analogizer_state_t));
        return of_analogizer_is_enabled();
    }
    if (n == OF_SYS_ANALOGIZER_IS_ENABLED)
        return of_analogizer_is_enabled();

    if (n == OF_SYS_TERM_PUTCHAR) {
        of_term_putchar((char)a0);
        return 0;
    }
    if (n == OF_SYS_TERM_CLEAR) {
        of_term_clear();
        return 0;
    }
    if (n == OF_SYS_TERM_SET_POS) {
        of_term_set_pos((int)a0, (int)a1);
        return 0;
    }

    if (n == OF_SYS_LINK_SEND)
        return of_link_send((uint32_t)a0);
    if (n == OF_SYS_LINK_RECV)
        return of_link_recv((uint32_t *)a0);
    if (n == OF_SYS_LINK_GET_STATUS)
        return (long)of_link_get_status();

    if (n == OF_SYS_TIMER_GET_US)
        return (long)of_timer_get_us();
    if (n == OF_SYS_TIMER_GET_MS)
        return (long)of_timer_get_ms();
    if (n == OF_SYS_TIMER_DELAY_US) {
        of_timer_delay_us((uint32_t)a0);
        return 0;
    }
    if (n == OF_SYS_TIMER_DELAY_MS) {
        of_timer_delay_ms((uint32_t)a0);
        return 0;
    }

    if (n == OF_SYS_FILE_READ) {
        /* Direct DMA syscall — of_file_read handles all cache
         * coherency via cache eviction (before and after). */
        return of_file_read((uint32_t)a0, (uint32_t)a1,
                             (void *)a2, (uint32_t)a3);
    }

    if (n == OF_SYS_FILE_SIZE)
        return of_file_size((uint32_t)a0);

    if (n == OF_SYS_GET_VERSION)
        return OF_API_VERSION;

    /* Audio ring buffer: enqueue samples for kernel-side drain */
    if (n == OF_SYS_AUDIO_ENQUEUE)
        return of_audio_enqueue((const int16_t *)a0, (int)a1);
    if (n == OF_SYS_AUDIO_RING_FREE)
        return of_audio_ring_free();

    /* Idle hook: register a function called during DMA waits */
    if (n == OF_SYS_SET_IDLE_HOOK) {
        of_file_set_idle_hook((void (*)(void))a0);
        return 0;
    }

    /* File slot query: count */
    if (n == OF_SYS_FILE_SLOT_COUNT)
        return file_slot_count;

    /* File slot register: a0 = slot_id, a1 = pointer to filename string */
    if (n == OF_SYS_FILE_SLOT_REGISTER) {
        if (a1)
            file_slot_register((uint32_t)a0, (const char *)a1);
        return 0;
    }

    /* File slot query: get entry
     * a0 = index, a1 = pointer to { uint32_t slot_id; char name[32]; } */
    if (n == OF_SYS_FILE_SLOT_GET) {
        int idx = (int)a0;
        if (idx < 0 || idx >= file_slot_count)
            return -1;
        if (a1) {
            uint32_t *out = (uint32_t *)a1;
            out[0] = file_slots[idx].slot_id;
            char *name = (char *)&out[1];
            for (int i = 0; i < FILE_SLOT_NAME_MAX; i++)
                name[i] = file_slots[idx].filename[i];
        }
        return 0;
    }

    /* Tile engine syscalls (0x1090+) */
    if (n >= 0x1090 && n < 0x10A0) {
        switch (n) {
        case OF_SYS_TILE_ENABLE:
            of_tile_enable((int)a0, (int)a1);
            return 0;
        case OF_SYS_TILE_SCROLL:
            of_tile_scroll((int)a0, (int)a1);
            return 0;
        case OF_SYS_TILE_SET:
            of_tile_set((int)a0, (int)a1, (uint16_t)a2);
            return 0;
        case OF_SYS_TILE_LOAD_MAP:
            of_tile_load_map((const uint16_t *)a0, (int)a1, (int)a2,
                             (int)a3, (int)a4);
            return 0;
        case OF_SYS_TILE_LOAD_CHR:
            of_tile_load_chr((int)a0, (const void *)a1, (int)a2);
            return 0;
        default:
            return -ENOSYS;
        }
    }

    /* Sprite engine syscalls (0x10A0+) */
    if (n >= 0x10A0 && n < 0x10B0) {
        switch (n) {
        case OF_SYS_SPRITE_ENABLE:
            of_sprite_enable((int)a0);
            return 0;
        case OF_SYS_SPRITE_SET:
            /* a0=index, a1=x, a2=y, a3=tile_id, a4=packed attrs:
               [3:0]=palette, [4]=hflip, [5]=vflip, [6]=enable */
            of_sprite_set((int)a0, (int)a1, (int)a2, (int)a3,
                          (int)(a4 & 0xF),
                          (int)((a4 >> 4) & 1),
                          (int)((a4 >> 5) & 1),
                          (int)((a4 >> 6) & 1));
            return 0;
        case OF_SYS_SPRITE_MOVE:
            of_sprite_move((int)a0, (int)a1, (int)a2);
            return 0;
        case OF_SYS_SPRITE_LOAD_CHR:
            of_sprite_load_chr((int)a0, (const void *)a1, (int)a2);
            return 0;
        case OF_SYS_SPRITE_HIDE:
            of_sprite_hide((int)a0);
            return 0;
        case OF_SYS_SPRITE_HIDE_ALL:
            of_sprite_hide_all();
            return 0;
        default:
            return -ENOSYS;
        }
    }

    /* Memory allocation syscalls (0x10C0+) */
    if (n == OF_SYS_MALLOC)
        return (long)dlmalloc((size_t)a0);
    if (n == OF_SYS_FREE) {
        dlfree((void *)a0);
        return 0;
    }
    if (n == OF_SYS_REALLOC)
        return (long)dlrealloc((void *)a0, (size_t)a1);
    if (n == OF_SYS_CALLOC)
        return (long)dlcalloc((size_t)a0, (size_t)a1);

    /* Audio Mixer syscalls (0x10D0+) */
    if (n == OF_SYS_MIXER_INIT) {
        of_mixer_init((int)a0, (int)a1);
        return 0;
    }
    if (n == OF_SYS_MIXER_PLAY)
        return of_mixer_play((const uint8_t *)a0, (uint32_t)a1,
                             (uint32_t)a2, (int)a3, (int)a4);
    if (n == OF_SYS_MIXER_STOP) {
        of_mixer_stop((int)a0);
        return 0;
    }
    if (n == OF_SYS_MIXER_STOP_ALL) {
        of_mixer_stop_all();
        return 0;
    }
    if (n == OF_SYS_MIXER_SET_VOLUME) {
        of_mixer_set_volume((int)a0, (int)a1);
        return 0;
    }
    if (n == OF_SYS_MIXER_PUMP) {
        of_mixer_pump();
        return 0;
    }
    if (n == OF_SYS_MIXER_VOICE_ACTIVE)
        return of_mixer_voice_active((int)a0);
    if (n == OF_SYS_MIXER_SET_PAN) {
        of_mixer_set_pan((int)a0, (int)a1);
        return 0;
    }

    /* Audio Codec syscalls (0x10D8+) */
    if (n == OF_SYS_CODEC_PARSE_VOC)
        return of_codec_parse_voc((const uint8_t *)a0, (uint32_t)a1,
                                  (of_codec_result_t *)a2);
    if (n == OF_SYS_CODEC_PARSE_WAV)
        return of_codec_parse_wav((const uint8_t *)a0, (uint32_t)a1,
                                  (of_codec_result_t *)a2);

    /* LZW Compression syscalls (0x10E0+) */
    if (n == OF_SYS_LZW_COMPRESS)
        return of_lzw_compress((const uint8_t *)a0, (int32_t)a1, (uint8_t *)a2);
    if (n == OF_SYS_LZW_UNCOMPRESS)
        return of_lzw_uncompress((const uint8_t *)a0, (int32_t)a1, (uint8_t *)a2);

    /* DMA engine syscalls (0x10F0+) */
    if (n == OF_SYS_DMA_COPY) {
        dma_copy((void *)a0, (const void *)a1, (uint32_t)a2);
        return 0;
    }
    if (n == OF_SYS_DMA_FILL) {
        dma_fill((void *)a0, (uint32_t)a1, (uint32_t)a2);
        return 0;
    }
    if (n == OF_SYS_DMA_COPY_ASYNC) {
        dma_copy_async((void *)a0, (const void *)a1, (uint32_t)a2);
        return 0;
    }
    if (n == OF_SYS_DMA_FILL_ASYNC) {
        dma_fill_async((void *)a0, (uint32_t)a1, (uint32_t)a2);
        return 0;
    }
    if (n == OF_SYS_DMA_WAIT) {
        dma_wait();
        /* Evict D-cache so CPU sees DMA-written data */
        of_cache_flush_dcache();
        return 0;
    }
    if (n == OF_SYS_DMA_BUSY)
        return (DMA_STATUS & DMA_STATUS_BUSY) ? 1 : 0;

    return -ENOSYS;
}

/* ======================================================================
 * Syscall subsystem initialization
 * ====================================================================== */

void syscall_init(uintptr_t heap_start) {
    memset(fd_table, 0, sizeof(fd_table));
    fd_table[FD_STDIN].in_use = 1;
    fd_table[FD_STDOUT].in_use = 1;
    fd_table[FD_STDERR].in_use = 1;
    brk_base = heap_start;
    current_brk = heap_start;

    /* Reset file slot registry (apps re-register on startup) */
    file_slot_count = 0;
    memset(file_slots, 0, sizeof(file_slots));

    /* Reset read-ahead buffer */
    ra_owner_fd = -1;
    ra_valid = 0;

    /* Load file slot table from CRAM1 (populated by Chip32 loader) */
    file_slot_load_table();
}
