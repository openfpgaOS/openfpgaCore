/*
 * openfpgaOS Bootloader
 * Runs from BRAM. Loads os.bin via deferload to SDRAM, jumps to os_main.
 *
 * PHDP (Pocket-Host Debug Protocol) support:
 *   On boot, broadcasts EVT_BOOT_ALIVE over UART for 1 second.
 *   If a host responds, slot loads can be intercepted and streamed
 *   over UART instead of from SD card.
 *
 * IMPORTANT: This code runs BEFORE os.bin is loaded into SDRAM.
 * It must NOT call any HAL functions (they live in SDRAM).
 * All hardware access is done via direct register writes.
 */

#include "../hal/regs.h"

/* Debug variables (read by misaligned trap handler) */
volatile unsigned int pd_dbg_stage = 0;
volatile unsigned int pd_dbg_info = 0;

/* Data slot IDs */
#define OS_SLOT_ID      1       /* OS binary */

/* DMA timeout (~2 seconds at 100MHz) */
#define BOOT_DMA_TIMEOUT 200000000

/* External symbols from linker */
extern char _os_bss_start[], _os_bss_end[];
extern char _runtime_stack_top[];
extern char _os_load_addr[];
extern char _os_copy_size[];

/* OS entry point */
extern void os_main(void);
extern void switch_to_runtime_stack_and_call(void (*entry)(void), void *stack_top);

/* ======================================================================
 * PHDP protocol constants (must match phdp_proto.h on host side)
 * ====================================================================== */
#define PHDP_STX                0x02
#define PHDP_HEADER_SIZE        7
#define PHDP_CRC_SIZE           2
#define PHDP_MAX_CHUNK          512     /* Keep small — bootloader runs from 8KB BRAM */

#define PHDP_EVT_BOOT_ALIVE    0x01
#define PHDP_CMD_CLIENT_READY  0x02
#define PHDP_REQ_OVERRIDE      0x10
#define PHDP_RES_STREAM        0x11
#define PHDP_RES_USE_SD        0x12
#define PHDP_DATA_CHUNK        0x20
#define PHDP_REPORT_PROGRESS   0x21
#define PHDP_CMD_NAK_RETRY     0x22
#define PHDP_EVT_EXEC_START    0x31

/* Core ID and version for EVT_BOOT_ALIVE */
#define PHDP_CORE_ID            0x4F464F53  /* "OFOS" */
#define PHDP_VERSION            0x0100
#define PHDP_MAX_CHUNK_SIZE     512

/* Timeouts in CPU cycles (100 MHz) */
#define PHDP_DISCOVERY_CYCLES   (25000000)      /* 250ms — fast fallback to SD */
#define PHDP_ALIVE_INTERVAL     (5000000)       /* 50ms between broadcasts */
#define PHDP_OVERRIDE_CYCLES    (20000000)      /* 200ms */
#define PHDP_CHUNK_CYCLES       (100000000)     /* 1 second */
#define PHDP_MAX_RETRIES        5

/* ---- All functions below are self-contained, no HAL calls ---- */

__attribute__((section(".text.boot")))
static void boot_vram_putchar(int col, int row, char c) {
    if (col < TERM_COLS && row < TERM_ROWS)
        REG8(TERM_VRAM_BASE + row * TERM_COLS + col) = c;
}

__attribute__((section(".text.boot")))
static void boot_vram_puts(int col, int row, const char *s) {
    while (*s && col < TERM_COLS) {
        boot_vram_putchar(col, row, *s);
        col++;
        s++;
    }
}

__attribute__((section(".text.boot")))
static void boot_vram_clear_row(int row) {
    for (int col = 0; col < TERM_COLS; col++)
        boot_vram_putchar(col, row, ' ');
}

__attribute__((section(".text.boot")))
static void clear_os_bss(void) {
    unsigned int *p = (unsigned int *)_os_bss_start;
    unsigned int *end = (unsigned int *)_os_bss_end;
    while (p < end)
        *p++ = 0;
}

__attribute__((section(".text.boot")))
static void flush_icache(void) {
    __asm__ volatile("fence");
    __asm__ volatile(".word 0x0000100f");  /* fence.i */
}

/* Flush D-cache by conflict eviction.
 * Reads through 32KB from the top of SDRAM to force all dirty lines
 * out of the direct-mapped cache (512 sets x 64B = 32KB). */
