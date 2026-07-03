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
 *
 * Multi-vhd model (multidisk-capable RTL): up to three images mount as
 * FatFs volumes, one per blockdev disk index:
 *
 *   volume 0  S0 family library (or the legacy all-in-one image)
 *   volume 1  S1 writable instance volume (os.ini, saves, config)
 *   volume 2  S2 borrow library
 *
 * By-name and fixed-slot resolution searches mounted volumes S1 -> S2 -> S0
 * (os.ini prefers 1:/os.ini then 0:/os.ini); every nonvolatile write slot
 * (8, 9, 10-19) pins to volume 1 while in instance mode (multidisk cap AND
 * S1 mounted), else to volume 0.  Cross-volume duplicate basenames register
 * once (first volume in search order wins), so an S1 file shadows S2/S0.
 * Names the 16-entry dynamic table can't hold (a 60+-file library) are
 * resolved on demand by of_file_resolve_name() into overflow slot ids
 * (32+), which the kernel uses as a registry-miss fall-through.
 *
 * Legacy: with the multidisk cap clear (old RTL) or only S0 mounted, every
 * path resolves on volume 0 exactly as the single-volume code did ("0:" is
 * FatFs's default drive), no cross-volume probing happens, and behavior is
 * unchanged.
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
/* Overflow name slots: ids handed out by of_file_resolve_name() when a
 * basename isn't held by the dynamic table (the kernel registry tops out
 * at 32 entries / 16 dyn ids — a 60+-file library needs more).  Ids are
 * stable for the app's lifetime (never rebound while fds / the kernel io
 * cache may reference them); the table resets on init/relaunch only. */
#define OVF_SLOT_FIRST      DYN_SLOT_END   /* 32.. */
#define OVF_SLOT_COUNT      64u
#define MISTER_INSTANCE_ROOT_MAX 48u   /* "/games/<name>" */
#define MISTER_PATH_MAX          100u  /* "N:" + instance_root + longest leaf */
/* Path budget: the "N:" volume prefix (2 chars) + a full instance root +
 * the longest fixed leaf must fit every pathbuf[MISTER_PATH_MAX]. */
_Static_assert(2u + (MISTER_INSTANCE_ROOT_MAX - 1u)
                  + sizeof("/config/duke3d.cfg") <= MISTER_PATH_MAX,
               "MISTER_PATH_MAX too small for volume-prefixed slot paths");
/* Dynamic-slot stored path must hold a full volume-prefixed path
 * ("N:" + instance_root + "/assets/" + filename), so it tracks
 * MISTER_PATH_MAX.  Longer filenames truncate exactly as before. */
#define DYN_NAME_MAX        MISTER_PATH_MAX

/* os.bin staging copy, read through the uncached alias so the CPU never
 * caches lines the boot.rom ioctl DMA may rewrite on a reload. */
#define BOOT_STAGE_UNCACHED (SDRAM_UNCACHED_BASE + OF_TARGET_CRAM0_BRIDGE + \
                             OF_TARGET_CRAM0_OS_OFFSET)

/* ---------------------------------------------------------------------- */

typedef enum { FS_UNMOUNTED, FS_MOUNTED, FS_FAILED } fs_state_t;

static FATFS      fat_volume[FF_VOLUMES];
static fs_state_t fs_state[FF_VOLUMES];
static int        fs_prev_present[FF_VOLUMES];

/* FatFs logical-drive prefixes, indexed by volume. */
static const char *const vol_drv[FF_VOLUMES] = { "0:", "1:", "2:" };

/* By-name / fixed-slot search order: S1 instance -> S2 borrow -> S0 family.
 * os.ini has its own S1 -> S0 order (the borrow library never carries the
 * launch config). */
static const uint8_t vol_order_game[3]  = { 1, 2, 0 };
static const uint8_t vol_order_osini[2] = { 1, 0 };

typedef struct {
    uint32_t slot_id;
    char     path[DYN_NAME_MAX];
} dyn_slot_t;

static dyn_slot_t dyn_slots[16];
static int dyn_slot_count;
static int dyn_enumerated;
/* First dyn index belonging to the volume currently being enumerated —
 * entries below it came from earlier (higher-priority) volumes and dedup
 * new registrations by basename; entries at/above it are same-volume and
 * keep the historical no-dedup behavior. */
static int dyn_vol_start;

