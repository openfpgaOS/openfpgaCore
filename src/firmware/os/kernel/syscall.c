/*
 * openfpgaOS Syscall Dispatch
 * Implements Linux-compatible syscalls for musl + openfpgaOS HAL extensions
 */

#include "syscall.h"
#include "irq.h"
#include "../hal/hal.h"
#include "../os_string.h"
#include "of_version.h"

/* dlmalloc functions (provided by of_malloc.c, USE_DL_PREFIX) */
extern void *dlmalloc(size_t);
extern void  dlfree(void *);
extern void *dlrealloc(void *, size_t);
extern void *dlcalloc(size_t, size_t);

/* ======================================================================
 * Timer / Signal state
 * ====================================================================== */

#define SIGALRM 14

static void (*sigalrm_handler)(int) = NULL;
void (*timer_callback_ptr)(void) = NULL;  /* non-static: accessed by services_table.c */

/* Called from irq_handler() on machine timer interrupt */
void timer_isr_callback(void) {
    /* Service the terminal UART hook before app callbacks.  The Pocket
     * implementation is currently synchronous/no-op here, but keeping the
     * call preserves the HAL contract for targets with buffered output. */
    of_term_uart_drain();
    if (timer_callback_ptr)
        timer_callback_ptr();
    if (sigalrm_handler)
        sigalrm_handler(SIGALRM);
}

/* ======================================================================
 * File descriptor table
 * ====================================================================== */

#define MAX_FDS         32
#define FD_STDIN        0
#define FD_STDOUT       1
#define FD_STDERR       2
#define FD_FIRST_FREE   3

typedef struct {
    int      in_use;
    uint32_t slot_id;       /* APF data slot ID */
    uint32_t offset;        /* Current file offset */
    uint32_t size;          /* File size (0 if unknown) */
    int      flags;         /* O_RDONLY, O_WRONLY, etc. */
    int      is_save;       /* Is this a nonvolatile writable file? */
    int      save_slot;     /* Save slot index (0-9), or -1 for config/settings */
    int      nv_cache_idx;  /* Nonvolatile size-cache index */
    int      dirty;         /* Nonvolatile data/size changed since open */
    int      is_dir;        /* Is this a directory FD? */
    uint32_t dir_pos;       /* Current position in directory listing */
} fd_entry_t;

static fd_entry_t fd_table[MAX_FDS];

/* ======================================================================
 * I/O cache — LRU read cache in CRAM1 (PSRAM, bridge-speed)
 *
 * Bridge DMA writes to CRAM1 at 74.25 MHz (bridge clock) with zero
 * SDRAM contention. The CPU reads cached data from the CRAM1 cached
 * alias (0x31xxxxxx) through D-cache for fast sequential access.
 *
 * Each entry holds one 32KB block, keyed by (slot_id, aligned_offset).
 * On hit, data is served directly from CRAM1 — no bridge DMA needed.
 * On miss, the LRU entry is evicted and refilled via bridge DMA.
 *
 * Bridge DMA writes to CRAM1 (PSRAM bus, zero SDRAM contention).
 * CPU reads via the 0x31 cached alias — D-cache fills on first
 * access (one CDC read per 64B line), then all hits are single-cycle.
 * Lines are read-only (CPU never writes to 0x31), so eviction just
 * drops clean lines — no CDC writeback, no bridge contention.
 * ====================================================================== */

#define IO_CACHE_ENTRIES    8
#define IO_CACHE_BLOCK_SIZE (32 * 1024)
/* The bounce buffer now lives in SDRAM at 0x10380000 (256 KB,
 * sandwiched between the unused tail of TERM_FB and INTERACT_BASE).
 * The bridge_to_sdram fabric module sniffs writes whose address starts
 * with 0x10 and replays them on the SDRAM arbiter's M3 port — fully
 * bypassing the CRAM1 controller that races with the audio mixer. */
#define IO_CACHE_BASE       0x10380000u
#define IO_CACHE_CACHED     (IO_CACHE_BASE)                /* CPU cached reads */
#define IO_CACHE_UNCACHED   (IO_CACHE_BASE)                /* SDRAM has no uncached alias */
#define IO_CACHE_BRIDGE     (IO_CACHE_BASE)                /* Bridge writes here too */

typedef struct {
    uint32_t slot_id;       /* data slot this block belongs to */
    uint32_t file_off;      /* file offset (aligned to IO_CACHE_BLOCK_SIZE) */
    uint32_t valid;         /* bytes of valid data in this block */
    uint32_t lru;           /* access counter — higher = more recent */
} io_cache_entry_t;

static io_cache_entry_t io_cache[IO_CACHE_ENTRIES];
static uint32_t io_lru_counter;

/* Find a cache entry matching (slot, aligned_off), or return -1 */
static int io_cache_lookup(uint32_t slot_id, uint32_t aligned_off) {
    for (int i = 0; i < IO_CACHE_ENTRIES; i++) {
        if (io_cache[i].valid > 0 &&
            io_cache[i].slot_id == slot_id &&
            io_cache[i].file_off == aligned_off) {
            io_cache[i].lru = ++io_lru_counter;
            return i;
        }
    }
    return -1;
}

/* Find the LRU entry to evict */
static int io_cache_evict(void) {
    int best = 0;
    uint32_t oldest = io_cache[0].lru;
    for (int i = 1; i < IO_CACHE_ENTRIES; i++) {
        if (io_cache[i].valid == 0) return i;  /* prefer empty slot */
        if (io_cache[i].lru < oldest) {
            oldest = io_cache[i].lru;
            best = i;
        }
    }
    return best;
}

/* Get pointer to entry N's SDRAM-backed cached data. */
static inline const uint8_t *io_cache_data(int entry) {
    return (const uint8_t *)(IO_CACHE_CACHED + entry * IO_CACHE_BLOCK_SIZE);
}

/* Fill a cache entry via of_file_read, which bounces the bridge DMA
 * through CRAM0 scratch in hardware-sized chunks, then memcpys into the
 * caller's SDRAM buffer.
 *
 * v2 arch: the direct bridge→SDRAM fabric path was retired with CRAM1,
 * so the bridge can only write to CRAM0.  Using of_file_read lets the
 * of_disk_bridge backend handle the mode flip + bounce once, instead of
 * duplicating that sequence here.  The memcpy lands in SDRAM via the
 * L1 D$, so subsequent reads from cached_ptr hit the cache coherently
 * — no explicit invalidation needed. */
static int io_cache_fill(int entry, uint32_t slot_id,
                          uint32_t aligned_off, uint32_t fill) {
    void *cached_ptr = (void *)(IO_CACHE_CACHED + entry * IO_CACHE_BLOCK_SIZE);

    int rc = of_file_read(slot_id, aligned_off, cached_ptr, fill);
    if (rc < 0) return rc;

    io_cache[entry].slot_id = slot_id;
    io_cache[entry].file_off = aligned_off;
    io_cache[entry].valid = fill;
    io_cache[entry].lru = ++io_lru_counter;

    return 0;
}

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

#define MAX_FILE_SLOTS      32
#define FILE_SLOT_NAME_MAX  24
#define SAVE_SLOT_ID_BASE   10u
#define DUKE_SETTINGS_SLOT_ID 9u
#define NV_CONFIG_CACHE_IDX 0
#define NV_SAVE_CACHE_BASE  1
#define NV_CACHE_SLOTS      (SAVE_MAX_SLOTS + 1)

#define O_ACCMODE       03
#define O_WRONLY        01
#define O_RDWR          02
#define O_TRUNC         01000
#define O_APPEND        02000
#define O_DIRECTORY     0200000

typedef struct {
    uint32_t slot_id;
    char     filename[FILE_SLOT_NAME_MAX];
} file_slot_entry_t;

static file_slot_entry_t file_slots[MAX_FILE_SLOTS];
static int file_slot_count;
static uint32_t save_size_cache[NV_CACHE_SLOTS];
static uint8_t save_size_known[NV_CACHE_SLOTS];

