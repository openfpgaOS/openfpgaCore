/*
 * openfpgaOS File HAL Implementation
 * Low-level APF bridge file I/O
 */

#include "file.h"
#include "audio.h"
#include "mixer.h"
#include "cache.h"
#include "regs.h"
#include "terminal.h"

#define DMA_TIMEOUT         200000000   /* ~2 seconds at 100MHz */

/* Idle hook — called during any blocking wait (DMA, bridge, etc.)
 * Apps register this via OF_SYS_SET_IDLE_HOOK to do background
 * work (audio pump, input polling) during file I/O.
 *
 * The hook can safely use ecall (syscalls) because the trap handler
 * supports nested traps — it detects when sp is already on the trap
 * stack and continues from there instead of resetting to _stack_top. */
static void (*idle_hook)(void);

void of_file_set_idle_hook(void (*hook)(void)) {
    idle_hook = hook;
}

void of_file_init(void) {
    idle_hook = (void *)0;

    /* Warmup: the first bridge DMA after boot resets the bridge command
     * state machine (clears stale ACK/DONE from bootloader's DMA).
     * Without this, musl's fread hangs because the bridge doesn't
     * properly ACK the first command from the OS.
     * Read 4 bytes from slot 1 (os.bin) into DMA_BUFFER — harmless. */
    of_file_read(1, 0, (void *)DMA_BUFFER, 4);
    of_cache_flush_dcache();
}

/* Check for shutdown handshake: if the bridge wants to shut down,
 * flush D-cache (framebuffer/DMA data in SDRAM) and acknowledge
 * so the bridge can proceed with reset. Save data in CRAM1 does
 * not need flushing — bridge reads it via dedicated FIFO. */
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
    of_check_shutdown();
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

static int file_wait_complete(void) {
    uint32_t timeout;

    /* Wait for ACK */
    timeout = DMA_TIMEOUT;
    while (!(DS_STATUS & DS_STATUS_ACK)) {
        if (--timeout == 0)
            return OF_ERR_TIMEOUT;
        if ((timeout & 0x3FF) == 0) {
            of_mixer_pump();
            of_audio_drain();
            call_idle_hook();
        }
    }

    /* Wait for DONE */
    timeout = DMA_TIMEOUT;
    while (!(DS_STATUS & DS_STATUS_DONE)) {
        if (--timeout == 0)
            return OF_ERR_TIMEOUT;
        if ((timeout & 0x3FF) == 0) {
            of_mixer_pump();
            of_audio_drain();
            call_idle_hook();
        }
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
        if (--timeout == 0)
            return OF_ERR_TIMEOUT;
    }

    return 0;
}

int of_file_read(uint32_t slot_id, uint32_t slot_offset,
                  void *dest, uint32_t length) {
    /* Validate destination is in SDRAM */
    uint32_t addr = (uintptr_t)dest;
    if (addr < SDRAM_BASE || addr + length > SDRAM_BASE + SDRAM_SIZE)
        return OF_ERR_PARAM;

    /* Pre-DMA: flush (clean+inval) destination cache lines.
     * Clean: writes back any dirty data so it doesn't later evict
     *        and overwrite the DMA result.
     * Inval: ensures no stale lines survive to serve reads after DMA. */
    of_cache_flush_range(dest, length);

    /* Set up DMA transfer */
    DS_SLOT_ID     = slot_id;
    DS_SLOT_OFFSET = slot_offset;
    DS_BRIDGE_ADDR = sdram_to_bridge(dest);
    DS_LENGTH      = length;
    DS_COMMAND     = OF_FILE_CMD_READ;

    int rc = file_wait_complete();

    /* Post-DMA: invalidate D-cache lines for the DMA target region.
     * The bridge wrote directly to physical SDRAM, bypassing the CPU
     * D-cache. Any stale cache lines must be discarded so the CPU
     * fetches fresh DMA data on next access.
     *
     * Also invalidate the corresponding CRAM cached aliases if the
     * bridge address falls in CRAM space, since bridge writes go
     * directly to PSRAM without touching the D-cache. */
    of_cache_inval_range(dest, length);

    return rc;
}

/* Invalidate D-cache for CRAM cached aliases after any bridge
 * operation that writes to CRAM. The bridge bypasses the CPU
 * entirely, so cached reads would return stale data. */