/* Overflow name slots (see OVF_SLOT_FIRST above).  path[0]=='\0' = free;
 * id = OVF_SLOT_FIRST + index. */
static char ovf_paths[OVF_SLOT_COUNT][DYN_NAME_MAX];
static int  ovf_count;

/* By-name config slots (hal/file.h of_file_config_slot): per-game settings
 * files opened for writing by NAME (fopen("doom.cfg","wb")).  Only the LEAF
 * name is stored; slot_path() builds "N:" + instance_root + "/config/<name>"
 * live against nv_volume(), so the binding follows the mount/instance state
 * exactly like the fixed config slots 8/9 and writes can never land on a
 * library volume.  id = OF_FILE_CFG_SLOT_BASE + index. */
#define CFG_NAME_MAX 32u
static char cfg_names[OF_FILE_CFG_SLOT_COUNT][CFG_NAME_MAX];
static int  cfg_count;

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

static int ensure_vol_mounted(unsigned vol) {
    int present = of_blockdev_present(vol);

    /* Image came or went — restart this volume's mount state machine.
     * Slot->volume bindings can change under any transition (a slot that
     * resolved on S0 may now shadow-resolve on S1), so the whole shared
     * FIL cache is dropped, not just this volume's handles — exactly the
     * conservative reset the single-volume code did. */
    if (present != fs_prev_present[vol]) {
        fs_prev_present[vol] = present;
        if (fs_state[vol] == FS_MOUNTED || fs_state[vol] == FS_FAILED) {
            fil_cache_drop_all();
            f_unmount(vol_drv[vol]);
        }
        fs_state[vol] = FS_UNMOUNTED;
        dyn_enumerated = 0;
        dyn_slot_count = 0;
        size_memo_invalidate();
    }

    if (!present)
        return 0;
    if (fs_state[vol] == FS_MOUNTED)
        return 1;
    if (fs_state[vol] == FS_FAILED)
        return 0;

    FRESULT fr = f_mount(&fat_volume[vol], vol_drv[vol], 1);
    if (fr != FR_OK) {
        of_term_printf("[file] FAT mount failed on %s (%d)\n",
                       vol_drv[vol], (int)fr);
        fs_state[vol] = FS_FAILED;
        return 0;
    }

    fs_state[vol] = FS_MOUNTED;
    return 1;
}

/* Refresh every volume's mount state machine; 1 if ANY volume is usable.
 * Absent S1/S2 (or a capability-less bitstream, where their present bits
 * read 0) simply stay unmounted — never an error. */
static int ensure_mounted(void) {
    int any = 0;
    for (unsigned vol = 0; vol < FF_VOLUMES; vol++)
        any |= ensure_vol_mounted(vol);
    return any;
}

static int vol_mounted(unsigned vol) {
    return fs_state[vol] == FS_MOUNTED;
}

/* Instance mode: multidisk-capable RTL with the S1 instance volume
 * mounted.  All nonvolatile writes (saves + config) pin to volume 1 then;
 * otherwise they stay on volume 0 (legacy).  Callers must have run
 * ensure_mounted() first. */
static int instance_mode(void) {
    return of_blockdev_multidisk_cap() && vol_mounted(1);
}

