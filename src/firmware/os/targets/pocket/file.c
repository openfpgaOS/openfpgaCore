/*
 * openfpgaOS File HAL Implementation
 * Low-level APF bridge file I/O
 */

#include "file.h"
#include "disk.h"
#include "audio.h"
#include "mixer.h"
#include "cache.h"
#include "regs.h"
#include "terminal.h"
#include <string.h>

#define DMA_TIMEOUT         200000000   /* ~2 seconds at 100MHz */
#define DMA_CACHE_LINE_SIZE 64u

#ifndef OF_TARGET_CRAM0_DMA_CHUNK_SIZE
#define OF_TARGET_CRAM0_DMA_CHUNK_SIZE DMA_CHUNK_SIZE
#endif

/* Idle hook — called during any blocking wait (DMA, bridge, etc.)
 * Apps register this via OF_SYS_SET_IDLE_HOOK to do background
 * work (audio pump, input polling) during file I/O.
 *
 * The hook may use syscalls only when file I/O is running from normal
 * app/OS context. When a blocking file syscall is already executing inside
 * the trap handler, call_idle_hook() suppresses the hook to avoid nested
 * ecall clobbering the outer trap frame. */
static void (*idle_hook)(void);

/* Forward decl: bridge backend implementation lives below, but
 * of_file_init's warmup DMA needs to call it directly. */
static int bridge_read_impl(uint32_t slot_id, uint32_t slot_offset,
                            void *dest, uint32_t length);
long of_file_size(uint32_t slot_id);
static int bridge_warmed;
static int bridge_warmup_active;

/* Async data-slot read state. Completion is IRQ-driven on Pocket hardware;
 * of_file_async_poll() remains as a compatibility/fallback drain. */
static struct {
    volatile int      active;
    volatile uint32_t completed_count;
    int               token;
    uint32_t          length;
    void             *dest;
    void             *dma_dest;
    int               bounce_to_dest;
    void            (*callback)(int token, int result);
} async_state;

static int async_token_counter;
static uint32_t dma_stage_next;

static int addr_in_range(uint32_t addr, uint32_t length,
                         uint32_t base, uint32_t size) {
    return addr >= base && length <= size && addr <= base + size - length;
}

static int addr_in_sdram(uint32_t addr, uint32_t length) {
    return addr_in_range(addr, length, SDRAM_BASE, SDRAM_SIZE) ||
           addr_in_range(addr, length, SDRAM_UNCACHED_BASE, SDRAM_SIZE);
}

static int addr_in_cached_sdram(uint32_t addr, uint32_t length) {
    return addr_in_range(addr, length, SDRAM_BASE, SDRAM_SIZE);
}

static uint32_t sdram_alias_to_bridge(void *addr) {
    uint32_t a = (uintptr_t)addr;
    if (a >= SDRAM_UNCACHED_BASE && a < SDRAM_UNCACHED_BASE + SDRAM_SIZE)
        return a - SDRAM_UNCACHED_BASE;
    return a - SDRAM_BASE;
}

static int bridge_addr_targets_cram0(uint32_t bridge_addr, uint32_t length) {
    return addr_in_range(bridge_addr, length, CRAM0_BRIDGE, CRAM_SIZE);
}

static void bridge_warmup_once(void) {
    if (bridge_warmed || bridge_warmup_active)
        return;

    bridge_warmup_active = 1;
    (void)bridge_read_impl(1, 0, (void *)CRAM0_SCRATCH, 4);
    of_cache_flush_dcache();
    bridge_warmup_active = 0;
    bridge_warmed = 1;
}

void of_file_set_idle_hook(void (*hook)(void)) {
    idle_hook = hook;
}

void of_file_init(void) {
    idle_hook = (void *)0;
    bridge_warmed = 0;
    bridge_warmup_active = 0;
    async_state.active = 0;
    async_state.completed_count = 0;
    async_state.token = 0;
    async_state.length = 0;
    async_state.dest = (void *)0;
    async_state.dma_dest = (void *)0;
    async_state.bounce_to_dest = 0;
    async_state.callback = (void *)0;
    async_token_counter = 0;
    dma_stage_next = 0;

#if OF_TARGET_PLATFORM_ID == OF_PLATFORM_SIM
    /* Sim has no bridge model — the warmup DMA below would hang
     * forever waiting for ack/done from a non-existent peer. */
    return;
#endif
    IRQ_MASK &= ~IRQ_MASK_DATASLOT;
    DS_STATUS = DS_STATUS_IRQ_PENDING;

    /* Bridge warmup: first DMA after boot resets the bridge command
     * state machine. Read 4 bytes from slot 1 into CRAM0 scratch.
     * Skipped when the bridge isn't the active backend, since the
     * boot ROM channel may have been picked because the bridge is
     * wedged. The dispatcher must run before of_file_init — see hal.c.
     * (v2 arch: CRAM1 retired, scratch moved to CRAM0.) */
    if (of_disk_active() == &of_disk_bridge)
        bridge_warmup_once();
}

