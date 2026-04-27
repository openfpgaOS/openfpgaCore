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

/* Idle hook — called during any blocking wait (DMA, bridge, etc.)
 * Apps register this via OF_SYS_SET_IDLE_HOOK to do background
 * work (audio pump, input polling) during file I/O.
 *
 * The hook can safely use ecall (syscalls) because the trap handler
 * supports nested traps — it detects when sp is already on the trap
 * stack and continues from there instead of resetting to _stack_top. */
static void (*idle_hook)(void);

/* Forward decl: bridge backend implementation lives below, but
 * of_file_init's warmup DMA needs to call it directly. */
static int bridge_read_impl(uint32_t slot_id, uint32_t slot_offset,
                            void *dest, uint32_t length);

void of_file_set_idle_hook(void (*hook)(void)) {
    idle_hook = hook;
}

void of_file_init(void) {
    idle_hook = (void *)0;

    /* Bridge warmup: first DMA after boot resets the bridge command
     * state machine. Read 4 bytes from slot 1 into CRAM0 scratch.
     * Skipped when the bridge isn't the active backend, since the
     * boot ROM channel may have been picked because the bridge is
     * wedged. The dispatcher must run before of_file_init — see hal.c.
     * (v2 arch: CRAM1 retired, scratch moved to CRAM0.) */
    if (of_disk_active() == &of_disk_bridge) {
        bridge_read_impl(1, 0, (void *)CRAM0_SCRATCH, 4);
        of_cache_flush_dcache();
    }
}

/* Check for shutdown handshake: if the bridge wants to shut down,
 * flush D-cache (framebuffer/DMA data in SDRAM) and acknowledge
 * so the bridge can proceed with reset. Save data in CRAM0 does
 * not need flushing — CRAM0 is uncached per PMA. */
void of_check_shutdown(void) {
    if (SYS_SHUTDOWN & SHUTDOWN_PENDING) {
        of_cache_flush_dcache();
        SYS_SHUTDOWN = SHUTDOWN_ACK;
    }
}

/* Call the idle hook safely — save/restore trap CSRs so the
 * hook can use ecall without corrupting the outer syscall's
 * return path. Note: the hook must NOT use syscalls that
 * trigger file I/O (would recurse into file_wait_complete). */
static inline void call_idle_hook(void) {
    if (!idle_hook) return;

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
        if ((timeout & 0xFFFFF) == 0)
            of_term_printf(".");
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
    if (err)
        return -((int)err);

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

    return 0;
}

/* Check if address is in SDRAM (DMA-capable) range */

/* Bridge backend implementation for the disk HAL. Exported through
 * of_disk_bridge so the dispatcher in hal/disk.c can route reads
 * here when the boot ROM disk channel is unavailable. */