static void save_size_cache_store(int cache_idx, uint32_t size);

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

static int save_slot_from_data_id(uint32_t slot_id) {
    if (slot_id >= SAVE_SLOT_ID_BASE &&
        slot_id < SAVE_SLOT_ID_BASE + (uint32_t)SAVE_MAX_SLOTS)
        return (int)(slot_id - SAVE_SLOT_ID_BASE);
    return -1;
}

static int nv_cache_index_from_data_id(uint32_t slot_id) {
    if (slot_id == 8 || slot_id == 9)
        return NV_CONFIG_CACHE_IDX;

    int save_slot = save_slot_from_data_id(slot_id);
    if (save_slot >= 0)
        return NV_SAVE_CACHE_BASE + save_slot;

    return -1;
}

static int open_flags_want_write(long flags) {
    return ((flags & O_ACCMODE) != 0) || ((flags & O_TRUNC) != 0);
}

static void format_slot_name(char *name, uint32_t slot) {
    name[0] = 's';
    name[1] = 'l';
    name[2] = 'o';
    name[3] = 't';
    name[4] = ':';
    if (slot >= 10) {
        name[5] = '0' + (char)(slot / 10);
        name[6] = '0' + (char)(slot % 10);
        name[7] = '\0';
    } else {
        name[5] = '0' + (char)slot;
        name[6] = '\0';
    }
}

static int save_slot_from_filename(const char *path) {
    const char *name = path_basename(path);
    int len = 0;
    while (name[len])
        len++;

    if (len < 6)
        return -1;

    if (char_lower((unsigned char)name[len - 4]) != '.' ||
        char_lower((unsigned char)name[len - 3]) != 's' ||
        char_lower((unsigned char)name[len - 2]) != 'a' ||
        char_lower((unsigned char)name[len - 1]) != 'v')
        return -1;

    int end = len - 4;
    int start = end;
    while (start > 0 && name[start - 1] >= '0' && name[start - 1] <= '9')
        start--;

    if (start == end || start == 0 || name[start - 1] != '_')
        return -1;

    int slot = 0;
    for (int i = start; i < end; i++)
        slot = slot * 10 + (name[i] - '0');

    return (slot < (int)SAVE_MAX_SLOTS) ? slot : -1;
}

static int nv_settings_slot_from_filename(const char *path) {
    const char *name = path_basename(path);

    /* Duke's settings slot can be empty on first boot.  Some Pocket
     * launcher/bridge combinations do not return a useful GETFILE name
     * for that empty nonvolatile entry, so keep the manifest filename as
     * a stable fallback. */
    if (stricmp(name, "duke3d.cfg") == 0)
        return (int)DUKE_SETTINGS_SLOT_ID;

    return -1;
}

int file_slot_get_count(void) {
    return file_slot_count;
}

const char *file_slot_get(int idx, uint32_t *slot_id_out) {
    if (idx < 0 || idx >= file_slot_count)
        return NULL;
    if (slot_id_out)
        *slot_id_out = file_slots[idx].slot_id;
    return file_slots[idx].filename;
}

int file_slot_find(const char *filename, uint32_t *slot_id_out) {
    int slot = file_slot_lookup(filename);
    if (slot < 0)
        return -1;

    if (slot_id_out)
        *slot_id_out = (uint32_t)slot;
    return 0;
}

/* ======================================================================
 * Heap (brk) management
 * ====================================================================== */

/* Heap layout — brk grows up from brk_base, mmap grows down from
 * mmap_bottom. The two ranges never overlap: sys_brk rejects requests
 * above mmap_bottom and sys_mmap2 rejects requests below current_brk.
 * Previously both shared current_brk, which let a later sys_mmap2
 * advance current_brk past a live mmap region — then musl's mallocng
 * (which caches the kernel brk locally) would re-extend the brk heap
 * back into addresses already handed out as mmap, corrupting both. */
uintptr_t current_brk;
static uintptr_t brk_base;
static uintptr_t mmap_bottom;

/* Top of the app's brk window (below this live the app's mmap pages).
 * Previously read by of_malloc.c's __kernel_sbrk when the kernel
 * dlmalloc shared the app's brk; it now has its own heap and this
 * helper has no remaining caller, but the accessor is kept cheap
 * and isolates the mmap_bottom symbol for future introspection. */
uintptr_t of_brk_limit(void) { return mmap_bottom; }

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
#define ENOTDIR         20
#define EIO             5
#define EFAULT          14

/* ======================================================================
 * Directory listing — enumerate data slots via DS_CMD_GETFILE
 *
 * opendir("/") probes slots 0-6 for loaded files and auto-registers
 * them in the file slot table.  getdents64 returns the entries.
 * ====================================================================== */

#define MAX_DATA_SLOTS  32  /* APF supports up to 32 data slots per core */

struct linux_dirent64 {
    uint64_t d_ino;
    int64_t  d_off;
    uint16_t d_reclen;
    uint8_t  d_type;
    char     d_name[1];
};

#define DT_REG  8

/* Probe data slots: check datatable metadata, then try GETFILE for the
 * real filename. Normal read-only slots need a non-zero size.
 * Nonvolatile config/save slots are registered when their datatable
 * flags exist, even if the current file is logically empty. */
static void dir_probe_slots(void) {
    char name[FILE_SLOT_NAME_MAX];
    /* Slot 0 is reserved by APF (never carries core-visible data).
     * Walk 1..MAX_DATA_SLOTS-1 and skip entries with no datatable
     * mapping or payload. */
    for (uint32_t slot = 1; slot < MAX_DATA_SLOTS; slot++) {
        name[0] = '\0';
        long sz = of_file_size(slot);
        long flags = of_file_flags(slot);
        int nv_slot = of_nvslot_is_supported(slot);
        if (sz <= 0 && (!nv_slot || flags <= 0))
            continue;

        /* Retry GETFILE a few times — empirically APF sometimes needs
         * several attempts before it populates the response struct,
         * particularly for slots that were consumed by the boot-ROM
         * path rather than a prior DS_CMD_READ. */
        int got_name = 0;
        int get_rc = -1;
        for (int attempt = 0; attempt < 4; attempt++) {
            get_rc = of_file_get_name(slot, name, sizeof(name));
            if (get_rc == 0 && name[0]) {
                got_name = 1;
                break;
            }
        }
        if (!got_name) {
            /* Fallback name — APF has no filename record for this
             * slot, but DT_QUERY confirms it holds data. Register
             * under "slot:N" so the slot is still reachable. */
            format_slot_name(name, slot);
        }

        if (file_slot_lookup(name) < 0)
            file_slot_register(slot, name);
    }
}

/* Public filesystem init — eager FTAB population at boot time. See
 * syscall.h for rationale.  Returns the number of slots that had data
 * (matches what apps will see via opendir). */
int filesystem_init(void) {
    int before = file_slot_count;
    dir_probe_slots();
    return file_slot_count - before;
}

static long sys_getdents64(int fd, void *buf, uint32_t count) {
    if (fd < 0 || fd >= MAX_FDS || !fd_table[fd].in_use)
        return -EBADF;
    fd_entry_t *f = &fd_table[fd];
    if (!f->is_dir)
        return -ENOTDIR;

    uint8_t *out = (uint8_t *)buf;
    uint32_t written = 0;
    int pos = (int)f->dir_pos;

    while (pos < file_slot_count) {
        const char *name = file_slots[pos].filename;
        uint32_t namelen = 0;
        while (name[namelen]) namelen++;

        uint32_t reclen = (8 + 8 + 2 + 1 + namelen + 1 + 7) & ~7;
        if (written + reclen > count)
            break;

        struct linux_dirent64 *de = (struct linux_dirent64 *)(out + written);
        de->d_ino = file_slots[pos].slot_id + 1;
        de->d_off = pos + 1;
        de->d_reclen = (uint16_t)reclen;
        de->d_type = DT_REG;

        char *dst = de->d_name;
        for (uint32_t i = 0; i < namelen; i++)
            dst[i] = name[i];
        dst[namelen] = '\0';

        written += reclen;
        pos++;
    }

    f->dir_pos = (uint32_t)pos;
    return (long)written;
}

