//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS File HAL Implementation — MiSTer
 *
 * The Pocket implements this HAL over APF data-slot DMA; MiSTer implements
 * it over a FAT filesystem inside the OSD-mounted disk image (FatFs on top
 * of the hps_bridge sector engine, see blockdev.c).  The kernel's slot-id
 * file service is preserved by a fixed slot→path map plus directory
 * enumeration for everything else:
 *
 *   slot 1      boot.rom        RAM-backed (the ioctl-DMA'd os.bin staging)
 *   slot 2      /os.ini
 *   slot 3      /app.elf
 *   slot 7      /bank.ofsf
 *   slot 8      /config/shared.cfg     (nonvolatile, pre-created 256 KB)
 *   slot 9      /config/duke3d.cfg     (nonvolatile, pre-created 256 KB)
 *   slot 10-19  /saves/slot_N.sav      (nonvolatile, pre-created 256 KB)
 *   slot 4-6, 20+  assigned to files enumerated from / and /assets
 *
 * dir_probe_slots() in kernel/syscall.c walks ids 1..31 through
 * of_file_size64/of_file_flags/of_file_get_name and registers filenames, so
 * fopen-by-name works exactly as on Pocket.  Saves never resolve through
 * the registry (save_slot_from_filename parses "*_N.sav" first), so the
 * fixed slot_N.sav backing names are never visible to apps.
 */

#include "file.h"
#include "disk.h"
#include "cache.h"
#include "regs.h"
#include "save.h"
#include "terminal.h"
#include "blockdev.h"
#include "hps_regs.h"
#include "mister_file.h"
#include "fatfs/ff.h"
#include <string.h>

#define BOOT_SLOT_ID        1u
#define FIL_CACHE_SLOTS     4
#define DYN_SLOT_FIRST_LOW  4u    /* dynamic ids 4..6 first (Pocket layout), */
#define DYN_SLOT_FIRST_HIGH 20u   /* then 20..31 */
#define DYN_SLOT_END        32u
#define MISTER_INSTANCE_ROOT_MAX 48u   /* "/games/<name>" */
#define MISTER_PATH_MAX          96u   /* instance_root + "/saves/slot_N.sav" */
/* Dynamic-slot stored path must hold a full instance-relative path
 * (instance_root + "/assets/" + filename), so it tracks MISTER_PATH_MAX. */
#define DYN_NAME_MAX        MISTER_PATH_MAX

/* os.bin staging copy, read through the uncached alias so the CPU never
 * caches lines the boot.rom ioctl DMA may rewrite on a reload. */
#define BOOT_STAGE_UNCACHED (SDRAM_UNCACHED_BASE + OF_TARGET_CRAM0_BRIDGE + \
                             OF_TARGET_CRAM0_OS_OFFSET)

/* ---------------------------------------------------------------------- */

static FATFS fat_volume;
static enum { FS_UNMOUNTED, FS_MOUNTED, FS_FAILED } fs_state;
static int fs_prev_present;

typedef struct {
    uint32_t slot_id;
    char     path[DYN_NAME_MAX];
} dyn_slot_t;

static dyn_slot_t dyn_slots[16];
static int dyn_slot_count;
static int dyn_enumerated;

typedef struct {
    uint32_t slot_id;   /* 0 = empty */
    int      writable;
    uint32_t stamp;
    FIL      fil;
} fil_cache_t;

static fil_cache_t fil_cache[FIL_CACHE_SLOTS];
static uint32_t fil_stamp;

/* Async read state (deferred-callback model — see of_file_read_async). */
static struct {
    volatile int active;
    volatile int completed;
    int          token;
    int          result;
    void       (*callback)(int token, int result);
} async_state;

static int async_token_counter;
static uint32_t dma_stage_next;

/* FatFs reentrancy guard — see mister_file.h. */
volatile int mister_fs_depth;