static int bridge_read_impl(uint32_t slot_id, uint32_t slot_offset,
                             void *dest, uint32_t length) {
    uint32_t dest_addr = (uintptr_t)dest;

    /* v2 arch: CRAM1 retired, scratch moved to CRAM0.  CRAM0 is
     * uncached per PMA, so no D-cache invalidation is needed around
     * bridge-written data there. */

    /* CRAM0 destinations: bridge writes directly, no copy needed. */
    if (dest_addr >= CRAM0_BASE && dest_addr < CRAM0_BASE + CRAM_SIZE) {
        uint32_t bridge_addr = cpu_to_bridge(dest);

        CRAM0_MODE = CRAM0_MODE_BRIDGE;
        for (volatile int s = 0; s < 8; s++) {}

        DS_SLOT_ID     = slot_id;
        DS_SLOT_OFFSET = slot_offset;
        DS_BRIDGE_ADDR = bridge_addr;
        DS_LENGTH      = length;
        DS_COMMAND     = DS_CMD_READ;

        return file_wait_complete();
    }

    /* All other destinations: bridge DMAs to CRAM0 scratch, CPU copies
     * to dest.  This path is for boot, ELF loading, and direct
     * of_file_read API calls. */
    CRAM0_MODE = CRAM0_MODE_BRIDGE;
    for (volatile int s = 0; s < 8; s++) {}

    DS_SLOT_ID     = slot_id;
    DS_SLOT_OFFSET = slot_offset;
    DS_BRIDGE_ADDR = CRAM0_SCRATCH_BRIDGE;
    DS_LENGTH      = length;
    DS_COMMAND     = DS_CMD_READ;

    int rc = file_wait_complete();
    if (rc < 0) return rc;

    CRAM0_MODE = CRAM0_MODE_CPU;
    for (volatile int s = 0; s < 8; s++) {}
    memcpy(dest, (const void *)CRAM0_SCRATCH, length);

    return rc;
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

/* Bridge backend size: delegates to of_file_size, which queries
 * the APF datatable through the bridge. Returns -1 on empty slot. */
long of_file_size(uint32_t slot_id);
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

/* Invalidate D-cache for CRAM cached aliases after any bridge
 * operation that writes to CRAM. The bridge bypasses the CPU
 * entirely, so cached reads would return stale data. */
int of_file_read_raw(uint32_t slot_id, uint32_t slot_offset,
                      uint32_t bridge_addr, uint32_t length) {
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

void of_file_inval_cram(uint32_t bridge_addr, uint32_t length) {
    /* v2 arch: CRAM0 is uncached per PMA (and CRAM1 is gone), so no
     * D-cache invalidation is needed for bridge-written CRAM data.
     * Kept as a no-op so callers don't need to know the PMA policy. */
    (void)bridge_addr;
    (void)length;
}

static int datatable_entry_for_slot(uint32_t slot_id, uint32_t *entry_out) {
    /* APF's datatable is indexed by array position in data.json, while
     * DS_CMD_READ/GETFILE use the slot `id` field. openfpgaOS reserves
     * ids 10-19 for the ten nonvolatile save slots, which are appended
     * after entries 0-7 in data.json; ids 8 and 9 are intentionally
     * unused in current templates. */
    if (slot_id < 8) {
        *entry_out = slot_id;
        return 0;
    }

    if (slot_id >= 10 &&
        slot_id < 10 + (uint32_t)OF_TARGET_SAVE_MAX_SLOTS) {
        *entry_out = 8 + (slot_id - 10);
        return 0;
    }

    return -1;
}

static long datatable_read_word(uint32_t word) {
    /* Toggle-based CDC: writing DT_QUERY flips a toggle bit. The
     * clk_74a domain detects the change, reads the datatable BRAM,
     * and tags the result with the captured toggle. The result
     * register reads {toggle_match[31], data[30:0]} — bit 31 is high
     * only when the result corresponds to THIS query. */
    DT_QUERY = word;

    uint32_t val = 0;
    for (int i = 0; i < 1000; i++) {
        val = DT_QUERY;
        if (val & 0x80000000)
            return (long)(val & 0x7FFFFFFF);
    }

    return -1;
}

long of_file_flags(uint32_t slot_id) {
    uint32_t entry;
    if (datatable_entry_for_slot(slot_id, &entry) < 0)
        return -1;

    return datatable_read_word(entry * 2);
}

long of_file_size(uint32_t slot_id) {
    uint32_t entry;
    if (datatable_entry_for_slot(slot_id, &entry) < 0)
        return -1;

    long size = datatable_read_word(entry * 2 + 1);
    return (size > 0) ? size : -1;
}

/*
 * Get the filename for a data slot via APF target command 0x0190.
 * The bridge writes a response struct to the DMA buffer containing
 * the filename. Returns 0 on success, <0 on error.
 * Filename is written to `name_out` (max `name_max` chars).
 */
int of_file_get_name(uint32_t slot_id, char *name_out, uint32_t name_max) {
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
    /* Wait for bridge idle before issuing command */
    {
        uint32_t wait = DMA_TIMEOUT;
        while (!(DS_STATUS & DS_STATUS_READY)) {
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
    /* Wait for bridge idle before issuing command */
    {
        uint32_t wait = DMA_TIMEOUT;
        while (!(DS_STATUS & DS_STATUS_READY)) {
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

/* (moved to top of file read section) */

int of_file_read_chunked(uint32_t slot_id, uint32_t slot_offset,
                          void *dest, uint32_t total) {
    uint32_t done = 0;

    while (done < total) {
        uint32_t chunk = total - done;
        if (chunk > DMA_CHUNK_SIZE)
            chunk = DMA_CHUNK_SIZE;

        /* of_file_read handles CRAM bounce automatically for SDRAM dests */
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

static struct {
    int      active;
    int      token;
    uint32_t length;
    void    *dest;
    void   (*callback)(int token, int result);
} async_state;

static int async_token_counter;

int of_file_read_async(uint32_t slot_id, uint32_t slot_offset,
                       void *dest, uint32_t length,
                       void (*callback)(int token, int result)) {
    if (async_state.active)
        return OF_ERR_BUSY;

    uint32_t bridge_addr = cpu_to_bridge(dest);

    /* Pre-invalidate cache for the DMA target */
    of_cache_inval_range(dest, length);

    /* Fire the DMA */
    DS_SLOT_ID     = slot_id;
    DS_SLOT_OFFSET = slot_offset;
    DS_BRIDGE_ADDR = bridge_addr;
    DS_LENGTH      = length;
    DS_COMMAND     = DS_CMD_READ;

    /* Record pending state */
    int token = async_token_counter++;
    async_state.active   = 1;
    async_state.token    = token;
    async_state.length   = length;
    async_state.dest     = dest;
    async_state.callback = callback;

    return token;
}

int of_file_async_poll(void) {
    if (!async_state.active)
        return 0;

    uint32_t st = DS_STATUS;

    /* Still waiting for ACK or DONE */
    if (!(st & DS_STATUS_DONE))
        return 0;

    /* Check error */
    uint32_t err = (st & DS_STATUS_ERR_MASK) >> DS_STATUS_ERR_SHIFT;
    int result = err ? -((int)err) : 0;

    /* Wait for bridge idle + writes drained (fast, non-blocking spin) */
    while (!(DS_STATUS & DS_STATUS_READY)) {}
    while (!(DS_STATUS & DS_STATUS_WR_IDLE)) {}

    /* Post-invalidate cache */
    of_cache_inval_range(async_state.dest, async_state.length);

    /* Clear state before callback (callback may start another read) */
    int token = async_state.token;
    void (*cb)(int, int) = async_state.callback;
    async_state.active = 0;

    if (cb)
        cb(token, result);

    return 1;
}

int of_file_async_busy(void) {
    return async_state.active;
}