__attribute__((section(".text.boot")))
static void flush_dcache(void) {
    __asm__ volatile("fence" ::: "memory");
    volatile char *p = (volatile char *)(SDRAM_BASE + SDRAM_SIZE - 32768);
    for (uint32_t i = 0; i < 32768; i += 64)
        (void)p[i];
    __asm__ volatile("fence" ::: "memory");
}

/* ======================================================================
 * UART helpers (polling, no interrupts)
 * ====================================================================== */

/* Quick check if UART hardware is present and responsive.
 * Reads UART_STATUS once — if it returns a sane value (bit 0 = 1),
 * the peripheral exists. If the bus hangs, we never get here. */
static int uart_available __attribute__((section(".bss.boot")));  /* 0 = untested */

__attribute__((section(".text.boot")))
static int uart_probe(void) {
    if (uart_available) return uart_available;  /* already probed (1=yes, 2=no) */
    /* The UART status register always has bit 0 = 1 when present */
    uint32_t status = UART_STATUS;
    uart_available = (status & 1) ? 1 : 2;
    return (uart_available == 1);
}

__attribute__((section(".text.boot")))
static void uart_putc(uint8_t c) {
    /* Timeout: ~100us at 100MHz. Never block boot. */
    for (int i = 0; i < 10000; i++) {
        if (UART_STATUS & UART_TX_RDY) {
            UART_TX_DATA = c;
            return;
        }
    }
}

/* Try to read one byte. Returns 1 if got a byte, 0 if nothing available. */
__attribute__((section(".text.boot")))
static int uart_getc(uint8_t *c) {
    if (UART_STATUS & UART_RX_AVAIL) {
        *c = (uint8_t)(UART_RX_DATA & 0xFF);
        return 1;
    }
    return 0;
}

/* ======================================================================
 * PHDP CRC-16/CCITT
 * ====================================================================== */

__attribute__((section(".text.boot")))
static uint16_t phdp_crc16(const uint8_t *data, uint32_t len) {
    uint16_t crc = 0xFFFF;
    for (uint32_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int j = 0; j < 8; j++)
            crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : crc << 1;
    }
    return crc;
}

/* ======================================================================
 * PHDP packet TX
 * ====================================================================== */

static uint8_t phdp_seq __attribute__((section(".bss.boot")));

/* Packet buffer — must be in BRAM, not SDRAM (bootloader runs before SDRAM init).
 * 4KB chunk + 9 bytes header/CRC. */
static uint8_t phdp_buf[PHDP_HEADER_SIZE + PHDP_MAX_CHUNK + PHDP_CRC_SIZE]
    __attribute__((section(".bss.boot")));

__attribute__((section(".text.boot")))
static void phdp_send(uint8_t cmd, const void *payload, uint32_t len) {
    phdp_buf[0] = PHDP_STX;
    phdp_buf[1] = phdp_seq++;
    phdp_buf[2] = cmd;
    phdp_buf[3] = (len >>  0) & 0xFF;
    phdp_buf[4] = (len >>  8) & 0xFF;
    phdp_buf[5] = (len >> 16) & 0xFF;
    phdp_buf[6] = (len >> 24) & 0xFF;

    if (len > 0 && payload) {
        const uint8_t *src = (const uint8_t *)payload;
        for (uint32_t i = 0; i < len; i++)
            phdp_buf[PHDP_HEADER_SIZE + i] = src[i];
    }

    uint32_t body = PHDP_HEADER_SIZE + len;
    uint16_t crc = phdp_crc16(phdp_buf, body);
    phdp_buf[body + 0] = (crc >> 0) & 0xFF;
    phdp_buf[body + 1] = (crc >> 8) & 0xFF;

    uint32_t total = body + PHDP_CRC_SIZE;
    for (uint32_t i = 0; i < total; i++)
        uart_putc(phdp_buf[i]);
}

/* ======================================================================
 * PHDP packet RX (blocking with timeout)
 * ====================================================================== */

/* Try to receive a complete packet into phdp_buf.
 * Returns the command byte on success, or -1 on timeout.
 * Payload is at phdp_buf + PHDP_HEADER_SIZE, length in *out_len. */
