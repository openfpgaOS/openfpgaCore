/*
 * Misaligned access trap handler for RISC-V
 * Emulates unaligned loads/stores using byte operations
 */

#include "../hal/terminal.h"
#include "../hal/regs.h"
/* Debug variables (defined in main.c) */
extern volatile unsigned int pd_dbg_stage;
extern volatile unsigned int pd_dbg_info;

/* Trap frame layout (matches start.S) */
typedef struct {
    unsigned int regs[32];   /* x0-x31 (x0 always 0) at offset 0 */
    unsigned int mepc;       /* at offset 128 */
    unsigned int mcause;     /* at offset 132 */
    unsigned int mtval;      /* at offset 136 */
    unsigned int fregs[32];  /* f0-f31 at offset 140 */
} trap_frame_t;

/* RISC-V instruction encodings */
#define OPCODE_LOAD   0x03
#define OPCODE_STORE  0x23
#define OPCODE_FLW    0x07  /* Float load (I-type, funct3=010) */
#define OPCODE_FSW    0x27  /* Float store (S-type, funct3=010) */

#define FUNCT3_LB     0x0
#define FUNCT3_LH     0x1
#define FUNCT3_LW     0x2
#define FUNCT3_LBU    0x4
#define FUNCT3_LHU    0x5

#define FUNCT3_SB     0x0
#define FUNCT3_SH     0x1
#define FUNCT3_SW     0x2

/* mcause values */
#define CAUSE_LOAD_MISALIGNED   4
#define CAUSE_STORE_MISALIGNED  6

/* Valid memory regions for emulation (derived from hal/regs.h).
 * v2 arch: CRAM1 + the 0x38 cached alias are gone — only CRAM0 at
 * its base (uncached per PMA) remains. */
#define BRAM_END_ADDR       (BRAM_BASE + BRAM_SIZE)
#define SDRAM_END_ADDR      (SDRAM_BASE + SDRAM_SIZE)
#define CRAM0_START_ADDR    CRAM0_BASE
#define CRAM0_END_ADDR      (CRAM0_BASE + CRAM_SIZE)
#define SDRAM_UC_END_ADDR   (SDRAM_UNCACHED_BASE + SDRAM_SIZE)

/* Check if address range is in valid memory that the APP is allowed
 * to touch via an emulated misaligned access.  The BRAM window below
 * APP_BRAM_BASE holds the boot ROM, trap vectors, _ecall_handler and
 * _trap_restore code — a bogus app pointer (uninitialised FILE *,
 * stack garbage, NULL+offset) with low-address misalignment can land
 * here.  If the misaligned handler "helpfully" emulates a store to
 * those addresses via byte writes, it corrupts the trap path itself
 * and the next exception blows up in code that no longer exists.
 *
 * The 0x147 trap (app's __stdio_write error path storing to a FILE *
 * that was 0x147) proved this: the emulated stores rewrote bytes
 * inside _ecall_handler, which then misbehaved on the next syscall.
 *
 * Reject OS-BRAM stores/loads here — the app sees the original
 * misaligned trap turned into a fatal_trap instead of silently
 * scribbling kernel code. */
__attribute__((section(".text.boot")))
static int addr_valid(unsigned int addr, unsigned int len) {
    unsigned int end = addr + len - 1;
    /* Check for overflow */
    if (end < addr) return 0;
    /* BRAM — only the app's window is emulatable. OS BRAM
     * (< APP_BRAM_BASE, and > APP_BRAM_END which is the kernel
     * trap stack) is off-limits. */
    if (addr >= APP_BRAM_BASE && end < APP_BRAM_END) return 1;
    /* SDRAM (cached) */
    if (addr >= SDRAM_BASE && end < SDRAM_END_ADDR) return 1;
    /* CRAM0 (uncached per PMA; CRAM1 retired in v2 arch) */
    if (addr >= CRAM0_START_ADDR && end < CRAM0_END_ADDR) return 1;
    /* SDRAM (uncached alias — used for PAK data) */
    if (addr >= SDRAM_UNCACHED_BASE && end < SDRAM_UC_END_ADDR) return 1;
    return 0;
}

/* Read byte from memory */
__attribute__((section(".text.boot")))
static inline unsigned char read_byte(unsigned int addr) {
    return *(volatile unsigned char *)addr;
}