/* One-entry f_stat memo: dir_probe_slots calls of_file_size64 and then
 * of_file_flags (which re-checks size) for every slot at boot — without
 * this each slot costs two full FAT path walks.  Invalidated on any
 * write/open-writable/mount transition. */
static struct { uint32_t slot_id; int64_t size; int valid; } size_memo;

static void size_memo_invalidate(void) { size_memo.valid = 0; }

/* ---------------------------------------------------------------------- */

void of_file_set_idle_hook(void (*hook)(void)) {
    of_blockdev_set_idle_hook(hook);
}

static void fil_cache_drop_all(void) {
    for (int i = 0; i < FIL_CACHE_SLOTS; i++) {
        if (fil_cache[i].slot_id)
            f_close(&fil_cache[i].fil);
        fil_cache[i].slot_id = 0;
    }
}

static int ensure_mounted(void) {
    int present = of_blockdev_present();

    /* Image came or went — restart the mount state machine. */
    if (present != fs_prev_present) {
        fs_prev_present = present;
        if (fs_state == FS_MOUNTED || fs_state == FS_FAILED) {
            fil_cache_drop_all();
            f_unmount("");
        }
        fs_state = FS_UNMOUNTED;
        dyn_enumerated = 0;
        dyn_slot_count = 0;
        size_memo_invalidate();
    }

    if (!present)
        return 0;
    if (fs_state == FS_MOUNTED)
        return 1;
    if (fs_state == FS_FAILED)
        return 0;

    FRESULT fr = f_mount(&fat_volume, "", 1);
    if (fr != FR_OK) {
        of_term_printf("[file] FAT mount failed (%d)\n", (int)fr);
        fs_state = FS_FAILED;
        return 0;
    }

    fs_state = FS_MOUNTED;
    return 1;
}

/* Small local case-insensitive compare (kernel's stricmp is static). */
static int name_ieq(const char *a, const char *b) {
    while (*a && *b) {
        char ca = *a, cb = *b;
        if (ca >= 'A' && ca <= 'Z') ca += 32;
        if (cb >= 'A' && cb <= 'Z') cb += 32;
        if (ca != cb) return 0;
        a++; b++;
    }
    return *a == *b;
}

static int dyn_register(const char *dir, const char *name) {
    if (dyn_slot_count >= (int)(sizeof(dyn_slots) / sizeof(dyn_slots[0])))
        return -1;

    uint32_t id;
    int low_used = 0, high_used = 0;
    for (int i = 0; i < dyn_slot_count; i++) {
        uint32_t s = dyn_slots[i].slot_id;
        if (s >= DYN_SLOT_FIRST_LOW && s < DYN_SLOT_FIRST_LOW + 3) low_used++;
        if (s >= DYN_SLOT_FIRST_HIGH) high_used++;
    }
    if (low_used < 3)
        id = DYN_SLOT_FIRST_LOW + (uint32_t)low_used;
    else if (DYN_SLOT_FIRST_HIGH + (uint32_t)high_used < DYN_SLOT_END)
        id = DYN_SLOT_FIRST_HIGH + (uint32_t)high_used;
    else
        return -1;

    dyn_slot_t *d = &dyn_slots[dyn_slot_count];
    d->slot_id = id;

    uint32_t pos = 0;
    for (const char *p = dir; *p && pos < DYN_NAME_MAX - 2; p++)
        d->path[pos++] = *p;
    for (const char *p = name; *p && pos < DYN_NAME_MAX - 1; p++)
        d->path[pos++] = *p;
    d->path[pos] = '\0';

    dyn_slot_count++;
    return 0;
}

/* Optional per-instance root for the game's files, e.g. "/games/Quake".
 * Empty (the default) selects the legacy single-game layout where files live
 * at the image root.  The launcher (menu.elf / Arch-A relaunch) sets this
 * before switching into an instance; with it empty every join_root() result
 * is byte-identical to the pre-instance paths, so existing single-game images
 * are unaffected. */
static char instance_root[MISTER_INSTANCE_ROOT_MAX];