/* Check for shutdown handshake: if the bridge wants to shut down,
 * hand CRAM0 back to the bridge, flush D-cache (framebuffer/DMA data
 * in SDRAM), and acknowledge so the bridge can proceed with reset.
 * Save data in CRAM0 does not need cache flushing — CRAM0 is uncached
 * per PMA — but the bridge must own the CRAM0 mux before the Pocket
 * nonvolatile exit writeback reads the save window. */
void of_check_shutdown(void) {
    if (SYS_SHUTDOWN & SHUTDOWN_PENDING) {
        fence();
        CRAM0_MODE = CRAM0_MODE_BRIDGE;
        for (volatile int s = 0; s < 8; s++) {}
        fence();
        of_cache_flush_dcache();
        SYS_SHUTDOWN = SHUTDOWN_ACK;
        while (SYS_SHUTDOWN & SHUTDOWN_PENDING) {
            /* Do not resume app code after handing CRAM0 to the bridge. */
        }
    }
}

/* Call the idle hook safely. Hooks may issue syscalls only when this wait is
 * running from normal context; while already inside a file syscall trap, MIE is
 * clear and we skip the hook to avoid overwriting the outer BRAM trap frame.
 * Note: the hook must NOT use syscalls that trigger file I/O, since that would
 * recurse into file_wait_complete(). */
static inline void call_idle_hook(void) {
    if (!idle_hook) return;

    uint32_t mstatus;
    __asm__ volatile("csrr %0, mstatus" : "=r"(mstatus));
    if ((mstatus & 0x8u) == 0)
        return;

    /* Save trap CSRs that ecall would clobber */
    uint32_t saved_mepc, saved_mcause, saved_mtval;
    __asm__ volatile("csrr %0, mepc"   : "=r"(saved_mepc));
    __asm__ volatile("csrr %0, mcause" : "=r"(saved_mcause));
    __asm__ volatile("csrr %0, mtval"  : "=r"(saved_mtval));

    idle_hook();

    /* Restore trap CSRs */
    __asm__ volatile("csrw mepc, %0"   :: "r"(saved_mepc));
    __asm__ volatile("csrw mcause, %0" :: "r"(saved_mcause));
    __asm__ volatile("csrw mtval, %0"  :: "r"(saved_mtval));
}

static int file_op_count;

static int file_wait_complete(void) {
    uint32_t timeout;

    file_op_count++;

    /* Wait for ACK */
    timeout = DMA_TIMEOUT;
    while (!(DS_STATUS & DS_STATUS_ACK)) {
        if (--timeout == 0) {
            of_term_printf("[ACK timeout #%d st=%02x]\n",
                        file_op_count, DS_STATUS & 0x3F);
            return OF_ERR_TIMEOUT;
        }
        if ((timeout & 0x3FF) == 0)
            call_idle_hook();
    }

    /* Wait for DONE */
    timeout = DMA_TIMEOUT;
    while (!(DS_STATUS & DS_STATUS_DONE)) {
        if (--timeout == 0) {
            of_term_printf("[bridge timeout DONE #%d st=%02x]\n",
                        file_op_count, DS_STATUS & 0x3F);
            return OF_ERR_TIMEOUT;
        }
        if ((timeout & 0x3FF) == 0)
            call_idle_hook();
    }

    /* Check error bits */
    uint32_t err = (DS_STATUS & DS_STATUS_ERR_MASK) >> DS_STATUS_ERR_SHIFT;
    if (err) {
        DS_STATUS = DS_STATUS_IRQ_PENDING;
        return -((int)err);
    }

    /* Wait for bridge to return to idle (ACK cleared via CDC).
     * Without this, the next command can be silently dropped because
     * the dispatch guard sees target_ack_s still high. */
    timeout = DMA_TIMEOUT;
    while (!(DS_STATUS & DS_STATUS_READY)) {
        if (--timeout == 0) {
            of_term_printf("[bridge timeout READY #%d st=%02x]\n",
                        file_op_count, DS_STATUS & 0x7F);
            return OF_ERR_TIMEOUT;
        }
    }

    /* Wait for all bridge write data to drain to memory.
     * READY means the command state machine is idle, but SDRAM skid
     * buffer and CRAM0 write queue may still have pending data.
     * WR_IDLE = skid empty + bridge master idle + CRAM0 idle. */
    timeout = DMA_TIMEOUT;
    while (!(DS_STATUS & DS_STATUS_WR_IDLE)) {
        if (--timeout == 0) {
            of_term_printf("[bridge timeout WR_IDLE #%d st=%02x]\n",
                        file_op_count, DS_STATUS & 0x7F);
            return OF_ERR_TIMEOUT;
        }
    }

    DS_STATUS = DS_STATUS_IRQ_PENDING;
    return 0;
}