/* Write byte to memory */
__attribute__((section(".text.boot")))
static inline void write_byte(unsigned int addr, unsigned char val) {
    *(volatile unsigned char *)addr = val;
}

/* Emulate misaligned load */
__attribute__((section(".text.boot")))
static unsigned int emulate_load(unsigned int addr, int funct3) {
    unsigned int val = 0;

    switch (funct3) {
    case FUNCT3_LH:  /* Load halfword (signed) */
        val = read_byte(addr) | (read_byte(addr + 1) << 8);
        val = (int)(signed short)val;
        break;

    case FUNCT3_LHU: /* Load halfword (unsigned) */
        val = read_byte(addr) | (read_byte(addr + 1) << 8);
        break;

    case FUNCT3_LW:  /* Load word */
        val = read_byte(addr) |
              (read_byte(addr + 1) << 8) |
              (read_byte(addr + 2) << 16) |
              (read_byte(addr + 3) << 24);
        break;
    }

    return val;
}

/* Emulate misaligned store */
__attribute__((section(".text.boot")))
static void emulate_store(unsigned int addr, unsigned int val, int funct3) {
    switch (funct3) {
    case FUNCT3_SH:  /* Store halfword */
        write_byte(addr, val & 0xFF);
        write_byte(addr + 1, (val >> 8) & 0xFF);
        break;

    case FUNCT3_SW:  /* Store word */
        write_byte(addr, val & 0xFF);
        write_byte(addr + 1, (val >> 8) & 0xFF);
        write_byte(addr + 2, (val >> 16) & 0xFF);
        write_byte(addr + 3, (val >> 24) & 0xFF);
        break;
    }
}

/* Debug counter for misaligned traps */
static unsigned int misaligned_count = 0;

/* Read instruction at PC using byte reads (mepc may not be 4-byte aligned) */
__attribute__((section(".text.boot")))
static unsigned int read_instr(unsigned int pc) {
    /* Check if this is a compressed instruction (low 2 bits != 11) */
    unsigned int lo = read_byte(pc) | (read_byte(pc + 1) << 8);
    if ((lo & 3) != 3)
        return lo;  /* 16-bit compressed instruction */
    /* 32-bit instruction */
    return lo | (read_byte(pc + 2) << 16) | (read_byte(pc + 3) << 24);
}

/* Decode and handle misaligned access
 * Returns 1 if handled, 0 if should trap normally */