/* Join instance_root + leaf (leaf begins with '/') into buf. */
static const char *join_root(const char *leaf, char *buf, uint32_t max) {
    uint32_t pos = 0;
    for (uint32_t i = 0; instance_root[i] && pos < max - 1u; i++)
        buf[pos++] = instance_root[i];
    for (uint32_t i = 0; leaf[i] && pos < max - 1u; i++)
        buf[pos++] = leaf[i];
    buf[pos] = '\0';
    return buf;
}

static void enumerate_dir(const char *dir) {
    DIR dj;
    FILINFO fno;

    /* Only close what was opened: on FR_NO_PATH (e.g. /assets/ absent —
     * a perfectly normal image) dj is uninitialized stack memory, and
     * f_closedir's validate/dec_share would act on garbage. */
    FRESULT fr0 = f_findfirst(&dj, &fno, dir, "*");
    FRESULT fr = fr0;
    while (fr == FR_OK && fno.fname[0]) {
        if (!(fno.fattrib & AM_DIR)) {
            /* Skip files already covered by fixed slots. */
            if (!name_ieq(fno.fname, "os.ini") &&
                !name_ieq(fno.fname, "app.elf") &&
                !name_ieq(fno.fname, "bank.ofsf") &&
                !name_ieq(fno.fname, "boot.rom"))
                dyn_register(dir, fno.fname);
        }
        fr = f_findnext(&dj, &fno);
    }
    if (fr0 == FR_OK)
        f_closedir(&dj);
}

static void ensure_enumerated(void) {
    if (dyn_enumerated || !ensure_mounted())
        return;
    dyn_enumerated = 1;
    /* Enumerate the active instance's tree (root when no instance is set). */
    char dirbuf[MISTER_PATH_MAX];
    enumerate_dir(join_root("/", dirbuf, sizeof(dirbuf)));
    enumerate_dir(join_root("/assets/", dirbuf, sizeof(dirbuf)));
}

void of_file_set_instance_root(const char *root) {
    uint32_t i = 0;
    if (root && root[0] == '/') {
        while (root[i] && i < MISTER_INSTANCE_ROOT_MAX - 1u) {
            instance_root[i] = root[i];
            i++;
        }
        /* Drop a trailing '/': leaves are always joined as "/<leaf>". */
        while (i > 1u && instance_root[i - 1u] == '/')
            i--;
    }
    instance_root[i] = '\0';
}

const char *of_file_get_instance_root(void) {
    return instance_root;
}

void of_file_relaunch_reset(void) {
    mister_fs_enter();
    /* Close every cached backing FIL (releases FF_FS_LOCK and flushes any
     * FatFs-buffered tail; nv writes are already f_sync'd per write). */
    fil_cache_drop_all();
    /* Cancel a pending async read whose completion callback points into the
     * outgoing app's now-stale code. */
    async_state.active = 0;
    async_state.completed = 0;
    async_state.callback = (void *)0;
    /* Force the dynamic asset slots to re-enumerate for the next instance. */
    dyn_enumerated = 0;
    dyn_slot_count = 0;
    size_memo_invalidate();
    mister_fs_exit();
}

/* "/games/<name>/os.ini" -> buf. */
static void build_instance_ini(const char *name, char *buf, uint32_t max) {
    const char *pfx = "/games/";
    const char *sfx = "/os.ini";
    uint32_t pos = 0;
    for (uint32_t i = 0; pfx[i] && pos < max - 1u; i++) buf[pos++] = pfx[i];
    for (uint32_t i = 0; name[i] && pos < max - 1u; i++) buf[pos++] = name[i];
    for (uint32_t i = 0; sfx[i] && pos < max - 1u; i++) buf[pos++] = sfx[i];
    buf[pos] = '\0';
}