__attribute__((section(".text.boot")))
static int phdp_recv(uint32_t timeout_cycles, uint32_t *out_len) {
    uint32_t start = SYS_CYCLE_LO;
    uint32_t pos = 0;
    uint32_t payload_len = 0;
    uint32_t need = PHDP_HEADER_SIZE;  /* first, collect header */
    int have_header = 0;

    while ((SYS_CYCLE_LO - start) < timeout_cycles) {
        uint8_t c;
        if (!uart_getc(&c))
            continue;

        /* Sync: wait for STX at position 0 */
        if (pos == 0 && c != PHDP_STX)
            continue;

        phdp_buf[pos++] = c;

        if (!have_header && pos == PHDP_HEADER_SIZE) {
            /* Parse header */
            payload_len = (uint32_t)phdp_buf[3]
                        | ((uint32_t)phdp_buf[4] << 8)
                        | ((uint32_t)phdp_buf[5] << 16)
                        | ((uint32_t)phdp_buf[6] << 24);

            if (payload_len > PHDP_MAX_CHUNK) {
                /* Invalid — restart sync */
                pos = 0;
                continue;
            }
            need = PHDP_HEADER_SIZE + payload_len + PHDP_CRC_SIZE;
            have_header = 1;
        }

        if (have_header && pos == need) {
            /* Validate CRC */
            uint32_t body = PHDP_HEADER_SIZE + payload_len;
            uint16_t expected = phdp_crc16(phdp_buf, body);
            uint16_t got = (uint16_t)phdp_buf[body]
                         | ((uint16_t)phdp_buf[body + 1] << 8);

            if (expected != got) {
                /* CRC mismatch — restart sync */
                pos = 0;
                have_header = 0;
                need = PHDP_HEADER_SIZE;
                continue;
            }

            if (out_len) *out_len = payload_len;
            return (int)phdp_buf[2];  /* CMD byte */
        }
    }

    return -1;  /* timeout */
}

/* ======================================================================
 * PHDP discovery — Phase I
 * Returns 1 if host connected, 0 if timeout (boot from SD).
 * ====================================================================== */

__attribute__((section(".text.boot")))
static int phdp_discover(void) {
    /* EVT_BOOT_ALIVE payload: Core ID (4B) + Version (2B) + Max Chunk (2B) */
    uint8_t alive_payload[8];
    alive_payload[0] = (PHDP_CORE_ID >>  0) & 0xFF;
    alive_payload[1] = (PHDP_CORE_ID >>  8) & 0xFF;
    alive_payload[2] = (PHDP_CORE_ID >> 16) & 0xFF;
    alive_payload[3] = (PHDP_CORE_ID >> 24) & 0xFF;
    alive_payload[4] = (PHDP_VERSION >>  0) & 0xFF;
    alive_payload[5] = (PHDP_VERSION >>  8) & 0xFF;
    alive_payload[6] = (PHDP_MAX_CHUNK_SIZE >> 0) & 0xFF;
    alive_payload[7] = (PHDP_MAX_CHUNK_SIZE >> 8) & 0xFF;

    uint32_t disc_start = SYS_CYCLE_LO;
    uint32_t last_alive = 0;

    while ((SYS_CYCLE_LO - disc_start) < PHDP_DISCOVERY_CYCLES) {
        /* Broadcast EVT_BOOT_ALIVE every 100ms */
        uint32_t now = SYS_CYCLE_LO;
        if ((now - last_alive) >= PHDP_ALIVE_INTERVAL) {
            phdp_send(PHDP_EVT_BOOT_ALIVE, alive_payload, 8);
            last_alive = now;
        }

        /* Try to receive CMD_CLIENT_READY with short timeout
         * (10ms — enough for one packet at 2 Mbaud) */
        uint32_t dummy;
        int cmd = phdp_recv(1000000, &dummy);
        if (cmd == PHDP_CMD_CLIENT_READY)
            return 1;
    }

    return 0;
}

/* ======================================================================
 * PHDP slot override — Phase II
 * Returns 1 if host will stream (total_size and chunk_size filled).
 * Returns 0 if host says use SD or timeout.
 * ====================================================================== */