__attribute__((section(".text.boot")))
int handle_misaligned(trap_frame_t *frame) {
    unsigned int mcause = frame->mcause;

    /* Only handle misaligned load/store traps */
    if (mcause != CAUSE_LOAD_MISALIGNED && mcause != CAUSE_STORE_MISALIGNED)
        return 0;

    unsigned int instr = read_instr(frame->mepc);
    int is_compressed = ((instr & 3) != 3);

    /* Debug: print first few traps */
    misaligned_count++;
    if (misaligned_count <= 5) {
        of_term_printf("T#%d mc=%x pc=%x i=%x\n",
                    misaligned_count, mcause, frame->mepc, instr);
    }

    /* ---- Compressed (RVC) instructions ---- */
    if (is_compressed) {
        unsigned int cfunct3 = (instr >> 13) & 0x7;
        unsigned int cop = instr & 0x3;

        /* C.LW / C.FLW (quadrant 0: op=00, funct3=010/011)
         * C.SW / C.FSW (quadrant 0: op=00, funct3=110/111)
         * imm = {i[5], i[12:10], i[6], 00} (word-scaled) */
        if (cop == 0 && (cfunct3 == 2 || cfunct3 == 6 ||
                         cfunct3 == 3 || cfunct3 == 7)) {
            unsigned int rs1c = 8 + ((instr >> 7) & 0x7);
            unsigned int rd_rs2c = 8 + ((instr >> 2) & 0x7);
            unsigned int imm_val = ((instr >> 4) & 0x4)  |   /* bit 6 → bit 2 */
                                   ((instr >> 7) & 0x38) |   /* bits 12:10 → bits 5:3 */
                                   ((instr << 1) & 0x40);    /* bit 5 → bit 6 */
            unsigned int addr = frame->regs[rs1c] + imm_val;

            if (!addr_valid(addr, 4))
                return 0;

            if (cfunct3 == 2) {         /* C.LW */
                frame->regs[rd_rs2c] = emulate_load(addr, FUNCT3_LW);
            } else if (cfunct3 == 6) {  /* C.SW */
                emulate_store(addr, frame->regs[rd_rs2c], FUNCT3_SW);
            } else if (cfunct3 == 3) {  /* C.FLW */
                frame->fregs[rd_rs2c] = emulate_load(addr, FUNCT3_LW);
            } else {                    /* C.FSW */
                emulate_store(addr, frame->fregs[rd_rs2c], FUNCT3_SW);
            }

            frame->mepc += 2;
            return 1;
        }

        /* C.LWSP / C.FLWSP (quadrant 2: op=10, funct3=010/011)
         * imm = {i[3:2], i[12], i[6:4], 00} (word-scaled) */
        if (cop == 2 && (cfunct3 == 2 || cfunct3 == 3)) {
            unsigned int rd_reg = (instr >> 7) & 0x1F;
            unsigned int imm_val = ((instr >> 2) & 0x1C) |   /* bits 6:4 → bits 4:2 */
                                   ((instr >> 7) & 0x20) |   /* bit 12 → bit 5 */
                                   ((instr << 4) & 0xC0);    /* bits 3:2 → bits 7:6 */
            unsigned int addr = frame->regs[2] + imm_val;  /* sp-relative */

            if (!addr_valid(addr, 4))
                return 0;

            if (cfunct3 == 2) {         /* C.LWSP */
                if (rd_reg != 0)
                    frame->regs[rd_reg] = emulate_load(addr, FUNCT3_LW);
            } else {                    /* C.FLWSP */
                frame->fregs[rd_reg] = emulate_load(addr, FUNCT3_LW);
            }

            frame->mepc += 2;
            return 1;
        }

        /* C.SWSP / C.FSWSP (quadrant 2: op=10, funct3=110/111)
         * imm = {i[8:7], i[12:9], 00} (word-scaled) */
        if (cop == 2 && (cfunct3 == 6 || cfunct3 == 7)) {
            unsigned int rs2_reg = (instr >> 2) & 0x1F;
            unsigned int imm_val = ((instr >> 7) & 0x3C) |   /* bits 12:9 → bits 5:2 */
                                   ((instr >> 1) & 0xC0);    /* bits 8:7 → bits 7:6 */
            unsigned int addr = frame->regs[2] + imm_val;  /* sp-relative */

            if (!addr_valid(addr, 4))
                return 0;

            if (cfunct3 == 6) {         /* C.SWSP */
                emulate_store(addr, frame->regs[rs2_reg], FUNCT3_SW);
            } else {                    /* C.FSWSP */
                emulate_store(addr, frame->fregs[rs2_reg], FUNCT3_SW);
            }

            frame->mepc += 2;
            return 1;
        }

        return 0;  /* Unrecognized compressed instruction */
    }

    /* ---- Standard 32-bit instructions ---- */
    unsigned int opcode = instr & 0x7F;
    unsigned int funct3 = (instr >> 12) & 0x7;
    unsigned int rd = (instr >> 7) & 0x1F;
    unsigned int rs1 = (instr >> 15) & 0x1F;
    unsigned int rs2 = (instr >> 20) & 0x1F;
    int imm;
    unsigned int addr;

    if (opcode == OPCODE_LOAD) {
        /* I-type immediate: instr[31:20] sign-extended */
        imm = ((int)instr) >> 20;
        addr = frame->regs[rs1] + imm;

        /* Validate address before accessing */
        unsigned int access_len = (funct3 == FUNCT3_LW) ? 4 :
                                  (funct3 == FUNCT3_LH || funct3 == FUNCT3_LHU) ? 2 : 1;
        if (!addr_valid(addr, access_len))
            return 0;

        /* Emulate the load */
        unsigned int val = emulate_load(addr, funct3);

        /* Write to destination register (rd=0 is hardwired to 0, ignore) */
        if (rd != 0) {
            frame->regs[rd] = val;
        }

        /* Advance PC past the instruction */
        frame->mepc += 4;
        return 1;
    }

    if (opcode == OPCODE_STORE) {
        /* S-type immediate: {instr[31:25], instr[11:7]} sign-extended */
        imm = ((instr >> 7) & 0x1F) | (((int)instr >> 20) & 0xFFFFFFE0);
        addr = frame->regs[rs1] + imm;

        /* Validate address before accessing */
        unsigned int access_len = (funct3 == FUNCT3_SW) ? 4 :
                                  (funct3 == FUNCT3_SH) ? 2 : 1;
        if (!addr_valid(addr, access_len))
            return 0;

        /* Get value from source register */
        unsigned int val = frame->regs[rs2];

        /* Emulate the store */
        emulate_store(addr, val, funct3);

        /* Advance PC past the instruction */
        frame->mepc += 4;
        return 1;
    }

    /* FLW: float load word (I-type, opcode 0x07, funct3=010) */
    if (opcode == OPCODE_FLW) {
        imm = ((int)instr) >> 20;
        addr = frame->regs[rs1] + imm;

        if (!addr_valid(addr, 4))
            return 0;

        frame->fregs[rd] = emulate_load(addr, FUNCT3_LW);
        frame->mepc += 4;
        return 1;
    }

    /* FSW: float store word (S-type, opcode 0x27, funct3=010) */
    if (opcode == OPCODE_FSW) {
        imm = ((instr >> 7) & 0x1F) | (((int)instr >> 20) & 0xFFFFFFE0);
        addr = frame->regs[rs1] + imm;

        if (!addr_valid(addr, 4))
            return 0;

        emulate_store(addr, frame->fregs[rs2], FUNCT3_SW);
        frame->mepc += 4;
        return 1;
    }

    /* Not a load/store instruction - can't handle */
    return 0;
}