int of_file_list_instances(char *names, uint32_t stride, uint32_t max) {
    if (!names || stride == 0u || max == 0u || !ensure_mounted())
        return 0;

    mister_fs_enter();
    DIR dj;
    FILINFO fno;
    int count = 0;
    /* f_findfirst leaves dj uninitialized on FR_NO_PATH (no /games dir — a
     * normal single-game image), so only close on FR_OK (see enumerate_dir). */
    FRESULT fr0 = f_findfirst(&dj, &fno, "/games", "*");
    FRESULT fr = fr0;
    while (fr == FR_OK && fno.fname[0] && (uint32_t)count < max) {
        if (fno.fattrib & AM_DIR) {
            /* Only a directory that actually carries an os.ini is a launchable
             * instance — "read the ini" to qualify it. */
            char ini[MISTER_PATH_MAX];
            FILINFO si;
            build_instance_ini(fno.fname, ini, sizeof(ini));
            if (f_stat(ini, &si) == FR_OK) {
                /* Skip a name that won't fit the caller's stride rather than
                 * emit a truncated one the caller would then fail to relaunch. */
                uint32_t nlen = 0;
                while (fno.fname[nlen]) nlen++;
                if (nlen < stride) {
                    char *dst = names + (uint32_t)count * stride;
                    for (uint32_t k = 0; k <= nlen; k++)
                        dst[k] = fno.fname[k];
                    count++;
                }
            }
        }
        fr = f_findnext(&dj, &fno);
    }
    if (fr0 == FR_OK)
        f_closedir(&dj);
    mister_fs_exit();
    return count;
}

/* Resolve a slot id to a FAT path.  Returns NULL for unmapped slots and for
 * the RAM-backed boot slot.  A game's own files — os.ini, app.elf, sound bank,
 * per-game settings, and saves — resolve under instance_root when an instance
 * is active; the shared config (8) is global across instances. */
static const char *slot_path(uint32_t slot_id, char *buf, uint32_t max) {
    switch (slot_id) {
    case 2:  return join_root("/os.ini", buf, max);
    case 3:  return join_root("/app.elf", buf, max);
    case 7:  return join_root("/bank.ofsf", buf, max);
    case 8:  return "/config/shared.cfg";              /* shared across instances */
    case 9:  return join_root("/config/duke3d.cfg", buf, max);
    default: break;
    }

    if (slot_id >= 10 && slot_id < 10 + OF_TARGET_SAVE_MAX_SLOTS) {
        uint32_t n = slot_id - 10;
        /* slot_N.sav uses a single digit; the save-file contract (and the
         * image builder's preallocation) depend on it.  Guard the assumption. */
        _Static_assert(OF_TARGET_SAVE_MAX_SLOTS <= 10,
                       "single-digit slot_N.sav naming requires <=10 save slots");
        char leaf[20];   /* "/saves/slot_N.sav" + NUL */
        const char *pfx = "/saves/slot_";
        uint32_t pos = 0;
        while (pfx[pos]) { leaf[pos] = pfx[pos]; pos++; }
        leaf[pos++] = (char)('0' + n);
        leaf[pos++] = '.'; leaf[pos++] = 's'; leaf[pos++] = 'a'; leaf[pos++] = 'v';
        leaf[pos] = '\0';
        return join_root(leaf, buf, max);
    }

    ensure_enumerated();
    for (int i = 0; i < dyn_slot_count; i++) {
        if (dyn_slots[i].slot_id == slot_id)
            return dyn_slots[i].path;
    }

    return (void *)0;
}

/* Shared FIL handle cache.  Write opens are exclusive under FF_FS_LOCK, so
 * a cached read handle for the same slot is evicted before reopening
 * writable (and vice versa). */
