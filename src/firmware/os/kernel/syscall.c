//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Syscall Dispatch
 * Implements Linux-compatible syscalls for musl + openfpgaOS HAL extensions
 */

#include "syscall.h"
#include "irq.h"
#include "../hal/hal.h"
#include "../os_string.h"
#include "of_version.h"
#include "iso9660_vfs.h"
#include "launch.h"

/* dlmalloc functions (provided by of_malloc.c, USE_DL_PREFIX) */
extern void *dlmalloc(size_t);
extern void  dlfree(void *);
extern void *dlrealloc(void *, size_t);
extern void *dlcalloc(size_t, size_t);

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
#define EISDIR          21
#define EBUSY           16
#define EIO             5
#define EFAULT          14
#define ERANGE          34
#define EFBIG           27

#define AT_FDCWD        -100

/* ======================================================================
 * Timer / Signal state
 * ====================================================================== */

#define SIGALRM 14

static void (*sigalrm_handler)(int) = NULL;
void (*timer_callback_ptr)(void) = NULL;  /* non-static: accessed by services_table.c */

/* ---- Shared 100 MHz machine-timer policy --------------------------------
 * On a SW-audio-mixer build (of_mixer_use_sw, e.g. OS30 Pocket) the DAC FIFO
 * is fed ONLY by sw_mixer_pump() running inside timer_isr_callback, so the OS
 * must keep the machine timer firing at OS_TIMER_HZ (1 kHz) for the whole
 * session — independent of whatever rate, or none, an app requests.  An app's
 * requested callback / itimer rate is then recreated by dividing the 1 kHz
 * tick in software (timer_cb_div / sigalrm_div).  On a HW-mixer build both
 * dividers stay 1 and the HW timer takes the app's rate directly and is
 * disabled on clear (legacy behaviour, unchanged). */
#define OS_TIMER_HZ  1000u
static uint32_t timer_cb_div = 1u, timer_cb_cnt = 0u;  /* divides timer_callback_ptr */
static uint32_t sigalrm_div  = 1u, sigalrm_cnt  = 0u;  /* divides SIGALRM */

static inline uint32_t sys_irq_save_local(void)
{
    uint32_t prev;
    __asm__ volatile("csrrci %0, mstatus, 0x8"
                     : "=r"(prev) :: "memory");
    return prev & 0x8u;
}

static inline void sys_irq_restore_local(uint32_t prev)
{
    if (prev)
        __asm__ volatile("csrrsi zero, mstatus, 0x8" ::: "memory");
}

/* Software mixer DAC pump, driven from the 1 kHz audio tick when the HW
 * audio_mixer.v is cut (OS30).  Always linked; self-no-ops when a HW mixer
 * is present (of_mixer_use_sw==0), so the call is cheap on every build. */
void sw_mixer_pump(void);

/* Program the native timer callback (OF_TIMER / MIDI service path).
 *   cb==NULL      -> clear the callback
 *   self_divide!=0-> the consumer divides the tick itself (e.g. of_midi_pump
 *                    accumulates elapsed ms), so feed it the raw HW tick.
 * When of_mixer_use_sw the HW timer is pinned ON at 1 kHz (never disabled,
 * never slower) so the DAC pump keeps running; the app callback rate is
 * recreated by timer_cb_div.  Otherwise the HW timer takes the app rate
 * directly and is disabled on clear (legacy HW-mixer behaviour). */
void of_os_timer_set_callback(void (*cb)(void), uint32_t app_hz, int self_divide) {
    uint32_t irq = sys_irq_save_local();
    if (cb) {
        timer_callback_ptr = cb;
        if (of_mixer_use_sw) {
            TIMER_PERIOD = CPU_FREQ_HZ / OS_TIMER_HZ;
            timer_cb_div = (self_divide || app_hz == 0u || app_hz >= OS_TIMER_HZ)
                         ? 1u : (OS_TIMER_HZ / app_hz);
        } else {
            TIMER_PERIOD = CPU_FREQ_HZ / (app_hz ? app_hz : OS_TIMER_HZ);
            timer_cb_div = 1u;
        }
        timer_cb_cnt = 0u;
        TIMER_CTRL = TIMER_CTRL_ENABLE;
    } else {
        timer_callback_ptr = NULL;
        timer_cb_div = 1u;
        timer_cb_cnt = 0u;
        if (of_mixer_use_sw) {
            /* Keep the SW DAC pump alive — only the app callback is cleared. */
            TIMER_PERIOD = CPU_FREQ_HZ / OS_TIMER_HZ;
            TIMER_CTRL = TIMER_CTRL_ENABLE;
        } else {
            TIMER_CTRL = 0;
        }
    }
    sys_irq_restore_local(irq);
}

/* Boot-time arm: on a SW-mixer build the OS owns a permanent 1 kHz tick so
 * audio plays even when no app ever touches the timer (the dominant OS30
 * "no music -> no sound at all" failure).  No-op on HW-mixer builds. */
void of_os_timer_boot_arm(void) {
    if (!of_mixer_use_sw)
        return;
    uint32_t irq = sys_irq_save_local();
    TIMER_PERIOD = CPU_FREQ_HZ / OS_TIMER_HZ;
    TIMER_CTRL = TIMER_CTRL_ENABLE;
    sys_irq_restore_local(irq);
}

/* Called from irq_handler() on machine timer interrupt */
void timer_isr_callback(void) {
#ifdef OF_DEBUG_WALL_REVEAL
    /* Debug tick counter only (targets/pocket/video.c).  The instrument
     * never arms this timer itself — see the of_video_init note there. */
    {
        extern void of_video_dbg_wall_tick(void);
        of_video_dbg_wall_tick();
    }
#endif
    /* Service the terminal UART hook before app callbacks.  The Pocket
     * implementation is currently synchronous/no-op here, but keeping the
     * call preserves the HAL contract for targets with buffered output. */
    of_term_uart_drain();
    /* Feed the DAC FIFO every HW tick when the SW mixer is active (the OS
     * pins this timer at 1 kHz on that path; the 1024-deep FIFO gives ~21 ms
     * of slack).  Cheap no-op (one branch) when a HW mixer is present. */
    sw_mixer_pump();
    /* Async file-I/O watchdog + deferred bounce-copy pump (hal/file.h).
     * Two-branch no-op when no async transfer is pending. */
    of_file_async_tick();
    /* App native callback, divided down from the 1 kHz tick when the SW mixer
     * owns the timer (timer_cb_div==1 on HW-mixer builds => fires every tick,
     * identical to the legacy path). */
    if (timer_callback_ptr && ++timer_cb_cnt >= timer_cb_div) {
        timer_cb_cnt = 0u;
        timer_callback_ptr();
    }
    if (sigalrm_handler && ++sigalrm_cnt >= sigalrm_div) {
        sigalrm_cnt = 0u;
        sigalrm_handler(SIGALRM);
    }
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
    int      kind;
    uint32_t slot_id;       /* APF data slot ID */
    uint64_t offset;        /* Current file offset */
    uint64_t size;          /* File size (0 if unknown) */
    int      flags;         /* O_RDONLY, O_WRONLY, etc. */
    int      is_save;       /* Is this a nonvolatile writable file? */
    int      save_slot;     /* Save slot index (0-9), or -1 for config/settings */
    int      nv_cache_idx;  /* Nonvolatile size-cache index */
    int      dirty;         /* Nonvolatile data/size changed since open */
    int      is_dir;        /* Is this a directory FD? */
    uint32_t dir_pos;       /* Current position in directory listing */
    int      mount_idx;     /* Mounted VFS index, or -1 */
    union {
        iso9660_file_t iso_file;
        iso9660_dir_t iso_dir;
    } u;
} fd_entry_t;

static fd_entry_t fd_table[MAX_FDS];

enum {
    FD_KIND_NONE = 0,
    FD_KIND_SLOT_FILE,
    FD_KIND_NV_FILE,
    FD_KIND_ROOT_DIR,
    FD_KIND_ISO_FILE,
    FD_KIND_ISO_DIR,
};

/* ======================================================================
 * Always-on file read cache
 *
 * The cache storage is a target-owned SDRAM reservation, not kernel BSS:
 * the Pocket OSDATA linker region is intentionally small, while the cache
 * defaults to 4 MB.  The target memory map lowers the app's SDRAM limit so
 * apps cannot legally overlap this region.
 * ====================================================================== */

#define IO_CACHE_TOTAL_SIZE FILE_CACHE_SIZE
#define IO_CACHE_BLOCK_SIZE FILE_CACHE_BLOCK_SIZE
#define IO_CACHE_ENTRIES    ((int)(IO_CACHE_TOTAL_SIZE / IO_CACHE_BLOCK_SIZE))