/* Bridge backend implementation for the disk HAL. Exported through
 * of_disk_bridge so the dispatcher in hal/disk.c can route reads
 * here when the boot ROM disk channel is unavailable. */
static int bridge_read_impl(uint32_t slot_id, uint32_t slot_offset,
                             void *dest, uint32_t length) {
    if (!bridge_warmup_active)
        bridge_warmup_once();

    uint32_t dest_addr = (uintptr_t)dest;
    uint8_t *dst = (uint8_t *)dest;
    int direct_cram0 = (dest_addr >= CRAM0_BASE)
                    && (length <= CRAM_SIZE)
                    && (dest_addr <= CRAM0_BASE + CRAM_SIZE - length);
    int direct_sdram = addr_in_sdram(dest_addr, length);
    int cached_sdram = addr_in_cached_sdram(dest_addr, length);

    /* v2 arch: CRAM1 retired, scratch moved to CRAM0.  CRAM0 is
     * uncached per PMA, so no D-cache invalidation is needed around
     * bridge-written data there. */

    uint32_t done = 0;
    while (done < length) {
        uint32_t chunk = length - done;
        int rc;

        if (direct_sdram && !direct_cram0) {
            uint32_t cur = dest_addr + done;

            if (cached_sdram && (cur & (DMA_CACHE_LINE_SIZE - 1u))) {
                chunk = DMA_CACHE_LINE_SIZE -
                        (cur & (DMA_CACHE_LINE_SIZE - 1u));
                if (chunk > length - done)
                    chunk = length - done;
                if (chunk > OF_TARGET_CRAM0_DMA_CHUNK_SIZE)
                    chunk = OF_TARGET_CRAM0_DMA_CHUNK_SIZE;
                goto bounce_via_cram0;
            }

            if (cached_sdram) {
                chunk &= ~(DMA_CACHE_LINE_SIZE - 1u);
                if (chunk == 0) {
                    chunk = length - done;
                    if (chunk > OF_TARGET_CRAM0_DMA_CHUNK_SIZE)
                        chunk = OF_TARGET_CRAM0_DMA_CHUNK_SIZE;
                    goto bounce_via_cram0;
                }
            }

            if (chunk > DMA_CHUNK_SIZE)
                chunk = DMA_CHUNK_SIZE;
            if (cached_sdram)
                of_cache_inval_range(dst + done, chunk);

            rc = of_file_read_raw(slot_id, slot_offset + done,
                                  sdram_alias_to_bridge(dst + done), chunk);
            if (rc < 0)
                return rc;

            if (cached_sdram)
                of_cache_inval_range(dst + done, chunk);
            done += chunk;
            continue;
        }

        if (chunk > OF_TARGET_CRAM0_DMA_CHUNK_SIZE)
            chunk = OF_TARGET_CRAM0_DMA_CHUNK_SIZE;

        CRAM0_MODE = CRAM0_MODE_BRIDGE;
        for (volatile int s = 0; s < 8; s++) {}

        uint32_t bridge_addr = direct_cram0
            ? cpu_to_bridge(dst + done)
            : CRAM0_SCRATCH_BRIDGE;
        rc = of_file_read_raw(slot_id, slot_offset + done, bridge_addr, chunk);
        if (rc < 0)
            return rc;

        if (!direct_cram0) {
            CRAM0_MODE = CRAM0_MODE_CPU;
            for (volatile int s = 0; s < 8; s++) {}
            memcpy(dst + done, (const void *)CRAM0_SCRATCH, chunk);
        }

        done += chunk;
        continue;

bounce_via_cram0:
        CRAM0_MODE = CRAM0_MODE_BRIDGE;
        for (volatile int s = 0; s < 8; s++) {}

        rc = of_file_read_raw(slot_id, slot_offset + done,
                              CRAM0_SCRATCH_BRIDGE, chunk);
        if (rc < 0)
            return rc;

        CRAM0_MODE = CRAM0_MODE_CPU;
        for (volatile int s = 0; s < 8; s++) {}
        memcpy(dst + done, (const void *)CRAM0_SCRATCH, chunk);
        done += chunk;
    }

    return 0;
}