static FIL *open_slot_impl(uint32_t slot_id, int writable) {
    if (!ensure_mounted())
        return (void *)0;

    char pathbuf[MISTER_PATH_MAX];
    const char *path = slot_path(slot_id, pathbuf, sizeof(pathbuf));
    if (!path)
        return (void *)0;

    fil_cache_t *hit = (void *)0, *victim = (void *)0;
    for (int i = 0; i < FIL_CACHE_SLOTS; i++) {
        fil_cache_t *c = &fil_cache[i];
        if (c->slot_id == slot_id) { hit = c; break; }
        /* Prefer an empty entry, else the least recently used. */
        if (!victim ||
            (!c->slot_id && victim->slot_id) ||
            (c->slot_id && victim->slot_id && c->stamp < victim->stamp))
            victim = c;
    }

    if (hit) {
        if (hit->writable == writable || (hit->writable && !writable)) {
            hit->stamp = ++fil_stamp;
            return &hit->fil;
        }
        /* Mode upgrade: reopen writable. */
        f_close(&hit->fil);
        hit->slot_id = 0;
        victim = hit;
    }

    if (!victim)
        return (void *)0;
    if (victim->slot_id) {
        f_close(&victim->fil);
        victim->slot_id = 0;
    }

    BYTE mode = writable ? (FA_READ | FA_WRITE) : FA_READ;
    FRESULT fr = f_open(&victim->fil, path, mode);
    if (fr != FR_OK)
        return (void *)0;

    victim->slot_id = slot_id;
    victim->writable = writable;
    victim->stamp = ++fil_stamp;
    return &victim->fil;
}

FIL *mister_file_open_slot(uint32_t slot_id, int writable) {
    if (writable)
        size_memo_invalidate();
    mister_fs_enter();
    FIL *fil = open_slot_impl(slot_id, writable);
    mister_fs_exit();
    return fil;
}

void mister_file_drop_slot(uint32_t slot_id) {
    mister_fs_enter();
    for (int i = 0; i < FIL_CACHE_SLOTS; i++) {
        if (fil_cache[i].slot_id == slot_id) {
            f_close(&fil_cache[i].fil);
            fil_cache[i].slot_id = 0;
        }
    }
    mister_fs_exit();
    size_memo_invalidate();
}

void of_file_init(void) {
    of_blockdev_init();
    fs_state = FS_UNMOUNTED;
    fs_prev_present = -1;       /* force state machine through first call */
    dyn_enumerated = 0;
    dyn_slot_count = 0;
    fil_stamp = 0;
    for (int i = 0; i < FIL_CACHE_SLOTS; i++)
        fil_cache[i].slot_id = 0;
    async_state.active = 0;
    async_state.completed = 0;
    async_state.callback = (void *)0;
    async_token_counter = 0;
    dma_stage_next = 0;

    mister_fs_enter();
    int mounted = ensure_mounted();
    mister_fs_exit();
    if (mounted)
        of_term_printf("[file] disk image mounted (%u MB)\n",
                       (unsigned)(of_blockdev_size() >> 20));
}

/* MiSTer has no APF shutdown handshake — the framework hard-resets the
 * core on exit.  Saves are durable at write time (save.c f_syncs every
 * nonvolatile write), so the only job here is draining a deferred async
 * completion.  Called periodically from IRQ context (kernel/irq.c). */
void of_check_shutdown(void) {
    of_file_async_irq_service();
}

/* ---------------------------------------------------------------------- */

static int boot_slot_read(uint32_t slot_offset, void *dest, uint32_t length) {
    uint32_t boot_len = HPS_BOOT_LEN;
    if (!(HPS_STATUS & HPS_STATUS_BOOT_LOADED) || boot_len == 0)
        return OF_ERR_NOT_SUPPORTED;
    if (slot_offset > boot_len || length > boot_len - slot_offset)
        return OF_ERR_BAD_RANGE;
    memcpy(dest, (const void *)(BOOT_STAGE_UNCACHED + slot_offset), length);
    return 0;
}

