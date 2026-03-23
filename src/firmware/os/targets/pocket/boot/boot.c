/*
 * openfpgaOS Bootloader
 * Runs from BRAM. Loads os.bin via deferload to SDRAM, jumps to os_main.
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
static void clear_os_bss(void) {
    unsigned int *p = (unsigned int *)_os_bss_start;
    unsigned int *end = (unsigned int *)_os_bss_end;
    while (p < end)
        *p++ = 0;
}

__attribute__((section(".text.boot")))
static void flush_icache(void) {
    __asm__ volatile("fence");
    __asm__ volatile(".word 0x0000100f");
}

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
static int boot_load_os(void *dest, uint32_t total) {
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

    /* Show loading status on VRAM (row 13 is below the boot banner) */
    boot_vram_puts(0, 13, "Loading os.bin...");

    pd_dbg_stage = 3;

    /* Load os.bin */
    uint32_t os_size = (uint32_t)(uintptr_t)_os_copy_size;
    int rc = boot_load_os(_os_load_addr, os_size);
    if (rc < 0) {
        boot_vram_puts(0, 14, "LOAD FAILED!");
        pd_dbg_info = (unsigned int)(-rc);
        while (1) {}
    }

    pd_dbg_stage = 4;

    boot_vram_puts(0, 14, "OK - starting OS");

    flush_icache();
    clear_os_bss();

    pd_dbg_stage = 5;

    /* Jump to OS */
    switch_to_runtime_stack_and_call(os_main, _runtime_stack_top);

    while (1) {}
    return 0;
}
