/*
 * openfpgaOS ELF Loader Implementation
 * Loads static-PIE ELF32 RISC-V binaries with relocation support
 */

#include "loader.h"
#include "../hal/hal.h"
#include "../os_string.h"

/* ======================================================================
 * ELF32 Header Definitions (minimal, no external headers needed)
 * ====================================================================== */

#define EI_NIDENT   16
#define ET_EXEC     2
#define ET_DYN      3       /* PIE executables are ET_DYN */
#define EM_RISCV    243
#define PT_LOAD     1
#define PT_DYNAMIC  2
#define PF_X        1
#define PF_W        2
#define PF_R        4

#define DT_NULL     0
#define DT_RELA     7
#define DT_RELASZ   8
#define DT_RELAENT  9
#define DT_REL      17
#define DT_RELSZ    18
#define DT_RELENT   19
#define DT_JMPREL   23
#define DT_PLTRELSZ 2

#define R_RISCV_RELATIVE    3

typedef struct {
    uint8_t  e_ident[EI_NIDENT];
    uint16_t e_type;
    uint16_t e_machine;
    uint32_t e_version;
    uint32_t e_entry;
    uint32_t e_phoff;
    uint32_t e_shoff;
    uint32_t e_flags;
    uint16_t e_ehsize;
    uint16_t e_phentsize;
    uint16_t e_phnum;
    uint16_t e_shentsize;
    uint16_t e_shnum;
    uint16_t e_shstrndx;
} Elf32_Ehdr;

typedef struct {
    uint32_t p_type;
    uint32_t p_offset;
    uint32_t p_vaddr;
    uint32_t p_paddr;
    uint32_t p_filesz;
    uint32_t p_memsz;
    uint32_t p_flags;
    uint32_t p_align;
} Elf32_Phdr;

typedef struct {
    uint32_t r_offset;
    uint32_t r_info;
    int32_t  r_addend;
} Elf32_Rela;

typedef struct {
    int32_t d_tag;
    uint32_t d_val;
} Elf32_Dyn;

/* ======================================================================
 * BRAM App Region
 *
 * Apps can place hot code/data in BRAM for zero-latency access.
 * The ELF loader detects PT_LOAD segments with VMA in the BRAM range
 * and copies them to BRAM via the DMA bounce buffer (DMA can't target
 * BRAM directly since it's not in the bridge address space).
 * ====================================================================== */

/* APP_BRAM_BASE/END defined in hal/regs.h (single source of truth) */

/* ======================================================================
 * ELF Loading Implementation
 * ====================================================================== */

/* Read bytes from data slot into a buffer.
 * Bounces through CRAM1 because buf is typically on the stack (BRAM),
 * which isn't in the bridge address space. */
static int elf_read(uint32_t slot_id, uint32_t offset, void *buf, uint32_t len) {
    if (len > DMA_CHUNK_SIZE)
        return -1;

    int rc = of_file_read(slot_id, offset, (void *)CRAM1_SCRATCH, len);
    if (rc < 0)
        return rc;

    memcpy(buf, (const void *)CRAM1_SCRATCH, len);
    return 0;
}

/* Copy ELF data to BRAM.
 * BRAM is not in the bridge address space, so of_file_read bounces
 * through CRAM1 and copies to the BRAM address. */
static int elf_copy_to_bram(uint32_t slot_id, uint32_t file_offset,
                            uintptr_t bram_addr, uint32_t len) {
    uint32_t done = 0;
    while (done < len) {
        uint32_t chunk = len - done;
        if (chunk > DMA_CHUNK_SIZE)
            chunk = DMA_CHUNK_SIZE;

        int rc = of_file_read(slot_id, file_offset + done,
                               (void *)(bram_addr + done), chunk);
        if (rc < 0)
            return rc;

        done += chunk;
    }
    return 0;
}