/* ======================================================================
 * Fatal trap output — writes directly to VRAM + color RAM.
 * Bypasses of_term_putchar/ANSI parser entirely so trap output
 * is always readable regardless of terminal state.
 * All functions are in .text.boot (BRAM) for reliability.
 * ====================================================================== */

#define TRAP_VRAM  0x20000000
#define TRAP_COLOR 0x20000800
#define TRAP_COLS  40
#define TRAP_ROWS  30
#define TRAP_ATTR  0x0F  /* white on black */

static int trap_col, trap_row;

__attribute__((section(".text.boot")))
static void trap_putchar(char c) {
    if (c == '\n') {
        trap_col = 0;
        trap_row++;
    } else {
        if (trap_col < TRAP_COLS && trap_row < TRAP_ROWS) {
            int off = trap_row * TRAP_COLS + trap_col;
            (*(volatile unsigned char *)(TRAP_VRAM + off)) = (unsigned char)c;
            (*(volatile unsigned char *)(TRAP_COLOR + off)) = TRAP_ATTR;
        }
        trap_col++;
    }
}

__attribute__((section(".text.boot")))
static void trap_puts(const char *s) {
    while (*s) trap_putchar(*s++);
}

__attribute__((section(".text.boot")))
static void trap_hex(unsigned int val) {
    const char *hex = "0123456789ABCDEF";
    trap_puts("0x");
    for (int i = 28; i >= 0; i -= 4)
        trap_putchar(hex[(val >> i) & 0xF]);
}

__attribute__((section(".text.boot")))
static void trap_uint(unsigned int val) {
    char buf[12];
    int n = 0;
    if (val == 0) { trap_putchar('0'); return; }
    while (val > 0) { buf[n++] = '0' + (val % 10); val /= 10; }
    while (n > 0) trap_putchar(buf[--n]);
}

__attribute__((section(".text.boot")))
static void trap_line(const char *label, unsigned int val) {
    trap_puts(label);
    trap_hex(val);
    trap_putchar('\n');
}

/* Fatal trap handler — called when we can't handle the exception.
 *
 * Writes to UART MMIO (0x4F000004) *must* poll UART_STATUS.TX_RDY
 * (bit 1) at 0x4F000000 before each byte.  The fabric UART is a
 * single-byte passthrough (TX FIFO was removed for ALM budget); writing
 * while TX is still shifting simply drops the byte — which is why
 * earlier trap dumps lost characters and appeared "buffered" on the
 * host side.  Bounded poll (~8000 cycles ≈ 16× byte-time at 2 Mbaud)
 * so a wedged UART still lets the infinite loop engage instead of
 * hanging the dump forever. */