static unsigned nv_volume(void) {
    return instance_mode() ? 1u : 0u;
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

/* Basename of a stored (volume-prefixed) path. */
static const char *stored_basename(const char *path) {
    const char *base = path;
    for (const char *p = path; *p; p++)
        if (*p == '/') base = p + 1;
    return base;
}

static int dyn_register(const char *dir, const char *name) {
    if (dyn_slot_count >= (int)(sizeof(dyn_slots) / sizeof(dyn_slots[0])))
        return -1;

    /* Cross-volume shadow: a basename already registered by an earlier
     * (higher-priority) volume wins; the duplicate is not re-registered
     * and does not consume a dyn slot.  Same-volume duplicates (across
     * /, /assets/, /config/) keep the historical no-dedup behavior so a
     * legacy single-volume image enumerates byte-identically. */
    for (int i = 0; i < dyn_vol_start; i++) {
        if (name_ieq(stored_basename(dyn_slots[i].path), name))
            return 0;
    }

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
 * before switching into an instance; with it empty every vol_join() result
 * is "N:" + the pre-instance path, so existing single-game images are
 * unaffected (an unprefixed path and a "0:" path name the same file). */
static char instance_root[MISTER_INSTANCE_ROOT_MAX];

/* "V:" + (optional instance_root) + leaf (leaf begins with '/') into buf. */
static const char *vol_join_ex(unsigned vol, int with_root, const char *leaf,
                               char *buf, uint32_t max) {
    uint32_t pos = 0;
    if (max < 3u) {
        if (max) buf[0] = '\0';
        return buf;
    }
    buf[pos++] = (char)('0' + vol);
    buf[pos++] = ':';
    if (with_root)
        for (uint32_t i = 0; instance_root[i] && pos < max - 1u; i++)
            buf[pos++] = instance_root[i];
    for (uint32_t i = 0; leaf[i] && pos < max - 1u; i++)
        buf[pos++] = leaf[i];
    buf[pos] = '\0';
    return buf;
}

static const char *vol_join(unsigned vol, const char *leaf,
                            char *buf, uint32_t max) {
    return vol_join_ex(vol, 1, leaf, buf, max);
}

/* Resolve a leaf across mounted volumes in `order`, preferring the first
 * volume where the file exists (f_stat).  With zero or one volume mounted
 * this collapses to a plain path build with NO extra FAT walks, so legacy
 * single-image behavior (and cost) is unchanged.  When the file exists on
 * no volume, the path on the first mounted volume in order is returned so
 * open/stat fail there exactly as they would today. */
static const char *resolve_search(const uint8_t *order, unsigned n,
                                  const char *leaf, char *buf, uint32_t max) {
    int first = -1, mounted = 0;
    for (unsigned i = 0; i < n; i++) {
        if (!vol_mounted(order[i]))
            continue;
        if (first < 0) first = (int)order[i];
        mounted++;
    }
    if (mounted <= 1)
        return vol_join(first < 0 ? 0u : (unsigned)first, leaf, buf, max);

    for (unsigned i = 0; i < n; i++) {
        unsigned vol = order[i];
        if (!vol_mounted(vol))
            continue;
        FILINFO fno;
        if (f_stat(vol_join(vol, leaf, buf, max), &fno) == FR_OK)
            return buf;
    }
    return vol_join((unsigned)first, leaf, buf, max);
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
    /* Enumerate each mounted volume's tree in search order (S1 -> S2 -> S0)
     * so an instance file shadows a borrow/family duplicate; within one
     * volume: root when no instance is set, /assets/ for loose data, and
     * /config/ because per-game settings (e.g. doom.cfg) open BY NAME. */
    char dirbuf[MISTER_PATH_MAX];
    for (unsigned i = 0; i < sizeof(vol_order_game); i++) {
        unsigned vol = vol_order_game[i];
        if (!vol_mounted(vol))
            continue;
        dyn_vol_start = dyn_slot_count;
        enumerate_dir(vol_join(vol, "/", dirbuf, sizeof(dirbuf)));
        enumerate_dir(vol_join(vol, "/assets/", dirbuf, sizeof(dirbuf)));
        enumerate_dir(vol_join(vol, "/config/", dirbuf, sizeof(dirbuf)));
    }
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
    /* The root changes every slot's path resolution — a memoized size for
     * the old tree must not answer for the new one. */
    size_memo_invalidate();
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
    dyn_vol_start = 0;
    /* Overflow name bindings die with the app: syscall_init wipes the fd
     * table and the io cache at relaunch, so no stale reference survives
     * and the ids are free to rebind for the next app.  Named config
     * bindings follow the same lifetime. */
    ovf_count = 0;
    cfg_count = 0;
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
 * is active; the shared config (8) is global across instances.
 *
 * Volume policy (see file header): read-searchable slots (os.ini, app.elf,
 * bank.ofsf) probe mounted volumes in search order; every nonvolatile write
 * slot (8, 9, saves) pins to nv_volume() so writes NEVER land on a library
 * volume — the S1 image preallocates those files, and a missing one fails
 * loudly (save.c's durability invariant) instead of falling back. */
static const char *slot_path(uint32_t slot_id, char *buf, uint32_t max) {
    ensure_mounted();

    switch (slot_id) {
    case 2:  return resolve_search(vol_order_osini, sizeof(vol_order_osini),
                                   "/os.ini", buf, max);
    case 3:  return resolve_search(vol_order_game, sizeof(vol_order_game),
                                   "/app.elf", buf, max);
    case 7:  return resolve_search(vol_order_game, sizeof(vol_order_game),
                                   "/bank.ofsf", buf, max);
    case 8:  /* shared across instances (no instance_root), but per-world:
              * the S1 instance volume owns it in instance mode. */
             return vol_join_ex(nv_volume(), 0, "/config/shared.cfg", buf, max);
    case 9:  return vol_join(nv_volume(), "/config/duke3d.cfg", buf, max);
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
        return vol_join(nv_volume(), leaf, buf, max);
    }

    if (slot_id >= OF_FILE_CFG_SLOT_BASE &&
        slot_id < OF_FILE_CFG_SLOT_BASE + (uint32_t)cfg_count) {
        /* "/config/" + bound leaf name, pinned to the nonvolatile volume
         * (same policy as slots 8/9 — see the header comment above). */
        _Static_assert(2u + (MISTER_INSTANCE_ROOT_MAX - 1u) + 8u + CFG_NAME_MAX
                           <= MISTER_PATH_MAX,
                       "MISTER_PATH_MAX too small for named config paths");
        char leaf[8u + CFG_NAME_MAX];   /* "/config/" + name + NUL */
        const char *pfx = "/config/";
        const char *nm = cfg_names[slot_id - OF_FILE_CFG_SLOT_BASE];
        uint32_t pos = 0;
        while (pfx[pos]) { leaf[pos] = pfx[pos]; pos++; }
        for (uint32_t k = 0; nm[k]; k++)
            leaf[pos++] = nm[k];
        leaf[pos] = '\0';
        return vol_join(nv_volume(), leaf, buf, max);
    }

    if (slot_id >= OVF_SLOT_FIRST &&
        slot_id < OVF_SLOT_FIRST + (uint32_t)ovf_count)
        return ovf_paths[slot_id - OVF_SLOT_FIRST];

    ensure_enumerated();
    for (int i = 0; i < dyn_slot_count; i++) {
        if (dyn_slots[i].slot_id == slot_id)
            return dyn_slots[i].path;
    }

    return (void *)0;
}

/* Directories searched for by-name resolution, mirroring enumerate_dir. */
static const char *const resolve_dirs[3] = { "/", "/assets/", "/config/" };

static int resolve_name_body(const char *name) {
    if (!name || !name[0])
        return -1;

    ensure_enumerated();

    /* Already held by a dynamic slot? */
    for (int i = 0; i < dyn_slot_count; i++) {
        if (name_ieq(stored_basename(dyn_slots[i].path), name))
            return (int)dyn_slots[i].slot_id;
    }
    /* Already bound to an overflow slot?  Ids must stay stable — fds and
     * the kernel io cache key on them — so a name never binds twice. */
    for (int i = 0; i < ovf_count; i++) {
        if (name_ieq(stored_basename(ovf_paths[i]), name))
            return (int)(OVF_SLOT_FIRST + (uint32_t)i);
    }

    /* Names owned by fixed slots resolve through the registry, never here. */
    if (name_ieq(name, "os.ini") || name_ieq(name, "app.elf") ||
        name_ieq(name, "bank.ofsf") || name_ieq(name, "boot.rom"))
        return -1;

    if (ovf_count >= (int)OVF_SLOT_COUNT)
        return -1;

    /* Probe the search dirs on each mounted volume in S1 -> S2 -> S0 order
     * and bind the first hit to a fresh overflow id. */
    for (unsigned i = 0; i < sizeof(vol_order_game); i++) {
        unsigned vol = vol_order_game[i];
        if (!vol_mounted(vol))
            continue;
        for (unsigned d = 0; d < 3u; d++) {
            char *buf = ovf_paths[ovf_count];
            vol_join(vol, resolve_dirs[d], buf, DYN_NAME_MAX);
            uint32_t pos = 0;
            while (buf[pos]) pos++;
            for (uint32_t k = 0; name[k] && pos < DYN_NAME_MAX - 1u; k++)
                buf[pos++] = name[k];
            buf[pos] = '\0';

            FILINFO fno;
            if (f_stat(buf, &fno) == FR_OK && !(fno.fattrib & AM_DIR))
                return (int)(OVF_SLOT_FIRST + (uint32_t)ovf_count++);
        }
    }
    return -1;
}

/* Registry-miss fall-through used by kernel/syscall.c: resolve a basename
 * directly against the mounted volumes.  Returns a stable read-only slot
 * id (dyn or overflow) or -1.  See hal/file.h. */
int of_file_resolve_name(const char *name) {
    mister_fs_enter();
    int id = resolve_name_body(name);
    mister_fs_exit();
    return id;
}

/* ---- by-name config slots (see hal/file.h) --------------------------- */

int mister_file_cfg_slot_valid(uint32_t slot_id) {
    return slot_id >= OF_FILE_CFG_SLOT_BASE &&
           slot_id < OF_FILE_CFG_SLOT_BASE + (uint32_t)cfg_count;
}

/* Evict cached READ handles that alias a named config file under its
 * dyn/overflow registration (the /config tree is enumerated, so the same
 * physical file can be reachable as both a read id and a config slot).
 * FF_FS_LOCK refuses a writable open while any other handle to the file is
 * live (FR_LOCKED), so the alias handle must be dropped before the config
 * slot's own open. */
static void cfg_drop_alias_handles(const char *name) {
    uint32_t alias[2];
    int n = 0;
    for (int i = 0; i < dyn_slot_count && n < 2; i++)
        if (name_ieq(stored_basename(dyn_slots[i].path), name))
            alias[n++] = dyn_slots[i].slot_id;
    for (int i = 0; i < ovf_count && n < 2; i++)
        if (name_ieq(stored_basename(ovf_paths[i]), name))
            alias[n++] = OVF_SLOT_FIRST + (uint32_t)i;
    for (int a = 0; a < n; a++) {
        for (int i = 0; i < FIL_CACHE_SLOTS; i++) {
            if (fil_cache[i].slot_id == alias[a]) {
                f_close(&fil_cache[i].fil);
                fil_cache[i].slot_id = 0;
            }
        }
    }
}

static int cfg_slot_body(const char *name) {
    if (!ensure_mounted())
        return -1;

    /* Already bound — ids stay stable for the app's lifetime (fds and the
     * kernel nv size cache key on them). */
    for (int i = 0; i < cfg_count; i++)
        if (name_ieq(cfg_names[i], name))
            return (int)(OF_FILE_CFG_SLOT_BASE + (uint32_t)i);

    if (cfg_count >= (int)OF_FILE_CFG_SLOT_COUNT)
        return -1;

    uint32_t len = 0;
    while (name[len]) len++;
    if (len == 0 || len >= CFG_NAME_MAX)
        return -1;

    /* The backing file must already exist ON THE PINNED VOLUME — the
     * nonvolatile-write contract shared with slots 8/9: writes never land
     * on a library volume, and files are never created at runtime.  A
     * config the image builder didn't preallocate fails the open cleanly
     * (the SDK tooling owns that gap). */
    char leaf[8u + CFG_NAME_MAX];
    const char *pfx = "/config/";
    uint32_t pos = 0;
    while (pfx[pos]) { leaf[pos] = pfx[pos]; pos++; }
    for (uint32_t k = 0; k < len; k++)
        leaf[pos++] = name[k];
    leaf[pos] = '\0';

    char pathbuf[MISTER_PATH_MAX];
    FILINFO fno;
    if (f_stat(vol_join(nv_volume(), leaf, pathbuf, sizeof(pathbuf)), &fno)
            != FR_OK || (fno.fattrib & AM_DIR))
        return -1;

    for (uint32_t k = 0; k <= len; k++)
        cfg_names[cfg_count][k] = name[k];
    return (int)(OF_FILE_CFG_SLOT_BASE + (uint32_t)cfg_count++);
}

int of_file_config_slot(const char *name) {
    if (!name || !name[0])
        return -1;
    /* Names owned by the fixed nonvolatile slots keep their fixed routes
     * (kernel registry -> slot 8/9); binding them here would open the same
     * file under two ids and trip FF_FS_LOCK. */
    if (name_ieq(name, "shared.cfg") || name_ieq(name, "duke3d.cfg"))
        return -1;
    mister_fs_enter();
    int id = cfg_slot_body(name);
    mister_fs_exit();
    return id;
}

/* Shared FIL handle cache.  Write opens are exclusive under FF_FS_LOCK, so
 * a cached read handle for the same slot is evicted before reopening
 * writable (and vice versa). */
static FIL *open_slot_impl(uint32_t slot_id, int writable) {
    if (!ensure_mounted())
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

    /* Cache hit with a compatible mode: no path resolution at all — this
     * keeps the per-chunk read path free of cross-volume f_stat probes. */
    if (hit && (hit->writable == writable || (hit->writable && !writable))) {
        hit->stamp = ++fil_stamp;
        return &hit->fil;
    }

    char pathbuf[MISTER_PATH_MAX];
    const char *path = slot_path(slot_id, pathbuf, sizeof(pathbuf));
    if (!path)
        return (void *)0;

    if (hit) {
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

    /* A named config slot aliases a file that may also sit in the cache
     * under its dyn/overflow READ id; FF_FS_LOCK would refuse this open
     * (FR_LOCKED, writable case) while that handle lives. */
    if (slot_id >= OF_FILE_CFG_SLOT_BASE &&
        slot_id < OF_FILE_CFG_SLOT_BASE + (uint32_t)cfg_count)
        cfg_drop_alias_handles(cfg_names[slot_id - OF_FILE_CFG_SLOT_BASE]);

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
    for (unsigned vol = 0; vol < FF_VOLUMES; vol++) {
        fs_state[vol] = FS_UNMOUNTED;
        fs_prev_present[vol] = -1;  /* force state machine through first call */
    }
    dyn_enumerated = 0;
    dyn_slot_count = 0;
    dyn_vol_start = 0;
    ovf_count = 0;
    cfg_count = 0;
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
    if (mounted) {
        if (vol_mounted(0))
            of_term_printf("[file] disk image mounted (%u MB)\n",
                           (unsigned)(of_blockdev_size(0) >> 20));
        if (vol_mounted(1))
            of_term_printf("[file] S1 instance volume mounted (%u MB)\n",
                           (unsigned)(of_blockdev_size(1) >> 20));
        if (vol_mounted(2))
            of_term_printf("[file] S2 borrow volume mounted (%u MB)\n",
                           (unsigned)(of_blockdev_size(2) >> 20));
    }
}

/* MiSTer has no APF shutdown handshake — the framework hard-resets the
 * core on exit.  Saves are durable at write time (save.c f_syncs every
 * nonvolatile write), so the only job here is draining a deferred async
 * completion.  Called periodically from IRQ context (kernel/irq.c). */
void of_check_shutdown(void) {
    of_file_async_irq_service();
}

/* Boot finished: arm the DS ERR_TIMEOUT retry loop (see blockdev.h — it
 * must stay OFF during init/probe so a not-yet-responding HPS fails fast
 * instead of blank-screening the boot for minutes). */
void of_file_boot_complete(void) {
    of_blockdev_enable_retries();
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
    /* Any mounted image makes the FAT backend usable — an instance-only
     * boot (S1 without a family library) still carries os.ini + app.elf. */
    for (uint32_t disk = 0; disk < OF_BLOCKDEV_DISK_COUNT; disk++)
        if (of_blockdev_present(disk))
            return 1;
    return 0;
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

/* Logical text extent of a named config slot: bytes before the first
 * 0x00/0xFF (the terminator the stale-tail zeroing in save.c maintains, and
 * the preallocation fill).  The nv fd layer sizes read fds from this, so a
 * reloaded config parses exactly what was written — never the 256 KB
 * physical preallocation.  -1 = logically empty (matches the size64
 * "empty slot" convention). */
static int64_t cfg_logical_size(uint32_t slot_id) {
    FIL *fil = open_slot_impl(slot_id, 0);
    if (!fil)
        return -1;
    if (f_lseek(fil, 0) != FR_OK)
        return -1;

    int64_t extent = 0;
    for (;;) {
        uint8_t buf[512];
        UINT br = 0;
        if (f_read(fil, buf, sizeof(buf), &br) != FR_OK)
            return -1;
        for (UINT i = 0; i < br; i++) {
            if (buf[i] == 0x00u || buf[i] == 0xFFu)
                return extent > 0 ? extent : -1;
            extent++;
        }
        if (br < sizeof(buf))
            break;
    }
    return extent > 0 ? extent : -1;
}

static int64_t size64_body(uint32_t slot_id) {
    if (!ensure_mounted())
        return -1;

    if (slot_id >= OF_FILE_CFG_SLOT_BASE &&
        slot_id < OF_FILE_CFG_SLOT_BASE + (uint32_t)cfg_count)
        return cfg_logical_size(slot_id);

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