void of_file_inval_cram(uint32_t bridge_addr, uint32_t length) {
    /* Bridge addresses 0x00000000-0x00FFFFFF map to CRAM0 (CPU 0x30000000)
     * Bridge addresses 0x30000000-0x30FFFFFF map to CRAM1 (CPU 0x31000000)
     * Invalidate the cached alias so CPU reads see fresh bridge data. */
    if (bridge_addr < 0x01000000) {
        of_cache_inval_range((void *)(CRAM0_BASE + bridge_addr), length);
    } else if (bridge_addr >= 0x30000000 && bridge_addr < 0x31000000) {
        uint32_t offset = bridge_addr - 0x30000000;
        of_cache_inval_range((void *)(CRAM1_BASE + offset), length);
    }
}

long of_file_size(uint32_t slot_id) {
    (void)slot_id;
    return -1;  /* TODO: implement via Chip32 register bank */
}

/*
 * Get the filename for a data slot via APF target command 0x0190.
 * The bridge writes a response struct to the DMA buffer containing
 * the filename. Returns 0 on success, <0 on error.
 * Filename is written to `name_out` (max `name_max` chars).
 */
int of_file_get_name(uint32_t slot_id, char *name_out, uint32_t name_max) {
    /* Clear response buffer */
    volatile uint8_t *resp = (volatile uint8_t *)DMA_BUFFER_UNCACHED;
    for (int i = 0; i < 256; i++)
        ((volatile uint32_t *)DMA_BUFFER)[i] = 0;

    fence();

    /* The response struct is at bridge address of DMA_BUFFER.
     * APF writes: offset 0 = status, offset 4+ = filename (null-terminated) */
    DS_SLOT_ID     = slot_id;
    DS_RESP_ADDR   = sdram_to_bridge((void *)DMA_BUFFER);
    DS_COMMAND     = DS_CMD_GETFILE;

    int rc = file_wait_complete();
    if (rc < 0)
        return rc;

    /* The APF getfile response struct (get_dataslot_file_t):
     * offset 0x00: uint32_t status
     * offset 0x04: char filename[128] (null-terminated)
     * offset 0x84: char path[128] (null-terminated) */
    const char *filename = (const char *)(resp + 4);

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
    DS_SLOT_ID     = slot_id;
    DS_SLOT_OFFSET = 0;
    DS_BRIDGE_ADDR = bridge_addr;
    DS_LENGTH      = length;
    DS_COMMAND     = OF_FILE_CMD_WRITE;

    return file_wait_complete();
}

int of_file_slot_write_at(uint32_t slot_id, uint32_t slot_offset,
                           uint32_t bridge_addr, uint32_t length) {
    DS_SLOT_ID     = slot_id;
    DS_SLOT_OFFSET = slot_offset;
    DS_BRIDGE_ADDR = bridge_addr;
    DS_LENGTH      = length;
    DS_COMMAND     = OF_FILE_CMD_WRITE;

    return file_wait_complete();
}

/* Check if address is in SDRAM (DMA-capable) range */
static int addr_is_sdram(uint32_t addr) {
    return (addr >= SDRAM_BASE && addr < SDRAM_BASE + SDRAM_SIZE);
}

int of_file_read_chunked(uint32_t slot_id, uint32_t slot_offset,
                          void *dest, uint32_t total) {
    uint32_t dest_addr = (uintptr_t)dest;
    int bounce = !addr_is_sdram(dest_addr);
    uint32_t done = 0;

    while (done < total) {
        uint32_t chunk = total - done;
        if (chunk > DMA_CHUNK_SIZE)
            chunk = DMA_CHUNK_SIZE;

        if (bounce) {
            /* DMA to SDRAM bounce buffer, then copy to destination.
             * of_file_read() invalidates cache lines for DMA_BUFFER
             * via cache eviction, so the subsequent read gets fresh data. */
            int rc = of_file_read(slot_id, slot_offset + done,
                                   (void *)DMA_BUFFER, chunk);
            if (rc < 0)
                return rc;

            uint8_t *src = (uint8_t *)DMA_BUFFER;
            uint8_t *dst = (uint8_t *)((uintptr_t)dest + done);
            for (uint32_t i = 0; i < chunk; i++)
                dst[i] = src[i];
        } else {
            int rc = of_file_read(slot_id, slot_offset + done,
                                   (void *)((uintptr_t)dest + done), chunk);
            if (rc < 0)
                return rc;
        }

        done += chunk;
    }

    return 0;
}