/* of_disk_bridge probe — the bridge is "available" if the data slot
 * command FSM is in READY state. Cheap and side-effect-free. After a
 * successful probe the dispatcher will route reads through
 * bridge_read_impl above.
 *
 * Note this still returns 1 even if the SD card is wedged — there is
 * no SD-card-level liveness check from the CPU side. The boot ROM
 * channel is probed first precisely so that dev workflows can bypass a hung
 * SD/bridge entirely. */
static int bridge_probe(void) {
    return (DS_STATUS & DS_STATUS_READY) ? 1 : 0;
}

/* Bridge backend size: delegates to the legacy/saturating size wrapper.
 * Loader inputs are small; POSIX slot FDs use of_file_size64 below. */
static long bridge_size_impl(uint32_t slot_id) {
    return of_file_size(slot_id);
}

const of_disk_driver_t of_disk_bridge = {
    .name  = "SD",
    .probe = bridge_probe,
    .read  = bridge_read_impl,
    .size  = bridge_size_impl,
};

/* Public of_file_read — thin wrapper around the disk dispatcher.
 * Routes to whichever backend was selected at of_disk_init time. */
int of_file_read(uint32_t slot_id, uint32_t slot_offset,
                  void *dest, uint32_t length) {
    return of_disk_read(slot_id, slot_offset, dest, length);
}

/* Raw bridge DMA: caller owns cache coherency and destination ownership.
 * Higher-level reads prefer direct SDRAM DMA for aligned cache-line ranges
 * and use the CRAM0 scratch bounce only where required. */
int of_file_read_raw(uint32_t slot_id, uint32_t slot_offset,
                      uint32_t bridge_addr, uint32_t length) {
    uint32_t max_len = bridge_addr_targets_cram0(bridge_addr, length)
        ? OF_TARGET_CRAM0_DMA_CHUNK_SIZE
        : DMA_CHUNK_SIZE;

    if (length > max_len)
        return OF_ERR_BAD_RANGE;
    if (async_state.active)
        return OF_ERR_BUSY;

    /* Wait for bridge fully idle — READY (cmd FSM idle) AND WR_IDLE
     * (all write data drained).  Both must be true before issuing a
     * new command, otherwise the dispatch guard may silently drop it
     * if target_ack_s hasn't fully deasserted through the CDC. */
    {
        uint32_t wait = DMA_TIMEOUT;
        while ((DS_STATUS & (DS_STATUS_READY | DS_STATUS_WR_IDLE))
               != (DS_STATUS_READY | DS_STATUS_WR_IDLE)) {
            if (--wait == 0) return OF_ERR_TIMEOUT;
        }
    }

    DS_SLOT_ID     = slot_id;
    DS_SLOT_OFFSET = slot_offset;
    DS_BRIDGE_ADDR = bridge_addr;
    DS_LENGTH      = length;
    fence();
    DS_COMMAND     = DS_CMD_READ;

    /* Verify command was accepted — if the dispatch guard dropped it,
     * ds_cmd_active won't be set and ACK will never come.  Retry once. */
    fence();
    for (int i = 0; i < 100; i++) {
        uint32_t st = DS_STATUS;
        if (st & DS_STATUS_ACK)  goto accepted;  /* ACK already */
        if (!(st & DS_STATUS_READY)) goto accepted;  /* cmd_active set → READY cleared */
    }
    /* Command likely dropped — retry */
    fence();
    DS_COMMAND = DS_CMD_READ;

accepted:
    return file_wait_complete();
}

static int datatable_entry_candidate_for_slot(uint32_t slot_id,
                                              uint32_t *entry_out) {
    /* APF's datatable is indexed by array position in data.json, while
     * DS_CMD_READ/GETFILE use the slot `id` field. Current layout:
     *   ids 0-7      -> entries 0-7   (game, os, os.ini, app,
     *                                    data 1-3, soundbank)
     *   id 8 or id 9 -> entry  8      (one pre-save nonvolatile slot:
     *                                    SDK Shared Config or Duke settings)
     *   ids 10-19    -> entries 9-18  (ten nonvolatile save slots)
     * If you add or remove a pre-save slot in data.json, this map MUST
     * be updated in lockstep -- the relationship is contractual and
     * APF does not expose a dependable runtime layout query. */
    if (slot_id <= 7) {
        *entry_out = slot_id;
        return 0;
    }

    if (slot_id == 8 || slot_id == 9) {
        *entry_out = 8;
        return 0;
    }

    if (slot_id >= 10 &&
        slot_id < 10 + (uint32_t)OF_TARGET_SAVE_MAX_SLOTS) {
        *entry_out = 9 + (slot_id - 10);
        return 0;
    }

    return -1;
}