static int fat_read_body(uint32_t slot_id, uint32_t slot_offset,
                         void *dest, uint32_t length) {
    FIL *fil = open_slot_impl(slot_id, 0);
    if (!fil)
        return OF_ERR_NOT_SUPPORTED;

    if (f_lseek(fil, slot_offset) != FR_OK)
        return OF_ERR_BAD_RANGE;

    UINT br = 0;
    FRESULT fr = f_read(fil, dest, length, &br);
    if (fr != FR_OK)
        return OF_ERR_IO;
    if (br != length)
        return OF_ERR_BAD_RANGE;   /* short read past EOF */
    return 0;
}

static int fat_read_impl(uint32_t slot_id, uint32_t slot_offset,
                         void *dest, uint32_t length) {
    if (slot_id == BOOT_SLOT_ID)
        return boot_slot_read(slot_offset, dest, length);

    mister_fs_enter();
    int rc = fat_read_body(slot_id, slot_offset, dest, length);
    mister_fs_exit();
    return rc;
}

static int fat_probe(void) {
    return of_blockdev_present();
}

static long fat_size_impl(uint32_t slot_id) {
    long sz = of_file_size(slot_id);
    return sz;
}

const of_disk_driver_t of_disk_bridge = {
    .name  = "VHD",
    .probe = fat_probe,
    .read  = fat_read_impl,
    .size  = fat_size_impl,
};

int of_file_read(uint32_t slot_id, uint32_t slot_offset,
                 void *dest, uint32_t length) {
    return of_disk_read(slot_id, slot_offset, dest, length);
}

int of_file_read_chunked(uint32_t slot_id, uint32_t slot_offset,
                         void *dest, uint32_t total) {
    uint32_t done = 0;
    while (done < total) {
        uint32_t chunk = total - done;
        if (chunk > DMA_CHUNK_SIZE)
            chunk = DMA_CHUNK_SIZE;
        int rc = of_file_read(slot_id, slot_offset + done,
                              (void *)((uintptr_t)dest + done), chunk);
        if (rc < 0)
            return rc;
        done += chunk;
    }
    return 0;
}

/* "Raw" reads/writes address memory by SDRAM byte offset (the MiSTer
 * bridge address space).  No separate cache discipline is needed — FatFs
 * moves the bytes through the CPU's cached view. */
int of_file_read_raw(uint32_t slot_id, uint32_t slot_offset,
                     uint32_t bridge_addr, uint32_t length) {
    if (length > DMA_CHUNK_SIZE)
        return OF_ERR_BAD_RANGE;
    if (async_state.active)
        return OF_ERR_BUSY;
    void *dest = (void *)(uintptr_t)(SDRAM_BASE + bridge_addr);
    return fat_read_impl(slot_id, slot_offset, dest, length);
}

static int slot_write_body(uint32_t slot_id, uint32_t slot_offset,
                           uint32_t bridge_addr, uint32_t length) {
    FIL *fil = open_slot_impl(slot_id, 1);
    if (!fil)
        return OF_ERR_NOT_SUPPORTED;
    if (f_lseek(fil, slot_offset) != FR_OK)
        return OF_ERR_BAD_RANGE;

    const void *src = (const void *)(uintptr_t)(SDRAM_BASE + bridge_addr);
    UINT bw = 0;
    if (f_write(fil, src, length, &bw) != FR_OK || bw != length)
        return OF_ERR_IO;
    if (f_sync(fil) != FR_OK)
        return OF_ERR_IO;
    return 0;
}

int of_file_slot_write_at(uint32_t slot_id, uint32_t slot_offset,
                          uint32_t bridge_addr, uint32_t length) {
    if (async_state.active)
        return OF_ERR_BUSY;

    size_memo_invalidate();
    mister_fs_enter();
    int rc = slot_write_body(slot_id, slot_offset, bridge_addr, length);
    mister_fs_exit();
    return rc;
}

int of_file_slot_write(uint32_t slot_id, uint32_t bridge_addr,
                       uint32_t length) {
    return of_file_slot_write_at(slot_id, 0, bridge_addr, length);
}