__attribute__((section(".text.boot")))
static int phdp_request_override(uint8_t slot_id,
                                  uint32_t *total_size,
                                  uint16_t *chunk_size) {
    /* Send REQ_OVERRIDE */
    phdp_send(PHDP_REQ_OVERRIDE, &slot_id, 1);

    /* Wait for response */
    uint32_t len = 0;
    int cmd = phdp_recv(PHDP_OVERRIDE_CYCLES, &len);

    if (cmd == PHDP_RES_STREAM && len >= 6) {
        uint8_t *p = phdp_buf + PHDP_HEADER_SIZE;
        *total_size = (uint32_t)p[0]
                    | ((uint32_t)p[1] << 8)
                    | ((uint32_t)p[2] << 16)
                    | ((uint32_t)p[3] << 24);
        *chunk_size = (uint16_t)p[4]
                    | ((uint16_t)p[5] << 8);

        /* Validate */
        if (*total_size > 16 * 1024 * 1024) return 0;
        if (*chunk_size > PHDP_MAX_CHUNK) *chunk_size = PHDP_MAX_CHUNK;
        return 1;
    }

    return 0;  /* RES_USE_SD or timeout */
}

/* ======================================================================
 * PHDP streaming — Phase III
 * Receives DATA_CHUNK packets and writes to dest.
 * Returns 0 on success, <0 on error.
 * ====================================================================== */

__attribute__((section(".text.boot")))
static int phdp_stream_slot(volatile uint8_t *dest, uint32_t total_size,
                             uint16_t chunk_size) {
    (void)chunk_size;  /* used by host for pacing, Pocket accepts any size <= MAX_CHUNK */
    uint32_t received = 0;
    int retries = 0;

    while (received < total_size) {
        /* Wait for DATA_CHUNK */
        uint32_t len = 0;
        int cmd = phdp_recv(PHDP_CHUNK_CYCLES, &len);

        if (cmd == PHDP_DATA_CHUNK && len > 0) {
            /* Write payload to destination memory */
            uint8_t *src = phdp_buf + PHDP_HEADER_SIZE;
            for (uint32_t i = 0; i < len; i++)
                dest[received + i] = src[i];

            received += len;
            retries = 0;

            /* Send REPORT_PROGRESS (flow control ACK) */
            uint8_t prog[4];
            prog[0] = (received >>  0) & 0xFF;
            prog[1] = (received >>  8) & 0xFF;
            prog[2] = (received >> 16) & 0xFF;
            prog[3] = (received >> 24) & 0xFF;
            phdp_send(PHDP_REPORT_PROGRESS, prog, 4);

            /* Update OSD progress */
            boot_vram_clear_row(14);
            boot_vram_puts(0, 14, "UART: ");
            /* Simple progress: show KB received */
            uint32_t kb = received / 1024;
            char numbuf[12];
            int npos = 0;
            if (kb == 0) {
                numbuf[npos++] = '0';
            } else {
                char tmp[10];
                int tpos = 0;
                while (kb > 0) { tmp[tpos++] = '0' + (kb % 10); kb /= 10; }
                while (tpos > 0) numbuf[npos++] = tmp[--tpos];
            }
            numbuf[npos] = '\0';
            boot_vram_puts(6, 14, numbuf);
            boot_vram_puts(6 + npos, 14, " KB");

        } else if (cmd < 0) {
            /* Timeout — send NAK retry */
            retries++;
            if (retries >= PHDP_MAX_RETRIES) return -1;

            uint8_t nak_seq = phdp_seq - 1;
            phdp_send(PHDP_CMD_NAK_RETRY, &nak_seq, 1);
        }
        /* Ignore other commands during streaming */
    }

    return 0;
}

/* ======================================================================
 * Standard SD card boot (original path)
 * ====================================================================== */

/* Inline DMA read -- does not call any SDRAM functions */
__attribute__((section(".text.boot")))
static int boot_dma_read(uint32_t slot_id, uint32_t slot_offset,
                         uint32_t bridge_addr, uint32_t length) {
    uint32_t timeout;

    __asm__ volatile("fence" ::: "memory");

    DS_SLOT_ID     = slot_id;
    DS_SLOT_OFFSET = slot_offset;
    DS_BRIDGE_ADDR = bridge_addr;
    DS_LENGTH      = length;
    DS_COMMAND     = DS_CMD_READ;

    /* Wait for ACK */
    timeout = BOOT_DMA_TIMEOUT;
    while (!(DS_STATUS & DS_STATUS_ACK)) {
        if (--timeout == 0) return -1;
    }

    /* Wait for DONE */
    timeout = BOOT_DMA_TIMEOUT;
    while (!(DS_STATUS & DS_STATUS_DONE)) {
        if (--timeout == 0) return -2;
    }

    /* Check error */
    uint32_t err = (DS_STATUS & DS_STATUS_ERR_MASK) >> DS_STATUS_ERR_SHIFT;
    if (err) return -(int)err;

    /* Post-DMA settle */
    for (volatile int i = 0; i < 32; i++) {}

    return 0;
}