static int datatable_read_word32(uint32_t word, uint32_t *value_out,
                                 int *full_reg_out) {
    /* Toggle-based CDC: writing DT_QUERY flips a toggle bit. The
     * clk_74a domain detects the change, reads the datatable BRAM,
     * and tags the result with the captured toggle. DT_QUERY keeps the
     * legacy {valid, data[30:0]} readback, while DT_QUERY_DATA exposes
     * the full 32-bit payload so file sizes >= 2GB retain bit 31. */
    DT_QUERY = word;

    uint32_t val = 0;
    for (int i = 0; i < 1000; i++) {
        val = DT_QUERY;
        if (val & 0x80000000) {
            if (value_out) {
                uint32_t legacy = val & 0x7FFFFFFFu;
                uint32_t full = DT_QUERY_DATA;
                int has_full = (full != 0 || legacy == 0);

                /* Older bitstreams leave 0x94 as a retired zero register.
                 * Keep normal app/core loading working there, while newer
                 * bitstreams use DT_QUERY_DATA to preserve size bit 31. */
                *value_out = has_full ? full : legacy;
                if (full_reg_out)
                    *full_reg_out = has_full;
            }
            return 0;
        }
    }

    return -1;
}

static int datatable_probe_size_bit31(uint32_t slot_id, uint32_t low_size) {
    enum { PROBE_LEN = 32 };

    if (low_size == 0 || async_state.active)
        return 0;

    volatile uint8_t *probe = (volatile uint8_t *)CRAM0_SCRATCH;

    CRAM0_MODE = CRAM0_MODE_CPU;
    for (volatile int s = 0; s < 8; s++) {}
    for (uint32_t i = 0; i < PROBE_LEN; i++)
        probe[i] = (uint8_t)(0x5Au + i * 37u);
    __asm__ volatile("fence" ::: "memory");

    CRAM0_MODE = CRAM0_MODE_BRIDGE;
    for (volatile int s = 0; s < 8; s++) {}

    int rc = of_file_read_raw(slot_id, low_size, CRAM0_SCRATCH_BRIDGE,
                              PROBE_LEN);
    if (rc < 0)
        return 0;

    CRAM0_MODE = CRAM0_MODE_CPU;
    for (volatile int s = 0; s < 8; s++) {}
    for (uint32_t i = 0; i < PROBE_LEN; i++) {
        if (probe[i] != (uint8_t)(0x5Au + i * 37u))
            return 1;
    }

    return 0;
}

/* Fallback for slot ids the fixed map rejects (e.g. >= 20).  The APF datatable
 * is positional and target_dataslot_getfile can't return data to the CPU (see
 * Chip32.md), so we scan the table directly: each entry's word0[15:0] holds
 * that entry's slot id, so a linear scan recovers an arbitrary id's array
 * position.  Additive -- existing <=19 layouts still take the hardcoded fast
 * path above, so this cannot change their behaviour.  Bounded by
 * DATATABLE_MAX_ENTRIES; the datatable BRAM (mf_datatable.v) holds 512 entries,
 * but 64 keeps each miss cheap.  A queried id is always one we declared, so its
 * real (lower) entry is found before any stale post-declaration position. */
#define DATATABLE_MAX_ENTRIES 64u
static int datatable_entry_scan_for_slot(uint32_t slot_id, uint32_t *entry_out) {
    for (uint32_t e = 0; e < DATATABLE_MAX_ENTRIES; e++) {
        uint32_t w0 = 0;
        if (datatable_read_word32(e * 2u, &w0, NULL) < 0)
            return -1;
        if ((w0 & 0xFFFFu) == slot_id) {
            *entry_out = e;
            return 0;
        }
    }
    return -1;
}

static int datatable_entry_for_slot(uint32_t slot_id, uint32_t *entry_out) {
    if (datatable_entry_candidate_for_slot(slot_id, entry_out) == 0)
        return 0;
    return datatable_entry_scan_for_slot(slot_id, entry_out);
}