int of_file_slot_write_chunked(uint32_t slot_id, uint32_t slot_offset,
                               uint32_t bridge_addr, uint32_t total,
                               uint32_t chunk_size) {
    if (total == 0)
        return of_file_slot_write_at(slot_id, slot_offset, bridge_addr, 0);
    if (chunk_size == 0 || chunk_size > total)
        chunk_size = total;

    uint32_t done = 0;
    while (done < total) {
        uint32_t chunk = total - done;
        if (chunk > chunk_size)
            chunk = chunk_size;
        int rc = of_file_slot_write_at(slot_id, slot_offset + done,
                                       bridge_addr + done, chunk);
        if (rc < 0)
            return rc;
        done += chunk;
    }
    return 0;
}

/* ---------------------------------------------------------------------- */

int of_file_get_name(uint32_t slot_id, char *name_out, uint32_t name_max) {
    const char *base = (void *)0;
    char pathbuf[MISTER_PATH_MAX];

    if (!name_out || name_max == 0)
        return -1;

    if (slot_id == BOOT_SLOT_ID) {
        base = "boot.rom";
    } else {
        mister_fs_enter();
        const char *path = slot_path(slot_id, pathbuf, sizeof(pathbuf));
        mister_fs_exit();
        if (!path)
            return -1;
        base = path;
        for (const char *p = path; *p; p++)
            if (*p == '/') base = p + 1;
    }

    uint32_t i;
    for (i = 0; i < name_max - 1 && base[i]; i++)
        name_out[i] = base[i];
    name_out[i] = '\0';
    return (i > 0) ? 0 : -1;
}

long of_file_flags(uint32_t slot_id) {
    if (of_file_size64(slot_id) > 0)
        return (long)slot_id;
    /* Pre-created nonvolatile slots always exist; report them present so
     * dir_probe_slots registers them even when logically empty. */
    char pathbuf[MISTER_PATH_MAX];
    mister_fs_enter();
    const char *p = (slot_id >= 8 && slot_id < 10 + OF_TARGET_SAVE_MAX_SLOTS)
                        ? slot_path(slot_id, pathbuf, sizeof(pathbuf))
                        : (void *)0;
    mister_fs_exit();
    if (p)
        return (long)slot_id;
    return -1;
}

static int64_t size64_body(uint32_t slot_id) {
    if (!ensure_mounted())
        return -1;

    char pathbuf[MISTER_PATH_MAX];
    const char *path = slot_path(slot_id, pathbuf, sizeof(pathbuf));
    if (!path)
        return -1;

    FILINFO fno;
    if (f_stat(path, &fno) != FR_OK)
        return -1;
    return fno.fsize ? (int64_t)fno.fsize : -1;
}

int64_t of_file_size64(uint32_t slot_id) {
    if (slot_id == BOOT_SLOT_ID) {
        uint32_t len = (HPS_STATUS & HPS_STATUS_BOOT_LOADED) ? HPS_BOOT_LEN : 0;
        return len ? (int64_t)len : -1;
    }

    if (size_memo.valid && size_memo.slot_id == slot_id)
        return size_memo.size;

    mister_fs_enter();
    int64_t sz = size64_body(slot_id);
    mister_fs_exit();

    size_memo.slot_id = slot_id;
    size_memo.size = sz;
    size_memo.valid = 1;
    return sz;
}

long of_file_size(uint32_t slot_id) {
    int64_t size = of_file_size64(slot_id);
    if (size <= 0)
        return -1;
    if (size > 0x7FFFFFFFll)
        return 0x7FFFFFFFl;
    return (long)size;
}

/* Synthesized APF datatable view, for boot diagnostics that dump id/size
 * pairs.  Entry layout mirrors the Pocket contract:
 *   entries 0-7 → ids 0-7, entry 8 → presave (id 8), entries 9-18 → 10-19 */