__attribute__((section(".text.boot")))
static int boot_load_os_sd(void *dest, uint32_t total) {
    uint32_t done = 0;
    uint32_t base_bridge = (uint32_t)(uintptr_t)dest - SDRAM_BASE;

    while (done < total) {
        uint32_t chunk = total - done;
        if (chunk > DMA_CHUNK_SIZE)
            chunk = DMA_CHUNK_SIZE;

        int rc = boot_dma_read(OS_SLOT_ID, done,
                               base_bridge + done, chunk);
        if (rc < 0)
            return rc;
        done += chunk;
    }
    return 0;
}

/* ======================================================================
 * Main
 * ====================================================================== */

__attribute__((section(".text.boot")))
int main(void) {
    pd_dbg_stage = 1;

    /* Wait for APF bridge */
    unsigned int start_wait = SYS_CYCLE_LO;
    while (!(SYS_STATUS & SYS_STATUS_ALLCOMPLETE)) {
        if ((SYS_CYCLE_LO - start_wait) > 500000000)
            break;
    }

    pd_dbg_stage = 2;

    /* Brief delay for deferload to settle */
    for (volatile int i = 0; i < 1000000; i++) {}

    /* ── PHDP Discovery ─────────────────────────────────────────── */
    int debug_mode = 0;

    if (uart_probe()) {
        boot_vram_puts(0, 13, "Checking debug host...");
        debug_mode = phdp_discover();
        boot_vram_clear_row(13);
    }

    if (debug_mode) {
        /* Host connected — try to override OS slot */
        boot_vram_clear_row(13);
        boot_vram_puts(0, 13, "Debug host connected!");

        uint32_t total_size = 0;
        uint16_t chunk_size = 0;

        if (phdp_request_override(OS_SLOT_ID, &total_size, &chunk_size)) {
            /* Stream OS binary over UART */
            boot_vram_clear_row(13);
            boot_vram_puts(0, 13, "Streaming os.bin via UART...");

            volatile uint8_t *dest = (volatile uint8_t *)(uintptr_t)_os_load_addr;
            int rc = phdp_stream_slot(dest, total_size, chunk_size);

            if (rc < 0) {
                boot_vram_puts(0, 15, "UART STREAM FAILED - SD fallback");
                goto load_from_sd;
            }

            /* Send EVT_EXEC_START */
            uint8_t exec_payload[4];
            uint32_t entry = (uint32_t)(uintptr_t)os_main;
            exec_payload[0] = (entry >>  0) & 0xFF;
            exec_payload[1] = (entry >>  8) & 0xFF;
            exec_payload[2] = (entry >> 16) & 0xFF;
            exec_payload[3] = (entry >> 24) & 0xFF;
            phdp_send(PHDP_EVT_EXEC_START, exec_payload, 4);

            boot_vram_clear_row(14);
            boot_vram_puts(0, 14, "OK - starting OS (debug)");
            goto start_os;
        }
        /* Host said USE_SD or timeout — fall through */
        boot_vram_clear_row(13);
    }

load_from_sd:
    /* ── Standard SD card boot ──────────────────────────────────── */
    boot_vram_clear_row(13);
    boot_vram_puts(0, 13, "Loading os.bin...");

    pd_dbg_stage = 3;

    uint32_t os_size = (uint32_t)(uintptr_t)_os_copy_size;

    int rc = boot_load_os_sd(_os_load_addr, os_size);

    if (rc < 0) {
        boot_vram_puts(0, 14, "LOAD FAILED!");
        pd_dbg_info = (unsigned int)(-rc);
        while (1) {}
    }

    pd_dbg_stage = 4;
    boot_vram_puts(0, 14, "OK - starting OS");

start_os:
    flush_dcache();
    flush_icache();
    clear_os_bss();

    pd_dbg_stage = 5;

    /* Jump to OS */
    switch_to_runtime_stack_and_call(os_main, _runtime_stack_top);

    while (1) {}
    return 0;
}