#if IO_CACHE_TOTAL_SIZE == 0
#error "The file read cache is always on; OF_TARGET_FILE_CACHE_SIZE must be nonzero"
#endif

#if (IO_CACHE_TOTAL_SIZE % IO_CACHE_BLOCK_SIZE) != 0
#error "OF_TARGET_FILE_CACHE_SIZE must be a multiple of OF_TARGET_FILE_CACHE_BLOCK_SIZE"
#endif

#if (IO_CACHE_BLOCK_SIZE & (IO_CACHE_BLOCK_SIZE - 1u)) != 0
#error "OF_TARGET_FILE_CACHE_BLOCK_SIZE must be a power of two"
#endif

typedef struct {
    uint32_t slot_id;
    uint32_t file_off;
    uint32_t valid;
    uint32_t lru;
} io_cache_entry_t;

static io_cache_entry_t io_cache[IO_CACHE_ENTRIES];
static uint32_t io_lru_counter;

/* Lookup accelerators.  Both are stale-tolerant hints validated against
 * the entry before use, so eviction/invalidation never needs to touch
 * them — correctness rests solely on the io_cache[] contents, exactly
 * as with the plain scan.  io_cache_mru catches the dominant pattern
 * (consecutive read() calls walking one cached block); the hash hint
 * catches alternation between a few hot blocks.  The full scan remains
 * as the miss-path fallback. */
#define IO_CACHE_HINTS 256u   /* power of two; entry index fits a byte */
static int io_cache_mru = -1;
static uint8_t io_cache_hint[IO_CACHE_HINTS];

static inline uint32_t io_cache_hash(uint32_t slot_id, uint32_t aligned_off)
{
    return ((slot_id * 2654435761u) ^ (aligned_off / IO_CACHE_BLOCK_SIZE))
           & (IO_CACHE_HINTS - 1u);
}

static inline int io_cache_entry_is(int i, uint32_t slot_id, uint32_t aligned_off)
{
    return io_cache[i].valid > 0 &&
           io_cache[i].slot_id == slot_id &&
           io_cache[i].file_off == aligned_off;
}

static inline const uint8_t *io_cache_data(int entry)
{
    return (const uint8_t *)(uintptr_t)
        (FILE_CACHE_BASE + (uint32_t)entry * IO_CACHE_BLOCK_SIZE);
}

static int io_cache_lookup(uint32_t slot_id, uint32_t aligned_off)
{
    uint32_t h = io_cache_hash(slot_id, aligned_off);
    int i = io_cache_mru;

    if (i >= 0 && io_cache_entry_is(i, slot_id, aligned_off))
        goto hit;
    i = io_cache_hint[h];
    if (i < IO_CACHE_ENTRIES && io_cache_entry_is(i, slot_id, aligned_off))
        goto hit;
    for (i = 0; i < IO_CACHE_ENTRIES; i++)
        if (io_cache_entry_is(i, slot_id, aligned_off))
            goto hit;
    return -1;

hit:
    io_cache[i].lru = ++io_lru_counter;
    io_cache_mru = i;
    io_cache_hint[h] = (uint8_t)i;
    return i;
}

static int io_cache_evict(void)
{
    int best = 0;
    uint32_t oldest = io_cache[0].lru;

    for (int i = 1; i < IO_CACHE_ENTRIES; i++) {
        if (io_cache[i].valid == 0)
            return i;
        if (io_cache[i].lru < oldest) {
            oldest = io_cache[i].lru;
            best = i;
        }
    }
    return best;
}

static int io_cache_fill(int entry, uint32_t slot_id,
                         uint32_t aligned_off, uint32_t fill)
{
    if (fill == 0)
        return -EIO;

    void *cache_ptr = (void *)(uintptr_t)
        (FILE_CACHE_BASE + (uint32_t)entry * IO_CACHE_BLOCK_SIZE);

    int rc = of_file_read(slot_id, aligned_off, cache_ptr, fill);
    if (rc < 0)
        return rc;

    io_cache[entry].slot_id = slot_id;
    io_cache[entry].file_off = aligned_off;
    io_cache[entry].valid = fill;
    io_cache[entry].lru = ++io_lru_counter;
    return 0;
}

static int slot_read_cached(uint32_t slot_id, uint32_t off,
                            void *dst, uint32_t len)
{
    uint8_t *out = (uint8_t *)dst;
    uint32_t done = 0;
    int64_t file_size = -2;

    while (done < len) {
        if (done > 0xFFFFFFFFu - off)
            return -EINVAL;

        uint32_t cur = off + done;
        uint32_t aligned_off = cur & ~(IO_CACHE_BLOCK_SIZE - 1u);
        uint32_t buf_off = cur - aligned_off;
        int entry = io_cache_lookup(slot_id, aligned_off);

        if (entry < 0) {
            entry = io_cache_evict();
            uint32_t fill = IO_CACHE_BLOCK_SIZE;

            if (file_size == -2)
                file_size = of_file_size64(slot_id);
            if (file_size > 0) {
                if ((uint64_t)aligned_off >= (uint64_t)file_size)
                    return -EIO;
                if ((uint64_t)aligned_off + fill > (uint64_t)file_size)
                    fill = (uint32_t)((uint64_t)file_size - aligned_off);
            }

            int rc = io_cache_fill(entry, slot_id, aligned_off, fill);
            if (rc < 0)
                return rc;
        }

        if (buf_off >= io_cache[entry].valid)
            return -EIO;

        uint32_t avail = io_cache[entry].valid - buf_off;
        uint32_t n = len - done;
        if (n > avail)
            n = avail;
        if (n == 0)
            return -EIO;

        memcpy(out + done, io_cache_data(entry) + buf_off, n);
        done += n;
    }

    return 0;
}

/* ======================================================================
 * File slot registry -- maps filenames to APF data slot IDs
 *
 * Chip32 writes a file table ("FTAB") to CRAM0 at the OS slot offset
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
#define SHARED_CONFIG_SLOT_ID 8u
#define DUKE_SETTINGS_SLOT_ID 9u
#define NV_CONFIG_CACHE_IDX 0
#define NV_SAVE_CACHE_BASE  1
/* By-name config slots (hal/file.h of_file_config_slot) get their own
 * size-cache entries so each named .cfg tracks its own logical size. */
#define NV_NAMED_CFG_CACHE_BASE (NV_SAVE_CACHE_BASE + SAVE_MAX_SLOTS)
/* Data id 9 (presave / per-game settings) is a SEPARATE 256 KB CRAM window
 * from id 8 (shared config) — see targets/pocket/save.c nvslot_map, which
 * maps 8 to bridge 0x20380000 and 9 to 0x200C0000.  They used to share
 * NV_CONFIG_CACHE_IDX, so a core declaring BOTH (Diablo: stash at 8,
 * settings at 9) had one logical size serving two files: whichever closed
 * last decided the length the host wrote back for both, truncating one or
 * persisting trailing garbage past the other.  Appended at the END so the
 * save and named-config indices keep the values they already had. */
#define NV_PRESAVE_CACHE_IDX (NV_NAMED_CFG_CACHE_BASE + OF_FILE_CFG_SLOT_COUNT)
#define NV_CACHE_SLOTS      (NV_PRESAVE_CACHE_IDX + 1)

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
    if (file_slot_count >= MAX_FILE_SLOTS) {
        /* A dropped registration means the app's open-by-name will fail with
         * NOENT for a file that IS on the media — which on MiSTer surfaced
         * as an unexplained black screen (Wolfenstein shipped 47 files in one
         * vhd; the VSWAP.* family silently fell off the table, 2026-08-13).
         * The cap itself is fine — no legitimate instance needs 32 names —
         * so the fix is to make the packaging bug impossible to miss. */
        of_term_printf("  \033[91mfile table full (%d): dropped %s\033[0m\n",
                       MAX_FILE_SLOTS, filename);
        return;
    }

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

/* Search file slot registry (case-insensitive).  Full registered name
 * first, then basename.
 * Returns slot_id or -1 if not found.
 *
 * The two-pass order matters when a slot is registered under a
 * subdirectory: Quake's mission packs bind "HIPNOTIC/PAK0.PAK" alongside
 * id1's "pak0.pak", and those share a basename.  Basename-only matching
 * resolved BOTH to whichever came first in the table (id1), so the mission
 * pack silently loaded the base game's pak instead of its own.  Matching
 * the full name first lets a caller name the exact slot it means, while
 * bare filenames keep resolving as before. */