int of_file_datatable_word(uint32_t word, uint32_t *value_out) {
    uint32_t entry = word >> 1;
    uint32_t slot_id;

    if (entry <= 7)
        slot_id = entry;
    else if (entry == 8)
        slot_id = 8;
    else if (entry <= 18)
        slot_id = 10 + (entry - 9);
    else
        return -1;

    if (value_out) {
        if ((word & 1u) == 0) {
            *value_out = slot_id & 0xFFFFu;
        } else {
            int64_t sz = of_file_size64(slot_id);
            *value_out = sz > 0 ? (uint32_t)sz : 0;
        }
    }
    return 0;
}

/* ======================================================================
 * Async file read — deferred-callback model.
 *
 * The sector engine is synchronous from the CPU's point of view (no
 * completion IRQ in v1), so the read itself happens inline and the
 * callback is deferred to the next of_file_async_poll() or the periodic
 * IRQ drain (of_check_shutdown → of_file_async_irq_service).
 *
 * Unlike the Pocket, the IRQ drain is GATED on the FatFs reentrancy
 * guard (mister_fs_depth, see mister_file.h): a callback may issue new
 * file I/O, and FatFs is non-reentrant — delivery while the main thread
 * is inside an f_* call would corrupt filesystem state.  When gated, the
 * completion is simply picked up by a later tick or by poll().
 * ====================================================================== */

static uint32_t align_up_u32(uint32_t value, uint32_t align) {
    return (value + align - 1u) & ~(align - 1u);
}

void *of_file_dma_stage_alloc(uint32_t size, uint32_t align) {
    if (size == 0)
        return (void *)0;
    if (align < 4u)
        align = 4u;
    if ((align & (align - 1u)) != 0)
        return (void *)0;

    uint32_t off = align_up_u32(dma_stage_next, align);
    if (off > OF_TARGET_CRAM0_APP_DMA_SIZE ||
        size > OF_TARGET_CRAM0_APP_DMA_SIZE - off)
        return (void *)0;

    dma_stage_next = off + size;
    return (void *)(uintptr_t)(CRAM0_BASE + OF_TARGET_CRAM0_APP_DMA_OFFSET + off);
}

int of_file_dma_stage_reset(void) {
    if (async_state.active)
        return OF_ERR_BUSY;
    dma_stage_next = 0;
    return 0;
}

uint32_t of_file_async_max_read(void) {
    return OF_TARGET_CRAM0_DMA_CHUNK_SIZE;
}

uint32_t of_file_dma_stage_size(void) {
    return OF_TARGET_CRAM0_APP_DMA_SIZE;
}

static void async_drain(void) {
    if (!async_state.active || !async_state.completed)
        return;

    int token = async_state.token;
    int result = async_state.result;
    void (*cb)(int, int) = async_state.callback;

    async_state.active = 0;
    async_state.completed = 0;
    async_state.callback = (void *)0;

    if (cb)
        cb(token, result);
}

int of_file_read_async(uint32_t slot_id, uint32_t slot_offset,
                       void *dest, uint32_t length,
                       void (*callback)(int token, int result)) {
    if (async_state.active)
        return OF_ERR_BUSY;
    if (length > OF_TARGET_CRAM0_DMA_CHUNK_SIZE)
        return OF_ERR_BAD_RANGE;

    int token = async_token_counter++;
    int rc = of_disk_read(slot_id, slot_offset, dest, length);

    async_state.token = token;
    async_state.result = rc;
    async_state.callback = callback;
    /* Publish the payload before the flags async_drain() gates on, so the IRQ
     * drain can never observe completed=1 with a stale token/result/callback. */
    __sync_synchronize();
    async_state.completed = 1;
    async_state.active = 1;

    return token;
}

void of_file_async_irq_service(void) {
    /* IRQ context: only deliver when the main thread is outside FatFs. */
    if (mister_fs_depth == 0)
        async_drain();
}

int of_file_async_poll(void) {
    if (async_state.active && async_state.completed) {
        async_drain();
        return 1;
    }
    return 0;
}

int of_file_async_busy(void) {
    return async_state.active;
}
