/*
 * Misaligned access trap handler for RISC-V
 * Emulates unaligned loads/stores using byte operations
 */

#include "../hal/terminal.h"
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

/* Valid memory regions for emulation */
#define BRAM_START      0x00000000
#define BRAM_END        0x00010000
#define SDRAM_START     0x10000000
#define SDRAM_END       0x14000000
#define PSRAM_START     0x30000000
#define PSRAM_END       0x38000000
#define SDRAM_UC_START  0x50000000  /* Uncached SDRAM alias */
#define SDRAM_UC_END    0x54000000

/* Check if address range is in valid memory */
__attribute__((section(".text.boot")))
static int addr_valid(unsigned int addr, unsigned int len) {
    unsigned int end = addr + len - 1;
    /* Check for overflow */
    if (end < addr) return 0;
    /* BRAM */
    if (end < BRAM_END) return 1;  /* BRAM_START is 0, unsigned addr always >= 0 */
    /* SDRAM (cached) */
    if (addr >= SDRAM_START && end < SDRAM_END) return 1;
    /* PSRAM */
    if (addr >= PSRAM_START && end < PSRAM_END) return 1;
    /* SDRAM (uncached alias — used for PAK data) */
    if (addr >= SDRAM_UC_START && end < SDRAM_UC_END) return 1;
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

/* Fatal trap handler - called when we can't handle the exception */
__attribute__((section(".text.boot")))
void fatal_trap(trap_frame_t *frame) {
    (void)frame;
    /* Just freeze — don't write anything to display.
     * The last of_print output on terminal will remain visible. */
    for(;;) { __asm__ volatile(""); }

    /* Snapshot before anything that might trap */
    trap_frame_t snap = *frame;
    unsigned int dbg_stage = pd_dbg_stage;
    unsigned int dbg_info = pd_dbg_info;
    unsigned int handled = misaligned_count;

    /* Clear screen: white-on-black for all cells */
    for (int i = 0; i < TRAP_COLS * TRAP_ROWS; i++) {
        (*(volatile unsigned char *)(TRAP_VRAM + i)) = ' ';
        (*(volatile unsigned char *)(TRAP_COLOR + i)) = TRAP_ATTR;
    }

    trap_col = 0;
    trap_row = 1;

    trap_puts("  !! CPU TRAP !!\n\n");
    trap_line("  mcause: ", snap.mcause);
    trap_line("  mepc:   ", snap.mepc);
    trap_line("  mtval:  ", snap.mtval);
    trap_line("  sp:     ", snap.regs[2]);
    trap_line("  ra:     ", snap.regs[1]);
    trap_line("  stage:  ", dbg_stage);
    trap_line("  info:   ", dbg_info);
    trap_puts("  handled: ");
    trap_uint(handled);
    trap_putchar('\n');

    if (addr_valid(snap.mepc, 4)) {
        unsigned int instr = read_byte(snap.mepc) |
                             (read_byte(snap.mepc + 1) << 8) |
                             (read_byte(snap.mepc + 2) << 16) |
                             (read_byte(snap.mepc + 3) << 24);
        trap_line("  instr:  ", instr);
    }

    /* Halt forever — use asm to prevent compiler from optimizing this away */
    __asm__ volatile("1: j 1b");
}