__attribute__((section(".text.boot")))
static inline void trap_uart_putb(unsigned c) {
    volatile unsigned *ust = (volatile unsigned *)0x4F000000;
    volatile unsigned *utx = (volatile unsigned *)0x4F000004;
    for (int i = 0; i < 8000; i++) {
        if (*ust & 0x2u) break;  /* UART_TX_RDY */
    }
    *utx = c;
}

__attribute__((section(".text.boot")))
static void trap_uart_hex(uint32_t v) {
    for (int i = 28; i >= 0; i -= 4) {
        unsigned n = (v >> i) & 0xF;
        trap_uart_putb((n < 10) ? ('0' + n) : ('a' + n - 10));
    }
}

__attribute__((section(".text.boot")))
static void trap_uart_puts(const char *s) {
    while (*s) trap_uart_putb((unsigned)*s++);
}

__attribute__((section(".text.boot")))
void fatal_trap(trap_frame_t *frame) {
    /* Shout cause/mepc/mtval to UART first so the host sees it even
     * if the terminal / screen has been torn down.  Format:
     *   ==TRAP==
     *   mcause=<>  mepc=<>  mtval=<>  mstatus=<>
     *   sp=<>  ra=<>
     *
     * Reading mstatus here (not from the frame — the trap entry
     * doesn't save it) tells us MIE/MPIE/MPP at trap time, which
     * pinpoints traps taken with IRQs masked / from nested context. */
    uint32_t mstatus_v;
    __asm__ volatile ("csrr %0, mstatus" : "=r"(mstatus_v));

    trap_uart_puts("\n==TRAP==\nmcause=");
    trap_uart_hex(frame->mcause);
    trap_uart_puts(" mepc=");
    trap_uart_hex(frame->mepc);
    trap_uart_puts(" mtval=");
    trap_uart_hex(frame->mtval);
    trap_uart_puts(" mstatus=");
    trap_uart_hex(mstatus_v);
    trap_uart_puts("\nsp=");
    trap_uart_hex(frame->regs[2]);
    trap_uart_puts(" ra=");
    trap_uart_hex(frame->regs[1]);
    trap_uart_puts("\n");

    /* Diagnostic: re-read memory at mepc DIRECTLY from SDRAM to tell us
     * whether the corruption is in SDRAM (a DMA master — the GPU in LITE —
     * wrote junk there) or in the I-cache path (cache returned bad bits).
     *
     *   cbo.inval the D-cache line → guarantees the subsequent volatile
     *     load goes to DRAM, not cache.
     *   Use the SDRAM uncached alias at 0x38000000-0x3FFFFFFF (if mepc
     *     is in the cached 0x10000000 SDRAM window) so the load BYPASSES
     *     both D-cache and I-cache and reads the real storage.
     *
     * If sdram_mem == mtval → SDRAM is physically corrupted.
     * If sdram_mem != mtval && sdram_mem == expected → I-cache path bug.
     */
    uintptr_t mepc = frame->mepc;
    uintptr_t ra   = frame->regs[1];
    if (mepc >= 0x10000000u && mepc < 0x14000000u) {
        /* Round down to word boundary — RVC instructions can be at
         * 2-byte alignment, but reads must be 4-byte.  Print 4 words
         * spanning mepc so the actual fault and its neighbours are
         * visible from the physical memory side. */
        uintptr_t word0 = mepc & ~3u;
        __asm__ volatile(".insn i 0x0F, 2, x0, %0, 0" :: "r"(word0)       : "memory");
        __asm__ volatile(".insn i 0x0F, 2, x0, %0, 0" :: "r"(word0 + 64)  : "memory");
        __asm__ volatile("fence" ::: "memory");
        trap_uart_puts("sdram@mepc:");
        for (int i = -2; i < 4; i++) {
            uintptr_t a = word0 + i * 4;
            if (a >= 0x10000000u && a < 0x14000000u) {
                trap_uart_puts(" ");
                trap_uart_hex(*(volatile uint32_t *)a);
            }
        }
        trap_uart_puts("\n");
    }
    /* Read GPU's stray-write latch.  GPU_DBG_BADWR at 0x4A000030 holds
     * the first M_WR AXI write whose address was outside the 4 MB
     * framebuffer band (0x10000000..0x103FFFFF); bit 0 is the "ever
     * fired" flag, bits [31:2] are the violating addr's word index.
     * GPU_DBG_BADCNT at 0x4A000034 holds the total count of violations.
     * If hit=1, the GPU *did* write outside the FB — the user's
     * GPU-writing-somewhere hypothesis is confirmed. */
    uint32_t gpu_bad    = *(volatile uint32_t *)0x4A000030u;
    uint32_t gpu_cnt    = *(volatile uint32_t *)0x4A000034u;
    uint32_t gpu_ring_wr = *(volatile uint32_t *)0x4A000038u;
    uint32_t gpu_status = *(volatile uint32_t *)0x4A000014u;
    uint32_t gpu_rdptr  = *(volatile uint32_t *)0x4A000010u;
    uint32_t gpu_wrptr  = *(volatile uint32_t *)0x4A000004u;
    uint32_t gpu_fence  = *(volatile uint32_t *)0x4A000018u;
    trap_uart_puts("gpu_status=");
    trap_uart_hex(gpu_status);
    trap_uart_puts(" rdptr=");
    trap_uart_hex(gpu_rdptr);
    trap_uart_puts(" wrptr=");
    trap_uart_hex(gpu_wrptr);
    trap_uart_puts(" fence=");
    trap_uart_hex(gpu_fence);
    trap_uart_puts("\n");
    trap_uart_puts("gpu_ringwr_cnt=");
    trap_uart_hex(gpu_ring_wr);
    trap_uart_puts(" bad_waddr=");
    trap_uart_hex(gpu_bad);
    trap_uart_puts(" (hit=");
    trap_uart_putb('0' + (gpu_bad & 1));
    trap_uart_puts(" count=");
    trap_uart_hex(gpu_cnt);
    trap_uart_puts(")\n");

    /* Also dump 4 words at ra (the caller). If the app's text got
     * overwritten by a stray DMA, the jal at ra-4 will show garbage. */
    if (ra >= 0x10000000u && ra < 0x14000000u) {
        uintptr_t word0 = ra & ~3u;
        __asm__ volatile(".insn i 0x0F, 2, x0, %0, 0" :: "r"(word0) : "memory");
        __asm__ volatile("fence" ::: "memory");
        trap_uart_puts("sdram@ra:");
        for (int i = -2; i < 4; i++) {
            uintptr_t a = word0 + i * 4;
            if (a >= 0x10000000u && a < 0x14000000u) {
                trap_uart_puts(" ");
                trap_uart_hex(*(volatile uint32_t *)a);
            }
        }
        trap_uart_puts("\n");
    }

    /* Dump 64 words of app stack around sp — lets us find where the
     * corrupted return address came from.  Safe because we're on the
     * trap stack in BRAM and the app's sp is in SDRAM.  Stop before
     * reading past the SDRAM PMA end (0x14000000) so the dump itself
     * can't trigger a recursive load access fault — that used to
     * double-print the trap when sp was deep and 64 words crossed the
     * PMA boundary. */
    uintptr_t sp = frame->regs[2];
    if (sp >= 0x10000000u && sp < 0x14000000u && (sp & 3) == 0) {
        const uintptr_t sdram_end = 0x14000000u;
        uint32_t max_words = (sdram_end - sp) >> 2;
        if (max_words > 64) max_words = 64;
        trap_uart_puts("stk:");
        for (uint32_t i = 0; i < max_words; i++) {
            if ((i & 3) == 0) trap_uart_puts("\n ");
            trap_uart_hex(((volatile uint32_t *)sp)[i]);
            trap_uart_puts(" ");
        }
        trap_uart_puts("\n");
    }

    /* Also print all 32 x-regs from the trap frame — gives us whatever
     * was in ra/gp/fp/caller-saved/temp regs at fault time. */
    trap_uart_puts("regs:");
    for (int i = 0; i < 32; i++) {
        if ((i & 3) == 0) {
            trap_uart_puts("\n x");
            trap_uart_hex((uint32_t)i);
            trap_uart_puts(":");
        }
        trap_uart_puts(" ");
        trap_uart_hex(frame->regs[i]);
    }
    trap_uart_puts("\n==END==\n");

    /* Halt forever — use asm to prevent compiler from optimizing this away */
    __asm__ volatile("1: j 1b");
}