long of_file_flags(uint32_t slot_id) {
    uint32_t entry;
    if (datatable_entry_for_slot(slot_id, &entry) < 0)
        return -1;

    uint32_t word = 0;
    if (datatable_read_word32(entry * 2, &word, NULL) < 0)
        return -1;

    /* Word 0 is [31:16]=size high bits for >4GB files and [15:0]=id. */
    return (long)(word & 0xFFFFu);
}

static int64_t of_file_size64_common(uint32_t slot_id, int allow_probe) {
    uint32_t entry;
    if (datatable_entry_for_slot(slot_id, &entry) < 0)
        return -1;

    uint32_t id_word = 0;
    uint32_t size_low = 0;
    int size_has_full_reg = 0;
    if (datatable_read_word32(entry * 2, &id_word, NULL) < 0)
        return -1;
    if (datatable_read_word32(entry * 2 + 1, &size_low,
                              &size_has_full_reg) < 0)
        return -1;

    uint64_t size = ((uint64_t)(id_word >> 16) << 32) | size_low;
    if (allow_probe && !size_has_full_reg &&
        datatable_probe_size_bit31(slot_id, size_low)) {
        size += 0x80000000ull;
    }

    return size ? (int64_t)size : -1;
}

int64_t of_file_size64(uint32_t slot_id) {
    return of_file_size64_common(slot_id, 1);
}

long of_file_size(uint32_t slot_id) {
    int64_t size = of_file_size64_common(slot_id, 0);
    if (size <= 0)
        return -1;
    if (size > 0x7FFFFFFFll)
        return 0x7FFFFFFFl;
    return (long)size;
}

/*
 * Get the filename for a data slot via APF target command 0x0190.
 * The bridge writes a response struct to the DMA buffer containing
 * the filename. Returns 0 on success, <0 on error.
 * Filename is written to `name_out` (max `name_max` chars).
 */
int of_file_get_name(uint32_t slot_id, char *name_out, uint32_t name_max) {
    if (async_state.active)
        return OF_ERR_BUSY;

    /* v2 arch: response buffer lands in CRAM0 scratch (uncached per
     * PMA, so no D-cache dance is needed).  Use a dedicated GETFILE
     * offset so it can't collide with an in-flight bridge DMA that
     * targets the lower part of CRAM0_SCRATCH. */
    #define GETFILE_ADDR      CRAM0_SCRATCH
    #define GETFILE_BRIDGE    CRAM0_SCRATCH_BRIDGE
    uint32_t bridge_addr = GETFILE_BRIDGE;
    volatile uint32_t *resp32 = (volatile uint32_t *)GETFILE_ADDR;

    /* CPU-side scribble on CRAM0 — take ownership of the mux first. */
    CRAM0_MODE = CRAM0_MODE_CPU;
    for (volatile int s = 0; s < 8; s++) {}
    for (int i = 0; i < 64; i++)
        resp32[i] = 0;
    __asm__ volatile("fence" ::: "memory");

    /* Hand the mux back to the bridge for the primer + GETFILE DMAs. */
    CRAM0_MODE = CRAM0_MODE_BRIDGE;
    for (volatile int s = 0; s < 8; s++) {}

    /* Prime APF's internal slot state with a throw-away 4-byte read.
     * Empirically, APF only returns a valid filename from GETFILE after
     * the slot has been touched by DS_CMD_READ via the bridge. Slots
     * loaded exclusively through the boot-ROM path (e.g. the OS binary
     * via the bootloader) or slots never yet accessed return an empty
     * response struct. The primer read is 4 bytes at offset 0; the data
     * is discarded. rc is ignored — if the read fails, GETFILE will
     * also fail cleanly below. */
    (void)of_file_read_raw(slot_id, 0, bridge_addr, 4);

    /* Re-clear the response buffer — the primer read landed its 4 bytes
     * there, and we want GETFILE to start from known zeros. */
    CRAM0_MODE = CRAM0_MODE_CPU;
    for (volatile int s = 0; s < 8; s++) {}
    for (int i = 0; i < 64; i++)
        resp32[i] = 0;
    __asm__ volatile("fence" ::: "memory");
    CRAM0_MODE = CRAM0_MODE_BRIDGE;
    for (volatile int s = 0; s < 8; s++) {}

    /* Wait for bridge idle */
    {
        uint32_t wait = 5000000;
        while (!(DS_STATUS & DS_STATUS_READY)) {
            if (--wait == 0) return OF_ERR_TIMEOUT;
        }
    }

    DS_SLOT_ID     = slot_id;
    DS_RESP_ADDR   = bridge_addr;
    DS_COMMAND     = DS_CMD_GETFILE;

    /* Wait for command completion and all bridge writes to drain.
     * The APF host fetches the filename from SD and writes the response
     * struct back to the bridge address — WR_IDLE ensures the data
     * has landed in memory before we read it. */
    {
        uint32_t wait = 5000000;  /* ~50ms */
        while (!(DS_STATUS & DS_STATUS_DONE)) {
            if (--wait == 0) return OF_ERR_TIMEOUT;
        }
        wait = 5000000;
        while (!(DS_STATUS & DS_STATUS_READY)) {
            if (--wait == 0) return OF_ERR_TIMEOUT;
        }
        wait = 5000000;
        while (!(DS_STATUS & DS_STATUS_WR_IDLE)) {
            if (--wait == 0) return OF_ERR_TIMEOUT;
        }
    }

    DS_STATUS = DS_STATUS_IRQ_PENDING;
    uint32_t err = (DS_STATUS & DS_STATUS_ERR_MASK) >> DS_STATUS_ERR_SHIFT;
    if (err)
        return -((int)err);

    /* CRAM0 is uncached per PMA — no D-cache invalidation needed.
     * Flip the mux back to the CPU so reads below go through our CDC. */
    CRAM0_MODE = CRAM0_MODE_CPU;
    for (volatile int s = 0; s < 8; s++) {}
    const char *filename = (const char *)GETFILE_ADDR;

    /* Extract basename (after last '/') */
    const char *base = filename;
    for (const char *p = filename; *p; p++) {
        if (*p == '/') base = p + 1;
    }

    uint32_t i;
    for (i = 0; i < name_max - 1 && base[i]; i++)
        name_out[i] = base[i];
    name_out[i] = '\0';

    return (i > 0) ? 0 : -1;
}

