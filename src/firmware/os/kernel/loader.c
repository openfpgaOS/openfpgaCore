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

extern long of_file_size(uint32_t slot_id);

int elf_load(uint32_t slot_id, uintptr_t load_addr,
             elf_load_result_t *result) {
    Elf32_Ehdr ehdr;
    int rc;

    /* Check if slot has data before attempting DMA.
     * An empty slot causes the bridge to never ACK, hanging forever. */
    if (of_file_size(slot_id) <= 0)
        return -1;

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

/* Linux ELF auxiliary vector tags -- the subset musl actually consults
 * during __init_libc / __init_tls. Pushing at least AT_PAGESZ is mandatory
 * for musl's mallocng to work: mallocng uses libc.page_size in size and
 * alignment calculations, and a zero page_size produces divide-by-zero
 * (silent wraparound on rv32) and infinite loops in the allocator.
 *
 * AT_RANDOM is consumed by __init_ssp for the stack canary; passing NULL
 * is handled (it falls back to a deterministic seed) but pushing the slot
 * keeps musl on the well-trodden path.
 */
#define AT_NULL    0
#define AT_PAGESZ  6
#define AT_HWCAP  16
#define AT_RANDOM 25

void elf_exec(const elf_load_result_t *result,
              int argc, char **argv) {
    /* Set up the standard SysV ELF initial stack for the app:
     *
     *   sp -> argc                                   (1 word)
     *         argv[0..argc-1]                        (argc words)
     *         NULL                                   (argv terminator)
     *         envp[0..]                              (0 entries here)
     *         NULL                                   (envp terminator)
     *         auxv[0..N-1]                           (type, value) pairs
     *         AT_NULL, 0                             (auxv terminator)
     *
     * musl's _start (rv32 crt_arch.h) does `mv a0, sp` then `tail _start_c`,
     * and _start_c reads argc/argv/envp/auxv from a0 in that order.
     */
    uint32_t *sp = (uint32_t *)result->stack_top;

    /* Push in reverse: top of memory first, sp ends pointing at argc.
     * Each pair below pushes value first, then type, since stack grows
     * down -- so the type ends up at the lower address (read first). */

    /* AT_NULL terminator (must be last in memory) */
    *(--sp) = 0;
    *(--sp) = AT_NULL;

    /* AT_RANDOM: pointer to 16 bytes of "random" data for stack canary.
     * NULL is acceptable -- musl's __init_ssp handles it by seeding from
     * the address of __stack_chk_guard. */
    *(--sp) = 0;
    *(--sp) = AT_RANDOM;

    /* AT_HWCAP: hardware capability bitmask. We don't expose any caps
     * via auxv (apps that care use of_caps.h instead). */
    *(--sp) = 0;
    *(--sp) = AT_HWCAP;

    /* AT_PAGESZ: musl mallocng REQUIRES this -- without it page_size is
     * 0 and the allocator misbehaves (loops, returns NULL, or corrupts). */
    *(--sp) = 4096;
    *(--sp) = AT_PAGESZ;

    /* envp terminator (no envp entries) */
    *(--sp) = 0;

    /* argv terminator */
    *(--sp) = 0;

    /* argv entries (reverse order so argv[0] ends up at lowest addr) */
    for (int i = argc - 1; i >= 0; i--)
        *(--sp) = (uint32_t)argv[i];

    /* argc */
    *(--sp) = (uint32_t)argc;

    /* Jump to entry point with the new stack */
    void (*entry)(void) = (void (*)(void))result->entry;
    switch_to_runtime_stack_and_call(entry, sp);

    __builtin_unreachable();
}
