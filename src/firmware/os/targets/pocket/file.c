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

void of_file_set_idle_hook(void (*hook)(void)) {
    idle_hook = hook;
}

void of_file_init(void) {
    idle_hook = (void *)0;

    /* Warmup: the first bridge DMA after boot resets the bridge command
     * state machine (clears stale ACK/DONE from bootloader's DMA).
     * Without this, musl's fread hangs because the bridge doesn't
     * properly ACK the first command from the OS.
     * Read 4 bytes from slot 1 (os.bin) into CRAM1 scratch — harmless. */
    of_file_read(1, 0, (void *)CRAM1_BASE, 4);
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
     * buffer and CRAM write queues may still have pending data.
     * WR_IDLE = skid empty + bridge master idle + CRAM0/CRAM1 idle. */
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

int of_file_read(uint32_t slot_id, uint32_t slot_offset,
                  void *dest, uint32_t length) {
    uint32_t dest_addr = (uintptr_t)dest;

    /* CRAM1 destinations: bridge writes directly, no copy needed. */
    if (dest_addr >= CRAM1_BASE && dest_addr < CRAM1_BASE + CRAM_SIZE) {
        uint32_t bridge_addr = cpu_to_bridge(dest);

        of_cache_inval_range(dest, length);

        DS_SLOT_ID     = slot_id;
        DS_SLOT_OFFSET = slot_offset;
        DS_BRIDGE_ADDR = bridge_addr;
        DS_LENGTH      = length;
        DS_COMMAND     = DS_CMD_READ;

        int rc = file_wait_complete();

        of_cache_inval_range(dest, length);

        return rc;
    }

    /* All other destinations: bridge DMAs to CRAM1 scratch area,
     * CPU copies to final destination.
     * The hot path (fread/fopen) uses the I/O cache in syscall.c
     * which already reads through CRAM1 directly — this path is
     * for boot, ELF loading, and direct of_file_read API calls. */
    of_cache_inval_range((void *)CRAM1_BASE, length);

    DS_SLOT_ID     = slot_id;
    DS_SLOT_OFFSET = slot_offset;
    DS_BRIDGE_ADDR = CRAM1_BRIDGE;  /* CRAM1 base in bridge space */
    DS_LENGTH      = length;
    DS_COMMAND     = DS_CMD_READ;

    int rc = file_wait_complete();
    if (rc < 0) return rc;

    of_cache_inval_range((void *)CRAM1_BASE, length);
    memcpy(dest, (const void *)CRAM1_BASE, length);

    return rc;
}

/* Invalidate D-cache for CRAM cached aliases after any bridge
 * operation that writes to CRAM. The bridge bypasses the CPU
 * entirely, so cached reads would return stale data. */
int of_file_read_raw(uint32_t slot_id, uint32_t slot_offset,
                      uint32_t bridge_addr, uint32_t length) {
    /* Wait for bridge idle — the dispatch guard silently drops
     * commands if target_ack_s is still high from a previous op. */
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
    DS_COMMAND     = DS_CMD_READ;

    return file_wait_complete();
}

void of_file_inval_cram(uint32_t bridge_addr, uint32_t length) {
    /* Bridge addresses 0x20000000-0x20FFFFFF map to CRAM0 (CPU 0x30000000)
     * Bridge addresses 0x30000000-0x30FFFFFF map to CRAM1 (CPU 0x31000000)
     * Invalidate the cached alias so CPU reads see fresh bridge data. */
    if (bridge_addr >= CRAM0_BRIDGE && bridge_addr < CRAM0_BRIDGE + CRAM_SIZE) {
        uint32_t offset = bridge_addr - CRAM0_BRIDGE;
        of_cache_inval_range((void *)(CRAM0_BASE + offset), length);
    } else if (bridge_addr >= CRAM1_BRIDGE && bridge_addr < CRAM1_BRIDGE + CRAM_SIZE) {
        uint32_t offset = bridge_addr - CRAM1_BRIDGE;
        of_cache_inval_range((void *)(CRAM1_BASE + offset), length);
    }
}

long of_file_size(uint32_t slot_id) {
    /* Read file size from APF datatable via hardware query register.
     * Datatable layout: each data slot has 2 entries (flags, size).
     * Slot N size is at entry N*2+1.  Entries 15+ are save slot sizes,
     * so only data slots 0-6 (entries 0-13) are valid. */
    if (slot_id > 6)
        return -1;

    /* Toggle-based CDC: writing DT_QUERY flips a toggle bit. The
     * clk_74a domain detects the change, reads the datatable BRAM,
     * and tags the result with the captured toggle. The result
     * register reads {toggle_match[31], size[30:0]} — bit 31 is
     * high only when the result corresponds to THIS query. */
    DT_QUERY = slot_id * 2 + 1;

    /* Poll until bit 31 (toggle match) indicates fresh result */
    uint32_t val;
    for (int i = 0; i < 1000; i++) {
        val = DT_QUERY;
        if (val & 0x80000000)
            break;
    }
    if (!(val & 0x80000000))
        return -1;

    uint32_t size = val & 0x7FFFFFFF;
    return (size > 0) ? (long)size : -1;
}

/*
 * Get the filename for a data slot via APF target command 0x0190.
 * The bridge writes a response struct to the DMA buffer containing
 * the filename. Returns 0 on success, <0 on error.
 * Filename is written to `name_out` (max `name_max` chars).
 */
int of_file_get_name(uint32_t slot_id, char *name_out, uint32_t name_max) {
    /* Clear response buffer in CRAM1 (uncached for bridge visibility) */
    volatile uint8_t *resp = (volatile uint8_t *)CRAM1_UNCACHED;
    for (int i = 0; i < 256; i++)
        ((volatile uint32_t *)CRAM1_UNCACHED)[i] = 0;

    fence();

    /* Wait for bridge idle */
    {
        uint32_t wait = DMA_TIMEOUT;
        while (!(DS_STATUS & DS_STATUS_READY)) {
            if (--wait == 0) return OF_ERR_TIMEOUT;
        }
    }

    /* Response struct at CRAM1 base in bridge address space.
     * APF writes: offset 0 = status, offset 4+ = filename (null-terminated) */
    DS_SLOT_ID     = slot_id;
    DS_RESP_ADDR   = CRAM1_BRIDGE;  /* CRAM1 in bridge space */
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