/* Process RELA relocations */
static void process_rela(uintptr_t load_base, Elf32_Rela *rela, uint32_t count) {
    for (uint32_t i = 0; i < count; i++) {
        uint32_t type = rela[i].r_info & 0xFF;

        if (type == R_RISCV_RELATIVE) {
            /* R_RISCV_RELATIVE: *(base + offset) = base + addend */
            uint32_t *target = (uint32_t *)(load_base + rela[i].r_offset);
            *target = (uint32_t)(load_base + (uintptr_t)rela[i].r_addend);
        }
        /* Other relocation types are not supported for static-PIE */
    }
}

int elf_load(uint32_t slot_id, uintptr_t load_addr,
             elf_load_result_t *result) {
    Elf32_Ehdr ehdr;
    int rc;

    /* Read ELF header */
    rc = elf_read(slot_id, 0, &ehdr, sizeof(ehdr));
    if (rc < 0)
        return -1;

    /* Validate ELF magic */
    if (ehdr.e_ident[0] != 0x7f || ehdr.e_ident[1] != 'E' ||
        ehdr.e_ident[2] != 'L'  || ehdr.e_ident[3] != 'F')
        return -2;

    /* Validate architecture */
    if (ehdr.e_machine != EM_RISCV)
        return -3;

    /* Must be ET_DYN (PIE) or ET_EXEC */
    if (ehdr.e_type != ET_DYN && ehdr.e_type != ET_EXEC)
        return -4;

    /* Calculate load base:
     * For ET_DYN (PIE): load_base = load_addr (vaddrs are relative to 0)
     * For ET_EXEC at base 0: same treatment (code is PIC via -fPIE)
     * For ET_EXEC at nonzero base: use absolute vaddrs as-is */
    uintptr_t load_base;
    if (ehdr.e_type == ET_DYN) {
        load_base = load_addr;
    } else {
        /* Peek at first PT_LOAD to check if linked at 0 */
        load_base = 0;
        for (int i = 0; i < ehdr.e_phnum; i++) {
            Elf32_Phdr phdr;
            rc = elf_read(slot_id, ehdr.e_phoff + i * ehdr.e_phentsize,
                          &phdr, sizeof(phdr));
            if (rc < 0) return -5;
            if (phdr.p_type == PT_LOAD) {
                if (phdr.p_vaddr == 0)
                    load_base = load_addr;
                break;
            }
        }
    }

    uintptr_t bss_end = 0;
    uint32_t dynamic_offset = 0;
    uint32_t dynamic_size = 0;

    /* Read and process program headers */
    for (int i = 0; i < ehdr.e_phnum; i++) {
        Elf32_Phdr phdr;
        rc = elf_read(slot_id, ehdr.e_phoff + i * ehdr.e_phentsize,
                      &phdr, sizeof(phdr));
        if (rc < 0)
            return -5;

        if (phdr.p_type == PT_LOAD) {
            /* Check if this segment targets BRAM (VMA in app BRAM range) */
            if (phdr.p_vaddr >= APP_BRAM_BASE &&
                phdr.p_vaddr < APP_BRAM_END) {
                /* BRAM segment: DMA to bounce buffer, CPU-copy to BRAM */
                if (phdr.p_filesz > 0) {
                    rc = elf_copy_to_bram(slot_id, phdr.p_offset,
                                          phdr.p_vaddr, phdr.p_filesz);
                    if (rc < 0)
                        return -6;
                }
                /* Zero BRAM BSS if memsz > filesz */
                if (phdr.p_memsz > phdr.p_filesz) {
                    memset((void *)(phdr.p_vaddr + phdr.p_filesz), 0,
                           phdr.p_memsz - phdr.p_filesz);
                }
                /* BRAM segments don't contribute to SDRAM bss_end */
            } else {
                uintptr_t seg_addr = load_base + phdr.p_vaddr;

                /* Load file contents directly to SDRAM via DMA */
                if (phdr.p_filesz > 0) {
                    rc = of_file_read_chunked(slot_id, phdr.p_offset,
                                               (void *)seg_addr, phdr.p_filesz);
                    if (rc < 0)
                        return -6;
                }

                /* Zero BSS (memsz > filesz) */
                if (phdr.p_memsz > phdr.p_filesz) {
                    memset((void *)(seg_addr + phdr.p_filesz), 0,
                           phdr.p_memsz - phdr.p_filesz);
                }

                /* Track end of loaded segments */
                uintptr_t seg_end = seg_addr + phdr.p_memsz;
                if (seg_end > bss_end)
                    bss_end = seg_end;
            }
        }

        if (phdr.p_type == PT_DYNAMIC) {
            dynamic_offset = phdr.p_offset;
            dynamic_size = phdr.p_filesz;
        }
    }

    /* Process dynamic section for relocations */
    if (dynamic_size > 0 && ehdr.e_type == ET_DYN) {
        /* Dynamic section is already loaded in memory */
        Elf32_Dyn *dyn = (Elf32_Dyn *)(load_base + dynamic_offset);

        uint32_t rela_addr = 0, rela_size = 0, rela_ent = 0;

        /* Parse dynamic entries for RELA info */
        /* Actually, dynamic section vaddr may differ from offset.
         * Re-read it to find the addresses. */
        for (int i = 0; i < ehdr.e_phnum; i++) {
            Elf32_Phdr phdr;
            elf_read(slot_id, ehdr.e_phoff + i * ehdr.e_phentsize,
                     &phdr, sizeof(phdr));
            if (phdr.p_type == PT_DYNAMIC) {
                dyn = (Elf32_Dyn *)(load_base + phdr.p_vaddr);
                break;
            }
        }

        for (int i = 0; dyn[i].d_tag != DT_NULL; i++) {
            switch (dyn[i].d_tag) {
            case DT_RELA:     rela_addr = dyn[i].d_val; break;
            case DT_RELASZ:   rela_size = dyn[i].d_val; break;
            case DT_RELAENT:  rela_ent  = dyn[i].d_val; break;
            }
        }

        if (rela_addr && rela_size && rela_ent) {
            Elf32_Rela *rela = (Elf32_Rela *)(load_base + rela_addr);
            uint32_t count = rela_size / rela_ent;
            process_rela(load_base, rela, count);
        }
    }

    /* Flush caches: D-cache for loaded data, I-cache for loaded code */
    of_cache_flush();

    /* Fill result */
    result->entry     = load_base + ehdr.e_entry;
    result->load_base = load_base;
    result->bss_end   = (bss_end + 15) & ~15;  /* Align to 16 bytes */
    result->stack_top = APP_STACK_TOP;           /* Below OS runtime stack */

    return 0;
}