/* ======================================================================
 * Linux syscall implementations
 * ====================================================================== */

static long sys_brk(long addr) {
    if (addr == 0)
        return (long)current_brk;

    uintptr_t new_brk = (uintptr_t)addr;

    if (new_brk < brk_base)
        return (long)current_brk;
    /* Refuse to grow into the mmap region. Returning the old break
     * signals failure per the Linux brk(2) contract; musl's sbrk()
     * will surface it as ENOMEM. */
    if (new_brk > mmap_bottom)
        return (long)current_brk;

    /* Linux guarantees brk-extended pages are zero-filled via the
     * page-fault handler. On bare metal the SDRAM retains whatever
     * it last held (prior app's heap, framebuffer pixels, etc.),
     * which trips code and allocators that implicitly assume fresh
     * memory starts at zero. Zero the new range here to match the
     * POSIX contract. */
    if (new_brk > current_brk)
        memset((void *)current_brk, 0, new_brk - current_brk);
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
        if ((f->flags & O_ACCMODE) == 0)
            return -EBADF;
        if (f->flags & O_APPEND)
            f->offset = f->size;

        uint32_t capacity = of_nvslot_capacity(f->slot_id);
        if (capacity == 0)
            return -EIO;
        if (f->offset >= capacity)
            return 0;
        uint32_t available = capacity - f->offset;
        uint32_t to_write = (uint32_t)count;
        if (to_write > available)
            to_write = available;

        int rc = of_nvslot_write(f->slot_id, (const void *)buf,
                                 f->offset, to_write);
        if (rc > 0) {
            f->offset += rc;
            if (f->offset > f->size)
                f->size = f->offset;
            f->dirty = 1;
            save_size_cache_store(f->nv_cache_idx, f->size);
            of_nvslot_set_size(f->slot_id, f->size);
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
        if ((f->flags & O_ACCMODE) == O_WRONLY)
            return -EBADF;
        if (f->offset >= f->size)
            return 0;

        uint32_t to_read = (uint32_t)count;
        if (to_read > f->size - f->offset)
            to_read = f->size - f->offset;

        int rc = of_nvslot_read(f->slot_id, (void *)buf,
                                f->offset, to_read);
        if (rc > 0)
            f->offset += rc;
        return rc;
    }

    /* Lazy file size resolution — only probe on first read, not at fopen */
    if (f->size == 0 && f->slot_id > 0 && f->offset == 0) {
        long sz = of_file_size(f->slot_id);
        if (sz > 0)
            f->size = (uint32_t)sz;
        /* If size unknown (-1), leave f->size as 0 and read without
         * bounds checking — the bridge returns short/zero when EOF. */
    }

    uint32_t to_read = (uint32_t)count;
    if (f->size > 0 && f->offset + to_read > f->size) {
        if (f->offset >= f->size)
            return 0;
        to_read = f->size - f->offset;
    }

    /* I/O cache: serve reads from an SDRAM-backed LRU cache.
     * Each cache block is 32KB, keyed by (slot_id, aligned_offset).
     * The Pocket bridge backend internally chunks APF reads through
     * CRAM0 scratch before copying into this cache. */

    uint8_t *dst = (uint8_t *)buf;
    uint32_t done = 0;

    while (done < to_read) {
        uint32_t aligned_off = f->offset & ~(IO_CACHE_BLOCK_SIZE - 1);
        uint32_t buf_off = f->offset - aligned_off;

        /* Look up in I/O cache */
        int entry = io_cache_lookup(f->slot_id, aligned_off);

        if (entry < 0) {
            /* Cache miss — evict LRU and fill from bridge DMA */
            entry = io_cache_evict();

            uint32_t fill = IO_CACHE_BLOCK_SIZE;
            if (f->size > 0 && aligned_off + fill > f->size)
                fill = f->size - aligned_off;
            if (fill == 0) break;

            int rc = io_cache_fill(entry, f->slot_id, aligned_off, fill);
            if (rc < 0) {
                if (f->size == 0) {
                    f->size = f->offset;
                    break;
                }
                if (done > 0) break;
                return (long)rc;
            }
        }

        /* Serve from cache entry */
        uint32_t avail = io_cache[entry].valid - buf_off;
        uint32_t n = to_read - done;
        if (n > avail) n = avail;
        if (n == 0) break;

        /* Cache data is already in SDRAM; the bridge backend handled
         * CRAM0 scratch ownership and chunking when filling the entry. */
        memcpy(dst + done, io_cache_data(entry) + buf_off, n);
        f->offset += n;
        done += n;
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

static uint32_t save_clamp_size(uint32_t size) {
    return (size > SAVE_SLOT_SIZE) ? SAVE_SLOT_SIZE : size;
}

static void save_size_cache_store(int cache_idx, uint32_t size) {
    if (cache_idx < 0 || cache_idx >= (int)NV_CACHE_SLOTS)
        return;
    save_size_cache[cache_idx] = save_clamp_size(size);
    save_size_known[cache_idx] = 1;
}

/* Force a re-read from the HAL on the next save_current_size() call.
 * Use after a HAL operation fails — the in-memory cache may no longer
 * reflect the APF datatable state. */
static void save_size_cache_invalidate(int cache_idx) {
    if (cache_idx < 0 || cache_idx >= (int)NV_CACHE_SLOTS)
        return;
    save_size_known[cache_idx] = 0;
}

static uint32_t nv_current_size(uint32_t slot_id, int cache_idx) {
    if (cache_idx < 0 || cache_idx >= (int)NV_CACHE_SLOTS)
        return 0;

    if (save_size_known[cache_idx])
        return save_size_cache[cache_idx];

    long sz = of_file_size(slot_id);
    uint32_t size = 0;
    if (sz > 0)
        size = save_clamp_size((uint32_t)sz);

    save_size_cache_store(cache_idx, size);
    return size;
}

static long open_nv_fd(int fd, uint32_t slot_id, long flags) {
    int cache_idx = nv_cache_index_from_data_id(slot_id);
    if (cache_idx < 0 || !of_nvslot_is_supported(slot_id)) {
        fd_table[fd].in_use = 0;
        return -EINVAL;
    }

    fd_entry_t *f = &fd_table[fd];
    f->slot_id = slot_id;
    f->is_save = 1;
    f->save_slot = save_slot_from_data_id(slot_id);
    f->nv_cache_idx = cache_idx;
    f->size = nv_current_size(slot_id, cache_idx);

    if (flags & O_TRUNC) {
        /* Update the HAL first; only commit cached state if the HW
         * accepted the truncation.  Otherwise a later close() would
         * re-flush the bogus zero-size through a known-broken path. */
        if (of_nvslot_set_size(slot_id, 0) != 0) {
            fd_table[fd].in_use = 0;
            return -EIO;
        }
        f->size = 0;
        f->dirty = 1;
        save_size_cache_store(cache_idx, 0);
    }

    if (flags & O_APPEND)
        f->offset = f->size;

    of_save_begin_cpu();
    return fd;
}

/*
 * sys_openat -- Open a file by path.
 *
 * Path conventions:
 *   "slot:<N>"  Open APF data slot N.
 *   "save:<N>"  Open save slot N (backward-compatible alias).
 *   filename    Open any registered data-slot filename.
 *
 * Read-only slots reject write opens. Nonvolatile config/settings and
 * save slots are read/write files backed by CRAM0. Dirty closes publish
 * the logical size; Pocket persists them on core exit.
 */
static long sys_openat(long dirfd, long pathname, long flags, long mode) {
    (void)dirfd;
    (void)mode;
    const char *path = (const char *)pathname;
    if (!path)
        return -EINVAL;
    /* Empty path matches Linux's open("", ...) semantics: -ENOENT.
     * Rejecting here prevents file_slot_lookup("") from resolving to
     * any slot that happens to carry an empty filename (stricmp("","")
     * returns 0). Must come before alloc_fd() so no FD is leaked. */
    if (!path[0])
        return -ENOENT;

    int fd = alloc_fd();
    if (fd < 0)
        return fd;

    fd_entry_t *f = &fd_table[fd];
    f->offset = 0;
    f->flags = (int)flags;
    f->is_save = 0;
    f->save_slot = -1;
    f->nv_cache_idx = -1;
    f->slot_id = 0;
    f->size = 0;
    f->dirty = 0;
    f->is_dir = 0;
    f->dir_pos = 0;

    /* O_DIRECTORY — return a directory FD for getdents64.
     * filesystem_init() already populated file_slots at boot; probe
     * lazily only if the FTAB is empty (e.g. sim target without a
     * boot-time init). */
    if (flags & O_DIRECTORY) {
        if (file_slot_count == 0)
            dir_probe_slots();
        f->is_dir = 1;
        return fd;
    }

    /* "slot:<N>" -- APF data slot. Save-slot IDs are writable; all
     * other slots are read-only. */
    if (prefix_match(path, "slot:", 5)) {
        const char *p = path + 5;
        if (*p < '0' || *p > '9') {
            fd_table[fd].in_use = 0;
            return -EINVAL;
        }
        int slot = parse_int(&p);
        if (of_nvslot_is_supported((uint32_t)slot))
            return open_nv_fd(fd, (uint32_t)slot, flags);
        if (open_flags_want_write(flags)) {
            fd_table[fd].in_use = 0;
            return -EACCES;
        }
        f->slot_id = (uint32_t)slot;
        f->size = 0;  /* Resolved lazily on first read */
        return fd;
    }

    /* "save:<N>" or "save_<N>" -- save slot (read/write, 256 KB) */
    if (prefix_match(path, "save:", 5) || prefix_match(path, "save_", 5)) {
        const char *p = path + 5;
        if (*p < '0' || *p > '9') {
            fd_table[fd].in_use = 0;
            return -EINVAL;
        }
        int slot = parse_int(&p);
        return open_nv_fd(fd, SAVE_SLOT_ID_BASE + (uint32_t)slot, flags);
    }

    /* Search file slot registry by basename (case-insensitive) */
    int slot = file_slot_lookup(path);
    if (slot >= 0) {
        if (of_nvslot_is_supported((uint32_t)slot))
            return open_nv_fd(fd, (uint32_t)slot, flags);

        if (open_flags_want_write(flags)) {
            fd_table[fd].in_use = 0;
            return -EACCES;
        }

        /* Reject registered names whose slot is unbacked (size == 0).
         * Without this, fopen returns a valid FD and subsequent reads
         * serve stale bytes from the I/O cache's bounce buffer. */
        long sz = of_file_size(slot);
        if (sz <= 0) {
            fd_table[fd].in_use = 0;
            return -ENOENT;
        }
        f->slot_id = (uint32_t)slot;
        f->size = (uint32_t)sz;
        return fd;
    }

    int save_slot = save_slot_from_filename(path);
    if (save_slot >= 0)
        return open_nv_fd(fd, SAVE_SLOT_ID_BASE + (uint32_t)save_slot, flags);

    int settings_slot = nv_settings_slot_from_filename(path);
    if (settings_slot >= 0 && of_nvslot_is_supported((uint32_t)settings_slot))
        return open_nv_fd(fd, (uint32_t)settings_slot, flags);

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
        fd_entry_t *f = &fd_table[fd];
        uint32_t save_size = f->size;
        int rc = 0;
        if (f->dirty) {
            rc = of_nvslot_set_size(f->slot_id, save_size);
            /* POSIX: close() always closes the fd.  Nonvolatile files
             * all follow the same path: writes update the CRAM0 window,
             * close() publishes the logical size to the APF datatable,
             * and the Pocket nonvolatile exit path persists the slot.
             *
             * Config/settings used to issue an immediate DS_CMD_WRITE
             * here, but Duke save slots are stable specifically because
             * they avoid in-game target-to-host writes and rely on the
             * automatic writeback path.  Keep settings on that same path. */
            if (rc == 0)
                save_size_cache_store(f->nv_cache_idx, save_size);
            else
                save_size_cache_invalidate(f->nv_cache_idx);
        }
        f->in_use = 0;
        of_save_end_cpu();
        return rc;
    }

    fd_table[fd].in_use = 0;
    return 0;
}

/* Services table helpers — non-static so services_table.c can call them */
long sys_openat_svc(const char *path) {
    return sys_openat(-100, (long)path, 0, 0);
}

void sys_close_svc(int fd) {
    sys_close((long)fd);
}

long sys_file_size_fd(int fd) {
    if (fd < 0 || fd >= MAX_FDS || !fd_table[fd].in_use)
        return -1;
    fd_entry_t *f = &fd_table[fd];
    if (f->is_save)
        return (long)f->size;
    if (f->size == 0 && f->slot_id > 0) {
        long sz = of_file_size(f->slot_id);
        if (sz > 0) f->size = (uint32_t)sz;
    }
    return (long)f->size;
}

/* _llseek: (fd, offset_hi, offset_lo, &result, whence) — riscv32 uses this */
static long sys_llseek(long fd, long off_hi, long off_lo,
                       long result_ptr, long whence) {
    (void)off_hi; /* 32-bit offsets only */
    if (fd < 0 || fd >= MAX_FDS || !fd_table[fd].in_use)
        return -EBADF;

    fd_entry_t *f = &fd_table[fd];
    long offset = off_lo;
    long new_offset;

    /* Resolve file size for SEEK_END */
    if (whence == 2 && !f->is_save && f->size == 0 && f->slot_id > 0) {
        long sz = of_file_size(f->slot_id);
        if (sz > 0) f->size = (uint32_t)sz;
    }

    switch (whence) {
    case 0: new_offset = offset; break;
    case 1: new_offset = (long)f->offset + offset; break;
    case 2: new_offset = (long)f->size + offset; break;
    default: return -EINVAL;
    }

    if (new_offset < 0)
        return -EINVAL;

    f->offset = (uint32_t)new_offset;

    /* I/O cache is keyed by (slot_id, aligned_offset) — seeks just
     * change f->offset and the cache naturally serves the right block. */

    /* _llseek writes result to user pointer as 64-bit */
    if (result_ptr) {
        int64_t *res = (int64_t *)result_ptr;
        *res = (int64_t)new_offset;
    }
    return 0;  /* _llseek returns 0 on success, -1 on error */
}

/* Legacy 32-bit timespec layout (8 bytes): tv_sec + tv_nsec are both
 * 32-bit. Used by direct callers of SYS_clock_* (113/114/115). */
struct ts32 { uint32_t tv_sec; uint32_t tv_nsec; };

/* time64 timespec layout (16 bytes): int64 tv_sec + long tv_nsec, with
 * 4 bytes of tail padding so the struct is 16-byte aligned. riscv32
 * musl sends this for SYS_clock_*64 (403/406/407). Reading it with the
 * 32-bit layout would alias tv_nsec onto the high half of tv_sec and
 * silently corrupt every clock-related syscall. */
struct ts64 { int64_t tv_sec; int32_t tv_nsec; int32_t _pad; };

static long sys_clock_gettime(long clk_id, long tp) {
    (void)clk_id;
    struct ts32 *ts = (void *)tp;
    uint32_t ns;
    ts->tv_sec = of_timer_get_seconds(&ns);
    ts->tv_nsec = ns;
    return 0;
}

static long sys_clock_gettime_time64(long clk_id, long tp) {
    (void)clk_id;
    struct ts64 *ts = (void *)tp;
    if (!ts) return 0;
    uint32_t ns;
    ts->tv_sec = (int64_t)of_timer_get_seconds(&ns);
    ts->tv_nsec = (int32_t)ns;
    return 0;
}

static long sys_clock_getres(long clk_id, long tp) {
    (void)clk_id;
    struct ts32 *ts = (void *)tp;
    if (ts) { ts->tv_sec = 0; ts->tv_nsec = 10; }
    return 0;
}

static long sys_clock_getres_time64(long clk_id, long tp) {
    (void)clk_id;
    struct ts64 *ts = (void *)tp;
    if (ts) { ts->tv_sec = 0; ts->tv_nsec = 10; }
    return 0;
}

static long sys_clock_nanosleep(long clk_id, long flags,
                                long rqtp, long rmtp) {
    (void)clk_id; (void)flags; (void)rmtp;
    struct ts32 *ts = (void *)rqtp;
    if (!ts) return 0;
    uint32_t us = ts->tv_sec * 1000000 + ts->tv_nsec / 1000;
    if (us > 0) of_timer_delay_us(us);
    return 0;
}

static long sys_clock_nanosleep_time64(long clk_id, long flags,
                                       long rqtp, long rmtp) {
    (void)clk_id; (void)flags; (void)rmtp;
    struct ts64 *ts = (void *)rqtp;
    if (!ts) return 0;
    /* Clamp tv_sec to 32 bits — the hardware timer wraps at ~71 minutes
     * either way, so a longer requested sleep can't be honored anyway. */
    uint32_t sec = (uint32_t)(ts->tv_sec & 0xFFFFFFFF);
    uint32_t us  = sec * 1000000u + (uint32_t)ts->tv_nsec / 1000u;
    if (us > 0) of_timer_delay_us(us);
    return 0;
}

/* -----------------------------------------------------------------
 * mmap region tracker
 *
 * Each successful sys_mmap2 carves a region from `mmap_bottom`
 * downward.  Without tracking, sys_munmap was a no-op and the region
 * was leaked forever — any app doing alloc/free cycles of sizes >=
 * musl's MMAP_THRESHOLD (e.g. the 48-size loop in testdemo's
 * test_malloc) would cumulatively consume the entire app mmap
 * window, after which malloc returns NULL for all subsequent sizes
 * that fall on the mmap path.
 *
 * Strategy: keep a bounded table of {base, len, free?} entries.
 *   - mmap: first reclaim by coalescing free entries adjacent to
 *     mmap_bottom (walking the table), then try to reuse an existing
 *     free entry of suitable size, then carve new space by lowering
 *     mmap_bottom.
 *   - munmap: find the entry that matches (base, len), mark it free.
 *     If the freed entry is at mmap_bottom itself, raise mmap_bottom
 *     immediately.
 *
 * MMAP_SLOTS sized for test workloads and typical musl mallocng
 * behaviour (tens of concurrent large allocations); bump if needed.
 * ----------------------------------------------------------------- */
/* Sized for mallocng's slab-per-size-class arenas: musl's allocator
 * mmaps a fresh slab the first time any size class is touched, so an
 * app pulling in many small allocations of diverse sizes can easily
 * hold ~70+ concurrent slabs.  256 keeps headroom for a heavy workload
 * without costing much BSS (12 B per slot). */
#define MMAP_SLOTS 256

typedef struct {
    uintptr_t base;     /* 0 = slot unused */
    uintptr_t len;      /* page-aligned */
    uint8_t   free;     /* 1 = munmapped, reusable */
} mmap_slot_t;

static mmap_slot_t mmap_slots[MMAP_SLOTS];
static int         mmap_slots_used;

static void mmap_coalesce_bottom(void) {
    /* Walk free slots; any free slot whose BASE equals mmap_bottom
     * sits exactly at the bottom of the carved arena and can be
     * reclaimed by raising mmap_bottom up by its length.  Carved
     * slots grow downward from the initial 0x13400000, so mmap_bottom
     * always equals the lowest currently-allocated (or just-freed)
     * address; a free slot starting at mmap_bottom is therefore the
     * region we can reabsorb into the bulk free arena.
     *
     * Iterate to fixed point — raising mmap_bottom can expose the
     * next-lowest free slot for the same treatment on the next pass. */
    int changed;
    do {
        changed = 0;
        for (int i = 0; i < MMAP_SLOTS; i++) {
            mmap_slot_t *s = &mmap_slots[i];
            if (!s->base || !s->free)
                continue;
            if (s->base == mmap_bottom) {
                mmap_bottom = s->base + s->len;
                s->base = 0;
                s->len  = 0;
                s->free = 0;
                mmap_slots_used--;
                changed = 1;
            }
        }
    } while (changed);
}

static int mmap_find_reusable(uintptr_t want_len) {
    /* Exact-fit only.  Splitting a larger free slot and returning the
     * tail has correctness pitfalls (mallocng's metadata in the head
     * part can be read through the mmap arena even after we mark it
     * reusable), so we trade the potential fragmentation for
     * predictability: a slot is reused only when an earlier munmap
     * released exactly the size now being requested.  mallocng's
     * usage pattern is size-class driven and usually does this — the
     * 48 MB malloc/free loop in testdemo's test_malloc frees each
     * mmap at the same size it was allocated at, so exact-fit alone
     * recovers all of it.  Anything without an exact match falls
     * through to carve-from-top. */
    for (int i = 0; i < MMAP_SLOTS; i++) {
        mmap_slot_t *s = &mmap_slots[i];
        if (!s->base || !s->free) continue;
        if (s->len == want_len) return i;
    }
    return -1;
}

static int mmap_alloc_slot(void) {
    for (int i = 0; i < MMAP_SLOTS; i++)
        if (!mmap_slots[i].base)
            return i;
    return -1;
}

static long sys_mmap2(long addr, long length, long prot,
                      long flags, long fd, long pgoffset) {
    (void)addr; (void)prot; (void)flags; (void)fd; (void)pgoffset;
    if (length <= 0)
        return -EINVAL;

    uintptr_t ulen = (uintptr_t)length;

    /* Page-align, rejecting carry-out of +4095 (musl can pass sizes
     * near SIZE_MAX when mallocng probes the mmap ceiling). */
    uintptr_t len_aligned = (ulen + 4095u) & ~4095u;
    if (len_aligned < ulen)
        return -ENOMEM;

    /* First try to reclaim space from previously munmapped regions
     * that are contiguous with mmap_bottom, then look for an exact/
     * best-fit free slot to reuse. */
    mmap_coalesce_bottom();

    int reuse = mmap_find_reusable(len_aligned);
    if (reuse >= 0) {
        mmap_slot_t *s = &mmap_slots[reuse];
        /* Exact-fit: flip free -> in-use in place.  memset ensures
         * mallocng sees a zeroed region, same as a fresh carve. */
        s->free = 0;
        memset((void *)s->base, 0, s->len);
        return (long)s->base;
    }

    /* Snapshot the two pointers through a volatile barrier.  Earlier
     * fix rounds showed GCC would optimize away whichever bound it
     * could prove redundant from an earlier inequality, leaving only
     * `base < current_brk` — which is useless against an underflowed
     * base that's numerically larger than current_brk.  The volatile
     * forces the compiler to treat mb/cb as opaque and keep every
     * explicit check we write. */
    volatile uintptr_t *mb_p = &mmap_bottom;
    volatile uintptr_t *cb_p = &current_brk;
    uintptr_t mb = *mb_p;
    uintptr_t cb = *cb_p;

    /* len_aligned must fit below mmap_bottom (prevents subtraction
     * underflow) AND above current_brk (prevents carving into brk). */
    if (len_aligned > mb)
        return -ENOMEM;

    uintptr_t base = mb - len_aligned;
    uintptr_t end  = base + len_aligned;     /* always == mb */

    if (base < cb)
        return -ENOMEM;
    if (end < base)              /* addition wrapped — shouldn't happen */
        return -ENOMEM;
    if (end > mb)                /* tautology, but forces the range to be sane */
        return -ENOMEM;

    /* Hard sanity: reject any base outside the SDRAM+CRAM window.
     * The portable app memory map lives in 0x10000000–0x3FFFFFFF.
     * Anything else (0x00xxxxxx BRAM, 0x4xxxxxxx peripherals, or
     * the 0xF-range unmapped addresses that produced the original
     * 0xFFEEFFF0 trap) is never a valid mmap target — fail here
     * instead of letting memset scribble a peripheral. */
    if (base < 0x10000000u || end > 0x40000000u)
        return -ENOMEM;

    /* Need a tracking slot before we commit.  If the table is full,
     * fail the allocation rather than losing track of it — a leaked
     * region would cause munmap to not find it and corruption later. */
    int slot = mmap_alloc_slot();
    if (slot < 0)
        return -ENOMEM;

    memset((void *)base, 0, len_aligned);
    *mb_p = base;

    mmap_slots[slot].base = base;
    mmap_slots[slot].len  = len_aligned;
    mmap_slots[slot].free = 0;
    mmap_slots_used++;

    return (long)base;
}

static long sys_munmap(long addr, long length) {
    if (!addr || length <= 0)
        return 0;    /* Linux tolerates 0-length unmap as a no-op. */

    uintptr_t base = (uintptr_t)addr;
    uintptr_t len  = ((uintptr_t)length + 4095u) & ~4095u;

    /* Find the exact allocation.  musl's mallocng pairs each mmap
     * with a matching munmap(base, size), so an exact-match lookup
     * covers the common case. */
    for (int i = 0; i < MMAP_SLOTS; i++) {
        mmap_slot_t *s = &mmap_slots[i];
        if (s->base != base || s->len != len || s->free)
            continue;

        /* If this region sits right at mmap_bottom, reclaim by
         * raising mmap_bottom and freeing the slot outright —
         * that's the LIFO case (very common for mallocng). */
        if (base == mmap_bottom) {
            mmap_bottom = base + len;
            s->base = 0;
            s->len  = 0;
            s->free = 0;
            mmap_slots_used--;
            /* Chain-coalesce any free slots that are now adjacent
             * to the newly-raised mmap_bottom. */
            mmap_coalesce_bottom();
        } else {
            /* Middle-of-arena free: mark reusable, keep in table. */
            s->free = 1;
        }
        return 0;
    }

    /* No matching region — treat as a no-op.  Could be a partial
     * munmap that musl never issues, or a double-free on the app
     * side; neither warrants a failure return. */
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

/* ----------------------------------------------------------------------------
 * Per-subsystem dispatchers for the SBI vendor extensions.
 *
 * Each takes the FID (a6) plus the call args. They return a long (the
 * raw value, or the negative error to be reported as sbiret.error).
 * The top-level dispatcher wraps this into struct of_sbiret.
 * ---------------------------------------------------------------------------- */

static long of_video_dispatch(long fid, long a0, long a1) {
    switch (fid) {
    case OF_VIDEO_FID_INIT:
        of_video_init();
        return 0;
    case OF_VIDEO_FID_FLIP:
        return (long)of_video_flip();
    case OF_VIDEO_FID_WAIT_FLIP:
        of_video_wait_flip();
        return 0;
    case OF_VIDEO_FID_SET_PALETTE:
        of_video_set_palette((uint8_t)a0, (uint8_t)(a1 >> 16),
                             (uint8_t)(a1 >> 8), (uint8_t)a1);
        return 0;
    case OF_VIDEO_FID_GET_SURFACE:
        return (long)of_video_get_surface();
    case OF_VIDEO_FID_SET_DISPLAY_MODE:
        of_video_set_display_mode((int)a0);
        return 0;
    case OF_VIDEO_FID_CLEAR:
        of_video_clear((uint8_t)a0);
        return 0;
    case OF_VIDEO_FID_SET_PALETTE_BULK:
        of_video_set_palette_bulk((const uint32_t *)a0, (int)a1);
        return 0;
    case OF_VIDEO_FID_FLUSH_CACHE:
        of_video_flush_cache();
        return 0;
    case OF_VIDEO_FID_SET_COLOR_MODE:
        SYS_COLOR_MODE = (uint32_t)a0;
        return 0;
    case OF_VIDEO_FID_SET_PALETTE_VGA4:
        of_video_set_palette_vga4((const uint8_t *)a0, (int)a1);
        return 0;
    case OF_VIDEO_FID_VSYNC:
        of_video_vsync();
        return 0;
    case OF_VIDEO_FID_SET_VSYNC_CALLBACK:
        of_irq_register_vsync((void (*)(void))a0);
        return 0;
    default:
        return OF_ERR_NOT_SUPPORTED;
    }
}

static long of_audio_dispatch(long fid, long a0, long a1) {
    switch (fid) {
    case OF_AUDIO_FID_WRITE:
        return of_audio_write((const int16_t *)a0, (int)a1);
    case OF_AUDIO_FID_GET_FREE:
        return of_audio_get_free();
    case OF_AUDIO_FID_INIT:
        of_audio_init();
        return 0;
    default:
        return OF_ERR_NOT_SUPPORTED;
    }
}

static long of_input_dispatch(long fid, long a0, long a1) {
    switch (fid) {
    case OF_INPUT_FID_POLL:
        of_input_poll();
        return 0;
    case OF_INPUT_FID_GET_STATE: {
        const of_input_state_t *state = of_input_get_state((int)a0);
        if (a1)
            memcpy((void *)a1, state, sizeof(of_input_state_t));
        return (long)state->buttons;
    }
    case OF_INPUT_FID_SET_DEADZONE:
        of_input_set_deadzone((int16_t)a0);
        return 0;
    case OF_INPUT_FID_POLL_P0:
        of_input_poll_p0((of_input_state_t *)a0);
        return 0;
    case OF_INPUT_FID_GET_KEYBOARD_STATE: {
        const of_keyboard_state_t *state = of_input_get_keyboard_state();
        if (a0)
            memcpy((void *)a0, state, sizeof(of_keyboard_state_t));
        return state->present;
    }
    case OF_INPUT_FID_READ_MOUSE_STATE:
        of_input_read_mouse_state((of_mouse_state_t *)a0);
        return 0;
    default:
        return OF_ERR_NOT_SUPPORTED;
    }
}


/* ======================================================================
 * Main syscall dispatch (called from trap handler via ecall)
 * ====================================================================== */

/* Forward declaration of the SBI vendor dispatcher (defined below). */
static long of_vendor_dispatch(long eid, long fid,
                               long a0, long a1, long a2,
                               long a3, long a4, long a5);

/* Linux-compat dispatcher: a7 was the syscall number, return goes in a0.
 * Wrapped by syscall_dispatch into sbiret { error=ret, value=0 } so the
 * trap handler's unconditional store of a0/a1 still presents the right
 * value to musl-built code (which only reads a0). */
static long linux_dispatch(long n, long a0, long a1, long a2,
                           long a3, long a4, long a5);

__attribute__((used))
struct of_sbiret syscall_dispatch(long a0, long a1, long a2, long a3,
                                  long a4, long a5, long fid, long eid) {
    /* SBI vendor extensions live in the openfpgaOS namespace. */
    if (OF_EID_IS_VENDOR(eid)) {
        long val = of_vendor_dispatch(eid, fid, a0, a1, a2, a3, a4, a5);
        /* Convention: vendor helpers return either a non-negative value
         * (success -> goes in sbiret.value) or one of the of_error.h
         * negative codes (failure -> goes in sbiret.error). */
        if (val < 0)
            return (struct of_sbiret){ .error = val, .value = 0 };
        return (struct of_sbiret){ .error = OF_OK, .value = val };
    }

    /* Otherwise this is a Linux-compat syscall (musl/POSIX path).
     *
     * Linux ABI returns the value (or negative errno) in a0 alone. The
     * trap handler in start.S decides whether to write a1 back to the
     * user frame based on the EID range -- for Linux-range EIDs it
     * restores the user's original a1 (from a callee-saved register
     * stashed before the C call), so anything we put in sbiret.value
     * here is ignored. We pass 0 to make that intent explicit. */
    long ret = linux_dispatch(eid, a0, a1, a2, a3, a4, a5);
    return (struct of_sbiret){ .error = ret, .value = 0 };
}

static long linux_dispatch(long n, long a0, long a1, long a2,
                           long a3, long a4, long a5) {
    /* Shutdown handshake is auto-acked in FPGA (core_top.v) —
     * no CPU involvement needed. */

    switch (n) {
    case SYS_brk:           return sys_brk(a0);
    case SYS_write:         return sys_write(a0, a1, a2);
    case SYS_read:          return sys_read(a0, a1, a2);
    case SYS_readv:         return sys_readv(a0, a1, a2);
    case SYS_writev:        return sys_writev(a0, a1, a2);
    case SYS_openat:        return sys_openat(a0, a1, a2, a3);
    case SYS_close:         return sys_close(a0);
    case SYS_getdents64:    return sys_getdents64((int)a0, (void *)a1, (uint32_t)a2);
    case SYS_lseek:         return sys_llseek(a0, a1, a2, a3, a4);
    case SYS_clock_gettime:           /* legacy 113 — direct callers */
        return sys_clock_gettime(a0, a1);
    case SYS_clock_gettime64:         /* 403 — riscv32 musl */
        return sys_clock_gettime_time64(a0, a1);
    case SYS_clock_getres:            /* legacy 114 */
        return sys_clock_getres(a0, a1);
    case SYS_clock_getres64:          /* 406 — riscv32 musl */
        return sys_clock_getres_time64(a0, a1);
    case SYS_clock_nanosleep:         /* legacy 115 */
        return sys_clock_nanosleep(a0, a1, a2, a3);
    case SYS_clock_nanosleep_time64:  /* 407 — riscv32 musl usleep/nanosleep */
        return sys_clock_nanosleep_time64(a0, a1, a2, a3);
    case SYS_mmap2:         return sys_mmap2(a0, a1, a2, a3, a4, a5);
    case SYS_munmap:        return sys_munmap(a0, a1);
    case SYS_mprotect:      return 0;
    case SYS_madvise:       return 0;
    case SYS_futex:         return 0;   /* single-threaded — no real locking needed */
    case SYS_tkill:         return 0;   /* single-threaded — abort() sends SIGABRT here */

    case SYS_exit:
    case SYS_exit_group:
        /* Switch scanout back to 8-bit terminal FB so user sees boot screen
         * even if the app left RGB565/low-bit framebuffer mode selected. */
        SYS_COLOR_MODE = COLOR_MODE_8BIT;
        TERM_FB_CTRL = 1;
        while (1) {}
        return 0;

    case SYS_getpid:        return 1;
    case SYS_gettid:        return 1;
    case SYS_set_tid_address: return 1;

    case SYS_rt_sigaction: {
        /* a0=signum, a1=act, a2=oldact, a3=sigsetsize */
        if (a0 == SIGALRM) {
            if (a2) {
                uint32_t *old = (uint32_t *)a2;
                old[0] = (uint32_t)(uintptr_t)sigalrm_handler;
            }
            if (a1) {
                uint32_t *act = (uint32_t *)a1;
                sigalrm_handler = (void (*)(int))(uintptr_t)act[0];
            }
        }
        return 0;
    }
    case SYS_rt_sigprocmask: return 0;

    case SYS_setitimer: {
        /* a0=which (ITIMER_REAL=0), a1=new itimerval, a2=old itimerval.
         * Both pointers come from userspace and feed kernel memory ops
         * (memset to a2, 16-byte read from a1).  Reject anything
         * outside the SDRAM window so a buggy/malicious app can't
         * scribble MMIO or kernel BSS.  Range matches the mmap2 sanity
         * check below (cached + uncached SDRAM + CRAM0). */
        if (a0 != 0) return -EINVAL;
        if (a1) {
            unsigned long e = (unsigned long)a1 + 16;
            if ((unsigned long)a1 < 0x10000000ul || e > 0x54000000ul ||
                e < (unsigned long)a1)
                return -EFAULT;
        }
        if (a2) {
            unsigned long e = (unsigned long)a2 + 16;
            if ((unsigned long)a2 < 0x10000000ul || e > 0x54000000ul ||
                e < (unsigned long)a2)
                return -EFAULT;
            memset((void *)a2, 0, 16);  /* zero old value */
        }
        if (a1) {
            uint32_t *nv = (uint32_t *)a1;
            uint32_t sec  = nv[0];  /* it_interval.tv_sec */
            uint32_t usec = nv[1];  /* it_interval.tv_usec */
            uint64_t cycles = (uint64_t)sec * CPU_FREQ_HZ
                            + (uint64_t)usec * (CPU_FREQ_HZ / 1000000);
            if (cycles == 0) {
                /* Check it_value too */
                sec  = nv[2];
                usec = nv[3];
                cycles = (uint64_t)sec * CPU_FREQ_HZ
                       + (uint64_t)usec * (CPU_FREQ_HZ / 1000000);
            }
            if (cycles == 0) {
                TIMER_CTRL = 0;  /* disarm */
            } else {
                TIMER_PERIOD = (uint32_t)(cycles > 0xFFFFFFFF ? 0xFFFFFFFF : cycles);
                TIMER_CTRL = TIMER_CTRL_ENABLE;
            }
        }
        return 0;
    }

    case SYS_statx: {
        /* a0=dirfd, a1=path, a2=flags, a3=mask, a4=statxbuf
         * Two modes:
         *   fstat: dirfd=fd, path="", flags=AT_EMPTY_PATH
         *   stat:  dirfd=AT_FDCWD, path="filename", flags=0 */
        long statx_fd = a0;
        uint32_t file_size = 0;

        if (statx_fd >= 0 && statx_fd < MAX_FDS && fd_table[statx_fd].in_use) {
            /* fstat mode: use existing fd */
            fd_entry_t *f = &fd_table[statx_fd];
            if (f->size == 0 && f->slot_id > 0) {
                long sz = of_file_size(f->slot_id);
                if (sz > 0) f->size = (uint32_t)sz;
            }
            file_size = f->size;
        } else if (a1) {
            /* stat mode: open file by path, get size, close */
            long fd = sys_openat(a0, a1, 0, 0);
            if (fd >= 0) {
                fd_entry_t *f = &fd_table[fd];
                if (f->size == 0 && f->slot_id > 0) {
                    long sz = of_file_size(f->slot_id);
                    if (sz > 0) f->size = (uint32_t)sz;
                }
                file_size = f->size;
                fd_table[fd].in_use = 0;
            } else {
                return -ENOENT;
            }
        } else {
            return -EBADF;
        }

        uint32_t *sx = (uint32_t *)a4;
        memset(sx, 0, 256);
        sx[0] = 0x7FF;         /* stx_mask (offset 0) */
        sx[10] = file_size;    /* stx_size low 32 (offset 40) */
        sx[11] = 0;            /* stx_size high 32 */
        return 0;
    }

    case SYS_fstat: {
        if (a0 >= 0 && a0 < MAX_FDS && fd_table[a0].in_use) {
            fd_entry_t *f = &fd_table[a0];
            if (f->size == 0 && f->slot_id > 0) {
                long sz = of_file_size(f->slot_id);
                if (sz > 0) f->size = (uint32_t)sz;
            }
            struct { uint64_t __pad[6]; uint32_t st_size; } *st = (void *)a1;
            memset(st, 0, 128);
            st->st_size = f->size;
            return 0;
        }
        return -EBADF;
    }
    case SYS_fstatat: {
        /* a0=dirfd, a1=path, a2=statbuf, a3=flags */
        /* Open the file, stat it, close */
        long fd = sys_openat(a0, a1, 0, 0);
        if (fd < 0) return fd;
        fd_entry_t *f = &fd_table[fd];
        if (f->size == 0 && f->slot_id > 0) {
            long sz = of_file_size(f->slot_id);
            if (sz > 0) f->size = (uint32_t)sz;
        }
        struct { uint64_t __pad[6]; uint32_t st_size; } *st = (void *)a2;
        memset(st, 0, 128);
        st->st_size = f->size;
        fd_table[fd].in_use = 0;
        return 0;
    }
    case SYS_fcntl:         return -ENOSYS;
    case SYS_ioctl:         return 0;   /* stub — makes musl stdout line-buffered */
    case SYS_faccessat:     return -ENOSYS;

    case SYS_riscv_flush_icache:
        of_cache_invalidate_icache();
        return 0;

    default:
        return -ENOSYS;
    }
}

/* ----------------------------------------------------------------------------
 * SBI vendor dispatcher (openfpgaOS extensions, EID >= OF_EID_BASE_VALUE)
 *
 * Top-level switch on EID, then a per-subsystem switch (or call to a
 * helper) on FID. Adding a new function: append a FID to the matching
 * enum in api/of_syscall_numbers.h, then add a case here.
 * ---------------------------------------------------------------------------- */

static long of_vendor_dispatch(long eid, long fid,
                               long a0, long a1, long a2,
                               long a3, long a4, long a5) {
    (void)a5;  /* no current FID uses 6 args; reserved for future */
    switch ((unsigned long)eid) {

    case OF_EID_BASE:
        switch (fid) {
        case OF_BASE_FID_GET_VERSION:
            return OF_API_VERSION;
        }
        break;

    case OF_EID_VIDEO:
        return of_video_dispatch(fid, a0, a1);

    case OF_EID_AUDIO:
        return of_audio_dispatch(fid, a0, a1);

    case OF_EID_INPUT:
        return of_input_dispatch(fid, a0, a1);

    case OF_EID_ANALOGIZER:
        switch (fid) {
        case OF_ANALOGIZER_FID_IS_ENABLED:
            return of_analogizer_is_enabled();
        case OF_ANALOGIZER_FID_GET_STATE:
            if (a0)
                memcpy((void *)a0, of_analogizer_get_state(),
                       sizeof(of_analogizer_state_t));
            return of_analogizer_is_enabled();
        }
        break;

    case OF_EID_NET:
        return of_net_dispatch(fid, a0, a1, a2);

    case OF_EID_TIMER:
        switch (fid) {
        case OF_TIMER_FID_SET_CALLBACK:
            timer_callback_ptr = (void (*)(void))a0;
            if (a0 && a1 > 0) {
                TIMER_PERIOD = CPU_FREQ_HZ / (uint32_t)a1;
                TIMER_CTRL = TIMER_CTRL_ENABLE;
            } else {
                TIMER_CTRL = 0;
            }
            return 0;
        case OF_TIMER_FID_STOP:
            TIMER_CTRL = 0;
            timer_callback_ptr = NULL;
            return 0;
        case OF_TIMER_FID_GET_US:
            return of_timer_get_us();
        case OF_TIMER_FID_GET_MS:
            return of_timer_get_ms();
        case OF_TIMER_FID_DELAY_US:
            of_timer_delay_us((uint32_t)a0);
            return 0;
        }
        break;

    case OF_EID_TILE:
    case OF_EID_SPRITE:
        /* Tile/sprite engine retired -- still occupies an EID for ABI
         * stability but every FID returns NOT_SUPPORTED. */
        return OF_ERR_NOT_SUPPORTED;

    case OF_EID_MEMORY:
        switch (fid) {
        case OF_MEMORY_FID_MALLOC:
            return (long)dlmalloc((size_t)a0);
        case OF_MEMORY_FID_FREE:
            dlfree((void *)a0);
            return 0;
        case OF_MEMORY_FID_REALLOC:
            return (long)dlrealloc((void *)a0, (size_t)a1);
        case OF_MEMORY_FID_CALLOC:
            return (long)dlcalloc((size_t)a0, (size_t)a1);
        }
        break;

    case OF_EID_MIXER:
        return of_mixer_dispatch(fid, a0, a1, a2, a3, a4);

    case OF_EID_CODEC:
        switch (fid) {
        case OF_CODEC_FID_PARSE_VOC:
            return of_codec_parse_voc((const uint8_t *)a0, (uint32_t)a1,
                                      (of_codec_result_t *)a2);
        case OF_CODEC_FID_PARSE_WAV:
            return of_codec_parse_wav((const uint8_t *)a0, (uint32_t)a1,
                                      (of_codec_result_t *)a2);
        }
        break;

    case OF_EID_LZW:
        switch (fid) {
        case OF_LZW_FID_COMPRESS:
            return of_lzw_compress((const uint8_t *)a0, (int32_t)a1,
                                   (uint8_t *)a2);
        case OF_LZW_FID_UNCOMPRESS:
            return of_lzw_uncompress((const uint8_t *)a0, (int32_t)a1,
                                     (uint8_t *)a2);
        }
        break;

    case OF_EID_INTERACT:
        switch (fid) {
        case OF_INTERACT_FID_GET: {
            int index = (int)a0;
            if (index < 0 || index >= 64) return 0;
            volatile uint32_t *vars = (volatile uint32_t *)INTERACT_UNCACHED;
            return (long)vars[index];
        }
        }
        break;

    case OF_EID_FILE:
        switch (fid) {
        case OF_FILE_FID_READ_ASYNC:
            return (long)of_file_read_async((uint32_t)a0, (uint32_t)a1,
                                            (void *)a2, (uint32_t)a3,
                                            (void (*)(int, int))a4);
        case OF_FILE_FID_ASYNC_POLL:
            return (long)of_file_async_poll();
        case OF_FILE_FID_ASYNC_BUSY:
            return (long)of_file_async_busy();
        }
        break;
    }

    return OF_ERR_NOT_SUPPORTED;
}

/* ======================================================================
 * Syscall subsystem initialization
 * ====================================================================== */

void syscall_init(uintptr_t heap_start) {
    memset(fd_table, 0, sizeof(fd_table));
    fd_table[FD_STDIN].in_use = 1;
    fd_table[FD_STDOUT].in_use = 1;
    fd_table[FD_STDERR].in_use = 1;
    brk_base    = heap_start;
    current_brk = heap_start;
    /* mmap grows down from APP_STACK_TOP - APP_STACK_SIZE.  The window
     * nominally ends at APP_VMAP_V1_SDRAM_END (0x13400000) for
     * portability, but on-target SDRAM extends further, and we let
     * mmap reclaim that tail so memory-hungry workloads don't run
     * out in the 48 MB portable window.  APP_VMAP_V1_SDRAM_END still
     * bounds PT_LOAD segments — just not mmap.
     *
     * Strict inequality vs APP_STACK_TOP is critical: sys_mmap2
     * zero-fills every carved region, and if mmap_bottom == stack
     * top the first mmap wipes whatever stack frames the app
     * already pushed.  Reserve the same 512 KB stack span used
     * above 0x13F80000 for the OS runtime stack. */
    mmap_bottom = 0x12F00000u;   /* APP_STACK_TOP (0x12F80000) - 512 KB */

    /* Reset mmap region tracker — the previous app's allocations
     * are no longer reachable once current_brk/mmap_bottom are
     * reseeded. */
    memset(mmap_slots, 0, sizeof(mmap_slots));
    mmap_slots_used = 0;

    /* Reset file slot registry (apps re-register on startup) */
    file_slot_count = 0;
    memset(file_slots, 0, sizeof(file_slots));

    /* Reset I/O cache */
    memset(io_cache, 0, sizeof(io_cache));
    io_lru_counter = 0;
}