int of_file_slot_write(uint32_t slot_id, uint32_t bridge_addr, uint32_t length) {
    if (async_state.active)
        return OF_ERR_BUSY;

    /* Wait for bridge idle before issuing command */
    {
        uint32_t wait = DMA_TIMEOUT;
        while ((DS_STATUS & (DS_STATUS_READY | DS_STATUS_WR_IDLE))
               != (DS_STATUS_READY | DS_STATUS_WR_IDLE)) {
            if (--wait == 0) return OF_ERR_TIMEOUT;
        }
    }

    DS_SLOT_ID     = slot_id;
    DS_SLOT_OFFSET = 0;
    DS_BRIDGE_ADDR = bridge_addr;
    DS_LENGTH      = length;
    DS_COMMAND     = DS_CMD_WRITE;

    return file_wait_complete();
}

int of_file_slot_write_at(uint32_t slot_id, uint32_t slot_offset,
                           uint32_t bridge_addr, uint32_t length) {
    if (async_state.active)
        return OF_ERR_BUSY;

    /* Wait for bridge idle before issuing command */
    {
        uint32_t wait = DMA_TIMEOUT;
        while ((DS_STATUS & (DS_STATUS_READY | DS_STATUS_WR_IDLE))
               != (DS_STATUS_READY | DS_STATUS_WR_IDLE)) {
            if (--wait == 0) return OF_ERR_TIMEOUT;
        }
    }

    DS_SLOT_ID     = slot_id;
    DS_SLOT_OFFSET = slot_offset;
    DS_BRIDGE_ADDR = bridge_addr;
    DS_LENGTH      = length;
    DS_COMMAND     = DS_CMD_WRITE;

    return file_wait_complete();
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

/* (moved to top of file read section) */

int of_file_read_chunked(uint32_t slot_id, uint32_t slot_offset,
                          void *dest, uint32_t total) {
    uint32_t done = 0;

    while (done < total) {
        uint32_t chunk = total - done;
        if (chunk > DMA_CHUNK_SIZE)
            chunk = DMA_CHUNK_SIZE;

        /* of_file_read handles SDRAM direct DMA and CRAM0 bounce fallback. */
        int rc = of_file_read(slot_id, slot_offset + done,
                               (void *)((uintptr_t)dest + done), chunk);
        if (rc < 0)
            return rc;

        done += chunk;
    }

    return 0;
}

/* ======================================================================
 * Async file read — non-blocking DMA with callback
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

static int async_dest_in_cram0(void *dest, uint32_t length) {
    uint32_t addr = (uintptr_t)dest;
    return addr >= CRAM0_BASE &&
           length <= CRAM_SIZE &&
           addr <= (CRAM0_BASE + CRAM_SIZE - length);
}

static void async_complete(uint32_t status) {
    uint32_t err = (status & DS_STATUS_ERR_MASK) >> DS_STATUS_ERR_SHIFT;
    int result = err ? -((int)err) : 0;

    void *dest = async_state.dest;
    void *dma_dest = async_state.dma_dest;
    int bounce_to_dest = async_state.bounce_to_dest;
    uint32_t length = async_state.length;
    int token = async_state.token;
    void (*cb)(int, int) = async_state.callback;

    CRAM0_MODE = CRAM0_MODE_CPU;
    for (volatile int s = 0; s < 8; s++) {}

    /* The bridge DMA target is always CRAM0: either the app-provided staging
     * buffer or the OS bounce window.  CRAM0 is uncached in the CPU PMA, so
     * cache maintenance here is both unnecessary and unsafe while the CRAM0
     * mux is being handed back from the bridge. */
    if (result == 0 && bounce_to_dest)
        memcpy(dest, dma_dest, length);

    async_state.active = 0;
    async_state.dest = (void *)0;
    async_state.dma_dest = (void *)0;
    async_state.bounce_to_dest = 0;
    async_state.length = 0;
    async_state.callback = (void *)0;
    async_state.completed_count++;
    IRQ_MASK &= ~IRQ_MASK_DATASLOT;

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
    int direct_cram0 = async_dest_in_cram0(dest, length);
    void *dma_dest = direct_cram0
        ? dest
        : (void *)(uintptr_t)(CRAM0_BASE + OF_TARGET_CRAM0_ASYNC_BOUNCE_OFFSET);
    if (!bridge_warmup_active)
        bridge_warmup_once();

    uint32_t st = DS_STATUS;
    if ((st & (DS_STATUS_READY | DS_STATUS_WR_IDLE))
        != (DS_STATUS_READY | DS_STATUS_WR_IDLE))
        return OF_ERR_BUSY;

    uint32_t bridge_addr = cpu_to_bridge(dma_dest);

    /* The async hardware path only writes CRAM0.  Do not issue CBO operations
     * for the DMA target: CRAM0 is uncached, and SDRAM destinations are filled
     * later by CPU memcpy from the internal CRAM0 bounce buffer. */
    CRAM0_MODE = CRAM0_MODE_BRIDGE;
    for (volatile int s = 0; s < 8; s++) {}

    /* Record pending state before firing the command.  IRQ delivery is masked
     * while the syscall is in progress, but the bridge can still complete very
     * quickly once the command reaches the APF host. */
    int token = async_token_counter++;
    async_state.active   = 1;
    async_state.completed_count = 0;
    async_state.token    = token;
    async_state.length   = length;
    async_state.dest     = dest;
    async_state.dma_dest = dma_dest;
    async_state.bounce_to_dest = !direct_cram0;
    async_state.callback = callback;

    DS_STATUS = DS_STATUS_IRQ_PENDING;
    IRQ_MASK |= IRQ_MASK_DATASLOT;

    /* Fire the DMA */
    DS_SLOT_ID     = slot_id;
    DS_SLOT_OFFSET = slot_offset;
    DS_BRIDGE_ADDR = bridge_addr;
    DS_LENGTH      = length;
    fence();
    DS_COMMAND     = DS_CMD_READ;
    fence();

    /* This is intentionally fire-and-return.  ACK is the APF host accepting
     * the command, not a cheap local "doorbell latched" bit, and it can take
     * far longer than a few CPU cycles on slow SD cards.  Completion is
     * reported by the data-slot IRQ or by of_file_async_poll() observing DONE. */
    return token;
}

void of_file_async_irq_service(void) {
    uint32_t st = DS_STATUS;
    if (!(st & DS_STATUS_IRQ_PENDING))
        return;

    DS_STATUS = DS_STATUS_IRQ_PENDING;

    if (!async_state.active) {
        IRQ_MASK &= ~IRQ_MASK_DATASLOT;
        return;
    }

    async_complete(st);
}

int of_file_async_poll(void) {
    if (async_state.completed_count) {
        async_state.completed_count--;
        return 1;
    }

    if (async_state.active) {
        uint32_t st = DS_STATUS;
        if (st & DS_STATUS_IRQ_PENDING) {
            of_file_async_irq_service();
        } else if ((st & DS_STATUS_DONE) &&
                   ((st & (DS_STATUS_READY | DS_STATUS_WR_IDLE))
                    == (DS_STATUS_READY | DS_STATUS_WR_IDLE))) {
            DS_STATUS = DS_STATUS_IRQ_PENDING;
            async_complete(st);
        }

        if (async_state.completed_count) {
            async_state.completed_count--;
            return 1;
        }
    }

    return 0;
}

int of_file_async_busy(void) {
    return async_state.active;
}