/* Assembly helper to switch stack and jump to entry */
extern void switch_to_runtime_stack_and_call(void (*entry)(void), void *stack_top);

void elf_exec(const elf_load_result_t *result,
              int argc, char **argv) {
    (void)argc; (void)argv;

    /* Set up initial stack frame for the app.
     * musl's _start expects:
     *   sp+0: argc
     *   sp+4: argv[0]
     *   sp+8: argv[1]
     *   ...
     *   sp+4*(argc+1): NULL (argv terminator)
     *   sp+4*(argc+2): NULL (envp terminator)
     *   sp+4*(argc+3): NULL (auxv terminator)
     */
    uint32_t *sp = (uint32_t *)result->stack_top;

    /* Push auxv terminator */
    *(--sp) = 0;
    *(--sp) = 0;

    /* Push envp terminator */
    *(--sp) = 0;

    /* Push argv terminator */
    *(--sp) = 0;

    /* Push argv entries (reverse order) */
    for (int i = argc - 1; i >= 0; i--)
        *(--sp) = (uint32_t)argv[i];

    /* Push argc */
    *(--sp) = (uint32_t)argc;

    /* Jump to entry point with the new stack */
    void (*entry)(void) = (void (*)(void))result->entry;
    switch_to_runtime_stack_and_call(entry, sp);

    __builtin_unreachable();
}