static int file_slot_lookup(const char *path) {
    const char *full = path;
    while (*full == '/')
        full++;
    for (int i = 0; i < file_slot_count; i++) {
        if (stricmp(full, file_slots[i].filename) == 0)
            return (int)file_slots[i].slot_id;
    }

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
    if (slot_id == SHARED_CONFIG_SLOT_ID)
        return NV_CONFIG_CACHE_IDX;
    if (slot_id == DUKE_SETTINGS_SLOT_ID)
        return NV_PRESAVE_CACHE_IDX;

    int save_slot = save_slot_from_data_id(slot_id);
    if (save_slot >= 0)
        return NV_SAVE_CACHE_BASE + save_slot;

    if (slot_id >= OF_FILE_CFG_SLOT_BASE &&
        slot_id < OF_FILE_CFG_SLOT_BASE + OF_FILE_CFG_SLOT_COUNT)
        return NV_NAMED_CFG_CACHE_BASE +
               (int)(slot_id - OF_FILE_CFG_SLOT_BASE);

    return -1;
}

static int open_flags_want_write(long flags) {
    return ((flags & O_ACCMODE) != 0) || ((flags & O_TRUNC) != 0);
}

static int open_flags_read_only(long flags) {
    return (flags & O_ACCMODE) == 0;
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
    /* Registry miss — the same target-HAL fall-through sys_openat and
     * sys_mount_iso already use.  The registry only holds the <=31 probed
     * ids; on MiSTer a full dynamic table pushes library files into the
     * overflow registry (ids 32+), and without this fall-through
     * of_file_slot_find() returned -1 for exactly those files — a
     * streaming app (CD music wad on the S2 volume) never obtained its
     * slot id and every subsequent of_file_read_async() refill failed. */
    if (slot < 0)
        slot = of_file_resolve_name(path_basename(filename));
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

static int parse_int(const char **pp);
static int prefix_match(const char *s, const char *prefix, int n);
static void dir_probe_slots(void);

/* ======================================================================
 * Mounted VFS
 * ====================================================================== */

#define VFS_MAX_MOUNTS      4
#define VFS_PATH_MAX        256
#define VFS_MOUNT_NAME_MAX  24

typedef struct {
    int used;
    char target[VFS_MOUNT_NAME_MAX];
    uint32_t slot_id;
    iso9660_mount_t iso;
} vfs_mount_t;

static vfs_mount_t vfs_mounts[VFS_MAX_MOUNTS];
static int vfs_mount_count;
static char vfs_cwd[VFS_PATH_MAX] = "/";

static int path_len(const char *s) {
    int n = 0;
    while (s && s[n])
        n++;
    return n;
}

static void path_copy(char *dst, uint32_t cap, const char *src) {
    uint32_t i = 0;
    if (cap == 0)
        return;
    while (src && src[i] && i + 1 < cap) {
        dst[i] = src[i];
        i++;
    }
    dst[i] = '\0';
}

static int normalize_abs_path(const char *path, char *out, uint32_t cap) {
    const char *src;
    uint32_t n = 0;

    if (!path || !out || cap < 2)
        return -EINVAL;

    int is_abs = (path[0] == '/' || path[0] == '\\');
    if (is_abs) {
        src = path;
    } else {
        if (vfs_cwd[0] != '/') {
            out[0] = '/';
            out[1] = '\0';
            n = 1;
        } else {
            path_copy(out, cap, vfs_cwd);
            n = (uint32_t)path_len(out);
        }
        if (n > 1 && out[n - 1] == '/')
            n--;
        if (n > 1) {
            if (n + 1 >= cap)
                return -ENAMETOOLONG;
            out[n++] = '/';
        }
        src = path;
    }

    if (is_abs) {
        out[n++] = '/';
        while (*src == '/' || *src == '\\')
            src++;
    }

    while (*src) {
        char part[64];
        uint32_t plen = 0;

        while (*src == '/' || *src == '\\')
            src++;
        while (*src && *src != '/' && *src != '\\') {
            if (plen + 1 >= sizeof(part))
                return -ENAMETOOLONG;
            part[plen++] = *src++;
        }
        part[plen] = '\0';
        if (plen == 0 || (plen == 1 && part[0] == '.'))
            continue;
        if (plen == 2 && part[0] == '.' && part[1] == '.') {
            if (n > 1) {
                n--;
                while (n > 1 && out[n - 1] != '/')
                    n--;
            }
            out[n] = '\0';
            continue;
        }

        if (n > 1 && out[n - 1] != '/') {
            if (n + 1 >= cap)
                return -ENAMETOOLONG;
            out[n++] = '/';
        }
        if (n + plen >= cap)
            return -ENAMETOOLONG;
        for (uint32_t i = 0; i < plen; i++)
            out[n++] = part[i];
        out[n] = '\0';
    }

    if (n == 0) {
        out[0] = '/';
        out[1] = '\0';
    } else {
        out[n] = '\0';
    }
    return 0;
}

static int mount_path_match(const char *path, const char *target) {
    int len = path_len(target);
    if (len <= 1)
        return 0;
    for (int i = 0; i < len; i++)
        if (path[i] != target[i])
            return 0;
    return path[len] == '\0' || path[len] == '/';
}

static int vfs_find_mount(const char *path, const char **rel_out) {
    int best = -1;
    int best_len = -1;

    for (int i = 0; i < VFS_MAX_MOUNTS; i++) {
        if (!vfs_mounts[i].used)
            continue;
        if (!mount_path_match(path, vfs_mounts[i].target))
            continue;
        int len = path_len(vfs_mounts[i].target);
        if (len > best_len) {
            best = i;
            best_len = len;
        }
    }

    if (best >= 0 && rel_out) {
        const char *rel = path + best_len;
        if (*rel == '/')
            rel++;
        *rel_out = rel;
    }
    return best;
}

static int vfs_mount_name_available(const char *target) {
    for (int i = 0; i < VFS_MAX_MOUNTS; i++)
        if (vfs_mounts[i].used && stricmp(vfs_mounts[i].target, target) == 0)
            return 0;
    return 1;
}

static int vfs_fd_refs_mount(int mount_idx) {
    for (int fd = FD_FIRST_FREE; fd < MAX_FDS; fd++)
        if (fd_table[fd].in_use && fd_table[fd].mount_idx == mount_idx)
            return 1;
    return 0;
}

static long sys_mount_iso(const char *source, const char *target) {
    char target_abs[VFS_PATH_MAX];
    int slot;

    if (!source || !target)
        return -EINVAL;

    int rc = normalize_abs_path(target, target_abs, sizeof(target_abs));
    if (rc < 0)
        return rc;
    if (target_abs[0] != '/' || target_abs[1] == '\0')
        return -EINVAL;
    if ((uint32_t)path_len(target_abs) >= VFS_MOUNT_NAME_MAX)
        return -ENAMETOOLONG;
    if (!vfs_mount_name_available(target_abs))
        return -EBUSY;

    if (prefix_match(source, "slot:", 5)) {
        const char *p = source + 5;
        if (*p < '0' || *p > '9')
            return -EINVAL;
        slot = parse_int(&p);
    } else {
        if (file_slot_count == 0)
            dir_probe_slots();
        slot = file_slot_lookup(source);
        if (slot < 0)
            slot = of_file_resolve_name(path_basename(source));
        if (slot < 0)
            return -ENOENT;
    }

    int mount_idx = -1;
    for (int i = 0; i < VFS_MAX_MOUNTS; i++) {
        if (!vfs_mounts[i].used) {
            mount_idx = i;
            break;
        }
    }
    if (mount_idx < 0)
        return -EMFILE;

    vfs_mount_t *m = &vfs_mounts[mount_idx];
    memset(m, 0, sizeof(*m));
    m->slot_id = (uint32_t)slot;
    path_copy(m->target, sizeof(m->target), target_abs);

    rc = iso9660_mount(&m->iso, (uint32_t)slot, slot_read_cached);
    if (rc < 0) {
        memset(m, 0, sizeof(*m));
        return rc;
    }

    m->used = 1;
    vfs_mount_count++;
    return 0;
}

static long sys_unmount_path(const char *target) {
    char target_abs[VFS_PATH_MAX];

    int rc = normalize_abs_path(target, target_abs, sizeof(target_abs));
    if (rc < 0)
        return rc;

    for (int i = 0; i < VFS_MAX_MOUNTS; i++) {
        if (!vfs_mounts[i].used ||
            stricmp(vfs_mounts[i].target, target_abs) != 0)
            continue;
        if (vfs_fd_refs_mount(i))
            return -EBUSY;
        iso9660_unmount(&vfs_mounts[i].iso);
        memset(&vfs_mounts[i], 0, sizeof(vfs_mounts[i]));
        if (vfs_mount_count > 0)
            vfs_mount_count--;
        if (mount_path_match(vfs_cwd, target_abs))
            path_copy(vfs_cwd, sizeof(vfs_cwd), "/");
        return 0;
    }

    return -ENOENT;
}

static long vfs_open_if_mounted(int fd, const char *path,
                                long flags, int *handled_out) {
    char full_path[VFS_PATH_MAX];
    const char *mount_rel = NULL;

    *handled_out = 0;
    if (vfs_mount_count == 0)
        return 0;

    int rc = normalize_abs_path(path, full_path, sizeof(full_path));
    if (rc < 0)
        return 0;

    int mount_idx = vfs_find_mount(full_path, &mount_rel);
    if (mount_idx < 0)
        return 0;

    *handled_out = 1;
    if (open_flags_want_write(flags)) {
        fd_table[fd].in_use = 0;
        return -EACCES;
    }

    fd_entry_t *f = &fd_table[fd];
    f->mount_idx = mount_idx;
    if (flags & O_DIRECTORY) {
        rc = iso9660_open_dir(&vfs_mounts[mount_idx].iso,
                              mount_rel, &f->u.iso_dir);
        if (rc < 0) {
            fd_table[fd].in_use = 0;
            return rc;
        }
        f->kind = FD_KIND_ISO_DIR;
        f->is_dir = 1;
        return fd;
    }

    rc = iso9660_open_file(&vfs_mounts[mount_idx].iso,
                           mount_rel, &f->u.iso_file);
    if (rc < 0) {
        fd_table[fd].in_use = 0;
        return rc;
    }

    f->kind = FD_KIND_ISO_FILE;
    f->size = iso9660_size_file(&f->u.iso_file);
    return fd;
}

/* ======================================================================
 * Directory listing — enumerate data slots via DS_CMD_GETFILE
 *
 * opendir("/") probes available APF data slots and auto-registers
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

#define DT_DIR  4
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
        int64_t sz = of_file_size64(slot);
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

    if (f->kind == FD_KIND_ISO_DIR) {
        while (written < count) {
            iso9660_dirent_t ent;
            int rc = iso9660_read_dir(&f->u.iso_dir, &ent);
            if (rc < 0)
                return (written > 0) ? (long)written : (long)rc;
            if (rc == 0)
                break;

            uint32_t namelen = 0;
            while (ent.name[namelen]) namelen++;

            uint32_t reclen = (8 + 8 + 2 + 1 + namelen + 1 + 7) & ~7;
            if (written + reclen > count)
                break;

            struct linux_dirent64 *de =
                (struct linux_dirent64 *)(out + written);
            de->d_ino = ((uint64_t)(f->mount_idx + 1) << 32)
                      | (uint32_t)(pos + 1);
            de->d_off = pos + 1;
            de->d_reclen = (uint16_t)reclen;
            de->d_type = ent.is_dir ? DT_DIR : DT_REG;

            char *dst = de->d_name;
            for (uint32_t i = 0; i < namelen; i++)
                dst[i] = ent.name[i];
            dst[namelen] = '\0';

            written += reclen;
            pos++;
        }
        f->dir_pos = (uint32_t)pos;
        return (long)written;
    }

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

    int mount_pos = pos - file_slot_count;
    while (pos >= file_slot_count && mount_pos < VFS_MAX_MOUNTS) {
        if (!vfs_mounts[mount_pos].used) {
            mount_pos++;
            pos++;
            continue;
        }

        const char *name = path_basename(vfs_mounts[mount_pos].target);
        uint32_t namelen = 0;
        while (name[namelen]) namelen++;

        uint32_t reclen = (8 + 8 + 2 + 1 + namelen + 1 + 7) & ~7;
        if (written + reclen > count)
            break;

        struct linux_dirent64 *de = (struct linux_dirent64 *)(out + written);
        de->d_ino = 0x1000u + mount_pos;
        de->d_off = pos + 1;
        de->d_reclen = (uint16_t)reclen;
        de->d_type = DT_DIR;

        char *dst = de->d_name;
        for (uint32_t i = 0; i < namelen; i++)
            dst[i] = name[i];
        dst[namelen] = '\0';

        written += reclen;
        mount_pos++;
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
        /* At capacity: MUST be an error, never 0.  write(2) returning 0 for
         * a non-zero count is a value no libc can act on — musl's
         * __stdio_write treats it as neither completion nor error and
         * re-issues the identical writev forever, hanging the app inside
         * fwrite with the slot half written.  EFBIG is the POSIX answer for
         * "exceeds the file size limit". */
        if (f->offset >= capacity)
            return -EFBIG;
        uint32_t available = capacity - (uint32_t)f->offset;
        uint32_t to_write = (uint32_t)count;
        if (to_write > available)
            to_write = available;

        int rc = of_nvslot_write(f->slot_id, (const void *)buf,
                                 (uint32_t)f->offset, to_write);
        /* of_nvslot_write returns len or -1; map anything non-positive to a
         * real errno.  A bare -1 would surface as EPERM, and a 0 would spin
         * musl exactly as above. */
        if (rc <= 0)
            return -EIO;

        f->offset += rc;
        if (f->offset > f->size)
            f->size = f->offset;
        f->dirty = 1;
        save_size_cache_store(f->nv_cache_idx, (uint32_t)f->size);
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

    if (f->kind == FD_KIND_ISO_FILE) {
        if ((f->flags & O_ACCMODE) == O_WRONLY)
            return -EBADF;
        int rc = iso9660_read_file(&vfs_mounts[f->mount_idx].iso,
                                   &f->u.iso_file, (void *)buf,
                                   (uint32_t)count);
        if (rc > 0)
            f->offset = iso9660_tell_file(&f->u.iso_file);
        return rc;
    }

    if (f->is_save) {
        if ((f->flags & O_ACCMODE) == O_WRONLY)
            return -EBADF;
        if (f->offset >= f->size)
            return 0;

        uint32_t to_read = (uint32_t)count;
        if (to_read > f->size - f->offset)
            to_read = (uint32_t)(f->size - f->offset);

        int rc = of_nvslot_read(f->slot_id, (void *)buf,
                                (uint32_t)f->offset, to_read);
        if (rc > 0)
            f->offset += rc;
        return rc;
    }

    /* Lazy file size resolution — only probe on first read, not at fopen */
    if (f->size == 0 && f->slot_id > 0 && f->offset == 0) {
        int64_t sz = of_file_size64(f->slot_id);
        if (sz > 0)
            f->size = (uint64_t)sz;
        /* If size unknown (-1), leave f->size as 0 and read without
         * bounds checking — the bridge returns short/zero when EOF. */
    }

    uint32_t to_read = (uint32_t)count;
    if (f->size > 0 && f->offset + (uint64_t)to_read > f->size) {
        if (f->offset >= f->size)
            return 0;
        to_read = (uint32_t)(f->size - f->offset);
    }

    if (to_read == 0)
        return 0;
    if (f->offset > 0xFFFFFFFFull)
        return -EINVAL;
    if ((uint64_t)to_read > 0x100000000ull - f->offset)
        to_read = (uint32_t)(0x100000000ull - f->offset);

    int rc = slot_read_cached(f->slot_id, (uint32_t)f->offset,
                              (void *)buf, to_read);
    if (rc < 0) {
        if (f->size == 0) {
            f->size = f->offset;
            return 0;
        }
        return (long)rc;
    }

    f->offset += to_read;
    return (long)to_read;
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

static uint32_t nv_infer_text_size(uint32_t slot_id) {
    uint32_t capacity = of_nvslot_capacity(slot_id);
    uint8_t buf[64];
    uint32_t off = 0;
    uint32_t last = 0;
    int seen = 0;

    while (off < capacity) {
        uint32_t n = capacity - off;
        if (n > sizeof(buf))
            n = sizeof(buf);

        if (of_nvslot_read(slot_id, buf, off, n) != (int)n)
            break;

        for (uint32_t i = 0; i < n; i++) {
            uint8_t b = buf[i];
            if (b == 0x00 || b == 0xff)
                return seen ? last : 0;
            seen = 1;
            last = off + i + 1;
        }

        off += n;
    }

    return seen ? last : 0;
}

static long open_nv_fd(int fd, uint32_t slot_id, long flags) {
    int cache_idx = nv_cache_index_from_data_id(slot_id);
    if (cache_idx < 0 || !of_nvslot_is_supported(slot_id)) {
        fd_table[fd].in_use = 0;
        return -EINVAL;
    }

    fd_entry_t *f = &fd_table[fd];
    f->slot_id = slot_id;
    f->kind = FD_KIND_NV_FILE;
    f->is_save = 1;
    f->save_slot = save_slot_from_data_id(slot_id);
    f->nv_cache_idx = cache_idx;
    f->size = nv_current_size(slot_id, cache_idx);

    /* Nonvolatile size metadata comes from the APF datatable and is
     * maintained by the Pocket launcher plus our SAVE_DT_SIZE update.
     * Some launcher/build transitions have left the size word stale even
     * though CRAM0 was loaded correctly.  For read-only save opens, let the
     * game probe the header directly by exposing the full slot capacity.
     * Empty slots still fail the game-level magic/header checks. */
    if (f->size == 0 && f->save_slot >= 0 && open_flags_read_only(flags))
        f->size = of_nvslot_capacity(slot_id);

    if (f->size == 0 && f->save_slot < 0 && open_flags_read_only(flags))
        f->size = nv_infer_text_size(slot_id);

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
    f->kind = FD_KIND_SLOT_FILE;
    f->mount_idx = -1;

    int special_path = prefix_match(path, "slot:", 5) ||
                       prefix_match(path, "save:", 5) ||
                       prefix_match(path, "save_", 5);
    if (!special_path) {
        int handled = 0;
        long vfs_fd = vfs_open_if_mounted(fd, path, flags, &handled);
        if (handled)
            return vfs_fd;
    }

    /* O_DIRECTORY — return a directory FD for getdents64.
     * filesystem_init() already populated file_slots at boot; probe
     * lazily only if the FTAB is empty (e.g. sim target without a
     * boot-time init).  Preserve the historical root fallback for
     * non-mounted directory probes: several ports use opendir() only
     * to enumerate the APF-root file table, and older kernels ignored
     * the path for O_DIRECTORY. */
    if (flags & O_DIRECTORY) {
        if (file_slot_count == 0)
            dir_probe_slots();
        f->kind = FD_KIND_ROOT_DIR;
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

        long slot_id = of_file_flags((uint32_t)slot);
        if (slot_id != slot) {
            fd_table[fd].in_use = 0;
            return -ENOENT;
        }
        if (open_flags_want_write(flags)) {
            fd_table[fd].in_use = 0;
            return -EACCES;
        }
        int64_t sz = of_file_size64((uint32_t)slot);
        if (sz <= 0) {
            fd_table[fd].in_use = 0;
            return -ENOENT;
        }
        f->slot_id = (uint32_t)slot;
        f->kind = FD_KIND_SLOT_FILE;
        f->size = (uint64_t)sz;
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

        /* Per-game settings opened by NAME (fopen("doom.cfg","wb")): route
         * to the target's preallocated /config backing slot.  This is what
         * makes settings writes land at all (the registry id is read-only,
         * so they used to die with -EACCES and the app's M_SaveDefaults
         * silently wrote nothing), and it keeps READS on the same slot so
         * the fd is bounded by the LOGICAL config size instead of the
         * 256 KB physical preallocation. */
        int cfg_slot = of_file_config_slot(path_basename(path));
        if (cfg_slot >= 0)
            return open_nv_fd(fd, (uint32_t)cfg_slot, flags);

        if (open_flags_want_write(flags)) {
            fd_table[fd].in_use = 0;
            return -EACCES;
        }

        /* Reject registered names whose slot is unbacked (size == 0). */
        int64_t sz = of_file_size64((uint32_t)slot);
        if (sz <= 0) {
            fd_table[fd].in_use = 0;
            return -ENOENT;
        }
        f->slot_id = (uint32_t)slot;
        f->kind = FD_KIND_SLOT_FILE;
        f->size = (uint64_t)sz;
        return fd;
    }

    int save_slot = save_slot_from_filename(path);
    if (save_slot >= 0)
        return open_nv_fd(fd, SAVE_SLOT_ID_BASE + (uint32_t)save_slot, flags);

    int settings_slot = nv_settings_slot_from_filename(path);
    if (settings_slot >= 0 && of_nvslot_is_supported((uint32_t)settings_slot))
        return open_nv_fd(fd, (uint32_t)settings_slot, flags);

    /* Same named-config route for config names the registry doesn't hold
     * (e.g. a full dynamic table on a large library card). */
    int cfg_slot = of_file_config_slot(path_basename(path));
    if (cfg_slot >= 0)
        return open_nv_fd(fd, (uint32_t)cfg_slot, flags);

    /* Registry miss — last resort: ask the target file HAL to resolve the
     * basename directly (MiSTer searches its mounted FAT volumes S1 -> S2
     * -> S0; targets without a filesystem return -1).  The returned id is
     * stable until relaunch, so fd/io-cache slot keying works unchanged.
     * Read-only by construction: writable names (saves, settings) were
     * already handled above. */
    int direct = of_file_resolve_name(path_basename(path));
    if (direct >= 0) {
        if (open_flags_want_write(flags)) {
            fd_table[fd].in_use = 0;
            return -EACCES;
        }
        int64_t dsz = of_file_size64((uint32_t)direct);
        if (dsz <= 0) {
            fd_table[fd].in_use = 0;
            return -ENOENT;
        }
        f->slot_id = (uint32_t)direct;
        f->kind = FD_KIND_SLOT_FILE;
        f->size = (uint64_t)dsz;
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
        fd_entry_t *f = &fd_table[fd];
        uint32_t save_size = (uint32_t)f->size;
        int rc = 0;
        if (f->dirty) {
            /* Nonvolatile APF slots are already backed by fixed CRAM0
             * addresses.  Keep close() cheap and deterministic: publish the
             * logical size for same-run reopens, then rely on Pocket's native
             * nonvolatile save-on-exit path to persist CRAM0.  Issuing a
             * synchronous DS_CMD_WRITE here duplicates the launcher's job and
             * can stall indefinitely on slow SD-card writeback. */
            rc = of_nvslot_set_size(f->slot_id, save_size);
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
    if (f->kind == FD_KIND_ISO_FILE || f->kind == FD_KIND_ISO_DIR)
        return (f->size > 0x7FFFFFFFull) ? 0x7FFFFFFFl : (long)f->size;
    if (f->is_save)
        return (f->size > 0x7FFFFFFFull) ? 0x7FFFFFFFl : (long)f->size;
    if (f->size == 0 && f->slot_id > 0) {
        int64_t sz = of_file_size64(f->slot_id);
        if (sz > 0) f->size = (uint64_t)sz;
    }
    return (f->size > 0x7FFFFFFFull) ? 0x7FFFFFFFl : (long)f->size;
}

/* _llseek: (fd, offset_hi, offset_lo, &result, whence) — riscv32 uses this */
static long sys_llseek(long fd, long off_hi, long off_lo,
                       long result_ptr, long whence) {
    if (fd < 0 || fd >= MAX_FDS || !fd_table[fd].in_use)
        return -EBADF;

    fd_entry_t *f = &fd_table[fd];
    int64_t offset = (int64_t)(((uint64_t)(uint32_t)off_hi << 32) |
                               (uint32_t)off_lo);
    int64_t new_offset;

    if (f->kind == FD_KIND_ISO_FILE) {
        switch (whence) {
        case 0: new_offset = offset; break;
        case 1: new_offset = (int64_t)iso9660_tell_file(&f->u.iso_file) + offset; break;
        case 2: new_offset = (int64_t)f->size + offset; break;
        default: return -EINVAL;
        }
        if (new_offset < 0 || new_offset > 0xFFFFFFFFll)
            return -EINVAL;
        int rc = iso9660_seek_file(&f->u.iso_file, new_offset, 0);
        if (rc < 0)
            return rc;
        f->offset = (uint64_t)new_offset;
        if (result_ptr) {
            int64_t *res = (int64_t *)result_ptr;
            *res = new_offset;
        }
        return 0;
    }

    if (f->kind == FD_KIND_ISO_DIR)
        return -EINVAL;

    /* Resolve file size for SEEK_END */
    if (whence == 2 && !f->is_save && f->size == 0 && f->slot_id > 0) {
        int64_t sz = of_file_size64(f->slot_id);
        if (sz > 0) f->size = (uint64_t)sz;
    }

    switch (whence) {
    case 0: new_offset = offset; break;
    case 1: new_offset = (int64_t)f->offset + offset; break;
    case 2: new_offset = (int64_t)f->size + offset; break;
    default: return -EINVAL;
    }

    if (new_offset < 0 || new_offset > 0xFFFFFFFFll)
        return -EINVAL;

    f->offset = (uint64_t)new_offset;

    /* _llseek writes result to user pointer as 64-bit */
    if (result_ptr) {
        int64_t *res = (int64_t *)result_ptr;
        *res = new_offset;
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

/* CLOCK_REALTIME epoch base.  The APF host latches its RTC (epoch seconds,
 * local wall-clock time) into the RTC_EPOCH sysreg within the first moments
 * of boot; adding the uptime yields wall-clock time with sub-second boot
 * skew.  The register reads 0 until the host command lands (or on hosts
 * without an RTC), in which case REALTIME degrades to plain uptime -- the
 * pre-existing behavior.  CLOCK_MONOTONIC and friends stay pure uptime so
 * interval timing is unaffected. */
static uint32_t clock_epoch_base(long clk_id) {
    if (clk_id != 0 /* CLOCK_REALTIME */)
        return 0;
    return RTC_EPOCH;
}

static long sys_clock_gettime(long clk_id, long tp) {
    struct ts32 *ts = (void *)tp;
    uint32_t ns;
    ts->tv_sec = of_timer_get_seconds(&ns) + clock_epoch_base(clk_id);
    ts->tv_nsec = ns;
    return 0;
}

static long sys_clock_gettime_time64(long clk_id, long tp) {
    struct ts64 *ts = (void *)tp;
    if (!ts) return 0;
    uint32_t ns;
    ts->tv_sec = (int64_t)of_timer_get_seconds(&ns) +
                 (int64_t)clock_epoch_base(clk_id);
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
 * hold hundreds of concurrent slabs once a larger C++ game/runtime has
 * menus, resource tables, file readers, and texture state alive at the
 * same time.
 *
 * Raised 1024 -> 4096 after DevilutionX (Diablo, issue #4) wedged the
 * allocator on hardware: a 42 KB request returned ENOMEM with 48 MB of
 * arena still free.  A full table makes EVERY carve fail, so the only
 * requests still served are those reusing a free region — which is why the
 * failure looked so strange from userspace (a 16 MB allocation succeeding
 * moments after a 41 KB one failed).  Reproduced on the host against these
 * exact sources: 1024 slots wedge after ~4 MB of live mappings, and the
 * arena size is irrelevant to it.  4096 costs 48 KB of OS BSS. */
#define MMAP_SLOTS 4096

/* Do not leave slivers behind when splitting a reused region: a remainder
 * smaller than this is handed out with the allocation instead of costing a
 * slot to track. One page is the smallest unit sys_mmap2 deals in. */
#define MMAP_MIN_SPLIT 4096u

typedef struct {
    uintptr_t base;     /* 0 = slot unused */
    uintptr_t len;      /* page-aligned */
    uint8_t   free;     /* 1 = munmapped, reusable */
} mmap_slot_t;

static mmap_slot_t mmap_slots[MMAP_SLOTS];
static int         mmap_slots_used;

static int mmap_alloc_slot(void);   /* used by the splitting reuse path */

static void mmap_coalesce_bottom(void) {
    /* Walk free slots; any free slot whose BASE equals mmap_bottom
     * sits exactly at the bottom of the carved arena and can be
     * reclaimed by raising mmap_bottom up by its length.  Carved
     * slots grow downward from the current app stack reserve, so mmap_bottom
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

/* Merge adjacent free regions into one slot.
 *
 * Without this the table leaks an entry per distinct freed size: reuse used
 * to be exact-fit, so a free region of the "wrong" size was dead space AND a
 * permanently occupied slot.  Merging reclaims the slot and, just as
 * importantly, rebuilds large contiguous free regions out of neighbouring
 * small ones so best-fit below can actually satisfy a big request.
 *
 * O(n^2) to fixed point, so it is deliberately NOT on the fast path — only
 * sys_mmap2's failure path calls it, right before it would have returned
 * ENOMEM. A rare quadratic sweep over 4096 slots is far cheaper than a
 * spurious allocation failure. */
static void mmap_merge_free(void) {
    int changed;
    do {
        changed = 0;
        for (int i = 0; i < MMAP_SLOTS; i++) {
            mmap_slot_t *s = &mmap_slots[i];
            if (!s->base || !s->free)
                continue;
            for (int j = 0; j < MMAP_SLOTS; j++) {
                mmap_slot_t *t = &mmap_slots[j];
                if (i == j || !t->base || !t->free)
                    continue;
                if (s->base + s->len == t->base) {
                    s->len += t->len;
                    t->base = 0;
                    t->len  = 0;
                    t->free = 0;
                    mmap_slots_used--;
                    changed = 1;
                }
            }
        }
    } while (changed);
}

/* Best fit over the free regions, splitting the remainder back into the
 * table when it is worth tracking.
 *
 * This replaces an exact-fit-only search.  The old comment here warned that
 * splitting risked handing back memory whose head mallocng might still read;
 * that concern does not apply to what we do now, because a split only ever
 * hands out a region the app had already munmapped in full, and the returned
 * range is zeroed before it is handed over exactly as a fresh carve is.
 * Keeping exact-fit-only, by contrast, was actively harmful: it guaranteed
 * that a workload allocating varied sizes could never reuse anything.
 *
 * Returns the slot index to hand out, or -1.  On success the slot's len is
 * the caller's requested length, so sys_munmap's bookkeeping stays exact. */
static int mmap_find_reusable(uintptr_t want_len) {
    int best = -1;
    for (int i = 0; i < MMAP_SLOTS; i++) {
        mmap_slot_t *s = &mmap_slots[i];
        if (!s->base || !s->free || s->len < want_len)
            continue;
        if (best < 0 || s->len < mmap_slots[best].len)
            best = i;
    }
    if (best < 0)
        return -1;

    mmap_slot_t *s = &mmap_slots[best];
    uintptr_t excess = s->len - want_len;
    if (excess >= MMAP_MIN_SPLIT) {
        /* Track the tail as its own free region. If no slot is spare the
         * split cannot be recorded, so hand the whole region over rather
         * than lose track of the tail -- see the len note below. */
        int extra = mmap_alloc_slot();
        if (extra >= 0) {
            mmap_slots[extra].base = s->base + want_len;
            mmap_slots[extra].len  = excess;
            mmap_slots[extra].free = 1;
            mmap_slots_used++;
            s->len = want_len;
        }
    }
    /* s->len may still exceed want_len when the split was skipped. That is
     * safe: sys_munmap matches on base and uses the recorded length, so the
     * region is returned whole. */
    return best;
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
    if (slot < 0) {
        /* Last chance: fold adjacent free regions together, which both
         * frees slots and may expose a region big enough to reuse outright.
         * Only reached when we would otherwise have returned ENOMEM, so the
         * quadratic sweep costs nothing in steady state. */
        mmap_merge_free();
        int reuse2 = mmap_find_reusable(len_aligned);
        if (reuse2 >= 0) {
            mmap_slot_t *s = &mmap_slots[reuse2];
            s->free = 0;
            memset((void *)s->base, 0, s->len);
            return (long)s->base;
        }
        slot = mmap_alloc_slot();
    }
    if (slot < 0) {
        /* Genuinely out of bookkeeping, with arena very possibly to spare.
         * Say so once: this exact condition (table full, tens of MB free)
         * is what made Diablo's level loads fail, and from userspace it is
         * indistinguishable from real exhaustion. */
        static int warned_slots_full;
        if (!warned_slots_full) {
            warned_slots_full = 1;
            of_term_printf("[mmap] slot table FULL (%d entries) - refusing "
                           "%u bytes with %u KB of arena free; raise "
                           "MMAP_SLOTS\n",
                           MMAP_SLOTS, (unsigned)len_aligned,
                           (unsigned)((mmap_bottom - current_brk) / 1024u));
        }
        return -ENOMEM;
    }

    memset((void *)base, 0, len_aligned);
    *mb_p = base;

    mmap_slots[slot].base = base;
    mmap_slots[slot].len  = len_aligned;
    mmap_slots[slot].free = 0;
    mmap_slots_used++;

    return (long)base;
}

void *syscall_alloc_app_mmap(uint32_t size)
{
    long ret = sys_mmap2(0, (long)size, 0, 0, -1, 0);
    return ret < 0 ? (void *)0 : (void *)(uintptr_t)ret;
}

static long sys_munmap(long addr, long length) {
    if (!addr || length <= 0)
        return 0;    /* Linux tolerates 0-length unmap as a no-op. */

    uintptr_t base = (uintptr_t)addr;
    uintptr_t len  = ((uintptr_t)length + 4095u) & ~4095u;

    /* Match on BASE alone and trust the recorded length.
     *
     * This used to also require s->len == len. That is no longer safe now
     * that mmap_find_reusable() can hand out a region larger than the caller
     * asked for (when the leftover was too small to be worth a slot): the
     * app frees the size it requested, the recorded region is bigger, and a
     * length-sensitive match would miss it and leak the region forever.
     * Base is unique across live regions, so it identifies the allocation on
     * its own; the caller's length is ignored entirely. */
    (void)len;
    for (int i = 0; i < MMAP_SLOTS; i++) {
        mmap_slot_t *s = &mmap_slots[i];
        if (s->base != base || s->free)
            continue;

        /* If this region sits right at mmap_bottom, reclaim by
         * raising mmap_bottom and freeing the slot outright —
         * that's the LIFO case (very common for mallocng).
         * Raise by the RECORDED length: the caller's may be smaller than
         * the region we actually handed out, and using it would strand the
         * difference in untracked space. */
        if (base == mmap_bottom) {
            mmap_bottom = base + s->len;
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

int syscall_free_app_mmap(void *ptr, uint32_t size)
{
    return (int)sys_munmap((long)(uintptr_t)ptr, (long)size);
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
        of_video_set_color_mode((int)a0);
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
    case OF_VIDEO_FID_SET_MODE:
        return of_video_set_mode((const of_video_mode_t *)a0);
    case OF_VIDEO_FID_GET_MODE:
        of_video_get_mode((of_video_mode_t *)a0);
        return 0;
    case OF_VIDEO_FID_GET_MODE_COUNT:
        return of_video_get_mode_count();
    case OF_VIDEO_FID_GET_MODE_INFO:
        return of_video_get_mode_info((int)a0, (of_video_mode_t *)a1);
    case OF_VIDEO_FID_GET_CAPS:
        of_video_get_caps((of_video_caps_t *)a0);
        return 0;
    case OF_VIDEO_FID_CHECK_MODE:
        return of_video_check_mode((const of_video_mode_t *)a0,
                                   (of_video_mode_t *)a1);
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
    case OF_INPUT_FID_IS_DOCKED:
        return of_input_is_docked();
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

static long stat_path_info(long dirfd, const char *path,
                           uint64_t *size_out, int *is_dir_out) {
    char full_path[VFS_PATH_MAX];

    if (!path)
        return -EFAULT;
    if (!path[0] && dirfd >= 0 && dirfd < MAX_FDS &&
        fd_table[dirfd].in_use) {
        fd_entry_t *f = &fd_table[dirfd];
        if (f->size == 0 && f->slot_id > 0) {
            int64_t sz = of_file_size64(f->slot_id);
            if (sz > 0) f->size = (uint64_t)sz;
        }
        if (size_out) *size_out = f->size;
        if (is_dir_out) *is_dir_out = f->is_dir;
        return 0;
    }

    int rc = normalize_abs_path(path, full_path, sizeof(full_path));
    if (rc < 0)
        return rc;

    if (full_path[0] == '/' && full_path[1] == '\0') {
        if (size_out) *size_out = 0;
        if (is_dir_out) *is_dir_out = 1;
        return 0;
    }

    const char *rel = NULL;
    int mount_idx = vfs_find_mount(full_path, &rel);
    if (mount_idx >= 0) {
        uint32_t iso_size = 0;
        rc = iso9660_stat(&vfs_mounts[mount_idx].iso, rel,
                          &iso_size, is_dir_out);
        if (rc == 0 && size_out)
            *size_out = iso_size;
        return rc;
    }

    long fd = sys_openat(dirfd, (long)path, 0, 0);
    if (fd < 0)
        return fd;
    fd_entry_t *f = &fd_table[fd];
    if (f->size == 0 && f->slot_id > 0) {
        int64_t sz = of_file_size64(f->slot_id);
        if (sz > 0) f->size = (uint64_t)sz;
    }
    if (size_out) *size_out = f->size;
    if (is_dir_out) *is_dir_out = f->is_dir;
    sys_close(fd);
    return 0;
}

static long sys_getcwd(char *buf, uint32_t size) {
    if (!buf)
        return -EFAULT;

    uint32_t len = (uint32_t)path_len(vfs_cwd) + 1;
    if (size < len)
        return -ERANGE;

    path_copy(buf, size, vfs_cwd);
    return (long)len;
}

static long sys_chdir(const char *path) {
    char full_path[VFS_PATH_MAX];
    uint64_t size = 0;
    int is_dir = 0;

    int rc = normalize_abs_path(path, full_path, sizeof(full_path));
    if (rc < 0)
        return rc;

    rc = stat_path_info(AT_FDCWD, full_path, &size, &is_dir);
    if (rc < 0)
        return rc;
    if (!is_dir)
        return -ENOTDIR;

    path_copy(vfs_cwd, sizeof(vfs_cwd), full_path);
    return 0;
}

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
        /* The timer read FIDs return a free-running unsigned 32-bit count
         * (us/ms since boot).  The us count crosses bit 31 at ~35.8 min, so
         * it must NOT go through the negative=error convention below -- doing
         * so returns 0 for every read past that point, which freezes any app
         * clock built on of_time_us() (app hangs ~35 min into a session).
         * These are pure values, never errors: pass them straight through. */
        if ((unsigned long)eid == OF_EID_TIMER &&
            (fid == OF_TIMER_FID_GET_US || fid == OF_TIMER_FID_GET_MS))
            return (struct of_sbiret){ .error = OF_OK, .value = val };
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
    case SYS_getcwd:        return sys_getcwd((char *)a0, (uint32_t)a1);
    case SYS_chdir:         return sys_chdir((const char *)a0);
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
#ifdef OF_DEBUG_WALL_REVEAL
        /* Debug: make every app exit visible before a relaunch swallows it —
         * reveal the terminal (with visible VGA colors), print the code,
         * hold 3 s.  Distinguishes an exit/relaunch loop from a hang. */
        {
            extern void of_video_dbg_force_term_palette(void);
            SYS_COLOR_MODE = COLOR_MODE_8BIT;
            FB_MODE_SIZE = ((uint32_t)FB_HEIGHT << 16) | FB_WIDTH;
            FB_MODE_STRIDE = FB_STRIDE;
            VIDEO_SCALER_MODE = VIDEO_SCALER_SLOT_DEFAULT_320X240;
            TERM_FB_CTRL = 1;
            of_video_dbg_force_term_palette();
            of_term_puts("\n==SYS_exit== code=");
            {
                char rev[12], out[12];
                int n = 0, m = 0;
                uint32_t v = (uint32_t)a0;
                do { rev[n++] = (char)('0' + (v % 10u)); v /= 10u; } while (v && n < 11);
                while (n) out[m++] = rev[--n];
                out[m] = 0;
                of_term_puts(out);
            }
#ifdef OF_TARGET_SUPPORTS_RELAUNCH
            of_term_puts(os_menu_pending() ? " (relaunch pending; 3s hold)\n"
                                           : " (halt)\n");
#else
            of_term_puts(" (halt)\n");
#endif
            uint64_t dbg_dl = read_cycles() + 3ull * CPU_FREQ_HZ;
            while (read_cycles() < dbg_dl) {}
        }
#endif
#ifdef OF_TARGET_SUPPORTS_RELAUNCH
        /* If a launcher (menu.elf) is registered, an app exit returns to it
         * instead of halting — game → exit → menu.  Does not return. */
        if (os_menu_pending()) {
            os_request_menu_relaunch();
            os_relaunch();
        }
#endif
        /* Restore the boot console so the user sees it on EVERY display path
         * — of_video_console_reveal also mirrors the terminal into FB0 for
         * MiSTer's fb_direct output, where a bare TERM_FB_CTRL flip is
         * invisible (the 2026-08 black-screen trap). */
        of_video_console_reveal();
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
                sigalrm_div = 1u;
                sigalrm_cnt = 0u;
                if (of_mixer_use_sw) {
                    /* Keep the SW DAC pump tick alive; just stop SIGALRM. */
                    TIMER_PERIOD = CPU_FREQ_HZ / OS_TIMER_HZ;
                    TIMER_CTRL = TIMER_CTRL_ENABLE;
                } else {
                    TIMER_CTRL = 0;  /* disarm */
                }
            } else if (of_mixer_use_sw) {
                /* Pin the HW timer at 1 kHz for the DAC pump and divide that
                 * tick down to the requested SIGALRM interval (ceil). */
                uint64_t hw  = CPU_FREQ_HZ / OS_TIMER_HZ;       /* cycles / 1 ms */
                uint32_t div = (uint32_t)((cycles + hw - 1) / hw);
                sigalrm_div = div ? div : 1u;
                sigalrm_cnt = 0u;
                TIMER_PERIOD = (uint32_t)hw;
                TIMER_CTRL = TIMER_CTRL_ENABLE;
            } else {
                sigalrm_div = 1u;
                sigalrm_cnt = 0u;
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
        uint64_t file_size = 0;

        if (statx_fd >= 0 && statx_fd < MAX_FDS && fd_table[statx_fd].in_use) {
            /* fstat mode: use existing fd */
            fd_entry_t *f = &fd_table[statx_fd];
            if (f->size == 0 && f->slot_id > 0) {
                int64_t sz = of_file_size64(f->slot_id);
                if (sz > 0) f->size = (uint64_t)sz;
            }
            file_size = f->size;
        } else if (a1 && vfs_mount_count > 0) {
            int is_dir = 0;
            long rc = stat_path_info(a0, (const char *)a1,
                                     &file_size, &is_dir);
            if (rc < 0)
                return rc;
            (void)is_dir;
        } else if (a1) {
            /* stat mode: open file by path, get size, close */
            long fd = sys_openat(a0, a1, 0, 0);
            if (fd >= 0) {
                fd_entry_t *f = &fd_table[fd];
                if (f->size == 0 && f->slot_id > 0) {
                    int64_t sz = of_file_size64(f->slot_id);
                    if (sz > 0) f->size = (uint64_t)sz;
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
        sx[10] = (uint32_t)file_size;         /* stx_size low 32 (offset 40) */
        sx[11] = (uint32_t)(file_size >> 32); /* stx_size high 32 */
        return 0;
    }

    case SYS_fstat: {
        if (a0 >= 0 && a0 < MAX_FDS && fd_table[a0].in_use) {
            fd_entry_t *f = &fd_table[a0];
            if (f->size == 0 && f->slot_id > 0) {
                int64_t sz = of_file_size64(f->slot_id);
                if (sz > 0) f->size = (uint64_t)sz;
            }
            struct { uint64_t __pad[6]; uint64_t st_size; } *st = (void *)a1;
            memset(st, 0, 128);
            st->st_size = f->size;
            return 0;
        }
        return -EBADF;
    }
    case SYS_fstatat: {
        /* a0=dirfd, a1=path, a2=statbuf, a3=flags */
        if (vfs_mount_count > 0) {
            uint64_t file_size = 0;
            int is_dir = 0;
            long rc = stat_path_info(a0, (const char *)a1,
                                     &file_size, &is_dir);
            if (rc < 0) return rc;
            struct { uint64_t __pad[6]; uint64_t st_size; } *st = (void *)a2;
            memset(st, 0, 128);
            st->st_size = file_size;
            (void)is_dir;
            return 0;
        }
        /* Open the file, stat it, close */
        long fd = sys_openat(a0, a1, 0, 0);
        if (fd < 0) return fd;
        fd_entry_t *f = &fd_table[fd];
        if (f->size == 0 && f->slot_id > 0) {
            int64_t sz = of_file_size64(f->slot_id);
            if (sz > 0) f->size = (uint64_t)sz;
        }
        struct { uint64_t __pad[6]; uint64_t st_size; } *st = (void *)a2;
        memset(st, 0, 128);
        st->st_size = f->size;
        fd_table[fd].in_use = 0;
        return 0;
    }
    case SYS_fcntl:         return -ENOSYS;
    case SYS_ioctl:         return 0;   /* stub — makes musl stdout line-buffered */
    case SYS_faccessat: {
        if (vfs_mount_count == 0)
            return -ENOSYS;
        uint64_t size = 0;
        int is_dir = 0;
        (void)a2; (void)a3;
        return stat_path_info(a0, (const char *)a1, &size, &is_dir);
    }

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
#ifdef OF_TARGET_SUPPORTS_RELAUNCH
        case OF_BASE_FID_RELAUNCH:
            /* a0 = instance root, a1 = ELF (either may be NULL).  Capture the
             * strings before any teardown, then hand off — does not return. */
            os_request_relaunch((const char *)a0, (const char *)a1);
            os_relaunch();           /* noreturn on success */
            return OF_ERR_NOT_SUPPORTED;   /* unreachable */
        case OF_BASE_FID_SET_MENU:
            os_set_menu((const char *)a0, (const char *)a1);
            return 0;
#else
        case OF_BASE_FID_RELAUNCH:
        case OF_BASE_FID_SET_MENU:
            return OF_ERR_NOT_SUPPORTED;
#endif
        case OF_BASE_FID_LIST_INSTANCES:
            /* a0 = names buffer, a1 = stride, a2 = max.  Backed by the file
             * HAL on every target (returns 0 where there is no /games tree). */
            return of_file_list_instances((char *)a0, (uint32_t)a1,
                                          (uint32_t)a2);
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
            return 0;
        }
        break;

    case OF_EID_NET:
        return of_net_dispatch(fid, a0, a1, a2);

    case OF_EID_TIMER:
        switch (fid) {
        case OF_TIMER_FID_SET_CALLBACK:
            /* App expects its callback at a1 Hz; on a SW-mixer build the OS
             * divides the pinned 1 kHz tick down to that rate. */
            of_os_timer_set_callback((a0 && a1 > 0) ? (void (*)(void))a0 : NULL,
                                     (uint32_t)a1, 0 /* OS divides to app rate */);
            return 0;
        case OF_TIMER_FID_STOP:
            of_os_timer_set_callback(NULL, 0u, 0);
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
        case OF_FILE_FID_MOUNT:
            if (!a2 || stricmp((const char *)a2, "iso9660") != 0)
                return OF_ERR_NOT_SUPPORTED;
            if (((uint32_t)a3 & ~1u) != 0)
                return -EINVAL;
            return sys_mount_iso((const char *)a0, (const char *)a1);
        case OF_FILE_FID_UMOUNT:
            return sys_unmount_path((const char *)a0);
        case OF_FILE_FID_SLOT_FIND: {
            if (!a0)
                return OF_ERR_INVALID_PARAM;
            if (file_slot_count == 0)
                dir_probe_slots();
            uint32_t slot_id = 0;
            int rc = file_slot_find((const char *)a0, &slot_id);
            if (rc < 0)
                return rc;
            return (long)slot_id;
        }
        case OF_FILE_FID_DMA_STAGE_ALLOC: {
            void *ptr = of_file_dma_stage_alloc((uint32_t)a0, (uint32_t)a1);
            return ptr ? (long)ptr : OF_ERR_NO_SHMEM;
        }
        case OF_FILE_FID_DMA_STAGE_RESET:
            return of_file_dma_stage_reset();
        case OF_FILE_FID_ASYNC_MAX_READ:
            return (long)of_file_async_max_read();
        case OF_FILE_FID_DMA_STAGE_SIZE:
            return (long)of_file_dma_stage_size();
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
    /* Initial app mmap ceiling.  main.c tightens this after filesystem
     * scan and SoundFont preload, when dynamic audio reservations are
     * known.  Strict inequality vs the stack top is critical because
     * sys_mmap2 zero-fills every carved region. */
    mmap_bottom = APP_STACK_TOP - APP_STACK_SIZE;

    /* Reset mmap region tracker — the previous app's allocations
     * are no longer reachable once current_brk/mmap_bottom are
     * reseeded. */
    memset(mmap_slots, 0, sizeof(mmap_slots));
    mmap_slots_used = 0;

    /* Reset file slot registry (apps re-register on startup) */
    file_slot_count = 0;
    memset(file_slots, 0, sizeof(file_slots));

    memset(io_cache, 0, sizeof(io_cache));
    io_lru_counter = 0;

    /* Reset mounted filesystems */
    memset(vfs_mounts, 0, sizeof(vfs_mounts));
    vfs_mount_count = 0;
    path_copy(vfs_cwd, sizeof(vfs_cwd), "/");
}

int syscall_set_app_stack_top(uintptr_t stack_top)
{
    stack_top &= ~(uintptr_t)15u;
    if (stack_top < APP_STACK_SIZE)
        return -1;

    uintptr_t bottom = stack_top - APP_STACK_SIZE;
    if (bottom < current_brk)
        return -1;

    mmap_bottom = bottom;
    memset(mmap_slots, 0, sizeof(mmap_slots));
    mmap_slots_used = 0;
    return 0;
}
